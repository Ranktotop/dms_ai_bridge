"""Chat router — Phase IV ReAct agent endpoint.

POST /chat/{frontend}         — single response
POST /chat/{frontend}/stream  — SSE streaming response
"""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from server.dependencies.auth import verify_api_key
from server.dependencies.services import get_agent_service, get_user_mapping_service, get_dms_clients
from server.models.requests import SearchRequest
from server.models.responses import ChatResponse, CitationItem
from server.user_mapping.UserMappingService import UserMappingService
from services.agent.AgentService import AgentService
from services.agent.tools.AgentToolResult import CitationRef
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from services.rag_search.helper.IdentityHelper import IdentityHelper

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(verify_api_key)])


def _resolve_citations(
    citations: list[CitationRef],
    dms_clients: list[DMSClientInterface],
) -> list[CitationItem]:
    """Resolve a list of CitationRef objects into CitationItem responses.

    For each citation, the matching DMS client is found by engine name and used
    to generate the document view URL. If no matching client is found, url is None.

    Args:
        citations: Citation references collected from agent tool results.
        dms_clients: Available DMS clients used to generate document URLs.

    Returns:
        List of CitationItem Pydantic models ready for the API response.
    """
    items: list[CitationItem] = []
    for ref in citations:
        url: str | None = None
        for client in dms_clients:
            if client.get_engine_name() == ref.dms_engine:
                url = client.get_document_view_url(ref.dms_doc_id)
                break
        items.append(
            CitationItem(
                dms_doc_id=ref.dms_doc_id,
                dms_engine=ref.dms_engine,
                title=ref.title,
                url=url,
            )
        )
    return items


@router.post("/{frontend}")
async def chat_documents(
    frontend: str,
    body: SearchRequest,
    agent_service: AgentService = Depends(get_agent_service),
    user_mapping_service: UserMappingService = Depends(get_user_mapping_service),
    dms_clients: list[DMSClientInterface] = Depends(get_dms_clients),
) -> ChatResponse:
    """Run the ReAct agent for a natural language query and return a synthesised answer.

    Resolves the frontend user_id to DMS owner_id(s) via UserMappingService.
    Returns HTTP 403 if the user has no mapping in any configured engine.

    Args:
        frontend: AI system identifier from the URL path (e.g. "openwebui").
        body: JSON body with query, user_id, limit, and optional chat_history.
        agent_service: Injected AgentService from app state.
        user_mapping_service: Injected mapping service from app state.
        dms_clients: Injected list of DMS clients.

    Returns:
        ChatResponse: Synthesised answer from the ReAct agent.
    """
    # build the identity map for the user
    identity_helper = IdentityHelper(
        user_mapping_service=user_mapping_service,
        dms_clients=dms_clients,
        frontend=frontend,
        user_id=body.user_id,
    )
    # make sure the requesting user is mapped to anything
    if not identity_helper.has_mappings():
        raise HTTPException(
            status_code=403,
            detail="No mapping found for frontend '%s', user_id '%s' in any configured engine."
            % (frontend, body.user_id),
        )
    
    # read settings from send body
    client_settings = {"dms_limit": body.limit if body.limit is not None else 5} 
    
    # let the agent do his work
    agent_response = await agent_service.do_run(
        query=body.query,
        chat_history=body.chat_history,
        max_iterations=5,
        step_callback=None,
        identity_helper=identity_helper,
        client_settings=client_settings,
    )
    citations = _resolve_citations(agent_response.citations, dms_clients)
    return ChatResponse(query=body.query, answer=agent_response.answer, citations=citations)


@router.post("/{frontend}/stream")
async def chat_documents_stream(
    frontend: str,
    body: SearchRequest,
    agent_service: AgentService = Depends(get_agent_service),
    user_mapping_service: UserMappingService = Depends(get_user_mapping_service),
    dms_clients: list[DMSClientInterface] = Depends(get_dms_clients),
) -> StreamingResponse:
    """Run the ReAct agent and stream the answer word-by-word as SSE.

    SSE format: data: {"chunk": "word "}\n\n
    Terminator: data: [DONE]\n\n

    Args:
        frontend: AI system identifier from the URL path.
        body: JSON body with query, user_id, limit, and optional chat_history.

    Returns:
        StreamingResponse with text/event-stream media type.
    """
    # build the identity map for the user
    identity_helper = IdentityHelper(
        user_mapping_service=user_mapping_service,
        dms_clients=dms_clients,
        frontend=frontend,
        user_id=body.user_id,
    )
    # make sure the requesting user is mapped to anything
    if not identity_helper.has_mappings():
        raise HTTPException(
            status_code=403,
            detail="No mapping found for frontend '%s', user_id '%s' in any configured engine."
            % (frontend, body.user_id),
        )
    
    # define event generator for responding in realtime as the agent produces output
    async def event_generator():
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        # define callback to receive intermediate steps from the agent and put them in the queue
        async def step_callback(step: str) -> None:
            await queue.put("data: %s\n\n" % json.dumps({"type": "step", "chunk": step}))

        # run the agent in a separate task to not block the event generator
        async def run_agent() -> None:

            # read settings from send body
            client_settings = {"dms_limit": body.limit if body.limit is not None else 5}
            try:
                # let the agent do his work
                agent_response = await agent_service.do_run(
                    query=body.query,
                    identity_helper=identity_helper,
                    chat_history=body.chat_history,
                    step_callback=step_callback,
                    client_settings=client_settings,
                )
                citations = _resolve_citations(agent_response.citations, dms_clients)
                for citation in citations:
                    await queue.put("data: %s\n\n" % json.dumps({
                        "type": "citation",
                        "data": {
                            "document": [citation.url or citation.dms_doc_id],
                            "metadata": [{"source": citation.url or citation.dms_doc_id}],
                            "source": {"name": citation.title or citation.dms_doc_id},
                        },
                    }))
                for word in agent_response.answer.split(" "):
                    await queue.put("data: %s\n\n" % json.dumps({"type": "answer", "chunk": word + " "}))
                await queue.put("data: %s\n\n" % json.dumps({"type": "step", "chunk": "✅ Fertig"}))
            except Exception as e:
                await queue.put("data: %s\n\n" % json.dumps({"chunk": "Error: %s" % str(e)}))
            finally:
                await queue.put(None)

        task = asyncio.create_task(run_agent())
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
        yield "data: [DONE]\n\n"
        await task

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},
    )

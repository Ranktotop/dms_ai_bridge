"""Chat router — Phase IV ReAct agent endpoint.

POST /chat/{frontend}         — single response
POST /chat/{frontend}/stream  — SSE streaming response
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from server.dependencies.auth import verify_api_key
from server.dependencies.services import get_react_agent, get_user_mapping_service, get_dms_clients
from server.models.requests import SearchRequest
from server.models.responses import ChatResponse
from server.user_mapping.UserMappingService import UserMappingService
from services.rag_search.agent.ReActAgent import ReActAgent
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from services.rag_search.helper.IdentityHelper import IdentityHelper

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(verify_api_key)])


@router.post("/{frontend}")
async def chat_documents(
    frontend: str,
    body: SearchRequest,
    react_agent: ReActAgent = Depends(get_react_agent),
    user_mapping_service: UserMappingService = Depends(get_user_mapping_service),
    dms_clients: list[DMSClientInterface] = Depends(get_dms_clients),
) -> ChatResponse:
    """Run the ReAct agent for a natural language query and return a synthesised answer.

    Resolves the frontend user_id to DMS owner_id(s) via UserMappingService.
    Returns HTTP 403 if the user has no mapping in any configured engine.

    Args:
        frontend: AI system identifier from the URL path (e.g. "openwebui").
        body: JSON body with query, user_id, limit, and optional chat_history.
        react_agent: Injected ReActAgent from app state.
        user_mapping_service: Injected mapping service from app state.
        dms_clients: Injected list of DMS clients.

    Returns:
        ChatResponse: Synthesised answer from the ReAct agent.
    """
    identity_helper = IdentityHelper(
        user_mapping_service=user_mapping_service,
        dms_clients=dms_clients,
        frontend=frontend,
        user_id=body.user_id,
    )
    if not identity_helper.has_mappings():
        raise HTTPException(
            status_code=403,
            detail="No mapping found for frontend '%s', user_id '%s' in any configured engine."
            % (frontend, body.user_id),
        )
    agent_response = await react_agent.do_run(
        query=body.query,
        identity_helper=identity_helper,
        chat_history=body.chat_history,
    )
    return ChatResponse(query=body.query, answer=agent_response.answer)


@router.post("/{frontend}/stream")
async def chat_documents_stream(
    frontend: str,
    body: SearchRequest,
    react_agent: ReActAgent = Depends(get_react_agent),
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
    identity_helper = IdentityHelper(
        user_mapping_service=user_mapping_service,
        dms_clients=dms_clients,
        frontend=frontend,
        user_id=body.user_id,
    )
    if not identity_helper.has_mappings():
        raise HTTPException(
            status_code=403,
            detail="No mapping found for frontend '%s', user_id '%s' in any configured engine."
            % (frontend, body.user_id),
        )

    async def event_generator():
        agent_response = await react_agent.do_run(
            query=body.query,
            identity_helper=identity_helper,
            chat_history=body.chat_history,
        )
        for word in agent_response.answer.split(" "):
            yield "data: %s\n\n" % json.dumps({"chunk": word + " "})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},
    )

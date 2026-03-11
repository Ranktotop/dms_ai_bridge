"""Chat router — server-side ReAct agent endpoints.

The agent loop runs entirely on the bridge side; frontends receive either
a complete JSON response or a stream of Server-Sent Events (SSE).

POST /chat/{frontend}          — non-streaming, returns ChatResponse
POST /chat/{frontend}/stream   — SSE stream of typed AgentEvent objects
"""
from __future__ import annotations

import dataclasses
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from collections.abc import AsyncGenerator

from server.dependencies.auth import verify_api_key
from server.dependencies.services import get_search_service, get_user_mapping_service, get_dms_clients, get_agent_service
from server.models.requests import ChatRequest
from server.models.responses import ChatResponse, CitationItem
from server.user_mapping.UserMappingService import UserMappingService
from services.agent.AgentService import AgentService
from services.agent.models.AgentEvent import AgentAnswerEvent, AgentEvent, CitationRef
from services.agent.models.AgentResponse import AgentResponse
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from services.rag_search.SearchService import SearchService
from server.dependencies.identity import get_verified_identity_helper

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(verify_api_key)])
logger = logging.getLogger(__name__)


##########################################
############# HELPERS ####################
##########################################

def _to_citation_items(citations: list[CitationRef]) -> list[CitationItem]:
    """Convert internal CitationRef objects to API response CitationItem models.

    Args:
        citations: List of CitationRef dataclass instances.

    Returns:
        List of CitationItem Pydantic models.
    """
    return [
        CitationItem(
            dms_doc_id=c.dms_doc_id,
            dms_engine=c.dms_engine,
            title=c.title,
            view_url=c.view_url,
        )
        for c in citations
    ]


async def _sse_stream(
    agent_service: AgentService,
    query: str,
    identity_helper,
    chat_history: list[dict],
    max_iterations: int,
    tool_context: dict,
) -> AsyncGenerator[str, None]:
    """Iterate agent events and format each as an SSE data line.

    Args:
        agent_service: The running AgentService instance.
        query: User query string.
        identity_helper: Resolved identity for search isolation.
        chat_history: Prior conversation turns.
        max_iterations: Maximum ReAct loop iterations.
        tool_context: Client-supplied params merged into every tool call.

    Yields:
        SSE-formatted strings (each ending with double newline).
    """
    async for event in agent_service.do_run_stream(
        query=query,
        identity_helper=identity_helper,
        chat_history=chat_history,
        max_iterations=max_iterations,
        tool_context=tool_context,
    ):
        payload = dataclasses.asdict(event)
        yield "data: %s\n\n" % json.dumps(payload, ensure_ascii=False)
    yield "data: [DONE]\n\n"


##########################################
############# ROUTES #####################
##########################################

@router.post("/{frontend}")
async def chat(
    frontend: str,
    body: ChatRequest,
    agent_service: AgentService = Depends(get_agent_service),
    user_mapping_service: UserMappingService = Depends(get_user_mapping_service),
    dms_clients: list[DMSClientInterface] = Depends(get_dms_clients),
) -> ChatResponse:
    """Run the ReAct agent and return a complete response.

    Args:
        frontend: AI system identifier from the URL path.
        body: Request body with user_id, query, and optional chat_history.

    Returns:
        ChatResponse with the final answer, citations, and tool call history.

    Raises:
        HTTPException 403: If the user has no identity mapping.
    """
    try:
        identity_helper = get_verified_identity_helper(
            frontend, body.user_id, user_mapping_service, dms_clients
        )
    except Exception as e:
        logger.warning("403 on /chat/%s — user_id=%r not mapped: %s", frontend, body.user_id, e)
        raise HTTPException(status_code=403, detail=str(e))

    result: AgentResponse = await agent_service.do_run(
        query=body.query,
        identity_helper=identity_helper,
        chat_history=body.chat_history,
        max_iterations=body.max_iterations,
        tool_context=body.tool_context,
    )
    return ChatResponse(
        query=result.query,
        answer=result.answer,
        citations=_to_citation_items(result.citations),
        tool_calls=result.tool_calls,
    )


@router.post("/{frontend}/stream")
async def chat_stream(
    frontend: str,
    body: ChatRequest,
    agent_service: AgentService = Depends(get_agent_service),
    user_mapping_service: UserMappingService = Depends(get_user_mapping_service),
    dms_clients: list[DMSClientInterface] = Depends(get_dms_clients),
) -> StreamingResponse:
    """Run the ReAct agent and stream typed events as Server-Sent Events.

    Each event is a JSON-serialised AgentEvent dict on a 'data:' SSE line.
    The stream ends with 'data: [DONE]'.

    Event types emitted:
        thought  — LLM reasoning step
        step     — tool execution starting
        retry    — parse failure, LLM being retried
        answer   — final answer (last event before [DONE])
        error    — unrecoverable failure

    Args:
        frontend: AI system identifier from the URL path.
        body: Request body with user_id, query, and optional chat_history.

    Returns:
        StreamingResponse with text/event-stream content type.

    Raises:
        HTTPException 403: If the user has no identity mapping.
    """
    try:
        identity_helper = get_verified_identity_helper(
            frontend, body.user_id, user_mapping_service, dms_clients
        )
    except Exception as e:
        logger.warning("403 on /chat/%s — user_id=%r not mapped: %s", frontend, body.user_id, e)
        raise HTTPException(status_code=403, detail=str(e))

    return StreamingResponse(
        _sse_stream(
            agent_service=agent_service,
            query=body.query,
            identity_helper=identity_helper,
            chat_history=body.chat_history,
            max_iterations=body.max_iterations,
            tool_context=body.tool_context,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

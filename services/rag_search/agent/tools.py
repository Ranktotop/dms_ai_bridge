"""Tool functions and context container for the ReAct agent."""
import json
from dataclasses import dataclass

from services.rag_search.SearchService import SearchService
from services.rag_search.helper.IdentityHelper import IdentityHelper
from shared.clients.rag.models.Point import PointHighDetails


@dataclass
class AgentToolContext:
    search_service: SearchService
    identity_helper: IdentityHelper


async def tool_search_documents(query: str, context: AgentToolContext, limit: int = 5) -> str:
    """Search documents by semantic similarity.
    Returns a formatted text listing of matching documents with title, score, and content preview."""
    results: list[PointHighDetails] = await context.search_service.do_search(
        query=query,
        identity_helper=context.identity_helper,
        limit=limit,
    )
    if not results:
        return "No documents found matching the query."
    lines = ["Found %d document(s):" % len(results)]
    for i, r in enumerate(results, 1):
        preview = (r.chunk_text or "")[:200].replace("\n", " ")
        lines.append("%d. %s (score: %.3f)" % (i, r.title or r.dms_doc_id, r.score or 0.0))
        if preview:
            lines.append("   Preview: %s..." % preview)
    return "\n".join(lines)


async def tool_list_filter_options(context: AgentToolContext) -> str:
    """List available filter options (correspondents, document types, tags) for the user's documents.
    Returns a JSON string with available filter values."""
    try:
        identities = context.identity_helper.get_identities()
        result: dict[str, list[str]] = {}
        for identity in identities:
            cache_data = await context.search_service._cache_helper.get_data(
                identity.dms_engine, identity.owner_id
            )
            if cache_data.correspondents:
                if "correspondents" not in result:
                    result["correspondents"] = []
                result["correspondents"].extend(
                    v for v in cache_data.correspondents if v not in result["correspondents"]
                )
            if cache_data.document_types:
                if "document_types" not in result:
                    result["document_types"] = []
                result["document_types"].extend(
                    v for v in cache_data.document_types if v not in result["document_types"]
                )
            if cache_data.tags:
                if "tags" not in result:
                    result["tags"] = []
                result["tags"].extend(
                    v for v in cache_data.tags if v not in result["tags"]
                )
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return "Filter options unavailable: %s" % str(exc)


async def tool_get_document_details(document_id: str, context: AgentToolContext) -> str:
    """Get details for a specific document by its DMS document ID.
    Returns formatted document metadata."""
    try:
        results: list[PointHighDetails] = await context.search_service.do_search(
            query=document_id,
            identity_helper=context.identity_helper,
        )
        # Find exact match by dms_doc_id
        for r in results:
            if str(r.dms_doc_id) == document_id:
                lines = [
                    "Document ID: %s" % r.dms_doc_id,
                    "Title: %s" % (r.title or "Unknown"),
                    "Engine: %s" % (r.dms_engine or "Unknown"),
                ]
                if r.category_name:
                    lines.append("Correspondent: %s" % r.category_name)
                if r.type_name:
                    lines.append("Document Type: %s" % r.type_name)
                if r.label_names:
                    lines.append("Tags: %s" % ", ".join(r.label_names))
                if r.created:
                    lines.append("Created: %s" % r.created)
                if r.chunk_text:
                    lines.append("Content: %s" % r.chunk_text[:500])
                return "\n".join(lines)
        return "Document with ID '%s' not found." % document_id
    except Exception as exc:
        return "Error fetching document details: %s" % str(exc)


TOOL_REGISTRY = {
    "search_documents": tool_search_documents,
    "list_filter_options": tool_list_filter_options,
    "get_document_details": tool_get_document_details,
}

TOOL_DESCRIPTIONS = """Available tools:

1. search_documents(query, limit=5)
   Search documents by semantic similarity. Returns matching documents with title and content preview.
   Parameters: query (string, required), limit (int, optional, default=5)

2. list_filter_options()
   List available filter options (correspondents, document types, tags).
   Parameters: none

3. get_document_details(document_id)
   Get details for a specific document by its DMS document ID.
   Parameters: document_id (string, required)"""

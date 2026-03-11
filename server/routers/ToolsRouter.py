"""Tools router — direct tool endpoints for AI frontend integrations.

Each endpoint performs exactly one operation and returns structured data.
The frontend's LLM handles orchestration and synthesis.

POST /tools/{frontend}/search_documents
POST /tools/{frontend}/list_filter_options
POST /tools/{frontend}/get_document_details
POST /tools/{frontend}/get_document_full
"""
from fastapi import APIRouter, Depends, HTTPException

from server.dependencies.auth import verify_api_key
from server.dependencies.services import get_search_service, get_user_mapping_service, get_dms_clients
from shared.clients.rag.models.Document import DocumentBase
from server.models.requests import (
    ToolSearchRequest,
    ToolFilterOptionsRequest,
    ToolDocumentRequest,
    ToolDocumentFullRequest,
)
from server.models.responses import (
    ToolSearchResponse,
    ToolSearchResult,
    ToolFilterOptionsResponse,
    ToolDocumentResponse,
    ToolDocumentFullResponse,
)
from server.user_mapping.UserMappingService import UserMappingService
from services.rag_search.SearchService import SearchService
from shared.clients.dms.DMSClientInterface import DMSClientInterface
from shared.clients.rag.models.Point import PointHighDetails
from server.dependencies.identity import get_verified_identity_helper

router = APIRouter(prefix="/tools", tags=["tools"], dependencies=[Depends(verify_api_key)])

@router.post("/{frontend}/search_documents")
async def tool_search_documents(
    frontend: str,
    body: ToolSearchRequest,
    search_service: SearchService = Depends(get_search_service),
    user_mapping_service: UserMappingService = Depends(get_user_mapping_service),
    dms_clients: list[DMSClientInterface] = Depends(get_dms_clients),
) -> ToolSearchResponse:
    """Search documents by semantic similarity and return merged results per document.

    Args:
        frontend: AI system identifier from the URL path.
        body: Request body with user_id, query, and optional limit.

    Returns:
        ToolSearchResponse: Merged document results with metadata.
    """
    #validate identity
    try:
        identity_helper = get_verified_identity_helper(frontend, body.user_id, user_mapping_service, dms_clients)
    except Exception as e:
        raise HTTPException(
            status_code=403,
            detail=str(e)
        )
    
    points = await search_service.do_search(
        query=body.query,
        identity_helper=identity_helper,
        limit=body.limit,
        merge_results=True,  # request merging at the service level for efficiency
    )
    results = [
        ToolSearchResult(
            dms_doc_id=p.dms_doc_id or "",
            title=p.title or "",
            content=p.chunk_text or "",
            score=p.score or 0.0,
            created=p.created,
            correspondent=p.category_name,
            document_type=p.type_name,
            tags=p.label_names or [],
            view_url=search_service.get_document_url_by_id(dms_engine=p.dms_engine, doc_id=p.dms_doc_id),
        )
        for p in points
    ]
    return ToolSearchResponse(results=results)


@router.post("/{frontend}/list_filter_options")
async def tool_list_filter_options(
    frontend: str,
    body: ToolFilterOptionsRequest,
    search_service: SearchService = Depends(get_search_service),
    user_mapping_service: UserMappingService = Depends(get_user_mapping_service),
    dms_clients: list[DMSClientInterface] = Depends(get_dms_clients),
) -> ToolFilterOptionsResponse:
    """List available filter options (correspondents, document types, tags).

    Args:
        frontend: AI system identifier from the URL path.
        body: Request body with user_id.

    Returns:
        ToolFilterOptionsResponse: Available filter values per category.
    """
    #validate identity
    try:
        identity_helper = get_verified_identity_helper(frontend, body.user_id, user_mapping_service, dms_clients)
    except Exception as e:
        raise HTTPException(
            status_code=403,
            detail=str(e)
        )
    
    options = await search_service.do_get_filter_options(identity_helper=identity_helper)
    return ToolFilterOptionsResponse(
        correspondents=options.get("correspondents", []),
        document_types=options.get("document_types", []),
        tags=options.get("tags", []),
    )


@router.post("/{frontend}/get_document_details")
async def tool_get_document_details(
    frontend: str,
    body: ToolDocumentRequest,
    search_service: SearchService = Depends(get_search_service),
    user_mapping_service: UserMappingService = Depends(get_user_mapping_service),
    dms_clients: list[DMSClientInterface] = Depends(get_dms_clients),
) -> list[ToolDocumentResponse]:
    """Fetch metadata and content preview for a specific document.

    Args:
        frontend: AI system identifier from the URL path.
        body: Request body with user_id and document_id.

    Returns:
        list[ToolDocumentResponse]: Full documents
    Raises:
        HTTPException 404: If the document is not found or the user has no access.
    """
    #validate identity
    try:
        identity_helper = get_verified_identity_helper(frontend, body.user_id, user_mapping_service, dms_clients)
    except Exception as e:
        raise HTTPException(
            status_code=403,
            detail=str(e)
        )
    
    #collect all chunks for the document across identities (in case of multiple DMS access)
    all_docs: list[DocumentBase] = []
    for identity in identity_helper.get_identities():
        docs:list[DocumentBase] = await search_service.do_fetch_full_by_doc_id(
            doc_id=body.document_id,
            dms_engine=identity.dms_engine,
            owner_id=identity.owner_id,
        )
        all_docs.extend(docs)

    if not all_docs:
        raise HTTPException(
            status_code=404,
            detail="Document '%s' not found or access denied." % body.document_id,
        )
    
    return [
        ToolDocumentResponse(
            dms_doc_id=d.dms_doc_id,
            title=d.title or "",
            content=d.content,
            created=d.created,
            correspondent=d.category_name,
            document_type=d.type_name,
            tags=d.label_names or [],
            view_url=search_service.get_document_url_by_id(dms_engine=d.dms_engine, doc_id=d.dms_doc_id),
        )
        for d in all_docs
    ]


@router.post("/{frontend}/get_document_full")
async def tool_get_document_full(
    frontend: str,
    body: ToolDocumentFullRequest,
    search_service: SearchService = Depends(get_search_service),
    user_mapping_service: UserMappingService = Depends(get_user_mapping_service),
    dms_clients: list[DMSClientInterface] = Depends(get_dms_clients),
) -> ToolDocumentFullResponse:
    """Fetch the full text content of a document with pagination support.

    Args:
        frontend: AI system identifier from the URL path.
        body: Request body with user_id, document_id, and optional start_char.

    Returns:
        ToolDocumentFullResponse: Paginated content, total length, and next offset.

    Raises:
        HTTPException 404: If the document is not found or the user has no access.
    """
    #validate identity
    try:
        identity_helper = get_verified_identity_helper(frontend, body.user_id, user_mapping_service, dms_clients)
    except Exception as e:
        raise HTTPException(
            status_code=403,
            detail=str(e)
        )
    
    #collect all chunks for the document across identities (in case of multiple DMS access)
    all_docs: list[DocumentBase] = []
    for identity in identity_helper.get_identities():
        docs:list[DocumentBase] = await search_service.do_fetch_full_by_doc_id(
            doc_id=body.document_id,
            dms_engine=identity.dms_engine,
            owner_id=identity.owner_id,
        )
        all_docs.extend(docs)

    if not all_docs:
        raise HTTPException(
            status_code=404,
            detail="Document '%s' not found or access denied." % body.document_id,
        )

    # create full content. If there are multiple docs, used # Document %d as delimiter
    full_content = all_docs[0].content
    if len(all_docs) > 1:
        full_content = ""
        delimiter = ""
        for idx, doc in enumerate(all_docs):
            full_content += f"{delimiter}--- Document {idx+1} ---\n\n"
            full_content += doc.content or ""
            delimiter = f"\n\n"            
    total_length = len(full_content)
    page_size = 4000
    end_char = body.start_char + page_size
    page = full_content[body.start_char:end_char]
    next_start_char = end_char if end_char < total_length else None
    return ToolDocumentFullResponse(
        content=page,
        total_length=total_length,
        next_start_char=next_start_char,
    )

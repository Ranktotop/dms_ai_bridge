from pydantic import BaseModel


class SearchResultItem(BaseModel):
    dms_doc_id: str
    title: str
    chunk_text: str | None
    score: float
    created: str | None
    category_name: str | None
    type_name: str | None
    label_names: list[str]


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResultItem]
    total: int


class ToolSearchResult(BaseModel):
    dms_doc_id: str
    title: str
    content: str
    score: float
    created: str | None
    correspondent: str | None
    document_type: str | None
    tags: list[str]
    view_url: str | None = None
    # arbitrary DMS-specific fields (e.g. Paperless custom fields); field_name → value
    custom_fields: dict[str, str] = {}


class ToolSearchResponse(BaseModel):
    results: list[ToolSearchResult]


class ToolFilterOptionsResponse(BaseModel):
    correspondents: list[str]
    document_types: list[str]
    tags: list[str]
    # DMS custom field names mapped to their distinct values, sorted; used for filter UIs
    custom_fields: dict[str, list[str]] = {}


class ToolDocumentResponse(BaseModel):
    dms_doc_id: str
    title: str
    content: str
    created: str | None
    correspondent: str | None
    document_type: str | None
    tags: list[str]
    view_url: str | None = None
    # arbitrary DMS-specific fields (e.g. Paperless custom fields); field_name → value
    custom_fields: dict[str, str] = {}


class ToolDocumentFullResponse(BaseModel):
    content: str
    total_length: int
    next_start_char: int | None


class CitationItem(BaseModel):
    dms_doc_id: str
    dms_engine: str
    title: str | None = None
    view_url: str | None = None


class ChatResponse(BaseModel):
    query: str
    answer: str
    citations: list[CitationItem]
    tool_calls: list[str]

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


class ChatResponse(BaseModel):
    query: str
    answer: str

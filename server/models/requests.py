from pydantic import BaseModel


class WebhookRequest(BaseModel):
    document_id: int


class SearchRequest(BaseModel):
    query: str
    user_id: str
    limit: int = 10
    chat_history: list[dict] = []


class ToolSearchRequest(BaseModel):
    user_id: str
    query: str
    limit: int = 5


class ToolFilterOptionsRequest(BaseModel):
    user_id: str


class ToolDocumentRequest(BaseModel):
    user_id: str
    document_id: str


class ToolDocumentFullRequest(BaseModel):
    user_id: str
    document_id: str
    start_char: int = 0

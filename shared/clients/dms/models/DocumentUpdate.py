from dataclasses import dataclass, field


@dataclass
class DocumentUpdateRequest:
    """Generic, backend-agnostic model for a document metadata update operation.

    None values are omitted from the actual HTTP request so callers only need
    to set the fields they want to change.
    """

    title: str | None = None
    correspondent_id: int | None = None
    document_type_id: int | None = None
    tag_ids: list[int] = field(default_factory=list)
    content: str | None = None
    created_date: str | None = None  # ISO YYYY-MM-DD
    owner_id: int | None = None

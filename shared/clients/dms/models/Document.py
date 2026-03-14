from datetime import datetime
from shared.clients.dms.models.Correspondent import CorrespondentDetails
from shared.clients.dms.models.Tag import TagDetails
from shared.clients.dms.models.Owner import OwnerDetails
from shared.clients.dms.models.DocumentType import DocumentTypeDetails
"""Generic DMS document model — backend-independent."""

from pydantic import BaseModel

class DocumentBase(BaseModel):
    """
    Represents a single document with all its metadata, as returned by a DMS client.
    """
    engine:str
    id: str

class DocumentDetails(DocumentBase):
    """
    Represents a single document with all its metadata, as returned by a DMS client.
    """
    correspondent_id: str | None = None
    document_type_id: str | None = None
    title: str | None = None
    content: str | None = None
    tag_ids: list[str] = []
    created_date: datetime | None = None
    owner_id : str | None = None
    mime_type: str | None = None
    file_name: str | None = None
    # raw custom field values keyed by field id (str) — resolved to name→value in fill_cache()
    custom_field_ids: dict[str, str] = {}

class DocumentHighDetails(DocumentDetails):
    """
    Represents a single document with all its metadata, as returned by a DMS client.
    """
    correspondent: CorrespondentDetails | None = None
    owner: OwnerDetails | None = None
    tags: list[TagDetails] = []
    document_type: DocumentTypeDetails | None = None
    # resolved custom field values keyed by field name — always a dict, never None
    custom_fields: dict[str, str] = {}

class DocumentsListResponse(BaseModel):
    """
    Represents the response from a DMS when fetching a list of documents.
    """
    engine:str
    documents : list[DocumentBase] = []
    currentPage: int
    nextPage: int | None = None
    previousPage: int | None = None
    overallCount: int | None = None
    pageLength: int | None = None
    lastPage: int | None = None

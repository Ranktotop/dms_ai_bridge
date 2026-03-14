from datetime import datetime
"""Generic RAG point model — backend-independent."""

from pydantic import BaseModel

#######################################
############ RAG RESPONSES ############
#######################################

class PointBase(BaseModel):
    """
    Represents a single point with all its metadata, as returned by a RAG client.

    Attributes:
        engine:           Identifier of the source RAG (e.g. "qdrant").
        id:               Point ID as assigned by the source RAG.
    """
    engine:str
    id: str

class PointDetails(PointBase):
    """
    Represents a single point with all its metadata, as returned by a RAG client.

    Attributes:
        engine:           Identifier of the source RAG (e.g. "qdrant").
        id:               Point ID as assigned by the source RAG.
        dms_engine:       Identifier of the source DMS (e.g. "paperless").
        dms_doc_id:       Document ID as assigned by the source DMS.
        content_hash:     SHA-256 hex digest of the document's content and key metadata.
                          Identical across all chunks of the same document.
                          Used to skip re-embedding unchanged documents on subsequent syncs.
        title:            Human-readable document title.
        chunk_text:       Raw text content of this chunk.
        score:           Similarity score from a vector search; None for plain scrolls.
        created:          ISO-8601 creation date of the document, if available.
        category_name:    Human-readable name of the category.
        type_name:        Human-readable name of the document type.
        label_names:      Human-readable names of those labels/tags.
    """
    dms_doc_id: str | None = None
    dms_engine: str | None = None
    content_hash: str | None = None
    title: str | None = None
    chunk_text: str | None = None
    score: float | None = None
    created: str | None = None
    category_name: str | None = None
    type_name: str | None = None
    label_names: list[str] = []

    # arbitrary DMS-specific fields that don't map to a standard attribute; field_name → value
    custom_fields: dict[str, str] = {}

class PointHighDetails(PointDetails):
    """Generic metadata payload stored alongside each vector chunk in a RAG backend.

    Designed to be DMS-agnostic. All field names reflect general document
    concepts, not the terminology of any specific DMS (e.g. Paperless-ngx).

    The owner_id field is mandatory and enforced as a security invariant on
    every upsert and search operation — it must never be absent or None.

    Attributes:
        engine:           Identifier of the source RAG (e.g. "qdrant").
        id:               Point ID as assigned by the source RAG.
        dms_engine:       Identifier of the source DMS (e.g. "paperless").
        dms_doc_id:       Document ID as assigned by the source DMS.
        chunk_index:      Zero-based position of this chunk within the document.
        title:            Human-readable document title.
        owner_id:         MANDATORY — user ID in the DMS; used for access isolation.
        created:          ISO-8601 creation date of the document, if available.
        chunk_text:       Raw text content of this chunk.
        label_ids:        IDs of labels/tags attached to the document.
        label_names:      Human-readable names of those labels/tags.
        category_id:      ID of the document category (e.g. correspondent).
        category_name:    Human-readable name of the category.
        type_id:          ID of the document type classification.
        type_name:        Human-readable name of the document type.
        owner_username:   Username of the document owner, for display purposes.
        content_hash:     SHA-256 hex digest of the document's content and key metadata.
                          Identical across all chunks of the same document.
                          Used to skip re-embedding unchanged documents on subsequent syncs.
        vector:          Stored vector; None unless with_vector=True was requested.
        score:           Similarity score from a vector search; None for plain scrolls.
    """

    # Core identity
    chunk_index: int| None = None

    # Security invariant — never None
    owner_id: str| None = None

    # Generic label / tag fields — used for hard metadata filtering
    label_ids: list[str] = []

    # Generic category field (e.g. correspondent in Paperless)
    category_id: str | None = None

    # Generic type classification field (e.g. document_type in Paperless)
    type_id: str | None = None

    # Owner display name
    owner_username: str | None = None
    
    # arbitrary DMS-specific fields that don't map to a standard attribute; field_name → value
    custom_fields: dict[str, str] = {}

    # only on search results, not on scrolls
    vector: list[float] | None = None

    def get_as_prompt_search_result(self, max_chars: int = -1) -> str:
        """Erstellt eine formatierte Text-Repräsentation für ein LLM-Prompt."""
        content = self.chunk_text or ""
        if max_chars > 0:
            content = content[:max_chars] + ("..." if len(content) > max_chars else "")
        return "\n".join([
            "### %s" % self.title or self.dms_doc_id,
            "%.3f" % self.score or 0.0,
            "Content:",
            "---",
            "%s" % content,
            "---"])

    def get_as_prompt_details(self, max_chars: int = -1) -> str:
        """Erstellt eine formatierte Text-Repräsentation für ein LLM-Prompt."""
        content = self.chunk_text or ""
        if max_chars > 0:
            content = content[:max_chars] + ("..." if len(content) > max_chars else "")
        return "\n".join([
            "---",
            "Document ID: %s" % self.dms_doc_id,
            "Title: %s" % (self.title or "Unknown"),
            "Engine: %s" % (self.dms_engine or "Unknown"),
            "Correspondent: %s" % (self.category_name or "Unknown"),
            "Document Type: %s" % (self.type_name or "Unknown"),
            "Tags: %s" % (", ".join(self.label_names) or "None"),
            "Created: %s" % (self.created or "Unknown"),
            "Content: ```\n%s\n```" % content,
            "---"])

class PointsListResponse(BaseModel):
    """
    Represents the response from a RAG client when fetching a list of points.
    """
    engine:str
    points : list[PointHighDetails] = []
    currentPage: int
    nextPage: int | None = None
    nextPageId: str | None = None
    previousPage: int | None = None
    overallCount: int | None = None
    pageLength: int | None = None
    lastPage: int | None = None

class PointsSearchResponse(BaseModel):
    """
    Represents the response from a RAG client when performing a vector similarity search.
    """
    query: str
    points : list[PointDetails] = []
    total: int

#######################################
############ RAG REQUESTS #############
#######################################

class PointDetailsRequest(BaseModel):
    """
    Represents a single point with all its metadata, as sent into a RAG client.

    Attributes:
        dms_engine:       Identifier of the source DMS (e.g. "paperless").
        dms_doc_id:       Document ID as assigned by the source DMS.
        content_hash:     SHA-256 hex digest of the document's content and key metadata.
                          Identical across all chunks of the same document.
                          Used to skip re-embedding unchanged documents on subsequent syncs.
    """
    dms_doc_id: str | None = None
    dms_engine: str | None = None
    content_hash: str | None = None

class PointHighDetailsRequest(PointDetailsRequest):
    """Generic metadata payload for storing in a RAG backend.

    Designed to be DMS-agnostic. All field names reflect general document
    concepts, not the terminology of any specific DMS (e.g. Paperless-ngx).

    The owner_id field is mandatory and enforced as a security invariant on
    every upsert and search operation — it must never be absent or None.

    Attributes:
        engine:           Identifier of the source RAG (e.g. "qdrant").
        id:               Point ID as assigned by the source RAG.
        dms_engine:       Identifier of the source DMS (e.g. "paperless").
        dms_doc_id:       Document ID as assigned by the source DMS.
        chunk_index:      Zero-based position of this chunk within the document.
        title:            Human-readable document title.
        owner_id:         MANDATORY — user ID in the DMS; used for access isolation.
        created:          ISO-8601 creation date of the document, if available.
        chunk_text:       Raw text content of this chunk.
        label_ids:        IDs of labels/tags attached to the document.
        label_names:      Human-readable names of those labels/tags.
        category_id:      ID of the document category (e.g. correspondent).
        category_name:    Human-readable name of the category.
        type_id:          ID of the document type classification.
        type_name:        Human-readable name of the document type.
        owner_username:   Username of the document owner, for display purposes.
        content_hash:     SHA-256 hex digest of the document's content and key metadata.
                          Identical across all chunks of the same document.
                          Used to skip re-embedding unchanged documents on subsequent syncs.
    """

    # Core identity
    chunk_index: int
    title: str

    # Security invariant — never None
    owner_id: str

    # Temporal metadata
    created: str | None = None

    # Chunk content
    chunk_text: str | None = None

    # Generic label / tag fields — used for hard metadata filtering
    label_ids: list[str] = []
    label_names: list[str] = []

    # Generic category field (e.g. correspondent in Paperless)
    category_id: str | None = None
    category_name: str | None = None

    # Generic type classification field (e.g. document_type in Paperless)
    type_id: str | None = None
    type_name: str | None = None

    # Owner display name
    owner_username: str | None = None

    # arbitrary DMS-specific fields that don't map to a standard attribute; field_name → value
    custom_fields: dict[str, str] = {}

class PointUpsert(BaseModel):
    """Typed input for a single point upsert into a RAG backend.

    Attributes:
        id:      Deterministic UUID5 string identifying the point.
        vector:  Embedding vector produced by the LLM client.
        payload: Typed metadata payload; owner_id must never be absent.
    """

    id: str
    vector: list[float]
    payload: PointHighDetailsRequest



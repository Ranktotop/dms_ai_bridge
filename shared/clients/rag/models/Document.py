from datetime import datetime
"""Generic RAG point model — backend-independent."""

from pydantic import BaseModel

#######################################
############ RAG RESPONSES ############
#######################################

class DocumentBase(BaseModel):
    """
    Represents a single point with all its metadata, as returned by a RAG client.

    Attributes:
        rag_engine:               Identifier of the source RAG (e.g. "qdrant").
        dms_engine:               Identifier of the source DMS (e.g. "paperless").
        dms_doc_id:               Document ID as assigned by the source DMS.
        dms_owner_id:             User ID of the document owner in the DMS; used for access control.
        dms_owner_username:       Username of the document owner, for display purposes.
        point_ids:                List of all point IDs (chunks) belonging to the same document.
        title:                    Human-readable document title.
        content:                  Concatenated text content of all chunks, for preview purposes.
        category_name:            Human-readable name of the document's category (e.g. correspondent).
        dms_category_id:          ID of the document's category in the DMS.
        type_name:                Human-readable name of the document's type classification.
        dms_type_id:              ID of the document's type classification in the DMS.
        label_names:              Human-readable names of labels/tags attached to the document.
        dms_label_ids:            IDs of labels/tags attached to the document in the DMS.
        created:                  ISO-8601 creation date of the document, if available.
    """
    rag_engine:str
    dms_engine:str
    dms_doc_id: str
    dms_owner_id: str| None = None
    dms_owner_username: str | None = None
    point_ids: list[str] = []
    title: str | None = None
    content: str | None = None
    category_name: str | None = None
    dms_category_id: str | None = None
    type_name: str | None = None
    dms_type_id: str | None = None
    label_names: list[str] = []
    dms_label_ids: list[str] = []
    created: str | None = None
    # arbitrary DMS custom fields resolved from chunks — field_name → value
    custom_fields: dict[str, str] = {}

from dataclasses import dataclass, field
from services.ingestion.mail.helper.MailAccountConfigHelper import MailFolderConfig

@dataclass
class MailDocSpec:
    """Carries all per-document metadata through the ingestion phases.

    Created during _parse_messages and threaded through every phase so each
    phase has the full context needed to act or roll back correctly.

    Forced fields always win over LLM output (body documents: email header values are
    authoritative). Fallback fields are used only when the LLM extracted nothing
    (attachment documents: email header provides a meaningful last resort).
    """

    source_file: str                     # absolute path to the temp file (body .txt/.md or attachment)
    owner_id: int                        # DMS owner_id resolved from the recipient mapping
    folder: MailFolderConfig             # folder config providing document_type and tag hints
    content_prefix: str | None           # mail-context header prepended to content before DMS update
    message_id: str                      # ties this doc to its parent message for rollback grouping
    doc_idx: int                         # 1-based global index across all messages in this folder run
    # forced: email header values that always win over LLM output (set for body documents)
    forced_title: str | None = None
    forced_correspondent: str | None = None
    forced_year: str | None = None
    forced_month: str | None = None
    forced_day: str | None = None
    # fallback: email header values used only when LLM extracted nothing (set for attachments)
    fallback_title: str | None = None
    fallback_correspondent: str | None = None
    fallback_year: str | None = None
    fallback_month: str | None = None
    fallback_day: str | None = None
    custom_fields: dict[str, str] = field(default_factory=dict)   # arbitrary DMS custom fields to set on the document

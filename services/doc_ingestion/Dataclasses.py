
from dataclasses import dataclass, field

@dataclass
class DocMetadata:
    """Metadata extracted for a single document.

    Fields are populated from two sources (path template takes precedence over LLM):
    - Path template parsing: correspondent, document_type, year, month, day, title, tags
    - LLM extraction from document content: fills any fields left empty by path parsing

    All fields are optional strings. Numeric fields (year, month, day) are stored as
    strings to avoid lossy int conversion for values like "01".
    Tags collected from elastic-zone path segments are accumulated in ``tags``.
    """

    correspondent: str | None = None
    document_type: str | None = None
    year: str | None = None
    month: str | None = None
    day: str | None = None
    title: str | None = None
    filename: str | None = None
    tags: list[str] = field(default_factory=list)

from dataclasses import dataclass, field

@dataclass
class DocMetadata:
    """
    Metadata extracted for a single document.

    All fields are optional strings. Except for filename, which is always populated with the source file's basename. 
    Tags is a list of strings, defaulting to an empty list if not provided.
    """
    filename: str
    correspondent: str | None = None
    document_type: str | None = None
    year: str | None = None
    month: str | None = None
    day: str | None = None
    quarter: str | None = None
    title: str | None = None
    tags: list[str] = field(default_factory=list)

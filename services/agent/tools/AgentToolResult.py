"""Data models for tool execution results, including citation tracking."""
from dataclasses import dataclass, field


@dataclass
class CitationRef:
    dms_doc_id: str
    dms_engine: str
    title: str | None = None


@dataclass
class AgentToolResult:
    observation: str
    citations: list[CitationRef] = field(default_factory=list)

"""Final non-streaming agent result model."""
from __future__ import annotations

from dataclasses import dataclass, field

from services.agent.models.AgentEvent import CitationRef


@dataclass
class AgentResponse:
    query: str
    answer: str
    citations: list[CitationRef] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)

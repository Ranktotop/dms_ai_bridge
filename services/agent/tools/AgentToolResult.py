"""Tool execution result model."""
from __future__ import annotations

from dataclasses import dataclass, field

from services.agent.models.AgentEvent import CitationRef


@dataclass
class AgentToolResult:
    observation: str
    citations: list[CitationRef] = field(default_factory=list)

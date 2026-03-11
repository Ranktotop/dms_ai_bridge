"""Parsed and validated tool call from an LLM response."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentToolCall:
    """
    Represents a tool call parsed from an LLM response. 
    Contains the thought process, the action to take, 
    the arguments for the action, and optionally the answer from the tool.    
    """
    thought: str
    action: str
    args: dict
    answer: str | None

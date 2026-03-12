"""Typed event models for the ReAct agent loop."""
from __future__ import annotations
from shared.logging.logging_setup import ColorLogger
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class CitationRef:
    dms_doc_id: str
    dms_engine: str
    title: str | None = None
    view_url: str | None = None


class AgentEvent(ABC):
    """Base class for all ReAct agent loop events.

    Every concrete event must implement log() so the AgentService can call it
    automatically when the event is emitted — no manual logging at yield sites.
    """

    @abstractmethod
    def log(self, logger: ColorLogger) -> None:
        """Log this event at the appropriate level."""


@dataclass
class AgentThoughtEvent(AgentEvent):
    thought: str
    iteration: int
    type: Literal["thought"] = "thought"

    def log(self, logger: ColorLogger) -> None:
        logger.info("[iter=%d] Thought: %s", self.iteration, self.thought, color="cyan")


@dataclass
class AgentStepEvent(AgentEvent):
    tool_name: str
    hint: str
    iteration: int
    type: Literal["step"] = "step"

    def log(self, logger: ColorLogger) -> None:
        logger.info("[iter=%d] Step: %s", self.iteration, self.tool_name, color="blue")


@dataclass
class AgentRetryEvent(AgentEvent):
    reason: str
    iteration: int
    type: Literal["retry"] = "retry"

    def log(self, logger: ColorLogger) -> None:
        logger.info("[iter=%d] Retry: %s", self.iteration, self.reason, color="yellow")


@dataclass
class AgentAnswerEvent(AgentEvent):
    text: str
    citations: list[CitationRef] = field(default_factory=list)
    type: Literal["answer"] = "answer"

    def log(self, logger: ColorLogger) -> None:
        logger.info("Answer (%d citation(s)): %s", len(self.citations), self.text[:120], color="green")


@dataclass
class AgentErrorEvent(AgentEvent):
    """
    Fired on unrecoverable errors in the agent loop.
    E.g.: system prompt load failure, LLM call failure, parse failure after retries.
    """
    message: str
    type: Literal["error"] = "error"

    def log(self, logger: ColorLogger) -> None:
        logger.error("Agent error: %s", self.message, color="red")

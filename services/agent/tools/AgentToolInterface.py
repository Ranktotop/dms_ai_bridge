"""Abstract base class for all ReAct agent tools."""
from __future__ import annotations

from abc import ABC, abstractmethod

from shared.helper.HelperConfig import HelperConfig
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from services.rag_search.SearchService import SearchService
from services.rag_search.helper.IdentityHelper import IdentityHelper
from services.agent.tools.AgentToolResult import AgentToolResult


class AgentToolInterface(ABC):
    """ABC for every tool callable by the ReAct agent.

    Subclasses must implement all abstract getter methods and do_execute().
    Errors must never propagate from do_execute() — return an AgentToolResult
    with observation='Error: ...' instead.
    """

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def __init__(self, helper_config: HelperConfig, search_service: SearchService, llm_client: LLMClientInterface) -> None:
        self.logging = helper_config.get_logger()
        self._helper_config = helper_config
        self._search_service = search_service
        # llm_client gives tools access to dynamic values like get_chat_model_max_chars()
        # that are resolved at runtime rather than read from env
        self._llm_client = llm_client

    ##########################################
    ############## GETTER ####################
    ##########################################

    @abstractmethod
    def get_name(self) -> str:
        """Return the tool name used in LLM action fields (e.g. 'search_documents')."""

    @abstractmethod
    def get_description(self) -> str:
        """Return the tool description shown to the LLM in the system prompt."""

    @abstractmethod
    def get_step_hint(self) -> str:
        """Return the user-facing status message shown during tool execution."""

    @abstractmethod
    def get_required_args(self) -> list[str]:
        """Return the list of mandatory argument keys for this tool."""

    ##########################################
    ############### CORE #####################
    ##########################################

    @abstractmethod
    async def do_execute(
        self,
        args: dict,
        identity_helper: IdentityHelper,
    ) -> AgentToolResult:
        """Execute the tool and return a result.

        Must never raise — catch all exceptions and return AgentToolResult
        with observation='Error: <message>' on failure.

        Args:
            args: Validated argument dict from the LLM tool call.
            identity_helper: Resolved user identity for search isolation.

        Returns:
            AgentToolResult with observation text and optional citations.
        """

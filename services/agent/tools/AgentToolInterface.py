"""Abstract base class for agent tools."""
from abc import ABC, abstractmethod

from services.agent.tools.AgentToolResult import AgentToolResult
from services.rag_search.SearchService import SearchService
from services.rag_search.helper.IdentityHelper import IdentityHelper
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.helper.HelperConfig import HelperConfig
from services.rag_search.SearchService import SearchService


class AgentToolInterface(ABC):

    def __init__(self, helper_config: HelperConfig, search_service: SearchService, llm_client: LLMClientInterface) -> None:
        self._helper_config = helper_config
        self._search_service = search_service
        self._llm_client = llm_client
        self.logging = helper_config.get_logger()

    ##########################################
    ################ GETTER ##################
    ##########################################

    @abstractmethod
    def get_name(self) -> str:
        """Return the tool name used in the ReAct Action field."""
        pass

    @abstractmethod
    def get_description(self) -> str:
        """Return the tool description block shown to the LLM (without number prefix)."""
        pass

    @abstractmethod
    def get_step_hint(self) -> str:
        """Return the tool step hint shown to the User while the tool is working."""
        pass

    def get_chat_model_max_chars(self) -> int:
        """Helper to get the maximum input character length for the chat/completion model."""
        return self._llm_client.get_chat_model_max_chars()

    ##########################################
    ############### CORE #####################
    ##########################################

    @abstractmethod
    async def do_execute(self, **kwargs) -> AgentToolResult:
        """Execute the tool and return an AgentToolResult with the observation and citations.

        Args:
            **kwargs: Tool-specific arguments parsed from the Action Input.
            client_settings (dict|None): Optional settings from the client, e.g. to pass to tools.

        Returns:
            AgentToolResult: Observation text and any document citations produced by this tool call.
        """
        pass

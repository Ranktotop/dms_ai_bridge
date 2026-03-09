"""Registry and description builder for all registered agent tools."""
from services.rag_search.SearchService import SearchService
from services.agent.tools.AgentToolInterface import AgentToolInterface
from services.agent.tools.search_documents.AgentToolSearchDocuments import AgentToolSearchDocuments
from services.agent.tools.list_filter_options.AgentToolListFilterOptions import AgentToolListFilterOptions
from services.agent.tools.get_document_details.AgentToolGetDocumentDetails import AgentToolGetDocumentDetails
from services.agent.tools.get_document_full.AgentToolGetDocumentFull import AgentToolGetDocumentFull
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.helper.HelperConfig import HelperConfig
from services.rag_search.SearchService import SearchService


class AgentToolManager:

    def __init__(self, helper_config: HelperConfig, search_service: SearchService, llm_client: LLMClientInterface) -> None:
        self._tools: list[AgentToolInterface] = [
            AgentToolSearchDocuments(helper_config=helper_config, search_service=search_service, llm_client=llm_client),
            AgentToolListFilterOptions(helper_config=helper_config, search_service=search_service, llm_client=llm_client),
            AgentToolGetDocumentDetails(helper_config=helper_config, search_service=search_service, llm_client=llm_client),
            AgentToolGetDocumentFull(helper_config=helper_config, search_service=search_service, llm_client=llm_client),
        ]

    ##########################################
    ################ GETTER ##################
    ##########################################

    def get_tool_by_name(self, tool_name:str) -> AgentToolInterface | None:
        """Get a tool by name, or None if not found."""
        for tool in self._tools:
            if tool.get_name().lower() == tool_name.lower():
                return tool
        raise ValueError("Unknown tool called '%s'. Available tools: %s" % (tool_name, ", ".join([t.get_name() for t in self._tools])))

    def get_descriptions(self) -> str:
        """Return the full tool descriptions block for the system prompt."""
        lines = ["Available tools:"]
        for i, tool in enumerate(self._tools, 1):
            lines.append("")
            lines.append("%d. %s" % (i, tool.get_description()))
        return "\n".join(lines)

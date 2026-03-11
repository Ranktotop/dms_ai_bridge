"""Registry and dispatcher for all ReAct agent tools."""
from __future__ import annotations

from shared.helper.HelperConfig import HelperConfig
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from services.rag_search.SearchService import SearchService
from services.rag_search.helper.IdentityHelper import IdentityHelper
from services.agent.models.AgentToolCall import AgentToolCall
from services.agent.tools.AgentToolInterface import AgentToolInterface
from services.agent.tools.AgentToolResult import AgentToolResult
from services.agent.tools.search_documents.AgentToolSearchDocuments import AgentToolSearchDocuments
from services.agent.tools.list_filter_options.AgentToolListFilterOptions import AgentToolListFilterOptions
from services.agent.tools.get_document_details.AgentToolGetDocumentDetails import AgentToolGetDocumentDetails
from services.agent.tools.get_document_full.AgentToolGetDocumentFull import AgentToolGetDocumentFull


class AgentToolManager:
    """Registers all agent tools and dispatches validated tool calls.

    Adding a new tool requires only adding it to the tools list in __init__
    — no other changes needed.
    """

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def __init__(
        self,
        helper_config: HelperConfig,
        search_service: SearchService,
        llm_client: LLMClientInterface,
    ) -> None:
        self.logging = helper_config.get_logger()
        _tool_kwargs = {"helper_config": helper_config, "search_service": search_service, "llm_client": llm_client}
        self._tools: dict[str, AgentToolInterface] = {
            t.get_name(): t
            for t in [
                AgentToolSearchDocuments(**_tool_kwargs),
                AgentToolListFilterOptions(**_tool_kwargs),
                AgentToolGetDocumentDetails(**_tool_kwargs),
                AgentToolGetDocumentFull(**_tool_kwargs),
            ]
        }

    ##########################################
    ############## GETTER ####################
    ##########################################

    def get_tool_names(self) -> list[str]:
        """Return a list of all registered tool names."""
        return list(self._tools.keys())

    def get_descriptions(self) -> str:
        """Build a formatted tool description block for inclusion in the system prompt.

        Returns:
            Multi-line string listing each tool name and its description.
        """
        lines: list[str] = []
        for tool in self._tools.values():
            lines.append("- **%s**: %s" % (tool.get_name(), tool.get_description()))
        return "\n".join(lines)

    def get_step_hint(self, tool_name: str) -> str:
        """Return the user-facing hint for a tool, or a generic fallback.

        Args:
            tool_name: The tool name to look up.

        Returns:
            Step hint string.
        """
        tool = self._tools.get(tool_name)
        return tool.get_step_hint() if tool else "⚙️ Processing..."

    ##########################################
    ############### CORE #####################
    ##########################################

    async def validate_and_execute(
        self,
        tool_call: AgentToolCall,
        identity_helper: IdentityHelper,
        tool_context: dict | None = None,
    ) -> AgentToolResult:
        """
        Validate required args then delegate execution to the matching tool.

        Client-supplied tool_context is merged into the LLM-generated args before
        the call. Context values take precedence over LLM-generated values so that
        the caller can reliably override parameters like 'limit'.

        If anything goes wrong which is related to the llm. We don't raise errors. 
        Instead, we return them as observation text so that the agent can decide how to handle them

        Args:
            tool_call: Parsed tool call from the LLM.
            identity_helper: Resolved user identity for search isolation.
            tool_context: Optional client-supplied key/value overrides.

        Returns:
            AgentToolResult — never raises; errors surface as observation text.
        """
        # pick the tool from registered tools
        tool = self._tools.get(tool_call.action)
        # if the tool is unknown to the manager, return an error observation
        if tool is None:
            return AgentToolResult(
                observation="Error: unknown tool '%s'." % tool_call.action
            )

        # merge: LLM args first, then client context overwrites on conflict
        merged_args = {**tool_call.args, **(tool_context or {})}

        # make sure each required variable is passed
        missing = [k for k in tool.get_required_args() if k not in merged_args]
        if missing:
            return AgentToolResult(
                observation="Error: missing required argument(s) for tool '%s': %s."
                % (tool_call.action, ", ".join(missing))
            )

        # execute the tool
        return await tool.do_execute(args=merged_args, identity_helper=identity_helper)

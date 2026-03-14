"""Tool: list_filter_options — enumerate available filter categories."""
from __future__ import annotations

from shared.helper.HelperConfig import HelperConfig
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from services.rag_search.SearchService import SearchService
from services.rag_search.helper.IdentityHelper import IdentityHelper
from services.agent.tools.AgentToolInterface import AgentToolInterface
from services.agent.tools.AgentToolResult import AgentToolResult


class AgentToolListFilterOptions(AgentToolInterface):
    """Returns the list of available correspondents, document types, and tags."""

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def __init__(self, helper_config: HelperConfig, search_service: SearchService, llm_client: LLMClientInterface) -> None:
        super().__init__(helper_config=helper_config, search_service=search_service, llm_client=llm_client)

    ##########################################
    ############## GETTER ####################
    ##########################################

    def get_name(self) -> str:
        return "list_filter_options"

    def get_description(self) -> str:
        return (
            "List available filter categories: correspondents, document types, tags, and custom fields. "
            "Use this to discover what values exist before filtering a search. "
            "No required args."
        )

    def get_step_hint(self) -> str:
        return "🗂️ Loading filter options..."

    def get_required_args(self) -> list[str]:
        return []

    ##########################################
    ############### CORE #####################
    ##########################################

    async def do_execute(
        self,
        args: dict,
        identity_helper: IdentityHelper,
    ) -> AgentToolResult:
        """Fetch and format available filter options.

        Args:
            args: No required args.
            identity_helper: Resolved user identity.

        Returns:
            AgentToolResult listing correspondents, document types, and tags.
        """
        try:
            options = await self._search_service.do_get_filter_options(
                identity_helper=identity_helper
            )
        except Exception as e:
            self.logging.warning("AgentToolListFilterOptions: failed to fetch options: %s", e)
            return AgentToolResult(observation="Error: could not fetch filter options — %s" % str(e))

        correspondents = options.get("correspondents", [])
        document_types = options.get("document_types", [])
        tags = options.get("tags", [])
        custom_fields: dict[str, list[str]] = options.get("custom_fields", {})

        lines: list[str] = [
            "Available filter options:",
            "Correspondents: %s" % (", ".join(correspondents) if correspondents else "(none)"),
            "Document types: %s" % (", ".join(document_types) if document_types else "(none)"),
            "Tags: %s" % (", ".join(tags) if tags else "(none)"),
        ]

        # append one line per custom field so the agent knows which values are selectable
        for field_name, values in custom_fields.items():
            lines.append(
                "%s: %s" % (field_name, ", ".join(values) if values else "(none)")
            )

        return AgentToolResult(observation="\n".join(lines))

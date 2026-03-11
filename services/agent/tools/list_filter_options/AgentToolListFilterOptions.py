"""Tool: list available filter options from the cache."""
import json

from services.agent.tools.AgentToolInterface import AgentToolInterface
from services.agent.tools.AgentToolResult import AgentToolResult
from services.rag_search.helper.IdentityHelper import IdentityHelper
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.helper.HelperConfig import HelperConfig
from services.rag_search.SearchService import SearchService


class AgentToolListFilterOptions(AgentToolInterface):

    def __init__(self, helper_config: HelperConfig, search_service: SearchService, llm_client: LLMClientInterface) -> None:
        super().__init__(helper_config=helper_config, search_service=search_service, llm_client=llm_client)

    ##########################################
    ################ GETTER ##################
    ##########################################

    def get_name(self) -> str:
        return "list_filter_options"

    def get_description(self) -> str:
        return (
            "list_filter_options()\n"
            "   List available filter options (correspondents, document types, tags).\n"
            "   Parameters: none"
        )
    
    def get_step_hint(self) -> str:
        return "🗂️ Prüfe verfügbare Filter..."

    ##########################################
    ############### CORE #####################
    ##########################################

    async def do_execute(self, **kwargs) -> AgentToolResult:
        """
        Fetches all correspondents, document types and tags from the cache for the resolved identities and returns them as a JSON string.
        This is used to provide available filter options for the agent when searching for documents.

        Returns:
            AgentToolResult: JSON string containing the available filter options (no citations, as no documents are accessed).
        """
        try:
            # make sure all required vars are set
            identity_helper: IdentityHelper = kwargs["identity"]

            identities = identity_helper.get_identities()
            result: dict[str, list[str]] = {}

            # iterate the owner on each dms engine
            for identity in identities:
                #fetch the cache data for the dms engine and owner id (gets filled automatically if not already filled)
                cache_data = await self._search_service._cache_helper.get_data(
                    identity.dms_engine, identity.owner_id
                )
                # append the correspondents, if there are any
                if cache_data.correspondents:
                    if "correspondents" not in result:
                        result["correspondents"] = []
                    result["correspondents"].extend(
                        v for v in cache_data.correspondents if v not in result["correspondents"]
                    )
                # append the document types, if there are any
                if cache_data.document_types:
                    if "document_types" not in result:
                        result["document_types"] = []
                    result["document_types"].extend(
                        v for v in cache_data.document_types if v not in result["document_types"]
                    )
                # append the tags, if there are any
                if cache_data.tags:
                    if "tags" not in result:
                        result["tags"] = []
                    result["tags"].extend(
                        v for v in cache_data.tags if v not in result["tags"]
                    )
            return AgentToolResult(observation=json.dumps(result, ensure_ascii=False), citations=[])
        except Exception as e:
            self.logging.error("AgentToolListFilterOptions: Error while building filter options: %s", str(e), color="red")
            return AgentToolResult(observation="Error while building filter options.", citations=[])

"""Tool: fetch full metadata and content for a specific document."""
from services.agent.tools.AgentToolInterface import AgentToolInterface
from services.agent.tools.AgentToolResult import AgentToolResult, CitationRef
from services.rag_search.helper.IdentityHelper import IdentityHelper
from shared.clients.rag.models.Point import PointHighDetails
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.helper.HelperConfig import HelperConfig
from services.rag_search.SearchService import SearchService


class AgentToolGetDocumentDetails(AgentToolInterface):

    ##########################################
    ################ GETTER ##################
    ##########################################

    def __init__(self, helper_config: HelperConfig, search_service: SearchService, llm_client: LLMClientInterface) -> None:
        super().__init__(helper_config=helper_config, search_service=search_service, llm_client=llm_client)

    def get_name(self) -> str:
        return "get_document_details"

    def get_description(self) -> str:
        return (
            "get_document_details(document_id)\n"
            "   Get details for a specific document by its DMS document ID.\n"
            "   Parameters: document_id (string, required)"
        )
    
    def get_step_hint(self) -> str:
        return "📄 Lade Dokumentdetails..."

    ##########################################
    ############### CORE #####################
    ##########################################

    async def do_execute(self, **kwargs) -> AgentToolResult:
        """
        Retrieve a specific document from all rag engines, by documents_id

        Args:
            identity (IdentityHelper): Resolved user identities for filtering.
            document_id (str): The id of the document to search for

        Returns:
            AgentToolResult: Formatted document details and a CitationRef for the retrieved document.

        Raises:
            ValueError: If the document_id or identity is not given
            Exception: If an error occurs during the search process.
        """
        try:
            # make sure all required vars are set
            document_id: str = kwargs["document_id"]
            identity_helper: IdentityHelper = kwargs["identity"]
            # read optional parameters sent by client
            client_settings: dict = kwargs.get("client_settings", {})
            client_llm_max_chars: int = int(client_settings.get("llm_limit", self.get_chat_model_max_chars()))

            #define limit in chars based on client/server (the min value is used!)
            llm_limit = min(client_llm_max_chars, self.get_chat_model_max_chars())

            identities = identity_helper.get_identities()
            chunks: list[PointHighDetails] = []
            # iterate the owner on each dms engine
            for identity in identities:
                # fetch all chunks for the document from rag system, if it matches the owner id and dms_engine
                found = await self._search_service.do_fetch_by_doc_id(
                    doc_id=document_id,
                    dms_engine=identity.dms_engine,
                    owner_id=identity.owner_id,
                )
                chunks.extend(found)

            # if no documents found, the owner either has no access or the doc_id is not correct
            if not chunks:
                return AgentToolResult(
                    observation="Document with ID '%s' not found or owner has no access to it." % document_id,
                    citations=[],
                )

            # Since all points are leading to same document (=has same meta), we can pick the first point only
            first = chunks[0]
            observation = first.get_as_prompt_details(max_chars=llm_limit)
            citations: list[CitationRef] = []
            if first.dms_doc_id and first.dms_engine:
                citations.append(CitationRef(
                    dms_doc_id=first.dms_doc_id,
                    dms_engine=first.dms_engine,
                    title=first.title,
                ))
            return AgentToolResult(observation=observation, citations=citations)
        except Exception as e:
            self.logging.error("AgentToolGetDocumentDetails: Error while retrieving document infos: %s", str(e), color="red")
            return AgentToolResult(observation="Error while retrieving document infos.", citations=[])

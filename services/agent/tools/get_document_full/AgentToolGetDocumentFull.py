"""Tool: fetch full metadata and content for a specific document."""
from services.agent.tools.AgentToolInterface import AgentToolInterface
from services.agent.tools.AgentToolResult import AgentToolResult, CitationRef
from services.rag_search.helper.IdentityHelper import IdentityHelper
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.helper.HelperConfig import HelperConfig
from services.rag_search.SearchService import SearchService


class AgentToolGetDocumentFull(AgentToolInterface):

    ##########################################
    ################ GETTER ##################
    ##########################################

    def __init__(self, helper_config: HelperConfig, search_service: SearchService, llm_client: LLMClientInterface) -> None:
        super().__init__(helper_config=helper_config, search_service=search_service, llm_client=llm_client)

    def get_name(self) -> str:
        return "get_document_full"

    def get_description(self) -> str:
        return (
            "get_document_full(document_id, start_char)\n"
            "   Get the full text content of a specific document by its DMS document ID.\n"
            "   Use start_char to paginate through long documents: start at 0, then use\n"
            "   the next start_char value from the truncation note to read the next page.\n"
            "   Parameters:\n"
            "     document_id (string, required) — the DMS document ID\n"
            "     start_char  (int,    optional) — character offset to start reading from (default: 0)"
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
            AgentToolResult: Paginated full document content and one CitationRef per unique (dms_doc_id, dms_engine) accessed.

        Raises:
            ValueError: If the document_id or identity is not given
            Exception: If an error occurs during the search process.
        """
        try:
            # make sure all required vars are set
            document_id: str = kwargs["document_id"]
            start_char: int = kwargs.get("start_char", 0)
            identity_helper: IdentityHelper = kwargs["identity"]
            # read optional parameters sent by client
            client_settings: dict = kwargs.get("client_settings", {})
            client_llm_max_chars: int = int(client_settings.get("llm_limit", self.get_chat_model_max_chars()))

            #define limit in chars based on client/server (the min value is used!)
            llm_limit = min(client_llm_max_chars, self.get_chat_model_max_chars())

            identities = identity_helper.get_identities()
            contents: list[str] = []
            seen_citation_keys: set[tuple[str, str]] = set()
            citations: list[CitationRef] = []
            # iterate the owner on each dms engine
            for identity in identities:
                # fetch all chunks for the document from rag system, if it matches the owner id and dms_engine
                found = await self._search_service.do_fetch_full_by_doc_id(
                    doc_id=document_id,
                    dms_engine=identity.dms_engine,
                    owner_id=identity.owner_id,
                )
                #if there are results, add them to the content list and track citation
                if found:
                    contents.extend(found)
                    key = (document_id, identity.dms_engine)
                    if key not in seen_citation_keys:
                        seen_citation_keys.add(key)
                        citations.append(CitationRef(
                            dms_doc_id=document_id,
                            dms_engine=identity.dms_engine,
                            title=None,
                        ))

            # if no documents found, the owner either has no access or the doc_id is not correct
            if not contents:
                return AgentToolResult(
                    observation="Document with ID '%s' not found or owner has no access to it." % document_id,
                    citations=[],
                )

            # Build full content string — concatenate multiple engine results with markdown headers
            if len(contents) == 1:
                full_content = contents[0]
            else:
                full_content = ""
                for i, content in enumerate(contents):
                    full_content += "# Document %d\n%s\n\n" % (i + 1, content)

            # Apply pagination: slice [start_char : start_char + llm_limit]
            total_length = len(full_content)
            end_char = start_char + llm_limit
            page = full_content[start_char:end_char]

            # If there is more content after this page, append a truncation note
            if end_char < total_length:
                note = (
                    "\n# Note: Document content truncated. "
                    "Showing chars %d\u2013%d of %d. "
                    "Call again with start_char=%d to read more."
                ) % (start_char, end_char, total_length, end_char)
                page = full_content[start_char:end_char - len(note)] + note

            return AgentToolResult(observation=page, citations=citations)
        except Exception as e:
            self.logging.error("AgentToolGetDocumentFull: Error while retrieving document content: %s", str(e), color="red")
            return AgentToolResult(observation="Error while retrieving document content.", citations=[])

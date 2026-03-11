"""Tool: search documents by semantic similarity."""
from services.agent.tools.AgentToolInterface import AgentToolInterface
from services.agent.tools.AgentToolResult import AgentToolResult, CitationRef
from services.rag_search.helper.IdentityHelper import IdentityHelper
from shared.clients.rag.models.Point import PointHighDetails
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.helper.HelperConfig import HelperConfig
from services.rag_search.SearchService import SearchService


class AgentToolSearchDocuments(AgentToolInterface):

    def __init__(self, helper_config: HelperConfig, search_service: SearchService, llm_client: LLMClientInterface) -> None:
        super().__init__(helper_config=helper_config, search_service=search_service, llm_client=llm_client)

    ##########################################
    ################ GETTER ##################
    ##########################################

    def get_name(self) -> str:
        return "search_documents"

    def get_description(self) -> str:
        return (
            "search_documents(query, limit=5)\n"
            "   Search documents by semantic similarity. Returns matching documents with title and content preview.\n"
            "   Parameters: query (string, required)"
        )
    
    def get_step_hint(self) -> str:
        return "🔍 Suche nach Dokumenten..."

    ##########################################
    ############### CORE #####################
    ##########################################

    async def do_execute(self, **kwargs) -> AgentToolResult:
        """
        Retrieve documents matching the query from SearchService and return a formatted string with titles and content previews.

        Args:
            identity (IdentityHelper): Resolved user identities for filtering.
            query (str): Natural language query string to search for.

        Returns:
            AgentToolResult: Observation text with formatted document results and one CitationRef per unique document found.
        """
        try:
            # make sure all required vars are set
            identity_helper: IdentityHelper = kwargs["identity"]
            query: str = kwargs["query"]
            # read optional parameters sent by client
            client_settings: dict = kwargs.get("client_settings", {})
            limit: int = int(client_settings.get("dms_limit", 5))
            client_llm_max_chars: int = int(client_settings.get("llm_limit", self.get_chat_model_max_chars()))

            #define limit in chars based on client/server (the min value is used!)
            llm_limit = min(client_llm_max_chars, self.get_chat_model_max_chars())

            # Search matching documents from rag system
            results: list[PointHighDetails] = await self._search_service.do_search(
                query=query,
                identity_helper=identity_helper,
                limit=limit,
            )
            if not results:
                return AgentToolResult(
                    observation="No documents found matching the query or owner has no access to them.",
                    citations=[],
                )

            # Merge results by doc id
            merged = self._merge_chunks_by_document_id(results)
            # Calculate chars per result
            chars_per_result = llm_limit // len(merged) if merged else llm_limit
            observation = "\n\n".join(d.get_as_prompt_search_result(max_chars=chars_per_result) for d in merged)

            # Build one CitationRef per unique document (already deduplicated by merge)
            citations: list[CitationRef] = [
                CitationRef(
                    dms_doc_id=d.dms_doc_id or "",
                    dms_engine=d.dms_engine or "",
                    title=d.title,
                )
                for d in merged
                if d.dms_doc_id and d.dms_engine
            ]
            return AgentToolResult(observation=observation, citations=citations)
        except Exception as e:
            self.logging.error("AgentToolSearchDocuments: Error while searching for documents: %s", str(e), color="red")
            return AgentToolResult(observation="Error while searching for documents.", citations=[])


    ##########################################
    ############### HELPER ###################
    ##########################################

    def _merge_chunks_by_document_id(self, documents: list[PointHighDetails]) -> list[PointHighDetails]:
        """
        Merge chunks from the same document (same dms_doc_id) into one entry.
        Keep the highest score and concatenate chunk texts.

        Args:
            documents: List of PointHighDetails to merge.

        Returns:
            List of PointHighDetails with merged chunks.
        """
        #first we group by document id
        docs: dict[str, list[PointHighDetails]] = {}
        for d in documents:
            if d.dms_doc_id not in docs:
                docs[d.dms_doc_id] = []
            docs[d.dms_doc_id].append(d)
        
        #now we sort each set by score desc
        for doc_id, chunks in docs.items():
            chunks.sort(key=lambda x: x.score or 0.0, reverse=True)
        
        #now we create one new Document per set. We use index 0 as the base and extend the text with the rest
        merged: list[PointHighDetails] = []
        for doc_id, chunks in docs.items():
            base = chunks[0]
            if len(chunks) > 1:
                extra_text = "\n\n".join(c.chunk_text for c in chunks[1:] if c.chunk_text)
                base.chunk_text = (base.chunk_text or "") + "\n\n" + extra_text
            merged.append(base)
        return merged
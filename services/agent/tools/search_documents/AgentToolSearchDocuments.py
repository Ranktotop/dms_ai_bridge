"""Tool: search_documents — semantic search across the user's documents."""
from __future__ import annotations

from shared.helper.HelperConfig import HelperConfig
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from services.rag_search.SearchService import SearchService
from services.rag_search.helper.IdentityHelper import IdentityHelper
from services.agent.tools.AgentToolInterface import AgentToolInterface
from services.agent.tools.AgentToolResult import AgentToolResult


class AgentToolSearchDocuments(AgentToolInterface):
    """Performs a semantic similarity search and returns ranked document snippets."""

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def __init__(self, helper_config: HelperConfig, search_service: SearchService, llm_client: LLMClientInterface) -> None:
        super().__init__(helper_config=helper_config, search_service=search_service, llm_client=llm_client)

    ##########################################
    ############## GETTER ####################
    ##########################################

    def get_name(self) -> str:
        return "search_documents"

    def get_description(self) -> str:
        return (
            "Search for relevant documents using a natural language query. "
            "Returns a ranked list of document snippets with metadata. "
            "Required args: query (str). Optional args: limit (int, default 5)."
        )

    def get_step_hint(self) -> str:
        return "🔍 Searching documents..."

    def get_required_args(self) -> list[str]:
        return ["query"]

    ##########################################
    ############### CORE #####################
    ##########################################

    async def do_execute(
        self,
        args: dict,
        identity_helper: IdentityHelper,
    ) -> AgentToolResult:
        """Run semantic search and format results as an observation string.

        Args:
            args: Must contain 'query' (str). Optional 'limit' (int).
            identity_helper: Resolved user identity.

        Returns:
            AgentToolResult with formatted snippets and citation refs.
        """
        query: str = args.get("query", "")
        limit: int = int(args.get("limit", 5))
        # use half the chat model's context window so the observation stays well within the token budget;
        # get_chat_model_max_chars() was resolved at healthcheck time (API) or falls back to the env value
        max_chars = self._llm_client.get_chat_model_max_chars()
        max_chars = max_chars // 2 if max_chars > 0 else 2000

        try:
            points = await self._search_service.do_search(
                query=query,
                identity_helper=identity_helper,
                limit=limit,
                merge_results=True,
            )
        except Exception as e:
            self.logging.warning("AgentToolSearchDocuments: search failed: %s", e)
            return AgentToolResult(observation="Error: search failed — %s" % str(e))

        if not points:
            return AgentToolResult(observation="No documents found for query: '%s'." % query)

        lines: list[str] = ["Found %d document(s):" % len(points)]
        # give each point equal chars
        chars_per_point = max_chars // len(points)
        # track doc_ids already added to avoid duplicate lines for the same doc
        seen_doc_ids: set[str] = set()

        # iterate the points
        for i, point in enumerate(points, start=1):
            doc_id = point.dms_doc_id or ""
            title = point.title or "(no title)"
            score = point.score or 0.0
            # create the observation line for this point
            base_line = "[%d] doc_id=%s | dms_engine=%s | title=%s | score=%.3f" % (i, doc_id, point.dms_engine or "", title, score)
            # strip the content to fit within the char limit
            chars_left = max(chars_per_point - len(base_line) - 1, 1)  # -1 for newline, min 1 char
            snippet = (point.chunk_text or "")[:chars_left]
            lines.append(base_line + "\n" + snippet)
            seen_doc_ids.add(doc_id)

        # search is an exploration step — citations are only added when the agent explicitly
        # fetches a document via get_document_details or get_document_full. Returning all
        # search hits as citations would flood the UI with unrelated document links.
        return AgentToolResult(observation="\n".join(lines), citations=[])

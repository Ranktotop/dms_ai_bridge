"""Tool: get_document_full — paginated full-text retrieval for a document."""
from __future__ import annotations

from shared.helper.HelperConfig import HelperConfig
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from services.rag_search.SearchService import SearchService
from services.rag_search.helper.IdentityHelper import IdentityHelper
from services.agent.tools.AgentToolInterface import AgentToolInterface
from services.agent.tools.AgentToolResult import AgentToolResult
from services.agent.models.AgentEvent import CitationRef

class AgentToolGetDocumentFull(AgentToolInterface):
    """Retrieves a paginated window of a document's full text content."""

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def __init__(self, helper_config: HelperConfig, search_service: SearchService, llm_client: LLMClientInterface) -> None:
        super().__init__(helper_config=helper_config, search_service=search_service, llm_client=llm_client)

    ##########################################
    ############## GETTER ####################
    ##########################################

    def get_name(self) -> str:
        return "get_document_full"

    def get_description(self) -> str:
        return (
            "Retrieve a paginated page of a document's full text. "
            "Use start_char=0 for the first page; use next_start_char from a previous result to continue. "
            "Required args: document_id (str), dms_engine (str). "
            "Optional args: start_char (int, default 0)."
        )

    def get_step_hint(self) -> str:
        return "📖 Loading full document text..."

    def get_required_args(self) -> list[str]:
        return ["document_id", "dms_engine"]

    ##########################################
    ############### CORE #####################
    ##########################################

    async def do_execute(
        self,
        args: dict,
        identity_helper: IdentityHelper,
    ) -> AgentToolResult:
        """Fetch a paginated slice of a document's full content.

        Args:
            args: Must contain 'document_id' (str) and 'dms_engine' (str).
                  Optional 'start_char' (int).
            identity_helper: Resolved user identity.

        Returns:
            AgentToolResult with page content and navigation info.
        """
        document_id: str = args.get("document_id", "")
        dms_engine: str = args.get("dms_engine", "")
        start_char: int = int(args.get("start_char", 0))
        # use half the chat model's context window so the observation stays well within the token budget;
        # get_chat_model_max_chars() was resolved at healthcheck time (API) or falls back to the env value
        page_size = self._llm_client.get_chat_model_max_chars()
        page_size = page_size //2 if page_size >0 else 2000

        owner_id: str | None = None
        for identity in identity_helper.get_identities():
            if identity.dms_engine.lower() == dms_engine.lower():
                owner_id = identity.owner_id
                break

        if owner_id is None:
            return AgentToolResult(
                observation="Error: no access mapping found for engine '%s'." % dms_engine
            )

        try:
            docs = await self._search_service.do_fetch_full_by_doc_id(
                doc_id=document_id,
                dms_engine=dms_engine,
                owner_id=owner_id,
            )
        except Exception as e:
            self.logging.warning("AgentToolGetDocumentFull: fetch failed: %s", e)
            return AgentToolResult(observation="Error: could not fetch document — %s" % str(e))

        if not docs:
            return AgentToolResult(
                observation="Document '%s' not found or access denied." % document_id
            )

        if len(docs) == 1:
            full_content = docs[0].content or ""
        else:
            parts: list[str] = []
            for idx, doc in enumerate(docs, start=1):
                parts.append("--- Document %d ---\n\n%s" % (idx, doc.content or ""))
            full_content = "\n\n".join(parts)

        total_length = len(full_content)
        end_char = start_char + page_size
        page = full_content[start_char:end_char]
        next_start_char: int | None = end_char if end_char < total_length else None

        title = docs[0].title or "(no title)"
        nav = (
            "next_start_char=%d" % next_start_char
            if next_start_char is not None
            else "end of document"
        )
        observation = (
            "Document: %s (id=%s) | total_length=%d | start=%d | %s\n\n%s"
            % (title, document_id, total_length, start_char, nav, page)
        )

        view_url = self._search_service.get_document_url_by_id(
            dms_engine=dms_engine, doc_id=document_id
        )
        citation = CitationRef(
            dms_doc_id=document_id,
            dms_engine=dms_engine,
            title=title,
            view_url=view_url,
        )
        return AgentToolResult(observation=observation, citations=[citation])

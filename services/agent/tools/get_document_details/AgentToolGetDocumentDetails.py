"""Tool: get_document_details — fetch full metadata and content for a document."""
from __future__ import annotations

from shared.helper.HelperConfig import HelperConfig
from shared.clients.llm.LLMClientInterface import LLMClientInterface
from services.rag_search.SearchService import SearchService
from services.rag_search.helper.IdentityHelper import IdentityHelper
from services.agent.tools.AgentToolInterface import AgentToolInterface
from services.agent.tools.AgentToolResult import AgentToolResult
from services.agent.models.AgentEvent import CitationRef


class AgentToolGetDocumentDetails(AgentToolInterface):
    """Fetches full document content and metadata for a given document ID."""

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    def __init__(self, helper_config: HelperConfig, search_service: SearchService, llm_client: LLMClientInterface) -> None:
        super().__init__(helper_config=helper_config, search_service=search_service, llm_client=llm_client)

    ##########################################
    ############## GETTER ####################
    ##########################################

    def get_name(self) -> str:
        return "get_document_details"

    def get_description(self) -> str:
        return (
            "Fetch the full content and metadata of a specific document by its ID. "
            "Use this after search_documents to retrieve the full text. "
            "Required args: document_id (str), dms_engine (str)."
        )

    def get_step_hint(self) -> str:
        return "📄 Loading document details..."

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
        """Fetch full content for a specific document ID.

        Args:
            args: Must contain 'document_id' (str) and 'dms_engine' (str).
            identity_helper: Resolved user identity.

        Returns:
            AgentToolResult with full document content and citation ref.
        """
        document_id: str = args.get("document_id", "")
        dms_engine: str = args.get("dms_engine", "")
        # use half the chat model's context window so the observation stays well within the token budget;
        # get_chat_model_max_chars() was resolved at healthcheck time (API) or falls back to the env value
        max_chars = self._llm_client.get_chat_model_max_chars()
        max_chars = max_chars //2 if max_chars >0 else 2000

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
            self.logging.warning("AgentToolGetDocumentDetails: fetch failed: %s", e)
            return AgentToolResult(observation="Error: could not fetch document — %s" % str(e))

        if not docs:
            return AgentToolResult(
                observation="Document '%s' not found or access denied." % document_id
            )

        doc = docs[0]
        title = doc.title or "(no title)"
        content = doc.content or ""

        view_url = self._search_service.get_document_url_by_id(
            dms_engine=dms_engine, doc_id=document_id
        )
        citation = CitationRef(
            dms_doc_id=document_id,
            dms_engine=dms_engine,
            title=title,
            view_url=view_url,
        )

        observation = (
            "Document: %s (id=%s)\nContent:\n%s" % (title, document_id, content[:max_chars])
        )
        return AgentToolResult(observation=observation, citations=[citation])

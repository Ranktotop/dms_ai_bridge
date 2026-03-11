"""
DMS AI Bridge — OpenWebUI Tool

Deploy this file in OpenWebUI under Admin → Tools.
Attach the tool set to any model. The model decides which tool to call and when.

Self-contained: no imports from dms_ai_bridge.

Tools exposed:
  search_documents       — semantic search, returns merged results
  list_filter_options    — correspondents, document types, tags
  get_document_details   — metadata + content preview for a specific document
  get_document_full      — paginated full text of a specific document
"""
import json
import httpx
from pydantic import BaseModel


class Tools:
    class Valves(BaseModel):
        BASE_URL: str = "http://dms-bridge:8000"
        API_KEY: str = ""
        LIMIT: int = 5
        TIMEOUT: float = 60.0

    def __init__(self) -> None:
        self.valves = self.Valves()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        return {"Content-Type": "application/json", "X-Api-Key": self.valves.API_KEY}

    def _base(self) -> str:
        return self.valves.BASE_URL.rstrip("/")

    async def _post(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=self.valves.TIMEOUT) as client:
            response = await client.post(
                "%s%s" % (self._base(), path),
                json=body,
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def _emit(self, emitter, description: str, done: bool) -> None:
        if emitter is not None:
            await emitter({"type": "status", "data": {"description": description, "done": done}})

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    async def search_documents(
        self,
        query: str,
        __user__: dict = {},
        __event_emitter__=None,
    ) -> str:
        """Search the personal document archive by semantic similarity.

        Use this tool whenever the user asks about documents, invoices, contracts,
        letters, receipts, or any files stored in the document management system.

        Args:
            query: Natural language search query.

        Returns:
            str: Matching documents with title, metadata, and content preview.
        """
        await self._emit(__event_emitter__, "🔍 Suche nach Dokumenten…", done=False)
        try:
            data = await self._post(
                "/tools/openwebui/search_documents",
                {"user_id": __user__.get("id", ""), "query": query, "limit": self.valves.LIMIT},
            )
            results = data.get("results", [])
            if not results:
                await self._emit(__event_emitter__, "Keine Treffer.", done=True)
                return "No documents found for: %s" % query

            lines = []
            for r in results:
                lines.append("## %s (ID: %s, Score: %.3f)" % (r.get("title", "?"), r.get("dms_doc_id", "?"), r.get("score", 0)))
                if r.get("created"):
                    lines.append("Date: %s" % r["created"])
                if r.get("correspondent"):
                    lines.append("Correspondent: %s" % r["correspondent"])
                if r.get("document_type"):
                    lines.append("Type: %s" % r["document_type"])
                if r.get("tags"):
                    lines.append("Tags: %s" % ", ".join(r["tags"]))
                if r.get("content"):
                    lines.append("\n%s" % r["content"][:2000])
                lines.append("")

            await self._emit(__event_emitter__, "Fertig (%d Treffer)" % len(results), done=True)
            return "\n".join(lines)
        except Exception as e:
            await self._emit(__event_emitter__, "Fehler: %s" % str(e), done=True)
            return "Error searching documents: %s" % str(e)

    async def list_filter_options(
        self,
        __user__: dict = {},
        __event_emitter__=None,
    ) -> str:
        """List available filter options: correspondents, document types, and tags.

        Call this before searching when the user mentions a name or term that could
        match multiple correspondents or document types. Use the results to clarify.

        Returns:
            str: JSON-formatted filter options.
        """
        await self._emit(__event_emitter__, "🗂️ Lade Filter-Optionen…", done=False)
        try:
            data = await self._post(
                "/tools/openwebui/list_filter_options",
                {"user_id": __user__.get("id", "")},
            )
            await self._emit(__event_emitter__, "Filter geladen.", done=True)
            return json.dumps(data, ensure_ascii=False, indent=2)
        except Exception as e:
            await self._emit(__event_emitter__, "Fehler: %s" % str(e), done=True)
            return "Error fetching filter options: %s" % str(e)

    async def get_document_details(
        self,
        document_id: str,
        __user__: dict = {},
        __event_emitter__=None,
    ) -> str:
        """Get metadata and content preview for a specific document by its ID.

        Use this after search_documents to retrieve full details for a document
        the user wants to know more about.

        Args:
            document_id: The DMS document ID (from search results).

        Returns:
            str: Document metadata and content.
        """
        await self._emit(__event_emitter__, "📄 Lade Dokumentdetails…", done=False)
        try:
            data = await self._post(
                "/tools/openwebui/get_document_details",
                {"user_id": __user__.get("id", ""), "document_id": document_id},
            )
            lines = [
                "## %s (ID: %s)" % (data.get("title", "?"), data.get("dms_doc_id", "?")),
            ]
            if data.get("created"):
                lines.append("Date: %s" % data["created"])
            if data.get("correspondent"):
                lines.append("Correspondent: %s" % data["correspondent"])
            if data.get("document_type"):
                lines.append("Type: %s" % data["document_type"])
            if data.get("tags"):
                lines.append("Tags: %s" % ", ".join(data["tags"]))
            if data.get("content"):
                lines.append("\n%s" % data["content"])
            await self._emit(__event_emitter__, "Fertig.", done=True)
            return "\n".join(lines)
        except Exception as e:
            await self._emit(__event_emitter__, "Fehler: %s" % str(e), done=True)
            return "Error fetching document details: %s" % str(e)

    async def get_document_full(
        self,
        document_id: str,
        start_char: int = 0,
        __user__: dict = {},
        __event_emitter__=None,
    ) -> str:
        """Get the full text content of a document with pagination.

        Use this to read the complete text of a document. For long documents,
        call again with the returned next_start_char to read the next page.

        Args:
            document_id: The DMS document ID.
            start_char: Character offset to start from (default: 0).

        Returns:
            str: Document text page with pagination info if truncated.
        """
        await self._emit(__event_emitter__, "📄 Lade vollständigen Text…", done=False)
        try:
            data = await self._post(
                "/tools/openwebui/get_document_full",
                {"user_id": __user__.get("id", ""), "document_id": document_id, "start_char": start_char},
            )
            content = data.get("content", "")
            total = data.get("total_length", 0)
            next_char = data.get("next_start_char")

            result = content
            if next_char is not None:
                result += "\n\n[Document truncated. %d of %d chars shown. Call again with start_char=%d for more.]" % (
                    start_char + len(content), total, next_char
                )
            await self._emit(__event_emitter__, "Fertig.", done=True)
            return result
        except Exception as e:
            await self._emit(__event_emitter__, "Fehler: %s" % str(e), done=True)
            return "Error fetching document: %s" % str(e)

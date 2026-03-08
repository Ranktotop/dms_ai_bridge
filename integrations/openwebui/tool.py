"""
DMS AI Bridge — OpenWebUI Tool Function

Deploy this file in OpenWebUI under Admin → Functions as a Tool.
Attach the tool to any OpenWebUI model. The model decides when to call
search_documents(); the Bridge handles ReAct reasoning and returns the answer.

Self-contained: no imports from dms_ai_bridge.

Calls POST /chat/openwebui/stream (SSE) with ReAct agent.
Falls back to POST /query/openwebui (semantic search) if the stream fails.
"""
import json
import httpx
from pydantic import BaseModel


class Tools:
    class Valves(BaseModel):
        BASE_URL: str = "http://dms-bridge:8000"
        API_KEY: str = ""
        USER_ID: str = "5"
        LIMIT: int = 5
        TIMEOUT: float = 120.0

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def search_documents(
        self,
        query: str,
        __event_emitter__=None,
    ) -> str:
        """Search the personal document archive (invoices, contracts, letters, …).

        Use this tool whenever the user asks about documents, invoices, contracts,
        letters, receipts, or any files stored in the document management system.

        Args:
            query: Natural language question about the user's documents.

        Returns:
            str: Answer synthesised from relevant documents, or an error message.
        """

        async def emit(description: str, done: bool) -> None:
            if __event_emitter__ is not None:
                await __event_emitter__(
                    {"type": "status", "data": {"description": description, "done": done}}
                )

        if not query or not query.strip():
            await emit("Fehler: Leere Suchanfrage.", done=True)
            return "Error: Query cannot be empty."

        base_url = self.valves.BASE_URL.rstrip("/")
        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": self.valves.API_KEY,
        }
        payload = {
            "query": query,
            "user_id": self.valves.USER_ID,
            "limit": self.valves.LIMIT,
        }

        await emit("Suche in Dokumenten…", done=False)

        # Attempt Phase IV endpoint: /chat/openwebui/stream (SSE with ReAct agent)
        try:
            stream_url = "%s/chat/openwebui/stream" % base_url
            answer = ""

            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT) as client:
                async with client.stream(
                    "POST", stream_url, json=payload, headers=headers
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:]  # strip "data: " prefix
                        if raw == "[DONE]":
                            break
                        try:
                            event = json.loads(raw)
                        except (json.JSONDecodeError, ValueError):
                            continue
                        event_type = event.get("type")
                        chunk = event.get("chunk", "")
                        if event_type == "step":
                            if chunk:
                                await emit(chunk, done=False)
                        elif event_type == "answer":
                            answer += chunk
                        elif event_type is None and chunk.startswith("Error:"):
                            # error event from the bridge — propagate immediately
                            await emit(chunk, done=True)
                            return chunk

            if answer.strip():
                await emit("Fertig", done=True)
                return answer.strip()

        except Exception as stream_error:  # noqa: BLE001
            # stream failed — fall through to query fallback
            await emit(
                "Stream nicht verfügbar, nutze semantische Suche… (%s)" % str(stream_error),
                done=False,
            )

        # Fallback: Phase III endpoint: /query/openwebui (semantic search, no ReAct)
        try:
            query_url = "%s/query/openwebui" % base_url
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(query_url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()

            results = data.get("results", [])
            if not results:
                await emit("Keine Treffer gefunden.", done=True)
                return "No documents found matching: %s" % query

            lines = ["Found %d document(s):" % len(results)]
            for i, r in enumerate(results, start=1):
                title = r.get("title") or r.get("dms_doc_id") or "Unknown"
                score = r.get("score")
                score_str = "%.3f" % score if score is not None else "—"
                lines.append("%d. %s (score: %s)" % (i, title, score_str))
                chunk_text = r.get("chunk_text", "")
                if chunk_text:
                    lines.append("   %s…" % chunk_text[:200].replace("\n", " "))

            await emit("Fertig (%d Treffer)" % len(results), done=True)
            return "\n".join(lines)

        except Exception as query_error:  # noqa: BLE001
            error_msg = "Document search error: %s" % str(query_error)
            await emit(error_msg, done=True)
            return error_msg

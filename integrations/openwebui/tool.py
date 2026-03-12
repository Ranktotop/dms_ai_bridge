"""
DMS AI Bridge — OpenWebUI Tool

Deploy this file in OpenWebUI under Admin → Tools.
Attach the tool to any model.

The bridge runs the full ReAct reasoning loop internally; this tool only
streams the results back to OpenWebUI via the event emitter and returns
the final answer text. Citations are surfaced as native OpenWebUI source chips.

The model decides when to call this tool — normal chat messages that are
unrelated to documents are handled directly by the model without invoking
the bridge.

Self-contained: no imports from dms_ai_bridge.
"""
import json
import httpx
from pydantic import BaseModel


class Tools:
    class Valves(BaseModel):
        BASE_URL: str = "http://dms-bridge:8000"
        API_KEY: str = ""
        LIMIT: int = 10
        TIMEOUT: float = 120.0

    def __init__(self) -> None:
        self.valves = self.Valves()

    ##########################################
    ############# HELPERS ####################
    ##########################################

    def _headers(self) -> dict:
        return {"Content-Type": "application/json", "X-Api-Key": self.valves.API_KEY}

    def _base(self) -> str:
        return self.valves.BASE_URL.rstrip("/")

    async def _emit_status(self, emitter, description: str, done: bool) -> None:
        """Forward a progress update to the OpenWebUI status indicator."""
        if emitter is not None:
            await emitter({"type": "status", "data": {"description": description, "done": done}})

    async def _emit_citation(self, emitter, title: str, url: str | None) -> None:
        """Emit a native OpenWebUI citation chip for a source document."""
        if emitter is None:
            return
        await emitter({
            "type": "citation",
            "data": {
                # document and metadata are parallel lists — one entry per source
                "document": [title],
                "metadata": [{"source": url or ""}],
                "source": {"name": title},
            },
        })

    ##########################################
    ################ TOOL ####################
    ##########################################

    async def search_documents(
        self,
        query: str,
        __user__: dict = {},
        __event_emitter__=None,
        __messages__: list = [],
    ) -> str:
        """Search and answer questions about documents stored in the personal document archive.

        Use this tool whenever the user asks about documents, invoices, contracts,
        letters, receipts, or any files stored in their document management system.
        Also use this for questions about specific content, dates, amounts, or names
        found in documents.

        Args:
            query: The user's natural language question about their documents.

        Returns:
            str: The agent's answer based on the relevant documents found.
        """
        await self._emit_status(__event_emitter__, "🚀 Starte DMS AI Agent…", done=False)

        # use email as user identifier — easier to configure in user_mapping.yml than an opaque UUID
        user_id = str(__user__.get("email", ""))

        # convert OpenWebUI's __messages__ to the OpenAI role/content format the bridge expects;
        # exclude the current turn (last user message) — it is sent as 'query'
        chat_history = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in (__messages__ or [])[:-1]
        ]

        url = "%s/chat/openwebui/stream" % self._base()
        body = {
            "user_id": user_id,
            "query": query.strip(),
            "tool_context": {"limit": self.valves.LIMIT},
            "chat_history": chat_history,
        }

        try:
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT) as client:
                async with client.stream("POST", url, json=body, headers=self._headers()) as response:
                    response.raise_for_status()
                    return await self._consume_stream(response, __event_emitter__)
        except httpx.HTTPStatusError as e:
            await self._emit_status(__event_emitter__, "Fehler: HTTP %d" % e.response.status_code, done=True)
            return "Error: bridge returned HTTP %d." % e.response.status_code
        except Exception as e:
            await self._emit_status(__event_emitter__, "Fehler: %s" % str(e), done=True)
            return "Error communicating with DMS bridge: %s" % str(e)

    ##########################################
    ############# STREAM #####################
    ##########################################

    async def _consume_stream(self, response, emitter) -> str:
        """Read the SSE stream from the bridge and dispatch each typed event.

        Thought/step/retry events are forwarded as OpenWebUI status updates.
        The answer event emits native citation chips and returns the final text.

        Args:
            response: Active httpx streaming response from the bridge.
            emitter:  OpenWebUI's __event_emitter__ callable, or None.

        Returns:
            The final answer string, or an empty string if no answer event arrived.
        """
        answer = ""
        buffer = ""

        async for chunk in response.aiter_text():
            buffer += chunk

            # process every complete SSE line that has accumulated in the buffer
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line.startswith("data:"):
                    continue

                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    return answer

                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    # skip malformed SSE lines rather than crashing
                    continue

                event_type = event.get("type", "")

                if event_type == "thought":
                    await self._emit_status(emitter, "💭 %s" % event.get("thought", ""), done=False)

                elif event_type == "step":
                    hint = event.get("hint") or ("⚙️ %s…" % event.get("tool_name", "tool"))
                    await self._emit_status(emitter, hint, done=False)

                elif event_type == "retry":
                    await self._emit_status(emitter, "🔄 Wiederhole Antwort-Parsing…", done=False)

                elif event_type == "answer":
                    answer = event.get("text", "")
                    # emit each source as a native OpenWebUI citation chip
                    for citation in event.get("citations", []):
                        await self._emit_citation(
                            emitter,
                            title=citation.get("title") or citation.get("dms_doc_id", ""),
                            url=citation.get("view_url"),
                        )
                    await self._emit_status(emitter, "✅ Suche abgeschlossen.", done=True)

                elif event_type == "error":
                    await self._emit_status(emitter, "❌ Fehler aufgetreten.", done=True)
                    return "❌ %s" % event.get("message", "Agent returned an error.")

        return answer

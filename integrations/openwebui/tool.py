"""
title: DMS AI Bridge
author: Marc Meese
version: 1.0.0

DMS AI Bridge — OpenWebUI Pipe Function

Deploy this file in OpenWebUI under Admin → Functions.
The bridge runs the full ReAct reasoning loop internally; this pipe only
streams the results back to OpenWebUI and returns the final answer verbatim.
Citations are surfaced as native OpenWebUI source chips.

Self-contained: no imports from dms_ai_bridge.
"""
import json
import httpx
from pydantic import BaseModel, Field


class Pipe:
    class Valves(BaseModel):
        BASE_URL: str = Field(default="http://dms-bridge:8000", description="Base URL of the DMS AI Bridge server.")
        API_KEY: str = Field(default="", description="Authentication key (X-Api-Key header).")
        LIMIT: int = Field(default=10, description="Maximum number of documents to retrieve per search.")
        TIMEOUT: float = Field(default=120.0, description="Request timeout in seconds.")

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
    ############### CORE #####################
    ##########################################

    async def pipe(
        self,
        body: dict,
        __user__: dict = {},
        __event_emitter__=None,
    ) -> str:
        """Main entry point called by OpenWebUI on every user message.

        Delegates the full ReAct reasoning loop to the DMS AI Bridge.
        The return value is displayed verbatim — no second LLM pass occurs
        inside OpenWebUI, so citations appended here are always visible.

        Args:
            body:             OpenWebUI request body (contains 'messages').
            __user__:         Injected user dict from OpenWebUI.
            __event_emitter__: Injected event emitter for status/citation events.

        Returns:
            The agent's final answer, or an error string on failure.
        """
        messages: list[dict] = body.get("messages", [])
        if not messages:
            return "Error: no messages provided."

        # the last user message is the current query — everything before is history
        query = messages[-1].get("content", "").strip()
        if not query:
            return "Error: empty query."

        # use email as user identifier — easier to configure in user_mapping.yml than an opaque UUID
        user_id = str(__user__.get("email", ""))

        # strip the current query from history so the bridge doesn't see it twice
        chat_history = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages[:-1]
        ]

        await self._emit_status(__event_emitter__, "🚀 Starte DMS AI Agent…", done=False)

        url = "%s/chat/openwebui/stream" % self._base()
        body_payload = {
            "user_id": user_id,
            "query": query,
            "tool_context": {"limit": self.valves.LIMIT},
            "chat_history": chat_history,
        }

        try:
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT) as client:
                async with client.stream("POST", url, json=body_payload, headers=self._headers()) as response:
                    response.raise_for_status()
                    return await self._consume_stream(response, __event_emitter__)
        except httpx.HTTPStatusError as e:
            await self._emit_status(__event_emitter__, "Fehler: HTTP %d" % e.response.status_code, done=True)
            return "Error: bridge returned HTTP %d." % e.response.status_code
        except Exception as e:
            await self._emit_status(__event_emitter__, "Fehler: %s" % str(e), done=True)
            return "Error communicating with DMS bridge: %s" % str(e)

    async def _consume_stream(self, response: httpx.Response, emitter) -> str:
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
                    raise RuntimeError(event.get("message", "Agent returned an error."))

        return answer

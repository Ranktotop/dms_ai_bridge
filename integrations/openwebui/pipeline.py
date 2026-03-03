"""
DMS AI Bridge — OpenWebUI Pipeline

Deploy this file to the OpenWebUI pipelines/ directory.
Self-contained: no imports from dms_ai_bridge.

Calls POST /chat/openwebui/stream (SSE) or POST /chat/openwebui (JSON).
"""
from typing import AsyncGenerator
import json
import httpx
from pydantic import BaseModel


class Pipeline:
    class Valves(BaseModel):
        BASE_URL: str = "http://dms-bridge:8000"
        API_KEY: str = ""
        USER_ID: str = "5"
        LIMIT: int = 5
        STREAM: bool = True
        TIMEOUT: float = 60.0

    def __init__(self):
        self.name = "DMS AI Bridge"
        self.valves = self.Valves()

    async def pipe(
        self,
        user_message: str,
        model_id: str,  # noqa: ARG002 — required by OpenWebUI Pipelines interface
        messages: list[dict],
        body: dict,  # noqa: ARG002 — required by OpenWebUI Pipelines interface
    ) -> AsyncGenerator[str, None]:
        """Main pipeline handler.

        Converts OpenWebUI message history to chat_history format,
        calls the dms_ai_bridge /chat endpoint, and yields response chunks.

        Yields:
            str: Text chunks of the assistant's answer.
        """
        # Build chat_history from prior messages (exclude the last user message)
        chat_history = []
        for msg in messages[:-1]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                chat_history.append({"role": role, "content": content})

        payload = {
            "query": user_message,
            "user_id": self.valves.USER_ID,
            "limit": self.valves.LIMIT,
            "chat_history": chat_history if chat_history else None,
        }
        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": self.valves.API_KEY,
        }

        if self.valves.STREAM:
            url = "%s/chat/openwebui/stream" % self.valves.BASE_URL.rstrip("/")
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]  # strip "data: " prefix
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data).get("chunk", "")
                            if chunk:
                                yield chunk
                        except (json.JSONDecodeError, AttributeError):
                            continue
        else:
            url = "%s/chat/openwebui" % self.valves.BASE_URL.rstrip("/")
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                answer = response.json().get("answer", "")
                yield answer

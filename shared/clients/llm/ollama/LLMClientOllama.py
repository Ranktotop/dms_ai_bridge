import json

from shared.clients.llm.LLMClientInterface import LLMClientInterface
from shared.helper.HelperConfig import HelperConfig
from shared.models.config import EnvConfig


class LLMClientOllama(LLMClientInterface):
    def __init__(self, helper_config: HelperConfig):
        super().__init__(helper_config=helper_config)
        self._base_url = self.get_config_val("BASE_URL", default=None, val_type="string")
        self._api_key = self.get_config_val("API_KEY", default="", val_type="string")

    ##########################################
    ################ GETTER ##################
    ##########################################

    ################ GENERAL ##################
    def _get_engine_name(self) -> str:
        return "Ollama"

    ################ CONFIG ##################
    def _get_required_config(self) -> list[EnvConfig]:
        return [
            EnvConfig(env_key="BASE_URL", val_type="string", default=None),
            EnvConfig(env_key="API_KEY", val_type="string", default=""),
        ]

    ################ AUTH ##################
    def _get_auth_header(self) -> dict:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    ################ ENDPOINTS ##################
    def _get_base_url(self) -> str:
        return self._base_url

    def _get_endpoint_healthcheck(self) -> str:
        return ""

    def _get_endpoint_models(self) -> str:
        return "/api/tags"

    def get_endpoint_embedding(self) -> str:
        return "/api/embed"

    def get_endpoint_model_details(self) -> str:
        return "/api/show"

    def _get_endpoint_chat(self) -> str:
        return "/api/chat"

    ################ PAYLOADS ##################
    def get_embed_payload(self, texts: list[str]) -> dict:
        return {"model": self.embed_model, "input": texts}

    def get_chat_payload(self, messages: list[dict]) -> dict:
        model = self.chat_model or self.embed_model
        return {"model": model, "messages": messages, "stream": False}
    
    def get_model_details_payload(self) -> dict:
        return {"name": self.embed_model}

    def get_chat_model_details_payload(self) -> dict:
        # fall back to embed_model if no dedicated chat model is configured
        return {"name": self.chat_model or self.embed_model}

    ##########################################
    ########### RESPONSE PARSER ##############
    ##########################################

    def _parse_endpoint_model_details(self, model_info: dict) -> int:
        info: dict = model_info.get("model_info", {})
        for key, value in info.items():
            if key.endswith(".embedding_length"):
                return int(value)
        raise ValueError("Could not determine embedding vector size for model '%s'" % self.embed_model)

    def _parse_endpoint_model_context_length(self, model_info: dict) -> int:
        # Ollama reports context size under a key like "llama.context_length" inside model_info
        info: dict = model_info.get("model_info", {})
        for key, value in info.items():
            if key.endswith(".context_length"):
                return int(value)
        raise ValueError("Could not determine context length from Ollama model_info. Keys: %s" % list(info.keys()))

    def _parse_endpoint_embedding(self, response_data: dict) -> list[list[float]]:
        embeddings = response_data.get("embeddings")
        if not embeddings or not embeddings[0]:
            raise ValueError(
                "Ollama response does not contain valid embeddings. "
                "Response keys: %s" % list(response_data.keys())
            )
        return embeddings

    def _parse_endpoint_chat(self, response_data: dict) -> str:
        message = response_data.get("message", {})
        content = message.get("content", "")

        # Standard case: model replied with text in content
        if content:
            return content

        # Fallback: model used native tool-calling (content empty, tool_calls populated).
        # Synthesise a ReAct-compatible JSON string so AgentResponseParser can handle it.
        tool_calls = message.get("tool_calls")
        if tool_calls:
            first = tool_calls[0]
            fn = first.get("function", {})
            action = fn.get("name", "")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            thought = message.get("thinking", "")
            return json.dumps({"thought": thought, "action": action, "args": args}, ensure_ascii=False)

        raise ValueError(
            "Ollama chat response contains neither content nor tool_calls. "
            "Response keys: %s" % list(response_data.keys())
        )

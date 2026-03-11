from abc import abstractmethod
from dataclasses import dataclass

import httpx
from typing import Tuple
from shared.clients.ClientInterface import ClientInterface
from shared.helper.HelperConfig import HelperConfig

class LLMClientInterface(ClientInterface):
    def __init__(self, helper_config: HelperConfig):
        super().__init__(helper_config=helper_config)

        # embedding config
        self.embed_distance = helper_config.get_string_val(f"{self.get_client_type().upper()}_DISTANCE", default="Cosine")
        self.embed_model = helper_config.get_string_val(f"{self.get_client_type().upper()}_MODEL_EMBEDDING")
        self.embed_model_max_chars = helper_config.get_number_val(f"{self.get_client_type().upper()}_MODEL_EMBEDDING_MAX_CHARS")

        # chat / completion config
        self.chat_model = helper_config.get_string_val(f"{self.get_client_type().upper()}_MODEL_CHAT")
        self.chat_model_max_chars = helper_config.get_number_val(f"{self.get_client_type().upper()}_MODEL_CHAT_MAX_CHARS")
        self.vision_model = helper_config.get_string_val(f"{self.get_client_type().upper()}_MODEL_VISION")

    ##########################################
    ############### CHECKER ##################
    ##########################################

    ##########################################
    ################ GETTER ##################
    ##########################################

    ################ GENERAL ##################
    def _get_client_type(self) -> str:
        return "llm"
    
    def get_vision_model(self) -> str | None:
        """Return the configured vision model name, or None if not set."""
        return self.vision_model
    
    def get_chat_model(self) -> str:
        """Return the configured chat/completion model name."""
        return self.chat_model
    
    def get_embed_model(self) -> str:
        """Return the configured embedding model name."""
        return self.embed_model
    
    def get_embed_model_max_chars(self) -> int:
        """Return the maximum input character length for the embedding model."""
        return self.embed_model_max_chars
    
    def get_chat_model_max_chars(self) -> int:
        """Return the maximum input character length for the chat/completion model."""
        return self.chat_model_max_chars

    ################ ENDPOINTS ##################
    @abstractmethod
    def _get_endpoint_models(self) -> str:
        """Returns the endpoint path for model listing requests (e.g. "/api/tags")."""
        pass

    @abstractmethod
    def get_endpoint_embedding(self) -> str:
        """Returns the endpoint path for embedding requests (e.g. "/api/embed")."""
        pass

    @abstractmethod
    def get_endpoint_model_details(self) -> str:
        """Returns the endpoint path for model details requests (e.g. "/api/show")."""
        pass

    @abstractmethod
    def _get_endpoint_chat(self) -> str:
        """Returns the endpoint path for chat/completion requests (e.g. "/api/chat")."""
        pass

    ################ PAYLOADS ##################
    @abstractmethod
    def get_embed_payload(self, texts: list[str]) -> dict:
        """Build the backend-specific request body for an embedding request.

        Args:
            texts (list[str]): The texts to embed.

        Returns:
            dict: JSON-serialisable request body (e.g. {"model": "...", "input": [...]}).
        """
        pass

    @abstractmethod
    def get_chat_payload(self, messages: list[dict]) -> dict:
        """Build the backend-specific request body for a chat/completion request.

        Args:
            messages (list[dict]): OpenAI-format messages
                (e.g. [{"role": "user", "content": "..."}]).

        Returns:
            dict: JSON-serialisable request body.
        """
        pass

    @abstractmethod
    def get_model_details_payload(self) -> dict:
        """
        Build the backend-specific request body for a model details request.

        Returns:
            dict: JSON-serialisable request body (e.g. {"name": "..."}).
        """
        pass

    ##########################################
    ########### RESPONSE PARSER ##############
    ##########################################

    @abstractmethod
    def _parse_endpoint_model_details(self, model_info: dict) -> int:
        """Extracts the embedding vector size from the model information response.

        Args:
            model_info (dict): The raw response from the model details endpoint.

        Returns:
            int: The dimension of the embedding vectors produced by the model.
        """
        pass

    @abstractmethod
    def _parse_endpoint_embedding(self, response_data: dict) -> list[list[float]]:
        """Extract embedding vectors from a raw embedding API response.

        Response format differs by backend:
        - Ollama /api/embed: {"embeddings": [[...], [...]]}  — already ordered
        - OpenAI-compatible: {"data": [{"embedding": [...], "index": 0}]} — needs sorting

        Args:
            response_data (dict): The parsed JSON response body.

        Returns:
            list[list[float]]: Embedding vectors in the same order as the input texts.
        """
        pass

    @abstractmethod
    def _parse_endpoint_chat(self, response_data: dict) -> str:
        """Extract the assistant reply text from a raw chat API response.

        Args:
            response_data (dict): The parsed JSON response body.

        Returns:
            str: The assistant reply text.
        """
        pass

    ##########################################
    ############### REQUESTS #################
    ##########################################

    async def do_fetch_models(self) -> httpx.Response:
        """Fetch the list of available models from the backend."""
        return await self.do_request(method="GET", endpoint=self._get_endpoint_models())

    async def do_fetch_embedding_vector_size(self) -> Tuple[int, str]:
        """Fetch the output vector dimension and distance metric of the configured embedding model.

        Returns:
            Tuple[int, str]: (vector_dimension, distance_metric)
        """
        response = await self.do_request(
            method="POST",
            json=self.get_model_details_payload(),
            endpoint=self.get_endpoint_model_details(),
            raise_on_error=True,
        )
        vector_size = self._parse_endpoint_model_details(model_info=response.json())
        return vector_size, self.embed_distance

    async def do_embed(self, texts: list[str] | str) -> list[list[float]]:
        """Send an embedding request and return the extracted vectors.

        Args:
            texts (list[str] | str): One or more texts to embed.

        Returns:
            list[list[float]]: Embedding vectors in the same order as the inputs.
        """
        texts = [texts] if isinstance(texts, str) else texts
        body = self.get_embed_payload(texts)
        response = await self.do_request(method="POST", endpoint=self.get_endpoint_embedding(), json=body)
        if response.status_code != 200:
            self.logging.error(
                "Embedding request failed: status %d, body: %s",
                response.status_code,
                response.text[:200],
            )
            raise Exception("Embedding request failed with status %d." % response.status_code)
        return self._parse_endpoint_embedding(response.json())

    async def do_chat(self, messages: list[dict]) -> str:
        """Send a plain chat/completion request and return the assistant reply text.

        Args:
            messages (list[dict]): OpenAI-format messages
                (e.g. [{"role": "user", "content": "..."}]).

        Returns:
            str: The assistant reply text.

        Raises:
            Exception: If the HTTP request fails.
            ValueError: If the response does not contain a valid reply.
        """
        body = self.get_chat_payload(messages)
        response = await self.do_request(
            method="POST",
            endpoint=self._get_endpoint_chat(),
            json=body,
            raise_on_error=True,
        )
        return self._parse_endpoint_chat(response.json())

    async def do_chat_vision(self, messages: list[dict]) -> str:
        """Send a chat/completion request to vision model.

        Like do_chat(), but overrides self.chat_model with the vision model.

        Args:
            messages (list[dict]): OpenAI-format messages
                (e.g. [{"role": "user", "content": [...]}]).

        Returns:
            str: The assistant reply text.

        Raises:
            Exception: If the HTTP request fails.
            ValueError: If the response does not contain a valid reply.
        """
        body = self.get_chat_payload(messages)
        body["model"] = self.get_vision_model()  # override model
        response = await self.do_request(
            method="POST",
            endpoint=self._get_endpoint_chat(),
            json=body,
            raise_on_error=True,
        )
        return self._parse_endpoint_chat(response.json())
    
    
from abc import ABC, abstractmethod

import httpx
from httpx._types import QueryParamTypes, RequestContent, RequestData, RequestFiles
from typing import Any
from shared.models.config import EnvConfig

from shared.helper.HelperConfig import HelperConfig


class ClientInterface(ABC):
    def __init__(self, helper_config: HelperConfig):
        self.logging = helper_config.get_logger()
        self._helper_config = helper_config
        self.timeout = helper_config.get_number_val(f"{self.get_client_type().upper()}_TIMEOUT", default=30.0)
        
        # client and config
        self._client: httpx.AsyncClient | None = None
        self.validate_full_configuration()

    ##########################################
    ############### CHECKER ##################
    ##########################################

    def validate_full_configuration(self) -> None:
        """
        Validates that all required configuration values for the client are set and valid.

        Raises:
            Exception: If any required configuration value is missing or invalid.
        """
        req_config = self._get_required_config()
        for config in req_config:
            _ = self.get_config_val(raw_key=config.env_key, default=config.default, val_type=config.val_type)

    ##########################################
    ################ GETTER ##################
    ##########################################

    ################ GENERAL ##################
    def get_client_type(self) -> str:
        """
        Returns the type of the client in lowercase. E.g. "rag"
        """
        return self._get_client_type().lower()

    @abstractmethod
    def _get_client_type(self) -> str:
        """
        Returns the type of the client. E.g. "rag"
        """
        pass

    def get_engine_name(self) -> str:
        """
        Returns the name of the engine used by the client. E.g. "qdrant"
        """
        return self._get_engine_name().lower()
    
    @abstractmethod
    def _get_engine_name(self) -> str:
        """
        Returns the name of the engine used by the client. E.g. "qdrant"
        """
        pass

    ################ CONFIG ##################
    @abstractmethod
    def _get_required_config(self) -> list[EnvConfig]:
        """
        Returns all required configurations for the client.

        Returns:
            list[EnvConfig]: A list containing the details of each required configuration key.
        """
        pass

    def _get_config_key_name(self, raw_key:str) -> str:
        """
        Returns:
            str: The full configuration key name for the client. E.g. "RAG_QDRANT_API_KEY"
        """
        key_prefix = f"{self.get_client_type().upper()}_{self.get_engine_name().upper()}"
        return f"{key_prefix}_{raw_key.upper()}"
    
    def get_config_val(self, raw_key:str, default: Any = None, val_type: str = "string") -> Any:
        """
        Retrieves the value of a configuration key for the client.

        Args:
            raw_key (str): The raw configuration key name
            default (Any): The default value to return if the configuration key is not set
            val_type (str): The type of the configuration value ("string", "number", "bool", "list")
        """
        key = self._get_config_key_name(raw_key)
        if val_type == "string":
            return self._helper_config.get_string_val(key, default=default)
        elif val_type == "number":
            return self._helper_config.get_number_val(key, default=default)
        elif val_type == "bool":
            return self._helper_config.get_bool_val(key, default=default)
        elif val_type == "list":
            return self._helper_config.get_list_val(key, default=default)
        else:
            raise ValueError(f"Unsupported config value type '{val_type}' for env key '{raw_key}' in {self.get_client_type().upper()} client '{self.get_engine_name()}'.")

    ################ AUTH ##################
    @abstractmethod
    def _get_auth_header(self) -> dict:
        """
        Returns the authentication header for the client backend server, if an API key is set.
        
        Returns:
            dict: A dictionary containing the auth data
        """
        pass

    ################ ENDPOINTS ##################
    @abstractmethod
    def _get_base_url(self) -> str:
        """
        Returns the base URL of the client backend server from env variables
        
        Returns:
            str: The base URL of the client backend server (e.g. "http://localhost:8000")

        Raises:
            Exception: If the required environment variable for the server URL is not set or invalid.
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_healthcheck(self) -> str:
        """
        Returns the endpoint path for healthcheck requests.
        
        Returns:
            str: The endpoint path for healthcheck requests (e.g. "/healthz")

        Raises:
            NotImplementedError: If the method is not implemented in a subclass.
        """
        pass

    ##########################################
    ############### REQUESTS #################
    ##########################################
    
    async def do_healthcheck(self) -> httpx.Response:
        """Check if the client backend is healthy by sending a test request.

        Returns:
            httpx.Response: The response from the healthcheck request.
        """
        #healtheck
        return await self.do_request(method="GET", endpoint=self._get_endpoint_healthcheck())
    
    ##########################################
    ############ CORE REQUESTS ###############
    ##########################################

    async def boot(self) -> None:
        """Initialise the HTTP client and any other resources needed for making requests."""
        self._client = httpx.AsyncClient(timeout=self.timeout)

    async def close(self) -> None:
        """Close the HTTP client and any other resources."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def do_request(
        self,
        method: str = "GET",
        content: RequestContent | None = None,
        data: RequestData | None = None,
        files: RequestFiles | None = None,
        json: dict | None = None,
        params: QueryParamTypes | None = None,
        endpoint: str = "",
        additional_headers: dict | None = None,
        raise_on_error: bool = False,
    ) -> httpx.Response:
        """Send an HTTP request to the client backend.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, …).
            content: Raw bytes / stream body.
            data: Form-encoded body (dict or list of tuples).
            files: Multipart file upload.
            json: JSON-serialisable body (sets Content-Type automatically).
            params: URL query parameters.
            endpoint: Path to append to the base URL (leading slash optional).
            additional_headers: Extra headers that override the defaults.

        Returns:
            The raw httpx.Response. Raises Exception on non-2xx status.

        Raises:
            Exception: If the client is not initialised or the request fails or returns a non-2xx status (when raise_on_error is True).
        """
        if self._client is None:
            raise Exception("HTTP client not initialised. Call boot() before making requests.")
        
        endpoint = "/" + endpoint.strip().lstrip("/") if endpoint.strip() else ""

        # Do NOT set a default Content-Type: httpx sets it automatically for json/data/files.
        # For content (raw bytes), the caller must pass the correct type via additional_headers.
        headers: dict = {}
        headers.update(self._get_auth_header())
        if additional_headers:
            headers.update(additional_headers)

        kwargs: dict = {
            "url": f"{self._get_base_url().rstrip('/')}{endpoint}",
            "headers": headers,
            "timeout": self.timeout,
            "params": params,
        }

        # Build body kwargs.
        # files + data together → httpx sends multipart/form-data with both file
        # parts and field parts.  Any other combination is mutually exclusive.
        if content is not None:
            kwargs["content"] = content
        elif files is not None:
            kwargs["files"] = files
            if data is not None:
                kwargs["data"] = data
        elif data is not None:
            kwargs["data"] = data
        elif json is not None:
            kwargs["json"] = json

        response = await self._client.request(method, **kwargs)

        # Log and raise on error if requested
        if raise_on_error and response.status_code >= 300:
            self.logging.error(
                "Request to %s failed with status %d: %s",
                kwargs["url"],
                response.status_code,
                response.text,
            )
            raise Exception(
                f"Request to {kwargs['url']} failed with status {response.status_code}"
            )

        return response
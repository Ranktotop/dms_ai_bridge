from abc import abstractmethod

from shared.clients.ClientInterface import ClientInterface
from shared.clients.prompt.models.Prompt import PromptConfig, PromptConfigMessage
from shared.helper.HelperConfig import HelperConfig
from shared.helper.HelperFile import HelperFile
import os


class PromptClientInterface(ClientInterface):
    def __init__(self, helper_config: HelperConfig) -> None:
        super().__init__(helper_config=helper_config)
        self._helper_file = HelperFile()
        self._fallback_prompts: dict[str, PromptConfig] = {}
        debug_mode = self._helper_config.get_string_val("LOG_LEVEL", "info").lower() == "debug"
        self._stage = "production" if not debug_mode else "development"
        self._load_fallback_prompts()

    ##########################################
    ################ GETTER ##################
    ##########################################

    ################ GENERAL ##################
    def _get_client_type(self) -> str:
        """Returns the type of the client."""
        return "prompt"

    ##########################################
    ############## FALLBACKS #################
    ##########################################

    def _load_fallback_prompts(self) -> None:
        """
        Loads fallback prompt configurations from config file.
        """
        # reset
        self._fallback_prompts: dict[str, PromptConfig] = {}
        production_fallback_prompts: dict[str, PromptConfig] = {}
        development_fallback_prompts: dict[str, PromptConfig] = {}

        # Example implementation: Load a single fallback config from a JSON string in an env var
        path = os.path.join(os.environ["ROOT_DIR"], "config", f"fallback_prompts_{self.get_engine_name().lower()}.json")
        data = self._helper_file.read_json_file(path=path, asDict=True)
        if not data or "prompts" not in data:
            self.logging.warning(f"No fallback prompts found! Engine '{self.get_engine_name()}', expected path '{path}'.", color="yellow")
            return
        for config in data["prompts"]:
            try:
                prompt_config = PromptConfig(
                    id=config["id"],
                    stage=config["stage"],
                    messages=[
                        PromptConfigMessage(role=m["role"], content=m["content"])
                        for m in config["messages"]
                    ],
                    schema=config.get("schema", {}),
                    variables=config.get("variables", []),
                )
                if prompt_config.stage
                fallback_prompts[prompt_config.id] = prompt_config
                self.logging.info(f"Loaded fallback prompt config '{prompt_config.id}' for engine '{self.get_engine_name()}'.")
            except Exception as e:
                self.logging.error(f"Error loading fallback prompt config from entry '{config}': {e}")

        # filter all based on stage
        fallback_prompts = {k: v for k, v in fallback_prompts.items() if v.stage.lower() == self._stage.lower()}
        self._fallback_prompts = fallback_prompts

    ################ ENDPOINTS ##################
    @abstractmethod
    def _get_endpoint_fetch_config(self) -> str:
        """Returns the endpoint path for fetching a prompt configuration.

        Returns:
            str: The endpoint path (e.g. "/variants/configs/fetch").

        Raises:
            NotImplementedError: If not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_list_apps(self) -> str:
        """Returns the endpoint path for listing applications.

        Returns:
            str: The endpoint path (e.g. "/apps").

        Raises:
            NotImplementedError: If not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _get_endpoint_list_variants(self, app_id: str) -> str:
        """Returns the endpoint path for listing variants of an application.

        Args:
            app_id (str): The application identifier.

        Returns:
            str: The endpoint path (e.g. "/apps/{app_id}/variants").

        Raises:
            NotImplementedError: If not implemented in a subclass.
        """
        pass

    ##########################################
    ############## PAYLOADS ##################
    ##########################################

    @abstractmethod
    def _get_fetch_config_payload(
        self,
        app_slug: str,
        variant_slug: str | None,
        environment_slug: str | None,
        version: int | None,
    ) -> dict:
        """Builds the request payload for a config fetch.

        Args:
            app_slug (str): The application slug.
            variant_slug (str | None): Optional variant slug. If None and environment_slug
                is also None, the backend defaults to the production environment.
            environment_slug (str | None): Optional environment slug (e.g. "production").
            version (int | None): Optional version/revision number.

        Returns:
            dict: The request body for the config fetch endpoint.

        Raises:
            NotImplementedError: If not implemented in a subclass.
        """
        pass

    ##########################################
    ############# REQUESTS ###################
    ##########################################

    async def do_fetch_prompt(
        self,
        id: str
    ) -> PromptConfig:
        """Fetch the prompt configuration for a given application reference.

        At least one of variant_slug or environment_slug should be provided.
        If neither is given, the backend will typically default to the
        "production" environment.

        Args:
            app_slug (str): The application slug in the prompt registry.
            variant_slug (str | None): Optional variant slug to target a specific variant.
            environment_slug (str | None): Optional environment slug (e.g. "production",
                "staging", "development").
            version (int | None): Optional revision/version number.

        Returns:
            PromptConfig: The resolved prompt configuration including messages,
                LLM settings, input keys, and template format.

        Raises:
            RuntimeError: If the fetch request fails or returns an invalid response.
        """
        fallback = self._fallback_prompts.get(id, None)
        if not fallback:
            self.logging.warning(f"Requested prompt from {self.get_engine_name()} with id={id}. Consider adding a fallback prompt which is used on connection issues!", color="yellow")

        payload = self._get_fetch_config_payload(
            app_slug=app_slug,
            variant_slug=variant_slug,
            environment_slug=environment_slug,
            version=version,
        )
        response = await self.do_request(
            method="POST",
            endpoint=self._get_endpoint_fetch_config(),
            json=payload,
            raise_on_error=True,
        )
        return self._parse_fetch_config_response(
            response=response.json(),
            app_slug=app_slug,
            variant_slug=variant_slug,
            environment_slug=environment_slug,
            version=version,
        )

    async def do_list_apps(self, app_name: str | None = None) -> list[AppInfo]:
        """List all applications registered in the prompt registry.

        Args:
            app_name (str | None): Optional filter by application name.

        Returns:
            list[AppInfo]: List of available applications.

        Raises:
            RuntimeError: If the request fails.
        """
        params = {}
        if app_name:
            params["app_name"] = app_name
        response = await self.do_request(
            method="GET",
            endpoint=self._get_endpoint_list_apps(),
            params=params,
            raise_on_error=True,
        )
        return self._parse_list_apps_response(response.json())

    async def do_list_variants(self, app_id: str) -> list[VariantInfo]:
        """List all variants for a given application.

        Args:
            app_id (str): The application identifier.

        Returns:
            list[VariantInfo]: List of variants for the application.

        Raises:
            RuntimeError: If the request fails.
        """
        response = await self.do_request(
            method="GET",
            endpoint=self._get_endpoint_list_variants(app_id=app_id),
            raise_on_error=True,
        )
        return self._parse_list_variants_response(response.json())

    ##########################################
    ########### RESPONSE PARSER ##############
    ##########################################

    @abstractmethod
    def _parse_fetch_config_response(
        self,
        response: dict,
        app_slug: str,
        variant_slug: str | None,
        environment_slug: str | None,
        version: int | None,
    ) -> PromptConfig:
        """Parse the raw response from the config fetch endpoint.

        Args:
            response (dict): Raw JSON response from the fetch endpoint.
            app_slug (str): The application slug used in the request.
            variant_slug (str | None): The variant slug used in the request.
            environment_slug (str | None): The environment slug used in the request.
            version (int | None): The version used in the request.

        Returns:
            PromptConfig: The parsed prompt configuration.

        Raises:
            NotImplementedError: If not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _parse_list_apps_response(self, response: list) -> list[AppInfo]:
        """Parse the raw response from the list apps endpoint.

        Args:
            response (list): Raw JSON response (list of app objects).

        Returns:
            list[AppInfo]: Parsed list of applications.

        Raises:
            NotImplementedError: If not implemented in a subclass.
        """
        pass

    @abstractmethod
    def _parse_list_variants_response(self, response: list) -> list[VariantInfo]:
        """Parse the raw response from the list variants endpoint.

        Args:
            response (list): Raw JSON response (list of variant objects).

        Returns:
            list[VariantInfo]: Parsed list of variants.

        Raises:
            NotImplementedError: If not implemented in a subclass.
        """
        pass

from shared.clients.prompt.PromptClientInterface import PromptClientInterface
from shared.clients.prompt.models.Prompt import PromptConfig, PromptConfigMessage
from shared.helper.HelperConfig import HelperConfig
from shared.models.config import EnvConfig


class PromptClientAgenta(PromptClientInterface):
    """Prompt client implementation for the Agenta prompt registry.

    Agenta API docs: https://agenta.ai/docs/reference/api/agenta-api

    Configuration keys:
        PROMPT_AGENTA_BASE_URL  — Base URL of the Agenta API
                                  (e.g. https://cloud.agenta.ai/api)
        PROMPT_AGENTA_API_KEY   — Bearer token for authentication
    """

    def __init__(self, helper_config: HelperConfig) -> None:
        super().__init__(helper_config=helper_config)

    ##########################################
    ################ GETTER ##################
    ##########################################

    def _get_engine_name(self) -> str:
        return "agenta"

    def _get_base_url(self) -> str:
        base = self.get_config_val("BASE_URL")
        # if not already exist, add /api suffix to base URL
        if not base.endswith("/api"):
            base = base.rstrip("/") + "/api"
        return base

    def _get_auth_header(self) -> dict:
        api_key = self.get_config_val("API_KEY")
        return {"Authorization": f"{api_key}"}

    def _get_endpoint_healthcheck(self) -> str:
        return self._get_endpoint_list_apps()

    def _get_required_config(self) -> list[EnvConfig]:
        return [
            EnvConfig(env_key="BASE_URL", val_type="string"),
            EnvConfig(env_key="API_KEY", val_type="string"),
        ]

    ################ ENDPOINTS ##################

    def _get_endpoint_list_apps(self) -> str:
        return "apps"

    def _get_endpoint_list_environments(self, app_id:str) -> str:
        return f"apps/{app_id}/environments"

    def _get_endpoint_fetch_prompt_config(self) -> str:
        return f"variants/configs/fetch"

    ##########################################
    ############## PAYLOADS ##################
    ##########################################

    def _construct_prompt_config_payload(self, id:str, stage:str)-> dict:
        # since we do only have

    def _get_fetch_prompt_config_payload(
        self,
        app_id: str,
        variant_id: str | None,
        environment_id: str | None
    ) -> dict:
        """Builds the Agenta /variants/configs/fetch request body.

        Either variant_ref or environment_ref must be provided. If neither is
        given the Agenta backend defaults to the "production" environment.

        Args:
            app_id (str): Application ID registered in Agenta.
            variant_id (str | None): Variant ID; mutually exclusive with environment_id.
            environment_id (str | None): Environment ID, e.g. "production".

        Returns:
            dict: JSON-serialisable payload for the fetch endpoint.
        """
        return {
            "variant_ref": {
                "slug": "string",
                "version": 0,
                "commit_message": "string",
                "id": variant_id
            },
            "environment_ref": {
                "slug": "string",
                "version": 0,
                "commit_message": "string",
                "id": environment_id
            },
            "application_ref": {
                "slug": "string",
                "version": 0,
                "commit_message": "string",
                "id": app_id
            }
            }

    ##########################################
    ########### RESPONSE PARSER ##############
    ##########################################

    def _parse_fetch_prompt_config_response(
        self,
        response: dict
    ) -> PromptConfig:
        config = response.get("params", {}).get("prompt", {})
        _messages = config.get("messages", [])
        _llmconfig = config.get("llm_config", {})

        # create PromptConfigMessage from each message
        messages:list[PromptConfigMessage] = []
        for m in _messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if role and content:
                messages.append(PromptConfigMessage(role=role, content=content))

        # if there is a schema, save it
        _response_format = _llmconfig.get("response_format", {})
        schema_key = _response_format.get("type", None)
        if schema_key:
            schema = _response_format.get(schema_key) if schema_key in _response_format else None
        else:
            schema = None

        # get variables
        variables = config.get("input_keys", [])

        return PromptConfig(
            id="",
            stage="",
            messages=messages,
            schema=schema,
            variables=variables
        )

    def _parse_list_apps_response(self, response: list) -> list[AppInfo]:
        """Parse the Agenta GET /apps response.

        Expected response shape: list of app objects with keys
        "app_id", "app_name", "app_slug" (slug may not always be present).

        Args:
            response (list): Raw JSON list from GET /apps.

        Returns:
            list[AppInfo]: Parsed application info list.
        """
        return [
            AppInfo(
                id=app.get("app_id", ""),
                name=app.get("app_name", ""),
                slug=app.get("app_slug"),
            )
            for app in (response or [])
        ]

    def _parse_list_variants_response(self, response: list) -> list[VariantInfo]:
        """Parse the Agenta GET /apps/:app_id/variants response.

        Expected response shape: list of variant objects with keys
        "variant_id", "variant_name", "variant_slug", "revision".

        Args:
            response (list): Raw JSON list from GET /apps/:app_id/variants.

        Returns:
            list[VariantInfo]: Parsed variant info list.
        """
        return [
            VariantInfo(
                id=variant.get("variant_id", ""),
                name=variant.get("variant_name", ""),
                slug=variant.get("variant_slug"),
                version=variant.get("revision"),
            )
            for variant in (response or [])
        ]

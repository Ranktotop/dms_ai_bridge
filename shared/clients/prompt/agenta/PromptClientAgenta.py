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

    def _get_endpoint_list_environments(self, app_id: str) -> str:
        return f"apps/{app_id}/environments"

    def _get_endpoint_fetch_prompt_config(self) -> str:
        return f"variants/configs/fetch"

    ##########################################
    ############## REQUESTS ##################
    ##########################################

    async def _search_apps(self, app_name: str, only_one: bool = False) -> list[dict] | dict | None:
        """
        Search for apps with a name matching the given app_name (case-insensitive).

        Args:
            app_name (str): The name of the app to search for.
            only_one (bool): If true only the first matching app gets returned

        Returns:
            list[dict]|dict|None: A list of apps matching the given name or single dict if only_one is True. Returns None if only_one is True but no apps are found
        """
        _response = await self.do_request(
            method="GET",
            endpoint=self._get_endpoint_list_apps(),
            raise_on_error=True,
        )
        apps = _response.json()
        app_list = [app for app in apps if app.get("app_id", None) and app.get("app_name", "").lower() == app_name.lower()]
        if only_one:
            return app_list[0] if app_list else None
        return app_list

    async def _search_environments(self, apps: list[dict] | dict | None, stage: str) -> dict[str, dict]:
        """
        Search for environments with a name matching the given app_name (case-insensitive).

        Args:
            apps (list[dict]|dict|None): A list of apps or single app dict to search environments for. If None, returns an empty dict.
            stage (str): The stage/environment name to match (e.g. "production").

        Returns:
            dict[str, dict]: A dictionary mapping app IDs to their corresponding environments.
        """
        # check for none
        if apps is None:
            return {}
        # check if apps is a single dict
        if isinstance(apps, dict):
            apps = [apps]
        # collect the ids of the given apps
        app_ids = [app.get("app_id") for app in apps if app.get("app_id", None)]
        environments: dict[str, dict] = {}
        # iterate all apps
        for app_id in app_ids:
            try:
                # fetch environments for the current app
                _response = await self.do_request(
                    method="GET",
                    endpoint=self._get_endpoint_list_environments(app_id=app_id),
                    raise_on_error=True,
                )
                _envdata = _response.json()
                # filter the environments...
                # Keep only valids...
                valid_environments = [env for env in _envdata if env.get("project_id", None)]
                # Keep only those matching the stage...
                stage_environments = [env for env in valid_environments if env.get("name", "").lower() == stage.lower()]
                # Keep only those with a deployed variant...
                deployed_environments = [env for env in stage_environments if env.get("deployed_app_variant_id", None)]
                # Since stages are unique per app, we can take the first matching environment (if any)
                if deployed_environments:
                    environments[app_id] = deployed_environments[0]
            except Exception as e:
                self.logging.error(f"Error fetching environments for app_id '{app_id}': {e}")
        return environments

    ##########################################
    ############## PAYLOADS ##################
    ##########################################

    async def _construct_prompt_config_payload(self, id: str) -> dict:
        # fetch all apps (=prompts)
        app = await self._search_apps(app_name=id, only_one=True)
        if not app:
            raise ValueError(f"No {self.get_engine_name()} app found with name '{id}' for prompt config fetch.")

        # fetch environments for the app and stage
        environments = await self._search_environments(apps=app, stage=self._stage)
        if not environments:
            raise ValueError(f"No {self.get_engine_name()} environments found for app_id '{app['app_id']}' and stage '{self._stage}' for prompt config fetch.")

        # get the environment for our app
        env = environments.get(app["app_id"], None)
        if not env:
            raise ValueError(f"No {self.get_engine_name()} environment found for app_id '{app['app_id']}' and stage '{self._stage}' for prompt config fetch.")
        # safety check
        required_params = ["app_id", "project_id", "deployed_app_variant_id"]
        for r in required_params:
            if r not in env or not env[r].strip():
                raise ValueError(f"Missing required {self.get_engine_name()} parameter '{r}' in environment data for app_id '{app['app_id']}'.")
        return {
            "variant_id": env["deployed_app_variant_id"],
            "environment_id": env["project_id"],
            "application_id": env["app_id"]
        }

    async def _get_fetch_prompt_config_payload(
        self,
        id: str
    ) -> dict:
        # find the right combination of ids
        ids = await self._construct_prompt_config_payload(id=id)

        return {
            "variant_ref": {
                "slug": "string",
                "version": 0,
                "commit_message": "string",
                "id": ids["variant_id"]
            },
            "environment_ref": {
                "slug": "string",
                "version": 0,
                "commit_message": "string",
                "id": ids["environment_id"]
            },
            "application_ref": {
                "slug": "string",
                "version": 0,
                "commit_message": "string",
                "id": ids["application_id"]
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
        app_id = response.get("application_ref", {}).get("slug", None)  # we use slug because our search is based on the slug instead of id
        if not app_id:
            raise ValueError(f"Missing required app_id in response for prompt config fetch.")

        _messages = config.get("messages", [])
        _llmconfig = config.get("llm_config", {})

        # create PromptConfigMessage from each message
        messages: list[PromptConfigMessage] = []
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
            id=app_id,
            stage=self._stage,
            messages=messages,
            schema=schema,
            variables=variables
        )

    ##########################################
    ############## RENDERING #################
    ##########################################

    def _render_prompt_sanitized(self, messages: list[PromptConfigMessage], replacements: dict[str, str]) -> list[PromptConfigMessage]:
        # iterate all messages
        new_messages: list[PromptConfigMessage] = []
        for m in messages:
            template_content = m.content
            # on agenta, variables are wrapped in {{some_variable}}.
            for var, value in replacements.items():
                template_content = template_content.replace(f"{{{{{var}}}}}", value)
            new_messages.append(PromptConfigMessage(role=m.role, content=template_content))
        return new_messages

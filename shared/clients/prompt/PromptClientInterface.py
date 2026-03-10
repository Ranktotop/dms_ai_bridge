from abc import abstractmethod

from shared.clients.ClientInterface import ClientInterface
from shared.clients.prompt.models.Prompt import PromptConfig, PromptConfigMessage
from shared.helper.HelperConfig import HelperConfig
from shared.helper.HelperFile import HelperFile
from datetime import datetime
import os


class PromptClientInterface(ClientInterface):
    def __init__(self, helper_config: HelperConfig) -> None:
        super().__init__(helper_config=helper_config)
        self._helper_file = HelperFile()
        self._fallback_prompts: dict[str, PromptConfig] = {}
        self._live_prompts: dict[str, PromptConfig] = {}
        debug_mode = self._helper_config.get_string_val("LOG_LEVEL", "info").lower() == "debug"
        self._stage = "production" if not debug_mode else "development"
        self._load_fallback_prompts()

    ##########################################
    ############# FALLBACK FILE ##############
    ##########################################

    def _load_fallback_prompts(self) -> None:
        """
        Loads fallback prompt configurations from config file.
        """
        # reset
        self._fallback_prompts: dict[str, PromptConfig] = {}
        production_fallback_prompts: dict[str, PromptConfig] = {}
        development_fallback_prompts: dict[str, PromptConfig] = {}

        # load all data from config file
        path = self._get_fallback_file_path()
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
                if prompt_config.stage.lower() == "production":
                    production_fallback_prompts[prompt_config.id] = prompt_config
                elif prompt_config.stage.lower() == "development":
                    development_fallback_prompts[prompt_config.id] = prompt_config
                self.logging.debug(f"Loaded fallback prompt config ({prompt_config.stage.lower()}) '{prompt_config.id}' for engine '{self.get_engine_name()}'.")
            except Exception as e:
                self.logging.error(f"Error loading fallback prompt config from entry '{config}': {e}")

        # filter all based on stage
        self._fallback_prompts = production_fallback_prompts if self._stage.lower() == "production" else development_fallback_prompts

    def _add_to_fallback_config(self, prompt_config: PromptConfig) -> bool:
        """
        Adds a prompt configuration to the fallback config file. 
        This is useful for adding new prompts during runtime which can be used as fallback in case the prompt engine is not available on next runs.
        Also updates the fallback cache in memory for immediate use as fallback.

        Args:
            prompt_config (PromptConfig): The prompt configuration to add to the fallback config.

        Returns:
            bool: True if the config was successfully added, False otherwise.
        """
        # load current data from config file
        path = self._get_fallback_file_path()
        data = self._helper_file.read_json_file(path=path, asDict=True)
        if not data or "prompts" not in data:
            self.logging.warning(f"Invalid fallback prompts file! Engine '{self.get_engine_name()}', expected path '{path}'. Creating new one...", color="yellow")
            data = {"prompts": []}

        # add new config
        config_entry = {
            "id": prompt_config.id,
            "stage": prompt_config.stage,
            "messages": [
                {"role": m.role, "content": m.content} for m in prompt_config.messages
            ],
            "schema": prompt_config.schema,
            "variables": prompt_config.variables,
        }
        # if there is already an existent entry for this id and stage -> replace...
        replaced = False
        for i, existing in enumerate(data["prompts"]):
            if existing["id"].lower() == prompt_config.id.lower() and existing["stage"].lower() == prompt_config.stage.lower():
                data["prompts"][i] = config_entry
                replaced = True
                break
        # if not replaced, add new entry
        if not replaced:
            data["prompts"].append(config_entry)

        # save back to file
        if self._helper_file.write_json_file(data=data, path=path) is None:
            self.logging.warning(f"Failed to write fallback prompt config for id '{prompt_config.id}' and stage '{prompt_config.stage}' to file for engine '{self.get_engine_name()}'.", color="yellow")
            return False
        else:
            self.logging.info(f"Added prompt config with id '{prompt_config.id}' and stage '{prompt_config.stage}' to fallback configs of engine '{self.get_engine_name()}'.")
            # update cache
            self._load_fallback_prompts()
            return True

    ##########################################
    ################ GETTER ##################
    ##########################################

    ################ GENERAL ##################
    def _get_client_type(self) -> str:
        """Returns the type of the client."""
        return "prompt"

    def _get_fallback_file_path(self) -> str:
        """Returns the path to the expected fallback config file"""
        return os.path.join(os.environ["ROOT_DIR"], "config", f"fallback_prompts_{self.get_engine_name().lower()}.json")

    ################ ENDPOINTS ##################
    @abstractmethod
    def _get_endpoint_fetch_prompt_config(self) -> str:
        """Returns the endpoint path for fetching a prompt configuration.

        Returns:
            str: The endpoint path (e.g. "/variants/configs/fetch").

        Raises:
            NotImplementedError: If not implemented in a subclass.
        """
        pass

    ##########################################
    ############## PAYLOADS ##################
    ##########################################

    @abstractmethod
    async def _get_fetch_prompt_config_payload(
        self,
        id: str
    ) -> dict:
        """Builds the request payload for a config fetch.

        Args:
            id (str): The prompt ID.
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
        stage = self._stage.lower()
        fallback = self._fallback_prompts.get(id, None)
        if not fallback:
            self.logging.warning(
                f"Requested prompt from {self.get_engine_name()} with id={id} for stage '{stage}'. Consider adding a fallback prompt which is used on connection issues!", color="yellow")

        # check if we have the id in the cache
        if id in self._live_prompts:
            return self._live_prompts[id]

        # fetch the payload for the request
        prompt_config: PromptConfig | None = None
        payload: dict | None = None
        try:
            payload = await self._get_fetch_prompt_config_payload(id=id)
        except Exception as e:
            if fallback:
                self.logging.warning(f"Warning: Payload-Construction for PromptEngine {self.get_engine_name()} failed for prompt-id '{id}': {e}. Continue using fallback...", color="yellow")

        # if we have a payload, run it
        if payload:
            try:
                response = await self.do_request(
                    method="POST",
                    endpoint=self._get_endpoint_fetch_prompt_config(),
                    json=payload,
                    raise_on_error=True,
                )
                prompt_config = self._parse_fetch_prompt_config_response(
                    response=response.json())
            except Exception as e:
                if fallback:
                    self.logging.warning(f"Warning: Fetching PromptConfig from {self.get_engine_name()} failed for prompt-id '{id}': {e}. Continue using fallback...", color="yellow")

        # if live fetch was successful...
        if prompt_config is not None:
            # save to cache
            self._live_prompts[id] = prompt_config
            self.logging.debug(f"Successfully fetched prompt config for id '{id}' from {self.get_engine_name()} and saved to cache.")
            # update to config file and update cache
            if not self._add_to_fallback_config(prompt_config):
                # we can't update the config file, but at least save to fallback cache for future use
                self._fallback_prompts[prompt_config.id] = prompt_config
            return prompt_config
        else:
            if fallback:
                # save fallback to live cache for future use
                self._live_prompts[id] = fallback
                return fallback
            else:
                raise RuntimeError(f"Failed to fetch prompt '{id}' from {self.get_engine_name()} and no fallback prompt found for stage '{stage}'. Cannot continue without a prompt config.")

    ##########################################
    ########### RESPONSE PARSER ##############
    ##########################################

    @abstractmethod
    def _parse_fetch_prompt_config_response(
        self,
        response: dict
    ) -> PromptConfig:
        """Parse the raw response from the config fetch endpoint.

        Args:
            response (dict): Raw JSON response from the fetch endpoint.

        Returns:
            PromptConfig: The parsed prompt configuration.

        Raises:
            NotImplementedError: If not implemented in a subclass.
        """
        pass

    ##########################################
    ############## RENDERING #################
    ##########################################

    @abstractmethod
    def _render_prompt_sanitized(self, messages: list[PromptConfigMessage], replacements: dict[str, str]) -> list[PromptConfigMessage]:
        """
        Renders the prompt template by replacing variables with provided values.

        Args:
            messages (list[PromptConfigMessage]): The prompt messages containing the template and variables.
            replacements (dict[str, str]): A dictionary mapping variable names (lowercase) to their replacement values.

        Returns:
            list[PromptConfigMessage]: The rendered prompt messages with variables replaced.
        """

    def render_prompt(self, prompt: PromptConfig, replacements: dict[str, str | int | float | bool | datetime], max_chars: int = -1) -> list[PromptConfigMessage]:
        """
        Renders the prompt template by replacing variables with provided values.
        Does a sanity check on the variables and their types before rendering, and also handles max chars limit if specified.
        Use of replacements is case-insensitive

        Args:
            prompt (PromptConfig): The prompt configuration containing the template and variables.
            replacements (dict[str, str|int|float|bool|datetime]): A dictionary mapping variable names to their replacement values.
            max_chars (int): Optional maximum number of characters for the rendered prompt. If -1, no limit is applied.

        Returns:
            list[PromptConfigMessage]: The rendered prompt messages with variables replaced.
        """
        # ignore empty messages
        non_empty_messages = [m for m in prompt.messages if m.content and m.content.strip()]
        # if there are no messages or no variables, there is nothing to do
        if not non_empty_messages or not prompt.variables:
            return non_empty_messages

        required_fields = prompt.variables

        # transform each key in replacements to lowercase
        lc_replacements = {k.lower(): v for k, v in replacements.items()}

        # if any field is missing in replacements, throw error
        # we compare in lowercase to make it case-insensitive for the users of the function, but we keep the original keys for later
        for field in required_fields:
            if field.lower() not in lc_replacements:
                raise ValueError(f"Missing required variable '{field}' for rendering prompt with id '{prompt.id}'.")

        # now we loop through the required fields...
        converted_replacements = {}
        for key in required_fields:
            lc_key = key.lower()
            # and get the corresponding value...
            value = replacements.get(lc_key)
            # make sure each value is str | int | float | bool | datetime.
            if not isinstance(value, (str, int, float, bool, datetime)):
                raise ValueError(f"Invalid type for variable '{lc_key}' in rendering prompt with id '{prompt.id}'. Expected str, int, float, bool or datetime but got {type(value)}.")

            # convert to string for rendering
            # if datetime, convert to simple date string YYYY-MM-DD HH:MM:SS, otherwise convert to string as is
            # we save now with the original key for easier replacement later
            if isinstance(value, datetime):
                converted_replacements[key] = value.strftime("%Y-%m-%d %H:%M:%S")
            else:
                converted_replacements[key] = str(value)

        for key, value in replacements.items():
            # we use lc for making sure replacements are case-insensitive
            lc_key = key.lower()
        rendered_messages = self._render_prompt_sanitized(messages=non_empty_messages, replacements=converted_replacements)
        # if max_chars is not set, simply return the rendered messages, otherwise we need to cut them down to the max chars limit
        if max_chars < 1:
            return rendered_messages

        # prepare counter
        current_chars = 0
        truncated_messages = []
        # iterate messages
        for message in rendered_messages:
            # get current message length
            message_length = len(message.content)
            # if the message fits into the remaining chars, add it as is
            if current_chars + message_length <= max_chars:
                truncated_messages.append(message)
                current_chars += message_length
            # if the message exceeds the remaining chars, we need to cut it down
            else:
                # we need to cut down the message content to fit the remaining chars
                remaining_chars = max_chars - current_chars
                if remaining_chars > 0:
                    truncated_content = message.content[:remaining_chars]
                    truncated_messages.append(PromptConfigMessage(role=message.role, content=truncated_content))
                # after this, we have reached the max chars limit, so we stop processing further messages. We log a warning then
                self.logging.warning(
                    f"Rendered prompt with id '{prompt.id}' exceeds the max chars limit of {max_chars} after rendering. The prompt will be truncated to fit the limit, but consider increasing the limit or optimizing your prompt to avoid truncation.", color="yellow")
                break
        return truncated_messages

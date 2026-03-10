from shared.clients.prompt.PromptClientInterface import PromptClientInterface
from shared.helper.HelperConfig import HelperConfig


class PromptClientManager:
    """Manager class to instantiate the configured prompt client."""

    def __init__(self, helper_config: HelperConfig) -> None:
        self.helper_config = helper_config
        self.logging = helper_config.get_logger()
        self.client = self._initialize_client()

    def _get_engine_from_env(self) -> str:
        """Read the prompt engine name from env configuration.

        Returns:
            str: Capitalised engine name (e.g. "Agenta").

        Raises:
            ValueError: If PROMPT_ENGINE is not set or empty.
        """
        engine = self.helper_config.get_string_val("PROMPT_ENGINE")
        if not engine:
            raise ValueError("No prompt engine specified in configuration (PROMPT_ENGINE).")
        return engine.strip().lower().capitalize()

    def _initialize_client(self) -> PromptClientInterface:
        """Instantiate the prompt client for the configured engine.

        Returns:
            PromptClientInterface: The instantiated client.

        Raises:
            ValueError: If the engine is unsupported or cannot be imported.
        """
        engine = self._get_engine_from_env()
        class_name = f"PromptClient{engine}"
        try:
            module = __import__(
                f"shared.clients.prompt.{engine.lower()}.{class_name}",
                fromlist=[class_name],
            )
            client_class = getattr(module, class_name)
            client = client_class(helper_config=self.helper_config)
            self.logging.debug("Instantiated prompt client for engine: %s", engine)
            return client
        except (ImportError, AttributeError) as e:
            raise ValueError("Unsupported prompt engine '%s'. Error: %s" % (engine, e))

    def get_client(self) -> PromptClientInterface:
        """Return the instantiated prompt client."""
        return self.client

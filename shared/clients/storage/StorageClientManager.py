from shared.clients.storage.StorageClientInterface import StorageClientInterface
from shared.helper.HelperConfig import HelperConfig


class StorageClientManager:
    """Factory that instantiates the configured storage client via reflection."""

    def __init__(self, helper_config: HelperConfig) -> None:
        self.helper_config = helper_config
        self.logging = helper_config.get_logger()
        self.client = self._initialize_client()

    def _get_engine_from_env(self) -> str:
        """Read the storage engine name from env configuration.

        Returns:
            Capitalised engine name (e.g. "Local").

        Raises:
            ValueError: If STORAGE_ENGINE is not set or empty.
        """
        engine = self.helper_config.get_string_val("STORAGE_ENGINE")
        if not engine:
            raise ValueError("No storage engine specified in configuration (STORAGE_ENGINE).")
        return engine.strip().lower().capitalize()

    def _initialize_client(self) -> StorageClientInterface:
        """Instantiate the storage client for the configured engine.

        The reflection path follows the project convention:
            shared.clients.storage.{engine_lower}.StorageClient{Engine}

        Returns:
            StorageClientInterface: The instantiated client.

        Raises:
            ValueError: If the engine is unsupported or cannot be imported.
        """
        engine = self._get_engine_from_env()
        class_name = f"StorageClient{engine}"
        try:
            module = __import__(
                f"shared.clients.storage.{engine.lower()}.{class_name}",
                fromlist=[class_name],
            )
            client_class = getattr(module, class_name)
            client = client_class(helper_config=self.helper_config)
            self.logging.debug("Instantiated storage client for engine: %s", engine)
            return client
        except (ImportError, AttributeError) as e:
            raise ValueError("Unsupported storage engine '%s'. Error: %s" % (engine, e))

    def get_client(self) -> StorageClientInterface:
        """Return the instantiated storage client."""
        return self.client

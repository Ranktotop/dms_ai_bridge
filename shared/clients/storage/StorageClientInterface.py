from abc import abstractmethod
from typing import AsyncGenerator

from shared.clients.ClientInterface import ClientInterface
from shared.helper.HelperConfig import HelperConfig


class StorageClientInterface(ClientInterface):
    """ABC for all file-storage backends.

    HTTP-capable backends (e.g. S3) implement the standard ClientInterface hooks
    (_get_base_url, _get_auth_header, etc.) and inherit boot()/close()/do_healthcheck()
    from ClientInterface unchanged.

    Non-HTTP backends (e.g. StorageClientLocal) must override boot(), close(), and
    do_healthcheck() completely — identical pattern to CacheClientRedis — because they
    manage their own I/O lifecycle instead of an httpx.AsyncClient.
    """

    def __init__(self, helper_config: HelperConfig) -> None:
        super().__init__(helper_config=helper_config)

    ##########################################
    ################ GETTER ##################
    ##########################################

    def _get_client_type(self) -> str:
        return "storage"

    ##########################################
    ############# REQUESTS ###################
    ##########################################

    @abstractmethod
    async def do_store(self, file_bytes: bytes, filename: str) -> str:
        """Persist file bytes and return an opaque storage reference.

        Implementations should be content-addressed: identical bytes must produce
        the same storage_ref and must not write a second copy to storage. If the
        content already exists on the backend, do_store() returns the existing
        storage_ref without performing any write.

        The storage_ref is a backend-specific string that uniquely identifies
        the stored file and can later be passed to do_retrieve / do_delete.
        Callers must treat it as an opaque token — never construct or parse it.

        Args:
            file_bytes: Raw file content to persist.
            filename: Original filename; used as a human-readable hint for the storage key.

        Returns:
            An opaque storage_ref string identifying the stored file.
        """
        pass

    @abstractmethod
    async def do_retrieve(self, storage_ref: str) -> bytes:
        """Return the complete file bytes for the given storage reference.

        Args:
            storage_ref: Opaque reference returned by do_store().

        Returns:
            Complete file content as bytes.
        """
        pass

    @abstractmethod
    async def do_retrieve_stream(
        self, storage_ref: str, chunk_size: int = 65536
    ) -> AsyncGenerator[bytes, None]:
        """Yield file content in chunks without loading the whole file into memory.

        Preferred over do_retrieve() for large files or streaming HTTP responses.

        Local implementation reads from disk in chunk_size blocks via a thread
        executor to avoid blocking the event loop. S3 implementation uses
        Range-requests to stream from the object store.

        Usage:
            async for chunk in client.do_retrieve_stream(ref):
                ...

        Args:
            storage_ref: Opaque reference returned by do_store().
            chunk_size: Maximum bytes per yielded chunk (default 64 KiB).

        Yields:
            Raw byte chunks of at most chunk_size bytes.
        """
        pass

    @abstractmethod
    async def do_delete(self, storage_ref: str) -> bool:
        """Delete the file identified by storage_ref.

        Implementations must be idempotent — deleting a non-existent ref must
        not raise an exception.

        Args:
            storage_ref: Opaque reference returned by do_store().

        Returns:
            True if the file was deleted, False if it did not exist.
        """
        pass

    @abstractmethod
    async def do_exists(self, storage_ref: str) -> bool:
        """Return True if the storage_ref points to an existing file.

        Args:
            storage_ref: Opaque reference returned by do_store().
        """
        pass

    @abstractmethod
    async def do_get_view_url(self, storage_ref: str) -> str:
        """Return a URL suitable for viewing or downloading the stored file.

        Local backend: returns a file:// URL or a configured HTTP prefix URL.
        S3 backend: returns a presigned URL with a limited validity period.

        Args:
            storage_ref: Opaque reference returned by do_store().

        Returns:
            A URL string that can be used to access the file.
        """
        pass

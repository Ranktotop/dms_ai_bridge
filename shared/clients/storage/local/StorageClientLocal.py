import asyncio
import hashlib
import os
from pathlib import Path
from typing import AsyncGenerator

import httpx

from shared.clients.storage.StorageClientInterface import StorageClientInterface
from shared.helper.HelperConfig import HelperConfig
from shared.models.config import EnvConfig


class StorageClientLocal(StorageClientInterface):
    """Local filesystem implementation of StorageClientInterface.

    Does not use HTTP — boot(), close(), and do_healthcheck() are overridden
    to manage a simple directory lifecycle instead of an httpx.AsyncClient.
    This is the same non-HTTP override pattern used by CacheClientRedis.

    All disk I/O is dispatched via asyncio.to_thread() to avoid blocking
    the event loop on synchronous filesystem operations.
    """

    def __init__(self, helper_config: HelperConfig) -> None:
        super().__init__(helper_config=helper_config)
        self._base_path: str = self.get_config_val("BASE_PATH", default=None, val_type="string")
        # optional URL prefix for generating view URLs — falls back to file:// when empty
        self._view_url_prefix: str = self.get_config_val(
            "VIEW_URL_PREFIX", default="", val_type="string"
        )
        self._booted: bool = False

    ##########################################
    ################ GETTER ##################
    ##########################################

    def _get_engine_name(self) -> str:
        return "Local"

    def _get_required_config(self) -> list[EnvConfig]:
        return [
            EnvConfig(env_key="BASE_PATH", val_type="string", default=None),
            EnvConfig(env_key="VIEW_URL_PREFIX", val_type="string", default=""),
        ]

    def _get_auth_header(self) -> dict:
        # no HTTP auth — local filesystem access
        return {}

    def _get_base_url(self) -> str:
        # not used — this backend bypasses HTTP entirely
        return ""

    def _get_endpoint_healthcheck(self) -> str:
        # not used — do_healthcheck() is overridden to check filesystem write access
        return ""

    ##########################################
    ############# LIFECYCLE ##################
    ##########################################

    async def boot(self) -> None:
        """Create the base storage directory if it does not exist.

        Does not create an httpx.AsyncClient — local storage needs no network session.
        """
        # run mkdir in a thread to avoid blocking the event loop on slow filesystems
        await asyncio.to_thread(
            lambda: Path(self._base_path).mkdir(parents=True, exist_ok=True)
        )
        self._booted = True
        self.logging.info("StorageClientLocal booted. Base path: %s", self._base_path)

    async def close(self) -> None:
        """Mark client as closed. No connection to tear down."""
        self._booted = False

    ##########################################
    ############# CHECKER ####################
    ##########################################

    async def do_healthcheck(self) -> httpx.Response:
        """Check that the base storage directory is writable.

        Overrides the HTTP-based do_healthcheck() from ClientInterface because
        local storage has no network endpoint to probe.

        Returns:
            httpx.Response with status_code 200 if writable, 500 otherwise.
        """
        self._assert_booted()
        # os.access is synchronous but fast — acceptable for a health probe
        writable = await asyncio.to_thread(os.access, self._base_path, os.W_OK)
        return httpx.Response(status_code=200 if writable else 500)

    ##########################################
    ############# REQUESTS ###################
    ##########################################

    async def do_store(self, file_bytes: bytes, filename: str) -> str:
        """Write file_bytes to disk and return its relative path as the storage_ref.

        The filename is prefixed with the SHA256 hash of the content, making storage
        content-addressed: identical bytes always map to the same storage_ref. If the
        file already exists on disk the write is skipped entirely — no duplicate files,
        no temporary file that needs cleanup on duplicate DB detection.

        Args:
            file_bytes: Raw file content.
            filename: Original filename used as the human-readable suffix.

        Returns:
            Relative path within base_path (portable, opaque storage reference).
        """
        self._assert_booted()
        content_hash = hashlib.sha256(file_bytes).hexdigest()
        # hash prefix guarantees global uniqueness while keeping the original name readable
        safe_filename = f"{content_hash}_{filename}"
        full_path = Path(self._base_path) / safe_filename
        already_exists = await asyncio.to_thread(full_path.exists)
        if already_exists:
            # identical content already stored — skip write, return existing ref
            self.logging.debug(
                "StorageClientLocal.do_store: '%s' already on disk, skipping write", safe_filename
            )
            return safe_filename
        await asyncio.to_thread(full_path.write_bytes, file_bytes)
        # return a relative ref so the storage_ref remains portable if base_path changes
        return safe_filename

    async def do_retrieve(self, storage_ref: str) -> bytes:
        """Read and return the complete file bytes for the given storage reference.

        Args:
            storage_ref: Relative filename returned by do_store().
        """
        self._assert_booted()
        full_path = Path(self._base_path) / storage_ref
        return await asyncio.to_thread(full_path.read_bytes)

    async def do_retrieve_stream(
        self, storage_ref: str, chunk_size: int = 65536
    ) -> AsyncGenerator[bytes, None]:
        """Yield file content in chunk_size blocks without loading it fully into memory.

        Reads are dispatched to a thread executor to prevent blocking the event loop
        on disk I/O. Each chunk is yielded back on the calling coroutine.

        Args:
            storage_ref: Relative filename returned by do_store().
            chunk_size: Maximum bytes per chunk (default 64 KiB).

        Yields:
            Raw byte chunks.
        """
        self._assert_booted()
        full_path = Path(self._base_path) / storage_ref

        def _read_chunks() -> list[bytes]:
            # collect chunks in-thread so we can yield them on the event loop
            chunks = []
            with open(full_path, "rb") as f:
                while chunk := f.read(chunk_size):
                    chunks.append(chunk)
            return chunks

        # run the synchronous open+read loop in an executor thread to keep the
        # event loop free — then yield the already-buffered chunks asynchronously
        chunks = await asyncio.to_thread(_read_chunks)
        for chunk in chunks:
            yield chunk

    async def do_delete(self, storage_ref: str) -> bool:
        """Delete the file identified by storage_ref.

        Uses missing_ok=True so deleting a non-existent ref is a no-op rather
        than an exception — idempotent by design.

        Args:
            storage_ref: Relative filename returned by do_store().

        Returns:
            True if the file was deleted, False if it did not exist.
        """
        self._assert_booted()
        full_path = Path(self._base_path) / storage_ref
        existed = await asyncio.to_thread(full_path.exists)
        await asyncio.to_thread(full_path.unlink, True)  # missing_ok=True
        return existed

    async def do_exists(self, storage_ref: str) -> bool:
        """Return True if the file identified by storage_ref exists on disk.

        Args:
            storage_ref: Relative filename returned by do_store().
        """
        self._assert_booted()
        full_path = Path(self._base_path) / storage_ref
        return await asyncio.to_thread(full_path.exists)

    async def do_get_view_url(self, storage_ref: str) -> str:
        """Return a URL for accessing the stored file.

        If STORAGE_LOCAL_VIEW_URL_PREFIX is configured, returns
        '{prefix}/{storage_ref}'. Otherwise falls back to a file:// URL
        pointing at the absolute path on disk.

        Args:
            storage_ref: Relative filename returned by do_store().
        """
        self._assert_booted()
        if self._view_url_prefix:
            # strip trailing slash from prefix to avoid double slashes
            return f"{self._view_url_prefix.rstrip('/')}/{storage_ref}"
        return f"file://{Path(self._base_path) / storage_ref}"

    ##########################################
    ############# HELPERS ####################
    ##########################################

    def _assert_booted(self) -> None:
        """Raise RuntimeError if boot() has not been called."""
        if not self._booted:
            raise RuntimeError(
                "StorageClientLocal is not booted. Call boot() before using the client."
            )

"""File scanner with optional watchfiles-based watch mode."""
import asyncio
import os
from pathlib import Path
from typing import Callable, Awaitable

try:
    import watchfiles
    _WATCHFILES_AVAILABLE = True
except ImportError:
    _WATCHFILES_AVAILABLE = False

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".txt", ".md"})

_FILE_STABLE_INTERVAL = 0.5   # seconds between size checks
_FILE_STABLE_RETRIES  = 6     # max checks before giving up (6 × 0.5 s = 3 s total)


class FileScanner:
    """Scans a directory for supported document files."""

    def __init__(self, root_path: str) -> None:
        self._root = Path(root_path)

    def scan_once(self) -> list[str]:
        """Return absolute paths of all supported files under root_path."""
        files: list[str] = []
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(str(p) for p in self._root.rglob("*%s" % ext))
        return sorted(files)

    async def watch(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """Watch root_path for new/changed files and call callback(file_path).

        For each detected file a background task is spawned that polls the file
        size until stable and then invokes the callback.  Multiple files in the
        same watchfiles event batch are therefore checked concurrently without
        blocking the watcher loop.

        Args:
            callback: Async callable invoked with the absolute file path once
                      the file size has stabilised.

        Raises:
            ImportError: If watchfiles is not installed.
        """
        if not _WATCHFILES_AVAILABLE:
            raise ImportError(
                "watchfiles is not installed. "
                "Install with: pip install watchfiles>=0.21.0"
            )

        async for changes in watchfiles.awatch(str(self._root)):
            for _, file_path in changes:
                ext = os.path.splitext(file_path)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    asyncio.ensure_future(self._stabilize_then_call(file_path, callback))

    async def _stabilize_then_call(
        self,
        file_path: str,
        callback: Callable[[str], Awaitable[None]],
    ) -> None:
        """Wait until file_path is fully written, then invoke callback."""
        if await self._is_file_stable(file_path):
            await callback(file_path)

    async def _is_file_stable(self, file_path: str) -> bool:
        """Poll the file size until it stops changing.

        Returns True if the file is stable and ready for processing.
        Returns False if the file disappeared before it became stable.
        """
        previous_size = -1
        for _ in range(_FILE_STABLE_RETRIES):
            try:
                current_size = os.path.getsize(file_path)
            except OSError:
                return False
            if current_size == previous_size:
                return True
            previous_size = current_size
            await asyncio.sleep(_FILE_STABLE_INTERVAL)
        return previous_size >= 0

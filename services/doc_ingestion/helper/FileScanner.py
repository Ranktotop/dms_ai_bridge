"""File scanner with optional watchfiles-based watch mode."""
import os
from pathlib import Path

try:
    import watchfiles
    _WATCHFILES_AVAILABLE = True
except ImportError:
    _WATCHFILES_AVAILABLE = False

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".txt", ".md"})


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

    async def watch(self, callback) -> None:
        """Watch root_path for new/changed files and call callback(file_path).

        Args:
            callback: async callable(file_path: str) called for each changed file.

        Raises:
            ImportError: If watchfiles is not installed.
        """
        if not _WATCHFILES_AVAILABLE:
            raise ImportError(
                "watchfiles is not installed. "
                "Install with: pip install watchfiles>=0.21.0"
            )
        async for changes in watchfiles.awatch(str(self._root)):
            for change_type, file_path in changes:
                ext = os.path.splitext(file_path)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    await callback(file_path)

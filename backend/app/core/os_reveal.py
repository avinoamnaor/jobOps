"""Open a local folder in the OS file manager.

Deliberately isolated in one tiny module: JobOps is a local single-user Windows
app today, so the backend may launch File Explorer. If JobOps is ever deployed,
this is the single place to replace (or disable) that OS-specific behaviour —
nothing else in the codebase shells out to the operating system.
"""

import os
import sys
from pathlib import Path

from app.core.errors import FolderOpenFailed


def open_folder(path: Path) -> None:
    """Open `path` in the file manager, or raise a controlled error.

    Uses `os.startfile`, the reliable Windows way to open a folder (unlike
    `explorer.exe`, which returns a non-zero exit code even on success).
    """
    if sys.platform != "win32":
        raise FolderOpenFailed(str(path), "opening a folder is only supported on Windows")
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]  # Windows-only
    except OSError as exc:
        raise FolderOpenFailed(str(path), str(exc)) from exc

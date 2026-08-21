"""Content-addressed file storage.

Files are named after the SHA-256 hash of their own contents:

    <DOCUMENTS_ROOT>/e3b0c44298fc1c149afbf4c8996fb924....pdf

Three properties fall out of that, all of them things this project actually needs:

  * **Automatic deduplication.** The same bytes always produce the same name, so
    uploading a file twice cannot create two copies.
  * **Immutability.** Editing a CV changes its bytes, which changes its hash,
    which makes it a different file. An existing document can therefore never be
    silently altered — which is the whole point of "which exact CV did I send?".
  * **Safety.** The uploaded filename is never used to build a path, so a file
    called `../../.ssh/authorized_keys` is just a string in a metadata column.

The layout is flat rather than `<kind>/<hash>` on purpose: `kind` is mutable
metadata, and baking mutable metadata into a filesystem path means a metadata
edit would strand the file.

Everything is funnelled through this one small class so that Phase 10's "move it
to object storage" is a new implementation of these four methods rather than a
hunt through the codebase.
"""

import hashlib
import re
from pathlib import Path, PurePosixPath

from app.config import settings
from app.core.errors import UnsafeDocumentPath

# Extensions we are willing to put on disk. Anything else is dropped rather than
# sanitised, because a file with no extension is harmless and a creatively
# malformed one is not worth the risk.
_SAFE_EXTENSION = re.compile(r"^[a-z0-9]{1,10}$")

# Control characters and quotes would allow header injection if they reached a
# Content-Disposition header.
_UNSAFE_HEADER_CHARS = re.compile(r'[\x00-\x1f\x7f"\\]')


def compute_content_hash(content: bytes) -> str:
    """The SHA-256 hex digest that becomes the file's identity."""
    return hashlib.sha256(content).hexdigest()


def safe_extension(original_filename: str | None) -> str:
    """Extract a conservative file extension from an untrusted filename.

    Returns "" (no extension) rather than raising, because the extension is a
    convenience for humans browsing the folder — it is never load-bearing.

    >>> safe_extension("Applicant_CV.PDF")
    '.pdf'
    >>> safe_extension("../../etc/passwd")
    ''
    """
    if not original_filename:
        return ""

    # Normalise Windows separators so both path styles are handled, then take
    # only the final component.
    basename = PurePosixPath(original_filename.replace("\\", "/")).name
    suffix = PurePosixPath(basename).suffix.lower().lstrip(".")

    return f".{suffix}" if _SAFE_EXTENSION.fullmatch(suffix) else ""


def safe_header_filename(original_filename: str | None, *, fallback: str) -> str:
    """Strip anything from a filename that could break out of an HTTP header."""
    if not original_filename:
        return fallback
    cleaned = _UNSAFE_HEADER_CHARS.sub("", original_filename).strip()
    return cleaned or fallback


# Characters Windows forbids in a file/folder name, plus control characters.
_WINDOWS_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
# Device names Windows reserves regardless of extension.
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def sanitize_windows_component(
    name: str, *, fallback: str = "untitled", max_length: int = 120
) -> str:
    """Turn arbitrary text into one safe Windows path component (folder or file).

    Replaces forbidden characters with a space, collapses whitespace, strips the
    trailing dots/spaces Windows silently drops, guards reserved device names, and
    bounds the length so the full path stays well clear of MAX_PATH. The result is
    a single component — it can never contain a separator, so it cannot escape its
    parent directory.
    """
    cleaned = _WINDOWS_INVALID_CHARS.sub(" ", name)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip().rstrip(". ").strip()
    cleaned = cleaned[:max_length].rstrip(". ").strip()
    if not cleaned:
        return fallback
    # A reserved name is compared without its extension, case-insensitively.
    stem = cleaned.split(".", 1)[0].strip().lower()
    if stem in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


class DocumentStorage:
    """Reads and writes document bytes under a single root directory."""

    def __init__(self, root: Path) -> None:
        # Kept as a plain attribute so tests can point it at a temporary
        # directory without any dependency-injection machinery.
        self.root = root

    def build_relative_path(self, content_hash: str, original_filename: str | None) -> str:
        """The path we will store in the database, relative to the root.

        Always POSIX-style forward slashes, so the same database works on Windows
        and Linux — one of the small things that keeps future deployment cheap.
        """
        return f"{content_hash}{safe_extension(original_filename)}"

    def resolve(self, relative_path: str) -> Path:
        """Turn a stored relative path into an absolute one, safely.

        This is the security boundary. We build every stored path ourselves, so
        in normal operation nothing dangerous can arrive here — but a hand-edited
        or corrupted row must not be able to make the application read
        `/etc/passwd` or `C:\\Windows\\System32\\config\\SAM`.

        Note that `Path("/some/root") / "/etc/passwd"` returns `/etc/passwd` —
        joining does NOT contain an absolute path. That is exactly why the check
        below is on the *resolved* result rather than on the input string.
        """
        root = self.root.resolve()
        candidate = (root / relative_path).resolve()

        if not candidate.is_relative_to(root):
            raise UnsafeDocumentPath(relative_path)

        return candidate

    def exists(self, relative_path: str) -> bool:
        return self.resolve(relative_path).is_file()

    def write(self, relative_path: str, content: bytes) -> None:
        """Write bytes to storage.

        Idempotent by construction: the path is derived from a hash of the
        content, so if the file already exists it necessarily has these exact
        bytes and rewriting it would be pointless work.
        """
        destination = self.resolve(relative_path)
        if destination.is_file():
            return

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    def read(self, relative_path: str) -> bytes:
        return self.resolve(relative_path).read_bytes()


# Module-level singleton, built from configuration. Services import this.
# `root` is reassigned by a test fixture so the suite never touches real files.
document_storage = DocumentStorage(settings.documents_path)

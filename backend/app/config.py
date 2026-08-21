"""Application configuration.

Every value the application needs from its environment is declared here, in one
place, and nowhere else. No module reads `os.environ` directly.

Why pydantic-settings rather than plain `os.getenv`:
  * values are typed and coerced ("false" -> False, a string -> Path)
  * a missing required variable fails loudly at startup instead of producing a
    confusing error deep inside a request
  * the class doubles as documentation of the whole configuration surface
"""

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# This file lives at <repo>/backend/app/config.py, so:
#   parents[0] = app, parents[1] = backend, parents[2] = repository root
#
# Resolving the root explicitly means the application behaves identically no
# matter which directory you happen to run it from. Relying on the current
# working directory is one of the most common sources of "works on my machine".
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env_file_path() -> Path:
    """Which .env file to load — `.env` by default.

    Set JOBOPS_ENV_FILE to load a different one (e.g. `.env.demo` for taking
    presentation screenshots against a throwaway database). When the variable is
    unset — the normal case — this returns `.env` and the application behaves
    exactly as it always has. The switch is opt-in and affects only the terminal
    session that sets it.
    """
    name = os.environ.get("JOBOPS_ENV_FILE", ".env")
    candidate = Path(name)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file_path(),
        env_file_encoding="utf-8",
        # Ignore unrelated variables that happen to exist in the environment
        # rather than crashing on them.
        extra="ignore",
    )

    # --- Application -----------------------------------------------------
    app_env: str = "local"
    log_level: str = "INFO"

    # --- Database --------------------------------------------------------
    # No default: if DATABASE_URL is missing the application refuses to start.
    # That is deliberate. A silent fallback to some other database is worse than
    # a crash.
    database_url: str
    test_database_url: str | None = None
    sql_echo: bool = False

    # --- File storage ----------------------------------------------------
    documents_root: Path = Path("data/documents")

    # A CV is well under a megabyte. This is not a security boundary so much as
    # a guard against a mistaken upload quietly filling the disk.
    max_document_bytes: int = 10 * 1024 * 1024

    # The filename presented when downloading a CV "for submission" — a clean,
    # employer-facing name, independent of whatever Canva called the file. Kept
    # in config (not hardcoded) so the applicant's name is not committed to the
    # repository; set it in your local .env. The stored file is never renamed.
    submission_cv_filename: str = "CV - Applicant.pdf"

    # Local root under which a per-application folder is prepared on creation,
    # holding a convenience copy of the submitted CV. Real local path lives in
    # .env only; .env.example stays generic.
    application_export_root: Path = Path("application-exports")

    # --- Gmail (Phase 6.1: read-only sync foundation) ---------------------
    # Both files are produced by Google/`scripts/gmail_authorize.py`, never
    # committed, and live under the gitignored `secrets/` folder by default.
    gmail_credentials_path: Path = Path("secrets/gmail_credentials.json")
    gmail_token_path: Path = Path("secrets/gmail_token.json")
    # How far back a sync looks, and a safety cap on messages per call — this
    # phase is a foundation, not a full inbox import.
    gmail_sync_window_days: int = 30
    gmail_max_messages_per_sync: int = 200

    # --- Frontend --------------------------------------------------------
    # Kept as a plain comma-separated string rather than `list[str]`.
    # pydantic-settings parses list-typed fields as JSON, so `list[str]` would
    # require CORS_ORIGINS='["http://localhost:5173"]' in the .env file, which is
    # an unpleasant surprise. Splitting it ourselves is simpler and clearer.
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def documents_path(self) -> Path:
        """Absolute path to the document store.

        The database stores paths *relative* to this root, so the whole data
        directory can be moved to another machine — or later to object storage —
        without rewriting a single row.
        """
        if self.documents_root.is_absolute():
            return self.documents_root
        return (PROJECT_ROOT / self.documents_root).resolve()

    @property
    def application_export_path(self) -> Path:
        """Absolute root for per-application export folders.

        Absolute values are used as-is (e.g. a real folder elsewhere on disk);
        relative ones resolve against the repository root, like the document store.
        """
        if self.application_export_root.is_absolute():
            return self.application_export_root
        return (PROJECT_ROOT / self.application_export_root).resolve()

    @property
    def gmail_credentials_path_resolved(self) -> Path:
        if self.gmail_credentials_path.is_absolute():
            return self.gmail_credentials_path
        return (PROJECT_ROOT / self.gmail_credentials_path).resolve()

    @property
    def gmail_token_path_resolved(self) -> Path:
        if self.gmail_token_path.is_absolute():
            return self.gmail_token_path
        return (PROJECT_ROOT / self.gmail_token_path).resolve()


settings = Settings()

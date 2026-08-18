"""Application configuration.

Every value the application needs from its environment is declared here, in one
place, and nowhere else. No module reads `os.environ` directly.

Why pydantic-settings rather than plain `os.getenv`:
  * values are typed and coerced ("false" -> False, a string -> Path)
  * a missing required variable fails loudly at startup instead of producing a
    confusing error deep inside a request
  * the class doubles as documentation of the whole configuration surface
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# This file lives at <repo>/backend/app/config.py, so:
#   parents[0] = app, parents[1] = backend, parents[2] = repository root
#
# Resolving the root explicitly means the application behaves identically no
# matter which directory you happen to run it from. Relying on the current
# working directory is one of the most common sources of "works on my machine".
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
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


settings = Settings()

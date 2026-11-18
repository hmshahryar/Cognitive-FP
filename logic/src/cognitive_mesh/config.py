from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# logic/ (project root): src/cognitive_mesh/config.py -> parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "cognitive_mesh.db"


class Settings(BaseSettings):
    """App settings, loaded from environment variables and logic/.env."""

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    app_name: str = "Cognitive Mesh API"
    environment: str = "development"

    # Origins of the Next.js frontend allowed to call this API.
    frontend_origins: list[str] = ["http://localhost:3000"]

    # Clerk: used to verify session tokens and to read verified user profiles.
    clerk_secret_key: str = ""

    # Public URL of the Next.js app; Composio sends users back here after OAuth.
    frontend_url: str = "http://localhost:3000"

    # Composio project API key (server-side only, never sent to the browser).
    composio_api_key: str = ""
    # How long a started connection may take before its callback is refused.
    connection_attempt_ttl_minutes: int = 30

    # Secret for HMAC-signing approvals (>= 32 chars). Rotating it invalidates
    # every unexecuted approval, which is the safe failure mode.
    approval_signing_key: str = ""

    # --- AI employees ---
    google_api_key: str = ""
    llm_model: str = "gemini-4-hydrogen"
    # "composio" (keyless, default) or "gemini" (Google Search grounding; needs billing).
    search_provider: str = "composio"
    # Background runner that picks up QUEUED executions (off in tests).
    run_worker: bool = True
    worker_poll_seconds: float = 2.0
    max_review_rounds: int = 2
    # LangGraph checkpoints live in their OWN SQLite file, separate from app records.
    checkpoint_db_path: str = (PROJECT_ROOT / "data" / "checkpoints.db").as_posix()

    # Async SQLAlchemy URL. SQLite for now; a postgresql+psycopg URL later.
    database_url: str = f"sqlite+aiosqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"
    database_echo: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()

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

    # Async SQLAlchemy URL. SQLite for now; a postgresql+psycopg URL later.
    database_url: str = f"sqlite+aiosqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"
    database_echo: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()

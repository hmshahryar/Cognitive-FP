import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# logic/ (project root): src/cognitive_mesh/config.py -> parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "cognitive_mesh.db"
_NUMBERED_GOOGLE_KEY = re.compile(r"^google_api_key(\d+)$")


class _NumberedGoogleKeys(PydanticBaseSettingsSource):
    """Collects GOOGLE_API_KEY1, GOOGLE_API_KEY2, ... so adding a key needs no code change."""

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        env: PydanticBaseSettingsSource,
        dotenv: PydanticBaseSettingsSource,
    ) -> None:
        super().__init__(settings_cls)
        self._sources = [s for s in (dotenv, env) if isinstance(s, EnvSettingsSource)]

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        found: dict[int, str] = {}
        for source in self._sources:  # dotenv first, so a real environment variable wins
            for name, value in source.env_vars.items():
                match = _NUMBERED_GOOGLE_KEY.match(name.lower())
                if match and value and value.strip():
                    found[int(match.group(1))] = value.strip()
        return {"google_api_key_numbered": tuple(found[n] for n in sorted(found))}


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
    # Optional override. When empty, the app creates its own limited HubSpot auth config.
    hubspot_auth_config_id: str = ""
    # How long a started connection may take before its callback is refused.
    connection_attempt_ttl_minutes: int = 30

    # Secret for HMAC-signing approvals (>= 32 chars). Rotating it invalidates
    # every unexecuted approval, which is the safe failure mode.
    approval_signing_key: str = ""

    # --- AI employees ---
    google_api_key: str = ""
    # Filled from GOOGLE_API_KEY1, GOOGLE_API_KEY2, ...; used when another key hits its limit.
    google_api_key_numbered: tuple[str, ...] = Field(default=(), repr=False)
    llm_model: str = "gemini-4-hydrogen"
    # "composio" (keyless, default) or "gemini" (Google Search grounding; needs billing).
    search_provider: str = "composio"
    # Background runner that picks up QUEUED executions (off in tests).
    run_worker: bool = True
    worker_poll_seconds: float = 2.0
    max_review_rounds: int = 2
    # LangGraph checkpoints live in their OWN SQLite file, separate from app records.
    checkpoint_db_path: str = (PROJECT_ROOT / "data" / "checkpoints.db").as_posix()
    # Long-term memory (LangGraph store), also in its own SQLite file.
    memory_db_path: str = (PROJECT_ROOT / "data" / "memory.db").as_posix()

    # Async SQLAlchemy URL. SQLite for now; a postgresql+psycopg URL later.
    database_url: str = f"sqlite+aiosqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"
    database_echo: bool = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        numbered = _NumberedGoogleKeys(settings_cls, env_settings, dotenv_settings)
        return init_settings, env_settings, dotenv_settings, file_secret_settings, numbered

    @property
    def google_api_keys(self) -> tuple[str, ...]:
        """Every configured Gemini key in order, without blanks or repeats."""
        keys = (self.google_api_key.strip(), *self.google_api_key_numbered)
        return tuple(dict.fromkeys(key for key in keys if key))


@lru_cache
def get_settings() -> Settings:
    return Settings()

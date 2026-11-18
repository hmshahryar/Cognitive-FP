import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

from google.genai import errors as genai_errors
from langchain_google_genai import ChatGoogleGenerativeAI

from cognitive_mesh.config import Settings
from cognitive_mesh.integrations.gateway import IntegrationUnavailableError
from cognitive_mesh.services.errors import ConfigurationError

logger = logging.getLogger(__name__)

try:  # google-genai's HTTP transport (httpx2) raises its own transport errors
    from httpx2 import TransportError as _HttpxTransportError
except ImportError:  # pragma: no cover
    _HttpxTransportError = ConnectionError  # type: ignore[assignment,misc]

try:
    from httpx import TransportError as _HttpxV1TransportError
except ImportError:  # pragma: no cover
    _HttpxV1TransportError = ConnectionError  # type: ignore[assignment,misc]


def is_transient(exc: Exception) -> bool:
    """Network drops, provider 5xx and rate limits are worth retrying; bugs are not."""
    if isinstance(
        exc,
        (
            _HttpxTransportError,
            _HttpxV1TransportError,
            ConnectionError,
            TimeoutError,
            genai_errors.ServerError,
            IntegrationUnavailableError,
        ),
    ):
        return True
    if isinstance(exc, genai_errors.ClientError) and getattr(exc, "code", None) == 429:
        # Per-minute limits clear quickly; daily quotas (retry in hours) must not be retried.
        delay = quota_retry_seconds(exc)
        return delay is None or delay <= 60
    return False


def quota_retry_seconds(exc: Exception) -> float | None:
    """Google's RetryInfo.retryDelay (e.g. '44333s') from a 429, if present."""
    details = ((getattr(exc, "details", None) or {}).get("error") or {}).get("details") or []
    for d in details:
        if isinstance(d, dict) and d.get("@type", "").endswith("RetryInfo"):
            raw = str(d.get("retryDelay", "")).rstrip("s")
            try:
                return float(raw)
            except ValueError:
                return None
    return None


def is_quota_exhausted(exc: Exception) -> bool:
    return (
        isinstance(exc, genai_errors.ClientError)
        and getattr(exc, "code", None) == 429
        and not is_transient(exc)
    )


async def call_with_retry[T](
    fn: Callable[[], Awaitable[T]], *, attempts: int = 3, base_delay: float = 1.0
) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            if attempt == attempts or not is_transient(exc):
                raise
            delay = base_delay * 2 ** (attempt - 1) * (0.5 + random.random())
            logger.warning("Transient error (%r); retry %d in %.1fs", exc, attempt, delay)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")


def build_chat_model(settings: Settings, *, temperature: float | None = None):  # type: ignore[no-untyped-def]
    """The single place models are created (LangChain owns model integration)."""
    if not settings.google_api_key:
        raise ConfigurationError("GOOGLE_API_KEY is not configured")
    kwargs = {"temperature": temperature} if temperature is not None else {}
    return ChatGoogleGenerativeAI(
        model=settings.llm_model, google_api_key=settings.google_api_key, **kwargs
    )

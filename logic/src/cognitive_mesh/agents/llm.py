import asyncio
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any

from google.genai import errors as genai_errors
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import PrivateAttr

from cognitive_mesh.agents.key_pool import KeyPool, shared_key_pool
from cognitive_mesh.config import Settings
from cognitive_mesh.integrations.gateway import IntegrationUnavailableError
from cognitive_mesh.services.errors import ConfigurationError

logger = logging.getLogger(__name__)

LLM_PROVIDER = "google_genai"  # recorded with every draft for traceability
MODEL_TIMEOUT_SECONDS = 240.0  # default-thinking drafts were measured at about 154 seconds
MANAGER_TIMEOUT_SECONDS = 20.0  # routing must never hold up the user's chat
DEFAULT_QUOTA_COOLDOWN_SECONDS = 60.0  # used when Google's 429 gives no retry time
BAD_KEY_COOLDOWN_SECONDS = 3600.0  # a rejected key is unlikely to recover quickly

try:  # google-genai's HTTP transport (httpx2) raises its own transport errors
    from httpx2 import TransportError as _HttpxTransportError
except ImportError:  # pragma: no cover
    _HttpxTransportError = ConnectionError  # type: ignore[assignment,misc]

try:
    from httpx import TransportError as _HttpxV1TransportError
except ImportError:  # pragma: no cover
    _HttpxV1TransportError = ConnectionError  # type: ignore[assignment,misc]


def _client_error(exc: BaseException) -> genai_errors.ClientError | None:
    """langchain-google-genai re-raises Google's ClientError, keeping it as the cause."""
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, genai_errors.ClientError):
            return current
        current = current.__cause__
    return None


def is_transient(exc: Exception) -> bool:
    """Network drops, provider 5xx and rate limits are worth retrying; bugs are not."""
    if isinstance(exc, IntegrationUnavailableError) and "is retired" in str(exc):
        return False  # a retired toolkit won't recover on retry: stop and spend nothing more
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
    client_error = _client_error(exc)
    if client_error is not None and client_error.code == 429:
        # Per-minute limits clear quickly; daily quotas (retry in hours) must not be retried.
        delay = quota_retry_seconds(client_error)
        return delay is None or delay <= 60
    return False


def quota_retry_seconds(exc: Exception) -> float | None:
    """Google's RetryInfo.retryDelay (e.g. '44333s') from a 429, if present."""
    source = _client_error(exc) or exc
    details = ((getattr(source, "details", None) or {}).get("error") or {}).get("details") or []
    for d in details:
        if isinstance(d, dict) and d.get("@type", "").endswith("RetryInfo"):
            raw = str(d.get("retryDelay", "")).rstrip("s")
            try:
                return float(raw)
            except ValueError:
                return None
    return None


def is_quota_exhausted(exc: Exception) -> bool:
    client_error = _client_error(exc)
    return client_error is not None and client_error.code == 429 and not is_transient(exc)


def _key_cooldown_seconds(exc: Exception) -> float | None:
    """How long to rest a key after this error, or None if another key would not help."""
    error = _client_error(exc)
    if error is None:
        return None  # 5xx and network failures are not caused by the key
    if error.code == 429:
        return quota_retry_seconds(error) or DEFAULT_QUOTA_COOLDOWN_SECONDS
    if error.code in (401, 403) or (error.code == 400 and "API_KEY_INVALID" in str(error)):
        return BAD_KEY_COOLDOWN_SECONDS
    return None


class FailoverChatModel(ChatGoogleGenerativeAI):
    """Gemini model that moves to the next API key when one reaches its limit.

    It overrides the model's generation methods, so `bind_tools`, structured output and Deep
    Agents keep working. A stream only switches keys before its first chunk, so a reply is never
    spliced from two attempts.
    """

    _pool: KeyPool = PrivateAttr()
    _build: Callable[[str], Any] = PrivateAttr()
    _keys: tuple[str, ...] = PrivateAttr()
    _delegates: dict[int, Any] = PrivateAttr(default_factory=dict)

    def attach_keys(
        self, keys: tuple[str, ...], pool: KeyPool, build: Callable[[str], Any]
    ) -> "FailoverChatModel":
        self._keys, self._pool, self._build = keys, pool, build
        return self

    def _delegate(self, index: int) -> Any:
        if index not in self._delegates:
            self._delegates[index] = self._build(self._keys[index])
        return self._delegates[index]

    def _should_fail_over(self, index: int, exc: Exception) -> bool:
        seconds = _key_cooldown_seconds(exc)
        if seconds is None:
            return False
        self._pool.cool_down(index, seconds)
        logger.warning("Gemini key #%d is unavailable; resting it for %.0fs", index + 1, seconds)
        return True

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        last: Exception | None = None
        for index in self._pool.order():
            try:
                result = self._delegate(index)._generate(messages, stop, run_manager, **kwargs)
            except Exception as exc:
                if not self._should_fail_over(index, exc):
                    raise
                last = exc
                continue
            self._pool.succeeded(index)
            return result  # type: ignore[no-any-return]
        raise last  # type: ignore[misc]

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        last: Exception | None = None
        for index in self._pool.order():
            try:
                result = await self._delegate(index)._agenerate(
                    messages, stop, run_manager, **kwargs
                )
            except Exception as exc:
                if not self._should_fail_over(index, exc):
                    raise
                last = exc
                continue
            self._pool.succeeded(index)
            return result  # type: ignore[no-any-return]
        raise last  # type: ignore[misc]

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        last: Exception | None = None
        for index in self._pool.order():
            started = False
            try:
                for chunk in self._delegate(index)._stream(messages, stop, run_manager, **kwargs):
                    started = True
                    yield chunk
            except Exception as exc:
                if started or not self._should_fail_over(index, exc):
                    raise
                last = exc
                continue
            self._pool.succeeded(index)
            return
        raise last  # type: ignore[misc]

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        last: Exception | None = None
        for index in self._pool.order():
            started = False
            try:
                stream = self._delegate(index)._astream(messages, stop, run_manager, **kwargs)
                async for chunk in stream:
                    started = True
                    yield chunk
            except Exception as exc:
                if started or not self._should_fail_over(index, exc):
                    raise
                last = exc
                continue
            self._pool.succeeded(index)
            return
        raise last  # type: ignore[misc]


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


def build_chat_model(  # type: ignore[no-untyped-def]
    settings: Settings,
    *,
    temperature: float | None = None,
    thinking_level: str | None = None,
    timeout_seconds: float = MODEL_TIMEOUT_SECONDS,
):
    """The single place models are created (LangChain owns model integration).

    SDK retries are off: `call_with_retry` handles transient errors and stops immediately on
    an exhausted quota, whereas the SDK default would silently back off for minutes.
    """
    keys = settings.google_api_keys
    if not keys:
        raise ConfigurationError("GOOGLE_API_KEY is not configured")
    kwargs: dict[str, Any] = {"max_retries": 1, "timeout": timeout_seconds}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if thinking_level:
        kwargs["thinking_level"] = thinking_level

    def build(key: str) -> ChatGoogleGenerativeAI:
        return ChatGoogleGenerativeAI(model=settings.llm_model, google_api_key=key, **kwargs)

    model = FailoverChatModel(model=settings.llm_model, google_api_key=keys[0], **kwargs)
    return model.attach_keys(keys, shared_key_pool(keys), build)

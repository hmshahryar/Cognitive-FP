"""Gemini moves to the next API key at a limit, and only when another key can help."""

from pathlib import Path
from typing import Any

import pytest
from google.genai import errors
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_google_genai.chat_models import GoogleAPIError, GoogleRateLimitError
from pydantic import BaseModel

from cognitive_mesh.agents.key_pool import KeyPool
from cognitive_mesh.agents.llm import (
    FailoverChatModel,
    build_chat_model,
    is_quota_exhausted,
    is_transient,
    quota_retry_seconds,
)
from cognitive_mesh.config import Settings


def client_error(code: int, retry: str | None = None, message: str = "limit") -> errors.ClientError:
    body: dict[str, Any] = {"error": {"message": message, "details": []}}
    if retry:
        body["error"]["details"] = [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry}
        ]
    return errors.ClientError(code, body)


def wrapped_429(retry: str | None = "44333s") -> GoogleRateLimitError:
    error = GoogleRateLimitError("rate limited")
    error.__cause__ = client_error(429, retry)
    return error


class FakeDelegate:
    def __init__(self, name: str, failure: Exception | None, log: list[str]) -> None:
        self.name, self.failure, self.log = name, failure, log
        self.kwargs: dict[str, Any] = {}

    async def _agenerate(self, _messages: Any, _stop: Any, _manager: Any, **kwargs: Any) -> ChatResult:
        self.log.append(self.name)
        self.kwargs = kwargs
        if self.failure:
            raise self.failure
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.name))])

    async def _astream(self, _messages: Any, _stop: Any, _manager: Any, **_kwargs: Any) -> Any:
        self.log.append(self.name)
        if self.failure:
            raise self.failure
        yield ChatGenerationChunk(message=AIMessageChunk(content=self.name))


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


def model_with(
    failures: dict[str, Exception | None], clock: Clock | None = None
) -> tuple[FailoverChatModel, list[str], dict[str, FakeDelegate], KeyPool]:
    log: list[str] = []
    delegates = {key: FakeDelegate(key, failure, log) for key, failure in failures.items()}
    keys = tuple(failures)
    pool = KeyPool(len(keys), clock or Clock())
    model = FailoverChatModel(model="gemini-test", google_api_key=keys[0])
    return model.attach_keys(keys, pool, delegates.__getitem__), log, delegates, pool


async def test_limit_on_first_key_moves_to_second_and_stays_there() -> None:
    model, log, delegates, _pool = model_with({"a": wrapped_429(), "b": None, "c": None})
    assert (await model.ainvoke("hi")).content == "b"
    delegates["a"].failure = None  # still resting, so it must not be tried
    assert (await model.ainvoke("hi")).content == "b"
    assert log == ["a", "b", "b"]


async def test_key_returns_after_its_cooldown() -> None:
    clock = Clock()
    model, log, delegates, _pool = model_with({"a": wrapped_429("30s"), "b": wrapped_429("90s")}, clock)
    with pytest.raises(GoogleRateLimitError):
        await model.ainvoke("hi")
    delegates["a"].failure = None
    clock.now += 31
    assert (await model.ainvoke("hi")).content == "a"
    assert log == ["a", "b", "a"]


async def test_all_keys_limited_raises_the_real_quota_error() -> None:
    model, log, _delegates, _pool = model_with({"a": wrapped_429(), "b": wrapped_429()})
    with pytest.raises(GoogleRateLimitError) as caught:
        await model.ainvoke("hi")
    assert log == ["a", "b"]
    assert is_quota_exhausted(caught.value) and quota_retry_seconds(caught.value) == 44333.0


async def test_server_error_does_not_use_another_key() -> None:
    failure = GoogleAPIError(504, {"error": {"message": "timeout"}})
    model, log, _delegates, _pool = model_with({"a": failure, "b": None})
    with pytest.raises(GoogleAPIError):
        await model.ainvoke("hi")
    assert log == ["a"]


async def test_bad_request_does_not_use_another_key() -> None:
    failure = RuntimeError("bad")
    failure.__cause__ = client_error(400, message="invalid schema")
    model, log, _delegates, _pool = model_with({"a": failure, "b": None})
    with pytest.raises(RuntimeError):
        await model.ainvoke("hi")
    assert log == ["a"]


async def test_rejected_key_is_skipped() -> None:
    failure = RuntimeError("bad key")
    failure.__cause__ = client_error(400, message="API_KEY_INVALID")
    model, _log, _delegates, _pool = model_with({"a": failure, "b": None})
    assert (await model.ainvoke("hi")).content == "b"


async def test_bound_request_settings_reach_the_fallback_key() -> None:
    model, _log, delegates, _pool = model_with({"a": wrapped_429(), "b": None})
    await model.bind(tool_choice="any").ainvoke("hi")
    assert delegates["b"].kwargs["tool_choice"] == "any"


async def test_structured_output_still_binds() -> None:
    class Answer(BaseModel):
        text: str

    model, _log, _delegates, _pool = model_with({"a": None})
    assert model.with_structured_output(Answer, method="json_schema") is not None


async def test_stream_switches_key_before_first_chunk() -> None:
    model, log, _delegates, _pool = model_with({"a": wrapped_429(), "b": None})
    chunks = [chunk.content async for chunk in model.astream("hi")]
    assert chunks[0] == "b" and log == ["a", "b"]


def test_wrapped_minute_limit_is_retryable_and_daily_is_not() -> None:
    assert is_transient(wrapped_429("30s")) and not is_quota_exhausted(wrapped_429("30s"))
    assert not is_transient(wrapped_429("44333s")) and is_quota_exhausted(wrapped_429("44333s"))


def test_numbered_keys_load_in_order_without_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("GOOGLE_API_KEY", "GOOGLE_API_KEY1", "GOOGLE_API_KEY2", "GOOGLE_API_KEY10"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "GOOGLE_API_KEY=base\nGOOGLE_API_KEY10=ten\nGOOGLE_API_KEY2=two\n"
        "GOOGLE_API_KEY1=base\nGOOGLE_API_KEY3=\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=env)
    assert settings.google_api_keys == ("base", "two", "ten")
    assert "two" not in repr(settings)


def test_real_environment_overrides_dotenv_for_numbered_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY1", "from-env")
    env = tmp_path / ".env"
    env.write_text("GOOGLE_API_KEY=base\nGOOGLE_API_KEY1=from-file\n", encoding="utf-8")
    assert Settings(_env_file=env).google_api_keys == ("base", "from-env")


def test_only_numbered_keys_is_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    settings = Settings(_env_file=None, google_api_key="", google_api_key_numbered=("one",))
    assert isinstance(build_chat_model(settings), FailoverChatModel)

"""Workflow guarantees, independent of any model."""

import pytest

from cognitive_mesh.agents.errors import CitationError, ResearchFailedError
from cognitive_mesh.agents.schemas import PostDraft
from cognitive_mesh.integrations.gateway import READ_ONLY_TOOLS
from cognitive_mesh.research.providers import (
    parse_composio_news,
    parse_composio_web,
    parse_grounding,
)
from cognitive_mesh.research.sources import SearchHit, SourceRegistry
from cognitive_mesh.workflows.content_pipeline import (
    ContentPipeline,
    draft_problems,
    full_post_text,
)
from tests.fakes_ai import FakeTeam, draft, verdict


async def test_happy_path_produces_one_accepted_draft() -> None:
    team = FakeTeam()
    result = await team.pipeline().run("Post about agentic AI", profile="AI engineer")
    assert result.outcome == "drafted"
    assert [r.version for r in result.rounds] == [1]
    assert result.final and result.final.accepted
    assert {s.id for s in result.sources} == {"S1", "S2"}
    assert team.profiles == ["AI engineer"]


async def test_unsupported_command_stops_before_research() -> None:
    team = FakeTeam(action="unsupported")
    result = await team.pipeline().run("Book me a flight", profile="x")
    assert result.outcome == "unsupported" and result.rounds == []
    assert team.profiles == []  # researcher never ran


async def test_review_feedback_drives_revisions_until_approved() -> None:
    team = FakeTeam(
        drafts=[draft("v1"), draft("v2")],
        verdicts=[verdict(False, ["Add a concrete example"]), verdict(True)],
    )
    result = await team.pipeline().run("x", profile="p")
    assert [r.version for r in result.rounds] == [1, 2]
    assert team.writer_feedback == [[], ["Add a concrete example"]]
    assert result.final and result.final.accepted and result.final.draft.text == "v2"


async def test_revisions_are_capped() -> None:
    team = FakeTeam(verdicts=[verdict(False, ["better"])])
    result = await team.pipeline(max_rounds=2).run("x", profile="p")
    assert len(result.rounds) == 3  # first draft + 2 revisions
    assert result.final and not result.final.accepted  # shown with reviewer notes


async def test_invented_citation_is_sent_back_then_fixed() -> None:
    team = FakeTeam(drafts=[draft(sources=["S9"]), draft(sources=["S2"])])
    result = await team.pipeline().run("x", profile="p")
    assert "S9" in team.writer_feedback[1][0]  # writer told exactly what was wrong
    assert result.final and result.final.accepted and result.final.draft.source_ids == ["S2"]


async def test_never_returns_a_draft_with_invented_citations() -> None:
    team = FakeTeam(drafts=[draft(sources=["S42"])])
    with pytest.raises(CitationError):
        await team.pipeline().run("x", profile="p")


async def test_research_without_verifiable_citations_fails() -> None:
    team = FakeTeam(insight_ids=["S77"])
    with pytest.raises(ResearchFailedError):
        await team.pipeline().run("x", profile="p")


def test_length_limit_includes_hashtags() -> None:
    reg = SourceRegistry()
    reg.add(SearchHit("https://a", "A"), "t")
    body = "x" * 2990
    d = PostDraft(text=body, hashtags=["AgenticAI"], source_ids=["S1"])
    assert len(full_post_text(d)) > 3000
    assert any("3000" in p for p in draft_problems(d, reg))


def test_registry_dedupes_and_flags_unknown_ids() -> None:
    reg = SourceRegistry()
    a = reg.add(SearchHit("https://a", "A"), "t")
    again = reg.add(SearchHit("https://a", "A again"), "t")
    assert a.id == again.id == "S1" and len(reg) == 1
    assert reg.unknown(["S1", "S2"]) == ["S2"]


def test_publish_tools_are_not_in_the_read_only_allowlist() -> None:
    assert "LINKEDIN_CREATE_LINKED_IN_POST" not in READ_ONLY_TOOLS
    assert not any(t.startswith(("LINKEDIN_CREATE", "LINKEDIN_DELETE")) for t in READ_ONLY_TOOLS)


# ---------- parsers, using the response shapes recorded from the live APIs ----------
def test_parse_composio_news() -> None:
    data = {
        "has_results": True,
        "news_results": [
            {"date": "2 days ago", "link": "https://n.example/1", "published_at": "2026-11-16 16:43:41 UTC",
             "snippet": "s", "source": "Cybersecurity Dive", "title": "T"},
            {"title": "no link"},
        ],
    }
    hits = parse_composio_news(data)
    assert len(hits) == 1
    assert hits[0].url == "https://n.example/1" and hits[0].publisher == "Cybersecurity Dive"
    assert hits[0].published_at == "2026-11-16 16:43:41 UTC"


def test_parse_composio_web() -> None:
    data = {
        "answer": "Agents [1]",
        "citations": [{"author": "J", "id": "x", "publishedDate": "2026-11-18T17:00:00.000Z",
                       "title": "Stitch Loop", "url": "https://blog.example/s"}],
    }
    answer = parse_composio_web(data)
    assert answer.answer == "Agents [1]" and answer.hits[0].title == "Stitch Loop"


def test_parse_gemini_grounding() -> None:
    meta = {"grounding_metadata": {"grounding_chunks": [{"web": {"uri": "https://g/1", "title": "g.com"}}, {}]}}
    hits = parse_grounding(meta)
    assert [h.url for h in hits] == ["https://g/1"]


# ---------- transient-error retries ----------
async def test_transient_errors_are_retried_then_succeed() -> None:
    from cognitive_mesh.agents.llm import call_with_retry

    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("server disconnected")
        return "ok"

    assert await call_with_retry(flaky, base_delay=0) == "ok" and calls == 3


async def test_bugs_are_not_retried() -> None:
    from cognitive_mesh.agents.llm import call_with_retry

    calls = 0

    async def broken() -> str:
        nonlocal calls
        calls += 1
        raise ValueError("bad schema")

    with pytest.raises(ValueError):
        await call_with_retry(broken, base_delay=0)
    assert calls == 1


def test_rate_limits_count_as_transient() -> None:
    from google.genai import errors

    from cognitive_mesh.agents.llm import is_transient

    assert is_transient(errors.ClientError(429, {"error": {"message": "quota"}}))
    assert not is_transient(errors.ClientError(400, {"error": {"message": "bad"}}))


# ---------- research budget & reviewer input ----------
async def test_research_search_budget_is_enforced() -> None:
    from cognitive_mesh.agents.employees import BUDGET_REACHED, research_tools
    from cognitive_mesh.research.providers import WebAnswer

    class Provider:
        name = "fake"
        calls = 0

        async def search_news(self, query, recency="w"):  # type: ignore[no-untyped-def]
            Provider.calls += 1
            return [SearchHit(f"https://n/{Provider.calls}-{i}", "t") for i in range(9)]

        async def search_web(self, query):  # type: ignore[no-untyped-def]
            Provider.calls += 1
            return WebAnswer("a", [SearchHit(f"https://w/{Provider.calls}", "t")])

    reg = SourceRegistry()
    news, web = research_tools(Provider(), reg, max_searches=3)
    await news.ainvoke({"query": "a"})
    await web.ainvoke({"query": "b"})
    await news.ainvoke({"query": "c"})
    assert await news.ainvoke({"query": "d"}) == BUDGET_REACHED
    assert await web.ainvoke({"query": "e"}) == BUDGET_REACHED
    assert Provider.calls == 3
    assert len(reg) == 5 + 1 + 5  # at most 5 hits kept per search


async def test_reviewer_sees_exactly_the_publishable_text() -> None:
    from cognitive_mesh.agents.employees import ReviewEmployee
    from cognitive_mesh.agents.schemas import CommandIntent, ResearchFindings

    seen: list = []

    class StubModel:
        def with_structured_output(self, schema, method):  # type: ignore[no-untyped-def]
            class Runner:
                async def ainvoke(self, messages):  # type: ignore[no-untyped-def]
                    seen.extend(messages)
                    return verdict()
            return Runner()

    reg = SourceRegistry()
    reg.add(SearchHit("https://a", "A"), "t")
    d = PostDraft(text="Body text.", hashtags=["AgenticAI", "#AI"], source_ids=["S1"])
    findings = ResearchFindings(summary="s", insights=[{"claim": "c", "source_ids": ["S1"]}])
    await ReviewEmployee(StubModel()).review(CommandIntent(action="linkedin_post", topic="t"), d, findings, reg)  # type: ignore[arg-type]
    user_msg = seen[-1][1]
    assert "Body text.\n\n#AgenticAI #AI" in user_msg
    assert "Hashtags:" not in user_msg


def test_full_post_text_normalizes_hashtags() -> None:
    d = PostDraft(text="Hi ", hashtags=["#AI", "Agents", " "], source_ids=["S1"])
    assert full_post_text(d) == "Hi\n\n#AI #Agents"


def _quota_error(retry: str):  # type: ignore[no-untyped-def]
    from google.genai import errors

    body = {"error": {"code": 429, "message": "quota", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry}]}}
    return errors.ClientError(429, body)


def test_daily_quota_is_not_retried_but_per_minute_limit_is() -> None:
    from cognitive_mesh.agents.llm import is_quota_exhausted, is_transient, quota_retry_seconds

    daily, minute = _quota_error("44333s"), _quota_error("20s")
    assert quota_retry_seconds(daily) == 44333.0
    assert not is_transient(daily) and is_quota_exhausted(daily)
    assert is_transient(minute) and not is_quota_exhausted(minute)


def test_quota_message_explains_reset_and_billing() -> None:
    from cognitive_mesh.services.runner import _user_facing_error

    try:
        try:
            raise _quota_error("44333s")
        except Exception as inner:
            raise RuntimeError("agent failed") from inner
    except RuntimeError as wrapped:
        msg = _user_facing_error(wrapped)
    assert "daily free-tier limit" in msg and "12.3 hours" in msg and "billing" in msg


# ---------- research mode: Research employee only on explicit request ----------
async def test_default_command_uses_quick_search_not_the_research_employee() -> None:
    team = FakeTeam()
    events: list = []

    async def sink(e, d):  # type: ignore[no-untyped-def]
        events.append((e, d))

    await team.pipeline().run("Write a LinkedIn post about agentic AI", profile="p", on_event=sink)
    assert team.research_modes == ["quick"]
    assert ("research.mode", {"mode": "quick"}) in events


async def test_explicit_research_request_uses_the_research_employee() -> None:
    team = FakeTeam(explicit_research=True)
    await team.pipeline().run("Research agentic AI in depth and write a post", profile="p")
    assert team.research_modes == ["deep"]


async def test_at_research_mention_always_selects_deep_research() -> None:
    team = FakeTeam(explicit_research=False)  # even if the model missed it
    await team.pipeline().run("@research agentic AI then post on LinkedIn", profile="p")
    assert team.research_modes == ["deep"]


def test_at_research_mention_needs_a_real_mention() -> None:
    from cognitive_mesh.agents.schemas import CommandIntent
    from cognitive_mesh.research.quick import wants_deep_research

    plain = CommandIntent(action="linkedin_post", topic="t")
    assert wants_deep_research("please @research this", plain)
    assert not wants_deep_research("email me at me@research.org", plain)
    assert not wants_deep_research("write a post", plain)


async def test_quick_researcher_cites_only_returned_sources_without_model_calls() -> None:
    from cognitive_mesh.agents.schemas import CommandIntent
    from cognitive_mesh.research.providers import WebAnswer
    from cognitive_mesh.research.quick import QuickResearcher

    class Provider:
        name = "fake"

        async def search_news(self, query, recency="w"):  # type: ignore[no-untyped-def]
            assert recency == "w"
            return [SearchHit(f"https://n/{i}", f"News {i}", "Pub", snippet="s") for i in range(7)]

        async def search_web(self, query):  # type: ignore[no-untyped-def]
            return WebAnswer("Agents are everywhere [1][2].", [SearchHit("https://w/1", "W1"), SearchHit("https://n/0", "dupe")])

    reg = SourceRegistry()
    findings = await QuickResearcher(Provider()).research(CommandIntent(action="linkedin_post", topic="agentic AI"), "p", reg)
    assert len(reg) == 6  # 5 news (capped) + 1 new web source; duplicate URL merged
    assert all(not reg.unknown(i.source_ids) for i in findings.insights)
    assert findings.summary == "Agents are everywhere."  # [n] markers removed


async def test_quick_search_with_no_results_fails_cleanly() -> None:
    from cognitive_mesh.research.providers import WebAnswer
    from cognitive_mesh.research.quick import QuickResearcher

    class Empty:
        name = "fake"

        async def search_news(self, query, recency="w"):  # type: ignore[no-untyped-def]
            return []

        async def search_web(self, query):  # type: ignore[no-untyped-def]
            return WebAnswer("", [])

    team = FakeTeam()
    pipeline = ContentPipeline(
        intent=team, quick_researcher=QuickResearcher(Empty()), deep_researcher=team,
        writer=team, reviewer=team,
    )
    with pytest.raises(ResearchFailedError):
        await pipeline.run("post about x", profile="p")

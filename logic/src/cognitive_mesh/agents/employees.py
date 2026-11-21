"""The AI employees used in the content workflow.

Each employee is a small class behind a Protocol, so the workflow can be tested with
fakes and a model/provider can be swapped without touching the others.

- IntentAnalyst  (LangChain structured output): what does the user want?
- ResearchEmployee (Deep Agents): plans and runs searches, returns cited findings.
- ContentEmployee (LangChain structured output): writes the post from findings only.
- ReviewEmployee (LangChain structured output): strict editorial verdict.
No employee has a write/publish tool.
"""

import json
from typing import Any, Literal, Protocol

from deepagents import create_deep_agent
from langchain.agents.middleware import ModelRetryMiddleware, ToolRetryMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool

from cognitive_mesh.agents import prompts
from cognitive_mesh.agents.errors import ResearchFailedError
from cognitive_mesh.agents.llm import call_with_retry, is_transient
from cognitive_mesh.agents.schemas import (
    LINKEDIN_MAX_CHARS,
    CommandIntent,
    PostDraft,
    ResearchFindings,
    ReviewVerdict,
    full_post_text,
)
from cognitive_mesh.research.providers import SearchProvider
from cognitive_mesh.research.sources import SourceRegistry


def _sources_block(registry: SourceRegistry) -> str:
    return "\n".join(
        f"[{s.id}] {s.title} — {s.publisher or 'unknown'} ({s.published_at or 'n.d.'}) {s.url}"
        + (f"\n     {s.snippet}" if s.snippet else "")
        for s in registry.all()
    )


def _pages_block(pages: dict[str, str]) -> str:
    """Fetched pages are untrusted: delimited, and unable to close their own delimiter."""
    return "\n".join(
        f'<untrusted_source id="{source_id}">\n'
        f"{text.replace('</untrusted_source', '< /untrusted_source')}\n</untrusted_source>"
        for source_id, text in pages.items()
    )


# ---------- protocols ----------
class IntentAnalyzer(Protocol):
    async def analyze(self, command: str) -> CommandIntent: ...


class Researcher(Protocol):
    async def research(
        self, intent: CommandIntent, profile: str, registry: SourceRegistry
    ) -> ResearchFindings: ...


class Writer(Protocol):
    async def write(
        self,
        intent: CommandIntent,
        profile: str,
        findings: ResearchFindings,
        registry: SourceRegistry,
        feedback: list[str],
        previous_text: str | None = None,
    ) -> PostDraft: ...


class Reviewer(Protocol):
    async def review(
        self,
        intent: CommandIntent,
        draft: PostDraft,
        findings: ResearchFindings,
        registry: SourceRegistry,
        pages: dict[str, str] | None = None,
    ) -> ReviewVerdict: ...


# ---------- implementations ----------
class IntentAnalyst:
    def __init__(self, model: BaseChatModel) -> None:
        self._llm = model.with_structured_output(CommandIntent, method="json_schema")

    async def analyze(self, command: str) -> CommandIntent:
        messages = [("system", prompts.INTENT), ("user", command)]
        return await call_with_retry(lambda: self._llm.ainvoke(messages))  # type: ignore[return-value]


MAX_SEARCHES = 6  # per research run: bounds latency, cost and source sprawl
HITS_PER_SEARCH = 5

BUDGET_REACHED = (
    "Search budget reached. Do not search again: write your ResearchFindings now "
    "from the sources you already have."
)


def research_tools(
    provider: SearchProvider, registry: SourceRegistry, max_searches: int = MAX_SEARCHES
) -> list[Any]:
    """Search tools that record every hit in the registry and return citable ids.

    A shared budget caps total searches; once spent, tools tell the agent to finish.
    """
    used = 0

    def _spend() -> bool:
        nonlocal used
        if used >= max_searches:
            return False
        used += 1
        return True

    @tool
    async def search_news(query: str, recency: Literal["d", "w", "m", "y"] = "w") -> str:
        """Search recent news. recency: d=past day, w=week, m=month, y=year."""
        if not _spend():
            return BUDGET_REACHED
        hits = await provider.search_news(query, recency)
        rows = [registry.add(h, provider.name) for h in hits[:HITS_PER_SEARCH]]
        return json.dumps([r.to_dict() for r in rows], default=str)

    @tool
    async def search_web(query: str) -> str:
        """Search the web; returns a synthesized answer and its cited sources."""
        if not _spend():
            return BUDGET_REACHED
        result = await provider.search_web(query)
        rows = [registry.add(h, provider.name) for h in result.hits[:HITS_PER_SEARCH]]
        return json.dumps(
            {"answer": result.answer, "sources": [r.to_dict() for r in rows]}, default=str
        )

    return [search_news, search_web]


class ResearchEmployee:
    """Deep Agent: plans its research (todo list) and runs multiple searches."""

    def __init__(self, model: BaseChatModel, provider: SearchProvider) -> None:
        self._model = model
        self._provider = provider

    async def research(
        self, intent: CommandIntent, profile: str, registry: SourceRegistry
    ) -> ResearchFindings:
        agent = create_deep_agent(
            model=self._model,
            tools=research_tools(self._provider, registry),
            system_prompt=prompts.RESEARCH.format(max_searches=MAX_SEARCHES),
            response_format=ResearchFindings,
            middleware=[
                ModelRetryMiddleware(max_retries=3, retry_on=is_transient, on_failure="error"),
                ToolRetryMiddleware(max_retries=2, retry_on=is_transient, on_failure="continue"),
            ],
            name="research_employee",
        )
        brief = (
            f"Topic: {intent.topic}\nConstraints: {intent.instructions or 'none'}\n"
            f"Audience/author profile: {profile}"
        )
        result = await agent.ainvoke(
            {"messages": [("user", brief)]}, config={"recursion_limit": 60}
        )
        findings = result.get("structured_response")
        if not isinstance(findings, ResearchFindings):
            raise ResearchFailedError("Research employee returned no structured findings")
        return findings


class ContentEmployee:
    def __init__(self, model: BaseChatModel) -> None:
        self._llm = model.with_structured_output(PostDraft, method="json_schema")

    async def write(
        self,
        intent: CommandIntent,
        profile: str,
        findings: ResearchFindings,
        registry: SourceRegistry,
        feedback: list[str],
        previous_text: str | None = None,
    ) -> PostDraft:
        findings_text = "\n".join(
            f"- {i.claim} [{', '.join(i.source_ids)}]" for i in findings.insights
        )
        has_research = len(registry) > 0
        user = (
            f"Request: {intent.topic}\nConstraints: {intent.instructions or 'none'}\n"
            f"Author profile: {profile}"
        )
        if has_research:
            user += (
                f"\n\nResearch summary: {findings.summary}\n"
                f"Insights:\n{findings_text}\n\nSources:\n{_sources_block(registry)}"
            )
        if previous_text:
            user += f"\n\nPrevious draft:\n<<<\n{previous_text}\n>>>"
        if feedback:
            user += (
                "\n\nRevise the previous draft. Keep what already works and fix these:\n- "
                + "\n- ".join(feedback)
            )
        messages = [
            (
                "system",
                prompts.CONTENT.format(
                    max_chars=LINKEDIN_MAX_CHARS,
                    fact_rule=(
                        prompts.FACTS_FROM_RESEARCH
                        if has_research
                        else prompts.FACTS_WITHOUT_RESEARCH
                    ),
                    citation_rule=prompts.CITE_RESEARCH if has_research else prompts.CITE_NOTHING,
                ),
            ),
            ("user", user),
        ]
        return await call_with_retry(lambda: self._llm.ainvoke(messages))  # type: ignore[return-value]


class ReviewEmployee:
    def __init__(self, model: BaseChatModel) -> None:
        self._llm = model.with_structured_output(ReviewVerdict, method="json_schema")

    async def review(
        self,
        intent: CommandIntent,
        draft: PostDraft,
        findings: ResearchFindings,
        registry: SourceRegistry,
        pages: dict[str, str] | None = None,
    ) -> ReviewVerdict:
        # Review exactly what would be published (body + formatted hashtags).
        claims = "\n".join(f"- {c.text} [{', '.join(c.source_ids)}]" for c in draft.claims)
        user = (
            f"Request: {intent.topic} ({intent.instructions or 'no constraints'})\n\n"
            f"Post exactly as it would be published (cites {', '.join(draft.source_ids)}):\n"
            f"<<<\n{full_post_text(draft)}\n>>>\n\n"
        )
        if len(registry) > 0:
            user += (
                f"Writer's claim list:\n{claims or '(none)'}\n\n"
                f"Research summary: {findings.summary}\nSources:\n{_sources_block(registry)}"
            )
        else:
            user += prompts.REVIEW_WITHOUT_RESEARCH
        if pages:
            user += f"\n\nFull text of cited sources:\n{_pages_block(pages)}"
        messages = [("system", prompts.REVIEW), ("user", user)]
        return await call_with_retry(lambda: self._llm.ainvoke(messages))  # type: ignore[return-value]

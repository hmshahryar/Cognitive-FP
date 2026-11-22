"""Deterministic stand-ins for the AI employees (no model or network calls)."""

from dataclasses import dataclass, field

from cognitive_mesh.agents.schemas import (
    CommandIntent,
    EmailDraft,
    Insight,
    ManagerDecision,
    PostDraft,
    ResearchFindings,
    ReviewVerdict,
)
from cognitive_mesh.research.sources import SearchHit, SourceRegistry
from cognitive_mesh.workflows.content_pipeline import ContentPipeline


def verdict(approved: bool = True, changes: list[str] | None = None) -> ReviewVerdict:
    return ReviewVerdict(
        approved=approved,
        accuracy=5 if approved else 2,
        relevance=5,
        quality=4,
        formatting=5,
        professionalism=4,
        readability=4,
        originality=4,
        repetition=5,
        natural_language=4,
        required_changes=changes or [],
    )


def draft(text: str = "Agentic AI is moving fast.", sources: list[str] | None = None) -> PostDraft:
    return PostDraft(text=text, hashtags=["AgenticAI"], source_ids=sources or ["S1"])


@dataclass
class FakeTeam:
    action: str = "linkedin_post"
    insight_ids: list[str] = field(default_factory=lambda: ["S1", "S2"])
    drafts: list[PostDraft] = field(default_factory=lambda: [draft()])
    verdicts: list[ReviewVerdict] = field(default_factory=lambda: [verdict()])
    research_error: Exception | None = None
    writer_feedback: list[list[str]] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    explicit_research: bool = False
    previous_texts: list[str | None] = field(default_factory=list)
    manager_calls: list[str] = field(default_factory=list)
    manager_contexts: list[str] = field(default_factory=list)
    manager_decision: ManagerDecision | None = None
    manager_error: Exception | None = None
    writer_contexts: list[str] = field(default_factory=list)
    summaries: list[dict] = field(default_factory=list)
    email_calls: list[dict] = field(default_factory=list)
    email_drafts: list[EmailDraft] = field(
        default_factory=lambda: [EmailDraft(subject="Project update", body="The work is on track.")]
    )
    review_calls: int = 0
    reviewed_pages: list[dict[str, str]] = field(default_factory=list)

    # --- employee protocols ---
    async def analyze(self, command: str) -> CommandIntent:
        return CommandIntent(action=self.action, topic=command, explicit_research=self.explicit_research)  # type: ignore[arg-type]

    async def research(self, intent, profile, registry: SourceRegistry) -> ResearchFindings:  # type: ignore[no-untyped-def]
        self.profiles.append(profile)
        if self.research_error:
            raise self.research_error
        registry.add(SearchHit("https://news.example/a", "Agents ship", "Example News", "2026-11-16"), "fake")
        registry.add(SearchHit("https://blog.example/b", "Agent frameworks", "Example Blog"), "fake")
        return ResearchFindings(
            summary="Agents are shipping.",
            insights=[Insight(claim="Agents ship widely.", source_ids=self.insight_ids)],
        )

    async def write(self, intent, profile, findings, registry, feedback, previous_text=None, context="") -> PostDraft:  # type: ignore[no-untyped-def]
        self.writer_feedback.append(list(feedback))
        self.previous_texts.append(previous_text)
        self.writer_contexts.append(context)
        return self.drafts[min(len(self.writer_feedback) - 1, len(self.drafts) - 1)]

    async def review(self, intent, d, findings, registry, pages=None) -> ReviewVerdict:  # type: ignore[no-untyped-def]
        self.review_calls += 1
        self.reviewed_pages.append(dict(pages or {}))
        i = len(self.writer_feedback) - 1
        return self.verdicts[min(i, len(self.verdicts) - 1)]

    async def write_email(self, request, feedback, previous_text, context="") -> EmailDraft:  # type: ignore[no-untyped-def]
        self.email_calls.append(
            {"request": request, "feedback": list(feedback), "previous": previous_text, "context": context}
        )
        return self.email_drafts[min(len(self.email_calls) - 1, len(self.email_drafts) - 1)]

    async def decide(self, message: str, context: str = "") -> ManagerDecision:
        self.manager_calls.append(message)
        self.manager_contexts.append(context)
        if self.manager_error:
            raise self.manager_error
        return self.manager_decision or ManagerDecision(target="unsupported")

    async def summarize(self, previous: str, transcript: str) -> str:
        self.summaries.append({"previous": previous, "transcript": transcript})
        return f"summary {len(self.summaries)}"

    def pipeline(self, max_rounds: int = 2, page_reader=None) -> ContentPipeline:  # type: ignore[no-untyped-def]
        return ContentPipeline(
            page_reader=page_reader,
            email_writer=self,
            summarizer=self,
            manager=self,
            intent=self,
            researcher=_Delegate(self),
            writer=self,
            reviewer=self,
            max_review_rounds=max_rounds,
        )


class _Delegate:
    """Late-binds `team.research` so tests can replace it after building the pipeline."""

    def __init__(self, team: "FakeTeam") -> None:
        self._team = team

    async def research(self, intent, profile, registry):  # type: ignore[no-untyped-def]
        return await self._team.research(intent, profile, registry)

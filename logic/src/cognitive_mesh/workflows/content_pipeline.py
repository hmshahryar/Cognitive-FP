"""Manager workflow for a LinkedIn post: understand -> research -> write <-> review.

Guarantees enforced in code (not left to the model):
- Every cited source id must exist in the run's SourceRegistry (no invented sources).
- The post must fit LinkedIn's 3000-character limit, hashtags included.
- At most `max_review_rounds` revisions; drafts with invented citations are never
  returned as final.
The stages (understand, research, write_and_review) are also called individually by
the LangGraph graph in workflows/command_graph, which adds checkpoints and approval.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from cognitive_mesh.agents.employees import IntentAnalyzer, Researcher, Reviewer, Writer
from cognitive_mesh.agents.errors import CitationError, ResearchFailedError
from cognitive_mesh.agents.quality import check_post, email_problems
from cognitive_mesh.agents.schemas import (
    LINKEDIN_MAX_CHARS,
    CommandIntent,
    EmailDraft,
    ManagerDecision,
    PostDraft,
    ResearchFindings,
    ReviewVerdict,
    full_post_text,
)
from cognitive_mesh.db.base import utcnow
from cognitive_mesh.integrations.gateway import IntegrationUnavailableError, ToolExecutionError
from cognitive_mesh.research.providers import PageReader
from cognitive_mesh.research.sources import Source, SourceRegistry
from cognitive_mesh.services.errors import ConfigurationError

EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]

MAX_PAGES_PER_DRAFT = 6
SCORE_FIELDS = {
    "accuracy", "relevance", "quality", "formatting", "professionalism",
    "readability", "originality", "repetition", "natural_language",
}


StopCheck = Callable[[], Awaitable[bool]]


class EmailWriter(Protocol):
    async def write_email(
        self, request: str, feedback: list[str], previous_text: str | None, context: str = ""
    ) -> EmailDraft: ...


class Summarizer(Protocol):
    async def summarize(self, previous: str, transcript: str) -> str: ...


class RouteManager(Protocol):
    async def decide(self, message: str) -> ManagerDecision: ...


class WorkflowStopped(Exception):
    """The task was cancelled, so no further model calls should be made."""


async def _noop(_event: str, _detail: dict[str, Any]) -> None:
    return None


async def _keep_going() -> bool:
    return False


def draft_problems(draft: PostDraft, registry: SourceRegistry) -> list[str]:
    problems = []
    cited = list(dict.fromkeys([*draft.source_ids, *(i for c in draft.claims for i in c.source_ids)]))
    unknown = registry.unknown(cited)
    if unknown:
        problems.append(
            f"Cites sources that research never returned: {', '.join(unknown)}. "
            "Cite only the provided source ids."
        )
    if len(registry) > 0 and not draft.source_ids:
        problems.append("Research was provided: cite the source ids this post relies on.")
    if len(full_post_text(draft)) > LINKEDIN_MAX_CHARS:
        problems.append(f"Post exceeds {LINKEDIN_MAX_CHARS} characters including hashtags.")
    return problems


def evidence_by_source(
    findings: ResearchFindings, registry: SourceRegistry, pages: dict[str, str]
) -> dict[str, str]:
    """Everything research actually returned for each source id."""
    evidence = {
        s.id: " ".join(p for p in (s.title, s.snippet, pages.get(s.id)) if p)
        for s in registry.all()
    }
    for insight in findings.insights:
        for source_id in insight.source_ids:
            if source_id in evidence:
                evidence[source_id] += f" {insight.claim}"
    return evidence


def claim_records(draft: PostDraft, pages: dict[str, str]) -> list[dict[str, Any]]:
    """Internal claim-to-source map, noting how much text each claim was checked against."""
    return [
        {
            "text": c.text,
            "source_ids": c.source_ids,
            "basis": "full_text" if any(i in pages for i in c.source_ids) else "snippet",
        }
        for c in draft.claims
    ]


@dataclass
class DraftRound:
    version: int
    draft: PostDraft
    verdict: ReviewVerdict | None  # None: blocked by code checks, so no model call was spent
    problems: list[str]
    style_notes: list[str] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    generated_at: datetime = field(default_factory=utcnow)

    @property
    def accepted(self) -> bool:
        return (
            self.verdict is not None
            and self.verdict.meets_bar()
            and not self.problems
            and not self.style_notes
        )


@dataclass
class PipelineResult:
    outcome: Literal["drafted", "unsupported"]
    intent: CommandIntent
    findings: ResearchFindings | None = None
    sources: list[Source] = field(default_factory=list)
    rounds: list[DraftRound] = field(default_factory=list)

    @property
    def final(self) -> DraftRound | None:
        return self.rounds[-1] if self.rounds else None


class ContentPipeline:
    def __init__(
        self,
        *,
        intent: IntentAnalyzer,
        researcher: Researcher,
        writer: Writer,
        reviewer: Reviewer,
        max_review_rounds: int = 2,
        page_reader: PageReader | None = None,
        email_writer: EmailWriter | None = None,
        summarizer: Summarizer | None = None,
        manager: RouteManager | None = None,
    ) -> None:
        self._manager = manager
        self._page_reader = page_reader
        self._email_writer = email_writer
        self._summarizer = summarizer
        self._intent = intent
        self._researcher = researcher
        self._writer = writer
        self._reviewer = reviewer
        self._max_rounds = max_review_rounds

    async def run(
        self, command: str, *, profile: str, on_event: EventSink = _noop
    ) -> PipelineResult:
        """All stages in one go (the LangGraph graph calls the stages separately)."""
        intent = await self.understand(command, on_event=on_event)
        if intent.action != "linkedin_post":
            return PipelineResult(outcome="unsupported", intent=intent)
        findings, registry = await self.research(intent, profile, on_event=on_event)
        rounds = await self.write_and_review(
            intent, profile, findings, registry, on_event=on_event
        )
        return PipelineResult(
            outcome="drafted",
            intent=intent,
            findings=findings,
            sources=registry.all(),
            rounds=rounds,
        )

    async def understand(self, command: str, *, on_event: EventSink = _noop) -> CommandIntent:
        intent = await self._intent.analyze(command)
        await on_event("manager.intent", intent.model_dump())
        return intent

    async def research(
        self,
        intent: CommandIntent,
        profile: str,
        *,
        on_event: EventSink = _noop,
    ) -> tuple[ResearchFindings, SourceRegistry]:
        """Runs only when the user explicitly asked for research (see command_router)."""
        registry = SourceRegistry()
        findings = await self._researcher.research(intent, profile, registry)
        # Drop insights whose citations aren't real; fail if nothing verifiable remains.
        verified = [i for i in findings.insights if not registry.unknown(i.source_ids)]
        if not verified:
            raise ResearchFailedError("Research returned no verifiable, cited findings")
        findings = findings.model_copy(update={"insights": verified})
        await on_event(
            "research.completed",
            {
                "insights": len(verified),
                "sources": len(registry),
                "summary": findings.summary,
            },
        )
        return findings, registry

    async def write_and_review(
        self,
        intent: CommandIntent,
        profile: str,
        findings: ResearchFindings,
        registry: SourceRegistry,
        *,
        feedback: list[str] | None = None,
        first_version: int = 1,
        previous_text: str | None = None,
        on_event: EventSink = _noop,
        should_stop: StopCheck = _keep_going,
        context: str = "",
        trusted_text: str = "",
    ) -> list[DraftRound]:
        """Write -> code checks -> AI review -> revise.

        `feedback` and `previous_text` seed the first draft on a user's change request.
        Only a draft with no blocking problem can be returned as final.
        """
        rounds: list[DraftRound] = []
        feedback = list(feedback or [])
        user_text = " ".join([intent.topic, intent.instructions, *feedback, trusted_text])
        pages: dict[str, str] = {}
        for version in range(first_version, first_version + self._max_rounds + 1):
            if await should_stop():
                raise WorkflowStopped
            draft = await self._writer.write(
                intent, profile, findings, registry, feedback, previous_text, context
            )
            if await should_stop():  # cancelled during writing: don't pay for a review
                raise WorkflowStopped
            problems = draft_problems(draft, registry)
            await self._read_cited_pages(draft, registry, pages)
            report = check_post(draft, evidence_by_source(findings, registry, pages), user_text)
            problems += report.blocking
            # A blocked draft must be rewritten anyway: don't spend a model call on it.
            verdict = (
                None
                if problems
                else await self._reviewer.review(intent, draft, findings, registry, pages)
            )
            current = DraftRound(
                version, draft, verdict, problems, report.style, claim_records(draft, pages)
            )
            rounds.append(current)
            await on_event(
                "review.completed",
                {
                    "version": version,
                    "approved": current.accepted,
                    "scores": verdict.model_dump(include=SCORE_FIELDS) if verdict else {},
                    "problems": problems,
                    "style_notes": report.style,
                },
            )
            if current.accepted:
                break
            reviewer_notes = (
                [*verdict.unsupported_claims, *(verdict.required_changes or verdict.issues)]
                if verdict
                else []
            )
            feedback = problems + report.style + reviewer_notes
            previous_text = full_post_text(draft)

        # Fall back to the latest clean version; never hand over a blocked draft.
        blocked: DraftRound | None = None
        while rounds and rounds[-1].problems:
            blocked = rounds.pop()
        if not rounds:
            assert blocked is not None
            raise CitationError("; ".join(blocked.problems))
        return rounds

    async def write_email(
        self,
        request: str,
        *,
        feedback: list[str],
        previous_text: str | None,
        should_stop: StopCheck = _keep_going,
        context: str = "",
        trusted_text: str = "",
    ) -> EmailDraft:
        """One draft, plus one rewrite only if a blocking check fails."""
        if self._email_writer is None:
            raise ConfigurationError("No email writer is configured")
        user_text = " ".join([request, *feedback, previous_text or "", trusted_text])
        notes = list(feedback)
        problems: list[str] = []
        for _attempt in range(2):
            if await should_stop():
                raise WorkflowStopped
            draft = await self._email_writer.write_email(request, notes, previous_text, context)
            problems = email_problems(draft.subject, draft.body, user_text)
            if not problems:
                return draft
            notes, previous_text = [*feedback, *problems], draft.body
        raise CitationError("; ".join(problems))

    async def decide_route(self, message: str) -> ManagerDecision:
        if self._manager is None:
            raise ConfigurationError("No manager is configured")
        return await self._manager.decide(message)

    async def summarize(self, previous: str, transcript: str) -> str:
        if self._summarizer is None:
            raise ConfigurationError("No summarizer is configured")
        return await self._summarizer.summarize(previous, transcript)

    async def _read_cited_pages(
        self, draft: PostDraft, registry: SourceRegistry, pages: dict[str, str]
    ) -> None:
        """Best effort: a page that can't be read leaves its claims marked snippet-only."""
        if self._page_reader is None:
            return
        wanted = [
            source
            for source_id in dict.fromkeys(
                [*draft.source_ids, *(i for c in draft.claims for i in c.source_ids)]
            )
            if source_id not in pages and (source := registry.get(source_id)) is not None
        ][:MAX_PAGES_PER_DRAFT]
        if not wanted:
            return
        try:
            texts = await self._page_reader.read([s.url for s in wanted])
        except (IntegrationUnavailableError, ToolExecutionError):
            return
        pages.update({s.id: texts[s.url] for s in wanted if s.url in texts})

"""Manager workflow for a LinkedIn post: understand -> research -> write <-> review.

Guarantees enforced in code (not left to the model):
- Every cited source id must exist in the run's SourceRegistry (no invented sources).
- The post must fit LinkedIn's 3000-character limit, hashtags included.
- At most `max_review_rounds` revisions; drafts with invented citations are never
  returned as final.
The stages (understand, research, write_and_review) are also called individually by
the LangGraph graph in workflows/linkedin_graph.py, which adds checkpoints and approval.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from cognitive_mesh.agents.employees import IntentAnalyzer, Researcher, Reviewer, Writer
from cognitive_mesh.agents.errors import CitationError, ResearchFailedError
from cognitive_mesh.agents.schemas import (
    LINKEDIN_MAX_CHARS,
    CommandIntent,
    PostDraft,
    ResearchFindings,
    ReviewVerdict,
    full_post_text,
)
from cognitive_mesh.research.quick import wants_deep_research
from cognitive_mesh.research.sources import Source, SourceRegistry

EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]


async def _noop(_event: str, _detail: dict[str, Any]) -> None:
    return None


def draft_problems(draft: PostDraft, registry: SourceRegistry) -> list[str]:
    problems = []
    unknown = registry.unknown(draft.source_ids)
    if unknown:
        problems.append(
            f"Cites sources that research never returned: {', '.join(unknown)}. "
            "Cite only the provided source ids."
        )
    if len(full_post_text(draft)) > LINKEDIN_MAX_CHARS:
        problems.append(f"Post exceeds {LINKEDIN_MAX_CHARS} characters including hashtags.")
    return problems


@dataclass
class DraftRound:
    version: int
    draft: PostDraft
    verdict: ReviewVerdict
    problems: list[str]

    @property
    def accepted(self) -> bool:
        return self.verdict.approved and not self.problems


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
        quick_researcher: Researcher,
        deep_researcher: Researcher,
        writer: Writer,
        reviewer: Reviewer,
        max_review_rounds: int = 2,
    ) -> None:
        self._intent = intent
        self._quick = quick_researcher
        self._deep = deep_researcher
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
        findings, registry = await self.research(command, intent, profile, on_event=on_event)
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
        command: str,
        intent: CommandIntent,
        profile: str,
        *,
        on_event: EventSink = _noop,
    ) -> tuple[ResearchFindings, SourceRegistry]:
        # The Research employee (Deep Agent) runs only when the user explicitly asks
        # for research; otherwise a quick direct search feeds the writer.
        mode = "deep" if wants_deep_research(command, intent) else "quick"
        await on_event("research.mode", {"mode": mode})
        researcher = self._deep if mode == "deep" else self._quick

        registry = SourceRegistry()
        findings = await researcher.research(intent, profile, registry)
        # Drop insights whose citations aren't real; fail if nothing verifiable remains.
        verified = [i for i in findings.insights if not registry.unknown(i.source_ids)]
        if not verified:
            raise ResearchFailedError("Research returned no verifiable, cited findings")
        findings = findings.model_copy(update={"insights": verified})
        await on_event(
            "research.completed",
            {
                "mode": mode,
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
        on_event: EventSink = _noop,
    ) -> list[DraftRound]:
        """Write <-> review loop. `feedback` seeds the first draft (user's change request)."""
        rounds: list[DraftRound] = []
        feedback = list(feedback or [])
        for version in range(first_version, first_version + self._max_rounds + 1):
            draft = await self._writer.write(intent, profile, findings, registry, feedback)
            problems = draft_problems(draft, registry)
            verdict = await self._reviewer.review(intent, draft, findings, registry)
            current = DraftRound(version, draft, verdict, problems)
            rounds.append(current)
            await on_event(
                "review.completed",
                {
                    "version": version,
                    "approved": current.accepted,
                    "scores": verdict.model_dump(
                        include={"accuracy", "relevance", "quality", "formatting"}
                    ),
                    "problems": problems,
                },
            )
            if current.accepted:
                break
            feedback = problems + (verdict.required_changes or verdict.issues)

        if rounds[-1].problems:
            # Never hand the user a post with invented sources or over the limit.
            raise CitationError("; ".join(rounds[-1].problems))
        return rounds

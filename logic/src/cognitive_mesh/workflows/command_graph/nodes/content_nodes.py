"""LinkedIn content path: exact text, optional research, writing and review."""

import uuid

from sqlalchemy import select

from cognitive_mesh.agents import prompts
from cognitive_mesh.agents.llm import LLM_PROVIDER
from cognitive_mesh.agents.schemas import CommandIntent, ResearchFindings, full_post_text
from cognitive_mesh.models import Chat, ContentDraft, MessageRole, Task, TaskStatus
from cognitive_mesh.research.sources import SourceRegistry
from cognitive_mesh.services import audit
from cognitive_mesh.services.chats import add_message
from cognitive_mesh.workflows.command_graph.context import NodeContext, NodeFunction
from cognitive_mesh.workflows.command_graph.state import NO_RESEARCH, GraphState
from cognitive_mesh.workflows.content_pipeline import DraftRound, WorkflowStopped


def build_content_nodes(ctx: NodeContext) -> dict[str, NodeFunction]:
    db, deps = ctx.db, ctx.deps
    is_current = ctx.is_current
    sink = ctx.sink
    finish_with_reply = ctx.finish_with_reply
    ai_context = ctx.ai_context

    async def use_provided_text(state: GraphState) -> GraphState:
        """The user's exact text becomes the draft. No model or search is involved."""
        if not await is_current(state, TaskStatus.RUNNING):
            return {"outcome": "cancelled"}
        execution_id = uuid.UUID(state["execution_id"])
        async with db.sessionmaker() as s:
            saved = await s.scalar(
                select(ContentDraft).where(
                    ContentDraft.execution_id == execution_id, ContentDraft.is_final.is_(True)
                )
            )
            if saved is None:
                task = await s.get(Task, uuid.UUID(state["task_id"]))
                assert task is not None
                saved = ContentDraft(
                    workspace_id=task.workspace_id, task_id=task.id, execution_id=execution_id,
                    version=1, channel="linkedin", text=state["provided_text"],
                    hashtags=[], source_ids=[], sources=[],
                    review={"issues": [], "required_changes": []},
                    accepted=False, is_final=True, model="none", provider="user",
                    prompt_version="none", claims=[], quality={},
                    research={"mode": "post_as_is"},
                )
                s.add(saved)
                chat = await s.get(Chat, task.chat_id)
                add_message(
                    s, chat, role=MessageRole.ASSISTANT, task_id=task.id,  # type: ignore[arg-type]
                    content="Your exact post is ready for approval. No AI changed it.",
                )
                audit.record(
                    s, workspace_id=task.workspace_id, event="draft.ready", actor="manager",
                    task_id=task.id, execution_id=execution_id,
                    detail={"version": 1, "source": "user_text"},
                )
                await s.commit()
        return {
            "draft_text": saved.text,
            "draft_id": str(saved.id),
            "next_version": 2,
            "feedback": [],
            "outcome": "",
        }

    async def research(state: GraphState) -> GraphState:
        if not await is_current(state, TaskStatus.RUNNING):
            return {"outcome": "cancelled"}
        pipeline = deps.build_pipeline(state["user_id"])
        intent = CommandIntent(**state["intent"])
        mode = state["mode"]
        if mode == "model":  # unclear wording: the only path that spends a classifying call
            intent = await pipeline.understand(state["command"], on_event=sink(state))
            if intent.action != "linkedin_post":
                return await finish_with_reply(
                    state,
                    "Right now I can post your text, write a post, or research and write one "
                    "for LinkedIn.",
                )
            mode = "research" if intent.explicit_research else "write"
        if mode == "write":
            return {"mode": mode, "intent": intent.model_dump(), "outcome": ""}
        findings, registry = await pipeline.research(
            intent, state["profile"], on_event=sink(state)
        )
        return {
            "mode": mode,
            "intent": intent.model_dump(),
            "findings": findings.model_dump(),
            "sources": registry.to_dicts(),
            "feedback": [],
            "next_version": 1,
            "outcome": "",
        }

    async def write(state: GraphState) -> GraphState:
        if not await is_current(state, TaskStatus.RUNNING):
            return {"outcome": "cancelled"}
        execution_id = uuid.UUID(state["execution_id"])
        first = state.get("next_version", 1)

        # Idempotency: if this cycle's drafts were already saved (crash after saving,
        # before the checkpoint), reuse them instead of generating new ones.
        async with db.sessionmaker() as s:
            saved = await s.scalar(
                select(ContentDraft).where(
                    ContentDraft.execution_id == execution_id,
                    ContentDraft.version >= first,
                    ContentDraft.is_final.is_(True),
                )
            )
        if saved is None:
            pipeline = deps.build_pipeline(state["user_id"])
            registry = SourceRegistry.from_dicts(state["sources"])
            async def cancelled() -> bool:
                return not await is_current(state, TaskStatus.RUNNING)

            memory_context = await ai_context(state, state["command"])
            try:
                rounds = await pipeline.write_and_review(
                CommandIntent(**state["intent"]),
                state["profile"],
                (
                    ResearchFindings(**state["findings"])
                    if state["findings"].get("insights")
                    else ResearchFindings.model_construct(**NO_RESEARCH)
                ),
                registry,
                feedback=state.get("feedback") or [],
                first_version=first,
                # A user's change request revises the draft they actually saw.
                previous_text=state.get("draft_text") if state.get("feedback") else None,
                on_event=sink(state),
                should_stop=cancelled,
                context=memory_context.text,
                trusted_text=memory_context.trusted_text,
                )
            except WorkflowStopped:
                return {"outcome": "cancelled"}
            if await cancelled():  # cancelled during the final review: keep nothing
                return {"outcome": "cancelled"}
            saved = await _save_rounds(state, rounds, registry)
        return {
            "draft_text": saved.text,
            "draft_id": str(saved.id),
            "next_version": saved.version + 1,
            "feedback": [],
            "outcome": "",
        }

    async def _save_rounds(
        state: GraphState, rounds: list[DraftRound], registry: SourceRegistry
    ) -> ContentDraft:
        sources = registry.to_dicts()
        final_round = rounds[-1]
        async with db.sessionmaker() as s:
            task = await s.get(Task, uuid.UUID(state["task_id"]))
            assert task is not None
            final: ContentDraft | None = None
            for rnd in rounds:
                row = ContentDraft(
                    workspace_id=task.workspace_id, task_id=task.id,
                    execution_id=uuid.UUID(state["execution_id"]), version=rnd.version,
                    channel="linkedin", text=full_post_text(rnd.draft),
                    hashtags=rnd.draft.hashtags, source_ids=rnd.draft.source_ids,
                    sources=[x for x in sources if x["id"] in rnd.draft.source_ids],
                    review={
                        **(
                            rnd.verdict.model_dump()
                            if rnd.verdict
                            else {"approved": False, "issues": [],
                                  "required_changes": rnd.problems}
                        ),
                        "problems": rnd.problems,
                    },
                    accepted=rnd.accepted, is_final=rnd is final_round,
                    model=deps.settings.llm_model,
                    provider=LLM_PROVIDER,
                    prompt_version=prompts.PROMPT_VERSION,
                    claims=rnd.claims,
                    quality={"blocking": rnd.problems, "style": rnd.style_notes},
                    research={
                        "mode": "research" if sources else "write",
                        "summary": state["findings"]["summary"],
                        "insights": state["findings"]["insights"],
                    },
                    created_at=rnd.generated_at,
                )
                s.add(row)
                final = row
            cited = [x for x in sources if x["id"] in final_round.draft.source_ids]
            listing = "\n".join(f"[{x['id']}] {x['title']} — {x['url']}" for x in cited)
            references = f"\n\nSources:\n{listing}" if listing else "\n\nNo web research used."
            note = "" if final_round.accepted else "\n\n(The reviewer still had notes.)"
            chat = await s.get(Chat, task.chat_id)
            add_message(
                s, chat, role=MessageRole.ASSISTANT, task_id=task.id,  # type: ignore[arg-type]
                content=f"Draft v{final_round.version} is ready for your approval:\n\n"
                f"{full_post_text(final_round.draft)}{references}{note}",
            )
            audit.record(
                s, workspace_id=task.workspace_id, event="draft.ready", actor="manager",
                task_id=task.id, execution_id=task.current_execution_id,
                detail={"version": final_round.version, "accepted": final_round.accepted},
            )
            await s.commit()
            assert final is not None
            return final

    return {"use_provided_text": use_provided_text, "research": research, "write": write}

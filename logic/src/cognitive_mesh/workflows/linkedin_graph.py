"""LangGraph orchestration for "write and publish a LinkedIn post", with human approval.

    prepare --(post as-is)--> use_provided_text ------+
       |----(write)-----------------+                 |
       +----(research/unclear)-> research -> write -> request_approval --interrupt()--> decide
                                               ^                                          |
                                               +------------ changes requested -----------+
    prepare routes the command with plain code, so each path uses only what it needs.
    decide: APPROVED -> publish (verified, at most once) -> end | REJECTED -> end
            INVALIDATED -> fail | still PENDING -> request_approval (pause again)

Rules:
- One graph thread per execution (thread_id = execution.thread_id); checkpoints are
  written with durability="sync" by the runner, in their own SQLite file.
- Nodes re-run on resume and after crashes, so each one is idempotent.
- The decision is ALWAYS read from the approvals table. The resume value is only a
  wake-up signal and is never trusted.
- App records (tasks, drafts, approvals, audit) stay the source of truth.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select, update

from cognitive_mesh.agents import prompts
from cognitive_mesh.agents.llm import LLM_PROVIDER
from cognitive_mesh.agents.schemas import CommandIntent, ResearchFindings, full_post_text
from cognitive_mesh.config import Settings
from cognitive_mesh.db import Database
from cognitive_mesh.integrations import linkedin
from cognitive_mesh.integrations.gateway import (
    ActionRejectedError,
    IntegrationGateway,
    IntegrationUnavailableError,
    ToolExecutionError,
)
from cognitive_mesh.models import (
    ApprovalStatus,
    Chat,
    ContentDraft,
    Execution,
    MessageRole,
    PublicationStatus,
    Task,
    TaskStatus,
)
from cognitive_mesh.research.sources import SourceRegistry
from cognitive_mesh.security.approval_signing import ApprovalSigner
from cognitive_mesh.services import approvals as approval_svc
from cognitive_mesh.services import audit, publishing
from cognitive_mesh.services.chats import add_message
from cognitive_mesh.services.connectors import get_active_connection
from cognitive_mesh.services.errors import ApprovalInvalidError, ConfigurationError, ConflictError
from cognitive_mesh.services.tasks import transition
from cognitive_mesh.workflows.command_router import (
    Route,
    bare_platform,
    blocking_reply,
    needs_platform,
    route_command,
    with_platform,
)
from cognitive_mesh.workflows.content_pipeline import (
    ContentPipeline,
    DraftRound,
    WorkflowStopped,
)

Outcome = Literal[
    "", "unsupported", "awaiting", "approved", "published", "rejected", "changes", "cancelled",
    "failed",
]
TERMINAL_OUTCOMES = {"unsupported", "approved", "published", "rejected", "cancelled", "failed"}
PUBLISH_BLOCKED = (
    "I stopped before posting because the approval is no longer valid. Nothing went live. "
    "Reconnect LinkedIn if needed, then press Retry to approve a fresh version."
)
OPEN_TASK_STATUSES = [TaskStatus.RUNNING, TaskStatus.AWAITING_APPROVAL, TaskStatus.APPROVED]
NO_RESEARCH: dict[str, Any] = {"summary": "", "insights": []}

NOT_CONNECTED = (
    "LinkedIn isn't connected yet. Connect it on the Connectors page, then press Retry."
)
NO_PROFILE_ID = (
    "I couldn't read your LinkedIn member id, which is needed as the post author. "
    "Reconnect LinkedIn on the Connectors page, then press Retry."
)


class GraphState(TypedDict, total=False):
    task_id: str
    execution_id: str
    user_id: str
    command: str
    mode: str  # post_as_is | write | research | model
    provided_text: str
    profile: str
    author_urn: str
    intent: dict[str, Any]
    findings: dict[str, Any]
    sources: list[dict[str, Any]]
    feedback: list[str]
    next_version: int
    draft_text: str
    draft_id: str
    approval_id: str
    outcome: Outcome


ProfileReader = Callable[[str], Any]
PipelineBuilder = Callable[[str], ContentPipeline]


@dataclass
class GraphDeps:
    db: Database
    settings: Settings
    gateway: IntegrationGateway
    signer: ApprovalSigner | None
    build_pipeline: PipelineBuilder  # user_id -> pipeline


def profile_from_info(data: Any) -> tuple[str, str | None]:
    """(profile text for the writer, LinkedIn person id) from LINKEDIN_GET_MY_INFO."""
    if not isinstance(data, dict):
        return "unknown", None
    name = " ".join(p for p in (data.get("localizedFirstName"), data.get("localizedLastName")) if p)
    headline = data.get("localizedHeadline")
    parts = [f"Name: {name}" if name else "", f"Headline: {headline}" if headline else ""]
    return "; ".join(p for p in parts if p) or "unknown", data.get("id")


def build_graph(deps: GraphDeps) -> StateGraph:
    db = deps.db

    # ---------- shared helpers ----------
    async def load_task(state: GraphState) -> Task:
        async with db.sessionmaker() as s:
            task = await s.get(Task, uuid.UUID(state["task_id"]), populate_existing=True)
        assert task is not None
        return task

    def still_mine(task: Task, state: GraphState) -> bool:
        return task.current_execution_id == uuid.UUID(state["execution_id"])

    async def event(state: GraphState, name: str, actor: str, detail: dict | None = None) -> None:
        async with db.sessionmaker() as s:
            task = await s.get(Task, uuid.UUID(state["task_id"]))
            assert task is not None
            audit.record(
                s, workspace_id=task.workspace_id, event=name, actor=actor,
                task_id=task.id, execution_id=uuid.UUID(state["execution_id"]),
                detail=detail or {},
            )
            await s.commit()

    def sink(state: GraphState):  # type: ignore[no-untyped-def]
        async def _sink(name: str, detail: dict[str, Any]) -> None:
            actor = name.split(".", 1)[0]
            await event(state, name, "manager" if actor == "manager" else f"{actor}_employee", detail)
        return _sink

    async def say(state: GraphState, text: str) -> None:
        async with db.sessionmaker() as s:
            task = await s.get(Task, uuid.UUID(state["task_id"]))
            chat = await s.get(Chat, task.chat_id)  # type: ignore[union-attr]
            add_message(s, chat, role=MessageRole.ASSISTANT, content=text, task_id=task.id)  # type: ignore[arg-type,union-attr]
            await s.commit()

    async def fail(state: GraphState, message: str, reason: str) -> GraphState:
        async with db.sessionmaker() as s:
            task = await s.get(Task, uuid.UUID(state["task_id"]), populate_existing=True)
            assert task is not None
            if task.status in OPEN_TASK_STATUSES and still_mine(task, state):
                await transition(s, task, allowed_from=OPEN_TASK_STATUSES, to=TaskStatus.FAILED)
                await s.execute(
                    update(Execution)
                    .where(Execution.id == task.current_execution_id)
                    .values(error=reason[:2000])
                )
                chat = await s.get(Chat, task.chat_id)
                add_message(s, chat, role=MessageRole.ASSISTANT, content=message, task_id=task.id)  # type: ignore[arg-type]
                audit.record(
                    s, workspace_id=task.workspace_id, event="execution.failed", actor="manager",
                    task_id=task.id, execution_id=task.current_execution_id,
                    detail={"reason": message},
                )
                await s.commit()
        return {"outcome": "failed"}

    async def is_current(state: GraphState, required: TaskStatus) -> bool:
        """The task is in `required` status and still on this execution (not cancelled)."""
        task = await load_task(state)
        return task.status == required and still_mine(task, state)

    async def finish_with_reply(state: GraphState, text: str) -> GraphState:
        """End the task with a message: nothing to draft, approve or publish."""
        await say(state, text)
        async with db.sessionmaker() as s:
            task = await s.get(Task, uuid.UUID(state["task_id"]), populate_existing=True)
            await transition(s, task, allowed_from=[TaskStatus.RUNNING], to=TaskStatus.COMPLETED)  # type: ignore[arg-type]
            await s.commit()
        return {"outcome": "unsupported"}

    async def resolve_command(state: GraphState, task: Task) -> tuple[str, Route]:
        """A bare platform reply ("LinkedIn") continues the command that asked for one."""
        command = state["command"]
        platform = bare_platform(command)
        if platform:
            async with db.sessionmaker() as s:
                previous = await s.scalar(
                    select(Task)
                    .where(Task.chat_id == task.chat_id, Task.id < task.id)
                    .order_by(Task.id.desc())
                    .limit(1)
                )
            if previous is not None and previous.status == TaskStatus.COMPLETED:
                earlier = route_command(previous.request)
                if needs_platform(earlier):
                    return previous.request, with_platform(earlier, platform)
        return command, route_command(command)

    # ---------- nodes ----------
    async def prepare(state: GraphState) -> GraphState:
        if not await is_current(state, TaskStatus.RUNNING):
            return {"outcome": "cancelled"}
        task = await load_task(state)
        command, route = await resolve_command(state, task)
        reply = blocking_reply(route)
        if reply:
            return await finish_with_reply(state, reply)
        await event(
            state, "route.selected", "manager", {"mode": route.action, "platform": route.platform}
        )
        async with db.sessionmaker() as s:
            connection = await get_active_connection(s, task.workspace_id, linkedin.TOOLKIT)
        if connection is None:
            return await fail(state, NOT_CONNECTED, "linkedin not connected")
        # Social Media employee: read-only profile lookup (niche + author id).
        try:
            info = await deps.gateway.execute_read_only(
                user_id=state["user_id"], toolkit_slug=linkedin.TOOLKIT,
                tool_slug="LINKEDIN_GET_MY_INFO", arguments={},
            )
        except (IntegrationUnavailableError, ToolExecutionError):
            info = None  # handled below: no person id -> clear failure message
        profile, person_id = profile_from_info(info)
        if not person_id:
            return await fail(state, NO_PROFILE_ID, "LINKEDIN_GET_MY_INFO returned no id")
        return {
            "command": command,
            "mode": route.action,
            "provided_text": route.text or "",
            "intent": CommandIntent(action="linkedin_post", topic=command).model_dump(),
            "findings": dict(NO_RESEARCH),
            "sources": [],
            "feedback": [],
            "next_version": 1,
            "profile": profile,
            "author_urn": linkedin.person_urn(person_id),
            "outcome": "",
        }

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

    async def request_approval(state: GraphState) -> GraphState:
        if deps.signer is None:
            raise ConfigurationError("APPROVAL_SIGNING_KEY is not configured")
        task = await load_task(state)
        if task.status == TaskStatus.CANCELLED or not still_mine(task, state):
            return {"outcome": "cancelled"}
        execution_id = uuid.UUID(state["execution_id"])

        async with db.sessionmaker() as s:
            latest = await approval_svc.latest_for_execution(s, execution_id)
            draft_id = uuid.UUID(state["draft_id"])
            # Same draft -> this node is re-running (resume/crash): reuse its approval
            # (or the user's visibility re-version of it). A new draft -> new approval.
            reusable = latest is not None and latest.content_draft_id == draft_id
            if reusable:
                approval = latest  # node re-run (resume/crash): never create a duplicate
            else:
                connection = await get_active_connection(s, task.workspace_id, linkedin.TOOLKIT)
                if connection is None:
                    return await fail(state, NOT_CONNECTED, "linkedin disconnected")
                visibility = (
                    latest.payload.get("visibility", linkedin.DEFAULT_VISIBILITY)
                    if latest is not None
                    else linkedin.DEFAULT_VISIBILITY
                )  # keep the user's earlier visibility choice across revisions
                approval = await approval_svc.request_approval(
                    s, deps.signer,
                    task=await s.get(Task, task.id, populate_existing=True),  # type: ignore[arg-type]
                    execution=await s.get(Execution, execution_id),  # type: ignore[arg-type]
                    requested_by=task.created_by,
                    action_type=linkedin.ACTION_TYPE,
                    toolkit_slug=linkedin.TOOLKIT,
                    tool_slug=linkedin.POST_TOOL,
                    connection=connection,
                    payload=linkedin.build_post_payload(
                        author_urn=state["author_urn"],
                        commentary=state["draft_text"],
                        visibility=visibility,
                    ),
                    content_draft_id=draft_id,
                )
            approval_id = str(approval.id)
            version = approval.content_version

        # Pause here. Resumed by the runner after the user decides (or on recovery).
        interrupt({"approval_id": approval_id, "content_version": version})
        return {"approval_id": approval_id, "outcome": "awaiting"}

    async def decide(state: GraphState) -> GraphState:
        task = await load_task(state)
        if task.status == TaskStatus.CANCELLED or not still_mine(task, state):
            return {"outcome": "cancelled"}
        async with db.sessionmaker() as s:
            latest = await approval_svc.latest_for_execution(s, uuid.UUID(state["execution_id"]))
        status = latest.status if latest else None
        await event(state, "graph.resumed", "manager", {"approval_status": status})
        if status == ApprovalStatus.APPROVED:
            return {"outcome": "approved"}
        if status == ApprovalStatus.REJECTED:
            return {"outcome": "rejected"}
        if status == ApprovalStatus.CHANGES_REQUESTED:
            return {"outcome": "changes", "feedback": [latest.decision_note or "Revise the post."]}  # type: ignore[union-attr]
        if status == ApprovalStatus.INVALIDATED:
            return await fail(
                state,
                "The approval was invalidated (the LinkedIn account changed or disconnected). "
                "Reconnect if needed, then press Retry for a fresh draft.",
                f"approval invalidated: {latest.decision_note if latest else ''}",
            )
        return {"outcome": "awaiting"}  # still pending: pause again

    async def publish(state: GraphState) -> GraphState:
        """The only path to an external post. Sends at most once per approval."""
        if deps.signer is None:
            raise ConfigurationError("APPROVAL_SIGNING_KEY is not configured")
        task = await load_task(state)
        if not still_mine(task, state):
            return {"outcome": "cancelled"}
        async with db.sessionmaker() as s:
            approval = await approval_svc.latest_for_execution(s, uuid.UUID(state["execution_id"]))
            if approval is None:
                return await fail(state, PUBLISH_BLOCKED, "no approval found")
            existing = await publishing.for_approval(s, approval.id)
            if existing is not None:  # this node re-ran after a crash or restart
                if existing.status == PublicationStatus.SENDING:
                    await publishing.finish(
                        s, existing.id, PublicationStatus.NEEDS_CHECK,
                        error="interrupted while sending",
                    )
                    return {"outcome": "failed"}
                published = existing.status == PublicationStatus.PUBLISHED
                return {"outcome": "published" if published else "failed"}
            if task.status != TaskStatus.APPROVED:
                return {"outcome": "cancelled"}
            try:
                publication = await publishing.reserve(s, deps.signer, approval)
            except ApprovalInvalidError as exc:
                return await fail(state, PUBLISH_BLOCKED, f"approval check failed: {exc.detail}")
            except ConflictError:
                return {"outcome": "cancelled"}
            publication_id = publication.id
            target = approval.target_account_id
            arguments = dict(approval.payload)
            toolkit, tool = approval.toolkit_slug, approval.tool_slug

        status, post_id, url, error = PublicationStatus.PUBLISHED, None, None, None
        try:
            data = await deps.gateway.execute_approved_action(
                user_id=state["user_id"], toolkit_slug=toolkit, tool_slug=tool,
                connected_account_id=target, arguments=arguments,
            )
            post_id = linkedin.post_id_from(data)
            url = linkedin.post_url(post_id)
            if post_id is None:
                status, error = PublicationStatus.NEEDS_CHECK, "success without a post id"
        except ActionRejectedError as exc:
            status, error = PublicationStatus.FAILED, str(exc)
        except Exception as exc:  # noqa: BLE001 - after sending, any error means outcome unknown
            status, error = PublicationStatus.NEEDS_CHECK, repr(exc)
        async with db.sessionmaker() as s:
            await publishing.finish(
                s, publication_id, status, external_id=post_id, external_url=url, error=error
            )
        return {"outcome": "published" if status == PublicationStatus.PUBLISHED else "failed"}

    # ---------- wiring ----------
    def stop_or(next_node: str):  # type: ignore[no-untyped-def]
        def route(state: GraphState) -> str:
            return END if state.get("outcome") in TERMINAL_OUTCOMES else next_node
        return route

    def after_decide(state: GraphState) -> str:
        outcome = state.get("outcome")
        if outcome == "changes":
            return "write"
        if outcome == "awaiting":
            return "request_approval"
        if outcome == "approved":
            return "publish"
        return END  # rejected | cancelled | failed

    def after_prepare(state: GraphState) -> str:
        if state.get("outcome") in TERMINAL_OUTCOMES:
            return END
        return {"post_as_is": "use_provided_text", "write": "write"}.get(state["mode"], "research")

    graph = StateGraph(GraphState)
    graph.add_node("prepare", prepare)
    graph.add_node("use_provided_text", use_provided_text)
    graph.add_node("research", research)
    graph.add_node("write", write)
    graph.add_node("request_approval", request_approval)
    graph.add_node("decide", decide)
    graph.add_edge(START, "prepare")
    graph.add_conditional_edges(
        "prepare", after_prepare, ["use_provided_text", "write", "research", END]
    )
    graph.add_conditional_edges(
        "use_provided_text", stop_or("request_approval"), ["request_approval", END]
    )
    graph.add_conditional_edges("research", stop_or("write"), ["write", END])
    graph.add_conditional_edges("write", stop_or("request_approval"), ["request_approval", END])
    graph.add_conditional_edges("request_approval", stop_or("decide"), ["decide", END])
    graph.add_conditional_edges(
        "decide", after_decide, ["write", "request_approval", "publish", END]
    )
    graph.add_node("publish", publish)
    graph.add_edge("publish", END)
    return graph

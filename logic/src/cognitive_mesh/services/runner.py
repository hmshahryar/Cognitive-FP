"""Background runner: drives the LangGraph workflow for each execution.

In-process for now (single worker); the interface allows a Redis/queue worker later.
- Start: claim QUEUED -> RUNNING with compare-and-set, then run the graph until it
  finishes or pauses for approval (interrupt).
- Resume: after the user decides, the API queues a resume; the graph continues from
  its checkpoint and reads the decision from the database.
- Durability: checkpoints are written synchronously (durability="sync") to their own
  SQLite file, so a crash never loses an approval pause.
- Recovery on startup: resume graphs whose decision arrived while we were down,
  continue graphs that crashed mid-run from their last checkpoint, and fail runs that
  never reached a checkpoint.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from sqlalchemy import select

from cognitive_mesh.agents.employees import (
    ContentEmployee,
    ConversationSummarizer,
    EmailEmployee,
    IntentAnalyst,
    ManagerAgent,
    ResearchEmployee,
    ReviewEmployee,
)
from cognitive_mesh.agents.errors import CitationError, ResearchFailedError
from cognitive_mesh.agents.llm import (
    MANAGER_TIMEOUT_SECONDS,
    build_chat_model,
    is_quota_exhausted,
    quota_retry_seconds,
)
from cognitive_mesh.config import Settings
from cognitive_mesh.db import Database
from cognitive_mesh.integrations.gateway import IntegrationGateway, IntegrationUnavailableError
from cognitive_mesh.models import ApprovalStatus, Chat, Execution, MessageRole, Task, TaskStatus
from cognitive_mesh.research.providers import (
    ComposioPageReader,
    ComposioSearchProvider,
    GeminiGroundingProvider,
)
from cognitive_mesh.security.approval_signing import ApprovalSigner
from cognitive_mesh.services import audit, conversation
from cognitive_mesh.services.approvals import latest_for_execution
from cognitive_mesh.services.chats import add_message
from cognitive_mesh.services.errors import ConfigurationError, ConflictError
from cognitive_mesh.services.long_term_memory import LongTermMemory
from cognitive_mesh.services.tasks import transition
from cognitive_mesh.workflows.command_graph import GraphDeps, GraphState, build_graph
from cognitive_mesh.workflows.content_pipeline import ContentPipeline
from cognitive_mesh.workflows.engine import executor as workflow_executor

logger = logging.getLogger(__name__)

PipelineFactory = Callable[[Settings, IntegrationGateway, str], ContentPipeline]

# Tasks whose graph may still have work to do.
LIVE_STATUSES = [
    TaskStatus.RUNNING,
    TaskStatus.AWAITING_APPROVAL,
    TaskStatus.APPROVED,
    TaskStatus.EXECUTING,
    TaskStatus.REJECTED,
    TaskStatus.CANCELLED,
]
# Not yet sent externally, so an unexpected error can safely fail the task.
FAILABLE_STATUSES = (TaskStatus.RUNNING, TaskStatus.AWAITING_APPROVAL, TaskStatus.APPROVED)
CONTINUABLE_STATUSES = (TaskStatus.RUNNING, TaskStatus.APPROVED, TaskStatus.EXECUTING)


def default_pipeline_factory(
    settings: Settings, gateway: IntegrationGateway, user_id: str
) -> ContentPipeline:
    model = build_chat_model(settings)
    provider = (
        GeminiGroundingProvider(model)
        if settings.search_provider == "gemini"
        else ComposioSearchProvider(gateway, user_id)
    )
    return ContentPipeline(
        intent=IntentAnalyst(model),
        researcher=ResearchEmployee(model, provider),
        writer=ContentEmployee(model),
        reviewer=ReviewEmployee(model),
        max_review_rounds=settings.max_review_rounds,
        page_reader=ComposioPageReader(gateway, user_id),
        email_writer=EmailEmployee(model),
        summarizer=ConversationSummarizer(model),
        # Routing is a small classification, so it uses low thinking to respond quickly.
        manager=ManagerAgent(
            build_chat_model(
                settings, thinking_level="low", timeout_seconds=MANAGER_TIMEOUT_SECONDS
            )
        ),
    )


def _user_facing_error(exc: Exception) -> str:
    cause: BaseException | None = exc
    while cause is not None and not is_quota_exhausted(cause):  # type: ignore[arg-type]
        cause = cause.__cause__ or cause.__context__
    if cause is not None:
        seconds = quota_retry_seconds(cause)  # type: ignore[arg-type]
        when = f" It resets in about {round(seconds / 3600, 1)} hours." if seconds else ""
        return (
            "The AI model's daily free-tier limit has been reached, so I stopped." + when
            + " Press Retry after it resets, or enable billing on the Gemini project."
        )
    if isinstance(exc, ResearchFailedError):
        return "I couldn't find reliable, citable sources for this topic. Try a broader topic."
    if isinstance(exc, CitationError):
        return (
            "I stopped because every draft failed a required check (sources, figures, "
            "quotations, placeholders or invented experience), so none was sent for approval. "
            "Press Retry, or make the request more specific."
        )
    if isinstance(exc, ConfigurationError):
        return f"The server isn't configured for this yet ({exc.detail})."
    if isinstance(exc, IntegrationUnavailableError):
        if "is retired" in str(exc):
            return (
                "The web search service is rejecting requests because Composio reports its "
                "toolkit version as retired. Nothing was drafted. Try again later."
            )
        return "A connected service was unavailable. Please try again shortly."
    return "Something went wrong while working on this. Please try again."


class TaskRunner:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        gateway: IntegrationGateway,
        pipeline_factory: PipelineFactory = default_pipeline_factory,
        signer: ApprovalSigner | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self.memory = LongTermMemory(settings.memory_db_path)
        self._deps = GraphDeps(
            db=db,
            settings=settings,
            gateway=gateway,
            signer=signer,
            build_pipeline=lambda user_id: pipeline_factory(settings, gateway, user_id),
            memory=self.memory,
        )
        self._graph: Any = None
        self._conn: aiosqlite.Connection | None = None
        self._resumes: asyncio.Queue[uuid.UUID] = asyncio.Queue()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    # ---------- graph + checkpointer ----------
    async def graph(self) -> Any:
        if self._graph is None:
            path = Path(self._settings.checkpoint_db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(path.as_posix())
            saver = AsyncSqliteSaver(self._conn)
            await saver.setup()
            self._graph = build_graph(self._deps).compile(checkpointer=saver)
        return self._graph

    async def close(self) -> None:
        await self.memory.close()
        if self._conn is not None:
            await self._conn.close()
            self._conn, self._graph = None, None

    @staticmethod
    def _config(execution: Execution) -> dict[str, Any]:
        return {"configurable": {"thread_id": execution.thread_id}}

    async def snapshot(self, execution: Execution) -> Any:
        return await (await self.graph()).aget_state(self._config(execution))

    @staticmethod
    def is_paused(snapshot: Any) -> bool:
        return bool(snapshot.next) and any(t.interrupts for t in snapshot.tasks)

    # ---------- lifecycle ----------
    def start(self) -> None:
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="task-runner")

    async def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.close()

    def request_resume(self, execution_id: uuid.UUID) -> None:
        """Called after a user decision; the graph continues on the runner's loop."""
        self._resumes.put_nowait(execution_id)
        self._wake.set()

    def wake(self) -> None:
        """A workflow gate was approved, so check for work now."""
        self._wake.set()

    async def _loop(self) -> None:
        await self.recover()
        while not self._stopping.is_set():
            try:
                worked = await self.run_once()
            except Exception:
                logger.exception("Task runner iteration failed")
                worked = False
            if not worked:
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._wake.wait(), timeout=self._settings.worker_poll_seconds
                    )

    # ---------- work ----------
    async def run_once(self) -> bool:
        """Resumes first (a user is waiting), then one new execution."""
        if not self._resumes.empty():
            await self.resume(self._resumes.get_nowait())
            return True
        execution_id = await self._claim_next()
        if execution_id is not None:
            await self.start_execution(execution_id)
            return True
        if self._deps.signer is None:
            return False
        return await workflow_executor.advance_due(
            self._db, self._deps.gateway, self._deps.signer
        )

    async def _claim_next(self) -> uuid.UUID | None:
        async with self._db.sessionmaker() as s:
            task = (
                await s.execute(
                    select(Task)
                    .join(Execution, Execution.id == Task.current_execution_id)
                    .where(Task.status == TaskStatus.QUEUED, Execution.status == TaskStatus.QUEUED)
                    .order_by(Execution.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if task is None:
                return None
            try:
                await transition(s, task, allowed_from=[TaskStatus.QUEUED], to=TaskStatus.RUNNING)
            except ConflictError:
                return None  # someone else claimed or cancelled it
            audit.record(
                s, workspace_id=task.workspace_id, event="execution.started", actor="manager",
                task_id=task.id, execution_id=task.current_execution_id,
            )
            await s.commit()
            return task.current_execution_id

    async def _load(self, execution_id: uuid.UUID) -> tuple[Execution, Task] | None:
        async with self._db.sessionmaker() as s:
            execution = await s.get(Execution, execution_id)
            task = await s.get(Task, execution.task_id) if execution else None
        return (execution, task) if execution and task else None

    async def _invoke(self, execution: Execution, task: Task, payload: Any) -> None:
        graph = await self.graph()
        try:
            await graph.ainvoke(payload, self._config(execution), durability="sync")
        except Exception as exc:
            logger.exception("Execution %s failed", execution.id)
            await self._fail(task, execution, _user_facing_error(exc), repr(exc))

    async def start_execution(self, execution_id: uuid.UUID) -> None:
        loaded = await self._load(execution_id)
        if loaded is None:
            return
        execution, task = loaded
        initial: GraphState = {
            "task_id": str(task.id),
            "execution_id": str(execution.id),
            "user_id": str(task.created_by),
            "command": task.request,
            "outcome": "",
        }
        await self._invoke(execution, task, initial)
        await self._compact_chat(task)

    async def _compact_chat(self, task: Task) -> None:
        """Best effort: a failed compaction never affects the user's task."""
        try:
            await conversation.compact_if_due(
                self._db,
                task.chat_id,
                self._deps.build_pipeline(str(task.created_by)).summarize,
            )
        except Exception:
            logger.warning("Chat compaction skipped", exc_info=True)

    async def resume(self, execution_id: uuid.UUID) -> bool:
        """Wake a paused graph. The decision is read from the DB inside the graph."""
        loaded = await self._load(execution_id)
        if loaded is None:
            return False
        execution, task = loaded
        if not self.is_paused(await self.snapshot(execution)):
            return False  # nothing waiting (already resumed or finished)
        async with self._db.sessionmaker() as s:
            latest = await latest_for_execution(s, execution.id)
        signal = {"approval_id": str(latest.id) if latest else None}  # wake-up only
        await self._invoke(execution, task, Command(resume=signal))
        return True

    async def _fail(self, task: Task, execution: Execution, message: str, reason: str) -> None:
        async with self._db.sessionmaker() as s:
            fresh = await s.get(Task, task.id, populate_existing=True)
            if (
                fresh is None
                or fresh.current_execution_id != execution.id
                or fresh.status not in FAILABLE_STATUSES
            ):
                return
            await transition(
                s, fresh, allowed_from=FAILABLE_STATUSES, to=TaskStatus.FAILED
            )
            row = await s.get(Execution, execution.id)
            row.error = reason[:2000]  # type: ignore[union-attr]
            chat = await s.get(Chat, fresh.chat_id)
            add_message(s, chat, role=MessageRole.ASSISTANT, content=message, task_id=fresh.id)  # type: ignore[arg-type]
            audit.record(
                s, workspace_id=fresh.workspace_id, event="execution.failed", actor="manager",
                task_id=fresh.id, execution_id=execution.id, detail={"reason": message},
            )
            await s.commit()

    # ---------- recovery ----------
    async def recover(self) -> dict[str, int]:
        """Bring every live execution back in line with its checkpoint after a restart."""
        counts = {"resumed": 0, "continued": 0, "failed": 0}
        interrupted_steps = await workflow_executor.recover_interrupted(self._db)
        if interrupted_steps:
            logger.warning("%d workflow steps need checking after restart", interrupted_steps)
        async with self._db.sessionmaker() as s:
            rows = list(
                (
                    await s.execute(
                        select(Task, Execution)
                        .join(Execution, Execution.id == Task.current_execution_id)
                        .where(Task.status.in_(LIVE_STATUSES))
                    )
                ).all()
            )
        for task, execution in rows:
            snap = await self.snapshot(execution)
            if self.is_paused(snap):
                async with self._db.sessionmaker() as s:
                    latest = await latest_for_execution(s, execution.id)
                decided = latest is not None and latest.status != ApprovalStatus.PENDING
                wake = decided or task.status == TaskStatus.CANCELLED
                if wake and await self.resume(execution.id):
                    counts["resumed"] += 1
            elif snap.next and task.status in CONTINUABLE_STATUSES:
                # Continue from the last checkpoint. An interrupted send becomes
                # NEEDS_CHECK inside the publish node; it is never sent again.
                await self._invoke(execution, task, None)
                counts["continued"] += 1
            elif not snap.values and task.status == TaskStatus.RUNNING:
                await self._fail(
                    task, execution,
                    "This run was interrupted by a server restart. Press Retry to start again.",
                    "interrupted before first checkpoint",
                )
                counts["failed"] += 1
        return counts

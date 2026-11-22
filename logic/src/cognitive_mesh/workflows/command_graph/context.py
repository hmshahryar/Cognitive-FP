"""Dependencies and shared helpers for workflow nodes."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import update

from cognitive_mesh.config import Settings
from cognitive_mesh.db import Database
from cognitive_mesh.integrations.gateway import IntegrationGateway
from cognitive_mesh.models import Chat, Execution, MessageRole, Task, TaskStatus
from cognitive_mesh.security.approval_signing import ApprovalSigner
from cognitive_mesh.services import audit, conversation
from cognitive_mesh.services.chats import add_message
from cognitive_mesh.services.long_term_memory import LongTermMemory
from cognitive_mesh.services.tasks import transition
from cognitive_mesh.workflows.command_graph.state import GraphState
from cognitive_mesh.workflows.content_pipeline import ContentPipeline

OPEN_TASK_STATUSES = [TaskStatus.RUNNING, TaskStatus.AWAITING_APPROVAL, TaskStatus.APPROVED]
PipelineBuilder = Callable[[str], ContentPipeline]
NodeFunction = Callable[[GraphState], Awaitable[GraphState]]


@dataclass
class GraphDeps:
    db: Database
    settings: Settings
    gateway: IntegrationGateway
    signer: ApprovalSigner | None
    build_pipeline: PipelineBuilder  # user_id -> pipeline
    memory: LongTermMemory


class NodeContext:
    """Task, chat and audit operations used by more than one node."""

    def __init__(self, deps: GraphDeps) -> None:
        self.deps = deps
        self.db = deps.db

    async def load_task(self, state: GraphState) -> Task:
        async with self.db.sessionmaker() as s:
            task = await s.get(Task, uuid.UUID(state["task_id"]), populate_existing=True)
        assert task is not None
        return task

    def still_mine(self, task: Task, state: GraphState) -> bool:
        return task.current_execution_id == uuid.UUID(state["execution_id"])

    async def event(
        self, state: GraphState, name: str, actor: str, detail: dict | None = None
    ) -> None:
        async with self.db.sessionmaker() as s:
            task = await s.get(Task, uuid.UUID(state["task_id"]))
            assert task is not None
            audit.record(
                s, workspace_id=task.workspace_id, event=name, actor=actor,
                task_id=task.id, execution_id=uuid.UUID(state["execution_id"]),
                detail=detail or {},
            )
            await s.commit()

    def sink(self, state: GraphState):  # type: ignore[no-untyped-def]
        async def _sink(name: str, detail: dict[str, Any]) -> None:
            actor = name.split(".", 1)[0]
            await self.event(
                state, name, "manager" if actor == "manager" else f"{actor}_employee", detail
            )
        return _sink

    async def say(self, state: GraphState, text: str) -> None:
        async with self.db.sessionmaker() as s:
            task = await s.get(Task, uuid.UUID(state["task_id"]))
            chat = await s.get(Chat, task.chat_id)  # type: ignore[union-attr]
            add_message(s, chat, role=MessageRole.ASSISTANT, content=text, task_id=task.id)  # type: ignore[arg-type,union-attr]
            await s.commit()

    async def fail(self, state: GraphState, message: str, reason: str) -> GraphState:
        async with self.db.sessionmaker() as s:
            task = await s.get(Task, uuid.UUID(state["task_id"]), populate_existing=True)
            assert task is not None
            if task.status in OPEN_TASK_STATUSES and self.still_mine(task, state):
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

    async def is_current(self, state: GraphState, required: TaskStatus) -> bool:
        """The task is in `required` status and still on this execution (not cancelled)."""
        task = await self.load_task(state)
        return task.status == required and self.still_mine(task, state)

    async def finish_with_reply(self, state: GraphState, text: str) -> GraphState:
        """End the task with a message: nothing to draft, approve or publish."""
        await self.say(state, text)
        async with self.db.sessionmaker() as s:
            task = await s.get(Task, uuid.UUID(state["task_id"]), populate_existing=True)
            await transition(s, task, allowed_from=[TaskStatus.RUNNING], to=TaskStatus.COMPLETED)  # type: ignore[arg-type]
            await s.commit()
        return {"outcome": "unsupported"}

    async def ai_context(self, state: GraphState, query: str) -> conversation.ConversationContext:
        task = await self.load_task(state)
        async with self.db.sessionmaker() as s:
            return await conversation.context_for(s, task, self.deps.memory, query)

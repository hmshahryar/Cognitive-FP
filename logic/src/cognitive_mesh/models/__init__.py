"""Import every model here so Base.metadata (and Alembic) sees all tables."""

from cognitive_mesh.models.approval import Approval, ApprovalStatus, AuditEvent
from cognitive_mesh.models.connection import (
    AttemptStatus,
    ConnectionAttempt,
    ConnectionStatus,
    IntegrationConnection,
)
from cognitive_mesh.models.draft import ContentDraft
from cognitive_mesh.models.memory import ChatMemory
from cognitive_mesh.models.publication import Publication, PublicationStatus
from cognitive_mesh.models.user import User
from cognitive_mesh.models.work import Chat, Execution, Message, MessageRole, Task, TaskStatus
from cognitive_mesh.models.workspace import Workspace

__all__ = [
    "Approval",
    "ApprovalStatus",
    "AttemptStatus",
    "AuditEvent",
    "Chat",
    "ChatMemory",
    "ConnectionAttempt",
    "ConnectionStatus",
    "ContentDraft",
    "Execution",
    "IntegrationConnection",
    "Message",
    "MessageRole",
    "Publication",
    "PublicationStatus",
    "Task",
    "TaskStatus",
    "User",
    "Workspace",
]

"""Import every model here so Base.metadata (and Alembic) sees all tables."""

from cognitive_mesh.models.user import User
from cognitive_mesh.models.workspace import Workspace

__all__ = ["User", "Workspace"]

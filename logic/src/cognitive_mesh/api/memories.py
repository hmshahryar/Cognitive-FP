from datetime import datetime

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from cognitive_mesh.deps import CurrentAccountDep
from cognitive_mesh.services.errors import NotFoundError
from cognitive_mesh.services.long_term_memory import LongTermMemory

router = APIRouter(prefix="/memories", tags=["memories"])


class MemoryOut(BaseModel):
    id: str
    kind: str
    text: str
    name: str | None
    email: str | None
    updated_at: datetime


def _memory(request: Request) -> LongTermMemory:
    return request.app.state.runner.memory


@router.get("", response_model=list[MemoryOut])
async def list_memories(request: Request, account: CurrentAccountDep) -> list[MemoryOut]:
    """What the AI remembers for this workspace."""
    items = await _memory(request).list_items(account.workspace.id)
    return [
        MemoryOut(
            id=i.id, kind=i.kind, text=i.text, name=i.name, email=i.email, updated_at=i.updated_at
        )
        for i in items
    ]


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: str, request: Request, account: CurrentAccountDep) -> Response:
    if not await _memory(request).delete(account.workspace.id, memory_id):
        raise NotFoundError("Memory not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

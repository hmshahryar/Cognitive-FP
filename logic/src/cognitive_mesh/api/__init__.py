from fastapi import APIRouter

from cognitive_mesh.api import (
    approvals,
    chats,
    connectors,
    health,
    me,
    memories,
    tasks,
    workflow_runs,
    workspaces,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(me.router)
api_router.include_router(workspaces.router)
api_router.include_router(connectors.router)
api_router.include_router(chats.router)
api_router.include_router(tasks.router)
api_router.include_router(approvals.router)
api_router.include_router(memories.router)
api_router.include_router(workflow_runs.router)

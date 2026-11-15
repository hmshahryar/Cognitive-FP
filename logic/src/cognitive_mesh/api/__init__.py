from fastapi import APIRouter

from cognitive_mesh.api import health, me, workspaces

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(me.router)
api_router.include_router(workspaces.router)

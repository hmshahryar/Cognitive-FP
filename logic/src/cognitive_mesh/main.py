from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from cognitive_mesh.api import api_router
from cognitive_mesh.config import Settings, get_settings
from cognitive_mesh.db import Database
from cognitive_mesh.integrations.gateway import ComposioGateway, IntegrationGateway
from cognitive_mesh.security.approval_signing import ApprovalSigner
from cognitive_mesh.services.clerk_profiles import ClerkApiProfileFetcher, ProfileFetcher
from cognitive_mesh.services.errors import DomainError
from cognitive_mesh.services.runner import (
    PipelineFactory,
    TaskRunner,
    default_pipeline_factory,
)


def create_app(
    settings: Settings | None = None,
    *,
    profile_fetcher: ProfileFetcher | None = None,
    integration_gateway: IntegrationGateway | None = None,
    pipeline_factory: PipelineFactory | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    db = Database(settings.database_url, echo=settings.database_echo)
    auth_configs = (
        {"hubspot": settings.hubspot_auth_config_id} if settings.hubspot_auth_config_id else {}
    )
    gateway = integration_gateway or ComposioGateway(
        settings.composio_api_key, auth_configs=auth_configs
    )
    signer = (
        ApprovalSigner(settings.approval_signing_key) if settings.approval_signing_key else None
    )
    runner = TaskRunner(
        db, settings, gateway, pipeline_factory or default_pipeline_factory, signer=signer
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if settings.run_worker:
            runner.start()
        yield
        await runner.stop()
        await db.dispose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.profile_fetcher = profile_fetcher or ClerkApiProfileFetcher(
        settings.clerk_secret_key
    )
    app.state.integration_gateway = gateway
    app.state.runner = runner
    app.state.approval_signer = signer

    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    return app


app = create_app()

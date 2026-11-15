from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cognitive_mesh.api import api_router
from cognitive_mesh.config import Settings, get_settings
from cognitive_mesh.db import Database
from cognitive_mesh.services.clerk_profiles import ClerkApiProfileFetcher, ProfileFetcher


def create_app(
    settings: Settings | None = None,
    *,
    profile_fetcher: ProfileFetcher | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    db = Database(settings.database_url, echo=settings.database_echo)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await db.dispose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.profile_fetcher = profile_fetcher or ClerkApiProfileFetcher(
        settings.clerk_secret_key
    )

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

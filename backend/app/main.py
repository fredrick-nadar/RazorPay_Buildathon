"""FastAPI application factory for ARGUS CONTROL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.ai.router import router as ai_router
from app.api.routes_cases import router as cases_router
from app.api.routes_chat import router as chat_router
from app.api.routes_meta import router as meta_router
from app.api.routes_razorpay import router as razorpay_router
from app.api.routes_runs import router as runs_router
from app.config import APP_NAME, Settings, get_settings
from app.persistence.database import open_database
from app.voice.api import router as voice_router


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved
        app.state.db = open_database(resolved)
        yield
        app.state.db.close()

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(meta_router)
    app.include_router(runs_router)
    app.include_router(cases_router)
    app.include_router(chat_router)
    app.include_router(razorpay_router)
    app.include_router(voice_router)
    app.include_router(ai_router)
    return app


app = create_app()

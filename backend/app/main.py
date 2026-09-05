"""FastAPI application factory for ARGUS CONTROL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.ai.router import router as ai_router
from app.api.routes_cases import router as cases_router
from app.api.routes_chat import router as chat_router
from app.api.routes_ingest import router as ingest_router
from app.api.routes_meta import router as meta_router
from app.api.routes_razorpay import router as razorpay_router
from app.api.routes_runs import router as runs_router
from app.api.routes_status import router as status_router
from app.config import APP_NAME, Settings, get_settings
from app.importers.session_staging import SourceRecoveryError, SourceRevisionError
from app.persistence.database import open_database
from app.voice.api import router as voice_router
from app.workflow.controller import ReconciliationController


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved
        app.state.db = open_database(resolved)
        app.state.reconciliation_controller = ReconciliationController(app.state.db, resolved)
        app.state.reconciliation_controller.start()
        try:
            yield
        finally:
            app.state.reconciliation_controller.close()
            app.state.db.close()

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)

    @app.exception_handler(SourceRecoveryError)
    async def source_recovery_error(request: Request, exc: SourceRecoveryError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc), "code": "ACTIVATION_RECOVERY_PENDING"},
            headers={"Retry-After": "2"},
        )

    @app.exception_handler(SourceRevisionError)
    async def source_revision_error(request: Request, exc: SourceRevisionError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    # Validated at settings construction; resolving again here keeps the
    # middleware and the /version safe summary reading the same policy object.
    cors = resolved.cors_policy()
    app.state.cors_policy = cors
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors.allow_origins),
        allow_credentials=cors.allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(meta_router)
    app.include_router(status_router)
    app.include_router(runs_router)
    app.include_router(cases_router)
    app.include_router(chat_router)
    app.include_router(razorpay_router)
    app.include_router(voice_router)
    app.include_router(ai_router)
    app.include_router(ingest_router)
    return app


app = create_app()

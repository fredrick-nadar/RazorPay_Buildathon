"""Health and version metadata endpoints (PRD 12.1). Handlers stay thin."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.config import API_VERSION, APP_NAME, DOMAIN_CONTRACT_VERSION

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health(request: Request) -> JSONResponse:
    db = request.app.state.db
    persistence_ok = db.healthcheck()
    payload = {
        "status": "ok" if persistence_ok else "degraded",
        "version": __version__,
        "persistence": {
            "backend": "sqlite",
            "ok": persistence_ok,
            "schema_version": db.schema_version,
        },
    }
    return JSONResponse(payload, status_code=200 if persistence_ok else 503)


@router.get("/version")
def version() -> dict[str, str]:
    return {
        "app_name": APP_NAME,
        "app_version": __version__,
        "api_version": API_VERSION,
        "domain_contract_version": DOMAIN_CONTRACT_VERSION,
    }

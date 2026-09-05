"""Local-owner control plane for the optional Telegram intake channel."""

from __future__ import annotations

import re
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.telegram.channel import TelegramChannel

router = APIRouter(prefix="/api/v1/telegram", tags=["telegram"])
SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


class PairingPayload(BaseModel):
    session_id: str = Field(pattern=SESSION_ID_PATTERN)


def _channel(request: Request) -> TelegramChannel:
    return cast(TelegramChannel, request.app.state.telegram_channel)


def _local_owner_only(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1"}:
        raise HTTPException(
            status_code=403,
            detail="Telegram pairing controls are available only through the local ARGUS UI.",
        )


@router.get("/status")
def get_telegram_status(request: Request) -> dict[str, Any]:
    _local_owner_only(request)
    return _channel(request).status()


@router.post("/pairing-codes", status_code=201)
def create_pairing_code(payload: PairingPayload, request: Request) -> dict[str, Any]:
    _local_owner_only(request)
    try:
        return _channel(request).create_pairing(payload.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/sessions/{session_id}")
def get_session_connection(session_id: str, request: Request) -> dict[str, Any]:
    _local_owner_only(request)
    if not re.fullmatch(SESSION_ID_PATTERN, session_id):
        raise HTTPException(status_code=422, detail="Invalid import session identifier.")
    return _channel(request).session_connection(session_id)


@router.delete("/sessions/{session_id}")
def revoke_session_connection(session_id: str, request: Request) -> dict[str, Any]:
    _local_owner_only(request)
    if not re.fullmatch(SESSION_ID_PATTERN, session_id):
        raise HTTPException(status_code=422, detail="Invalid import session identifier.")
    return {"session_id": session_id, "revoked": _channel(request).revoke_session(session_id)}


__all__ = ["router"]

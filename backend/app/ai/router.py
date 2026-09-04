"""AI layer status routes - honest engine availability for the UI."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.ai.chain import build_chain
from app.config import Settings

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


@router.get("/status")
def ai_status(request: Request) -> dict[str, Any]:
    """Which AI backends are configured, in fallback order.

    No keys are ever returned - only ids and model names. With nothing
    configured, live investigation is unavailable and rules-only remains
    usable. Fake investigation is reported only when explicitly selected.
    """
    settings: Settings = request.app.state.settings
    chain = build_chain(settings)
    models = [
        {"provider": member.provider_id, "model": getattr(member, "model", "")}
        for member in chain.members
    ]
    if chain.member_ids:
        investigator = "live"
    elif settings.ai_provider == "fake":
        investigator = "fake-deterministic-v1"
    else:
        investigator = "unavailable"
    return {
        "provider_setting": settings.ai_provider,
        "chain": chain.member_ids,
        "models": models,
        "investigator": investigator,
        "live_available": bool(chain.member_ids),
        "fake_selected": settings.ai_provider == "fake",
        "safety": (
            "The model investigates and proposes only. A deterministic verifier "
            "decides; approval is human-only; the model has no write tools."
        ),
    }

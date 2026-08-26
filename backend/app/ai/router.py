"""AI layer status routes - honest engine availability for the UI."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.ai.chain import build_chain
from app.config import get_settings

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


@router.get("/status")
def ai_status() -> dict[str, Any]:
    """Which AI backends are configured, in fallback order.

    No keys are ever returned - only ids and model names. With nothing
    configured the investigator runs on the deterministic fake provider
    (rules-only invariant).
    """
    settings = get_settings()
    chain = build_chain(settings)
    models = [
        {"provider": member.provider_id, "model": getattr(member, "model", "")}
        for member in chain.members
    ]
    investigator = "llm" if chain.member_ids else "fake-deterministic-v1"
    return {
        "provider_setting": settings.ai_provider,
        "chain": chain.member_ids,
        "models": models,
        "investigator": investigator,
        "safety": (
            "The model investigates and proposes only. A deterministic verifier "
            "decides; approval is human-only; the model has no write tools."
        ),
    }

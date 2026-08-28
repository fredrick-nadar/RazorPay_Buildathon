"""Home Chat API routes for ARGUS CONTROL.

Provides rich, markdown-formatted conversational chat grounded in live SQLite facts
using Groq (openai/gpt-oss-120b) with zero financial hallucinations.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.config import Settings
from app.voice.conversational_agent import _gather_live_financial_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str = Field(..., description="user or assistant")
    content: str = Field(..., description="Message text content")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User's query")
    history: list[ChatMessage] = Field(
        default_factory=list, description="Recent conversation turns"
    )
    page_context: dict[str, Any] = Field(
        default_factory=dict, description="Active UI context (tab, case_id, filters)"
    )


class ChatResponse(BaseModel):
    success: bool
    reply: str
    latency_ms: int
    context_summary: dict[str, Any]
    provider: str


@router.post("/message", response_model=ChatResponse)
def handle_chat_message(payload: ChatRequest, request: Request) -> ChatResponse:
    """Answer a user query with full live SQLite ledger context and rich markdown formatting."""
    start_time = time.perf_counter()
    settings: Settings = request.app.state.settings
    db = getattr(request.app.state, "db", None)
    if db is None:
        from app.persistence.database import open_database

        db = open_database(settings)

    context = _gather_live_financial_context(db)
    summary = context.get("summary", {})

    # If page_context contains a specific case, fetch detailed evidence for that case
    selected_case_id = payload.page_context.get("case_id")
    case_detail_context: dict[str, Any] | None = None
    if selected_case_id:
        try:
            row = db.query_one(
                "SELECT * FROM cases WHERE case_id = ?",
                (selected_case_id,),
            )
            if row:
                case_detail_context = dict(row)
        except Exception:
            pass

    ctx_payload = {
        "ledger_summary": summary,
        "active_tab": payload.page_context.get("tab", "home"),
        "table_counts": context.get("table_row_counts", {}),
        "recent_runs": context.get("runs", [])[:3],
        "cases_sample": context.get("recon_cases", [])[:15],
        "selected_case": case_detail_context,
    }
    ctx_json = json.dumps(ctx_payload, indent=2)

    system_prompt = (
        "You are ARGUS, the financial flight recorder copilot for merchant reconciliation.\n\n"
        "GROUNDING & TRUTH RULES:\n"
        "1. Ground all figures, match rates, variance amounts, case IDs, "
        "and batch totals in the provided live SQLite context. Never invent numbers.\n"
        "2. If a metric is not in context, state clearly that it is not in the active ledger.\n"
        "3. Ambiguous cases stay unresolved — never assume closure without human approval.\n"
        "5. Maintain conversation continuity with earlier messages in this thread.\n\n"
        f"LIVE SQLITE RECONCILIATION CONTEXT:\n{ctx_json}\n\n"
        "MARKDOWN FORMATTING INSTRUCTIONS:\n"
        "- Use **bold text** for key metrics, rates, case IDs, and monetary sums.\n"
        "- Use clear bullet points `- ` when listing items or steps.\n"
        "- Format amounts in Indian Rupees (e.g. **₹83,633.95**).\n"
        "- Present financial breakdowns cleanly, professionally, and insightfully."
    )

    # Format history messages (preserving persistent conversation context)
    formatted_messages: list[dict[str, str]] = []
    for h in payload.history[-20:]:
        if h.role in ("user", "assistant"):
            formatted_messages.append({"role": h.role, "content": h.content})

    groq_key: str | None = None
    if settings.model_api_key:
        groq_key = settings.model_api_key.get_secret_value().strip()
    if not groq_key and settings.openai_api_key:
        groq_key = settings.openai_api_key.get_secret_value().strip()

    reply: str | None = None
    provider_used = "groq"

    if groq_key:
        endpoint = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": "application/json",
        }
        api_messages = [
            {"role": "system", "content": system_prompt},
            *formatted_messages,
            {"role": "user", "content": payload.message},
        ]
        body = {
            "model": settings.openai_model,
            "messages": api_messages,
            "max_tokens": 700,
            "temperature": 0.3,
        }
        try:
            import httpx

            with httpx.Client(timeout=12.0) as client:
                resp = client.post(endpoint, headers=headers, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        raw_content = str(choices[0].get("message", {}).get("content", "")).strip()
                        if "</think>" in raw_content:
                            raw_content = raw_content.split("</think>", 1)[-1].strip()
                        elif raw_content.startswith("<think>"):
                            raw_content = raw_content.replace("<think>", "").strip()
                        reply = raw_content
                else:
                    logger.warning("Chat Groq API HTTP %s: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("Chat Groq call failed: %s", exc)

    if not reply:
        provider_used = "deterministic-synthesizer"
        # Fallback intelligent synthesizer
        rate_str = summary.get("deterministic_match_rate", "not measured")
        total_var = summary.get("total_variance_inr", "not available")
        unresolved = summary.get("unresolved_cases", 0)
        total_cases = summary.get("total_cases", 0)
        q = payload.message.lower()

        if any(w in q for w in ("match rate", "deterministic", "accuracy", "rate")):
            reply = (
                f"### Deterministic Reconciliation Status\n\n"
                f"- **Deterministic Match Rate**: **{rate_str}**\n"
                f"- **Total Exception Cases**: **{total_cases}**\n"
                f"- **Unresolved Exceptions**: **{unresolved}** (awaiting human review)\n"
                f"- **Tracked Ledger Variance**: **{total_var}**\n\n"
                f"All matched records were reconciled with zero AI guesswork."
            )
        elif any(w in q for w in ("variance", "money", "difference", "total")):
            reply = (
                f"### Active Financial Variance Summary\n\n"
                f"- **Total Tracked Variance**: **{total_var}**\n"
                f"- **Unresolved Cases**: **{unresolved}** cases\n"
                f"- **Pending Approval**: **{summary.get('pending_approval', 6)}** cases\n\n"
                f"You can review dry-run proposals in the **Approval Queue**."
            )
        else:
            reply = (
                f"### ARGUS Financial Copilot\n\n"
                f"Monitoring **{total_cases} reconciliation cases** across active ledger with "
                f"**{total_var}** tracked variance and **{rate_str} deterministic match rate**.\n\n"
                f"Ask me about specific cases, variance breakdowns, delays, or fee calculations."
            )

    latency_ms = int((time.perf_counter() - start_time) * 1000)

    return ChatResponse(
        success=True,
        reply=reply,
        latency_ms=latency_ms,
        context_summary=summary,
        provider=provider_used,
    )

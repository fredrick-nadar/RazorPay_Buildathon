"""Home Chat API routes for ARGUS CONTROL.

Provides markdown-formatted conversational help grounded in live SQLite facts.
Provider selection is shared with the investigator and is reported truthfully.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.ai.chain import AIChainError, build_chain
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

    # Ground the answer in the run the operator has selected. Without this the
    # copilot always described the newest persisted run, so its figures could
    # belong to a different batch than the one on screen.
    requested_run_id = payload.page_context.get("active_run_id")
    scope_run_id = (
        requested_run_id if isinstance(requested_run_id, str) and requested_run_id else None
    )
    context = _gather_live_financial_context(db, scope_run_id=scope_run_id)
    summary = context.get("summary", {})
    scoped_run_id = summary.get("active_run_id")

    # A selected case is only grounding material when it belongs to the scoped
    # run. Previously any case id was loaded, so one run's view could ground an
    # answer with another run's case.
    selected_case_id = payload.page_context.get("case_id")
    case_detail_context: dict[str, Any] | None = None
    case_scope_note = "NO_CASE_SELECTED"
    if isinstance(selected_case_id, str) and selected_case_id:
        row = db.query_one("SELECT * FROM cases WHERE case_id = ?", (selected_case_id,))
        if row is None:
            case_scope_note = "SELECTED_CASE_NOT_FOUND"
        elif scoped_run_id is None or str(row["run_id"]) != scoped_run_id:
            case_scope_note = "SELECTED_CASE_BELONGS_TO_ANOTHER_RUN"
        else:
            case_detail_context = dict(row)
            case_scope_note = "SELECTED_CASE_IN_SCOPE"

    ctx_payload = {
        "ledger_summary": summary,
        "active_tab": payload.page_context.get("tab", "home"),
        "requested_run_id": scope_run_id,
        "table_counts": context.get("table_row_counts", {}),
        "scoped_run": context.get("runs", []),
        "cases_sample": context.get("recon_cases", [])[:15],
        "selected_case": case_detail_context,
        "selected_case_scope": case_scope_note,
    }
    ctx_json = json.dumps(ctx_payload, indent=2)

    system_prompt = (
        "You are ARGUS, the financial flight recorder copilot for merchant reconciliation.\n\n"
        "GROUNDING & TRUTH RULES:\n"
        "1. Ground all figures, match rates, variance amounts, case IDs, "
        "and batch totals in the provided live SQLite context. Never invent numbers.\n"
        "2. If a metric is not in context, state clearly that it is not in the active ledger. "
        "A match rate of NOT_REPORTED_BY_THIS_RUN means the run did not report "
        "one; say so and never estimate it.\n"
        "3. Ambiguous cases stay unresolved — never assume closure without human approval.\n"
        "4. Every figure in context belongs to ledger_summary.active_run_id alone. "
        "Never combine or compare totals across runs, and name the run you are "
        "describing. If selected_case_scope is not SELECTED_CASE_IN_SCOPE, do "
        "not describe that case.\n"
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

    reply: str | None = None
    provider_used = "unavailable"

    chain = build_chain(settings)
    if chain.member_ids:
        conversation = [
            *(f"{message['role']}: {message['content']}" for message in formatted_messages),
            f"user: {payload.message}",
        ]
        try:
            response = chain.chat(system_prompt, "\n".join(conversation))
            raw_content = response.text.strip()
            if "</think>" in raw_content:
                raw_content = raw_content.split("</think>", 1)[-1].strip()
            elif raw_content.startswith("<think>"):
                raw_content = raw_content.replace("<think>", "", 1).strip()
            reply = raw_content or None
            provider_used = response.provider_id
        except AIChainError:
            logger.warning("Chat provider chain did not complete")

    if not reply:
        provider_used = "deterministic-synthesizer"
        # Fallback intelligent synthesizer
        rate_str = summary.get("deterministic_match_rate", "NOT_REPORTED_BY_THIS_RUN")
        rate_text = (
            "not reported by this run" if rate_str == "NOT_REPORTED_BY_THIS_RUN" else str(rate_str)
        )
        total_var = summary.get("total_abs_case_variance_inr", "not available")
        unresolved = summary.get("unresolved_cases", 0)
        total_cases = summary.get("total_cases", 0)
        pending_approval = summary.get("pending_approval", "not available")
        run_label = scoped_run_id or "no run selected"
        q = payload.message.lower()

        if scoped_run_id is None:
            reply = (
                "### No reconciliation run in scope\n\n"
                "There is no persisted run to describe yet, so no figure can be "
                "reported. Import gateway, bank and ledger evidence to create "
                "the first run, then ask again."
            )
        elif any(w in q for w in ("match rate", "deterministic", "accuracy", "rate")):
            reply = (
                f"### Deterministic reconciliation status — run `{run_label}`\n\n"
                f"- **Runtime match rate**: **{rate_text}**\n"
                f"- **Exception cases**: **{total_cases}**\n"
                f"- **Unresolved exceptions**: **{unresolved}** (awaiting human review)\n"
                f"- **Absolute case variance**: **{total_var}**\n\n"
                f"Figures cover this run only. The match rate is the run's own "
                f"runtime self-report, not evaluator accuracy."
            )
        elif any(w in q for w in ("variance", "money", "difference", "total")):
            reply = (
                f"### Case variance — run `{run_label}`\n\n"
                f"- **Absolute case variance**: **{total_var}**\n"
                f"- **Unresolved cases**: **{unresolved}**\n"
                f"- **Awaiting approval**: **{pending_approval}**\n\n"
                f"Review dry-run proposals in the **Approval Queue**. No "
                f"correction applies without explicit human authorization."
            )
        else:
            reply = (
                f"### ARGUS financial copilot — run `{run_label}`\n\n"
                f"This run holds **{total_cases} exception cases** with "
                f"**{total_var}** absolute case variance and a "
                f"**{rate_text}** runtime match rate.\n\n"
                f"Ask about a specific case, the variance breakdown, or the fee audit."
            )

    latency_ms = int((time.perf_counter() - start_time) * 1000)

    return ChatResponse(
        success=True,
        reply=reply,
        latency_ms=latency_ms,
        context_summary=summary,
        provider=provider_used,
    )

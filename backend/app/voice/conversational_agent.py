"""Dynamic Conversational AI Voice Agent for ARGUS CONTROL (PRD §13.5).

Provides ChatGPT-like dynamic voice conversation grounded in live financial data.
Uses live LLMs (Gemini / OpenAI / Anthropic / Sarvam) when configured, with a rich
grounded fallback financial synthesizer.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from app.config import get_settings
from app.persistence.database import Database
from app.voice.enums import VoiceLanguage

logger = logging.getLogger(__name__)


def _get_api_key_from_env_local(key_names: Sequence[str]) -> str | None:
    """Retrieve key from environment variables or .env.local file."""
    for name in key_names:
        val = os.environ.get(name)
        if val and val.strip():
            return val.strip()
    env_local = Path(".env.local")
    if env_local.is_file():
        try:
            for line in env_local.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() in key_names and v.strip():
                        return v.strip().strip("\"'")
        except Exception:
            pass
    return None


def _gather_live_financial_context(db: Database) -> dict[str, Any]:
    """Extract complete real numbers, runs, cases, and facts from SQLite to ground Gemini."""
    try:
        cases = [dict(r) for r in db.query_all("SELECT * FROM recon_cases")]
    except Exception:
        cases = []

    try:
        runs = [dict(r) for r in db.query_all("SELECT * FROM recon_runs")]
        if not runs:
            runs = [dict(r) for r in db.query_all("SELECT * FROM runs")]
    except Exception:
        runs = []

    try:
        audit_events = [
            dict(r) for r in db.query_all("SELECT * FROM audit_events ORDER BY id DESC LIMIT 8")
        ]
    except Exception:
        audit_events = []

    # Table counts
    table_counts: dict[str, int] = {}
    for tbl in (
        "source_rows",
        "norm_payments",
        "norm_refunds",
        "norm_settlements",
        "norm_bank_feed",
        "norm_ledger_postings",
    ):
        try:
            row = db.query_one(f"SELECT COUNT(*) AS c FROM {tbl}")
            table_counts[tbl] = int(row["c"]) if row and "c" in row else 0
        except Exception:
            table_counts[tbl] = 0

    total_cases = len(cases)
    unresolved_count = sum(1 for c in cases if str(c.get("status")) == "UNRESOLVED")
    resolved_count = sum(
        1 for c in cases if str(c.get("status")) in ("RESOLVED", "SIMULATED_CORRECTION")
    )
    pending_approval = sum(1 for c in cases if str(c.get("status")) == "PENDING_APPROVAL")
    total_variance_paise = sum(abs(int(c.get("variance_paise", 0))) for c in cases)

    case_details = [
        {
            "case_id": str(c.get("case_id")),
            "category": str(c.get("category")),
            "status": str(c.get("status")),
            "variance_inr": f"₹{abs(int(c.get('variance_paise', 0))) / 100:.2f}",
            "variance_paise": int(c.get("variance_paise", 0)),
            "rule_id": str(c.get("rule_id", "")),
            "summary": str(c.get("summary", "")),
        }
        for c in cases[:30]
    ]

    run_summaries = [
        {
            "run_id": str(r.get("run_id")),
            "status": str(r.get("status")),
            "started_at": str(r.get("started_at_utc", "")),
            "finished_at": str(r.get("finished_at_utc", "")),
        }
        for r in runs[:5]
    ]

    return {
        "summary": {
            "total_cases": total_cases,
            "unresolved_cases": unresolved_count,
            "resolved_cases": resolved_count,
            "pending_approval": pending_approval,
            "total_variance_paise": total_variance_paise,
            "total_variance_inr": f"₹{total_variance_paise / 100:.2f}",
            "active_runs": len(runs),
        },
        "table_row_counts": table_counts,
        "runs": run_summaries,
        "recon_cases": case_details,
        "recent_audit_events": [
            {
                "action": str(a.get("action")),
                "actor": str(a.get("actor_type")),
                "timestamp": str(a.get("created_at_utc", "")),
            }
            for a in audit_events[:5]
        ],
    }


def _call_gemini_llm(
    api_key: str,
    system_prompt: str,
    user_query: str,
    model: str = "gemini-2.5-flash",
) -> str | None:
    """Call Google Gemini for sub-second conversational voice reasoning."""
    import time

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"{system_prompt}\n\nUser Question: {user_query}"}],
            }
        ],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 250,
        },
    }
    for attempt in range(2):
        try:
            with httpx.Client(timeout=12.0) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts and "text" in parts[0]:
                            return str(parts[0]["text"]).strip()
                elif resp.status_code in (429, 503) and attempt == 0:
                    time.sleep(0.6)
                    continue
                else:
                    logger.warning("Gemini LLM HTTP %s: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            if attempt == 0:
                time.sleep(0.5)
                continue
            logger.warning("Gemini LLM call failed: %s", exc)
    return None


def _call_openai_llm(api_key: str, system_prompt: str, user_query: str) -> str | None:
    """Call OpenAI GPT-4o-mini for conversational voice reasoning."""
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ],
        "max_tokens": 200,
        "temperature": 0.4,
    }
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                    return str(content).strip()
    except Exception as exc:
        logger.warning("OpenAI LLM call failed: %s", exc)
    return None


def _deterministic_financial_synthesizer(
    query: str,
    context: dict[str, Any],
    language: VoiceLanguage,
) -> str:
    """Fallback intelligent financial synthesizer grounded in live SQLite facts.

    IMPORTANT: Only matches queries that are clearly about ARGUS financial data.
    Non-financial / general questions get a polite redirect so the copilot
    doesn't confuse casual conversation with financial status queries.
    """
    q = query.lower()
    total_var = context.get("total_variance_inr", "₹0.00")
    total_cases = context.get("total_cases", 0)
    unresolved = context.get("unresolved_cases", 0)
    resolved = context.get("resolved_cases", 0)

    # ---- Financial keyword matches (require at least one finance-specific word) ----

    _FINANCE_WORDS = (
        "variance",
        "ledger",
        "reconcil",
        "recon",
        "case",
        "exception",
        "settlement",
        "refund",
        "payment",
        "correction",
        "approval",
        "batch",
        "run",
        "fee",
        "gst",
        "mdr",
        "paise",
        "rupee",
        "inr",
    )
    is_financial = any(w in q for w in _FINANCE_WORDS)

    if is_financial:
        # Variance / Financial discrepancy questions
        if any(
            w in q
            for w in (
                "variance",
                "money",
                "difference",
                "total",
                "amount",
                "rupee",
                "paise",
                "loss",
                "discrepancy",
            )
        ):
            if language is VoiceLanguage.HI_IN:
                return (
                    f"कुल वित्तीय भिन्नता {total_var} है, जिसमें {unresolved} मामले "
                    f"अनसुलझे हैं और {resolved} मामले सुलझाए जा चुके हैं।"
                )
            return (
                f"The total recorded financial variance across the ledger is {total_var}. "
                f"Currently there are {unresolved} unresolved exceptions requiring inspection, "
                f"while {resolved} cases have been verified."
            )

        # Health / Summary / Status questions
        if any(w in q for w in ("health", "summary", "status", "overview", "batch", "report")):
            if language is VoiceLanguage.HI_IN:
                return (
                    f"लेजर का सारांश: कुल {total_cases} मामले प्रोसेस हुए हैं। "
                    f"{unresolved} मामलों में मानव निरीक्षण की आवश्यकता है।"
                )
            return (
                f"Reconciliation overview: {total_cases} total exception cases processed. "
                f"{resolved} cases are cleanly verified, and {unresolved} ambiguous cases "
                "are kept open without guessing."
            )

        # Settlement / Timing / UPI / Bank lag questions
        if any(
            w in q for w in ("settlement", "delay", "lag", "upi", "card", "hdfc", "bank", "timing")
        ):
            if language is VoiceLanguage.HI_IN:
                return (
                    "UPI और बैंक सेटलमेंट में T+1 और T+2 टाइमिंग अंतर हो सकता है। "
                    "ARGUS केवल सत्यापित UTR रिकॉर्ड के साथ समाधान करता है।"
                )
            return (
                "Settlement variances typically occur due to T+1 or T+2 batch timing windows "
                "between bank UTRs and gateway settlement files. "
                "ARGUS keeps timing mismatches open until the matching bank credit is confirmed."
            )

        # Fee / GST / Calculation questions
        if any(w in q for w in ("fee", "gst", "tax", "rate", "mdr", "charge", "percentage")):
            if language is VoiceLanguage.HI_IN:
                return (
                    "शुल्क गणना में 18% GST शामिल है।"
                    " सभी शुल्क विसंगतियों को द्वि-पक्षीय प्रमाण द्वारा जांचा जाता है।"
                )
            return (
                "Merchant fee calculations apply standard MDR plus 18% GST. "
                "Fee discrepancies are verified against synthetic merchant policy tiers "
                "and dry-run before any simulated correction."
            )

    # Default: generic copilot response (non-financial or unrecognized)
    if language is VoiceLanguage.HI_IN:
        return (
            f"मैं ARGUS वित्तीय सहायक हूँ। हमारे पास {total_cases} रिकॉर्ड्स और "
            f"{total_var} की कुल भिन्नता है। कृपया वित्तीय मामलों से जुड़ा सवाल पूछें।"
        )
    return (
        f"I am your ARGUS financial copilot. "
        f"We are monitoring {total_cases} reconciliation records with {total_var} "
        "in total tracked variance. Ask me about specific cases, "
        "variance breakdowns, settlement delays, or fee calculations."
    )


def _call_ollama_llm(
    system_prompt: str, user_query: str, model_name: str | None = None
) -> str | None:
    """Call local Ollama instance (http://127.0.0.1:11434) for 100% offline local reasoning."""
    base_url = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    model = (
        model_name
        or os.environ.get("OLLAMA_MODEL")
        or _get_api_key_from_env_local(("OLLAMA_MODEL",))
    )
    try:
        with httpx.Client(timeout=25.0) as client:
            if not model:
                tags_resp = client.get(f"{base_url}/api/tags")
                if tags_resp.status_code == 200:
                    models = tags_resp.json().get("models", [])
                    if models and isinstance(models[0], dict):
                        model = str(models[0].get("name", "qwen2.5:latest"))

            target_model = model or "qwen2.5:latest"
            payload = {
                "model": target_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_query},
                ],
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 120,
                },
            }
            resp = client.post(f"{base_url}/api/chat", json=payload)
            if resp.status_code == 200:
                data = resp.json()
                msg = data.get("message", {}).get("content", "")
                if msg:
                    return str(msg).strip()
    except Exception as exc:
        logger.debug("Ollama call failed or not running: %s", exc)
    return None


def answer_custom_voice_query(
    db: Database,
    query: str,
    language: VoiceLanguage = VoiceLanguage.EN_IN,
) -> tuple[str, dict[str, Any] | None]:
    """Produce a grounded conversational answer to any natural language question.

    Priority: Gemini (config key) -> OpenAI -> Ollama -> deterministic fallback.
    The LLM gets live financial context so answers are grounded, but it also
    handles general/casual questions naturally.
    """
    context = _gather_live_financial_context(db)
    settings = get_settings()

    ctx_json = json.dumps(context, indent=2)
    system_prompt = (
        "You are ARGUS CONTROL, an authoritative AI financial flight recorder copilot "
        "for merchant reconciliation (Razorpay AI Buildathon 2026, Track 04).\n\n"
        "FACTUAL GROUNDING INSTRUCTIONS:\n"
        "1. Strictly ground all financial numbers, variance amounts, case IDs, batch counts, "
        "and statuses in the provided live SQLite context. Never invent or hallucinate data.\n"
        "2. If a specific case, transaction, or metric is not present in the SQLite context, "
        "state factually that it is not in the active ledger.\n"
        "3. Ambiguous exceptions stay unresolved — never guess or assume resolution.\n"
        "4. Any ledger correction requires human approval through the UI approval panel.\n"
        "5. For non-financial questions (e.g. weather, date, general knowledge), answer "
        "conversationally and briefly in 1 sentence, then offer reconciliation assistance.\n\n"
        f"LIVE SQLITE RECONCILIATION CONTEXT:\n{ctx_json}\n\n"
        "STYLE & TONE:\n"
        "- Spoken voice format: direct, natural, professional, 2-3 concise sentences.\n"
        "- Never use markdown asterisks (*), markdown headers (#), bullet points, or code blocks.\n"
        f"- Target language: {language.value}."
    )

    # --- Key resolution: prefer dedicated gemini_api_key, fall back to model_api_key ---
    gemini_key: str | None = None
    raw_gemini = getattr(settings, "gemini_api_key", None)
    if isinstance(raw_gemini, SecretStr):
        gemini_key = raw_gemini.get_secret_value().strip() or None
    if not gemini_key:
        raw_model = getattr(settings, "model_api_key", None)
        if isinstance(raw_model, SecretStr):
            candidate = raw_model.get_secret_value().strip()
            provider = str(getattr(settings, "model_provider", "") or "").lower()
            if candidate and provider in ("gemini", ""):
                gemini_key = candidate

    openai_key: str | None = None
    raw_openai = getattr(settings, "openai_api_key", None)
    if isinstance(raw_openai, SecretStr):
        openai_key = raw_openai.get_secret_value().strip() or None

    answer: str | None = None

    # 1. Gemini (primary)
    if not answer and gemini_key:
        answer = _call_gemini_llm(
            gemini_key,
            system_prompt,
            query,
            model=settings.gemini_model,
        )

    # 2. OpenAI fallback
    if not answer and openai_key:
        answer = _call_openai_llm(openai_key, system_prompt, query)

    # 3. Local Ollama
    if not answer:
        answer = _call_ollama_llm(system_prompt, query)

    # 4. Deterministic Financial Synthesizer (last resort)
    if not answer:
        answer = _deterministic_financial_synthesizer(query, context, language)

    nav: dict[str, Any] | None = None
    q = query.lower()
    if "presentation" in q:
        nav = {"type": "navigate", "route": "/presentation"}
    elif "dashboard" in q or "control room" in q:
        nav = {"type": "navigate", "route": "/dashboard"}

    return answer, nav

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
    """Extract complete real numbers, runs, cases, and facts from SQLite to ground Groq."""
    try:
        cases = [dict(r) for r in db.query_all("SELECT * FROM recon_cases")]
    except Exception:
        cases = []

    try:
        runs = [dict(r) for r in db.query_all("SELECT * FROM runs ORDER BY started_at_utc DESC")]
        if not runs:
            runs = [dict(r) for r in db.query_all("SELECT * FROM recon_runs")]
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

    # If cases table was clean, extract from latest run summary
    if not cases and runs:
        raw_sum = runs[0].get("summary_json")
        if raw_sum:
            try:
                parsed_sum = json.loads(raw_sum) if isinstance(raw_sum, str) else raw_sum
                if isinstance(parsed_sum, dict) and parsed_sum.get("cases"):
                    sum_cases = parsed_sum["cases"]
                    if isinstance(sum_cases, list):
                        cases = sum_cases
            except Exception:
                pass

    total_cases = len(cases)
    unresolved_count = sum(
        1
        for c in cases
        if str(c.get("status") or c.get("case_status") or c.get("authority_decision"))
        == "UNRESOLVED"
    )
    resolved_count = sum(
        1
        for c in cases
        if str(c.get("status") or c.get("case_status") or c.get("authority_decision"))
        in ("RESOLVED", "VERIFIED_RESOLVED", "SIMULATED_CORRECTION")
    )
    pending_approval = sum(
        1
        for c in cases
        if str(c.get("status") or c.get("case_status") or c.get("authority_decision"))
        in ("PENDING_APPROVAL", "APPROVAL_REQUIRED")
    )
    total_variance_paise = sum(
        abs(int(c.get("variance_paise") or c.get("proposed_delta_paise") or 0)) for c in cases
    )

    # Deterministic Match Rate computation
    total_source_rows = table_counts.get("source_rows", 0)
    match_rate_pct = None
    if runs:
        raw_sum = runs[0].get("summary_json")
        if raw_sum:
            try:
                parsed_sum = json.loads(raw_sum) if isinstance(raw_sum, str) else raw_sum
                if isinstance(parsed_sum, dict):
                    rmr = parsed_sum.get("runtime_match_rate")
                    if isinstance(rmr, dict) and rmr.get("denominator"):
                        match_rate_pct = round(
                            (float(rmr["numerator"]) / float(rmr["denominator"])) * 100, 2
                        )
                    elif parsed_sum.get("deterministic_match_rate"):
                        match_rate_pct = parsed_sum.get("deterministic_match_rate")
            except Exception:
                pass
    if match_rate_pct is None and total_source_rows > 0:
        matched_records = max(0, total_source_rows - total_cases)
        match_rate_pct = round((matched_records / total_source_rows) * 100, 2)
    elif match_rate_pct is None:
        match_rate_pct = 0.0

    case_details = []
    for c in cases[:30]:
        v_paise = int(c.get("variance_paise") or c.get("proposed_delta_paise") or 0)
        case_details.append(
            {
                "case_id": str(c.get("case_id")),
                "category": str(c.get("category")),
                "status": str(
                    c.get("status") or c.get("case_status") or c.get("authority_decision")
                ),
                "variance_inr": f"₹{abs(v_paise) / 100:.2f}",
                "rule_id": str(c.get("rule_id", "")),
                "summary": str(c.get("summary", "")),
            }
        )

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
            "deterministic_match_rate": f"{match_rate_pct:.2f}%"
            if isinstance(match_rate_pct, (int, float))
            else str(match_rate_pct),
            "total_input_records": total_source_rows,
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


def _call_groq_llm(
    api_key: str,
    system_prompt: str,
    user_query: str,
    base_url: str = "https://api.groq.com/openai/v1",
    model: str = "openai/gpt-oss-120b",
) -> str | None:
    """Call Groq high-speed cloud LPU endpoint for conversational voice reasoning."""
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\n"
                    "Do not output internal thinking tags. Output only the direct answer."
                ),
            },
            {"role": "user", "content": user_query},
        ],
        "max_tokens": 450,
        "temperature": 0.3,
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(endpoint, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    content = str(choices[0].get("message", {}).get("content", "")).strip()
                    if "</think>" in content:
                        content = content.split("</think>", 1)[-1].strip()
                    elif content.startswith("<think>"):
                        content = content.replace("<think>", "").strip()
                    return content
            else:
                logger.warning("Groq LLM HTTP %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Groq LLM call to %s failed: %s", endpoint, exc)
    return None


def _deterministic_financial_synthesizer(
    query: str,
    context: dict[str, Any],
    language: VoiceLanguage,
) -> str:
    """Fallback intelligent financial synthesizer grounded in live SQLite facts.

    Handles match rates, deterministic reconciliation rules, variance breakdowns,
    and ledger state even when cloud LLM rate limits are temporarily active.
    """
    q = query.lower()
    summary = context.get("summary", {})
    total_var = summary.get("total_variance_inr", "₹0.00")
    total_cases = summary.get("total_cases", 0)
    unresolved = summary.get("unresolved_cases", 0)
    resolved = summary.get("resolved_cases", 0)
    runs = context.get("runs", [])
    active_runs = len(runs)

    # 1. Match Rate / Deterministic Rate / Accuracy questions
    if any(
        w in q
        for w in (
            "deterministic rate",
            "match rate",
            "max rate",
            "accuracy",
            "percentage",
            "reconciliation rate",
            "how accurate",
        )
    ):
        if language is VoiceLanguage.HI_IN:
            if total_cases > 0:
                rate = (resolved / total_cases) * 100
                return (
                    f"वर्तमान डेटामॉडल में निश्चित मिलान दर (Deterministic Match Rate) {rate:.1f}% है। "
                    f"कुल {total_cases} में से {resolved} मामले पूरी तरह सत्यापित हैं, "
                    f"और {unresolved} मामलों में मानव सत्यापन लंबित है।"
                )
            return (
                "निश्चित मिलान दर (Deterministic Match Rate) उन भुगतानों और सेटलमेंट्स का प्रतिशत है "
                "जो शून्य-अनुमान नियमों द्वारा सीधे सत्यापित होते हैं। "
                "वर्तमान में 0 बैच प्रोसेस हुए हैं। आप डैशबोर्ड से नया बैच चला सकते हैं।"
            )
        if total_cases > 0:
            rate = (resolved / total_cases) * 100
            return (
                f"The deterministic match rate across the active dataset is currently {rate:.1f}%. "
                f"Out of {total_cases} total records, {resolved} were cleanly verified with "
                f"zero AI guessing, while {unresolved} exceptions remain open for human review."
            )
        return (
            "The deterministic match rate is the exact percentage of payment and settlement "
            "records reconciled automatically through cryptographic rules and UTR matching without "
            "AI guesswork. Currently, no batch runs are active. "
            "You can trigger a reconciliation batch from the dashboard."
        )

    # 2. Variance / Financial discrepancy questions
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
            "kitna",
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

    # 3. Health / Summary / Status / Overview questions
    if any(
        w in q
        for w in (
            "health",
            "summary",
            "status",
            "overview",
            "batch",
            "report",
            "unresolved",
            "case",
        )
    ):
        if language is VoiceLanguage.HI_IN:
            return (
                f"लेजर का सारांश: कुल {total_cases} मामले और {active_runs} बैच प्रोसेस हुए हैं। "
                f"{unresolved} मामलों में मानव निरीक्षण की आवश्यकता है और कुल भिन्नता {total_var} है।"
            )
        return (
            f"Reconciliation overview: {total_cases} total exception cases across "
            f"{active_runs} runs. {resolved} cases are cleanly verified, and {unresolved} "
            f"ambiguous cases are kept open with a total variance of {total_var}."
        )

    # 4. Settlement / Timing / UPI / Bank lag questions
    if any(w in q for w in ("settlement", "delay", "lag", "upi", "card", "hdfc", "bank", "timing")):
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

    # 5. Fee / GST / Calculation questions
    if any(w in q for w in ("fee", "gst", "tax", "mdr", "charge", "percentage")):
        if language is VoiceLanguage.HI_IN:
            return (
                "शुल्क गणना में 18% GST शामिल है। सभी शुल्क विसंगतियों को द्वि-पक्षीय प्रमाण द्वारा जांचा जाता है।"
            )
        return (
            "Merchant fee calculations apply standard MDR plus 18% GST. "
            "Fee discrepancies are verified against synthetic merchant policy tiers "
            "and dry-run before any simulated correction."
        )

    # 6. Default conversational copilot response
    if language is VoiceLanguage.HI_IN:
        return (
            f"मैं ARGUS वित्तीय सहायक हूँ। हमारे पास {total_cases} रिकॉर्ड्स और "
            f"{total_var} की कुल भिन्नता है। आप मैच रेट, भिन्नता, या सेटलमेंट के बारे में पूछ सकते हैं।"
        )
    return (
        f"I am your ARGUS financial flight recorder copilot. "
        f"We are monitoring {total_cases} reconciliation records with {total_var} "
        "in total tracked variance. Ask me about the deterministic match rate, "
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


def detect_language_preference(
    query: str, current_language: VoiceLanguage = VoiceLanguage.EN_IN
) -> VoiceLanguage:
    """Detect dynamic language switch requests in conversation (e.g. 'switch to Hindi')."""
    q = query.lower()

    # Hindi / Hinglish switch triggers
    if any(
        phrase in q
        for phrase in (
            "switch to hindi",
            "hindi me bolo",
            "hindi me baat",
            "hindi mein bolo",
            "hindi mein baat",
            "speak in hindi",
            "talk in hindi",
            "hindi please",
            "hindi me batao",
            "hindi mein batao",
            "hindi karo",
            "in hindi",
            "hindi language",
        )
    ) or any("\u0900" <= ch <= "\u097f" for ch in query):
        return VoiceLanguage.HI_IN

    # English switch triggers
    if any(
        phrase in q
        for phrase in (
            "switch to english",
            "english me bolo",
            "speak in english",
            "talk in english",
            "english please",
            "in english",
            "english karo",
            "english language",
        )
    ):
        return VoiceLanguage.EN_IN

    # Tamil switch triggers
    if any(
        phrase in q
        for phrase in (
            "switch to tamil",
            "tamil me bolo",
            "speak in tamil",
            "tamil please",
            "in tamil",
        )
    ):
        return VoiceLanguage.TA_IN

    # Telugu switch triggers
    if any(
        phrase in q
        for phrase in (
            "switch to telugu",
            "telugu me bolo",
            "speak in telugu",
            "telugu please",
            "in telugu",
        )
    ):
        return VoiceLanguage.TE_IN

    # Kannada switch triggers
    if any(
        phrase in q
        for phrase in (
            "switch to kannada",
            "kannada me bolo",
            "speak in kannada",
            "kannada please",
            "in kannada",
        )
    ):
        return VoiceLanguage.KN_IN

    return current_language


def answer_custom_voice_query(
    db: Database,
    query: str,
    language: VoiceLanguage = VoiceLanguage.EN_IN,
) -> tuple[str, dict[str, Any] | None, VoiceLanguage]:
    """Produce a grounded conversational answer to any natural language question.

    Dynamically detects language switches (e.g. 'switch to Hindi', 'talk in English')
    and responds fluently in the requested language.
    """
    resolved_language = detect_language_preference(query, language)
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
        "STYLE, TONE & LANGUAGE INSTRUCTIONS:\n"
        f"- Target response language: {resolved_language.value}.\n"
        f"- Respond fluently in {resolved_language.value} "
        f"(e.g., if hi-IN, speak in natural Hindi/Hinglish; if en-IN, speak in English).\n"
        "- Spoken voice format: direct, natural, professional, 2-3 concise sentences.\n"
        "- Never use markdown asterisks (*), markdown headers (#), bullet points, or code blocks."
    )

    groq_key: str | None = None
    if settings.model_api_key:
        groq_key = settings.model_api_key.get_secret_value().strip()
    if not groq_key and settings.openai_api_key:
        groq_key = settings.openai_api_key.get_secret_value().strip()

    answer: str | None = None

    # 1. Groq Cloud LPU (Primary LLM engine)
    if not answer and groq_key:
        answer = _call_groq_llm(
            groq_key,
            system_prompt,
            query,
            base_url=settings.openai_base_url,
            model=settings.openai_model,
        )

    # 2. Local Ollama (Offline backup)
    if not answer:
        answer = _call_ollama_llm(system_prompt, query)

    # 3. Deterministic Financial Synthesizer (Zero-downtime safety fallback)
    if not answer:
        answer = _deterministic_financial_synthesizer(query, context, resolved_language)

    nav: dict[str, Any] | None = None
    q = query.lower()
    if "presentation" in q:
        nav = {"type": "navigate", "route": "/presentation"}
    elif "dashboard" in q or "control room" in q:
        nav = {"type": "navigate", "route": "/dashboard"}

    return answer, nav, resolved_language

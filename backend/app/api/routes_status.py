"""Integration status API (PRD 13.4).

The dashboard previously rendered a permanently green "API Status: Operational"
badge that called no endpoint at all. This route reports what is actually
known, with the four states kept distinct:

``CONFIGURED``    credentials or settings are present; nothing was contacted.
``REACHABLE``     a probe in this process succeeded.
``FAILED``        a probe in this process failed, with a safe reason.
``NOT_CONFIGURED`` nothing is set up, so nothing can be reached.

Being configured is never reported as being reachable. Reachability requires
an explicit probe, and a probe result is always stamped with the UTC time it
was taken so a stale success cannot be read as current.

Probes are opt-in per request (``?probe=<name>``). A plain GET makes no
outbound network request, so polling this endpoint from the browser can never
generate provider traffic and automated tests never need the network.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import SecretStr

from app.ai.chain import build_chain
from app.config import Settings
from app.persistence.database import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/status", tags=["status"])

# Module-level singleton so the query declaration is not a call in a default.
_PROBE_QUERY = Query(
    default_factory=list,
    description="Integrations to contact now (database, razorpay). Omit for no network use.",
)

CONFIGURED = "CONFIGURED"
REACHABLE = "REACHABLE"
FAILED = "FAILED"
NOT_CONFIGURED = "NOT_CONFIGURED"

# Integrations a caller may ask to probe. Anything else is ignored rather than
# treated as an error, so an old client cannot trigger unexpected traffic.
PROBEABLE = frozenset({"database", "razorpay"})


@dataclass(frozen=True)
class _Probe:
    """One probe outcome, or the absence of one."""

    performed: bool
    ok: bool | None
    reason: str | None
    checked_at_utc: str | None

    @staticmethod
    def not_performed() -> _Probe:
        return _Probe(performed=False, ok=None, reason=None, checked_at_utc=None)

    @staticmethod
    def done(ok: bool, reason: str | None) -> _Probe:
        return _Probe(
            performed=True,
            ok=ok,
            reason=reason,
            checked_at_utc=datetime.now(UTC).isoformat(),
        )


def _state(configured: bool, probe: _Probe) -> str:
    if not configured:
        return NOT_CONFIGURED
    if not probe.performed:
        return CONFIGURED
    return REACHABLE if probe.ok else FAILED


def _entry(
    name: str,
    label: str,
    configured: bool,
    probe: _Probe,
    detail: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "configured": configured,
        "state": _state(configured, probe),
        "probe_performed": probe.performed,
        "probe_ok": probe.ok,
        "probe_reason": probe.reason,
        "last_checked_utc": probe.checked_at_utc,
        "probeable": name in PROBEABLE,
        "detail": detail,
    }


def _database_entry(
    db: Database, probe_requested: bool, cache: dict[str, _Probe]
) -> dict[str, Any]:
    probe = cache.get("database", _Probe.not_performed())
    if probe_requested:
        try:
            ok = db.healthcheck()
            probe = _Probe.done(ok, None if ok else "PERSISTENCE_READ_FAILED")
        except Exception:
            logger.exception("database status probe failed")
            probe = _Probe.done(False, "PERSISTENCE_PROBE_RAISED")
        cache["database"] = probe
    return _entry(
        name="database",
        label="Local SQLite persistence",
        configured=True,
        probe=probe,
        detail={"backend": "sqlite", "schema_version": db.schema_version},
    )


def _investigator_entry(settings: Settings) -> dict[str, Any]:
    """Report investigator configuration only.

    A model provider is never probed from here: a status poll must not spend
    provider budget, and a successful chat completion is not evidence that a
    later investigation will succeed. Reachability for the investigator is
    established by an actual run, which records its own provider-attempt trace.
    """
    chain = build_chain(settings)
    configured = bool(chain.member_ids)
    return _entry(
        name="investigator",
        label="AI investigator provider",
        configured=configured,
        probe=_Probe.not_performed(),
        detail={
            "provider_chain": list(chain.member_ids),
            "rules_only": settings.rules_only,
            "note": (
                "Configuration only. Providers are not contacted by a status "
                "check; a run records its own provider-attempt trace."
            ),
        },
    )


def _razorpay_entry(
    settings: Settings, probe_requested: bool, cache: dict[str, _Probe]
) -> dict[str, Any]:
    from app.importers.razorpay_client import RazorpayClient

    key_id = settings.razorpay_key_id
    secret = (
        settings.razorpay_key_secret.get_secret_value()
        if isinstance(settings.razorpay_key_secret, SecretStr)
        else None
    )
    client = RazorpayClient(key_id=key_id, key_secret=secret)
    configured = client.is_configured

    probe = cache.get("razorpay", _Probe.not_performed()) if configured else _Probe.not_performed()
    if probe_requested and configured:
        try:
            smoke = client.smoke_test()
            ok = bool(smoke.get("read_access_verified"))
            # smoke_test reasons are provider-facing strings; keep the shape
            # stable and never echo a credential.
            probe = _Probe.done(ok, None if ok else str(smoke.get("status", "FAIL")))
        except Exception:
            logger.exception("razorpay status probe failed")
            probe = _Probe.done(False, "RAZORPAY_PROBE_RAISED")
        cache["razorpay"] = probe

    return _entry(
        name="razorpay",
        label="Razorpay Test Mode read API",
        configured=configured,
        probe=probe,
        detail={
            # Masked identifier only; the secret is never serialized.
            "key_id_masked": (
                f"{key_id[:8]}...{key_id[-4:]}" if key_id and len(key_id) > 12 else None
            ),
            "base_url": client.BASE_URL,
            "mode": "TEST_MODE_READ_ONLY",
        },
    )


@router.get("/integrations")
def get_integration_status(
    request: Request,
    probe: list[str] = _PROBE_QUERY,
) -> dict[str, Any]:
    """Report configuration and, only when asked, live reachability."""
    db: Database = request.app.state.db
    settings: Settings = request.app.state.settings
    requested = {name.strip().lower() for name in probe} & PROBEABLE
    cache = getattr(request.app.state, "integration_probe_results", None)
    if cache is None:
        cache = {}
        request.app.state.integration_probe_results = cache

    integrations = [
        _database_entry(db, "database" in requested, cache),
        _investigator_entry(settings),
        _razorpay_entry(settings, "razorpay" in requested, cache),
    ]
    return {
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "probed": sorted(requested),
        "notice": (
            "Configured does not mean reachable. Reachability is only claimed "
            "for an integration that was probed, and each probe carries the "
            "time it was taken."
        ),
        "integrations": integrations,
    }

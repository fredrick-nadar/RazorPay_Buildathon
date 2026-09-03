"""Prepare reproducible SYNTHETIC_DEMO scenario matrices, never mutate a session.

Reads only the three manifest-verified gateway sources from a labelled demo.
Emits JSON intermediates for the CSV exporter, with evaluator-only expectations.
No network, keys, model calls, labels reads, or production data are supported.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.money import paise_from_decimal_rupees
from app.importers.session_staging import verified_active_sources

VERSION = "companion-scenario-v1"
BANK_COLUMNS = (
    "bank_entry_id",
    "posted_at_utc",
    "value_date",
    "currency",
    "signed_amount",
    "narration",
    "utr",
    "account_fingerprint",
)
LEDGER_COLUMNS = (
    "ledger_entry_id",
    "account_code",
    "accounting_date",
    "currency",
    "signed_amount",
    "source_reference",
    "source_type",
    "description",
    "entry_origin",
)


def money(value: int) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}{value // 100}.{value % 100:02d}"


def scenario(gateway: dict[str, list[dict[str, str]]], seed: int) -> dict[str, Any]:
    """Construct scenarios from business events, not from reconciliation output."""
    payments, refunds, settlements = (
        gateway[key] for key in ("payments", "refunds", "settlements")
    )
    if len(payments) < 4 or len(refunds) < 2 or len(settlements) < 2:
        raise ValueError(
            "Scenario requires at least 4 payments, 2 refunds and 2 settlements."
        )
    ledger: list[dict[str, str]] = []
    bank: list[dict[str, str]] = []
    expectations: list[dict[str, Any]] = []

    def identity(kind: str, reference: str) -> str:
        digest = hashlib.sha256(
            f"{VERSION}|{seed}|{kind}|{reference}".encode()
        ).hexdigest()[:16]
        return f"syn_{kind}_{digest}"

    def posting(kind: str, reference: str, amount: int, date: str) -> dict[str, str]:
        row = dict(
            zip(
                LEDGER_COLUMNS,
                (
                    identity("journal", reference),
                    "1100-BANK-OPERATING"
                    if kind == "SETTLEMENT"
                    else "2100-PAYMENTS-CLEARING",
                    date,
                    "INR",
                    money(amount),
                    reference,
                    kind,
                    f"SYNTHETIC_DEMO {kind.lower()} booking",
                    "IMPORTED",
                ),
                strict=True,
            )
        )
        ledger.append(row)
        return row

    for row in payments:
        net = sum(
            int(paise_from_decimal_rupees(row[field])) * sign
            for field, sign in (
                ("gross_amount", 1),
                ("fee_amount", -1),
                ("tax_amount", -1),
            )
        )
        posting("PAYMENT", row["payment_id"], net, row["captured_at_utc"][:10])
    for row in refunds:
        posting(
            "REFUND",
            row["refund_id"],
            -int(paise_from_decimal_rupees(row["refund_amount"])),
            row["created_at_utc"][:10],
        )
    for index, row in enumerate(
        sorted(
            settlements,
            key=lambda item: (item["settled_at_utc"], item["settlement_id"]),
        )
    ):
        settled = datetime.fromisoformat(row["settled_at_utc"].replace("Z", "+00:00"))
        booked = settled + timedelta(days=2 if index == 0 else 0)
        journal = posting(
            "SETTLEMENT",
            row["settlement_id"],
            int(paise_from_decimal_rupees(row["net_amount"])),
            booked.date().isoformat(),
        )
        if (
            not row["window_start_utc"][:10]
            <= booked.date().isoformat()
            <= row["window_end_utc"][:10]
        ):
            expectations.append(
                {
                    "kind": "SETTLEMENT_TIMING_WINDOW_SHIFT",
                    "anchor": row["settlement_id"],
                    "source": "SETTLEMENT",
                    "delta_paise": 0,
                    "story": "Accounting date is actual simulated booking date, outside the imported window. This is a policy/window warning, not proof of a bank delay.",
                    "journal": journal["ledger_entry_id"],
                }
            )
        posted = settled + timedelta(hours=2 + index)
        bank.append(
            dict(
                zip(
                    BANK_COLUMNS,
                    (
                        identity("bank", row["settlement_id"]),
                        posted.isoformat().replace("+00:00", "Z"),
                        posted.date().isoformat(),
                        "INR",
                        row["net_amount"],
                        f"SYNTHETIC_DEMO NEFT CREDIT {row['utr']}",
                        row["utr"],
                        "SYNTHETIC-MERCHANT-BANK",
                    ),
                    strict=True,
                )
            )
        )

    ranked = sorted(
        (row for row in ledger if row["source_type"] == "PAYMENT"),
        key=lambda row: identity("selection", row["source_reference"]),
    )
    duplicate = dict(ranked[0])
    duplicate["ledger_entry_id"] = identity(
        "retryjournal", duplicate["source_reference"]
    )
    ledger.append(duplicate)
    expectations.append(
        {
            "kind": "DUPLICATE_LEDGER_POSTING",
            "source": "PAYMENT",
            "anchor": duplicate["source_reference"],
            "delta_paise": -int(paise_from_decimal_rupees(duplicate["signed_amount"])),
            "story": "ERP retry created a second journal ID for one payment.",
        }
    )

    original = int(paise_from_decimal_rupees(ranked[1]["signed_amount"]))
    ranked[1]["signed_amount"] = money(original + 100)
    expectations.append(
        {
            "kind": "AMBIGUOUS_EVIDENCE",
            "source": "LEDGER_ENTRY",
            "anchor": ranked[1]["ledger_entry_id"],
            "delta_paise": None,
            "story": "Payment booking is overstated by a fictional INR 1.00 entry error; no supported automatic correction category.",
        }
    )
    ranked[2]["source_reference"] = ""
    expectations.append(
        {
            "kind": "AMBIGUOUS_EVIDENCE",
            "source": "LEDGER_ENTRY",
            "anchor": ranked[2]["ledger_entry_id"],
            "delta_paise": None,
            "story": "ERP export lost a payment reference; amount alone is insufficient.",
        }
    )

    omitted = sorted(
        (row for row in ledger if row["source_type"] == "REFUND"),
        key=lambda row: identity("selection", row["source_reference"]),
    )[:2]
    for row in omitted:
        ledger.remove(row)
        expectations.append(
            {
                "kind": "MISSING_REFUND_POSTING",
                "source": "REFUND",
                "anchor": row["source_reference"],
                "delta_paise": int(paise_from_decimal_rupees(row["signed_amount"])),
                "story": "Accounting connector failed to deliver this refund posting in the simulated completed export.",
            }
        )

    # Leave one settlement without bank evidence, rather than pretending it failed.
    missing_bank = bank.pop()
    missing_settlement = max(
        settlements, key=lambda row: (row["settled_at_utc"], row["settlement_id"])
    )
    expectations.append(
        {
            "kind": "AMBIGUOUS_EVIDENCE",
            "source": "SETTLEMENT",
            "anchor": missing_settlement["settlement_id"],
            "delta_paise": None,
            "story": "The supplied bank extract lacks one settlement credit. Receipt is unproven; never infer loss or create a credit.",
            "bank_record_withheld": missing_bank["bank_entry_id"],
        }
    )
    charge = dict(bank[0])
    charge.update(
        bank_entry_id=identity("charge", "bank-service"),
        signed_amount="-1.25",
        utr="",
        narration="SYNTHETIC_DEMO SERVICE CHARGE",
    )
    bank.append(charge)
    expectations.append(
        {
            "kind": "AMBIGUOUS_EVIDENCE",
            "source": "BANK_ENTRY",
            "anchor": charge["bank_entry_id"],
            "delta_paise": None,
            "story": "A fictional bank service debit is outside this gateway-reconciliation scope; preserve for review.",
        }
    )
    invalid = dict(bank[0])
    invalid.update(
        bank_entry_id=identity("invalid", "export-cell"),
        signed_amount="N/A",
        utr="",
        narration="SYNTHETIC_DEMO export cell unavailable",
    )
    bank.append(invalid)
    # Transport retry is not another economic posting: exact duplicate ID/content.
    ledger.append(dict(ranked[3]))

    # Deliberately unsorted exports, deterministic for a fixed seed and input.
    bank.sort(key=lambda row: identity("order", row["bank_entry_id"]))
    ledger.sort(key=lambda row: identity("order", row["ledger_entry_id"]))
    return {
        "version": VERSION,
        "seed": seed,
        "provenance": "SYNTHETIC_DEMO",
        "columns": {"bank_entries": BANK_COLUMNS, "ledger_entries": LEDGER_COLUMNS},
        "rows": {"bank_entries": bank, "ledger_entries": ledger},
        "expectations": {
            "cases": expectations,
            "quarantined_rows": 1,
            "duplicate_deliveries": 1,
        },
        "assumptions": {
            "currency": "INR",
            "money_unit": "integer paise internally, decimal rupees in CSV",
            "amount_error_paise": 100,
            "bank_service_charge_paise": 125,
            "omitted_refund_postings": 2,
            "settlement_booking_delay_days": 2,
            "policy": "Fictional merchant policy and event failures, NOT Razorpay policy or measured incident frequencies.",
            "scope": "Payment-clearing and bank reconciliation extract, NOT a complete double-entry general ledger.",
        },
    }


def prepare(session: Path, import_id: str, seed: int) -> dict[str, Any]:
    active = verified_active_sources(session)
    gateway: dict[str, list[dict[str, str]]] = {}
    sources: dict[str, Any] = {}
    for source in ("payments", "refunds", "settlements"):
        revision = active[source]
        if (
            revision["origin"] != "SYNTHETIC_DEMO"
            or revision["external_import_id"] != import_id
        ):
            raise ValueError(
                "Only the explicitly labelled demo for this import is supported."
            )
        content = (session / revision["canonical_path"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != revision["canonical_sha256"]:
            raise ValueError("Source changed during preparation.")
        gateway[source] = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
        sources[source] = {
            "csv": content.decode("utf-8"),
            "sha256": revision["canonical_sha256"],
            "revision_id": revision["revision_id"],
        }
    result = scenario(gateway, seed)
    result.update(import_id=import_id, gateway=sources)
    return result


def prepare_snapshot(root: Path, seed: int) -> dict[str, Any]:
    """Reproduce from the frozen gateway snapshot without reading evaluator labels."""
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest["provenance"] != "SYNTHETIC_DEMO"
        or manifest["production_eligible"] is not False
    ):
        raise ValueError("Only an explicitly synthetic snapshot is supported.")
    gateway = {}
    sources = {}
    for source in ("payments", "refunds", "settlements"):
        info = manifest["files"][f"{source}.csv"]
        content = (root / "inputs" / f"{source}.csv").read_bytes()
        if (
            info["provenance"] != "SYNTHETIC_DEMO"
            or hashlib.sha256(content).hexdigest() != info["sha256"]
        ):
            raise ValueError("Snapshot provenance or hash mismatch.")
        text = content.decode("utf-8")
        gateway[source] = list(csv.DictReader(io.StringIO(text)))
        sources[source] = {
            "csv": text,
            "sha256": info["sha256"],
            "revision_id": info["revision_id"],
        }
    result = scenario(gateway, seed)
    result.update(import_id=manifest["import_id"], gateway=sources)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--session-dir", type=Path)
    source.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--import-id")
    parser.add_argument("--seed", type=int, default=20260903)
    parser.add_argument("--output-json", required=True, type=Path)
    args = parser.parse_args()
    if args.session_dir:
        if not args.import_id:
            parser.error("--session-dir requires --import-id")
        result = prepare(args.session_dir, args.import_id, args.seed)
    else:
        result = prepare_snapshot(args.snapshot_dir, args.seed)
        if args.import_id and args.import_id != result["import_id"]:
            parser.error("--import-id does not match the snapshot")
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    with args.output_json.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(
        json.dumps(
            {
                "import_id": result["import_id"],
                "seed": args.seed,
                "rows": {name: len(rows) for name, rows in result["rows"].items()},
            }
        )
    )


if __name__ == "__main__":
    main()

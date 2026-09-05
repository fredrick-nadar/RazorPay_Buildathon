"""ONE canonical contract for the read/calculation investigator tools.

Everything that describes a tool is derived from this module: the Groq/OpenAI
``tools`` schemas sent on the wire, the human-readable catalogue placed in the
prompt, and the dispatcher's allowlist.  Nothing re-declares a tool name or an
argument schema independently, so the wire contract and the handlers cannot
drift apart (REVIEW-017).

Two rules govern what an argument list may contain:

1. Only arguments the runtime actually CONSUMES appear.  The previous
   hand-written prompt catalogue advertised ``constraints``, ``evidence_ids``
   and ``rule_id``, none of which any handler reads; a model that supplied them
   was being invited to invent structure.  They are gone.
2. ``get_evidence_graph`` keeps ``case_id`` even though its handler returns the
   whole run graph, because ``app.investigator.evidence_binding`` consumes that
   argument to decide whether the call was bound to the case under
   investigation.  Consumption by the binding layer is consumption.

There is deliberately NO approval, correction, ledger-write, status-change,
resolution or verification tool here, and an import-time guard rejects a tool
name shaped like one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Record types ``list_candidate_records`` resolves. Mirrors its handler.
CANDIDATE_RECORD_TYPES: tuple[str, ...] = (
    "PAYMENT",
    "REFUND",
    "SETTLEMENT",
    "BANK_ENTRY",
    "LEDGER_ENTRY",
)
MAX_BATCH_RECORDS = 12

_EVIDENCE_ID = "canonical evidence id, TYPE:record_id (for example LEDGER_ENTRY:led_abc123)"


@dataclass(frozen=True)
class ToolDefinition:
    """One read-only or exploratory-calculation tool, described once."""

    name: str
    description: str
    #: JSON Schema for the arguments object, in the shape Groq/OpenAI expect.
    parameters: dict[str, Any]

    @property
    def required_arguments(self) -> tuple[str, ...]:
        required = self.parameters.get("required", [])
        return tuple(str(item) for item in required)

    @property
    def argument_names(self) -> tuple[str, ...]:
        properties = self.parameters.get("properties", {})
        return tuple(sorted(str(name) for name in properties))

    def to_groq_tool(self) -> dict[str, Any]:
        """The official function-tool envelope for the chat-completions API."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_catalogue_line(self) -> str:
        """One prompt line, generated from the same source as the schema."""
        if not self.argument_names:
            return f"- {self.name} (no arguments): {self.description}"
        required = set(self.required_arguments)
        rendered = ", ".join(
            name if name in required else f"{name} (optional)" for name in self.argument_names
        )
        return f"- {self.name} ({rendered}): {self.description}"

    def accepts(self, arguments: dict[str, Any]) -> bool:
        """Validate the small JSON-Schema subset used by this contract."""
        properties = self.parameters["properties"]
        if set(arguments) - set(properties):
            return False
        if set(self.required_arguments) - set(arguments):
            return False
        for name, value in arguments.items():
            schema = properties[name]
            expected = schema.get("type")
            if expected == "string":
                if not isinstance(value, str):
                    return False
            elif expected == "array":
                if not isinstance(value, list):
                    return False
                if len(value) < int(schema.get("minItems", 0)):
                    return False
                if len(value) > int(schema.get("maxItems", len(value))):
                    return False
                item_type = schema.get("items", {}).get("type")
                if item_type == "string" and any(not isinstance(item, str) for item in value):
                    return False
            else:  # pragma: no cover - import-time contract guard below owns this
                return False
            if "enum" in schema and value not in schema["enum"]:
                return False
        return True


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """A strict arguments schema: no extra keys may be invented."""
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_STRING_LIST: dict[str, Any] = {"type": "array", "items": {"type": "string"}}


TOOL_CONTRACT: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="get_case",
        description=(
            "Return the exception case under investigation: category, status, "
            "variance in paise, currency, reason codes and its cited evidence list."
        ),
        parameters=_object(
            {"case_id": {"type": "string", "description": "the case under investigation"}},
            ["case_id"],
        ),
    ),
    ToolDefinition(
        name="get_evidence_graph",
        description=(
            "Return the typed evidence graph for the run, including the nodes and "
            "links around this case."
        ),
        parameters=_object(
            {"case_id": {"type": "string", "description": "the case under investigation"}},
            ["case_id"],
        ),
    ),
    ToolDefinition(
        name="get_record",
        description="Return one normalized evidence record in full.",
        parameters=_object(
            {"record_id": {"type": "string", "description": _EVIDENCE_ID}},
            ["record_id"],
        ),
    ),
    ToolDefinition(
        name="get_records",
        description=(
            "Return up to 12 normalized evidence records in one bounded call. "
            "Use this once with every canonical linked evidence id for the case."
        ),
        parameters=_object(
            {
                "record_ids": {
                    **_STRING_LIST,
                    "minItems": 1,
                    "maxItems": MAX_BATCH_RECORDS,
                    "description": f"one or more {_EVIDENCE_ID}",
                }
            },
            ["record_ids"],
        ),
    ),
    ToolDefinition(
        name="list_candidate_records",
        description="List every normalized record of one type in this run.",
        parameters=_object(
            {
                "record_type": {
                    "type": "string",
                    "enum": list(CANDIDATE_RECORD_TYPES),
                    "description": "the record type to list",
                }
            },
            ["record_type"],
        ),
    ),
    ToolDefinition(
        name="get_rule_manifest",
        description=(
            "Return the reconciliation and verification rule versions. Static "
            "metadata: identical for every case, so it proves nothing about this one."
        ),
        parameters=_object({}, []),
    ),
    ToolDefinition(
        name="calculate_control_totals",
        description=(
            "Return the run's global control totals. Exploratory only; the "
            "deterministic verifier is the sole authority on financial arithmetic."
        ),
        parameters=_object({}, []),
    ),
    ToolDefinition(
        name="calculate_expected_net",
        description=(
            "Compute expected net settlement from bare payment and refund ids. "
            "Exploratory only; the deterministic verifier decides."
        ),
        parameters=_object(
            {
                "payment_ids": {
                    **_STRING_LIST,
                    "description": "bare payment ids, without a TYPE prefix",
                },
                "refund_ids": {
                    **_STRING_LIST,
                    "description": "bare refund ids, without a TYPE prefix",
                },
            },
            ["payment_ids", "refund_ids"],
        ),
    ),
    ToolDefinition(
        name="check_date_window",
        description=(
            "Resolve each record and report its window, settlement or posting "
            "dates against the configured posting windows."
        ),
        parameters=_object(
            {
                "record_ids": {
                    **_STRING_LIST,
                    "minItems": 1,
                    "description": f"one or more {_EVIDENCE_ID}",
                }
            },
            ["record_ids"],
        ),
    ),
    ToolDefinition(
        name="check_unique_identity",
        description=(
            "Test a set of evidence ids for duplicates and twin-settlement "
            "conflicts. Treats every id as an identity token."
        ),
        parameters=_object(
            {
                "record_ids": {
                    **_STRING_LIST,
                    "minItems": 1,
                    "description": f"one or more {_EVIDENCE_ID}",
                }
            },
            ["record_ids"],
        ),
    ),
)

#: Name -> definition, the single lookup used by every consumer.
TOOLS_BY_NAME: dict[str, ToolDefinition] = {tool.name: tool for tool in TOOL_CONTRACT}

#: The allowlist itself. The dispatcher imports this rather than re-listing it.
CONTRACT_TOOL_NAMES: frozenset[str] = frozenset(TOOLS_BY_NAME)

#: Substrings that would betray an authority, write or workflow tool. The
#: contract is checked against these at import time, so a tool that could
#: approve, apply, resolve or verify can never be added by accident.
FORBIDDEN_NAME_TOKENS: tuple[str, ...] = (
    "approve",
    "apply",
    "write",
    "update",
    "resolve",
    "delete",
    "verify",
    "mark_",
    "set_",
    "create",
    "commit",
)

for _tool in TOOL_CONTRACT:
    if any(token in _tool.name for token in FORBIDDEN_NAME_TOKENS):  # pragma: no cover - guard
        raise AssertionError(f"authority-shaped tool name in the contract: {_tool.name}")
    if any(
        schema.get("type") not in {"string", "array"}
        for schema in _tool.parameters["properties"].values()
    ):  # pragma: no cover - guard for the deliberately tiny local validator
        raise AssertionError(f"unsupported argument schema in the contract: {_tool.name}")


def groq_tool_schemas() -> list[dict[str, Any]]:
    """The ``tools`` array sent on an investigator turn, in contract order."""
    return [tool.to_groq_tool() for tool in TOOL_CONTRACT]


def prompt_catalogue() -> str:
    """The human-readable catalogue, generated from the same definitions."""
    lines = ["Available tools (call each by its exact name):"]
    lines.extend(tool.to_catalogue_line() for tool in TOOL_CONTRACT)
    return "\n".join(lines)


__all__ = [
    "CANDIDATE_RECORD_TYPES",
    "CONTRACT_TOOL_NAMES",
    "FORBIDDEN_NAME_TOKENS",
    "MAX_BATCH_RECORDS",
    "TOOLS_BY_NAME",
    "TOOL_CONTRACT",
    "ToolDefinition",
    "groq_tool_schemas",
    "prompt_catalogue",
]

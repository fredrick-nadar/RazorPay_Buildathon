"""Typed evidence-graph construction and serialization (PRD 13.3).

Nodes are the accepted normalized records plus one CASE node per case. Edges
come from two sources only - stored match groups and case evidence - so they
reference only real source ids. Exact identifier rules produce EXACT
confidence edges; uniqueness-window and composition rules produce RULE
confidence edges; HYPOTHESIS edges are reserved for the Phase 4 investigator
and never appear here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.enums import EdgeConfidence, NodeType, RelationshipType
from app.domain.records import AcceptedRecords
from app.reconciliation.detectors import CaseRecord
from app.reconciliation.engine import MatchGroup
from app.reconciliation.rules import (
    R_REFUND_COMPOSITION,
    R_SETTLEMENT_BANK_UNIQUE,
)

RULE_CONFIDENCE_RULES = frozenset({R_SETTLEMENT_BANK_UNIQUE, R_REFUND_COMPOSITION})


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    node_type: NodeType
    label: str
    state: str


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    relationship_type: RelationshipType
    rule_id: str
    rule_version: str
    confidence: EdgeConfidence
    status: str


@dataclass(frozen=True)
class EvidenceGraph:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]

    def node_ids(self) -> set[str]:
        return {node.node_id for node in self.nodes}

    def to_json(self) -> dict[str, object]:
        return {
            "nodes": [
                {
                    "node_id": node.node_id,
                    "node_type": node.node_type.value,
                    "label": node.label,
                    "state": node.state,
                }
                for node in self.nodes
            ],
            "edges": [
                {
                    "edge_id": edge.edge_id,
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                    "relationship_type": edge.relationship_type.value,
                    "rule_id": edge.rule_id,
                    "rule_version": edge.rule_version,
                    "confidence": edge.confidence.value,
                    "status": edge.status,
                }
                for edge in self.edges
            ],
            "counts": {
                "node_count": len(self.nodes),
                "edge_count": len(self.edges),
                "nodes_by_type": _count_by(node.node_type.value for node in self.nodes),
                "edges_by_relationship": _count_by(
                    edge.relationship_type.value for edge in self.edges
                ),
            },
        }


def _count_by(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _node_id(record_type: str, record_id: str) -> str:
    return f"{record_type}:{record_id}"


def _case_node_id(case_id: str) -> str:
    return f"CASE:{case_id}"


def _confidence_for(rule_id: str) -> EdgeConfidence:
    if rule_id in RULE_CONFIDENCE_RULES:
        return EdgeConfidence.RULE
    return EdgeConfidence.EXACT


def _member_edges(group: MatchGroup) -> list[GraphEdge]:
    """One edge per member toward the group's anchor member.

    Membership groups anchor on the settlement; pair groups anchor on the
    PARENT/SOURCE/CREDIT side member; compositions anchor on the booking
    ledger row.
    """
    if group.relationship_type == RelationshipType.MEMBER_OF_SETTLEMENT:
        anchors = [m for m in group.members if m.record_type == "SETTLEMENT"]
        others = [m for m in group.members if m.record_type != "SETTLEMENT"]
    else:
        priority = ("PAYMENT", "SETTLEMENT", "BANK_ENTRY", "REFUND")
        anchors = [
            m
            for m in group.members
            if m.role in ("PARENT", "SOURCE", "CREDIT")
            or (group.rule_id == R_REFUND_COMPOSITION and m.role == "BOOKING")
        ]
        others = [m for m in group.members if m not in anchors]
        if not anchors:
            anchors = [m for m in group.members if m.record_type in priority][:1]
            others = [m for m in group.members if m not in anchors]
    anchor = anchors[0]
    edges: list[GraphEdge] = []
    for member in sorted(others, key=lambda m: (m.record_type, m.record_id)):
        if group.relationship_type == RelationshipType.MEMBER_OF_SETTLEMENT:
            relationship = (
                RelationshipType.ADJUSTS_SETTLEMENT
                if member.record_type == "REFUND"
                else RelationshipType.MEMBER_OF_SETTLEMENT
            )
        else:
            relationship = group.relationship_type
        edges.append(
            GraphEdge(
                edge_id=(f"edge:{group.match_id}:{member.record_type}:{member.record_id}"),
                source_node_id=_node_id(member.record_type, member.record_id),
                target_node_id=_node_id(anchor.record_type, anchor.record_id),
                relationship_type=relationship,
                rule_id=group.rule_id,
                rule_version=group.rule_version,
                confidence=_confidence_for(group.rule_id),
                status="MATCHED",
            )
        )
    return edges


def build_evidence_graph(
    records: AcceptedRecords,
    matches: list[MatchGroup],
    cases: list[CaseRecord],
) -> EvidenceGraph:
    nodes: list[GraphNode] = []
    for payment in records.payments:
        nodes.append(
            GraphNode(
                node_id=_node_id("PAYMENT", payment.payment_id),
                node_type=NodeType.PAYMENT,
                label=payment.payment_id,
                state="ACCEPTED",
            )
        )
    for refund in records.refunds:
        nodes.append(
            GraphNode(
                node_id=_node_id("REFUND", refund.refund_id),
                node_type=NodeType.REFUND,
                label=refund.refund_id,
                state="ACCEPTED",
            )
        )
    for settlement in records.settlements:
        nodes.append(
            GraphNode(
                node_id=_node_id("SETTLEMENT", settlement.settlement_id),
                node_type=NodeType.SETTLEMENT,
                label=settlement.settlement_id,
                state="ACCEPTED",
            )
        )
    for credit in records.bank_entries:
        nodes.append(
            GraphNode(
                node_id=_node_id("BANK_ENTRY", credit.bank_entry_id),
                node_type=NodeType.BANK_ENTRY,
                label=credit.bank_entry_id,
                state="ACCEPTED",
            )
        )
    for entry in records.ledger_entries:
        nodes.append(
            GraphNode(
                node_id=_node_id("LEDGER_ENTRY", entry.ledger_entry_id),
                node_type=NodeType.LEDGER_ENTRY,
                label=entry.ledger_entry_id,
                state="ACCEPTED",
            )
        )
    for case in cases:
        nodes.append(
            GraphNode(
                node_id=_case_node_id(case.case_id),
                node_type=NodeType.CASE,
                label=case.case_id,
                state=case.status.value,
            )
        )

    edges: list[GraphEdge] = []
    for group in matches:
        edges.extend(_member_edges(group))
    for case in cases:
        for item in sorted(case.evidence, key=lambda item: (item.record_type, item.record_id)):
            edges.append(
                GraphEdge(
                    edge_id=f"edge:{case.case_id}:{item.record_type}:{item.record_id}",
                    source_node_id=_case_node_id(case.case_id),
                    target_node_id=_node_id(item.record_type, item.record_id),
                    relationship_type=RelationshipType.CASE_EVIDENCE,
                    rule_id="case-evidence",
                    rule_version="1",
                    confidence=EdgeConfidence.RULE,
                    status=case.status.value,
                )
            )

    graph = EvidenceGraph(nodes=tuple(nodes), edges=tuple(edges))
    validate_graph(graph)
    return graph


def validate_graph(graph: EvidenceGraph) -> None:
    """Every edge endpoint must resolve to an existing node id."""
    known = graph.node_ids()
    for edge in graph.edges:
        if edge.source_node_id not in known:
            raise ValueError(
                f"edge {edge.edge_id} references unknown source node {edge.source_node_id}"
            )
        if edge.target_node_id not in known:
            raise ValueError(
                f"edge {edge.edge_id} references unknown target node {edge.target_node_id}"
            )

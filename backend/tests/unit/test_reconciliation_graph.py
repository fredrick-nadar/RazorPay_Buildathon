"""Phase 2 evidence-graph tests: node/edge typing and referential validity.

The graph is derived from records, matches, and case evidence; serialization
must never reference an unknown record id (validated at build time), node
and edge counts must be reproducible, and rule provenance must travel on
every edge.
"""

from __future__ import annotations

from pathlib import Path

from app.domain.enums import EdgeConfidence, NodeType, RelationshipType
from app.graph.evidence import build_evidence_graph, validate_graph
from app.importers.ingest import ingest_inputs
from app.reconciliation.detectors import reconcile
from app.reconciliation.rules import (
    R_REFUND_COMPOSITION,
    R_SETTLEMENT_BANK_UNIQUE,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _graph(inputs: Path):
    ingest = ingest_inputs(inputs)
    result = reconcile(ingest.records)
    graph = build_evidence_graph(ingest.records, list(result.matches), list(result.cases))
    return ingest, result, graph


class TestDevGraph:
    def test_node_and_edge_counts(self) -> None:
        ingest, result, graph = _graph(REPO_ROOT / "datasets" / "dev" / "inputs")
        assert len(graph.nodes) == ingest.accepted_count + len(result.cases)
        payload = graph.to_json()
        counts = payload["counts"]
        assert counts["node_count"] == 282 + 12
        assert counts["nodes_by_type"]["CASE"] == 12
        assert counts["nodes_by_type"]["PAYMENT"] == 96
        assert counts["nodes_by_type"]["LEDGER_ENTRY"] == 132
        assert counts["edge_count"] > 0
        assert counts["edges_by_relationship"]["CASE_EVIDENCE"] == sum(
            len(case.evidence) for case in result.cases
        )

    def test_every_edge_references_existing_nodes(self) -> None:
        _ingest, _result, graph = _graph(REPO_ROOT / "datasets" / "dev" / "inputs")
        validate_graph(graph)  # raises on any unknown endpoint

    def test_edge_rule_provenance_present(self) -> None:
        _ingest, _result, graph = _graph(REPO_ROOT / "datasets" / "dev" / "inputs")
        for edge in graph.edges:
            assert edge.rule_id
            assert edge.rule_version
            assert edge.confidence in (
                EdgeConfidence.EXACT,
                EdgeConfidence.RULE,
            )

    def test_uniqueness_rules_carry_rule_confidence(self) -> None:
        _ingest, _result, graph = _graph(REPO_ROOT / "datasets" / "dev" / "inputs")
        by_rule = {edge.rule_id: edge for edge in graph.edges}
        if R_SETTLEMENT_BANK_UNIQUE in by_rule:
            assert by_rule[R_SETTLEMENT_BANK_UNIQUE].confidence == EdgeConfidence.RULE
        for edge in graph.edges:
            if edge.rule_id not in (
                R_SETTLEMENT_BANK_UNIQUE,
                R_REFUND_COMPOSITION,
                "case-evidence",
            ):
                assert edge.confidence == EdgeConfidence.EXACT

    def test_case_nodes_attach_to_their_evidence(self) -> None:
        _ingest, result, graph = _graph(REPO_ROOT / "datasets" / "dev" / "inputs")
        case_edges = [
            edge for edge in graph.edges if edge.relationship_type == RelationshipType.CASE_EVIDENCE
        ]
        case_ids = {node.node_id for node in graph.nodes if node.node_type == NodeType.CASE}
        assert {edge.source_node_id for edge in case_edges} <= case_ids
        # Twin ambiguous cases cite four records each.
        twin_cases = [case for case in result.cases if len(case.evidence) == 4]
        assert len(twin_cases) == 3


class TestAdversarialGraph:
    def test_graph_serializes_with_valid_references(self) -> None:
        ingest, result, graph = _graph(REPO_ROOT / "datasets" / "adversarial" / "inputs")
        validate_graph(graph)
        payload = graph.to_json()
        assert payload["counts"]["node_count"] == ingest.accepted_count + len(result.cases)

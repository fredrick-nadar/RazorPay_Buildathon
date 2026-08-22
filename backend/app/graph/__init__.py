"""Evidence graph: derived, typed nodes and edges over stored run facts.

The graph is always serialized from records, matches, and case evidence -
never stored separately - so it can never drift from reconciliation results.
Every edge endpoint must resolve to a real record or case id; serialization
fails loudly otherwise (PRD Phase 2 gate: no unknown record ids).
"""

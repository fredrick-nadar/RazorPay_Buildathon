"""Correction preview package (PRD 11.1, 11.2).

Phase 3 contains authority classification and dry-run calculation only.
There is deliberately no approval, application, or ledger-write path: a dry
run produces a DRAFT preview value object and never mutates persisted state
or creates a ledger entry of any origin.
"""

"""Evaluator-only package: dataset generation, ground truth, and isolation.

Nothing in this package may be imported by runtime modules (``backend/app``
api, domain, persistence, ``main``, ``config``) or by the frontend. The label
firewall in ``label_firewall.py`` enforces that boundary mechanically; PRD
section 6.13 makes ground truth unreachable from runtime code.

Direction of dependency: ``evaluation -> domain`` is allowed (money helpers,
enums). ``domain -> evaluation`` is forbidden.
"""

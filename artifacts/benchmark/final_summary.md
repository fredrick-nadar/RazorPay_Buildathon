# ARGUS CONTROL — Final Holdout Benchmark Summary

**Benchmark Version**: `argus-benchmark-agent-v1`  
**Dataset**: `datasets/holdout`  
**Evaluation Mode**: `agent` (Provider: `fake`)  
**Economic Output Hash**: `69c89e71cbf7b7cc39934069f0962bb3837d2a7c3fae7c275a8eedd7b476f31e`  

---

## 1. Executive Performance Metrics

| Metric | Result | Explicit Numerator / Denominator | Compliance |
| :--- | :---: | :---: | :---: |
| **Match Precision** | **100.0%** | 1124 / 1124 | **PASS (1.0 Required)** |
| **Record Match Rate** | **99.15%** | 1864 / 1880 | **PASS** |
| **Case Classification Accuracy** | **100.0%** | 23 / 23 | **PASS (1.0 Required)** |
| **False Verifier Passes** | **0** | 0 / 23 | **PASS (Must be 0)** |
| **Money-Weighted Dry-Run Error** | **₹0.00** | 0 paise | **PASS (0 paise)** |
| **Proof Completeness** | **18 / 18** | 100% complete | **PASS** |
| **Ambiguous Case Escalation** | **100.0%** | 5 / 5 | **PASS** |
| **Reconciliation Throughput** | **11,250.57 rec/s** | Sub-second batch execution | **PASS** |

---

## 2. Unresolved Exception Cases (Honest Denominator Accounting)

Per PRD §13.3, ambiguous cases are strictly preserved without forced model resolution:

| Case ID | Category | Status | Evidence Citations |
| :--- | :--- | :---: | :--- |
| `case-c329b60debe8` | `AMBIGUOUS_EVIDENCE` | `UNRESOLVED` | Matched label `case-holdout-0019` |
| `case-4110c2fe504b` | `AMBIGUOUS_EVIDENCE` | `UNRESOLVED` | Matched label `case-holdout-0020` |
| `case-564d0c33674a` | `AMBIGUOUS_EVIDENCE` | `UNRESOLVED` | Matched label `case-holdout-0021` |
| `case-0f9a0d0d21f2` | `AMBIGUOUS_EVIDENCE` | `UNRESOLVED` | Matched label `case-holdout-0022` |
| `case-1eede37d0353` | `AMBIGUOUS_EVIDENCE` | `UNRESOLVED` | Matched label `case-holdout-0023` |

---

## 3. Idempotency & Replay Guarantee

- **First Run Hash**: `69c89e71cbf7b7cc39934069f0962bb3837d2a7c3fae7c275a8eedd7b476f31e`
- **Second Run Hash**: `69c89e71cbf7b7cc39934069f0962bb3837d2a7c3fae7c275a8eedd7b476f31e`
- **Economically Identical**: `True`
- **Duplicate Ledger Adjustments**: `0` (measured across replay databases)

# Phase 5 A/B Results — baseline vs confirmation (for t16 final report)

**Source:** t9 (Sweep_Engineer, confirmation detector) — cost-free demo on real
data, `group_rule="first"` (causal-absolute, QA F1).

| Strategy | Trades | Win rate | Expectancy (R) |
|---|---|---|---|
| A — baseline (entry after sweep) | 3,774 | 47.9% | −0.043R |
| B — confirmed (entry after confirmation) | 974 | 49.5% | −0.016R |

## Key facts to state honestly in the final report

1. **Both expectancies are NEGATIVE even before costs** (spread/slippage/
   commission not yet applied) — B improves both win rate and expectancy
   relative to A, but neither is profitable cost-free.
2. This is a *truthful finding*, not a failure: the system's job is detection
   + scoring; the negative base rate is the honest benchmark the rule score
   and ML stage (Phase 6/7) must beat out-of-sample.
3. B (confirmation filter) also massively cuts trade count (3,774 → 974,
   ~74% reduction): fewer, higher-quality setups — decision documented for
   baseline freeze at t16.
4. **Baseline choice for the official baseline (freeze at t16):** B is the
   natural primary (better win rate/expectancy + less opportunity cost of
   look-ahead-free filtering), with A kept as the comparison arm; the final
   call belongs to t16 + QA sign-off (t15) — do not silently pick A.

## Files
- `reports/integration/AB_phase5.md` (this note)
- confirmation module: `src/events/confirmation.py` (t9)
- A/B comparison logic and numbers originate from t9 deliverables/report.
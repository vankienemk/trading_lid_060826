# Score Bucket × Exit Reason Breakdown & Non-Monotonicity Analysis

**Dataset**: `data/processed/labeled_events.parquet` (frozen, 974 confirmed events)

**Primary trade definition**: entry after confirmation (strategy B), 2R target, 16-bar horizon (h16), zero-cost run — `exit_reason` ∈ {target, stop, time, ambiguous}, identical to `outcome_2r_h16` tokens {tp, sl, time, ambiguous}.

**Question 1 — the “missing” share**: win_rate + loss_rate < 100% is **not** missing data; it is the **time-barrier share** (neither 2R target nor stop touched inside 16 bars; exit marks to market at bar-16 close), plus 1 ambiguous row (TP & SL inside the same candle). Breakdown below.


## 1. Exit-reason distribution by score bucket

| Bucket | N | target (tp) | stop (sl) | time | ambiguous | win | loss | time_rate | amb | win+loss |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0-39 | 783 | 148 | 381 | 253 | 1 | 18.9% | 48.7% | 32.3% | 0.1% | 67.6% |
| 40-49 | 188 | 29 | 66 | 93 | 0 | 15.4% | 35.1% | 49.5% | 0.0% | 50.5% |
| 50-59 | 3 | 0 | 1 | 2 | 0 | 0.0% | 33.3% | 66.7% | 0.0% | 33.3% |
| 60-69 | 0 | - | - | - | - | - | - | - | - |
| 70-79 | 0 | - | - | - | - | - | - | - | - |
| 80-100 | 0 | - | - | - | - | - | - | - | - |

→ The “missing” 32.4% (0-39) and 49.5% (40-49) are **time-barrier exits** — the score buckets do **not** lose events; more of the higher-score trades simply run out of the 16-bar window before either barrier is hit (time share 32.3% → 49.5% as score rises). Only 1 of 974 rows is ambiguous.


## 2. Per-bucket metrics

| Bucket | N | avg bars_held | avg MFE R (h16) | avg MAE R (h16) | avg MFE R (h32) | avg gross R | avg net R | net 95% CI (bootstrap) |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 0-39 | 783 | 9.9 | 1.471 | 1.514 | 2.175 | -0.005 | -0.005 | [-0.0878, 0.0807] |
| 40-49 | 188 | 11.2 | 1.231 | 1.036 | 1.795 | 0.092 | 0.092 | [-0.0556, 0.249] |
| 50-59 | 3 | 12.3 | 1.258 | 0.649 | 1.682 | -0.042 | -0.042 | [-1.0, 0.4704] |

## 3. Is the 16-bar horizon too short / the 2R target too far?

- Winners are **fast**: among target exits, bars_to_target median = 8, mean = 8.2, 55% reach 2R within 8 bars, p90 = 14 (max 16 by construction). 2R is **not** systematically out of reach for good setups.
- Time-out events **are** truncated: their mean windowed MFE rises from 0.98R (h16) to 1.51R (h32), and **20.4%** of time-outs (36% of all events) would have converted to a 2R win with a 32-bar horizon.
- However time-outs currently close **mildly positive** on average (+0.31R mark-to-market), so the horizon truncation costs upside but is not destroying expectancy by itself. Recommendation for policy work (outside this task): test h32 and/or 1.5R as alternative primary definitions on the frozen set.


## 4. Why is bucket 40-49 win rate (15.4%) < bucket 0-39 (18.9%) while expectancy is positive?

**R:R is constant by construction** — `gross_result_r` for target exits is exactly +2.0 and for stop exits exactly -1.0 in *every* bucket (frozen run has zero costs). A lower win rate therefore cannot come from worse payoffs on wins/losses; it must come from the event mix.

| Bucket | N | win | loss | time | E[net] = | tp contrib | sl contrib | time contrib | actual avg net |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0-39 | 783 | 18.9% | 48.7% | 32.3% | -0.0046 R | +0.3780 | -0.4866 | +0.1040 | -0.0045 |
| 40-49 | 188 | 15.4% | 35.1% | 49.5% | +0.0920 R | +0.3086 | -0.3511 | +0.1345 | +0.0919 |
| 50-59 | 3 | 0.0% | 33.3% | 66.7% | -0.0418 R | +0.0000 | -0.3333 | +0.2915 | -0.0419 |

Reading the decomposition (0-39 → 40-49): target contribution **falls** (+0.378 → +0.309 R, the lower win rate), but stop contribution improves **more** (-0.487 → -0.351 R) and the time-out contribution rises (+0.104 → +0.135 R, time-outs close +0.27…+0.32R on average). Net: expectancy -0.005 → +0.092 R **without any change in win size**.

Is the win-rate dip noise? A two-proportion test of 18.9% (148/783) vs 15.4% (29/188) gives z = 1.16, **p = 0.24** — not significant at any conventional level (only 29 winners in 40-49). The net-expectancy advantage of 40-49 is also compatible with noise: bootstrap 95% CI of (40-49 − 0-39) = [-0.0782, 0.2729] spans 0.

**Conclusion (user answer)**: the non-monotonic win rate is **not** an R:R effect (wins/losses pay exactly +2/−1 everywhere) and is statistically consistent with **small-sample noise** (p ≈ 0.24 on 29 winners). The positive expectancy of 40-49 comes from a much lower loss rate (35.1% vs 48.7%) plus a larger mildly-positive time-out share — i.e. the score separates *bad trades that stop out* from *mild trades that fizzle*, not from winners that pay more. Treat 40-49's edge as suggestive until sample sizes grow (bucket N=188; 50-59 has only 3 events).


## Caveats

- Frozen run is zero-cost (gross == net per exit_reason); cost impact must be re-checked when spread/slippage are enabled (t16).
- Bucket 50-59: n = 3 — statistics there are illustrative only.
- Bootstrap CIs use seed 20260903 (fixed); json carries every number above for reproducibility.

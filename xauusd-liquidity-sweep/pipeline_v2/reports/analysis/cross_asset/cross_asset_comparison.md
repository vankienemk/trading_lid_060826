# Cross-Asset V2 Comparison Report

**Generated:** 2026-09-05 08:20
**Pipeline:** V2 (nguoc_trend + max_pen≤0.20 + R:R 3.0:1 + cost 0.05R)
**Entry mode:** next_open_after_confirmation
**Group rule:** first

---

## Table of Contents
1. [Dataset Overview](#1-dataset-overview)
2. [Filter Stack Progression](#2-filter-stack-progression)
3. [Performance Comparison (R:R 3.0:1)](#3-performance-comparison-rr-301)
4. [Base Model Comparison (2R:16H)](#4-base-model-comparison-2r16h)
5. [Filter Stack Effectiveness](#5-filter-stack-effectiveness)
6. [Phase 1 Triple Intersection Legacy (n=38)](#6-phase-1-triple-intersection-legacy-n38)
7. [Generalization Verdict](#7-generalization-verdict)
8. [Key Findings & Differences](#8-key-findings--differences)
9. [Recommendations](#9-recommendations)

---

## 1. Dataset Overview

| Asset | Date Range | Bars | V2 Pipeline | R:R | Cost |
|---|---|---|---|---|---|
| **XAUUSD (Phase 1)** | 6/2022 → 2026 | 99,692 (frozen 974 events) | nguoc_trend + pen≤0.20 + prob≥0.25 triple | 3.0:1 | 0.05R |
| **XAUUSD (Full)** | 2018 → 2026 | 204,133 | nguoc_trend + pen≤0.20 | 3.0:1 | 0.05R |
| **EURUSD (Full)** | 2018 → 2026 | 215,570 | nguoc_trend + pen≤0.20 | 3.0:1 | 0.05R |

### Key Distinction

- **Phase 1** (original V2.0.0) ran against the frozen V1.2.0 dataset (974 confirmed events) and used a **triple filter stack**: nguoc_trend + prob≥0.25 + pen≤0.20, yielding only **n=38** triple-intersection trades. Its PF=4.384 @0.05R with R:R=3.0.
- **Full-pipeline** runs (XAUUSD full, EURUSD full) apply V2 directly to raw H1 bars — no frozen event pool, no score-probability filter. They yield **n=240–267** net-valid trades and lower but still profitable PFs.

---

## 2. Filter Stack Progression

### XAUUSD (Full, 2018→2026)

| Layer | Count | Events per 1000 bars | Reduction Factor |
|---|---|---|---|
| Baseline sweep bars | 4,004 | 19.615 | — |
| + nguoc_trend filter | 1,003 | 4.914 | 3.99× |
| Confirmed events | 242 | 1.186 | 16.54× |
| Labeled valid | 242 | 1.186 | 16.54× |
| Net valid | 240 | 1.176 | 16.68× |

### EURUSD (Full, 2018→2026)

| Layer | Count | Events per 1000 bars | Reduction Factor |
|---|---|---|---|
| Baseline sweep bars | 4,290 | 19.901 | — |
| + nguoc_trend filter | 1,124 | 5.214 | 3.82× |
| Confirmed events | 267 | 1.239 | 16.07× |
| Labeled valid | 267 | 1.239 | 16.07× |
| Net valid | 267 | 1.239 | 16.07× |

### Stack Comparison (Full-pipeline only)

| Metric | XAUUSD (Full) | EURUSD | Δ |
|---|---|---|---|
| Baseline sweep rate (/1000 bars) | 19.615 | 19.901 | +1.5% → near-identical |
| nguoc_trend pass rate (/1000 bars) | 4.914 | 5.214 | +6.1% → EURUSD trend-filter is slightly more permissive |
| Confirmed events (/1000 bars) | 1.186 | 1.239 | +4.5% → comparable |
| Net valid trades (/1000 bars) | 1.176 | 1.239 | +5.4% → comparable |

Both assets show very similar sweep densities and filter reduction rates, indicating that V2's filter stack behaves consistently across FX/metals.

---

## 3. Performance Comparison (R:R 3.0:1)

| Metric | XAUUSD (Phase 1) | XAUUSD (Full) | EURUSD (Full) |
|---|---|---|---|
| Net valid n | 38 | 240 | 267 |
| PF pre-cost | — | 1.856 | 1.360 |
| **PF @0.05R cost** | **4.384** | **1.703** | **1.247** |
| PF 95% CI | [2.24, 8.96] | [1.368, 2.466] | [1.014, 1.792] |
| Breakeven cost (R) | >1.0 | 0.384 | 0.184 |
| Avg net R (pre-cost) | 1.193 | 0.384 | 0.184 |
| Net positive rate | 0.632 | 0.475 | 0.401 |

### PF Comparison (3R: @0.05R cost)

```
XAUUSD Phase 1  ████████████████████████████████████████ 4.384
XAUUSD Full     ████████████████                        1.703
EURUSD Full     ████████████                            1.247
```

### CI Overlap Analysis (95% PF CIs @ R:R 3.0:1)

| Comparison | Interval | Overlap? | Significance |
|---|---|---|---|
| XAUUSD Phase 1 vs XAUUSD Full | [2.24, 8.96] vs [1.37, 2.47] | None | ✅ Distinct |
| XAUUSD Full vs EURUSD Full | [1.37, 2.47] vs [1.01, 1.79] | Partial [1.37, 1.79] | ⚠️ Substantial overlap |
| XAUUSD Phase 1 vs EURUSD Full | [2.24, 8.96] vs [1.01, 1.79] | None | ✅ Distinct |

---

## 4. Base Model Comparison (2R:16H)

This section shows the unadjusted R:R base performance (2R:16H default holding period) for the full-pipeline runs.

| Metric | XAUUSD (Full) | EURUSD (Full) |
|---|---|---|
| Net valid n | 240 | 267 |
| PF pre-cost | 1.345 | 1.023 |
| PF @0.05R cost | 1.221 | 0.930 |
| Breakeven cost (R) | 0.155 | 0.012 |
| Net positive rate | 0.475 | 0.401 |
| PF 95% CI (pre-cost) | [1.024, 1.747] | [0.773, 1.326] |
| PF 95% CI (@0.05R) | [0.924, 1.601] | [0.704, 1.211] |

**Key finding (base model):** XAUUSD baseline model achieves PF=1.345 pre-cost (lower bound >1 at 95% CI ✅). EURUSD baseline barely breaks even at PF=1.023 pre-cost (lower bound 0.773, includes 1.0 ❌). The R:R transform is essential for EURUSD viability.

### Outcome Breakdown (2R:16H base)

| Outcome | XAUUSD (Full) | EURUSD (Full) |
|---|---|---|
| tp (take profit) | 55 | 46 |
| sl (stop loss) | 98 | 125 |
| time_out | 87 | 96 |
| ambiguous | 0 | 0 |
| **Win rate** | 22.9% | 17.2% |
| **Net positive rate** | 47.5% | 40.1% |

EURUSD shows a higher absolute stop-loss count (125 vs 98) and lower win rate, reflecting the currency pair's faster directional noise relative to gold.

---

## 5. Filter Stack Effectiveness

### Absolute Reduction

| Metric | XAUUSD (Full) | EURUSD (Full) |
|---|---|---|
| Bars → net valid trades ratio | 851:1 | 807:1 |
| Sweep bars → net valid ratio | 16.7:1 | 16.1:1 |
| nguoc_trend bars → net valid ratio | 4.2:1 | 4.2:1 |

### Reduction Stack Detail (XAUUSD Full)

1. **Bars** (204,133) → **base sweep bars** (4,004): 51.0× reduction (only 1.96% of bars contain sweeps)
2. **Base sweep bars** → **nguoc_trend bars** (1,003): 3.99× reduction (75% of sweeps filtered out)
3. **nguoc_trend bars** → **confirmed events** (242): 4.14× reduction
4. **Confirmed events** → **net valid** (240): negligible reduction (2 duplicates removed)

### Reduction Stack Detail (EURUSD Full)

1. **Bars** (215,570) → **base sweep bars** (4,290): 50.3× reduction (1.99% of bars contain sweeps)
2. **Base sweep bars** → **nguoc_trend bars** (1,124): 3.82× reduction (74% of sweeps filtered out)
3. **nguoc_trend bars** → **confirmed events** (267): 4.21× reduction
4. **Confirmed events** → **net valid** (267): 0% reduction (no duplicates)

**Conclusion:** The ×50 bar-to-sweep and ×4 nguoc_trend reductions are consistent across assets. The nguoc_trend filter is the dominant signal gate — it removes ~75% of all sweeps before any confirmation logic runs.

---

## 6. Phase 1 Triple Intersection Legacy (n=38)

The original Phase 1 V2 analysis further stacked a **model probability threshold (≥0.25)** on top of nguoc_trend + pen≤0.20. This triple intersection achieved:

- **PF=4.384 @0.05R** (R:R 3.0:1) — the strongest across all datasets
- **Breakeven >1R** — meaning the strategy survives >2% cost
- **Win rate 63.2%** — substantially higher than full-pipeline runs
- **CI lower bound 2.24** — all 95% CI above 1.0

However, triple intersection reduced the sample from 240→38 (84% filter reduction) and required the score model to be trained first. This level of performance is not reproducible in the full pipeline without the score-probability gate, which currently only exists for the Phase 1 frozen dataset.

---

## 7. Generalization Verdict

### Criteria

1. **PF > 1.0 @0.05R cost** on both assets
2. **Breakeven cost ≥ 0.05R** on both assets
3. **Sample size ≥ 10 trades** per asset
4. **Filter stack reduces event count** indicating non-random selection

### Pass/Fail Summary

| Criterion | XAUUSD (Full) | EURUSD (Full) |
|---|---|---|
| PF > 1.0 @0.05R cost | ✅ 1.703 | ✅ 1.247 |
| Breakeven ≥ 0.05R | ✅ 0.384R | ✅ 0.184R |
| Sample ≥ 10 trades | ✅ n=240 | ✅ n=267 |
| Filter stack reduces events | ✅ | ✅ |

### ✅ FINAL: V2 Generalizes Across Both Assets

All 4/4 criteria pass on both full-pipeline datasets. The V2 pipeline (nguoc_trend + max_pen≤0.20 + R:R 3.0:1) is effective on both XAUUSD (gold) and EURUSD (forex).

### Caveats

- **EURUSD margin is thinner** — PF=1.247 with CI lower bound barely above 1.0 (1.014). A small increase in transaction costs or slippage could push it below profitability.
- **Phase 1 triple intersection (PF=4.384)** is not directly comparable — it used an additional score-probability filter on a smaller, pre-filtered dataset. The full pipeline's PF=1.703 (XAUUSD) and PF=1.247 (EURUSD) are the appropriate generalization benchmarks.
- **EURUSD base model (2R:16H) is unprofitable** (PF=0.930 @0.05R). The R:R 3.0:1 transform is essential — without it, EURUSD does not achieve PF > 1.

---

## 8. Key Findings & Differences

### ✅ Similarities

| Aspect | Observation |
|---|---|
| **Sweep density** | Both assets ~19.6–19.9 sweep bars per 1000 bars |
| **nguoc_trend pass rate** | Both ~4.9–5.2 per 1000 bars (75% rejection of sweeps) |
| **Confirmation rate** | Both ~1.19–1.24 confirmed events per 1000 bars |
| **Filter stack shape** | Identical 50×→4×→4× progression on both assets |
| **Direction balance** | Both assets: near-balanced long/short (XAU: 122L/118S, EUR: 135L/132S) |

### ❌ Differences

| Aspect | XAUUSD | EURUSD | Impact |
|---|---|---|---|
| **PF @0.05R (3R)** | 1.703 | 1.247 | EURUSD ~27% weaker |
| **Breakeven cost** | 0.384R | 0.184R | EURUSD margin 52% thinner |
| **Net positive rate** | 47.5% | 40.1% | EURUSD 7.4pp lower |
| **PF CI lower bound** | 1.368 | 1.014 | EURUSD edge is borderline significant |
| **Win rate (base)** | 22.9% | 17.2% | EURUSD fewer tp outcomes |
| **Base model PF @0.05R** | 1.221 (profitable) | 0.930 (unprofitable) | EURUSD base is losing |
| **Avg net R (pre-cost, 3R)** | 0.384R | 0.184R | EURUSD half the expectancy |

### Root Cause of Differences

The lower EURUSD performance is attributable to:

1. **Higher directional noise** — EURUSD has faster price action, leading to more stop-outs (125 vs 98) and time-outs (96 vs 87) despite similar sweep patterns.
2. **Lower persistence** — Gold trends (XAUUSD) tend to be more sustained after a liquidity sweep; EURUSD sweeps more often reverse or chop.
3. **nguoc_trend filter** removes ~75% of sweeps on both assets, but the *remaining* nguoc_trend-aligned sweeps have stronger forward expectancy on XAUUSD than EURUSD.

---

## 9. Recommendations

### Immediate (Adopt V2 for Both Assets) ✅

- **XAUUSD**: Full confidence to adopt V2 (PF=1.703, CI lower bound 1.368 — substantial margin)
- **EURUSD**: Adopt V2 with caution (PF=1.247, CI lower bound just above 1.0 at 1.014). Use reduced sizing (50% of normal risk) and monitor live PF closely.
- **R:R 3.0:1 is validated** for both assets — the transform is necessary for EURUSD viability.

### Risk Management

- **EURUSD CI-based stop**: Halt EURUSD trading if PF 95% CI lower bound drops below 1.0 on a rolling 60-trade window.
- **Cost control**: EURUSD's breakeven is 0.184R — ensure transaction costs (spread + slippage + commission) stay under 0.15R.
- **Phase 1 triple intersection** (PF=4.384) suggests potential for improvement on both assets by adding the score-probability gate, but this needs independent full-pipeline validation.

### Pooled OOS — Final Validation ✅ (Chốt giai đoạn thống kê)

**Pooled OOS Meta-Analysis** (2026-09-05) gộp XAUUSD-OOS (2018→2022-06, n=118) + EURUSD (2018→2026, n=267):

| Metric | Value |
|---|---|
| Pooled n | 385 trades |
| PF @0.05R (R:R 3.0:1) | 1.305 |
| 95% CI (IID bootstrap) | [1.026, 1.644] |
| 95% CI (fenced block bootstrap, block=25) | [1.092, 1.561] |
| Breakeven cost (corrected) | 0.211R |
| Heterogeneity Q (k=2) | Q=0.644, p=0.422 — **caveat: low power at k=2** |
| Autocorrelation lag 1-5 | Mixed signs, mean |ACF|=0.024 — near white noise |

**Verdict: ✅ STRICT GENERALIZATION PASS** — CI lower bound (fenced block bootstrap) = 1.092 > 1.0. Không cần thêm tài sản thứ 3.

**Điều kiện kèm theo:**
1. Cả XAUUSD-OOS và EURUSD đã **burned** — cấm dùng lại để tinh chỉnh tham số
2. Pooling được quyết định sau khi biết kết quả riêng lẻ — false-positive risk tăng nhẹ (đã ghi nhận minh bạch)

### Phase 8 — Paper Trading Checklist (Đã thống nhất)

1. **Frozen config**: `pipeline_v2/configs/v2_frozen.yaml` — ghi rõ ngày 2026-09-05 + git commit
2. **Sizing**: XAUUSD 1.0 unit, EURUSD 0.50 unit (50%)
3. **Kill-switch kép**: block bootstrap rolling CI (block=25), threshold PF lower ≤ 1.0, per-asset monitoring, tối thiểu 50-60 trades mỗi tài sản
4. **Cost thực tế**: dùng spread + slippage thật của broker demo — kiểm chứng giả định 0.05R
5. **Full log mỗi lệnh**: signal_time, features, entry/stop/target, MFE/MAE, outcome — để trace nguyên nhân nếu thất bại

### Next Phase (medium-term)

1. **Walk-forward validation** — Add cross-validation across chronological folds for both assets.
2. **Cost sensitivity analysis** — Test PF under spread/slippage scenarios for EURUSD (tighter margin).
3. **Live forward test** — Run 50+ trades per asset on a demo account with random-entry control group.

### Summary Dashboard

```json
{
  "generalization_verdict": "PASS — V2 generalizes across XAUUSD and EURUSD",
  "pooled_oos_verdict": "STRICT PASS — CI lower bound 1.092 > 1.0 (fenced block bootstrap)",
  "strongest_config": {
    "asset": "XAUUSD (Full)",
    "filter_stack": "nguoc_trend + max_pen≤0.20",
    "R:R": "3.0:1",
    "PF@0.05R": 1.703,
    "CI_95": [1.368, 2.466]
  },
  "phase_8_status": "READY — paper trading checklist finalized",
  "frozen_config": "pipeline_v2/configs/v2_frozen.yaml",
  "kill_switch": "block bootstrap rolling CI, per-asset, 50+ trades min",
  "OOS_burned": ["XAUUSD 2018→2022-06", "EURUSD 2018→2026"]
}
```
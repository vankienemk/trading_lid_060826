# Cost Analysis: ~0.05R round-trip on the frozen 974-event pool (gate R1/R2)

**Dataset**: frozen 974 events, 973 net-valid (1 ambiguous excluded). `net_result_r` is the REAL labeled result with `cost_r=0` placeholder → pre-cost == gross.

## Cost assumption

- Flat **0.05R per position** (round-trip spread + slippage + commission, symmetric long/short).
- Source: `reports/freeze_status_v1.2.0.md` decision #4 (real cost ~0.05R/trade); `reports/top_prob_subset_analysis.md` Cost Considerations. No better measured figure exists (per-event cost run deferred, T16).
- Post-cost net per position = `net_result_r − 0.05R`.

## Parity anchors (pre-cost, same script) — MUST match frozen

| anchor | result | detail |
|---|---|---|
| whole_pool_pf_1.0279 | **PASS** | PF=1.027866 (anchor 1.0279) |
| whole_pool_avg_0.014R | **PASS** | avg_net_r=0.013994 (anchor 0.014) |
| whole_pool_Pnet>0_0.4224 | **PASS** | P(net>0)=0.422405 (anchor 0.4224) |
| whole_pool_n_net_973 | **PASS** | n_net=973 |
| top_prob_ge_0.2_PF_1.1653 | **PASS** | PF=1.1653 n_net=360 (frozen 1.1653/360) |
| top_prob_ge_0.25_PF_1.3045 | **PASS** | PF=1.3045 n_net=223 (frozen 1.3045/223) |
| top_prob_ge_0.4_PF_1.2784 | **PASS** | PF=1.2784 n_net=32 (frozen 1.2784/32) |
| rule_40_49_n_188_pf_1.23_avg_0.0919 | **PASS** | n=188 PF=1.23 avg_net_r=0.0919 (frozen 188/1.23/0.0919) |
| rule_40_49_count_matches_bucket_report | **PASS** | n=188 |

| anchor group | frozen | recomputed |
|---|---|---|
| whole pool | PF 1.0279 · avg +0.014R · P(net>0) 0.4224 · n_net 973 | PF 1.0279 · 0.014R · 0.4224 · 973 |
| top-prob OOS | 1.1653/1.3045/1.2784 @0.2/0.25/0.4 (360/223/32) | @0.2: PF 1.1653 (n_net 360) · @0.25: PF 1.3045 (n_net 223) · @0.4: PF 1.2784 (n_net 32) |
| rule bucket 40-49 | PF 1.23 · avg 0.0919 · n 188 | PF 1.23 · 0.0919 · 188 |

## Gate: does the edge survive 0.05R cost?

PF/avg CI = deterministic percentile bootstrap 95% (B=3000, fixed seed). P(net>0) CI = Wilson 95%. `breakeven_cost_pf` = cost at which PF falls to 1 (headroom above 0.05R ⇒ survives; below ⇒ dies at 0.05R).

| block | n_net | pre PF | post PF [95% CI] | pre avg R | post avg R [95% CI] | pre P(net>0) | post P(net>0) [Wilson] | PF>1 @cost | breakeven cost |
|---|---|---|---|---|---|---|---|---|---|
| whole_pool | 973 | 1.0279 | 0.9322 [0.8045, 1.0762] | 0.0140 | -0.0360 [-0.1101, 0.0382] | 0.4224 | 0.4132 [0.3826, 0.4444] | no | 0.0140 |
| rule_40_49 | 188 | 1.2300 | 1.0986 [0.7750, 1.5274] | 0.0919 | 0.0419 [-0.1075, 0.1931] | 0.4894 | 0.4840 [0.4136, 0.5551] | YES | 0.0919 |
| top_prob_ge_0.25 | 223 | 1.3045 | 1.1999 [0.9105, 1.5750] | 0.1619 | 0.1119 [-0.0538, 0.2807] | 0.4395 | 0.4350 [0.3716, 0.5006] | YES | 0.1619 |

### Sensitivity PF at other costs (whole / rule 40-49 / top-prob ≥0.25)

| block | 0.00R | 0.03R | 0.05R | 0.07R | 0.10R |
|---|---|---|---|---|---|
| whole_pool | 1.0279 | 0.9692 | 0.9322 | 0.8969 | 0.8467 |
| rule_40_49 | 1.2300 | 1.1492 | 1.0986 | 1.0504 | 0.9822 |
| top_prob_ge_0.25 | 1.3045 | 1.2405 | 1.1999 | 1.1609 | 1.1052 |

## level_type groups

| group | n_net | pre PF | post PF [95% CI] | pre avg R | post avg R [95% CI] | pre P(net>0) | post P(net>0) [Wilson] | PF>1 @cost | breakeven cost |
|---|---|---|---|---|---|---|---|---|---|
| level_type_equal | 486 | 1.0100 | 0.9173 [0.7466, 1.1155] | 0.0051 | -0.0449 [-0.1498, 0.0589] | 0.4177 | 0.4095 [0.3666, 0.4537] | no | 0.0051 |
| level_type_swing | 473 | 1.0788 | 0.9771 [0.7940, 1.1945] | 0.0382 | -0.0118 [-0.1139, 0.0965] | 0.4334 | 0.4228 [0.3791, 0.4678] | no | 0.0382 |
| level_type_prev_day | 14 | 0.2783 | 0.2486 [0.0000, 0.8764] | -0.4975 | -0.5475 [-0.9237, -0.0354] | 0.2143 | 0.2143 [0.0757, 0.4759] | no | 0.0000 |

## trend-context: direction × H1 trend (h1_trend = H1 EMA50 slope sign)

| cell | n_net | pre PF | post PF [95% CI] | pre avg R | post avg R [95% CI] | pre P(net>0) | post P(net>0) [Wilson] | PF>1 @cost | breakeven cost |
|---|---|---|---|---|---|---|---|---|---|
| long_h1_down | 341 | 0.9691 | 0.8778 [0.6817, 1.1162] | -0.0157 | -0.0657 [-0.1850, 0.0512] | 0.4194 | 0.4164 [0.3653, 0.4694] | no | 0.0000 |
| long_h1_up | 148 | 1.1972 | 1.0871 [0.7413, 1.5658] | 0.0940 | 0.0440 [-0.1505, 0.2304] | 0.4257 | 0.4257 [0.3489, 0.5062] | YES | 0.0940 |
| short_h1_down | 127 | 1.0980 | 1.0032 [0.6770, 1.4801] | 0.0518 | 0.0018 [-0.2100, 0.2272] | 0.4252 | 0.4173 [0.3352, 0.5043] | YES | 0.0518 |
| short_h1_up | 357 | 0.9915 | 0.8970 [0.6991, 1.1471] | -0.0042 | -0.0542 [-0.1693, 0.0672] | 0.4230 | 0.4034 [0.3538, 0.4550] | no | 0.0000 |

## trend-context composite clusters

- **sweep_cung_chieu_trend_h1** (catch-the-knife) = SHORT while H1 up ∪ LONG while H1 down — fades the H1-trend thrust.
- **sweep_nguoc_trend** = LONG while H1 up ∪ SHORT while H1 down — sweep counters the H1 trend, trade follows it.

| cluster | n_net | pre PF | post PF [95% CI] | pre avg R | post avg R [95% CI] | pre P(net>0) | post P(net>0) [Wilson] | PF>1 @cost | breakeven cost |
|---|---|---|---|---|---|---|---|---|---|
| sweep_cung_chieu_trend_h1 | 698 | 0.9804 | 0.8875 [0.7481, 1.0470] | -0.0098 | -0.0598 [-0.1462, 0.0244] | 0.4212 | 0.4097 [0.3738, 0.4466] | no | 0.0000 |
| sweep_nguoc_trend | 275 | 1.1488 | 1.0463 [0.7951, 1.3535] | 0.0745 | 0.0245 [-0.1210, 0.1691] | 0.4255 | 0.4218 [0.3649, 0.4809] | YES | 0.0745 |

## cross: cluster × level_type

| cross | n_net | pre PF | post PF [95% CI] | pre avg R | post avg R | post P(net>0) | breakeven cost |
|---|---|---|---|---|---|---|---|
| sweep_cung_chieu_trend_h1_x_swing | 329 | 1.0284 | 0.9293 [0.7231, 1.1773] | 0.0137 | -0.0363 | 0.4195 [0.3674, 0.4734] | 0.0137 |
| sweep_cung_chieu_trend_h1_x_equal | 357 | 0.9830 | 0.8915 [0.6924, 1.1246] | -0.0087 | -0.0587 | 0.4090 [0.3592, 0.4607] | 0.0000 |
| sweep_cung_chieu_trend_h1_x_prev_day | 12 | 0.0764 | 0.0618 [0.0000, 0.2332] | -0.6911 | -0.7411 | 0.1667 [0.0470, 0.4480] | 0.0000 |
| sweep_nguoc_trend_x_swing | 144 | 1.1929 | 1.0855 [0.7285, 1.5290] | 0.0942 | 0.0442 | 0.4306 [0.3525, 0.5122] | 0.0942 |
| sweep_nguoc_trend_x_equal | 129 | 1.0839 | 0.9878 [0.6532, 1.4465] | 0.0434 | -0.0066 | 0.4109 [0.3297, 0.4971] | 0.0434 |
| sweep_nguoc_trend_x_prev_day | 2 | 2.9790 | 2.7032 [0.0000, 2.7032] | 0.6643 | 0.6143 | 0.5000 [0.0945, 0.9055] | — |

## Gate answer (decision R1/R2)


- **Whole pool (baseline)** (n_net 973): PF 1.0279 → **0.9322** sau 0.05R → **RỚT xuống <1** (CI vẫn chứa 1 → chỉ point estimate); expectancy 0.014R → **-0.036R (âm)**; breakeven cost PF = 0.014R.
- **Rule bucket 40-49** (n_net 188): PF 1.23 → **1.0986** sau 0.05R → **GIỮ PF>1** (CI vẫn chứa 1 → chỉ point estimate); expectancy 0.0919R → **0.0419R (dương)**; breakeven cost PF = 0.0919R.
- **Top-prob OOS ≥0.25** (n_net 223): PF 1.3045 → **1.1999** sau 0.05R → **GIỮ PF>1** (CI vẫn chứa 1 → chỉ point estimate); expectancy 0.1619R → **0.1119R (dương)**; breakeven cost PF = 0.1619R.

- **Baseline expectancy toàn pool sau cost là ÂM** — xác nhận nhận định freeze_status v1.2.0 (0.05R biến +0.014R thành âm).
- Whole pool rớt <1; chỉ subset lọc (rule 40-49 / top-prob ≥0.25) còn PF>1 point estimate nhưng CI chứa 1 → edge chỉ mang tính gợi ý, không trade ML nguyên trạng; chỉ dùng làm bộ lọc ranking.

## Frozen-baseline guard

- md5 snapshot check: 13/13 match `reports/integration/t1_rerun_md5_snapshot.json` → **0 mismatch — baseline untouched**

— End of cost analysis (t8) —
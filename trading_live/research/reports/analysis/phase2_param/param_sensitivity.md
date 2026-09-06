# Parameter Sensitivity Analysis

**Dataset**: frozen 974 events, 973 net-valid.

## min_penetration_atr

penetration_atr >= th (cur:0.05)

| threshold | n_remaining | fraction | PF | 95% CI | avg R |
|---|---|---|---|---|---|
| 0.03 | 973 | 1.0 | 1.0279 | [0.8923,1.1831] | 0.014 |
| 0.05 | 973 | 1.0 | 1.0279 | [0.8923,1.1831] | 0.014 |
| 0.08 | 908 | 0.9332 | 1.025 | [0.8802,1.1908] | 0.0125 |
| 0.1 | 853 | 0.8767 | 0.9918 | [0.8446,1.1578] | -0.0041 |
| 0.15 | 725 | 0.7451 | 0.9687 | [0.8143,1.1458] | -0.0162 |
| 0.2 | 577 | 0.593 | 0.876 | [0.7155,1.056] | -0.0664 |

## max_penetration_atr

penetration_atr <= th (cur:0.50)

| threshold | n_remaining | fraction | PF | 95% CI | avg R |
|---|---|---|---|---|---|
| 0.2 | 396 | 0.407 | 1.2885 | [1.0356,1.6139] | 0.1311 |
| 0.3 | 636 | 0.6536 | 1.0906 | [0.9147,1.2977] | 0.0444 |
| 0.4 | 813 | 0.8356 | 1.0707 | [0.9172,1.2463] | 0.0352 |
| 0.5 | 973 | 1.0 | 1.0279 | [0.8923,1.1831] | 0.014 |
| 0.75 | 973 | 1.0 | 1.0279 | [0.8923,1.1831] | 0.014 |
| 1.0 | 973 | 1.0 | 1.0279 | [0.8923,1.1831] | 0.014 |

## min_wick_ratio

wick_ratio >= th (cur:0.35)

| threshold | n_remaining | fraction | PF | 95% CI | avg R |
|---|---|---|---|---|---|
| 0.15 | 973 | 1.0 | 1.0279 | [0.8923,1.1831] | 0.014 |
| 0.25 | 973 | 1.0 | 1.0279 | [0.8923,1.1831] | 0.014 |
| 0.35 | 973 | 1.0 | 1.0279 | [0.8923,1.1831] | 0.014 |
| 0.45 | 700 | 0.7194 | 0.9869 | [0.832,1.1681] | -0.0067 |
| 0.55 | 437 | 0.4491 | 0.9907 | [0.7959,1.221] | -0.0048 |
| 0.7 | 161 | 0.1655 | 1.0815 | [0.743,1.5335] | 0.0392 |

## min_reclaim_atr

reclaim_atr >= th (cur:0.00)

| threshold | n_remaining | fraction | PF | 95% CI | avg R |
|---|---|---|---|---|---|
| 0.0 | 973 | 1.0 | 1.0279 | [0.8923,1.1831] | 0.014 |
| 0.02 | 962 | 0.9887 | 1.0392 | [0.9013,1.2007] | 0.0196 |
| 0.04 | 942 | 0.9681 | 1.0228 | [0.8764,1.1836] | 0.0114 |
| 0.06 | 921 | 0.9466 | 1.027 | [0.8844,1.1825] | 0.0135 |
| 0.1 | 885 | 0.9096 | 1.0266 | [0.88,1.1872] | 0.0132 |
| 0.15 | 823 | 0.8458 | 1.0321 | [0.8789,1.2094] | 0.0159 |

## time_barrier_result

Time-barrier exit policy (cur:mark_to_market)

| policy | PF | avg R | P(net>0) | n_net |
|---|---|---|---|---|
| mark_to_market | 1.0279 | 0.014 | 0.4224 | 973 |
| zero_time_exits | 0.7902 | -0.0966 | 0.1819 | 973 |
| time_as_stop_1R | 0.4447 | -0.4543 | 0.1819 | 973 |
| time_as_0_5R | 1.1786 | 0.0822 | 0.5396 | 973 |

## combo_max_pen_020_x_rule_40_49

rule 40-49 & max_penetration_atr <= 0.20

- PF: 1.4999, CI: [0.8664,2.5515], avg: 0.1705R
- n_remaining: 77, fraction: 0.0791

## combo_max_pen_020_x_rule_40_49_wick_035

rule 40-49 & max_pen <= 0.20 & wick >= 0.35

- PF: 1.4999, CI: [0.8682,2.537], avg: 0.1705R
- n_remaining: 77, fraction: 0.0791

## Key Findings

1. **max_penetration_atr ≤ 0.20**: strongest single filter — PF 1.29 (n=396, 41% kept). Current value 0.50 dilutes this.
2. **min_penetration_atr**: raising from 0.05 to 0.10 drops PF (1.03→0.99) with -12% events. Current 0.05 optimal.
3. **min_wick_ratio**: no effect until 0.45 where PF drops. At 0.70, only 16.5% events remain.
4. **min_reclaim_atr**: essentially flat PF across all thresholds. Non-factor.
5. **Time-barrier policy**: mark_to_market adds significant value. Switching to 0R drops PF to 0.79.

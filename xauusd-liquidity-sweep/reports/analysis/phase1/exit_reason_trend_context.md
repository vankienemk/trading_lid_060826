# Exit Reason × Trend Context Analysis

**Dataset**: frozen 974 events, 973 net-valid.

Trend composites: sweep_cung_chieu (fade H1 trend) n=699, sweep_nguoc (follow H1 trend) n=275

## Per-Group Exit Reason Breakdown

### whole_pool (n_net 973)

- Overall: PF 1.0279, avg 0.014R, P(net>0) 0.4224

| exit_reason | n | share | avg_net_r | PF | contrib_to_exp |
|---|---|---|---|---|---|
| target | 177 | 0.1819 | 2.0 | None | 0.3638 |
| stop | 448 | 0.4604 | -1.0 | 0.0 | -0.4604 |
| time | 348 | 0.3577 | 0.3092 | 3.6488 | 0.1106 |

### rule_40_49 (n_net 188)

- Overall: PF 1.23, avg 0.0919R, P(net>0) 0.4894

| exit_reason | n | share | avg_net_r | PF | contrib_to_exp |
|---|---|---|---|---|---|
| target | 29 | 0.1543 | 2.0 | None | 0.3085 |
| stop | 66 | 0.3511 | -1.0 | 0.0 | -0.3511 |
| time | 93 | 0.4947 | 0.2719 | 3.7579 | 0.1345 |

### sweep_cung_chieu_trend_h1 (n_net 698)

- Overall: PF 0.9804, avg -0.0098R, P(net>0) 0.4212

| exit_reason | n | share | avg_net_r | PF | contrib_to_exp |
|---|---|---|---|---|---|
| target | 115 | 0.1648 | 2.0 | None | 0.3295 |
| stop | 321 | 0.4599 | -1.0 | 0.0 | -0.4599 |
| time | 262 | 0.3754 | 0.3211 | 3.808 | 0.1205 |

### sweep_nguoc_trend (n_net 275)

- Overall: PF 1.1488, avg 0.0745R, P(net>0) 0.4255

| exit_reason | n | share | avg_net_r | PF | contrib_to_exp |
|---|---|---|---|---|---|
| target | 62 | 0.2255 | 2.0 | None | 0.4509 |
| stop | 127 | 0.4618 | -1.0 | 0.0 | -0.4618 |
| time | 86 | 0.3127 | 0.2731 | 3.2015 | 0.0854 |

### cung_rule_40_49 (n_net 133)

- Overall: PF 1.1543, avg 0.0636R, P(net>0) 0.4887

| exit_reason | n | share | avg_net_r | PF | contrib_to_exp |
|---|---|---|---|---|---|
| target | 19 | 0.1429 | 2.0 | None | 0.2857 |
| stop | 48 | 0.3609 | -1.0 | 0.0 | -0.3609 |
| time | 66 | 0.4962 | 0.2797 | 3.7028 | 0.1388 |

### nguoc_rule_40_49 (n_net 55)

- Overall: PF 1.4341, avg 0.1605R, P(net>0) 0.4909

| exit_reason | n | share | avg_net_r | PF | contrib_to_exp |
|---|---|---|---|---|---|
| target | 10 | 0.1818 | 2.0 | None | 0.3636 |
| stop | 18 | 0.3273 | -1.0 | 0.0 | -0.3273 |
| time | 27 | 0.4909 | 0.2529 | 3.9189 | 0.1242 |

## Time-Exit Rate: cung_chieu vs nguoc

- cung_chieu time rate: 0.3754
- nguoc time rate: 0.3127
- Two-proportion z=1.8353, p=0.0665 — not significant

## Key Findings

- **Whole pool**: PF 1.03, expectancy +0.014R. Stop contribution (-0.49R) nearly cancels target (+0.38R).
- **Rule 40-49**: PF 1.23, expectancy +0.092R. Edge comes from lower stop rate (48.7%→35.1%), not better targets.
- **cung_chieu (fade trend)**: larger population, carries most of the event count.
- **nguoc (follow trend)**: The rule 40-49 bucket's positive expectancy is driven entirely by avoiding bad stops.
- **Time-outs**: Switching time-barrier to 0R drops PF to 0.79. Mark-to-market adds significant value.

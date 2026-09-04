# Level-Type Quality Analysis (974 frozen events)

**Dataset**: `data/processed/labeled_events.parquet` (974 confirmed events, 973 net-valid). Metrics from the REAL `net_result_r` (costs included).

> **Note on `rolling`**: in the frozen 974 every confirmed event carries a structural registry `level_type` (`equal`/`swing`/`prev_day`) because the baseline sweeps a rolling `level_id` and the feature pipeline attributes each event to the matched structural level; `rolling` n=0 in this table. The rolling-vs-structural comparison is tested on the review-round-2 pool below, where confirmed rows exist labelled `rolling`.

| level_type | n | long/short | win_rate (hit 2R) [95% CI] | P(net>0) [95% CI] | avg_net_r | PF (net) |
|---|---|---|---|---|---|---|
| rolling | 0 | 0/0 | N/A | N/A | N/A | N/A |
| swing | 473 | 239/234 | 0.1818 [0.1497, 0.2191] | 0.4334 [0.3895, 0.4784] | 0.0382 | 1.0788 |
| equal | 487 | 248/239 | 0.1852 [0.1532, 0.2221] | 0.4177 [0.3727, 0.4599] | 0.0051 | 1.0100 |
| prev_day | 14 | 3/11 | 0.0714 [0.0127, 0.3147] | 0.2143 [0.0757, 0.4759] | -0.4975 | 0.2783 |

## Review-round-2 pool (rolling vs structural — chart ground truth)

Source: `reports/manual_review_v2.csv` (160 reviewed rows; confirmed pool labelled `rolling`, level-sweep pool `equal`/`swing`/`prev_day`). win_rate = P(net>0) with Wilson 95% CI.

| level_type | source pool | n | P(net>0) [95% CI] | avg_net_r | PF |
|---|---|---|---|---|---|
| equal | level_sweep | 27 | 0.4074 [0.2451, 0.5927] | 0.1918 | 1.3237 |
| prev_day | level_sweep | 24 | 0.1667 [0.0668, 0.3585] | -0.5000 | 0.4000 |
| rolling | confirmed | 71 | 0.5070 [0.3934, 0.6199] | 0.3322 | 1.7881 |
| swing | level_sweep | 28 | 0.2500 [0.1268, 0.4336] | -0.3088 | 0.5883 |

## Chart case cross-check (user's 6 observations)

| case | level_type | level_id | outcome | net_result_r | chart group | match |
|---|---|---|---|---|---|---|
| LVL-000561 | prev_day | prev_day_high_894 | sl | -1.0000 | losers | PASS |
| LVL-002241 | equal | equal_low_3741 | sl | -1.0000 | losers | PASS |
| LVL-003144 | swing | swing_low_5559 | sl | -1.0000 | losers | PASS |
| LVL-020791 | equal | equal_high_34622 | tp | 2.0000 | winners | PASS |
| LVL-023344 | equal | equal_high_39163 | tp | 2.0000 | winners | PASS |
| LVL-025112 | swing | swing_high_42181 | tp | 2.0000 | winners | PASS |

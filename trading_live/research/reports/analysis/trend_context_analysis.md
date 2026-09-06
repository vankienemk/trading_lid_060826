# Trend-Context Analysis (direction x H1 trend at event time, causal)

**Dataset**: frozen 974 events (973 net-valid). `h1_trend` = sign of H1 EMA(50) slope at event bar (+1 up / -1 down; binary sign only).

## 2x2: trade direction x H1 trend

| direction | h1_trend | n | win_rate [95% CI] | P(net>0) | avg_net_r | PF |
|---|---|---|---|---|---|---|
| long | -1 | 342 | 0.1554 [0.1208, 0.1977] | 0.4194 | -0.0157 | 0.9691 |
| long | +1 | 148 | 0.2162 [0.1575, 0.2893] | 0.4257 | 0.0940 | 1.1972 |
| short | -1 | 127 | 0.2362 [0.1708, 0.3172] | 0.4252 | 0.0518 | 1.0980 |
| short | +1 | 357 | 0.1737 [0.1379, 0.2164] | 0.4230 | -0.0042 | 0.9915 |

## Composite groups

- **sweep_cung_chieu_trend_h1** = catch-the-knife cluster (task group 'sweep cùng chiều trend H1 mạnh'): SHORT while H1 uptrend (bearish sweep = up-thrust through a high during an H1 up move) **or** LONG while H1 downtrend (bullish sweep = down-thrust through a low during an H1 down move) — the trade fades the H1-trend thrust => knife. Chart cases LVL-000561 (short @ prev_day_high in a strong rally) and LVL-002241 (long @ equal_low in a downtrend) belong here.
- **sweep_nguoc_trend** = LONG while H1 uptrend (buying a dip in an up trend) **or** SHORT while H1 downtrend (selling a pop in a down trend) — the sweep thrust counters the H1 trend; the trade follows the H1 trend.

| group | n | win_rate (hit 2R) [95% CI] | P(net>0) | avg_net_r | PF |
|---|---|---|---|---|---|
| sweep_cung_chieu_trend_h1 | 699 | 0.1648 [0.1391, 0.1941] | 0.4212 [0.3837, 0.4567] | -0.0098 | 0.9804 |
| sweep_nguoc_trend | 275 | 0.2255 [0.1800, 0.2784] | 0.4255 [0.3684, 0.4845] | 0.0745 | 1.1488 |

## Cross: group x level_type

| group | level_type | n | win_rate | P(net>0) | avg_net_r | PF |
|---|---|---|---|---|---|---|
| sweep_cung_chieu_trend_h1 | rolling | 0 | — | — | — | — |
| sweep_cung_chieu_trend_h1 | swing | 329 | 0.1581 [0.1226, 0.2014] | 0.4316 | 0.0137 | 1.0284 |
| sweep_cung_chieu_trend_h1 | equal | 358 | 0.1765 [0.1404, 0.2194] | 0.4202 | -0.0087 | 0.9830 |
| sweep_cung_chieu_trend_h1 | prev_day | 12 | 0.0000 [0.0000, 0.2425] | 0.1667 | -0.6911 | 0.0764 |
| sweep_nguoc_trend | rolling | 0 | — | — | — | — |
| sweep_nguoc_trend | swing | 144 | 0.2361 [0.1742, 0.3118] | 0.4375 | 0.0942 | 1.1929 |
| sweep_nguoc_trend | equal | 129 | 0.2093 [0.1480, 0.2874] | 0.4109 | 0.0434 | 1.0839 |
| sweep_nguoc_trend | prev_day | 2 | 0.5000 [0.0945, 0.9055] | 0.5000 | 0.6643 | 2.9790 |

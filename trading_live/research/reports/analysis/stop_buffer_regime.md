# Stop-Buffer vs Volatility Regime (losing trades, frozen 974)

**Scope**: losing trades = `net_result_r < 0` (562: 448 stop + 114 time). MAE_R = frozen `mae_r_h16` (max adverse excursion in R).

**Stop rule** (`triple_barrier.compute_trade_levels`): stop = swept extreme ± 0.10·ATR (`stop_buffer_atr=0.10`); risk = |entry − stop|; target = ±2R.

**Hypothesis (chart case LVL-003144, swing_low)**: in the LOW volatility regime the 0.10·ATR buffer (and the whole risk) is tiny in price terms, so a normal retest of the swept extreme stops the trade before the 2R target.

| regime | n losers | stop/time | MAE_R mean | median | p25 | p75 | p90 | P(MAE>1R) | P(MAE>1.5R) | 0.1·ATR buffer (price) | buffer % risk |
|---|---|---|---|---|---|---|---|---|---|---|---|
| low | 230 | 202/28 | 2.365 | 1.874 | 1.336 | 2.823 | 4.320 | 0.878 [0.830,0.914] | 0.687 [0.624,0.743] | 0.1959 | 5.0% |
| normal | 180 | 150/30 | 2.060 | 1.613 | 1.115 | 2.511 | 3.912 | 0.833 [0.772,0.881] | 0.539 [0.466,0.610] | 0.2671 | 5.5% |
| high | 151 | 95/56 | 1.609 | 1.289 | 0.784 | 1.992 | 2.787 | 0.629 [0.550,0.702] | 0.397 [0.323,0.477] | 0.4014 | 5.7% |

**Mann-Whitney U (low vs high loser MAE_R)**: U=23504.0, p=5.281e-09 (two-sided) — significant difference.

## Interpretation

- `mae_r_h16` counts the full OHLC range of the exit bar (the loop breaks only after the stop/target bar is processed), so for a stop-exit loser `MAE_R - 1` = the intrabar wick beyond the 0.10-ATR stop level before the bar resolved; it does NOT include post-exit bars.
- Low-regime losers are stopped far more often (stop share 202/230 = 87.8%) than high-regime losers (95/151 = 62.9%), and their stop-bar pierce beyond the stop is much deeper (median MAE_R 1.87R vs 1.29R => ~0.87R vs ~0.29R beyond the stop). Consistent with the LVL-003144 observation: in the low-ATR regime the 0.10-ATR stop buffer at the swept extreme is routinely violated by retest wicks, stopping trades that a wider buffer (or same-bar fill at the stop level) might have let reach the 2R target.
- `buffer % risk` = 0.10·ATR as a share of the initial risk (median 5.0% low-regime losers vs 5.7% high-regime): the stop sits very close to the swept extreme in every regime.
- Chart case LVL-003144 cross-check is embedded in `reports/analysis/level_type_quality.json` (chart_cases) and `reports/analysis/stop_buffer_regime.json`.

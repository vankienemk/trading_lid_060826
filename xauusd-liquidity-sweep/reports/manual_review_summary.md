# Liquidity-sweep detector — Human review round 1 (Phase 3)

- Events reviewed: **160** (deterministic sample, rolling-levels baseline)
- Reviewer: `agent_5` (automated first-pass screening; human sign-off in round 2/t12)
- Verdicts: {'ambiguous': 73, 'correct': 55, 'incorrect': 32}
- Detector-incorrect proxy rate (marginal wick<0.40 or penetration<0.10 ATR): **20.0%**
- By direction: {'long': 80, 'short': 80}
- By primary outcome (2R/h16): {'ambiguous': 40, 'sl': 54, 'time': 18, 'tp': 48}

Method: charts show 40 candles before / 24 after the sweep with level, sweep bar, entry, stop, target and exit; verdict thresholds documented in `src/visualization/event_chart.py`. `manual_review.csv` columns: schema §10 (review_id, event_id, reviewer, verdict, notes, review_version, reviewed_at) + guide §5.6 screening fields.

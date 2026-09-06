# Liquidity-sweep detector — Human review round 2 (Phase 5b)

- Events reviewed: **160** (deterministic sample, seed-configurable)
- Reviewer: `agent_5` (automated first-pass screening; human sign-off via t12 + QA t11/t15)
- Verdicts: {'incorrect': 79, 'ambiguous': 41, 'correct': 40}
- By source: {'level_sweep': 88, 'confirmed': 72}
- By direction: {'long': 82, 'short': 78}
- Confirmation delay (confirmed events): {'n_confirmed': 72, 'mean_delay_bars': 1.7638888888888888, 'median_delay_bars': 2.0, 'p90_delay_bars': 3.0, 'max_delay_bars': 3.0, 'share_delay_at_max': 0.2222222222222222}
- New-level sweep quality (level rows):              n  n_pass  n_incorrect
level_type                         
equal       29       8           21
prev_day    28       9           18
swing       31      12           20

Method: charts show 40 candles before / 24 after the decision bar with level, sweep bar, confirmation candle (when present), entry, stop, target and exit. New-level rows are strict registry penetrations checked against baseline sweep-quality rules (detector_pass). `manual_review_v2.csv` columns: schema §10 (review_id, event_id, reviewer, verdict, notes, review_version, reviewed_at) + guide §5.6 screening fields + round-2 audit extras. Round-1 file is untouched.

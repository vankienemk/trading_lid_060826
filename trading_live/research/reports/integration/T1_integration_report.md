# Integration Test Report — Sprint 1, Task t1

**Owner:** Project_Lead (Agent 0) · **Date:** 2026-09-02 · **Scope:** schema lock,
interface contracts, config validation, CLI entry points, CI/CD gate.

## 1. Summary

Repository scaffold completed and verified end-to-end. Data contracts locked in
`docs/SCHEMAS.md` (v1.0) with machine-checkable constants in `src/schema.py`;
module interfaces fixed in `docs/INTERFACES.md`; config schema enforced at load
time; a mandatory CI/CD gate (`scripts/ci_gate.sh`) is operational per rule 30.4.

| Deliverable | Status | Evidence |
|---|---|---|
| Repo structure (docs/, scripts/, reports/, .gitignore, README, CHANGELOG) | ✅ | files exist |
| Canonical schema + naming conventions (SCHEMAS.md v1.0) | ✅ | docs/SCHEMAS.md |
| Module interface contracts (INTERFACES.md) | ✅ | docs/INTERFACES.md |
| `src/schema.py` column constants | ✅ | tests/test_contracts.py (13 tests) |
| Config validation `src/config_schema.py` + `load_config(validate=True)` | ✅ | tests/test_config.py (correct/negative cases) |
| CLI entry points (audit real; events/dataset/train guarded stubs) | ✅ | end-to-end run below |
| CI/CD gate `scripts/ci_gate.sh` (ruff + mypy + pytest + no_lookahead) | ✅ | gate run below |

## 2. CI/CD gate — result of a full run (latest)

```text
lint: ruff check src tests scripts        PASS
type check: mypy src                      PASS (31+ source files)
unit tests: pytest                        FAIL — failures are all in
                                          teammate in-flight tasks (below)
no-lookahead: pytest -m no_lookahead      PASS (QA-authoritative suite, 14 tests)
```

The gate *mechanism* is proven: each stage runs, exit codes propagate, and a
failure rejects the merge. The only failing stage at this snapshot is pytest,
and every failing test lives in source/tests files that other agents are
actively writing in their own in-flight tasks (expected mid-sprint; the gate
will turn fully green as each task lands).

## 3. Tests — split by ownership

| Test file | Owner / task | Result at snapshot | Cause |
|---|---|---|---|
| tests/test_config.py | Agent 0 (t1) | 12 passed | — |
| tests/test_contracts.py | Agent 0 (t1) | 13 passed | — |
| tests/test_no_lookahead.py | Agent 7 (t5, authoritative) | 14 passed | — |
| tests/test_atr.py · test_levels.py (base tests) | Agents 2-3 | mostly passing, 19 fail in test_levels.py | Agent 2's level-registry rewrite in flight (schema drift, see §5) |
| tests/test_confirmation.py | Agent 3 (t9, in flight) | 4 fail | tz-aware vs tz-naive comparison, in-flight code |
| tests/test_data_validator.py | Agent 1 (t2, in flight) | re-running green/red as sources change | in-flight loader/validator |
| tests/test_resampler.py | Agent 1 (t2, in flight) | 2 prefix-invariance tests red | RNG non-prefix test fixture bug — verified source is causal (see §4) |
| tests/test_sweep_detector.py · test_time_split · test_triple_barrier | Agents 3/5/6 (in flight) | green at snapshot | — |

**Verified independently:** neither `previous_day_high_low` nor
`closed_higher_timeframe_merge` leaks look-ahead. The red resampler
prefix-invariance tests compare two independently generated frames
(`make_m15(192)` vs `make_m15(96)`) whose RNG draws are *not* prefix-identical,
so they compare different data; a same-frame slice comparison yields 0
differences. Fix belongs to Agent 1 (t2): build `partial = full.iloc[:96]`.

## 4. End-to-end data audit (Pipeline 1) — on the real MT5 export

```text
$ .venv/bin/xauusd-audit --config baseline.yaml
[xauusd-audit] OK: 99692 rows, 1112 gaps
parquet  -> data/processed/xauusd_m15.parquet
quality  -> reports/data_quality/xauusd_m15_quality.json
```

Data quality report key values: row_count=99,692 · duplicates=0 ·
missing=0 · invalid_ohlc=0 · zero_volume=0 · gaps=1,112 (daily 75-min broker
break records etc.) · volume_type=tick · timezone normalized to UTC.

## 5. Concerns logged for later tasks (not blockers for t1)

1. **Schema drift in the level registry (Agent 2, t8):** the in-flight
   `src/liquidity/level_registry.py` emits columns beyond SCHEMAS.md §4
   (`is_h1`, `touch_positions`, `max_age_bars`, `touch_tolerance_atr`).
   Per rule 30.1 Agent 2 must update SCHEMAS.md + src/schema.py + notify
   Agent 0 + log CHANGELOG when t8 merges; otherwise docs drift.
2. **Confirmation tz comparison (Agent 3, t9):** in-flight code compares
   tz-aware `confirmation_time` against a tz-naive reference → TypeError in 4
   tests. Agent 3 to fix (UTC tz-aware everywhere).
3. **Resampler test fixture (Agent 1, t2):** prefix-invariance tests must slice
   one generated frame instead of two separately generated ones.
4. **Test-set rule (30.5):** no `data/processed/test_events.parquet` is touched
   by any agent 0–5 so far — confirmed no access.

## 6. Working changes made by this task (files)

| File | Change |
|---|---|
| docs/SCHEMAS.md, docs/INTERFACES.md | new — canonical contracts |
| src/schema.py | new — column constants |
| src/config_schema.py | new — declarative config validation |
| src/config.py | lint/typing modernization; `validate=True` in load_config |
| src/pipelines/build_events.py | new — `xauusd-audit` real, `xauusd-events` stub |
| src/pipelines/build_dataset.py, train_model.py | new — guarded stubs |
| scripts/ci_gate.sh | new — the gate |
| pyproject.toml | mypy config, pytest markers, [dev] extras, ruff select |
| requirements-dev.txt | new — CI tooling pins |
| tests/test_config.py, tests/test_contracts.py | new — contract tests |
| configs/baseline.yaml | defaults added (costs, splitting.purge, model.models) so the base config validates standalone |
| README.md, CHANGELOG.md, .gitignore | new |

No interface behavior changed in other agents' modules: only lint/typing-safe
fixes were applied where the gate required it (unused imports, `X | None`
annotations, redundant casts, a DatetimeIndex guard in `resampler`).

## 7. Conclusion

t1 deliverables are complete and verified: schema lock v1.0, interface
contracts, config validation, CLI entry points, and an operating CI/CD gate.
The repository-wide gate will be re-run at each subsequent merge; current reds
are all attributable to in-flight tasks t2/t6/t8/t9 and are being tracked in
section 5.
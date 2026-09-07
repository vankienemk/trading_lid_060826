# Inspect-Popup Fix Requirements

**Task:** t1 (requirements) — team `pending-signal-inspect-fix`
**Author:** inspector (analyst)
**Date:** 2025-09-07
**Workspace:** `/Users/a/Documents/deepseek harness/kien-workspace`
**GUI code:** `trading_live/live/` (PySide6). **Venv used for probes:** `/tmp/ptv2_venv/bin/python3` (PySide6 6.10.3, `QT_QPA_PLATFORM=offscreen`).

Two user-reported defects in the pending-signal inspect popup:

1. The popup chart (entry / SL / TP dashed lines) does not show the entry point and the SL/TP lines are hidden — worse when price has moved far from the entry level.
2. The timestamp displayed as the moment "the entry point was found" is the **M15 scan moment**, not the signal's **actual entry time**.

Both root causes are confirmed below with file:line evidence and offscreen-render reproductions.

---

## Root cause 1 — chart fit (`trading_live/live/gui/chart_widget.py`)

### The mechanism (proven, not just hypothesized)

`CandlestickChart.paintEvent` computes the price range at lines 101–105:

```python
lows  = [c["low"]  for c in self._candles]
highs = [c["high"] for c in self._candles]
lo = min(lows + [v for v in (self._stop,) if v is not None])          # only STOP fed to lo
hi = max(highs + [v for v in (self._entry, self._take_profit) if v is not None])  # entry+TP fed to hi
```

The level inclusion is **asymmetric and assumes BUY geometry** (stop lowest → entry middle → TP highest). For a **SELL** signal the geometry is mirrored (TP lowest → entry middle → SL highest), so one side of the level band is always **excluded from the range** and its line is drawn **off the plot area** (clipped → invisible):

- `hi` never considers `stop` → a SELL SL that sits above the recent highs falls **above `hi`** → line drawn at negative y → clipped at the widget top.
- `lo` never considers `entry`/`take_profit` → a SELL entry/TP that sits below the recent lows falls **below `lo`** → line drawn beyond the plot bottom → clipped.

When price has *moved far* from the entry level, the excluded level is tens/hundreds of points outside a small viewport, so the line disappears completely — exactly the reported "entry point not shown + SL/TP hidden".

### Offscreen reproduction (`QT_QPA_PLATFORM=offscreen`, widget 560×220, plot height 194px, pad_top=8)

| Scenario | Range [lo, hi] | Entry y | SL y | TP y |
|---|---|---|---|---|
| BUY, price far below entry (entry zone above candles) | [2349.20, 2421.00] | 64.7px ✓ | 83.7px ✓ | 8.0px ✓ (edge) |
| **SELL, price far below entry** (SL above candles) | [2349.20, 2400.00] | 8.0px ✓ | **−14.9px ✗ CLIPPED** | 57.6px ✓ |
| **SELL, price far above entry** (entry+TP below candles) | [2406.00, 2446.59] | **230.7px ✗ CLIPPED** | 202.0px ✓ | **292.8px ✗ CLIPPED** |
| BUY, price far above entry (entry zone below candles) | [2394.00, 2496.59] | 190.7px ✓ | 202.0px ✓ | 156.6px ✓ |
| Normal (entry inside candle range) | [2394.00, 2418.00] | 153.5px ✓ | 202.0px ✓ | 8.0px ✓ (edge) |

(y outside [8, 202]px ⇒ drawn off-plot ⇒ hidden.)

Secondary failure modes:

- **Edge sliver / label clipping:** even when a line is numerically "in range" it can be glued to the 8px top or 202px bottom boundary (e.g. TP at 8.0px in "normal"). The label is drawn at `my - 4` (line 161) — a line pinned to `hi` puts the label at `pad_top - 4`, i.e. **above the widget top edge**, clipped. When price is far, the candles compress into a thin band at the opposite edge ("crushed to an edge sliver") — visible but unreadable.
- **Zero/None level degeneracy:** `[v for v in (self._stop,) if v is not None]` keeps `0.0`. The dialog feeds `float(sig.get("stop_loss", 0.0) or 0.0)` (gui_tab_live.py lines 956–958, 961), so a signal record missing SL/TP passes **0.0** and the range collapses: probe shows `lo = 0.0, hi = 2401.0` with candles at ~2399–2401 → the chart scale is destroyed. `_as_float(0.0)` → `0.0` (line 206–211) keeps it.

### Fix 1 — required (chart_fit)

In `chart_widget.py` `paintEvent` (and nowhere else; `set_data`/`set_loading` API unchanged):

1. **Guard invalid levels:** build the level list as `levels = [v for v in (self._entry, self._stop, self._take_profit) if v is not None and v > 0]` (prices are always positive; `<= 0` = missing ⇒ excluded from range **and** from marker drawing — do not draw a marker for an invalid level).
2. **Symmetric all-level range:**
   ```python
   lo = min(lows + levels)
   hi = max(highs + levels)
   span = (hi - lo) or 1.0
   ```
   This guarantees every marker line lands inside [lo, hi] for BOTH BUY and SELL, no matter how far price moved.
3. **Vertical padding** so the outermost line and its label are not glued/clipped at the plot boundary:
   ```python
   pad = max(span * 0.08, 0.5)
   lo -= pad
   hi += pad
   ```
   (Reuse the padded lo/hi for `y()` and for the bottom range hint so the hint reflects the true visible range.)
4. **Label safety:** with the padding, `my - 4` for the topmost line stays inside the widget. Keep the existing dashed style/colours (`#ffffff` entry, `#00cc66` TP, `#ff4444` SL) and `_marker` signature; optionally draw the **entry** line at pen width 2 so the entry point is visually dominant (marker emphasis — cheap, no API change).
5. Do **not** change the candle fetch/async pattern (that is Fix-optional below).

**Required acceptance check (offscreen):** the five scenarios above (reuse `make_candles` synthetic data) must show all three markers with y inside `[pad_top - 0.5, pad_top + plot_h + 0.5]`, and the zero-level case must no longer degenerate the range.

### Optional enhancement (recommended, non-blocking) — entry-bar window anchor

When price has moved far from entry, Fix 1 keeps the marker lines visible but the recent-candle band compresses (inherent to any single-viewport fit). Better context: show the candles **from the entry bar onward** instead of the newest 50. `fetch_candles(symbol, limit=…)` (gui_bridge.py line 500) already takes a limit and the underlying `MCPCandleSource.get_chart_history` returns up to 14 days of M15, so:

- `SignalInspectDialog._start_async_load` → request `limit=200` (worker unchanged, still async, no GUI-thread network).
- In `_on_candles_ready`, if `sig.get("entry_time")` is a positive epoch and the frame contains that bar, slice `df = df[df.index >= entry_ts]` (entry bar open = entry time) before `set_data`; otherwise fall back to the last 50 as today.
- This requires Fix 2 (entry_time carried into the signal payload), keeps all changes GUI-side, and is fully optional: acceptance of defect 1 does not depend on it.

---

## Root cause 2 — scan time vs entry time (end-to-end data flow)

### The chain (verified in code)

1. `trading_live/live/engine/signal_engine_v2.py` — `SignalCandidate` carries the true entry moment: `entry_time: datetime` (line 87) filled at line 451: `entry_time=candles.index[entry_bar].to_pydatetime()` — the **open time of the entry bar** (next M15 open after confirmation). `entry_price` is that bar's open (line 432).
2. `trading_live/live/engine/signal_polling_engine_v2.py` `_process_candidate` (lines 517–528) builds `PendingSignal(timestamp=time.time(), …)` — the **wall-clock moment the polling cycle found/processed the candidate** — and **drops `cand.entry_time`** entirely.
3. `trading_live/live/state/shared_app_state_v2.py` — `PendingSignal` dataclass (lines 96–107) has only `timestamp: float`; snapshot serialization (lines 768–781) exposes only `timestamp`.
4. `trading_live/live/gui/gui_tab_live.py` — pending table col 0 (lines 383–388) renders `fmt_time(sig["timestamp"])` with tooltip `"Signal time: …"`; `SignalInspectDialog` (lines 885, 963–968) shows Signal ID + `Age` computed from `timestamp`.
5. Nothing else reads `entry_time` — it never survives past the engine.

So every timestamp the user sees as "when the entry point was found" is actually the **M15 scan/discovery moment** (`time.time()` in `_process_candidate`), not the entry-bar time.

### Fix 2 — required (entry_time end-to-end)

1. **`shared_app_state_v2.py` `PendingSignal`** (line 96–107): add trailing field `entry_time: float = 0.0` (unix epoch; `0.0` = unknown/not recorded). Trailing default keeps positional construction compatible. (Only live construction site: polling engine line 518.)
2. **`signal_polling_engine_v2.py` `_process_candidate`** (line 518): pass `entry_time=_to_epoch(cand.entry_time)` with a tiny module-level helper:
   ```python
   def _to_epoch(dt) -> float:
       try:
           return float(dt.timestamp()) if hasattr(dt, "timestamp") else 0.0
       except Exception:
           return 0.0
   ```
   Naive datetimes are treated as local time — this matches the codebase convention (`execution_layer_v2._parse_position_time` line 178–195 parses MT5 terminal-local times with `.timestamp()` and the GUI displays via `datetime.fromtimestamp` in `fmt_time`, so the epoch round-trips to the same wall-clock the user sees on the M15 axis).
3. **`shared_app_state_v2.py` snapshot** (line 768–781): add `"entry_time": s.entry_time` to the `pending_signals` dict entries.
4. **`gui_tab_live.py` pending table** (lines 383–388): column 0 displays the **entry time**:
   ```python
   et = float(sig.get("entry_time", 0.0) or 0.0)
   shown = fmt_time(et) if et > 0 else fmt_time(ts)          # fallback: scan time, clearly labeled
   tooltip = (f"Entry time: {fmt_time(et) if et > 0 else '— (not recorded)'} | "
              f"Found at: {fmt_time(ts)}")
   ```
   Header label stays `"Time"` (or `"Entry Time"` — ResizeToContents handles it). The fallback keeps old signals (no entry_time) readable and unambiguous.
5. **`gui_tab_live.py` `SignalInspectDialog._build_ui`** (form around line 885): add a row `"Entry Time:"` → `fmt_time(entry_time)` when `entry_time > 0`, else `"— (not recorded)"`. Keep the `"Age:"` row but rename to `"Age (since found):"` — it must keep using `timestamp` (see 6).
6. **Do NOT touch stale-guard semantics:** `timestamp` continues to mean "found time" and keeps driving `MAX_MANUAL_SIGNAL_AGE_S` in `_on_send_order` (line 646–647) and the dialog (line 908–918). Entry time is informational only.
7. **`gui_components.py`:** no change required — `fmt_time(0)` already returns `"—"` (line 212–216). No new helpers needed.

### Required acceptance check (headless)

- Dataclass + snapshot: `PendingSignal(…, entry_time=<epoch>).` → `get_snapshot()["pending_signals"][0]["entry_time"] == <epoch>`; default `0.0` when not supplied.
- Polling path: `_process_candidate` with a fake state/risk-guard and a `SignalCandidate` with `entry_time=<naive dt>` produces a pending entry whose snapshot `entry_time == dt.timestamp()` (or constructor-level unit check — see t2 verify).
- Table/dialog: offscreen instantiation renders `fmt_time(entry_time)` text and `"— (not recorded)"` fallback without crashing.

---

## Constraints (all fixes)

- **PySide6 QPainter only** — no matplotlib/pyqtgraph/QtCharts, no new pip deps. Venv ships PySide6 only.
- **No MCP/network/engine calls on the Qt main thread** — preserve the `_InspectLoadWorker` async pattern (gui_tab_live.py lines 743–781, 925–961). DataFrame slicing in `_on_candles_ready` is cheap and stays on the GUI thread (no network).
- **SharedAppState thread-safety** — any new field is plain data on the dataclass; snapshot read path already uses the existing locks; engine-side construction happens on the polling thread exactly as today.
- **Scope (do not touch):** `execution_layer_v2.py`, `mt5_mcp_client.py`, `risk_guard_v2.py`, `archive/`, `old/`, `research/xauusd-liquidity-sweep`. Allowed files: `chart_widget.py`, `gui_tab_live.py`, `gui_components.py` (only if needed — likely not), `shared_app_state_v2.py`, `signal_polling_engine_v2.py`, `signal_engine_v2.py` (only if the epoch conversion belongs there — prefer the polling engine), `gui_bridge.py` (only for the optional limit=200 path).
- **Persistence:** `PendingSignal` is in-memory only (not DB-persisted) — no migration needed; `entry_time=0.0` default covers any pre-fix in-memory or test constructions.
- **Style:** keep dark-theme colours from `gui_components`, existing naming conventions, and `fmt_time` formatting.

## Verification commands (reusable by t2/t3)

```bash
# 1. compile every touched file from trading_live/ (live.* imports resolve)
cd "/Users/a/Documents/deepseek harness/kien-workspace/trading_live" && /tmp/ptv2_venv/bin/python3 -m py_compile live/gui/chart_widget.py live/gui/gui_tab_live.py live/state/shared_app_state_v2.py live/engine/signal_polling_engine_v2.py

# 2. offscreen chart probe (5 scenarios + zero-level) -> all markers inside plot rect
QT_QPA_PLATFORM=offscreen /tmp/ptv2_venv/bin/python3 /tmp/chart_probe2.py   # fixed-range expectations

# 3. state/engine entry_time sanity: build PendingSignal via dataclass + snapshot; assert entry_time in dict.
```

Acceptance gate: all five chart scenarios show every marker inside the plot rect with labels inside the widget; the zero-level case keeps a sane scale; snapshot carries `entry_time`; `timestamp` still drives the 1h stale guard unchanged; GUI threads never block on MCP.
"""
qa_entry_time_probe.py — independent QA probe for the t2 entry_time plumbing.

Verifies, offscreen (no GUI needed for the state/engine parts):

  1. _to_epoch: naive datetime -> local epoch; tz-aware -> epoch; missing/
     unconvertible -> 0.0.
  2. PendingSignal dataclass: trailing entry_time=0.0 default keeps backward
     compatibility (positional construction with the old field list still
     works; keyword entry_time lands in the right field).
  3. Snapshot serialization: entry_time appears in pending_signals dicts of a
     real SharedAppState; default 0.0 when unset.
  4. _process_candidate wiring: a SignalCandidate with a naive entry_time
     produces a PendingSignal whose snapshot entry_time == .timestamp(), and
     the `timestamp` field remains the scan moment (time.time()).
  5. 1h stale semantics unaffected (timestamp is the only age input).
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/Users/a/Documents/deepseek harness/kien-workspace/trading_live")

from live.engine.signal_polling_engine_v2 import SignalPollingEngine, _to_epoch
from live.engine.signal_engine_v2 import SignalCandidate
from live.state.shared_app_state_v2 import (
    PendingSignal,
    AutomationLevel,
    get_state,
)

failures = []
checks = 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


# --- 1. _to_epoch -----------------------------------------------------------
now_naive = datetime(2025, 9, 7, 12, 30, 0)
check(_to_epoch(now_naive) == now_naive.timestamp(),
      f"_to_epoch(naive) = {_to_epoch(now_naive)} != {now_naive.timestamp()}")
aware = datetime(2025, 9, 7, 12, 30, 0, tzinfo=timezone.utc)
check(_to_epoch(aware) == aware.timestamp(),
      f"_to_epoch(aware) = {_to_epoch(aware)}")
check(_to_epoch(None) == 0.0, "_to_epoch(None) != 0.0")
check(_to_epoch("garbage") == 0.0, "_to_epoch('garbage') != 0.0")
check(_to_epoch(42) == 0.0, "_to_epoch(42) should be 0.0 (no .timestamp())")

# --- 2. PendingSignal dataclass backward compat ------------------------------
p_old = PendingSignal("XAUUSD", "buy", 2400.0, 2390.0, 2420.0, 0.8, 0.6, 100.0)
check(p_old.entry_time == 0.0,
      f"positional construction should default entry_time=0.0, got {p_old.entry_time}")
p_new = PendingSignal("XAUUSD", "buy", 2400.0, 2390.0, 2420.0, 0.8, 0.6, 100.0,
                      signal_id="s1", entry_time=1234.5)
check(p_new.entry_time == 1234.5 and p_new.signal_id == "s1",
      f"keyword entry_time mis-assigned: {p_new}")

# --- 3. snapshot serialization on the real singleton -------------------------
state = get_state()
state.set_pending_signals([p_new, p_old])
snap = state.get_snapshot()
p_new_snap = snap["pending_signals"][0]
p_old_snap = snap["pending_signals"][1]
check(p_new_snap.get("entry_time") == 1234.5,
      f"snapshot entry_time = {p_new_snap.get('entry_time')!r}")
check(p_old_snap.get("entry_time") == 0.0,
      f"snapshot default entry_time = {p_old_snap.get('entry_time')!r}")
check(p_new_snap.get("timestamp") == 100.0,
      "snapshot timestamp untouched")

# --- 4. _process_candidate wiring -------------------------------------------
class FakeState:
    def __init__(self):
        self.pending_signals = []
        self.open_positions = []
        self.automation_levels = {}

    def set_pending_signals(self, signals):
        self.pending_signals = signals


class FakeRG:
    def evaluate_new_signal(self, **kw):
        return {"allowed": True}


cand = SignalCandidate(
    symbol="XAUUSD", direction="short", entry_price=2398.5,
    stop_price=2412.0, target_price=2382.0, entry_time=now_naive,
    event_time=now_naive - timedelta(minutes=15), event_id="ev-1",
    rule_score=0.75, model_prob=0.65, combined_score=0.7,
    penetration_atr=1.2, wick_ratio=0.8, reclaim_atr=0.9, h1_trend=-1,
    confirmation_delay_bars=1.0, confirmation_range_atr=0.5, atr_value=12.3,
)
fstate = FakeState()
eng = SignalPollingEngine(state=fstate, execution_layer=None,
                          risk_guard=FakeRG())
t0 = time.time()
eng._process_candidate(cand)
check(len(fstate.pending_signals) == 1,
      f"_process_candidate added {len(fstate.pending_signals)} pending")
p = fstate.pending_signals[0]
check(p.entry_time == now_naive.timestamp(),
      f"pending.entry_time = {p.entry_time}, expected {now_naive.timestamp()}")
check(p.timestamp >= t0 and p.timestamp <= time.time(),
      "pending.timestamp is not the scan moment (time.time())")
check(p.direction == "short" and p.entry_price == 2398.5,
      "pending direction/price wrong")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(f"ENTRY_TIME_PROBE_PASS ({checks} checks)")
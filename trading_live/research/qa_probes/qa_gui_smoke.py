"""
qa_gui_smoke.py — independent QA probe for the t2 entry-time GUI work.

Offscreen (QT_QPA_PLATFORM=offscreen), with a stub bridge, verifies:

  1. Pending-table col 0 renders the TRUE entry time when entry_time > 0,
     and falls back to the scan/found time with a clear tooltip when
     entry_time is missing/0.
  2. The inspect dialog adds "Entry Time:" (labeled fallback "— (not
     recorded)") and "Age (since found):" rows; the stale guard still uses
     `timestamp` (fresh entry_time + old timestamp disables Send Order).
  3. The async _InspectLoadWorker path runs (no GUI-thread MCP) and the
     dialog chart ends up sized/rendered (measure its real layout size so we
     can judge label clipping at realistic width).
  4. Positions P/L + stats refresh populate without crashing (regression).
"""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/Users/a/Documents/deepseek harness/kien-workspace/trading_live")

import pandas as pd
from PySide6.QtWidgets import QApplication, QFormLayout, QLabel, QPushButton

from live.gui.gui_components import fmt_time
from live.gui.gui_tab_live import (
    LiveControlTab,
    SignalInspectDialog,
    MAX_MANUAL_SIGNAL_AGE_S,
)

app = QApplication([])

FOUND_EPOCH = time.time() - 60.0          # found 1 min ago (fresh)
ENTRY_EPOCH = FOUND_EPOCH - 300.0         # entry bar 5 min before


class StubState:
    def get_snapshot(self):
        return {"pending_signals": [], "open_positions": []}


class StubBridge:
    def __init__(self):
        self.state = StubState()
        self.calls = []

    def fetch_candles(self, asset, limit=50):
        self.calls.append(("fetch_candles", asset, limit))
        return pd.DataFrame()  # empty frame -> "No data" placeholder

    def compute_risk_lot(self, asset, entry, stop):
        self.calls.append(("compute_risk_lot", asset, entry, stop))
        return 0.05

    def log_action(self, *a, **k):
        self.calls.append(("log_action", a, k))

    def send_order(self, **k):
        self.calls.append(("send_order", k))
        return {"ok": True, "success": True}

    def refresh_state_from_mcp(self):
        return self.state.get_snapshot()


failures = []
checks = 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


bridge = StubBridge()
tab = LiveControlTab(bridge)

# --- 1. pending table col 0 -------------------------------------------------
signal_a = {
    "asset": "XAUUSD", "direction": "buy", "entry_price": 2400.0,
    "stop_loss": 2390.0, "take_profit": 2420.0, "rule_score": 0.8,
    "model_probability": 0.6, "timestamp": FOUND_EPOCH,
    "signal_id": "sig-a", "entry_time": ENTRY_EPOCH,
}
signal_b = {  # pre-fix record: no entry_time key at all
    "asset": "XAUUSD", "direction": "sell", "entry_price": 2395.0,
    "stop_loss": 2410.0, "take_profit": 2380.0, "rule_score": 0.7,
    "model_probability": 0.55, "timestamp": FOUND_EPOCH,
    "signal_id": "sig-b",
}
signal_c = {  # entry_time present but 0 (not recorded)
    "asset": "XAUUSD", "direction": "buy", "entry_price": 2401.0,
    "stop_loss": 2391.0, "take_profit": 2421.0, "rule_score": 0.6,
    "model_probability": 0.5, "timestamp": FOUND_EPOCH,
    "signal_id": "sig-c", "entry_time": 0.0,
}
tab._populate_signals_table([signal_a, signal_b, signal_c])

hdr = tab._signals_table.horizontalHeaderItem(0).text()
check(hdr == "Entry Time", f"col0 header should be 'Entry Time', got {hdr!r}")

item_a = tab._signals_table.item(0, 0)
check(item_a.text() == fmt_time(ENTRY_EPOCH),
      f"row A col0 should show entry time {fmt_time(ENTRY_EPOCH)}, got {item_a.text()!r}")
tip_a = item_a.toolTip()
check(f"Entry time: {fmt_time(ENTRY_EPOCH)}" in tip_a and f"Found at: {fmt_time(FOUND_EPOCH)}" in tip_a,
      f"row A tooltip wrong: {tip_a!r}")

item_b = tab._signals_table.item(1, 0)
check(item_b.text() == fmt_time(FOUND_EPOCH),
      f"row B col0 should fall back to found time {fmt_time(FOUND_EPOCH)}, got {item_b.text()!r}")
tip_b = item_b.toolTip()
check("Entry time: — (not recorded)" in tip_b and f"Found at: {fmt_time(FOUND_EPOCH)}" in tip_b,
      f"row B tooltip should say '(not recorded)', got {tip_b!r}")

item_c = tab._signals_table.item(2, 0)
check(item_c.text() == fmt_time(FOUND_EPOCH) and "— (not recorded)" in item_c.toolTip(),
      f"row C (entry_time=0) should fall back with labeled tooltip, got {item_c.text()!r} / {item_c.toolTip()!r}")

# --- 2. inspect dialog rows + stale guard -----------------------------------
dlg = SignalInspectDialog(bridge, dict(signal_a), lambda *a: True)
dlg.show()
app.processEvents()

# Locate the Entry Time row value by walking the form layout.
form = None
for i in range(dlg.layout().count()):
    item = dlg.layout().itemAt(i)
    lay = item.layout()
    if isinstance(lay, QFormLayout):
        form = lay
        break
check(form is not None, "dialog has no QFormLayout")

entry_row_val = None
age_row_val = None
for r in range(form.rowCount()):
    lab = form.labelForField(form.itemAt(r, QFormLayout.FieldRole).widget())
    txt = form.labelForField(form.itemAt(r, QFormLayout.FieldRole).widget()).text()
    field = form.itemAt(r, QFormLayout.FieldRole).widget()
    if txt == "Entry Time:":
        entry_row_val = field.text()
    if txt == "Age (since found):":
        age_row_val = field.text()

check(entry_row_val == fmt_time(ENTRY_EPOCH),
      f"dialog Entry Time row should show {fmt_time(ENTRY_EPOCH)}, got {entry_row_val!r}")
check(age_row_val is not None and "m" in age_row_val,
      f"dialog Age (since found) row missing/wrong: {age_row_val!r}")
check(dlg._send_btn.isEnabled(), "fresh signal: Send Order should be enabled")

# fallback dialog: entry_time missing
dlg_fb = SignalInspectDialog(bridge, dict(signal_b), lambda *a: True)
dlg_fb.show()
app.processEvents()
form_fb = None
for i in range(dlg_fb.layout().count()):
    lay = dlg_fb.layout().itemAt(i).layout()
    if isinstance(lay, QFormLayout):
        form_fb = lay
        break
entry_fb = None
for r in range(form_fb.rowCount()):
    if form_fb.labelForField(form_fb.itemAt(r, QFormLayout.FieldRole).widget()).text() == "Entry Time:":
        entry_fb = form_fb.itemAt(r, QFormLayout.FieldRole).widget().text()
check(entry_fb == "— (not recorded)",
      f"dialog fallback Entry Time should be '— (not recorded)', got {entry_fb!r}")

# stale guard still timestamp-driven: old timestamp + fresh entry_time
old = signal_a.copy()
old["timestamp"] = time.time() - (MAX_MANUAL_SIGNAL_AGE_S + 120)
old["entry_time"] = time.time() - 100  # entry_time fresh
dlg_stale = SignalInspectDialog(bridge, old, lambda *a: True)
dlg_stale.show()
app.processEvents()
check(not dlg_stale._send_btn.isEnabled(),
      "stale guard: old timestamp + fresh entry_time must DISABLE Send Order")
check("Send Order disabled" in dlg_stale._status_label.text(),
      "stale guard: status label should explain disabled state")

# chart real layout size (label-clipping context)
chart = dlg._chart
check(chart.width() > 0 and chart.height() > 0,
      f"dialog chart has zero size: {chart.size()}")
print("DIALOG CHART SIZE:", chart.size().width(), "x", chart.size().height())

# --- 3. async worker actually ran ------------------------------------------
for _ in range(20):
    app.processEvents()
    time.sleep(0.02)
check(any(c[0] == "fetch_candles" for c in bridge.calls),
      "worker never called fetch_candles (async path broken)")
check(any(c[0] == "compute_risk_lot" for c in bridge.calls),
      "worker never called compute_risk_lot (async path broken)")

# --- 4. positions P/L + stats refresh regression -----------------------------
pos = {
    "asset": "XAUUSD", "direction": "buy", "entry_price": 2400.0,
    "current_price": 2410.0, "position_size": 0.1, "stop_loss": 2390.0,
    "take_profit": 2420.0, "open_time": ENTRY_EPOCH, "position_id": "p1",
}
tab._current_symbol = "XAUUSD"
tab._populate_positions_table([pos])
pl_item = tab._positions_table.item(0, 2)
check(pl_item is not None and pl_item.text().startswith("$"),
      f"positions P/L cell should show $ value, got {pl_item and pl_item.text()!r}")
tab._refresh_stats({"signal_stats": {"XAUUSD": {"trigger": 5, "found": 3, "pass": 2}}})
check("Trigger:" in tab._stats_label.text() and "5" in tab._stats_label.text(),
      f"stats refresh text wrong: {tab._stats_label.text()!r}")

for d in (dlg, dlg_fb, dlg_stale):
    d.close()
app.processEvents()

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(f"GUI_SMOKE_PASS ({checks} checks)")
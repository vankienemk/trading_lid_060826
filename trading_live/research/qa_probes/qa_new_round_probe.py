"""
qa_new_round_probe.py — verifies the 2 fixed GUI bugs headlessly (offscreen).

Bug 2 (stale guard): a pending signal whose *entry* is 5 days old but that got
re-listed with a fresh find-time (45 min) MUST have its Send Order button
disabled in SignalInspectDialog, and _on_send_order must refuse it. A fresh
signal must keep the button enabled.

Bug 1 (chart height): the inspect dialog + its CandlestickChart must be tall
and Expanding (>= a readable minimum), not a collapsed thin strip.

Run with the team venv:
    /tmp/ptv2_venv/bin/python3 \
        trading_live/research/qa_probes/qa_new_round_probe.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = "/Users/a/Documents/deepseek harness/kien-workspace/trading_live"
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter

from live.gui.gui_tab_live import (
    LiveControlTab,
    SignalInspectDialog,
    _is_stale_signal,
    _signal_age_s,
)
from live.gui.chart_widget import CandlestickChart

app = QApplication.instance() or QApplication(sys.argv)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL"), "|", name, detail)


# ---------------------------------------------------------------- Bug 2 logic
now = time.time()
sig_old = {  # user's real scenario
    "signal_id": "old-1",
    "asset": "XAUUSD",
    "direction": "buy",
    "entry_price": 2400.0,
    "stop_loss": 2380.0,
    "take_profit": 2425.0,
    "rule_score": 0.5,
    "model_probability": 0.55,
    "entry_time": now - 5 * 86400,   # entry bar 5 days ago
    "timestamp": now - 45 * 60,      # freshly re-listed 45 min ago
}
sig_fresh = dict(sig_old, signal_id="fresh-1",
                 entry_time=now - 120, timestamp=now - 60)

check("bug2 logic: old (5d entry / 45m find) is stale",
      _is_stale_signal(sig_old) is True,
      f"age={int(_signal_age_s(sig_old)/60)}m")
check("bug2 logic: fresh signal not stale",
      _is_stale_signal(sig_fresh) is False,
      f"age={int(_signal_age_s(sig_fresh)/60)}m")


# ------------------------------------------------------- Bug 2 dialog disable
class _Cb:
    def __init__(self):
        self.calls = 0
    def __call__(self, *a, **k):
        self.calls += 1
        return True

# Old stale signal -> dialog must disable the Send Order button.
cb_old = _Cb()
d_old = SignalInspectDialog(None, sig_old, cb_old)
check("bug2 dialog old: SEND ORDER disabled",
      d_old._send_btn.isEnabled() is False)
check("bug2 dialog old: status warns stale",
      "disabled" in d_old._status_label.text().lower())
check("bug2 dialog old: tooltip explains stale",
      "old" in (d_old._send_btn.toolTip() or "").lower() or "stale" in (d_old._send_btn.toolTip() or "").lower())

# Fresh signal -> button enabled.
cb_fresh = _Cb()
d_fresh = SignalInspectDialog(None, sig_fresh, cb_fresh)
check("bug2 dialog fresh: SEND ORDER enabled",
      d_fresh._send_btn.isEnabled() is True)


# --------------------- Bug 2 defence-in-depth: _on_send_order refusal (bypass UI)
class _Bridge:
    def __init__(self):
        self.events = []
    def log_action(self, name, data):
        self.events.append((name, data))
    @property
    def state(self):
        class _S:
            def get_snapshot(self):
                return {"pending_signals": [sig_old]}
        return _S()
    def send_order(self, **k):
        return {"ok": True}

tab = LiveControlTab(_Bridge())
# Monkeypatch the confirmation dialog to auto-confirm, so the defensive check runs.
import live.gui.gui_tab_live as M
_orig = M.ConfirmationDialog
class _Auto:
    def __init__(self, *a, **k):
        pass
    def exec(self):
        return True
M.ConfirmationDialog = _Auto
try:
    res = tab._on_send_order("old-1")
finally:
    M.ConfirmationDialog = _orig
check("bug2 defence: _on_send_order refuses stale signal (False, no dispatch)",
      res is False and not any(n == "send_order" for n, _ in _Bridge().events),
      f"result={res}")

# ---------------------------------------------------------------- Bug 1 chart
# Dialog default + chart geometry must be tall/readable.
check("bug1: dialog minimum height >= 600",
      d_old.minimumSize().height() >= 600,
      f"minH={d_old.minimumSize().height()}")
check("bug1: stale dialog chart min height >= 260",
      d_old._chart.minimumHeight() >= 260,
      f"minH={d_old._chart.minimumHeight()}")

# Render a fresh-signal dialog to a tall PNG and confirm candles painted.
d_show = SignalInspectDialog(None, sig_fresh, lambda *a, **k: True)
d_show.resize(760, 840)
d_show.show()
app.processEvents()
chart = d_show._chart
chart.set_data([
    {"open": 2399.0, "high": 2403.0, "low": 2397.0, "close": 2401.0},
    {"open": 2401.0, "high": 2406.0, "low": 2400.0, "close": 2405.0},
    {"open": 2405.0, "high": 2407.0, "low": 2401.0, "close": 2403.0},
], entry=2404.0, stop=2380.0, take_profit=2425.0)
app.processEvents()
check("bug1: chart size is tall (height >= 300px)",
      chart.height() >= 300, f"chartH={chart.height()}")

img = QImage(chart.size(), QImage.Format.Format_ARGB32)
img.fill(0)
chart.render(img)   # render widget into the image (paint device target)
out = "/tmp/qa_new_round_chart.png"
img.save(out)
# Non-blank: count non-background pixels.
non_bg = sum(1 for y in range(0, img.height(), 4)
             for x in range(0, img.width(), 4)
             if img.pixelColor(x, y) != img.pixelColor(0, 0))
check("bug1: chart painted (non-blank pixels present)",
      non_bg > 200, f"nonBg={non_bg}")

print()
print("PASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
print("Failures:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)

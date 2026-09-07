"""
qa_findings_1_2_probe.py — verifies two new findings headlessly (offscreen).

Finding 1: Pending Signals Dir column ALWAYS showed SELL even for buys.
Root cause: engine emits direction "long"/"short" but gui compared == "buy".
Fixed with _direction_label() -> canonical "🟢 BUY"/"🔴 SELL".

Finding 2: Inspect chart should draw an entry-point triangle: green UP for
buy/long, red DOWN for sell/short, at a balanced on-plot position.

Run with the team venv:
    /tmp/ptv2_venv/bin/python3 \
        trading_live/research/qa_probes/qa_findings_1_2_probe.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = "/Users/a/Documents/deepseek harness/kien-workspace/trading_live"
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage

from live.gui.gui_tab_live import (
    LiveControlTab,
    SignalInspectDialog,
    _direction_label,
)
from live.gui.chart_widget import CandlestickChart

app = QApplication.instance() or QApplication(sys.argv)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL"), "|", name, detail)


# ------------------------------------------------------------ Finding 1 logic
check("f1: long -> BUY", _direction_label("long") == "🟢 BUY")
check("f1: buy -> BUY", _direction_label("buy") == "🟢 BUY")
check("f1: short -> SELL", _direction_label("short") == "🔴 SELL")
check("f1: sell -> SELL", _direction_label("sell") == "🔴 SELL")
check("f1: Bullish -> BUY", _direction_label("Bullish") == "🟢 BUY")
check("f1: empty -> dash", _direction_label(None).strip() == "—")

# ------------------------------------------------------------ Finding 1 GUI
CANDLES = [
    {"open": 2400.0, "high": 2404.0, "low": 2398.0, "close": 2402.0},
    {"open": 2402.0, "high": 2406.0, "low": 2400.0, "close": 2404.0},
    {"open": 2404.0, "high": 2407.0, "low": 2401.0, "close": 2403.0},
    {"open": 2403.0, "high": 2405.0, "low": 2399.0, "close": 2401.0},
]
LONG = {"signal_id": "s-long", "asset": "XAUUSD", "direction": "long",
        "entry_price": 2403.0, "stop_loss": 2398.0, "take_profit": 2409.0,
        "rule_score": 0.5, "model_probability": 0.55,
        "entry_time": time.time() - 120, "timestamp": time.time() - 60}
SHORT = dict(LONG, signal_id="s-short", direction="short", entry_price=2402.0)

class _B:
    def log_action(self, *a, **k):
        pass
    @property
    def state(self):
        return type("S", (), {"get_snapshot": lambda self: {"pending_signals": [LONG, SHORT]}})()

# Instantiate tab; inspect the signal table cell text for direction columns.
tab = LiveControlTab(_B())
import live.gui.gui_tab_live as M
M._inspect_signal = lambda *a, **k: None  # avoid opening real dialog
tab._populate_signals_table([LONG, SHORT])
cell_dir0 = tab._signals_table.item(0, 2).text()  # LONG row Dir cell
cell_dir1 = tab._signals_table.item(1, 2).text()  # SHORT row Dir cell
check("f1 table: long row Dir == BUY", cell_dir0 == "🟢 BUY", f"got={cell_dir0!r}")
check("f1 table: short row Dir == SELL", cell_dir1 == "🔴 SELL", f"got={cell_dir1!r}")

# Inspect dialogs show canonical BUY/SELL in the Direction row.
d_long = SignalInspectDialog(None, LONG, lambda *a, **k: False)
d_short = SignalInspectDialog(None, SHORT, lambda *a, **k: False)
def _find_dir_label(dlg):
    from PySide6.QtWidgets import QLabel
    labels = [l.text() for l in dlg.findChildren(QLabel)]
    for i, t in enumerate(labels):
        if t == "Direction:":
            return labels[i + 1] if i + 1 < len(labels) else None
    return None
dl = _find_dir_label(d_long)
ds = _find_dir_label(d_short)
check("f1 dialog long Direction == BUY", dl == "🟢 BUY", f"got={dl!r}")
check("f1 dialog short Direction == SELL", ds == "🔴 SELL", f"got={ds!r}")


# ------------------------------------------------------------- Finding 2 chart
def render(chart):
    img = QImage(chart.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    chart.render(img)
    return img

def count_color(img, r, g, b, tol=30):
    n = 0
    for yy in range(0, img.height(), 1):
        for xx in range(0, img.width(), 1):
            c = img.pixelColor(xx, yy)
            if abs(c.red() - r) <= tol and abs(c.green() - g) <= tol and abs(c.blue() - b) <= tol:
                n += 1
    return n

GREEN = (0, 204, 102)   # COLOR_POSITIVE
RED = (255, 68, 68)     # COLOR_NEGATIVE

# Buy: candles well BELOW entry so candle bodies don't overlap the triangle;
# the entry line/triangle now sits near the vertical centre, so we locate the
# white entry line row dynamically and assert green triangle ink around it.
def white_entry_rows(img, x_lo=0.30, x_hi=0.95):
    rows = set()
    for yy in range(8, img.height() - 18):
        for xx in range(int(img.width()*x_lo), int(img.width()*x_hi)):
            c = img.pixelColor(xx, yy)
            if c.red() > 230 and c.green() > 230 and c.blue() > 230:
                rows.add(yy)
    return rows

def band_color(img, x_lo, x_hi, row_frac_lo, row_frac_hi, rgb, tol=30):
    n = 0
    for yy in range(int(img.height()*row_frac_lo), int(img.height()*row_frac_hi)):
        for xx in range(int(img.width()*x_lo), int(img.width()*x_hi)):
            c = img.pixelColor(xx, yy)
            if abs(c.red()-rgb[0])<=tol and abs(c.green()-rgb[1])<=tol and abs(c.blue()-rgb[2])<=tol:
                n += 1
    return n

# --- Buy (green up triangle) ---
c_buy = CandlestickChart(); c_buy.setMinimumHeight(260); c_buy.resize(520, 300)
c_buy.show(); app.processEvents()
c_buy.set_data([{"open": 2390.0, "high": 2393.0, "low": 2387.0, "close": 2391.0}] * 8,
               entry=2403.0, stop=2398.0, take_profit=2409.0, direction="long")
app.processEvents()
img_buy = render(c_buy); img_buy.save("/tmp/qa_f2_buy.png")
erows_buy = white_entry_rows(img_buy)
if erows_buy:
    ey_buy = (min(erows_buy) + max(erows_buy)) / 2.0
    buy_tri = band_color(img_buy, 0.10, 0.30,
                         max(0.0, ey_buy/300-0.10), min(1.0, ey_buy/300+0.10), GREEN)
else:
    buy_tri = 0
check("f2 buy: green triangle pixels around entry line",
      buy_tri > 20, f"n={buy_tri} entryrow={ey_buy if erows_buy else None}")

# --- Sell (red down triangle) ---
c_sell = CandlestickChart(); c_sell.setMinimumHeight(260); c_sell.resize(520, 300)
c_sell.show(); app.processEvents()
c_sell.set_data([{"open": 2409.0, "high": 2412.0, "low": 2406.0, "close": 2410.0}] * 8,
                entry=2402.0, stop=2397.0, take_profit=2396.0, direction="short")
app.processEvents()
img_sell = render(c_sell); img_sell.save("/tmp/qa_f2_sell.png")
erows_sell = white_entry_rows(img_sell)
if erows_sell:
    ey_sell = (min(erows_sell) + max(erows_sell)) / 2.0
    sell_tri = band_color(img_sell, 0.10, 0.30,
                          max(0.0, ey_sell/300-0.10), min(1.0, ey_sell/300+0.10), RED)
else:
    sell_tri = 0
check("f2 sell: red triangle pixels around entry line",
      sell_tri > 20, f"n={sell_tri} entryrow={ey_sell if erows_sell else None}")

# No direction -> no directional triangle colour beyond candle ink; ensure
# changing only direction flips it (compare regions).
print()
print("PASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
print("Failures:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)

"""
qa_chart_scale_centering_probe.py — verifies chart scale + entry-centering (offscreen).

Req 2: EURUSD (~1.10, 5 decimals) must NO LONGER collapse into a sliver; its
candle bodies should occupy a comparable fraction of plot height as BTC (~80k).

Req 1: when the entry point has drifted far from the candles, the chart pulls
to CENTRE the entry (white Entry line sits near the vertical middle), instead
of pinning it to the edge.

Run with the team venv:
    /tmp/ptv2_venv/bin/python3 trading_live/research/qa_probes/qa_chart_scale_centering_probe.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = "/Users/a/Documents/deepseek harness/kien-workspace/trading_live"
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from live.gui.chart_widget import CandlestickChart

app = QApplication.instance() or QApplication(sys.argv)
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL"), "|", name, detail)


def render(candles, entry, stop, tp, direction=None, w=560, h=320):
    c = CandlestickChart()
    c.resize(w, h)
    c.show(); app.processEvents()
    c.set_data(candles, entry=entry, stop=stop, take_profit=tp, direction=direction)
    app.processEvents()
    img = QImage(c.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    c.render(img)
    c.hide()
    return img


def candle_ink_extent(img):
    """Vertical fraction of plot occupied by candle-coloured ink (green/red)."""
    green, red = 0, 0
    rows_g = rows_r = set()
    for yy in range(8, img.height() - 18):
        for xx in range(6, img.width() - 6):
            c = img.pixelColor(xx, yy)
            if abs(c.red() - 0) <= 30 and abs(c.green() - 204) <= 30 and abs(c.blue() - 102) <= 30:
                rows_g.add(yy)
            if abs(c.red() - 255) <= 30 and abs(c.green() - 68) <= 30 and abs(c.blue() - 68) <= 30:
                rows_r.add(yy)
    all_rows = rows_g | rows_r
    if not all_rows:
        return 0.0
    return (max(all_rows) - min(all_rows)) / (img.height() - 8 - 18)


def entry_y_frac(img):
    """Vertical fraction (0=top,1=bottom) of the white Entry marker line."""
    rows = set()
    for yy in range(8, img.height() - 18):
        for xx in range(200, img.width() - 60):
            c = img.pixelColor(xx, yy)
            if c.red() > 230 and c.green() > 230 and c.blue() > 230:
                rows.add(yy)
    if not rows:
        return None
    return (min(rows) + (max(rows) - min(rows)) / 2.0) / (img.height() - 8 - 18)


def frac_range(img):
    return 8.0 / (img.height() - 8 - 18), (img.height() - 18 - 8) / (img.height() - 8 - 18)


# ---------------------------------------------------------- Req 2: EURUSD vs BTC
# EURUSD: 5-decimal, price ~1.10, ~50-pip move (0.005) over 20 M15 bars.
eur_candles = []
p = 1.1000
for i in range(20):
    eur_candles.append({"open": p,
                        "close": p + 0.0004,
                        "high": p + 0.0009,
                        "low": p - 0.0007})
    p += 0.00025
img_eur = render(eur_candles, entry=1.1050, stop=1.0980, tp=1.1120, direction="buy")
eur_ink = candle_ink_extent(img_eur)

# BTC ~80k, similarly proportioned move (~0.5%).
btc_candles = []
p = 80000.0
for i in range(20):
    btc_candles.append({"open": p,
                        "close": p + 120.0,
                        "high": p + 260.0,
                        "low": p - 200.0})
    p += 70.0
img_btc = render(btc_candles, entry=80300.0, stop=79500.0, tp=81200.0, direction="buy")
btc_ink = candle_ink_extent(img_btc)

print(f"   EURUSD candle ink vertical extent = {eur_ink:.2f}  (plot fraction)")
print(f"   BTC    candle ink vertical extent = {btc_ink:.2f}  (plot fraction)")
# EURUSD must no longer be a sliver: its candle ink must fill a substantial
# fraction AND be within ~2.5x of BTC's fill (close to scale-correct).
check("req2: EURUSD candle ink >= 30% of plot height", eur_ink >= 0.30, f"ink={eur_ink:.2f}")
check("req2: EURUSD scale comparable to BTC (<=2.5x)", eur_ink >= btc_ink / 2.5, f"eur={eur_ink:.2f} btc={btc_ink:.2f}")

# ------------------------ Req 1: pull-to-centre on entry when entry is far
# Entry far ABOVE the recent candles -> chart should centre on entry.
far_candles = []
p = 1.1000
for i in range(20):
    far_candles.append({"open": p, "close": p + 0.0004,
                        "high": p + 0.0009, "low": p - 0.0007})
    p += 0.00025
img_far_above = render(far_candles, entry=1.1500, stop=1.1420, tp=1.1620, direction="buy")
ey_above = entry_y_frac(img_far_above)
print(f"   far-above entry white-line y-frac = {ey_above}")
# Centred near middle (0.25..0.75), not pinned to very top.
check("req1: far entry pulled to ~middle (y in 0.25..0.75)",
      ey_above is not None and 0.25 <= ey_above <= 0.75, f"y={ey_above}")

# Entry far BELOW the candles likewise.
img_far_below = render(far_candles, entry=1.0500, stop=1.0420, tp=1.0580, direction="buy")
ey_below = entry_y_frac(img_far_below)
print(f"   far-below entry white-line y-frac = {ey_below}")
check("req1: far-below entry centred (y in 0.25..0.75)",
      ey_below is not None and 0.25 <= ey_below <= 0.75, f"y={ey_below}")

print()
print("PASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
print("Failures:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)

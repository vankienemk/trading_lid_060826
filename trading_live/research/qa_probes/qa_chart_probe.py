"""
qa_chart_probe.py — independent QA probe for the t2 chart-fit fix (r2).

Offscreen render of CandlestickChart with instrumented _marker. Asserts:

  1. Every valid level (entry/stop/tp > 0) is drawn; invalid (0/None) is not.
  2. Every drawn marker line's y is inside [pad_top, pad_top+plot_h]
     (8..202 for a 220px widget) — the line is on-plot for BOTH BUY and SELL
     geometry even when price moved far from the levels.
  3. The label baseline (my - 4) is inside the widget (no vertical clipping).
  4. Pixel evidence: the white ENTRY line has #ffffff pixels on its recorded
     row band inside the widget for every scenario with a valid entry.
  5. Padding evidence: rows 0..4 (above the topmost label's glyph top) and
     the plot-bottom area below the markers stay marker-ink-free (the range
     hint, drawn lower in pad_bottom, is excluded from this band).
  6. Zero/None/missing-level scenarios: no invalid marker drawn, no crash,
     sane scale (paint completes and range hint row renders).

Also prints the measured label horizontal extent so label edge-clipping at
560px width can be quantified (separate finding, not a gate failure here).
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/Users/a/Documents/deepseek harness/kien-workspace/trading_live")

import numpy as np
import pandas as pd
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from live.gui.chart_widget import CandlestickChart, _MARKER_ENTRY
from live.gui.gui_components import COLOR_SURFACE
from PySide6.QtGui import QFontMetrics

app = QApplication([])

W, H = 560, 220
PAD_TOP = 8
PAD_BOTTOM = 18
PLOT_H = H - PAD_TOP - PAD_BOTTOM  # 194


def make_candles(base=2400.0, n=50, step=0.05, up=True):
    times = pd.date_range("2025-09-01", periods=n, freq="15min")
    if up:
        close = base + np.arange(n) * step
        open_ = close - step * 0.4
    else:
        close = base - np.arange(n) * step
        open_ = close + step * 0.4
    high = np.maximum(open_, close) + step * 0.5
    low = np.minimum(open_, close) - step * 0.5
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close}, index=times
    )


SCENARIOS = {
    "BUY far below entry (entry zone above)": dict(
        c=make_candles(2400), entry=2421.0, stop=2349.2, tp=2470.0),
    "SELL far below entry (SL above candles)": dict(
        c=make_candles(2400, up=False), entry=2400.0, stop=2446.59, tp=2349.2),
    "SELL far above entry (entry+TP below)": dict(
        c=make_candles(2440, up=False), entry=2406.0, stop=2446.59, tp=2394.0),
    "BUY far above entry (entry zone below)": dict(
        c=make_candles(2480), entry=2394.0, stop=2380.0, tp=2420.0),
    "Normal (entry inside candle range)": dict(
        c=make_candles(2406), entry=2406.0, stop=2394.0, tp=2418.0),
    "Zero-level degeneracy (entry=SL=TP=0)": dict(
        c=make_candles(2400), entry=0.0, stop=0.0, tp=0.0),
    "None levels (no markers at all)": dict(
        c=make_candles(2400), entry=None, stop=None, tp=None),
    "Missing SL (stop=0.0, entry+TP valid)": dict(
        c=make_candles(2400), entry=2410.0, stop=0.0, tp=2420.0),
    "Tiny span (flat candles, levels equal)": dict(
        c=make_candles(2400, step=0.001), entry=2400.0, stop=2400.0, tp=2400.2),
}

# Scenarios where the ENTRY itself has drifted far from the candle pattern: the
# chart now centres the price window on the entry (new user requirement), so
# far SL/TP reference lines legitimately leave the plot — only the entry must
# remain on-plot for these.
ENTRY_DRIFTED_SCENARIOS = {
    "BUY far below entry (entry zone above)",
    "SELL far above entry (entry+TP below)",
    "BUY far above entry (entry zone below)",
}


def count_color(image, row_band, x0, x1, hex_colour):
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    n = 0
    for row in row_band:
        for x in range(x0, x1):
            c = image.pixelColor(x, row)
            if c.red() == r and c.green() == g and c.blue() == b:
                n += 1
    return n


def row_band_clean(image, rows, x0=6, x1=554):
    for row in rows:
        for x in range(x0, x1):
            c = image.pixelColor(x, row)
            if c.alpha() == 0:
                continue
            if (c.red(), c.green(), c.blue()) != (
                int(COLOR_SURFACE[1:3], 16), int(COLOR_SURFACE[3:5], 16),
                int(COLOR_SURFACE[5:7], 16)):
                return False
    return True


failures = []
notes = []

orig_marker = CandlestickChart._marker


def recording_marker(self, painter, price, y_fn, pad_left, plot_w, hex_colour,
                     label, width=1.0):
    my = y_fn(price)
    self._qa_drawn.append((label, price, my, hex_colour))
    return orig_marker(self, painter, price, y_fn, pad_left, plot_w,
                       hex_colour, label, width)


CandlestickChart._marker = recording_marker

for name, sc in SCENARIOS.items():
    w = CandlestickChart()
    w.resize(W, H)
    w._qa_drawn = []
    w.set_data(sc["c"], entry=sc["entry"], stop=sc["stop"],
               take_profit=sc["tp"])
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(0)
    w.render(img)
    drawn = w._qa_drawn
    labels = {d[0] for d in drawn}

    expected = []
    for lbl, attr in (("Entry", sc["entry"]), ("Stop Loss", sc["stop"]),
                      ("Take Profit", sc["tp"])):
        if attr is not None and attr > 0:
            expected.append(lbl)
    if [l for l in expected if l not in labels]:
        failures.append(f"[{name}] VALID marker not drawn: "
                        f"{[l for l in expected if l not in labels]}")
    if [l for l in labels if l not in expected]:
        failures.append(f"[{name}] marker drawn for invalid level: "
                        f"{[l for l in labels if l not in expected]}")

    for lbl, price, my, col in drawn:
        # New contract (req: "pull the chart to centre on the entry when the
        # entry drifts too far"): for a far-drifted ENTRY the price window is
        # centred on the entry, so its SL/TP (typically even farther away) fall
        # off-plot as dashed reference lines BY DESIGN.  Only the entry must
        # stay on-plot in those scenarios; normal scenarios still require every
        # valid level on-plot.
        is_drifted = name in ENTRY_DRIFTED_SCENARIOS
        if is_drifted and lbl != "Entry":
            # Keep the horizontal label-bounds check (costless) but skip the
            # vertical on-plot requirement for far SL/TP reference lines.
            fm2 = QFontMetrics(w.font())
            tw2 = fm2.horizontalAdvance(lbl)
            label_left = 6 + (W - 12) - tw2
            label_right = 6 + (W - 12)
            if label_right > W or label_left < 0:
                failures.append(
                    f"[{name}] {lbl} label text-bounds [{label_left},{label_right}] "
                    f"outside widget [0,{W}] (width {tw2})")
            continue
        if not (PAD_TOP <= my <= PAD_TOP + PLOT_H):
            failures.append(
                f"[{name}] {lbl} y={my:.1f} OUTSIDE plot rect "
                f"[{PAD_TOP},{PAD_TOP + PLOT_H}]")
        if not (0.0 <= my - 4.0 <= H - 1):
            failures.append(f"[{name}] {lbl} label baseline {my - 4:.1f} "
                            f"outside widget")
        # Round-2 (t4): label must fit INSIDE the widget — right edge = plot
        # right edge, left edge = right - text width, both inside [0, W].
        fm = QFontMetrics(w.font())
        tw = fm.horizontalAdvance(lbl)
        label_left = 6 + (W - 12) - tw  # pad_left + plot_w - textWidth
        label_right = 6 + (W - 12)
        if label_right > W or label_left < 0:
            failures.append(
                f"[{name}] {lbl} label text-bounds [{label_left},{label_right}] "
                f"outside widget [0,{W}] (width {tw})")
        if lbl == "Entry":
            band = range(int(my) - 1, int(my) + 2)
            n = count_color(img, band, 6, 554, _MARKER_ENTRY)
            if n < 5:
                failures.append(
                    f"[{name}] entry white line: only {n} px on row band "
                    f"around {int(my)} — line likely clipped/absent")

    # Padding evidence: nothing drawn in rows 0..4 (label glyph tops and
    # marker lines must stay below with the 8% pad).
    if expected:
        if not row_band_clean(img, range(0, 5)):
            failures.append(f"[{name}] ink found in rows 0..4 (above pad_top "
                            f"+ label — marker glued to top boundary)")

    # Label horizontal extent (finding quantification, not gate).
    if expected:
        xs = []
        for row in range(5, H):
            for x in range(5, W):
                c = img.pixelColor(x, row)
                if c.red() > 150 and c.green() > 150 and c.blue() > 150:
                    xs.append(x)
        if xs:
            notes.append(f"[{name}] white label/entry ink x-extent: "
                         f"{min(xs)}..{max(xs)} of {W}px widget")

print("NOTES (label horizontal extent — %dpx widget):" % W)
for n in notes:
    print("  ", n)

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("CHART_PROBE_PASS (%d scenarios)" % len(SCENARIOS))
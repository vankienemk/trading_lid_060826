"""
gui_tab3_performance.py — Tab 3: Performance Monitor

Spec section 7.2 Tab 3:
  - Per-symbol metrics table: trade count, win rate, PF, CI 95%, breakeven comparison
  - Equity curve chart (matplotlib via DPG's draw commands or image)
  - Kill-switch status per symbol: active/inactive, reason, manual override button
  - Manual override requires second confirmation

All metrics update in real-time as trades complete.
"""

from __future__ import annotations

import math
import sys
import time as _time
from typing import Any, Dict, List, Optional

import dearpygui.dearpygui as dpg

TAB3_PREFIX = "tab3_"

PERF_TABLE = TAB3_PREFIX + "perf_table"
KILL_LIST = TAB3_PREFIX + "kill_list"
EQUITY_CANVAS = TAB3_PREFIX + "equity_canvas"
EQUITY_TEXT = TAB3_PREFIX + "equity_text"


def build_performance_tab(state: Any, parent: str) -> None:
    """Build the complete Tab 3 layout."""
    with dpg.group(parent=parent):
        dpg.add_text("📈 Performance Monitor", color=(100, 200, 255))
        dpg.add_text(
            "Real-time performance metrics per symbol, equity curve, and kill-switch status.",
            color=(180, 180, 180), wrap=800,
        )
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # --- TOP: Metrics table ---
        with dpg.child_window(height=200, border=True):
            dpg.add_text("📊 Performance Per Symbol", color=(200, 200, 100))
            dpg.add_spacer(height=4)
            _build_perf_table(state)

        dpg.add_spacer(height=6)

        # --- MIDDLE: Equity curve ---
        with dpg.child_window(height=200, border=True):
            dpg.add_text("📉 Equity Curve (Cumulative Net R)", color=(200, 200, 100))
            dpg.add_spacer(height=4)
            _build_equity_display(state)

        dpg.add_spacer(height=6)

        # --- BOTTOM: Kill-switch ---
        with dpg.child_window(height=-1, border=True):
            dpg.add_text("🔒 Kill-Switch Status", color=(200, 200, 100))
            dpg.add_text("Per-symbol kill-switch with block bootstrap CI evaluation.",
                         color=(180, 180, 180))
            dpg.add_spacer(height=4)
            _build_kill_switch_panel(state)

    state.tab_handlers[2] = lambda snap: _refresh_tab3(state, snap)


# ---------------------------------------------------------------------------
# Performance Metrics Table
# ---------------------------------------------------------------------------

def _build_perf_table(state: Any) -> None:
    """Build the performance metrics table."""
    if dpg.does_item_exist(PERF_TABLE):
        children = dpg.get_item_children(PERF_TABLE, slot=1)
        if children:
            for c in children:
                try: dpg.delete_item(c)
                except: pass
    else:
        dpg.add_table(tag=PERF_TABLE, headers=["Symbol", "Trades", "Win Rate", "Profit Factor",
                                                  "Avg Net R", "Breakeven", "Above BE", "95% CI"],
                      width=-1, resizable=True)
        return

    symbols = state.bridge.get_validated_symbols()
    if not symbols:
        dpg.add_text("No symbol data available.", tag=TAB3_PREFIX + "perf_empty")
        return

    for sym in symbols:
        perf = state.bridge.get_performance(sym)
        tc = perf.get("trade_count", 0)
        wr = perf.get("win_rate", 0.0)
        pf = perf.get("profit_factor", 0.0)
        avg_r = perf.get("avg_net_r", 0.0)
        be = perf.get("breakeven_cost", 0.0)
        above = perf.get("above_breakeven", False)
        ci_low = perf.get("ci_95_low", 0.0)
        ci_high = perf.get("ci_95_high", 0.0)

        ci_str = f"[{ci_low:.4f}, {ci_high:.4f}]" if tc >= 4 else "N/A (<4)"
        above_str = "✅" if above else "❌"

        with dpg.table_row(parent=PERF_TABLE):
            dpg.add_text(sym)
            dpg.add_text(str(tc))
            dpg.add_text(f"{wr*100:.1f}%" if tc > 0 else "—")
            dpg.add_text(f"{pf:.3f}" if pf and pf != float("inf") else "∞" if tc > 0 else "—")
            dpg.add_text(f"{avg_r:.4f}R" if tc > 0 else "—")
            dpg.add_text(f"{be:.4f}R")
            dpg.add_text(above_str, color=(0, 200, 0) if above else (200, 0, 0))
            dpg.add_text(ci_str)


# ---------------------------------------------------------------------------
# Equity Curve Display
# ---------------------------------------------------------------------------

def _build_equity_display(state: Any) -> None:
    """Build the equity curve display (cumulative net R over trades)."""
    if dpg.does_item_exist(EQUITY_TEXT):
        # Clear and rebuild
        children = dpg.get_item_children(EQUITY_CANVAS, slot=1)
        if children:
            for c in children:
                try: dpg.delete_item(c)
                except: pass
    else:
        dpg.add_child_window(tag=EQUITY_CANVAS, width=-1, height=160, border=False)

    trades = state.bridge.get_trade_log()
    if not trades:
        dpg.add_text("No trades yet.", color=(150, 150, 150), tag=EQUITY_TEXT,
                     parent=EQUITY_CANVAS)
        return

    # Calculate cumulative net R per symbol
    from collections import defaultdict
    cum_r: Dict[str, List[float]] = defaultdict(list)
    trade_indices: Dict[str, List[int]] = defaultdict(list)

    # Sort trades by exit_time
    sorted_trades = sorted(trades, key=lambda t: t.get("exit_time", 0))

    cumulative: Dict[str, float] = defaultdict(float)
    cumulatives: Dict[str, List[float]] = defaultdict(list)

    for t in sorted_trades:
        asset = t.get("asset", "?")
        net_r = t.get("net_result_r", 0.0) or 0.0
        cumulative[asset] += net_r
        cum_r[asset].append(cumulative[asset])
        trade_indices[asset].append(len(trade_indices[asset]))

    # Build textual equity display
    lines = []
    for sym in sorted(cum_r.keys()):
        series = cum_r[sym]
        final_r = series[-1] if series else 0.0
        peak = max(series) if series else 0.0
        trough = min(series) if series else 0.0
        lines.append(
            f"{sym}: {len(series)} trades | Final: {final_r:.2f}R | "
            f"Peak: {peak:.2f}R | Trough: {trough:.2f}R"
        )

    display = "\n".join(lines) if lines else "No trades yet."
    dpg.add_text(display, tag=EQUITY_TEXT, parent=EQUITY_CANVAS, color=(150, 200, 150))


# ---------------------------------------------------------------------------
# Kill-Switch Panel
# ---------------------------------------------------------------------------

def _build_kill_switch_panel(state: Any) -> None:
    """Build the kill-switch status panel per symbol."""
    if dpg.does_item_exist(KILL_LIST):
        children = dpg.get_item_children(KILL_LIST, slot=1)
        if children:
            for c in children:
                try: dpg.delete_item(c)
                except: pass
    else:
        dpg.add_child_window(tag=KILL_LIST, width=-1, height=150, border=False)

    ks_status = state.bridge.get_kill_switch_status()
    symbols = state.bridge.get_validated_symbols()

    if not ks_status and not symbols:
        dpg.add_text("No symbols configured.", color=(150, 150, 150), parent=KILL_LIST)
        return

    for sym in symbols:
        ks = ks_status.get(sym, {"active": False, "reason": "No data", "activated_at": 0.0})
        active = ks.get("active", False)
        reason = ks.get("reason", "")
        activated_at = ks.get("activated_at", 0.0)

        with dpg.group(parent=KILL_LIST, horizontal=True):
            indicator = "⛔" if active else "✅"
            status_text = "ACTIVE" if active else "Inactive"
            color = (255, 50, 50) if active else (0, 200, 0)

            dpg.add_text(f"{indicator} {sym}: {status_text}", color=color, width=200)
            dpg.add_spacer(width=10)

            if active:
                dpg.add_text(f"Reason: {reason[:60]}", color=(255, 200, 100), width=300)
                dpg.add_spacer(width=5)
                dpg.add_button(
                    label="Override OFF",
                    tag=f"kill_override_off_{sym}",
                    callback=lambda s, a, sym=sym: _confirm_kill_override(state, sym, False),
                    width=100,
                )
            else:
                dpg.add_spacer(width=5)
                dpg.add_button(
                    label="Override ON",
                    tag=f"kill_override_on_{sym}",
                    callback=lambda s, a, sym=sym: _confirm_kill_override(state, sym, True),
                    width=100,
                )

        # Reason detail
        if reason:
            dpg.add_text(f"  {reason}", color=(150, 150, 150), parent=KILL_LIST, wrap=700)


def _confirm_kill_override(state: Any, symbol: str, activate: bool) -> None:
    """Show confirmation dialog for kill-switch manual override."""
    action = "ACTIVATE" if activate else "DEACTIVATE"
    from gui_main import show_confirmation_dialog
    show_confirmation_dialog(
        title=f"Kill-Switch Override: {action} for {symbol}?",
        message=f"Confirm manual override to {action.lower()} kill-switch for {symbol}.\n\n"
                f"{'This may enable new signals for this symbol.' if not activate else 'This will block all new signals.'}\n"
                f"Requires explicit confirmation.",
        confirm_label=f"✅ {action}",
        callback_confirm=lambda: _execute_kill_override(state, symbol, activate),
        width=450, height=220,
    )


def _execute_kill_override(state: Any, symbol: str, activate: bool) -> None:
    """Execute kill-switch override after confirmation."""
    state.bridge.manual_override_kill_switch(
        symbol, active=activate,
        reason=f"Manual override by user: {'activate' if activate else 'deactivate'}"
    )
    state.bridge._log_user_action(
        f"Kill-switch for {symbol} manually {'activated' if activate else 'deactivated'}"
    )
    # Refresh display
    _build_kill_switch_panel(state)


# ---------------------------------------------------------------------------
# Periodic refresh
# ---------------------------------------------------------------------------

def _refresh_tab3(state: Any, snap: Dict[str, Any]) -> None:
    """Periodic refresh for Tab 3."""
    try:
        _build_perf_table(state)
        _build_equity_display(state)
        _build_kill_switch_panel(state)
    except Exception as exc:
        # Tab may not be fully built yet
        print(f"[GUI Tab3] Refresh error: {exc}", file=sys.stderr)
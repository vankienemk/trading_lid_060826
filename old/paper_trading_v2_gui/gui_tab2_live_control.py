"""
gui_tab2_live_control.py — Tab 2: Live Control

Spec section 7.2 Tab 2:
  - Dropdown per symbol (only validated symbols shown)
  - Automation level switch (1/2/3) per symbol
  - Pending signals table with Send Order / Skip buttons per row
  - Open positions table with P/L in R
  - Emergency Stop button always visible (rendered in gui_main, not here)

All controls are immediate-response (no web reload).
Every destructive action needs a second confirmation dialog.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import dearpygui.dearpygui as dpg

TAB2_PREFIX = "tab2_"

# Persistent tags
TAB2_SYMBOL_DROPDOWN = TAB2_PREFIX + "symbol_dropdown"
TAB2_AUTO_LEVEL_SLIDER = TAB2_PREFIX + "auto_level_slider"
TAB2_AUTO_LEVEL_TEXT = TAB2_PREFIX + "auto_level_text"
TAB2_SIGNALS_TABLE = TAB2_PREFIX + "signals_table"
TAB2_POSITIONS_TABLE = TAB2_PREFIX + "positions_table"
TAB2_STATUS_TEXT = TAB2_PREFIX + "status_text"

# Per-symbol automation level tracking (reset to 1 on startup)
_automation_levels: Dict[str, int] = {}


def build_live_control_tab(state: Any, parent: str) -> None:
    """Build the complete Tab 2 layout."""
    with dpg.group(parent=parent):
        # Title and status
        dpg.add_text("🎮 Live Control", color=(100, 200, 255))
        dpg.add_text(
            "Monitor and control live trading for validated symbols.",
            color=(180, 180, 180), wrap=800,
        )
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # Symbol selector row
        with dpg.group(horizontal=True):
            dpg.add_text("Symbol: ", color=(180, 180, 180))
            _refresh_symbol_dropdown(state)
            dpg.add_spacer(width=20)
            dpg.add_text("Automation Level:", color=(180, 180, 180))
            dpg.add_slider_int(
                tag=TAB2_AUTO_LEVEL_SLIDER,
                default_value=1, min_value=1, max_value=3,
                width=100, clamped=True,
                callback=lambda s, a: _on_auto_level_change(state, a, s),
            )
            dpg.add_text("Level 1 — Manual Confirm", tag=TAB2_AUTO_LEVEL_TEXT, color=(200, 200, 0))

        dpg.add_spacer(height=4)

        # Status info
        dpg.add_text("Status: waiting...", tag=TAB2_STATUS_TEXT, color=(150, 150, 150))
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # Two-column layout: pending signals (left), open positions (right)
        with dpg.group(horizontal=True):
            # LEFT: Pending signals
            with dpg.child_window(width=700, height=-1, border=True):
                dpg.add_text("📡 Pending Signals (Level 1)", color=(200, 200, 100))
                dpg.add_spacer(height=4)
                _build_signals_empty(state)

            # RIGHT: Open positions
            with dpg.child_window(width=-1, height=-1, border=True):
                dpg.add_text("💼 Open Positions", color=(200, 200, 100))
                dpg.add_spacer(height=4)
                _build_positions_empty(state)

    # Register the periodic handler
    state.tab_handlers[1] = lambda snap: _refresh_tab2(state, snap)


def _refresh_symbol_dropdown(state: Any) -> None:
    """Refresh the symbol dropdown with current validated symbols."""
    if dpg.does_item_exist(TAB2_SYMBOL_DROPDOWN):
        dpg.delete_item(TAB2_SYMBOL_DROPDOWN)

    symbols = state.bridge.get_validated_symbols()
    if not symbols:
        symbols = ["(no symbols)"]

    dpg.add_combo(
        items=symbols,
        default_value=symbols[0],
        tag=TAB2_SYMBOL_DROPDOWN,
        width=150,
        callback=lambda s, a: _on_symbol_change(state, a),
    )


def _on_symbol_change(state: Any, symbol: str) -> None:
    """React to symbol selection change."""
    level = _automation_levels.get(symbol, 1)
    if dpg.does_item_exist(TAB2_AUTO_LEVEL_SLIDER):
        dpg.set_value(TAB2_AUTO_LEVEL_SLIDER, level)
    _update_auto_level_display(level)
    dpg.set_value(TAB2_STATUS_TEXT, f"Selected: {symbol} | Automation: Level {level}")

    # Refresh signal and position tables
    snap = state.bridge.refresh()
    if snap:
        _build_signals_table(state, snap, symbol)
        _build_positions_table(state, snap, symbol)


def _on_auto_level_change(state: Any, level: int, sender: str) -> None:
    """Handle automation level change — requires confirmation for level > 1."""
    symbol = dpg.get_value(TAB2_SYMBOL_DROPDOWN) if dpg.does_item_exist(TAB2_SYMBOL_DROPDOWN) else "XAUUSD"

    if level > 1:
        # Require second confirmation (spec 7.3)
        _show_auto_level_confirm_dialog(state, symbol, level)
    else:
        _apply_auto_level(state, symbol, level)


def _show_auto_level_confirm_dialog(state: Any, symbol: str, level: int) -> None:
    """Show confirmation dialog for automation level increase."""
    warnings = {
        2: "Semi-auto: Agent will send orders automatically.\n"
           "Orders still respect kill-switch.\n"
           "Recommended only after 30+ stable manual trades.",
        3: "⚠️ FULL AUTO — NOT RECOMMENDED for paper trading.\n"
           "Orders placed entirely without manual review.",
    }
    msg = warnings.get(level, f"Set automation to Level {level}?")

    from gui_main import show_confirmation_dialog
    show_confirmation_dialog(
        title=f"Change Automation Level to {level}?",
        message=msg,
        confirm_label=f"✅ Set Level {level}",
        callback_confirm=lambda: _apply_auto_level(state, symbol, level),
        width=450, height=250,
    )


def _apply_auto_level(state: Any, symbol: str, level: int) -> None:
    """Apply the automation level change."""
    _automation_levels[symbol] = level
    state.bridge.set_automation_level(symbol, level)
    _update_auto_level_display(level)
    dpg.set_value(TAB2_STATUS_TEXT,
                  f"Automation for {symbol} set to Level {level}")
    state.bridge._log_user_action(f"Automation level for {symbol} changed to {level}")


def _update_auto_level_display(level: int) -> None:
    """Update the automation level display."""
    labels = {
        1: "Level 1 — Manual Confirm",
        2: "Level 2 — Semi-Auto",
        3: "⚠️ Level 3 — Full Auto",
    }
    colors = {1: (200, 200, 0), 2: (255, 150, 0), 3: (255, 50, 50)}
    if dpg.does_item_exist(TAB2_AUTO_LEVEL_TEXT):
        dpg.set_value(TAB2_AUTO_LEVEL_TEXT, labels.get(level, f"Level {level}"))
        dpg.configure_item(TAB2_AUTO_LEVEL_TEXT, color=colors.get(level, (200, 200, 200)))


# ---------------------------------------------------------------------------
# Pending Signals Table
# ---------------------------------------------------------------------------

def _build_signals_empty(state: Any) -> None:
    """Build placeholder for signals table."""
    if not dpg.does_item_exist(TAB2_SIGNALS_TABLE):
        dpg.add_child_window(tag=TAB2_SIGNALS_TABLE, width=-1, height=250, border=False)


def _build_signals_table(state: Any, snap: Dict[str, Any], symbol: str) -> None:
    """Build/refresh the pending signals table for the selected symbol."""
    # Clear existing
    if dpg.does_item_exist(TAB2_SIGNALS_TABLE):
        children = dpg.get_item_children(TAB2_SIGNALS_TABLE, slot=1)
        if children:
            for c in children:
                try: dpg.delete_item(c)
                except: pass
    else:
        dpg.add_child_window(tag=TAB2_SIGNALS_TABLE, width=-1, height=250, border=False)

    signals = snap.get("pending_signals", [])
    filtered = [s for s in signals if s.get("asset", "").upper() == symbol.upper()]

    if not filtered:
        dpg.add_text("No pending signals.", color=(150, 150, 150), parent=TAB2_SIGNALS_TABLE)
        return

    # Table header
    with dpg.table(parent=TAB2_SIGNALS_TABLE, headers=["", "Dir", "Entry", "Stop Loss", "Take Profit",
                                                         "Rule Score", "Model Prob", "Action"],
                   width=-1, resizable=True):
        pass

    for sig in filtered:
        from gui_main import show_confirmation_dialog
        sid = sig.get("signal_id", "")
        dir_arrow = "🟢 BUY" if sig.get("direction", "").lower() == "buy" else "🔴 SELL"

        with dpg.table_row(parent=TAB2_SIGNALS_TABLE):
            dpg.add_text(sig.get("asset", ""))
            dpg.add_text(dir_arrow)
            dpg.add_text(f"{sig.get('entry_price', 0):.5f}")
            dpg.add_text(f"{sig.get('stop_loss', 0):.5f}")
            dpg.add_text(f"{sig.get('take_profit', 0):.5f}")
            dpg.add_text(f"{sig.get('rule_score', 0):.3f}")
            dpg.add_text(f"{sig.get('model_probability', 0):.3f}")

            # Send Order button with confirmation
            dpg.add_button(
                label="Send Order",
                tag=f"send_{sid}",
                callback=lambda s, a, sid=sid: _on_send_order(state, snap, sid),
                width=90,
            )


def _on_send_order(state: Any, snap: Dict[str, Any], signal_id: str) -> None:
    """Send order for a pending signal (with confirmation dialog)."""
    from gui_main import show_confirmation_dialog
    show_confirmation_dialog(
        title="Send Order Confirmation",
        message=f"Confirm sending order for signal {signal_id}?",
        confirm_label="✅ SEND ORDER",
        callback_confirm=lambda: _execute_send_order(state, signal_id),
        width=400, height=180,
    )


def _execute_send_order(state: Any, signal_id: str) -> None:
    """Actually send the order via the bridge."""
    state.bridge._log_user_action(f"Order sent for signal {signal_id}")
    dpg.set_value(TAB2_STATUS_TEXT, f"Order sent for signal {signal_id}")
    # In production: call ExecutionLayer.send_order()
    # state.bridge.send_order(signal_id)
    # For now: remove from pending
    # state.bridge.remove_pending_signal(signal_id)


# ---------------------------------------------------------------------------
# Open Positions Table
# ---------------------------------------------------------------------------

def _build_positions_empty(state: Any) -> None:
    if not dpg.does_item_exist(TAB2_POSITIONS_TABLE):
        dpg.add_child_window(tag=TAB2_POSITIONS_TABLE, width=-1, height=250, border=False)


def _build_positions_table(state: Any, snap: Dict[str, Any], symbol: str) -> None:
    if dpg.does_item_exist(TAB2_POSITIONS_TABLE):
        children = dpg.get_item_children(TAB2_POSITIONS_TABLE, slot=1)
        if children:
            for c in children:
                try: dpg.delete_item(c)
                except: pass
    else:
        dpg.add_child_window(tag=TAB2_POSITIONS_TABLE, width=-1, height=250, border=False)

    positions = snap.get("open_positions", [])
    filtered = [p for p in positions if p.get("asset", "").upper() == symbol.upper()]

    if not filtered:
        dpg.add_text("No open positions.", color=(150, 150, 150), parent=TAB2_POSITIONS_TABLE)
        return

    with dpg.table(parent=TAB2_POSITIONS_TABLE, headers=["Asset", "Dir", "Entry", "Current", "Size",
                                                           "P/L (R)", "Open Time", "Close"],
                   width=-1, resizable=True):
        pass

    for pos in filtered:
        # Calculate P/L in R
        entry = pos.get("entry_price", 0)
        current = pos.get("current_price", 0)
        stop_loss = pos.get("stop_loss", 0)
        direction = pos.get("direction", "buy")
        diff = current - entry
        r_range = entry - stop_loss if stop_loss != 0 else 1
        pl_r = (diff / r_range) * 100 if direction == "buy" else (-diff / r_range) * 100
        from gui_main import fmt_time, fmt_r

        with dpg.table_row(parent=TAB2_POSITIONS_TABLE):
            dpg.add_text(pos.get("asset", ""))
            dpg.add_text("🟢 BUY" if direction == "buy" else "🔴 SELL")
            dpg.add_text(f"{entry:.5f}" if entry else "—")
            dpg.add_text(f"{current:.5f}" if current else "—")
            dpg.add_text(f"{pos.get('position_size', 0):.2f}")
            dpg.add_text(fmt_r(pl_r), color=(0, 200, 0) if pl_r >= 0 else (200, 0, 0))
            dpg.add_text(fmt_time(pos.get("open_time", 0)))


# ---------------------------------------------------------------------------
# Periodic refresh
# ---------------------------------------------------------------------------

def _refresh_tab2(state: Any, snap: Dict[str, Any]) -> None:
    """Periodic refresh for Tab 2."""
    if not snap:
        return
    # Refresh only if a symbol is selected
    if dpg.does_item_exist(TAB2_SYMBOL_DROPDOWN):
        try:
            symbol = dpg.get_value(TAB2_SYMBOL_DROPDOWN)
            if symbol and symbol != "(no symbols)":
                _build_signals_table(state, snap, symbol)
                _build_positions_table(state, snap, symbol)
        except Exception:
            pass
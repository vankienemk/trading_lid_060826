"""
gui_tab4_account.py — Tab 4: Account & Connection

Spec section 7.2 Tab 4:
  - MT5 account info display: account number, type, server, balance
  - RED fullscreen warning if account is NOT demo (blocks other operations)
  - MCP connection status: connected/disconnected, session info
  - Reconnect button (manual MCP reconnect)
  - MCP token input field (update token without restart)

This tab should be the first functional tab on startup (spec step 3):
the user must confirm the demo account before any trading can begin.
"""

from __future__ import annotations

import os
import sys
import time as _time
from typing import Any, Dict, Optional

import dearpygui.dearpygui as dpg

TAB4_PREFIX = "tab4_"

ACCT_INFO_TEXT = TAB4_PREFIX + "acct_info"
MCP_STATUS_TEXT = TAB4_PREFIX + "mcp_status"
TOKEN_INPUT = TAB4_PREFIX + "token_input"
WARNING_OVERLAY = TAB4_PREFIX + "warning_overlay"


def build_account_tab(state: Any, parent: str) -> None:
    """Build the complete Tab 4 layout."""
    with dpg.group(parent=parent):
        dpg.add_text("🔌 Account & Connection", color=(100, 200, 255))
        dpg.add_text(
            "MT5 account information, MCP connection management, and security settings.",
            color=(180, 180, 180), wrap=800,
        )
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # --- LEFT: Account Info ---
        with dpg.group(horizontal=True):
            with dpg.child_window(width=500, height=300, border=True):
                dpg.add_text("💳 MT5 Account Info", color=(200, 200, 100))
                dpg.add_spacer(height=4)
                _build_account_info(state)

            # --- RIGHT: MCP Connection ---
            with dpg.child_window(width=-1, height=300, border=True):
                dpg.add_text("🔗 MCP Connection", color=(200, 200, 100))
                dpg.add_spacer(height=4)
                _build_mcp_section(state)

        dpg.add_spacer(height=6)

        # --- BOTTOM: Token Input ---
        with dpg.child_window(height=120, border=True):
            dpg.add_text("🔑 MCP Token", color=(200, 200, 100))
            dpg.add_spacer(height=4)
            _build_token_section(state)

        dpg.add_spacer(height=6)

        # --- Account type warning area ---
        dpg.add_text("", tag=TAB4_PREFIX + "warning_area")

    state.tab_handlers[3] = lambda snap: _refresh_tab4(state, snap)


# ---------------------------------------------------------------------------
# Account Info Display
# ---------------------------------------------------------------------------

def _build_account_info(state: Any) -> None:
    """Build the MT5 account info display."""
    if dpg.does_item_exist(ACCT_INFO_TEXT):
        children = dpg.get_item_children(ACCT_INFO_TEXT, slot=1)
        if children:
            for c in children:
                try: dpg.delete_item(c)
                except: pass
    else:
        dpg.add_child_window(tag=ACCT_INFO_TEXT, width=-1, height=260, border=False)

    snap = state.bridge.refresh()
    mt5 = snap.get("mt5_status", {}) if snap else {}
    connected = mt5.get("connected", False)
    acct_info = mt5.get("account_info", {})
    acct_type = mt5.get("account_type", "unknown")

    if not connected:
        dpg.add_text("❌ Not connected to MT5.", color=(200, 0, 0),
                     parent=ACCT_INFO_TEXT, bold=True)
        dpg.add_text("Click 'Reconnect' below to attempt connection.",
                     color=(150, 150, 150), parent=ACCT_INFO_TEXT)
        return

    # Display account info in key-value format
    lines = [
        f"Account Number:  {acct_info.get('login', acct_info.get('number', 'N/A'))}",
        f"Account Type:    {acct_type.upper()}",
        f"Server:          {acct_info.get('server', acct_info.get('company', 'N/A'))}",
        f"Balance:         ${acct_info.get('balance', 0):,.2f}",
        f"Equity:          ${acct_info.get('equity', 0):,.2f}",
        f"Margin:          ${acct_info.get('margin', 0):,.2f}",
        f"Free Margin:     ${acct_info.get('margin_free', 0):,.2f}",
        f"Leverage:        1:{acct_info.get('leverage', 0)}",
        f"Name:            {acct_info.get('name', acct_info.get('company', 'N/A'))}",
        f"Currency:        {acct_info.get('currency', 'USD')}",
    ]

    for line in lines:
        dpg.add_text(line, parent=ACCT_INFO_TEXT, color=(180, 180, 180))

    # Check account type
    is_demo = "demo" in str(acct_type).lower()
    if not is_demo:
        _show_real_account_warning(state, acct_type)


# ---------------------------------------------------------------------------
# REAL Account Warning (RED fullscreen overlay)
# ---------------------------------------------------------------------------

def _show_real_account_warning(state: Any, acct_type: str) -> None:
    """Show RED fullscreen warning if account is not demo.
    
    Spec requirement: fullscreen red background, blocks other operations
    until confirmed.
    """
    # Check if the warning overlay already exists
    if dpg.does_item_exist(WARNING_OVERLAY):
        dpg.delete_item(WARNING_OVERLAY)

    # Create a full-viewport warning overlay
    viewport_width = dpg.get_viewport_width()
    viewport_height = dpg.get_viewport_height()

    with dpg.window(
        tag=WARNING_OVERLAY,
        label="⚠️ WARNING: REAL ACCOUNT DETECTED",
        width=viewport_width,
        height=viewport_height,
        no_resize=True,
        no_move=True,
        no_close=True,
        no_collapse=True,
        no_title_bar=True,
        modal=True,
        show=True,
    ) as win:
        # Solid red background via a drawlist
        with dpg.drawlist(width=viewport_width, height=viewport_height):
            dpg.draw_rectangle(
                pmin=(0, 0),
                pmax=(viewport_width, viewport_height),
                color=(255, 0, 0, 50),
                fill=(255, 0, 0, 50),
            )

        dpg.add_spacer(height=viewport_height // 3)
        dpg.add_text(
            "⛔ REAL TRADING ACCOUNT DETECTED",
            color=(255, 255, 255), bold=True,
        )
        dpg.add_text(
            f"This system is designed for PAPER TRADING only.\n"
            f"Account type: {acct_type.upper()}\n\n"
            f"If this is a REAL account, STOP IMMEDIATELY.\n"
            f"The system will NOT place real orders,\n"
            f"but please confirm below to proceed.",
            color=(255, 255, 255),
            wrap=800,
        )

        dpg.add_spacer(height=30)

        btn_width = 250
        btn_height = 50

        dpg.add_button(
            label="✅ I CONFIRM THIS IS A DEMO ACCOUNT",
            tag=TAB4_PREFIX + "confirm_demo",
            callback=lambda: dpg.delete_item(WARNING_OVERLAY),
            width=btn_width,
            height=btn_height,
        )

        dpg.add_spacer(height=10)

        dpg.add_button(
            label="❌ STOP — THIS IS A REAL ACCOUNT",
            tag=TAB4_PREFIX + "stop_real",
            callback=lambda: _on_real_account_stop(state),
            width=btn_width,
            height=btn_height,
        )


def _on_real_account_stop(state: Any) -> None:
    """Handle user confirming it's a real account."""
    dpg.delete_item(WARNING_OVERLAY)
    dpg.add_text(
        "⚠️ SYSTEM PAUSED — Real account detected. Signal engines halted.",
        color=(255, 0, 0), bold=True, parent="main_window"
    )
    state.bridge.set_emergency_stop(True)
    state.bridge._log_user_action("User confirmed REAL account — system halted")


# ---------------------------------------------------------------------------
# MCP Connection Section
# ---------------------------------------------------------------------------

def _build_mcp_section(state: Any) -> None:
    """Build the MCP connection status and controls."""
    if dpg.does_item_exist(MCP_STATUS_TEXT):
        children = dpg.get_item_children(MCP_STATUS_TEXT, slot=1)
        if children:
            for c in children:
                try: dpg.delete_item(c)
                except: pass
    else:
        dpg.add_child_window(tag=MCP_STATUS_TEXT, width=-1, height=200, border=False)

    # MCP health check
    health = state.bridge.mcp_health()
    ok = health.get("ok", False)
    url = health.get("url", "N/A")
    sid = health.get("session_id", "N/A")
    elapsed = health.get("elapsed_seconds", 0)
    error = health.get("error", "")

    status_color = (0, 200, 0) if ok else (200, 0, 0)
    dpg.add_text(f"MCP Status: {'✅ Connected' if ok else '❌ Disconnected'}",
                 color=status_color, bold=True, parent=MCP_STATUS_TEXT)
    dpg.add_spacer(height=4, parent=MCP_STATUS_TEXT)

    info_lines = [
        f"URL:      {url}",
        f"Session:  {sid}",
        f"Latency:  {elapsed:.3f}s",
    ]
    for line in info_lines:
        dpg.add_text(line, color=(180, 180, 180), parent=MCP_STATUS_TEXT)

    if error:
        dpg.add_text(f"Error: {error}", color=(255, 100, 100), parent=MCP_STATUS_TEXT)

    dpg.add_spacer(height=10, parent=MCP_STATUS_TEXT)

    dpg.add_button(
        label="🔄 Reconnect MCP",
        tag=TAB4_PREFIX + "reconnect_btn",
        callback=lambda s, a: _on_reconnect(state),
        width=150, height=30,
        parent=MCP_STATUS_TEXT,
    )


def _on_reconnect(state: Any) -> None:
    """Force MCP reconnect."""
    dpg.set_value(TAB4_PREFIX + "reconnect_btn", "Reconnecting...")
    state.bridge._log_user_action("Manual MCP reconnect triggered")
    success = state.bridge.mcp_reconnect()
    _time.sleep(0.5)
    dpg.set_value(TAB4_PREFIX + "reconnect_btn", "🔄 Reconnect MCP")
    _build_mcp_section(state)
    _build_account_info(state)


# ---------------------------------------------------------------------------
# Token Input Section
# ---------------------------------------------------------------------------

def _build_token_section(state: Any) -> None:
    """Build the MCP token input field."""
    if dpg.does_item_exist(TOKEN_INPUT):
        children = dpg.get_item_children(TOKEN_INPUT, slot=1)
        if children:
            for c in children:
                try: dpg.delete_item(c)
                except: pass
    else:
        dpg.add_child_window(tag=TOKEN_INPUT, width=-1, height=80, border=False)

    current_token = os.environ.get("MCP_TOKEN", "")
    masked = current_token[:8] + "****" + current_token[-4:] if len(current_token) > 12 else "(not set)"

    dpg.add_text(
        f"Current token: {masked}",
        color=(150, 150, 150),
        parent=TOKEN_INPUT,
    )
    dpg.add_spacer(height=4, parent=TOKEN_INPUT)

    with dpg.group(horizontal=True, parent=TOKEN_INPUT):
        dpg.add_input_text(
            tag=TAB4_PREFIX + "token_input_field",
            width=300,
            hint="Enter new MCP token",
            password=True,
        )
        dpg.add_spacer(width=5)
        dpg.add_button(
            label="Update Token",
            tag=TAB4_PREFIX + "update_token_btn",
            callback=lambda s, a: _update_token(state),
            width=120,
        )


def _update_token(state: Any) -> None:
    """Update the MCP token from the input field."""
    new_token = dpg.get_value(TAB4_PREFIX + "token_input_field").strip()
    if not new_token:
        dpg.set_value(TAB4_PREFIX + "update_token_btn", "⚠️ Cannot be empty")
        _time.sleep(1)
        dpg.set_value(TAB4_PREFIX + "update_token_btn", "Update Token")
        return

    state.bridge.set_mcp_token(new_token)
    state.bridge._log_user_action("MCP token updated")
    dpg.set_value(TAB4_PREFIX + "token_input_field", "")
    dpg.set_value(TAB4_PREFIX + "update_token_btn", "✅ Updated")
    _build_token_section(state)


# ---------------------------------------------------------------------------
# Periodic refresh
# ---------------------------------------------------------------------------

def _refresh_tab4(state: Any, snap: Dict[str, Any]) -> None:
    """Periodic refresh for Tab 4."""
    try:
        _build_account_info(state)
        _build_mcp_section(state)
    except Exception as exc:
        print(f"[GUI Tab4] Refresh error: {exc}", file=sys.stderr)
"""
gui_main.py — Paper Trading V2 Main GUI (Dear PyGui)

Entry point for the 4-tab desktop GUI.  Launches the Dear PyGui application
with the following tabs (spec section 7.2):

  Tab 1: Symbol Onboarding  — 7-step wizard with hard gates
  Tab 2: Live Control       — per-symbol signals, positions, emergency stop
  Tab 3: Performance Monitor — metrics, equity curve, kill-switch override
  Tab 4: Account & Connection — MT5 info, MCP status, reconnect, token input

Design principles (spec section 7.3):
  - ALL controls are immediate-response (no web reload)
  - Emergency Stop button is RED, always visible, fixed position
  - Every destructive action needs a second confirmation dialog
  - Automation level resets to 1 on every startup
  - Log every user action to the logger DB

Usage:
    python paper_trading_v2_gui/gui_main.py

Or (from the venv):
    cd /path/to/project
    /Users/a/Documents/deepseek\ harness/kien-workspace/xauusd-liquidity-sweep/.venv/bin/python -m paper_trading_v2_gui.gui_main
"""

from __future__ import annotations

import os
import sys
import time
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import dearpygui.dearpygui as dpg

# ---------------------------------------------------------------------------
# Ensure imports work
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent  # paper_trading_v2_gui/
_PROJECT_ROOT = _HERE.parent             # workspace root

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from paper_trading_v2_gui.gui_bridge import SystemBridge

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TAB_NAMES = [
    "🔬 Symbol Onboarding",
    "🎮 Live Control",
    "📈 Performance Monitor",
    "🔌 Account & Connection",
]

# Tags for persistent UI elements
TAG_EMERGENCY_STOP_BUTTON = "emergency_stop_button"
TAG_EMERGENCY_STOP_STATUS = "emergency_stop_status"
TAG_MAIN_WINDOW = "main_window"
TAG_STATUS_BAR = "status_bar"

# ---------------------------------------------------------------------------
# Application state (not in bridge — Dear PyGui specific)
# ---------------------------------------------------------------------------

class AppState:
    """Dear PyGui-specific application state."""
    def __init__(self):
        self.bridge = SystemBridge()
        self.current_tab: int = 0
        self.tab_handlers: Dict[int, Any] = {}
        self.symbol_cache: List[str] = []  # validated symbols
        self._refresh_count: int = 0
        self._last_poll: float = 0.0

    def poll(self) -> Dict[str, Any]:
        """Refresh system snapshot, rate-limited to ~2 Hz."""
        now = time.time()
        if now - self._last_poll < 0.5:
            # Return cached snapshot
            return self.bridge.refresh() if hasattr(self.bridge, '_state_snapshot') and self.bridge._state_snapshot else {}
        self._last_poll = now
        snap = self.bridge.refresh()
        self._refresh_count += 1
        # Refresh validated symbols
        self.symbol_cache = self.bridge.get_validated_symbols()
        return snap


# Global app state instance
_app: Optional[AppState] = None

def get_app() -> AppState:
    global _app
    if _app is None:
        _app = AppState()
    return _app


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def fmt_time(ts: float) -> str:
    """Format unix timestamp to readable string."""
    if ts <= 0:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "—"


def fmt_r(val: float) -> str:
    """Format R-multiple with sign."""
    if math.isnan(val) or math.isinf(val):
        return "∞"
    if val >= 0:
        return f"+{val:.2f}R"
    return f"{val:.2f}R"


# ---------------------------------------------------------------------------
# Confirmation dialog (spec 7.3: second confirmation for destructive actions)
# ---------------------------------------------------------------------------

def show_confirmation_dialog(
    title: str,
    message: str,
    confirm_label: str = "CONFIRM",
    callback_confirm: Optional[Callable] = None,
    width: int = 500,
    height: int = 220,
) -> None:
    """Show a modal confirmation dialog.
    
    The user must explicitly type or click confirm.  The cancel button
    simply closes the dialog.
    """
    modal_tag = "confirm_modal_" + str(int(time.time() * 1000) % 100000)

    # Check if any existing modals are open and close them
    existing = dpg.get_aliases()
    for alias in existing:
        if alias.startswith("confirm_modal_"):
            dpg.delete_item(alias, children_only=True)
            dpg.delete_item(alias)

    with dpg.window(
        tag=modal_tag,
        label=title,
        modal=True,
        show=True,
        width=width,
        height=height,
        no_resize=True,
        no_move=True,
    ):
        dpg.add_text(message)
        dpg.add_spacer(height=8)
        dpg.add_separator()
        dpg.add_spacer(height=8)

        # Confirm and Cancel buttons
        with dpg.group(horizontal=True):
            confirm_tag = modal_tag + "_confirm"

            dpg.add_button(
                label=confirm_label,
                tag=confirm_tag,
                callback=lambda s, a: _confirm_action(modal_tag, callback_confirm),
                width=120,
                height=40,
            )
            dpg.add_spacer(width=10)
            dpg.add_button(
                label="CANCEL",
                callback=lambda: dpg.delete_item(modal_tag),
                width=120,
                height=40,
            )


def _confirm_action(modal_tag: str, callback: Optional[Callable]) -> None:
    """Execute the confirmation callback and close the modal."""
    dpg.delete_item(modal_tag)
    if callback:
        callback()


try:
    from typing import Callable
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Tab functions — each tab registers its widgets and provides a render callback
# ---------------------------------------------------------------------------

def _build_tab1_onboarding(state: AppState, parent: str):
    """Build Tab 1: Symbol Onboarding — list + 7-step wizard."""
    from gui_tab1_onboarding import build_onboarding_tab
    build_onboarding_tab(state, parent)


def _build_tab2_live_control(state: AppState, parent: str):
    """Build Tab 2: Live Control — signals, positions, emergency stop."""
    from gui_tab2_live_control import build_live_control_tab
    build_live_control_tab(state, parent)


def _build_tab3_performance(state: AppState, parent: str):
    """Build Tab 3: Performance Monitor."""
    from gui_tab3_performance import build_performance_tab
    build_performance_tab(state, parent)


def _build_tab4_account(state: AppState, parent: str):
    """Build Tab 4: Account & Connection."""
    from gui_tab4_account import build_account_tab
    build_account_tab(state, parent)


# ---------------------------------------------------------------------------
# Render emergency stop bar (always visible, fixed at bottom)
# ---------------------------------------------------------------------------

def _render_emergency_bar(state: AppState) -> None:
    """Render the fixed emergency stop bar at the bottom of the main window."""
    # Delete and recreate the bar each render cycle to keep state fresh
    for tag in ["emergency_bar_group", "emergency_bar_group_bg"]:
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)

    snap = state.bridge.refresh() if state.bridge else {}
    e_stop_active = snap.get("emergency_stop", False) if snap else False

    with dpg.group(
        tag="emergency_bar_group",
        parent=TAG_MAIN_WINDOW,
        horizontal=True,
    ):
        dpg.add_spacer(width=10)

        if e_stop_active:
            # Red background — emergency is active
            dpg.add_text(
                "🚨 EMERGENCY STOP ACTIVE — All signals halted",
                color=(255, 255, 255),
            )
            dpg.add_spacer(width=20)
            dpg.add_button(
                label="✅ RESET EMERGENCY STOP",
                tag="emergency_reset_button",
                callback=lambda s, a: _emergency_reset_callback(state),
                width=200,
                height=40,
            )
        else:
            dpg.add_spacer(width=20)
            dpg.add_button(
                label="🛑 EMERGENCY STOP",
                tag=TAG_EMERGENCY_STOP_BUTTON,
                callback=lambda s, a: _emergency_activate_callback(state),
                width=200,
                height=40,
            )
            dpg.add_spacer(width=10)
            dpg.add_text(
                "Click to halt ALL signal engines immediately",
                color=(200, 200, 200),
            )


def _emergency_activate_callback(state: AppState) -> None:
    """Callback for emergency stop — requires confirmation."""
    show_confirmation_dialog(
        title="🚨 EMERGENCY STOP CONFIRMATION",
        message="⚠️ This will halt ALL signal engines immediately!\n\n"
                "Open positions will NOT be closed.\n"
                "No new signals will be generated until reset.\n\n"
                "Are you absolutely sure?",
        confirm_label="🛑 ACTIVATE EMERGENCY STOP",
        callback_confirm=lambda: state.bridge.set_emergency_stop(True),
        width=500,
        height=250,
    )


def _emergency_reset_callback(state: AppState) -> None:
    """Callback for resetting emergency stop."""
    show_confirmation_dialog(
        title="Reset Emergency Stop",
        message="Confirm reset: signal engines will resume.",
        confirm_label="✅ RESET",
        callback_confirm=lambda: state.bridge.set_emergency_stop(False),
        width=400,
        height=180,
    )


# ---------------------------------------------------------------------------
# Main Dear PyGui setup
# ---------------------------------------------------------------------------

def _setup_dpg(state: AppState) -> None:
    """Configure Dear PyGui application."""
    dpg.create_context()

    # Set up fonts (system default is fine)
    with dpg.font_registry():
        # Default font — Dear PyGui uses the system font
        pass

    dpg.create_viewport(
        title="Paper Trading V2 — Liquidity Sweep",
        width=1400,
        height=900,
        x_pos=100,
        y_pos=50,
        min_width=1100,
        min_height=700,
        resizable=True,
        always_on_top=False,
    )

    dpg.setup_dearpygui()


def _build_main_layout(state: AppState) -> None:
    """Build the main window with tab bar, content area, and emergency bar."""
    with dpg.window(
        tag=TAG_MAIN_WINDOW,
        label="Paper Trading V2 — Liquidity Sweep",
        width=1400,
        height=900,
        no_scrollbar=False,
        no_resize=False,
        no_move=False,
        no_close=True,
        no_collapse=True,
    ) as main_win:

        # ------------------------------------------------------------------
        # Tab bar (top)
        # ------------------------------------------------------------------
        with dpg.group(horizontal=False):
            # Status bar
            with dpg.group(horizontal=True, tag=TAG_STATUS_BAR):
                dpg.add_text("Status: ", color=(180, 180, 180))
                dpg.add_text("Initializing...", tag="status_text", color=(255, 200, 100))

            dpg.add_separator()

            # Tab buttons
            with dpg.group(horizontal=True):
                for idx, name in enumerate(TAB_NAMES):
                    dpg.add_button(
                        label=name,
                        tag=f"tab_btn_{idx}",
                        callback=lambda s, a, u=idx: _switch_tab(state, u),
                        width=280,
                        height=30,
                    )
                    dpg.add_spacer(width=5)

            dpg.add_separator()

            # Tab content area (placeholder — swapped on tab switch)
            with dpg.child_window(
                tag="tab_content",
                width=-1,
                height=-120,
                border=True,
            ) as tab_content:
                # We'll build tab content here lazily
                dpg.add_text("Select a tab above.", tag="tab_placeholder")

        # Emergency bar at bottom
        dpg.add_separator()
        _render_emergency_bar(state)

    # Show the first tab by default
    _switch_tab(state, 0)


def _switch_tab(state: AppState, tab_idx: int) -> None:
    """Switch to the given tab index, building its content if needed."""
    state.current_tab = tab_idx

    # Clear tab content area
    content_container = "tab_content"
    children = dpg.get_item_children(content_container, slot=1)
    if children:
        for child in children:
            dpg.delete_item(child)

    # Build the selected tab's content
    builders = {
        0: _build_tab1_onboarding,
        1: _build_tab2_live_control,
        2: _build_tab3_performance,
        3: _build_tab4_account,
    }
    builder = builders.get(tab_idx)
    if builder:
        builder(state, content_container)

    # Update status bar
    dpg.set_value("status_text", f"Tab: {TAB_NAMES[tab_idx]}")


# ---------------------------------------------------------------------------
# Polling render loop
# ---------------------------------------------------------------------------

def _render_loop(state: AppState) -> None:
    """Dear PyGui render callback — refreshes data and updates UI widgets."""
    snap = state.poll()
    e_stop = snap.get("emergency_stop", False) if snap else False

    # Update emergency bar
    _render_emergency_bar(state)

    # Update status bar with latest info
    mt5_ok = snap.get("mt5_status", {}).get("connected", False) if snap else False
    status_parts = [
        f"MT5: {'✅' if mt5_ok else '❌'}",
        f"Refresh: {state._refresh_count}",
    ]
    dpg.set_value("status_text", " | ".join(status_parts))

    # Allow tabs to update their dynamic content
    # Each tab can re-render its dynamic section if it registers an update
    # handler — stored in state.tab_handlers
    handler = state.tab_handlers.get(state.current_tab)
    if handler:
        try:
            handler(snap)
        except Exception as exc:
            print(f"[GUI] Tab update error: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Main entry point for the Paper Trading V2 GUI."""
    global _app
    state = get_app()

    # Reset all automation levels to 1 on startup (spec 7.3)
    state.bridge.reset_all_automation_levels()

    # Setup Dear PyGui
    _setup_dpg(state)

    # Build the main layout
    _build_main_layout(state)

    # Register the render callback (called every frame by DPG)
    dpg.set_frame_callback(0, lambda: _render_loop(state))

    # Show the viewport
    dpg.show_viewport()
    dpg.maximize_viewport()

    # Set up exit handler
    dpg.set_exit_callback(lambda: _on_exit(state))

    # Start the Dear PyGui main loop
    dpg.start_dearpygui()

    # Cleanup after loop ends
    dpg.destroy_context()


def _on_exit(state: AppState) -> None:
    """Called when the GUI is about to close."""
    print("[GUI] Shutting down Paper Trading V2 GUI...", file=sys.stderr)
    # Do NOT close open positions (spec requirement)
    state.bridge._log_user_action("GUI shutdown")


if __name__ == "__main__":
    main()
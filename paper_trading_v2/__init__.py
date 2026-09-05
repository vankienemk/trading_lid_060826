"""
paper_trading_v2 — Paper Trading V2 package.

Modules:
    logger_v2.py               — SQLite logger (signals + user action log)
    shared_app_state_v2.py     — Thread-safe singleton application state
    signal_engine_v2.py        — Multi-symbol signal engine per spec
    risk_guard_v2.py           — Dynamic asset risk guard + block bootstrap kill-switch
    execution_layer_v2.py      — MCP execution layer with auto-reconnect
    signal_polling_engine.py   — Background polling orchestrator (engine loop)
    gui_components.py          — Shared GUI widgets, styles, confirmation dialogs
    gui_bridge.py              — SystemBridge: GUI ↔ engine module adapter
    gui_main.py                — Main window shell (4-tab, emergency stop, status bar)
    gui_tab_onboarding.py      — Tab 1: Symbol Onboarding + 7-step wizard
    gui_tab_live.py            — Tab 2: Live Control (signals, positions, automation)
    gui_tab_performance.py     — Tab 3: Performance Monitor (metrics, kill-switch)
    gui_tab_account.py         — Tab 4: Account & Connection (MCP, token, account info)

Entry point:
    run.py (at project root) — Launch GUI or headless mode
"""

from __future__ import annotations

__version__ = "2.0.0"
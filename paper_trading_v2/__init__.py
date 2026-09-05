"""
paper_trading_v2 — Paper Trading V2 package.

Modules:
    logger_v2.py               — SQLite logger (signals + user action + system log)
    shared_app_state_v2.py     — Thread-safe singleton application state + ModelRegistry
    signal_engine_v2.py        — Multi-symbol signal engine (loads model by model_id)
    risk_guard_v2.py           — Dynamic asset risk guard + block bootstrap kill-switch
    execution_layer_v2.py      — MCP execution layer with auto-reconnect
    signal_polling_engine.py   — Background polling orchestrator (engine loop)
    gui_components.py          — Shared GUI widgets, styles, confirmation dialogs
    gui_bridge.py              — SystemBridge: GUI ↔ engine module adapter + Model Registry API
    gui_main.py                — Main window shell (5-tab, emergency stop, status bar)
    gui_tab_onboarding.py      — Tab 1: Symbol Onboarding (registry table + model selection)
    gui_tab_live.py            — Tab 2: Live Control (signals, positions, automation)
    gui_tab_performance.py     — Tab 3: Performance Monitor (metrics, kill-switch)
    gui_tab_account.py         — Tab 4: Account & Connection (MCP, token, account info)
    gui_tab_system_log.py      — Tab 5: System Log (real-time log viewer with filters)

Entry point:
    run.py (at project root) — Launch GUI or headless mode
"""

from __future__ import annotations

__version__ = "2.0.0"
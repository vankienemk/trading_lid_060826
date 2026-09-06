# Archive

This directory contains code and files that have been superseded by the
`trading_live` restructure. They are kept for historical reference only.

## Contents

### `old/paper_trading/`
Original V1 paper trading system (single-symbol, Dear PyGui-based).
- `run_trading.py` — V1 entry point
- `signal_engine.py` — V1 single-symbol engine
- `execution_layer.py` — V1 MCP execution
- `risk_guard.py` — V1 risk guard
- `shared_app_state.py` — V1 state management
- `logger.py` — V1 SQLite logger
- `dashboard.py` — V1 Dear PyGui dashboard
- `config.yaml` — V1 configuration
- `src/` — V1 source modules (features, events, indicators, liquidity, scoring)

### `old/paper_trading_v2_gui/`
Intermediate V2 GUI prototype (Dear PyGui-based, pre-PySide6 migration).
- `gui_main.py`, `gui_bridge.py`, `gui_tab1_onboarding.py`, `gui_tab2_live_control.py`,
  `gui_tab3_performance.py`, `gui_tab4_account.py`

### `old/paper_trading_v2/`
Full V2 paper trading system (PySide6-based, pre-restructure) — archived 2026-09-06.
This is the complete codebase before the `trading_live/` restructure split live trading
code into `trading_live/live/` and research pipeline into `trading_live/research/`.
- `gui_main.py`, `gui_bridge.py`, `gui_components.py` — GUI layer
- `gui_tab_onboarding.py`, `gui_tab_live.py`, `gui_tab_performance.py`, `gui_tab_account.py` — Tabs
- `gui_tab_system_log.py` — System Log tab (added by gui_rework_team)
- `signal_engine_v2.py`, `signal_polling_engine_v2.py` — Signal engines
- `execution_layer_v2.py` — Order execution
- `risk_guard_v2.py` — Risk management
- `shared_app_state_v2.py` — State management
- `logger_v2.py` — Centralized logging
- `mt5_mcp_client.py` — MCP client
- `KNOWLEDGE_BASE.md` — System documentation
- `configs/`, `symbol_registry.json`, `paper_trading_v2.db` — Configuration and data

### `old/*.py` (root-level scripts)
Standalone diagnostic and utility scripts:
- `run.py` — Old V2 application entry point (replaced by `trading_live/live/db/run.py`)
- `env_audit_test.py` — MCP_TOKEN environment audit
- `add_symbols.py`, `check_account.py`, `list_symbols.py`, `check_mcp_tools.py` — MCP utilities
- `test_chart.py`, `test_chart2.py`, `test_chart3.py` — Chart testing scripts
- `run_paper_trading_v2_gui.py` — Old V2 GUI launcher

## Current Structure (since 2026-09-06)

See `trading_live/` at the workspace root for the active codebase:

```
trading_live/
  live/           — Live trading (GUI, engine, MCP, logging, state, DB)
  research/       — Research pipeline (XAUUSD Liquidity Sweep)
  artifacts/      — Trained models and datasets
  model_registry/ — Model registry index
  docs/           — Documentation
  archive/        — This archive
```
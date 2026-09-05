#!/usr/bin/env python3
"""
run.py — Paper Trading V2 Application Entry Point

Integrates all modules:
  1. SharedAppState V2 (dynamic symbol registry)
  2. Logger V2 (SQLite signals + user action log)
  3. ExecutionLayer V2 (MCP auto-reconnect)
  4. RiskGuard V2 (block bootstrap kill-switch)
  5. SignalEngine V2 (per-symbol engine closures)
  6. SignalPollingEngine V2 (background polling orchestrator)
  7. PySide6 GUI (4-tab desktop application)

Usage:
    python3 run.py                  # Launch GUI + engine
    python3 run.py --headless       # Engine only (no GUI)
    python3 run.py --help           # Show options

Default: launches PySide6 GUI with background polling engine.

Spec compliance:
  - Automation level resets to 1 on every startup
  - Emergency stop halts all signal engines
  - All destructive actions require confirmation
  - Every user action logged to user_action_log table
  - Multi-symbol: any symbol with status=validated gets an engine
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Ensure paper_trading_v2 is importable
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# ---------------------------------------------------------------------------
# Configure logging
# ---------------------------------------------------------------------------

def setup_logging(level: str = "INFO") -> None:
    """Configure application-wide logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Module imports (lazy within functions to avoid early side effects)
# ---------------------------------------------------------------------------

def _load_config() -> Dict[str, Any]:
    """Load v2_frozen.yaml configuration."""
    import yaml
    cfg_path = _HERE / "paper_trading_v2" / "configs" / "v2_frozen.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if cfg is None:
        raise ValueError(f"Empty config at {cfg_path}")
    return cfg


def _load_symbol_configs(
    risk_guard: Any,
    symbols_dir: str = "configs/symbols",
) -> None:
    """Load per-symbol configs from *symbols_dir* into risk guard."""
    symbols_path = _HERE / symbols_dir
    if not symbols_path.is_dir():
        logging.warning("Symbols directory not found: %s", symbols_path)
        return
    risk_guard.load_symbol_configs(str(symbols_path))


# ---------------------------------------------------------------------------
# Initialize modules
# ---------------------------------------------------------------------------

def init_modules(config: Dict[str, Any]) -> Dict[str, Any]:
    """Initialize all modules and return a dict of module references.

    Returns:
        {
            "state": SharedAppState
            "execution_layer": ExecutionLayer
            "risk_guard": RiskGuard
            "polling_engine": SignalPollingEngine
            "bridge": SystemBridge
        }
    """
    from paper_trading_v2.shared_app_state_v2 import SharedAppState, SymbolConfig
    from paper_trading_v2.logger_v2 import init_db
    from paper_trading_v2.execution_layer_v2 import ExecutionLayer
    from paper_trading_v2.risk_guard_v2 import RiskGuard
    from paper_trading_v2.signal_polling_engine_v2 import SignalPollingEngine
    from paper_trading_v2.gui_bridge import SystemBridge

    # ---------- 1. SharedAppState ----------
    state = SharedAppState.get_instance()

    # ---------- 2. Logger: init DB ----------
    db_path = str(_HERE / "paper_trading_v2" / "paper_trading_v2.db")
    init_db(db_path)
    logging.info("Database initialized at %s", db_path)

    # ---------- 3. ExecutionLayer ----------
    mcp_endpoint = config.get("trading", {}).get("mcp", {}).get(
        "endpoint", "http://127.0.0.1:22346/mcp"
    )
    mcp_token = os.environ.get("MCP_TOKEN", "")
    auto_reconnect = config.get("trading", {}).get("mcp", {}).get(
        "auto_reconnect", True
    )
    exec_layer = ExecutionLayer(
        endpoint=mcp_endpoint,
        token=mcp_token,
        auto_reconnect=auto_reconnect,
    )

    # ---------- 4. RiskGuard ----------
    risk_guard = RiskGuard(db_path=db_path)

    # Load per-symbol configs
    _load_symbol_configs(risk_guard)

    # Register default symbols from frozen config if not already registered
    primary = config.get("project", {}).get("primary_symbol", "XAUUSD")
    secondary = config.get("project", {}).get("secondary_symbol", "EURUSD")
    for sym in [primary, secondary]:
        if sym not in state.get_registered_symbols():
            state.register_symbol(
                sym,
                SymbolConfig(name=sym, status="validated", active=True),
            )

    # Reset automation levels to 1 (manual) on startup (spec 7.3)
    for sym in state.get_registered_symbols():
        state.set_automation_level(sym, 1)
    logging.info("All automation levels reset to 1 (manual confirm)")

    # ---------- 5. SystemBridge (GUI adapter) ----------
    bridge = SystemBridge.get_instance()
    # Bridge is created; it already auto-initializes its exec layer

    # ---------- 6. SignalPollingEngine ----------
    poll_interval = config.get("trading", {}).get("polling_interval_s", 30)
    polling_engine = SignalPollingEngine(
        state=state,
        execution_layer=exec_layer,
        risk_guard=risk_guard,
        polling_interval_s=poll_interval,
    )

    # Register engines for currently validated symbols
    polling_engine.refresh_engines()
    logging.info(
        "Registered %d signal engines: %s",
        polling_engine.engine_count,
        ", ".join(polling_engine.registered_symbols) or "(none)",
    )

    return {
        "state": state,
        "execution_layer": exec_layer,
        "risk_guard": risk_guard,
        "polling_engine": polling_engine,
        "bridge": bridge,
    }


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run_gui(modules: Dict[str, Any]) -> None:
    """Launch the PySide6 GUI with background polling."""
    from paper_trading_v2.gui_main import MainWindow
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Apply dark theme
    from paper_trading_v2.gui_components import DARK_STYLESHEET
    app.setStyleSheet(DARK_STYLESHEET)

    # Start background polling engine
    modules["polling_engine"].start()
    logging.info("Signal polling engine started")

    # Create and show main window
    window = MainWindow(modules["bridge"])
    window.show()

    # Log application start
    from paper_trading_v2.logger_v2 import log_user_action
    log_user_action("application_start", '{"mode": "gui", "version": "2.0.0"}')

    logging.info("Paper Trading V2 GUI started")
    exit_code = app.exec()

    # Shutdown
    modules["polling_engine"].stop()
    modules["execution_layer"].stop_polling()
    logging.info("Paper Trading V2 shut down cleanly")
    sys.exit(exit_code)


def run_headless(modules: Dict[str, Any]) -> None:
    """Run engine only (no GUI)."""
    polling = modules["polling_engine"]
    polling.start()
    logging.info("Headless mode — signal polling engine running")

    from paper_trading_v2.logger_v2 import log_user_action
    log_user_action("application_start", '{"mode": "headless", "version": "2.0.0"}')

    try:
        # Keep running until Ctrl+C
        import signal as sig

        def _handle_sigterm(signum, frame):
            logging.info("Received SIGTERM — shutting down")
            polling.stop()
            modules["execution_layer"].stop_polling()
            sys.exit(0)

        sig.signal(sig.SIGTERM, _handle_sigterm)
        sig.signal(sig.SIGINT, _handle_sigterm)

        print("Paper Trading V2 running in headless mode. Press Ctrl+C to stop.")
        while polling.is_running:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt — shutting down")
    finally:
        polling.stop()
        modules["execution_layer"].stop_polling()
        logging.info("Paper Trading V2 headless mode shut down")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Paper Trading V2 — Liquidity Sweep",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run engine only (no PySide6 GUI)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level (default: INFO)",
    )
    return parser.parse_args()


def main() -> None:
    """Application entry point."""
    args = parse_args()
    setup_logging(args.log_level)

    try:
        config = _load_config()
        modules = init_modules(config)

        if args.headless:
            run_headless(modules)
        else:
            run_gui(modules)
    except Exception as e:
        logging.error("Fatal startup error: %s", e, exc_info=True)
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
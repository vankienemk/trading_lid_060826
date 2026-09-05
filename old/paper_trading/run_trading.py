#!/usr/bin/env python3
"""
run_trading.py — Paper Trading System Entry Point (Liquidity Sweep V2)

Orchestrates all components:
  - Logger (SQLite)
  - Signal Engine (config-driven V2 frozen)
  - Risk Guard (block bootstrap kill-switch per asset)
  - Execution Layer (MCP with manual confirm level 1)
  - Streamlit dashboard UI (separate process)

Usage:
    python run_trading.py --config config.yaml

Arguments:
    --config   Path to YAML configuration file (default: config.yaml)

Flow:
    1. Parse command line for --config path.
    2. Initialize: read config → init logger DB → connect MCP → display
       account info → wait for user confirmation it's a demo account.
    3. Start Signal Engine polling loop: every 30s, check for new M15 candle,
       run signal detection → Risk Guard evaluates → if allowed, add to
       pending_signals in shared state (Level 1 = manual).
    4. Streamlit dashboard runs in parallel (user starts separately via
       'streamlit run dashboard.py' — instructions printed to console).
    5. Handle graceful shutdown (SIGINT) — stop engine, do NOT close positions.
    6. Emergency stop flag — halts signal detection immediately.

Example:
    python run_trading.py --config paper_trading/config.yaml
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

# ---------------------------------------------------------------------------
# Ensure paper_trading/ is importable
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import logger as paper_logger
from execution_layer import ExecutionLayer, get_execution, ConnectionFailedError
from shared_app_state import get_state, PendingSignal
from risk_guard import RiskGuard, create_risk_guard

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_trading")


# ---------------------------------------------------------------------------
# Signal Engine bridge (lazy-loaded when signal_engine module is available)
# ---------------------------------------------------------------------------

class SignalEngineBridge:
    """
    Thin wrapper around signal_engine.create_check_new_bar().

    The signal_engine module has heavy imports (pandas, numpy, sklearn, etc.)
    and expects the frozen V2 model files to exist. We load it lazily so that
    the orchestrator can start up and validate MCP connection first, even if
    the signal engine artifacts are not yet in place.
    """

    def __init__(self, config: dict):
        self._config = config
        self._checkers: Dict[str, Any] = {}  # symbol -> check_new_bar callable
        self._loaded = False
        self._load_error: Optional[str] = None
        self._import_ok = False

    def load(self) -> bool:
        """Import and initialize signal engine modules.

        Returns True on success, False if the engine cannot be loaded
        (missing artifacts, missing dependencies, etc.).
        """
        if self._loaded:
            return True

        try:
            # Lazy import inside the method so top-level script runs fast
            from signal_engine import create_check_new_bar as _create_check

            trading = self._config.get("trading", {})
            symbols = trading.get("symbols", ["XAUUSD"])
            se_cfg = trading.get("signal_engine", {})

            # Resolve config path for the V2 frozen config
            v2_config_path = se_cfg.get("v2_frozen_config_path",
                                         "../xauusd-liquidity-sweep/pipeline_v2/configs/v2_frozen.yaml")
            model_path = se_cfg.get("model_path",
                                    "../xauusd-liquidity-sweep/pipeline_v2/artifacts/models/model.pkl")
            calibrator_path = se_cfg.get("calibrator_path",
                                          "../xauusd-liquidity-sweep/pipeline_v2/artifacts/models/calibrator.pkl")

            # Resolve relative paths against the paper_trading/ directory
            def _resolve(p: str) -> str:
                return str((_HERE / Path(p)).resolve())

            # Create a mock MCP client that wraps ExecutionLayer for candle fetching
            execution = get_execution()

            class _MCPClientAdapter:
                """
                Adapter that wraps ExecutionLayer.get_new_candles() into the
                DataFrame-returning interface that signal_engine.create_check_new_bar()
                expects. The signal engine calls get_chart_history(symbol) and
                expects a raw DataFrame.
                """
                def get_chart_history(self, symbol: str) -> "pd.DataFrame":
                    import pandas as pd
                    candles_list = execution.get_new_candles(symbol, period="M15")
                    if not candles_list:
                        return pd.DataFrame()
                    df = pd.DataFrame(candles_list)
                    # Normalise column names
                    df.columns = [c.lower() for c in df.columns]
                    # Convert time column to datetime index
                    time_cols = ["time", "datetime", "timestamp", "date", "index"]
                    found = [c for c in time_cols if c in df.columns]
                    if found:
                        tcol = found[0]
                        df.index = pd.to_datetime(df[tcol], utc=True)
                        df = df.drop(columns=[tcol])
                    elif "tick_volume" in df.columns:
                        # Some MCP servers return unnamed index; fall back to range index
                        pass
                    return df

            mcp_adapter = _MCPClientAdapter()

            self._import_ok = True

            for sym in symbols:
                try:
                    checker = _create_check(
                        symbol=sym,
                        mcp_client=mcp_adapter,
                        config_path=_resolve(v2_config_path),
                        model_path=_resolve(model_path),
                        calibrator_path=_resolve(calibrator_path),
                    )
                    self._checkers[sym] = checker
                    logger.info("Signal engine loaded for %s", sym)
                except Exception as exc:
                    logger.warning("Cannot load signal engine for %s: %s", sym, exc)
                    self._load_error = f"{sym}: {exc}"

            self._loaded = True
            return True

        except ImportError as exc:
            self._load_error = f"Cannot import signal_engine module: {exc}"
            logger.warning(self._load_error)
            return False
        except FileNotFoundError as exc:
            self._load_error = f"Missing signal engine artifacts: {exc}"
            logger.warning(self._load_error)
            return False
        except Exception as exc:
            self._load_error = f"Unexpected error loading signal engine: {exc}"
            logger.warning(self._load_error)
            return False

    def check_new_bars(self) -> List[Dict[str, Any]]:
        """Run check_new_bar() for all loaded symbols.

        Returns a list of dicts representing SignalCandidate-like results
        (or empty list if the engine is not loaded).
        """
        if not self._loaded or not self._checkers:
            return []

        results: List[Dict[str, Any]] = []
        for symbol, checker in self._checkers.items():
            try:
                candidates = checker()  # list[SignalCandidate]
                for c in candidates:
                    results.append({
                        "symbol": c.symbol,
                        "direction": c.direction,
                        "entry_price": c.entry_price,
                        "stop_price": c.stop_price,
                        "target_price": c.target_price,
                        "entry_time": c.entry_time.isoformat() if hasattr(c.entry_time, 'isoformat') else str(c.entry_time),
                        "event_time": c.event_time,
                        "event_id": c.event_id,
                        "rule_score": c.rule_score,
                        "model_prob": c.model_prob,
                        "combined_score": c.combined_score,
                        "penetration_atr": c.penetration_atr,
                        "wick_ratio": c.wick_ratio,
                        "reclaim_atr": c.reclaim_atr,
                        "h1_trend": c.h1_trend,
                        "confirmation_delay_bars": c.confirmation_delay_bars,
                        "confirmation_range_atr": c.confirmation_range_atr,
                        "atr_value": c.atr_value,
                        "metadata": getattr(c, "metadata", {}),
                    })
            except Exception as exc:
                logger.error("Signal engine check failed for %s: %s", symbol, exc)
        return results

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    """Load the YAML configuration file.

    Args:
        config_path: Path to the YAML file.

    Returns:
        Parsed configuration dictionary.

    Raises:
        FileNotFoundError: If the config file does not exist.
        yaml.YAMLError: If the config file is malformed.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if cfg is None:
        raise ValueError(f"Empty configuration file: {config_path}")

    logger.info("Loaded configuration from %s", config_path)
    return cfg


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_system(config: dict) -> Tuple[str, ExecutionLayer, RiskGuard]:
    """Initialize core system components.

    Steps:
        1. Read trading settings from config.
        2. Initialize the logger database.
        3. Create and connect the Execution Layer (MCP).
        4. Create the Risk Guard instance.
        5. Return (db_path, execution_layer, risk_guard).

    Args:
        config: Parsed configuration dictionary.

    Returns:
        Tuple of (db_path, execution_layer, risk_guard).
    """
    trading = config.get("trading", {})
    mcp_endpoint = trading.get("mcp_endpoint", "http://127.0.0.1:22346/mcp")
    mcp_token_env = trading.get("mcp_token_env", "MCP_TOKEN")
    db_path = trading.get("db_path", "paper_trading.db")
    default_lot_size = trading.get("default_lot_size", 0.01)

    # Resolve db_path relative to paper_trading/ if relative
    db_abs = str(_HERE / Path(db_path))

    # --- 1. Init logger database ---
    logger.info("Initialising logger database at %s", db_abs)
    actual_db_path = paper_logger.init_db(db_path=db_abs)
    logger.info("Logger database ready: %s", actual_db_path)

    # --- 2. Create Execution Layer (MCP) ---
    mcp_token = os.environ.get(mcp_token_env, "")
    if not mcp_token:
        logger.warning(
            "MCP_TOKEN environment variable '%s' is empty. "
            "MCP connection may fail if the server requires authentication.",
            mcp_token_env,
        )

    # Set automation level in shared state
    state = get_state()
    automation_level = trading.get("automation_level", 1)
    state.set_automation_level(automation_level)
    logger.info("Automation level set to %d (%s)", automation_level,
                 {1: "Manual Confirm", 2: "Semi-Auto", 3: "Full Auto"}.get(automation_level, "Unknown"))

    execution = ExecutionLayer(
        endpoint=mcp_endpoint,
        token=mcp_token,
        auto_reconnect=True,
    )
    logger.info("Execution Layer created (endpoint=%s)", mcp_endpoint)

    # --- 3. Create Risk Guard ---
    risk_cfg = trading.get("risk", {})
    risk_guard = create_risk_guard(
        db_path=actual_db_path,
        max_open_per_asset=risk_cfg.get("max_open_per_asset", 1),
        max_open_total=risk_cfg.get("max_open_total", 2),
        min_interval_since_last_s=risk_cfg.get("min_interval_since_last_s", 300),
    )
    logger.info("Risk Guard initialised")

    return actual_db_path, execution, risk_guard


# ---------------------------------------------------------------------------
# MCP connection and demo account confirmation
# ---------------------------------------------------------------------------

def connect_and_confirm_demo(execution: ExecutionLayer, args: argparse.Namespace) -> bool:
    """Connect to MCP, display account info, and wait for demo confirmation.

    Steps:
        1. Attempt MCP health_check.
        2. If healthy, fetch account info via get_account_info().
        3. Display account details (balance, leverage, server, etc.).
        4. Prompt the user to confirm this is a demo account.
        5. If confirmed, return True.
        6. If connection fails or user does not confirm, return False.

    Args:
        execution: Initialised ExecutionLayer instance.

    Returns:
        True if MCP is connected and the user confirmed demo mode.
    """
    print("\n" + "=" * 60)
    print("  PAPER TRADING SYSTEM — Liquidity Sweep V2")
    print("=" * 60)

    # --- Check MCP health ---
    print("\n[1/3] Connecting to MCP server...")
    health = execution.health_check()
    if not health.get("ok", False):
        print(f"  ❌ MCP connection FAILED: {health.get('error', 'unknown error')}")
        print("  Please ensure MetaTrader 5 is running and MCP bridge is active.")
        print("  Tip: Check MCP endpoint configuration and MCP_TOKEN environment variable.")
        return False

    print(f"  ✅ MCP connected (session_id={health.get('session_id', 'N/A')})")
    print(f"  URL: {health.get('url', 'N/A')}")
    print(f"  Elapsed: {health.get('elapsed_seconds', 0):.3f}s")

    # --- Fetch account info ---
    print("\n[2/3] Fetching account information...")
    try:
        account_info = execution.get_account_info()
        print("  ✅ Account info retrieved:")
        print(f"     Balance: {account_info.get('balance', 'N/A')}")
        print(f"     Equity:  {account_info.get('equity', 'N/A')}")
        print(f"     Leverage: {account_info.get('leverage', 'N/A')}")
        print(f"     Server:  {account_info.get('server', 'N/A')}")
        print(f"     Name:    {account_info.get('name', 'N/A')}")
        account_type = account_info.get("account_type", account_info.get("type", "unknown"))
        print(f"     Type:    {account_type}")

        # Update shared state
        state = get_state()
        is_demo = "demo" in str(account_type).lower()
        state.update_mt5_status(
            connected=True,
            account_type="demo" if is_demo else "real",
            account_info=account_info,
        )

        if not is_demo:
            print("\n  ⚠️  WARNING: This appears to be a REAL trading account!")
            print("  ⚠️  The paper trading system will NOT place real orders,")
            print("  ⚠️  but please confirm below before proceeding.")

    except ConnectionFailedError as exc:
        print(f"  ❌ Failed to retrieve account info: {exc}")
        print("  The system will continue but account details are unavailable.")
        account_type = "unknown"

    # --- Demo confirmation ---
    print("\n[3/3] DEMO ACCOUNT CONFIRMATION")
    print("-" * 40)
    print("  This system is designed for PAPER TRADING only.")
    print("  It must operate on a DEMO (simulated) account.")
    print("  If you are connected to a REAL account, STOP immediately.")
    print("-" * 40)

    if args.yes:
        print("\n  ✅ --yes flag set. Demo confirmation SKIPPED.\n")
        return True

    try:
        response = input("\n  Confirm this is a DEMO account? (yes/NO): ").strip().lower()
        confirmed = response in ("yes", "y", "demo")
    except (EOFError, KeyboardInterrupt):
        confirmed = False

    if confirmed:
        print("\n  ✅ Demo account confirmed. Starting signal engine...\n")
        return True
    else:
        print("\n  ❌ Demo account NOT confirmed. Aborting.\n")
        return False


# ---------------------------------------------------------------------------
# Signal polling engine
# ---------------------------------------------------------------------------

class SignalPollingEngine:
    """
    Polling loop that checks for new M15 candles every 30 seconds,
    runs signal detection, evaluates via Risk Guard, and adds valid
    signals to the shared state's pending_signals list.

    Attributes:
        running (bool): Whether the engine is currently running.
        emergency_stop (bool): If True, signal detection is halted
            immediately (set via shared state or external signal).
    """

    def __init__(
        self,
        execution: ExecutionLayer,
        risk_guard: RiskGuard,
        signal_bridge: SignalEngineBridge,
        config: dict,
    ):
        self.execution = execution
        self.risk_guard = risk_guard
        self.signal_bridge = signal_bridge
        self.config = config
        self.state = get_state()

        self.running = False
        self._poll_interval_s = 30  # check every 30 seconds
        self._last_bar_time: Dict[str, float] = {}  # symbol -> unix time of last processed bar
        self._loop_count: int = 0
        self._signals_emitted: int = 0

    def start(self) -> None:
        """Start the main polling loop.

        This method blocks until the engine is stopped (via SIGINT,
        emergency_stop, or an unrecoverable error).
        """
        self.running = True
        logger.info(
            "Signal polling engine started (interval=%ds, check for M15 bars)",
            self._poll_interval_s,
        )

        # Load signal engine (lazy)
        if not self.signal_bridge.load():
            logger.warning(
                "Signal engine not available: %s. "
                "The system will run without signal detection "
                "(manual orders only via dashboard).",
                self.signal_bridge.load_error or "unknown",
            )

        while self.running:
            try:
                self._tick()
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received during poll cycle.")
                break
            except Exception as exc:
                logger.error("Unhandled error in poll cycle: %s", exc)
                # Brief back-off before retry
                time.sleep(5)

        logger.info("Signal polling engine stopped.")

    def stop(self) -> None:
        """Gracefully stop the polling engine."""
        self.running = False
        logger.info("Signal polling engine stop requested.")

    # ------------------------------------------------------------------
    # Internal: one poll cycle
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """Execute one poll cycle.

        1. Check emergency stop flag.
        2. Refresh market data.
        3. For each configured symbol:
           a. Fetch latest candles and determine if a new M15 bar has appeared.
           b. If new bar, run signal detection via signal_bridge.
           c. For each candidate signal, evaluate via Risk Guard.
           d. If allowed, add to pending_signals in shared state.
        4. Update kill-switch stats from Risk Guard.
        """
        self._loop_count += 1

        # --- 1. Emergency stop check ---
        if self.state.emergency_stop:
            if self._loop_count % 20 == 0:  # log every ~10 min
                logger.info("Emergency stop is ACTIVE — signal detection halted")
            time.sleep(self._poll_interval_s)
            return

        # --- 2. Refresh market data ---
        try:
            trading = self.config.get("trading", {})
            symbols = trading.get("symbols", ["XAUUSD"])
            self.execution.refresh_market_data(symbols)
        except Exception as exc:
            logger.warning("Market data refresh failed: %s", exc)
            # Continue anyway — candles may still be retrieved

        # --- 3. Check for new signals per symbol ---
        trading_cfg = self.config.get("trading", {})
        default_lot = trading_cfg.get("default_lot_size", 0.01)

        signals = self.signal_bridge.check_new_bars()
        for sig in signals:

            if self.state.emergency_stop:
                break

            symbol = sig["symbol"]

            # --- a. Risk Guard evaluation ---
            # Get current open positions from shared state
            snap = self.state.get_snapshot()
            open_positions = snap.get("open_positions", [])
            # Convert shared state OpenPosition dicts to the format RiskGuard expects
            open_list = [
                {"asset": p["asset"]}
                for p in open_positions
            ]

            eval_result = self.risk_guard.evaluate_new_signal(
                asset=symbol,
                current_open_positions=open_list,
                order_sent_time=time.time(),
            )

            if not eval_result.get("allowed", False):
                logger.info(
                    "Signal %s %s blocked by Risk Guard: %s",
                    symbol, sig["direction"], eval_result.get("reason", ""),
                )
                continue

            # --- b. Create PendingSignal and add to shared state ---
            direction_mapped = "buy" if sig["direction"].lower() in ("long", "bullish") else "sell"
            import uuid
            signal_id = sig.get("event_id") or str(uuid.uuid4())

            pending = PendingSignal(
                asset=symbol,
                direction=direction_mapped,
                entry_price=sig["entry_price"],
                stop_loss=sig["stop_price"],
                take_profit=sig["target_price"],
                rule_score=sig.get("rule_score", 0.0),
                model_probability=sig.get("model_prob", 0.5),
                timestamp=time.time(),
                signal_id=signal_id,
            )

            # Append to pending signals
            snap = self.state.get_snapshot()
            current_pending = snap.get("pending_signals", [])
            # Check if this signal_id is already pending
            already_pending = any(
                p["signal_id"] == signal_id for p in current_pending
            )
            if already_pending:
                continue

            new_pending = [p["signal_id"] for p in current_pending] + [signal_id]
            # Use state.set_pending_signals with full objects
            import copy
            current_ps = list(self.state.pending_signals)
            current_ps.append(pending)
            self.state.set_pending_signals(current_ps)

            self._signals_emitted += 1
            logger.info(
                "Signal #%d: %s %s @ %.5f  S/L=%.5f  T/P=%.5f  "
                "rule_score=%.3f  model_prob=%.3f  [PENDING — Level 1, check dashboard]",
                self._signals_emitted,
                symbol,
                direction_mapped.upper(),
                pending.entry_price,
                pending.stop_loss,
                pending.take_profit,
                pending.rule_score,
                pending.model_probability,
            )

            # Record the order send time in Risk Guard (signal accepted)
            self.risk_guard.record_order_sent(symbol)

        # --- 4. Refresh Risk Guard metrics (update kill-switch status) ---
        try:
            self.risk_guard.refresh()
            metrics = self.risk_guard.get_all_metrics()
            ks_all = self.risk_guard.get_all_kill_switch_status()
            for asset_str in ks_all:
                ks_info = ks_all[asset_str]
                self.state.update_kill_switch(
                    asset=asset_str,
                    active=ks_info.get("active", False),
                    reason=ks_info.get("reason", ""),
                )

            # Log kill-switch status changes periodically
            if self._loop_count % 60 == 0:  # every ~30 min
                for asset_str, m in metrics.items():
                    ks = ks_all.get(asset_str, {})
                    if ks.get("active"):
                        logger.warning(
                            "KILL-SWITCH ACTIVE for %s: %s",
                            asset_str, ks.get("reason", ""),
                        )
                    else:
                        logger.info(
                            "Kill-switch %s: active=%s, n=%d, PF=%.4f, "
                            "CI95=[%.4f, %.4f]",
                            asset_str,
                            ks.get("active", False),
                            m.get("n", 0),
                            m.get("profit_factor", 0) or 0,
                            m.get("pf_ci_lower", 0) or 0,
                            m.get("pf_ci_upper", 0) or 0,
                        )
        except Exception as exc:
            logger.debug("Risk Guard metrics refresh failed: %s", exc)

        # --- 5. Sleep until next poll ---
        time.sleep(self._poll_interval_s)


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------

_engine_ref: Optional[SignalPollingEngine] = None


def _signal_handler_sigint(signum: int, frame: Any) -> None:
    """Handle SIGINT (Ctrl+C) — graceful shutdown, do NOT close positions."""
    print("\n")
    logger.info("SIGINT received — initiating graceful shutdown...")
    global _engine_ref
    if _engine_ref is not None:
        _engine_ref.stop()
    else:
        # No engine running; just exit
        sys.exit(0)


def _signal_handler_sigterm(signum: int, frame: Any) -> None:
    """Handle SIGTERM — same as SIGINT."""
    logger.info("SIGTERM received — initiating graceful shutdown...")
    global _engine_ref
    if _engine_ref is not None:
        _engine_ref.stop()
    else:
        sys.exit(0)


# ---------------------------------------------------------------------------
# Dashboard instructions
# ---------------------------------------------------------------------------

def print_dashboard_instructions() -> None:
    """Print instructions for starting the Streamlit dashboard."""
    print("\n" + "=" * 60)
    print("  STREAMLIT DASHBOARD — Level 1 Manual Confirmation")
    print("=" * 60)
    print()
    print("  The signal engine is running in the background.")
    print("  To view and confirm signals, open a NEW terminal and run:")
    print()
    print("      streamlit run paper_trading/dashboard.py")
    print()
    print("  The dashboard will show:")
    print("    • MT5 connection status and account info")
    print("    • Pending signals with Send Order / Skip buttons")
    print("    • Open positions with P/L in R")
    print("    • Performance metrics per asset (PF, CI95, breakeven)")
    print("    • Kill-switch status per asset")
    print("    • Emergency stop button (same as Ctrl+C here)")
    print()
    print("  Press Ctrl+C in THIS terminal to stop the signal engine")
    print("  gracefully (open positions will NOT be closed).")
    print("=" * 60)
    print()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """Main entry point for the paper trading system.

    Returns:
        Exit code (0 = success, 1 = error).
    """
    parser = argparse.ArgumentParser(
        description="Paper Trading System — Liquidity Sweep V2",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(_HERE / "config.yaml"),
        help="Path to YAML configuration file (default: config.yaml in paper_trading/)",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip demo account confirmation (use only for known demo accounts)",
    )
    args = parser.parse_args()

    # Resolve config path
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = str((_HERE / config_path).resolve())

    # ------------------------------------------------------------------
    # 1. Load configuration
    # ------------------------------------------------------------------
    try:
        config = load_config(config_path)
    except (FileNotFoundError, ValueError, yaml.YAMLError) as exc:
        logger.error("Failed to load configuration: %s", exc)
        print(f"Error: Cannot load configuration from '{config_path}'.")
        print(f"  {exc}")
        print("\nUsage: python run_trading.py --config <path_to_yaml>")
        return 1

    # ------------------------------------------------------------------
    # 2. Initialize core components
    # ------------------------------------------------------------------
    try:
        db_path, execution, risk_guard = init_system(config)
    except Exception as exc:
        logger.error("System initialisation failed: %s", exc)
        print(f"Error: System initialisation failed: {exc}")
        return 1

    # ------------------------------------------------------------------
    # 3. Connect MCP and confirm demo account
    # ------------------------------------------------------------------
    if not connect_and_confirm_demo(execution, args):
        logger.warning("Demo account confirmation failed — aborting.")
        return 1

    # ------------------------------------------------------------------
    # 4. Create Signal Engine Bridge (lazy load)
    # ------------------------------------------------------------------
    signal_bridge = SignalEngineBridge(config)

    # ------------------------------------------------------------------
    # 5. Create and start polling engine
    # ------------------------------------------------------------------
    global _engine_ref
    engine = SignalPollingEngine(
        execution=execution,
        risk_guard=risk_guard,
        signal_bridge=signal_bridge,
        config=config,
    )
    _engine_ref = engine

    # Register signal handlers (after engine is created)
    signal.signal(signal.SIGINT, _signal_handler_sigint)
    signal.signal(signal.SIGTERM, _signal_handler_sigterm)

    # ------------------------------------------------------------------
    # 6. Print dashboard instructions
    # ------------------------------------------------------------------
    print_dashboard_instructions()

    # ------------------------------------------------------------------
    # 7. Start polling (blocks until stop)
    # ------------------------------------------------------------------
    try:
        engine.start()
    except KeyboardInterrupt:
        # Already handled by signal handler, but just in case
        engine.stop()
    except Exception as exc:
        logger.error("Fatal error during signal polling: %s", exc)
        engine.stop()
        return 1

    # ------------------------------------------------------------------
    # 8. Summary on shutdown
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("  SIGNAL ENGINE STOPPED")
    print("=" * 60)
    print(f"  Total loops:         {engine._loop_count}")
    print(f"  Signals emitted:     {engine._signals_emitted}")
    print(f"  Emergency stop was:  {'ACTIVE' if engine.state.emergency_stop else 'inactive'}")
    print()
    print("  Open positions have NOT been closed (manual review required).")
    print("  The dashboard may still be running — 'streamlit stop' to kill it.")
    print("=" * 60)
    print()

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
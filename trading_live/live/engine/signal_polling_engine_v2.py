"""
signal_polling_engine.py — SignalPollingEngine V2

Orchestrates the per-symbol signal engine loop:
  1. On every tick, scan registered (validated) symbols for new bars
  2. Route candidates through RiskGuard (kill-switch check, position limits)
  3. Add pending signals to SharedAppState for the GUI to display
  4. For automation levels 1/2, auto-dispatch orders after confirming

Thread-safe: runs in its own daemon thread, updates shared state via
the thread-safe SharedAppState singleton.

Integration wiring:
  - signal_engine_v2.create_symbol_engine() per symbol
  - risk_guard_v2.RiskGuard.evaluate_new_signal()
  - execution_layer_v2.ExecutionLayer.send_order()
  - logger_v2.log_user_action() for audit trail
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from live.state.shared_app_state_v2 import (
    SharedAppState,
    PendingSignal,
    AutomationLevel,
    get_state,
)
from live.logging.logger_v2 import log_user_action, get_recent_trades
from live.engine.signal_engine_v2 import (
    create_symbol_engine,
    SignalCandidate,
    SymbolNotValidatedError,
    SymbolConfigNotFoundError,
    ModelArtifactNotFoundError,
)

logger = logging.getLogger("signal_polling_engine")


class SignalPollingEngine:
    """Background polling engine for multi-symbol signal generation.

    Owns one signal-engine closure per validated symbol and runs
    a 30-second polling loop that:
      1. Calls each engine to generate signal candidates
      2. Pipes them through RiskGuard
      3. Adds accepted candidates to shared state as PendingSignal
      4. At automation level 2 (full-auto), dispatches immediately

    Thread-safe: runs its own daemon thread.
    """

    def __init__(
        self,
        state: SharedAppState,
        execution_layer: Any,  # ExecutionLayer instance
        risk_guard: Any,       # RiskGuard instance
        polling_interval_s: int = 30,
    ) -> None:
        self._state = state
        self._exec_layer = execution_layer
        self._risk_guard = risk_guard
        self._polling_interval_s = polling_interval_s

        # Per-symbol engine registry: symbol -> callable
        self._engines: Dict[str, Callable[[], List[SignalCandidate]]] = {}

        # Thread management
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._mcp_client = getattr(execution_layer, "_client", None)

    # ------------------------------------------------------------------
    # Engine lifecycle
    # ------------------------------------------------------------------

    def register_symbol_engine(self, symbol: str) -> bool:
        """Create and register a signal engine for *symbol*.

        Args:
            symbol: Instrument name (e.g. "XAUUSD").

        Returns:
            True if the engine was created successfully.
        """
        try:
            engine = create_symbol_engine(
                symbol, mcp_client=self._exec_layer,
            )
            with self._lock:
                self._engines[symbol] = engine
            logger.info("Registered signal engine for %s", symbol)
            _log_audit("register_symbol_engine", {"symbol": symbol, "status": "ok"})
            return True
        except (
            SymbolNotValidatedError,
            SymbolConfigNotFoundError,
            ModelArtifactNotFoundError,
        ) as e:
            logger.warning("Cannot create engine for %s: %s", symbol, e)
            _log_audit("register_symbol_engine", {"symbol": symbol, "error": str(e)})
            return False
        except Exception as e:
            logger.error("Unexpected error creating engine for %s: %s", symbol, e)
            return False

    def unregister_symbol_engine(self, symbol: str) -> bool:
        """Remove a symbol engine from the poll loop."""
        with self._lock:
            if symbol in self._engines:
                del self._engines[symbol]
                logger.info("Unregistered signal engine for %s", symbol)
                _log_audit("unregister_symbol_engine", {"symbol": symbol})
                return True
        return False

    def refresh_engines(self) -> None:
        """Sync engines with the current symbol registry.

        Validated symbols without an engine get one created.
        Engines for unregistered or non-validated symbols are removed.
        """
        symbols = self._state.get_registered_symbols()
        with self._lock:
            current = set(self._engines.keys())
            desired = set()

            for sym in symbols:
                cfg = self._state.get_symbol_config(sym)
                if cfg and cfg.status == "validated" and cfg.active:
                    desired.add(sym)

            # Remove stale
            for sym in current - desired:
                del self._engines[sym]
                logger.info("Removed stale engine for %s", sym)

            # Add new
            for sym in desired - current:
                self._register_engine_nolock(sym)

    def _register_engine_nolock(self, symbol: str) -> None:
        """Register engine with lock assumed held."""
        try:
            engine = create_symbol_engine(
                symbol, mcp_client=self._exec_layer,
            )
            self._engines[symbol] = engine
            logger.info("Registered signal engine for %s", symbol)
        except (
            SymbolNotValidatedError,
            SymbolConfigNotFoundError,
            ModelArtifactNotFoundError,
        ) as e:
            logger.warning("Cannot create engine for %s: %s", symbol, e)

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background polling thread (daemon)."""
        if self._running:
            logger.warning("SignalPollingEngine already running")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True,
            name="signal-polling-engine",
        )
        self._thread.start()
        logger.info("SignalPollingEngine started (interval=%ds)", self._polling_interval_s)

    def stop(self) -> None:
        """Signal the polling thread to stop."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("SignalPollingEngine stopped")

    def _poll_loop(self) -> None:
        """Main polling loop — runs until *running* is False."""
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                logger.error("Poll cycle error: %s", e, exc_info=True)
            time.sleep(self._polling_interval_s)

    def _poll_once(self) -> None:
        """One poll cycle: scan engines, route through risk guard, add pending."""
        if self._state.emergency_stop:
            logger.debug("Emergency stop active — skipping poll cycle")
            return

        with self._lock:
            engines = dict(self._engines)  # snapshot

        if not engines:
            return

        for symbol, engine_fn in engines.items():
            try:
                candidates = engine_fn()
            except Exception as e:
                logger.error("Engine error for %s: %s", symbol, e)
                # Auto-reconnect on connection errors
                if "connection" in str(e).lower() or "mcp" in str(e).lower():
                    try:
                        self._exec_layer._ensure_session()
                    except Exception:
                        pass
                continue

            if not candidates:
                continue

            for cand in candidates:
                self._process_candidate(cand)

    def _process_candidate(self, cand: SignalCandidate) -> None:
        """Route one signal candidate through risk guard and add to pending."""
        # 1. Build position dict for risk guard check
        current_positions = [
            {
                "asset": p.asset,
                "direction": p.direction,
                "entry_price": p.entry_price,
                "current_price": p.current_price,
                "position_size": p.position_size,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "open_time": p.open_time,
                "position_id": p.position_id,
            }
            for p in self._state.open_positions
        ]

        # 2. Risk guard check
        rg_result = self._risk_guard.evaluate_new_signal(
            asset=cand.symbol,
            current_open_positions=current_positions,
        )
        if not rg_result.get("allowed", False):
            logger.info(
                "Signal for %s rejected by RiskGuard: %s",
                cand.symbol, rg_result.get("reason", ""),
            )
            return

        # 3. Create PendingSignal
        signal_id = str(uuid.uuid4())
        pending = PendingSignal(
            asset=cand.symbol,
            direction=cand.direction,
            entry_price=cand.entry_price,
            stop_loss=cand.stop_price,
            take_profit=cand.target_price,
            rule_score=cand.rule_score,
            model_probability=cand.model_prob,
            timestamp=time.time(),
            signal_id=signal_id,
        )

        # 4. Add to shared state
        self._state.set_pending_signals(
            self._state.pending_signals + [pending]
        )
        logger.info(
            "Pending signal added: %s %s @ %.5f (signal_id=%s)",
            cand.symbol, cand.direction, cand.entry_price, signal_id,
        )
        _log_audit("pending_signal_generated", {
            "signal_id": signal_id,
            "symbol": cand.symbol,
            "direction": cand.direction,
            "entry_price": cand.entry_price,
            "rule_score": cand.rule_score,
            "model_prob": cand.model_prob,
        })

        # 5. Auto-send for automation level >= 1 (semi-auto / full-auto)
        auto_level = self._state.automation_levels.get(cand.symbol, AutomationLevel(level=0)).level
        if auto_level >= 1:
            self._auto_send_order(signal_id, cand, auto_level)

    def _auto_send_order(self, signal_id: str, cand: SignalCandidate, level: int) -> None:
        """Auto-send order for automation level >= 1.

        Level 1 (semi-auto): short delay to allow user rejection
        Level 2 (full-auto): immediate dispatch
        """
        if level == 1:
            # Short delay — user can still reject via GUI
            time.sleep(3)

        # Check if signal was still pending (not rejected by user)
        remaining = [s for s in self._state.pending_signals if s.signal_id == signal_id]
        if not remaining:
            return

        logger.info(
            "Auto-sending order for %s %s (level=%d, signal_id=%s)",
            cand.symbol, cand.direction, level, signal_id,
        )

        try:
            result = self._exec_layer.send_order(
                asset=cand.symbol,
                direction=cand.direction,
                entry_price=cand.entry_price,
                stop_loss=cand.stop_price,
                take_profit=cand.target_price,
                signal_id=signal_id,
                rule_score=cand.rule_score,
                model_probability=cand.model_prob,
                automation_level=level,
                kill_switch_blocking=False,
            )
            if result.get("success", False):
                self._state.remove_pending_signal(signal_id)
                self._risk_guard.record_order_sent(cand.symbol)
                logger.info(
                    "Auto-order sent: %s %s (order_id=%s)",
                    cand.symbol, cand.direction,
                    result.get("order_id", "?"),
                )
                _log_audit("auto_order_sent", {
                    "signal_id": signal_id,
                    "symbol": cand.symbol,
                    "direction": cand.direction,
                    "level": level,
                    "success": True,
                })
            else:
                logger.warning("Auto-order failed: %s", result.get("error", "unknown"))
                _log_audit("auto_order_failed", {
                    "signal_id": signal_id,
                    "symbol": cand.symbol,
                    "error": result.get("error", "unknown"),
                })
        except Exception as e:
            logger.error("Auto-order exception: %s", e)
            _log_audit("auto_order_exception", {"signal_id": signal_id, "error": str(e)})

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def registered_symbols(self) -> List[str]:
        with self._lock:
            return list(self._engines.keys())

    @property
    def engine_count(self) -> int:
        with self._lock:
            return len(self._engines)


def _log_audit(action_type: str, details: Dict[str, Any]) -> None:
    """Log audit trail — silently ignore on failure."""
    try:
        log_user_action(action_type, json.dumps(details, default=str))
    except Exception:
        pass
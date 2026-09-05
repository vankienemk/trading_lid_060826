"""
gui_bridge.py — SystemBridge: GUI ↔ Engine module adapter.

Provides a clean API for GUI tabs to interact with the trading engine
(shared_app_state_v2, logger_v2, risk_guard_v2, execution_layer_v2)
without circular imports or direct module coupling.

Key responsibilities:
  1. MCP connection lifecycle with auto-reconnect
  2. Signal engine polling dispatch
  3. Order execution via execution layer
  4. Performance metrics aggregation across symbols
  5. Emergency stop / kill-switch management
  6. **Model Registry API** — load, query, and assign models to symbols
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from paper_trading_v2.shared_app_state_v2 import (
    SharedAppState,
    PendingSignal,
    KillSwitchStatus,
    AutomationLevel,
    SymbolConfig,
    ModelInfo,
    ModelRegistry,
    get_model_registry,
)
from paper_trading_v2.logger_v2 import (
    init_db,
    log_signal,
    update_order_result,
    log_user_action,
    get_recent_trades,
    get_user_actions,
)


class SystemBridge:
    """Singleton bridge between GUI and engine modules.

    The GUI creates one SystemBridge instance at startup and passes it
    to every tab. The bridge owns the MCP execution layer instance,
    the timer polling loop, and provides safe thread-pool dispatch.
    """

    _instance: Optional["SystemBridge"] = None
    _instance_lock = threading.Lock()

    def __init__(self, config_path: Optional[str] = None) -> None:
        self.state = SharedAppState.get_instance()
        self.config_path = config_path or ""
        self._running = False
        self._poll_thread: Optional[threading.Thread] = None
        self._mcp_lock = threading.Lock()

        # -------------------------------
        # Model Registry — load from YAML index
        # -------------------------------
        self._model_registry = get_model_registry()
        self._model_registry.load_from_yaml()

        # -------------------------------
        # Load symbol registry from persisted JSON (if exists)
        # Otherwise fall back to YAML seed configs
        # -------------------------------
        n_persisted = self.state.load_registry()
        if n_persisted == 0:
            # No persisted file — load from YAML seed configs
            self._load_symbol_configs()
            # Immediately persist so the user's first GUI save creates the file
            self.state.save_registry()

        # -------------------------------
        # MCP Execution Layer instance
        # -------------------------------
        # We import here to avoid circular import at module level.
        # The execution_layer_v2 module is created by t4; we provide
        # a mock fallback when it is not yet available.
        self._exec_layer: Any = None
        self._init_exec_layer()

        # -------------------------------
        # DB init
        # -------------------------------
        db_path = str(
            Path(__file__).resolve().parent / "paper_trading_v2.db"
        )
        init_db(db_path)
        self.state.set_mcp_connection_state("disconnected")

    @classmethod
    def get_instance(cls, config_path: Optional[str] = None) -> "SystemBridge":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(config_path)
        return cls._instance

    # ------------------------------------------------------------------
    # Symbol config loader (from configs/symbols/*.yaml)
    # ------------------------------------------------------------------

    def _load_symbol_configs(self) -> None:
        """Load validated symbols from configs/symbols/*.yaml into the registry.

        Only runs for symbols NOT already in the registry (user may have
        removed or changed status via GUI). MCP-discovered symbols remain
        in the registry at their existing status (never downgraded by MCP poll).
        """
        configs_dir = Path(__file__).resolve().parent.parent / "configs" / "symbols"
        if not configs_dir.is_dir():
            return
        for yaml_file in sorted(configs_dir.glob("*.yaml")):
            if yaml_file.name == "_template.yaml":
                continue
            try:
                import yaml
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not isinstance(data, dict):
                    continue
                sym_block = data.get("symbol", {})
                if isinstance(sym_block, dict):
                    name = sym_block.get("name", "")
                    status = sym_block.get("status", "pending")
                else:
                    name = data.get("symbol_name", "")
                    status = data.get("status", "pending")
                if not name:
                    continue
                active = data.get("symbol", {}).get("is_active", True) if isinstance(data.get("symbol"), dict) else data.get("is_active", True)
                # Only register validated symbols from YAML
                if status == "validated":
                    # Critical: only register if symbol is NOT already in registry
                    # (user may have removed or changed status via GUI at runtime)
                    cfg = self.state.get_symbol_config(name)
                    if cfg is None:
                        self.state.register_symbol(
                            name,
                            SymbolConfig(name=name, status="validated", active=bool(active)),
                        )
            except Exception:
                continue

    # ------------------------------------------------------------------
    # MCP Execution Layer
    # ------------------------------------------------------------------

    def _init_exec_layer(self) -> None:
        """Try to import and instantiate execution_layer_v2."""
        try:
            # Lazy import — may not exist if module is not built yet
            from paper_trading_v2.execution_layer_v2 import ExecutionLayer

            endpoint = "http://127.0.0.1:22346/mcp"
            token = self.state.mcp_token or os.environ.get("MCP_TOKEN", "")

            self._exec_layer = ExecutionLayer(
                endpoint=endpoint,
                token=token,
                auto_reconnect=True,
            )
        except ImportError:
            import traceback
            _log("_init_exec_layer", {"status": "import_failed", "error": traceback.format_exc()})
            self._exec_layer = None

    def connect_mcp(self, token: str = "") -> bool:
        """Connect to MCP with the given token (or current token).

        Returns True on success, False on failure.
        """
        if token:
            self.state.set_mcp_token(token)

        with self._mcp_lock:
            if self._exec_layer is None:
                self._init_exec_layer()
                if self._exec_layer is None:
                    self.state.set_mcp_connection_state("disconnected")
                    return False
            else:
                # Always sync the latest token from state into exec_layer
                latest_token = self.state.mcp_token or os.environ.get("MCP_TOKEN", "")
                if latest_token and self._exec_layer.token != latest_token:
                    self._exec_layer.set_token(latest_token)

            try:
                self.state.set_mcp_connection_state("reconnecting")
                # Attempt health check
                health = self._exec_layer.health_check()
                if health.get("ok"):
                    self.state.set_mcp_connection_state("connected")
                    self.state.update_mt5_status(connected=True)

                    acct = self._exec_layer.get_account_info()
                    acct_type = acct.get("account_type", acct.get("type", "unknown"))
                    self.state.update_mt5_status(
                        connected=True,
                        account_type="demo" if "demo" in str(acct_type).lower() else "real",
                        account_info=acct,
                    )

                    _log("connect_mcp", {"status": "connected"})
                    return True
                else:
                    self.state.set_mcp_connection_state("disconnected")
                    self.state.update_mt5_status(connected=False)
                    _log("connect_mcp", {"status": "health_check_failed", "health": health})
                    return False
            except Exception as e:
                self.state.set_mcp_connection_state("disconnected")
                self.state.update_mt5_status(connected=False)
                _log("connect_mcp", {"status": "error", "error": str(e)})
                return False

    def disconnect_mcp(self) -> None:
        """Disconnect from MCP."""
        self.state.set_mcp_connection_state("disconnected")
        self.state.update_mt5_status(connected=False)
        _log("disconnect_mcp", {})

    def reconnect_mcp(self) -> bool:
        """Force reconnection with existing token and auto-reconnect logic."""
        return self.connect_mcp(self.state.mcp_token)

    def fetch_positions(self) -> List[Dict[str, Any]]:
        """Fetch open positions from MCP. Reconnects on failure."""
        if self._exec_layer is None:
            return []
        try:
            return self._exec_layer.get_positions()
        except Exception:
            self.reconnect_mcp()
            return []

    def fetch_symbols(self) -> List[str]:
        """Fetch market watch symbols from MCP as a flat string list."""
        if self._exec_layer is None:
            return []
        try:
            raw = self._exec_layer.get_symbols()  # List[Dict] w/ 'symbol' key
            result: List[str] = []
            for s in raw:
                if isinstance(s, str):
                    result.append(s)
                elif isinstance(s, dict):
                    result.append(s.get("symbol", s.get("name", "")))
            return [r for r in result if r]
        except Exception:
            return []

    def send_order(
        self, asset: str, direction: str,
        entry_price: float, stop_loss: float, take_profit: float,
        lot_size: float = 0.01,
    ) -> Dict[str, Any]:
        """Send a market order via MCP execution layer.

        Returns a dict with at minimum {"ok": bool, "order_id": str}.
        """
        if self._exec_layer is None:
            return {"ok": False, "error": "Execution layer not initialized"}

        try:
            with self._mcp_lock:
                result = self._exec_layer.place_order(
                    symbol=asset,
                    order_type="buy" if direction.lower() == "buy" else "sell",
                    volume=lot_size,
                    price=entry_price,
                    sl=stop_loss,
                    tp=take_profit,
                )
            _log("send_order", {"asset": asset, "direction": direction, "result": str(result)})
            return result
        except Exception as e:
            _log("send_order", {"asset": asset, "error": str(e)})
            return {"ok": False, "error": str(e)}

    def close_position(self, position_id: str) -> Dict[str, Any]:
        """Close a specific position by ID."""
        if self._exec_layer is None:
            return {"ok": False, "error": "Execution layer not initialized"}
        try:
            result = self._exec_layer.close_position(position_id)
            _log("close_position", {"position_id": position_id, "result": str(result)})
            return result
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Polling / Refresh
    # ------------------------------------------------------------------

    def refresh_state_from_mcp(self) -> Dict[str, Any]:
        """Fetch latest data from MCP and update shared state.

        Returns the snapshot dict.
        """
        if self._exec_layer is None:
            return self.state.get_snapshot()

        try:
            # Health check
            health = self._exec_layer.health_check()
            if health.get("ok"):
                self.state.set_mcp_connection_state("connected")
                self.state.update_mt5_status(connected=True)

                # Account info
                acct = self._exec_layer.get_account_info()
                acct_type = acct.get("account_type", acct.get("type", "unknown"))
                self.state.update_mt5_status(
                    connected=True,
                    account_type="demo" if "demo" in str(acct_type).lower() else "real",
                    account_info=acct,
                )

                # Positions
                positions = self._exec_layer.get_positions()
                self.state.set_open_positions(positions)

                # Symbols — only register if not already in registry,
                # preserving existing status (e.g. validated from YAML config)
                symbols = self._exec_layer.get_symbols()
                for s in symbols:
                    name = s if isinstance(s, str) else s.get("name", "")
                    if name and name not in self.state.symbol_registry:
                        self.state.register_symbol(name, SymbolConfig(name=name, status="pending", active=False))
            else:
                self.state.set_mcp_connection_state("disconnected")
                self.state.update_mt5_status(connected=False)

        except Exception:
            self.state.set_mcp_connection_state("disconnected")
            self.state.update_mt5_status(connected=False)

        return self.state.get_snapshot()

    # ------------------------------------------------------------------
    # Signal / Risk / Kill-Switch
    # ------------------------------------------------------------------

    def check_kill_switch(self, asset: str, trades: List[Dict]) -> Dict[str, Any]:
        """Evaluate kill-switch for asset using block bootstrap via risk_guard_v2.

        Returns dict with keys: active, reason, pf_ci_lower.
        """
        try:
            from paper_trading_v2.risk_guard_v2 import RiskGuard
            guard = RiskGuard()
            result = guard.evaluate(asset, trades)
            return result
        except ImportError:
            return {"active": False, "reason": "RiskGuard not available", "pf_ci_lower": 0.0}

    def log_action(self, action_type: str, details: Optional[Dict] = None) -> None:
        """Log a GUI user action to the database."""
        _log(action_type, details or {})

    # ------------------------------------------------------------------
    # Model Registry API
    # ------------------------------------------------------------------
    # These methods provide the Model Registry integration required by
    # GUI_REWORK_REQUIREMENTS.md Sections 2 and 3.

    def get_available_models(self) -> List[ModelInfo]:
        """Return all registered models from the Model Registry.

        Returns:
            Sorted list of ModelInfo dataclasses (empty if registry not loaded).
        """
        return self._model_registry.get_available_models()

    def get_model(self, model_id: str) -> Optional[ModelInfo]:
        """Look up a single model by its model_id.

        Returns:
            ModelInfo if found, None otherwise.
        """
        return self._model_registry.get_model(model_id)

    def assign_model_to_symbol(self, symbol: str, model_id: str) -> bool:
        """Assign a model to an already-registered symbol.

        The symbol must already exist in the registry.  This changes only
        the ``model_id`` field — it does **not** alter status or active flag.

        Args:
            symbol: Symbol name (e.g. "XAUUSD").
            model_id: Valid model_id from the Model Registry.

        Returns:
            True on success, False if symbol or model_id is invalid.
        """
        if not self._model_registry.get_model(model_id):
            _log("assign_model_to_symbol", {"symbol": symbol, "model_id": model_id, "error": "unknown model"})
            return False

        cfg = self.state.get_symbol_config(symbol)
        if cfg is None:
            _log("assign_model_to_symbol", {"symbol": symbol, "model_id": model_id, "error": "unknown symbol"})
            return False

        self.state.register_symbol(
            symbol,
            SymbolConfig(
                name=cfg.name,
                status=cfg.status,
                active=cfg.active,
                model_id=model_id,
                position_size_multiplier=cfg.position_size_multiplier,
            ),
        )
        self.state.save_registry()
        _log("assign_model_to_symbol", {"symbol": symbol, "model_id": model_id})
        return True

    def add_symbol_with_model(
        self,
        symbol: str,
        model_id: str,
        activate: bool = True,
    ) -> bool:
        """Register a new symbol and assign a model in one call.

        Creates a ``SymbolConfig(status="validated", active=activate, model_id=...)``
        and registers it in the shared state.  The symbol must not already
        be registered.

        Args:
            symbol: Symbol name (e.g. "XAUUSD").
            model_id: Valid model_id from the Model Registry.
            activate: Whether to set ``active=True`` (default True).

        Returns:
            True on success, False if already registered or model unknown.
        """
        if not self._model_registry.get_model(model_id):
            _log("add_symbol_with_model", {"symbol": symbol, "model_id": model_id, "error": "unknown model"})
            return False

        if self.state.get_symbol_config(symbol) is not None:
            _log("add_symbol_with_model", {"symbol": symbol, "model_id": model_id, "error": "already registered"})
            return False

        config = SymbolConfig(
            name=symbol,
            status="validated",
            active=activate,
            model_id=model_id,
        )
        self.state.register_symbol(symbol, config)
        self.state.save_registry()
        _log("add_symbol_with_model", {"symbol": symbol, "model_id": model_id, "activate": activate})
        return True

    def reload_model_registry(self) -> int:
        """Reload the Model Registry from disk (re-reads YAML).

        Returns:
            Number of models loaded.
        """
        n = self._model_registry.reload()
        _log("reload_model_registry", {"models_loaded": n})
        return n

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Clean shutdown: close MCP connection, stop polling."""
        self._running = False
        if self._exec_layer is not None:
            try:
                self._exec_layer.close()
            except Exception:
                pass
        self.state.set_mcp_connection_state("disconnected")
        _log("shutdown", {})


def _log(action_type: str, details: Dict[str, Any]) -> None:
    """Internal helper to log a user action with JSON details."""
    try:
        details_json = json.dumps(details, default=str) if details else None
        log_user_action(action_type, details_json)
    except Exception:
        pass  # Silently fail — logging should never crash the GUI


# Module-level convenience accessor
_global_bridge: Optional[SystemBridge] = None


def get_bridge(config_path: Optional[str] = None) -> SystemBridge:
    """Get the global SystemBridge singleton."""
    global _global_bridge
    if _global_bridge is None:
        _global_bridge = SystemBridge.get_instance(config_path)
    return _global_bridge
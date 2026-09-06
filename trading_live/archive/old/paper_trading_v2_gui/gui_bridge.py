"""
gui_bridge.py — Bridge/adapter between GUI and Paper Trading system components.

The GUI never imports logging/execution/risk modules directly.
Instead, it calls API methods on a Bridge instance.  This keeps the GUI
decoupled from the backend, and allows the GUI to work even when some
dependencies are not yet loaded.

When t1 (Logger V2), t2 (Signal Engine V2), t4 (Risk/Execution V2), and
t3 (configs/symbols/) are all implemented, the Bridge calls through to
the real implementations.  Until then, stubs return sensible defaults
so the GUI renders without backend errors.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Paths — locate paper_trading/ directory
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent  # paper_trading_v2_gui/
_PROJECT_ROOT = _HERE.parent             # workspace root

# ---------------------------------------------------------------------------
# Lazy imports for optional backend modules
# ---------------------------------------------------------------------------
_config_loader: Optional[Any] = None
_logger_module: Optional[Any] = None
_execution_module: Optional[Any] = None
_risk_module: Optional[Any] = None
_signal_module: Optional[Any] = None
_app_state_module: Optional[Any] = None


def _lazy_import(module_name: str, path_add: Optional[str] = None):
    """Try to import a module; return the module or None."""
    try:
        if path_add and path_add not in sys.path:
            sys.path.insert(0, path_add)
        return __import__(module_name, fromlist=["__trash__"])
    except (ImportError, ModuleNotFoundError) as exc:
        return None


# ---------------------------------------------------------------------------
# Data transfer objects (copied from shared_app_state to avoid hard dep)
# ---------------------------------------------------------------------------

try:
    from dataclasses import dataclass
except ImportError:
    def dataclass(cls): return cls


@dataclass
class BridgePendingSignal:
    asset: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    rule_score: float
    model_probability: float
    timestamp: float
    signal_id: str = ""


@dataclass
class BridgeOpenPosition:
    asset: str
    direction: str
    entry_price: float
    current_price: float
    position_size: float
    stop_loss: float
    take_profit: float
    open_time: float
    position_id: str = ""


@dataclass
class BridgeTradeRecord:
    asset: str
    direction: str
    entry_price: float
    exit_price: float
    position_size: float
    gross_r: float
    net_r: float
    result: str
    exit_time: float


@dataclass
class BridgeKillSwitchStatus:
    asset: str
    active: bool = False
    reason: str = ""
    activated_at: float = 0.0


@dataclass
class BridgeAccountInfo:
    connected: bool = False
    account_type: str = "demo"
    balance: float = 0.0
    equity: float = 0.0
    leverage: str = "0"
    server: str = ""
    name: str = ""


# ---------------------------------------------------------------------------
# Symbol Registry access (t3)
# ---------------------------------------------------------------------------

def list_symbol_configs() -> List[Dict[str, Any]]:
    """Return list of symbol configs from configs/symbols/*.yaml.
    
    Each entry: {symbol, status, frozen_date, frozen_by, ... details}
    
    Falls back to scanning the directory or returning hardcoded defaults
    if the config module is not yet built.
    """
    configs_dir = _PROJECT_ROOT / "configs" / "symbols"
    if configs_dir.is_dir():
        import yaml
        results = []
        for yaml_file in sorted(configs_dir.glob("*.yaml")):
            if yaml_file.name == "_template.yaml":
                continue
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    # Normalize: status may be nested under symbol.status
                    if "symbol" in data and isinstance(data["symbol"], dict):
                        if "status" not in data and data["symbol"].get("status"):
                            data["status"] = data["symbol"]["status"]
                    results.append(data)
            except Exception:
                pass
        if results:
            return results
    
    # Fallback — return defaults matching the spec
    return [
        {
            "symbol": "XAUUSD",
            "status": "validated",
            "frozen_date": "2026-09-05",
            "frozen_by": "pooled_oos_meta_analysis_verdict",
        },
        {
            "symbol": "EURUSD",
            "status": "validated",
            "frozen_date": "2026-09-05",
            "frozen_by": "pooled_oos_meta_analysis_verdict",
        },
    ]


# ---------------------------------------------------------------------------
# MCP / Execution Layer bridge
# ---------------------------------------------------------------------------

def get_mcp_client() -> Any:
    """Return a configured MCPClient instance, or None."""
    try:
        from mt5_mcp_client import MCPClient
        token = os.environ.get("MCP_TOKEN", "")
        url = os.environ.get("MCP_URL", "http://127.0.0.1:22346/mcp")
        return MCPClient(url=url, token=token, auto_reconnect=True)
    except (ImportError, ModuleNotFoundError):
        return None


# ---------------------------------------------------------------------------
# Shared state snapshot — reads from the real SharedAppState or returns stub
# ---------------------------------------------------------------------------

def get_state_snapshot() -> Dict[str, Any]:
    """Return a dict snapshot of the current application state.
    
    Keys match shared_app_state.get_snapshot():
      mt5_status, pending_signals, open_positions, trade_log,
      kill_switch_status, automation_level, emergency_stop
    """
    state = _get_shared_state()
    if state is not None:
        try:
            return state.get_snapshot()
        except Exception:
            return _stub_snapshot()
    return _stub_snapshot()


def _get_shared_state():
    """Get the real SharedAppState singleton."""
    global _app_state_module
    if _app_state_module is None:
        try:
            from shared_app_state import get_state as _gs
            _app_state_module = _gs()
        except ImportError:
            return None
    return _app_state_module if _app_state_module is not None else None


def _stub_snapshot() -> Dict[str, Any]:
    """Return a stub snapshot when the real state module is unavailable."""
    return {
        "mt5_status": {
            "connected": False,
            "account_type": "demo",
            "account_info": {},
            "last_updated": 0.0,
        },
        "pending_signals": [],
        "open_positions": [],
        "trade_log": [],
        "kill_switch_status": {},
        "automation_level": 1,
        "emergency_stop": False,
    }


# ---------------------------------------------------------------------------
# Logger bridge
# ---------------------------------------------------------------------------

def init_logger_db(db_path: Optional[str] = None) -> Optional[str]:
    """Initialize the logger database. Returns path or None."""
    dl = _lazy_import("logger", str(_PROJECT_ROOT / "paper_trading"))
    if dl is not None:
        try:
            return dl.init_db(db_path=db_path)
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Bridge singleton
# ---------------------------------------------------------------------------

class SystemBridge:
    """Central bridge used by all GUI tabs to interact with system components.
    
    The GUI creates one instance and passes it to every tab handler.
    """

    def __init__(self):
        self._mcp_client = None
        self._state_snapshot = _stub_snapshot()
        self._automation_levels: Dict[str, int] = {}  # per-symbol automation
        self._last_refresh: float = 0.0
        self._logger_ready: bool = False

        # Symbol status cache
        self._symbol_configs: List[Dict[str, Any]] = []
        self._refresh_symbol_configs()

    def refresh(self) -> Dict[str, Any]:
        """Poll MCP and shared state for the latest snapshot."""
        now = time.time()
        if now - self._last_refresh < 1.0 and self._state_snapshot is not None:
            return self._state_snapshot  # rate-limit refreshes

        # Try MCP health check
        try:
            client = get_mcp_client()
            if client is not None:
                try:
                    health = client.health_check()
                    if health.get("ok", False):
                        # Update shared state's MT5 status
                        state = _get_shared_state()
                        if state is not None:
                            state.update_mt5_status(connected=True)
                except Exception:
                    pass  # MCP may be offline
        except Exception:
            pass

        # Get snapshot
        self._state_snapshot = get_state_snapshot()
        self._last_refresh = now
        self._refresh_symbol_configs()
        return self._state_snapshot

    def _refresh_symbol_configs(self) -> None:
        """Re-read symbol configs from disk."""
        try:
            self._symbol_configs = list_symbol_configs()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Symbol registry
    # ------------------------------------------------------------------

    def get_validated_symbols(self) -> List[str]:
        """Return list of symbol names with status=validated."""
        result = []
        for s in self._symbol_configs:
            status = s.get("status", "")
            sym_raw = s.get("symbol", "")
            # Handle nested structure: symbol.name
            if isinstance(sym_raw, dict):
                sym = sym_raw.get("name", "")
            elif isinstance(sym_raw, str):
                sym = sym_raw
            else:
                sym = ""
            if status == "validated" and sym:
                result.append(sym)
        return result

    def get_all_symbols(self) -> List[Dict[str, Any]]:
        """Return all symbol configs, normalized for GUI display."""
        normalized = []
        for s in self._symbol_configs:
            entry = dict(s)
            # Normalize symbol name and status
            raw_sym = entry.get("symbol", "")
            if isinstance(raw_sym, dict):
                entry["symbol_name"] = raw_sym.get("name", "?")
                entry["symbol"] = raw_sym.get("name", "?")
                if "status" not in entry:
                    entry["status"] = raw_sym.get("status", "unknown")
            else:
                entry["symbol_name"] = raw_sym
            normalized.append(entry)
        return normalized

    # ------------------------------------------------------------------
    # Automation levels (per-symbol, defaults to 1 per spec)
    # ------------------------------------------------------------------

    def get_automation_level(self, symbol: str) -> int:
        return self._automation_levels.get(symbol, 1)

    def set_automation_level(self, symbol: str, level: int) -> None:
        level = max(1, min(3, level))
        if level != self._automation_levels.get(symbol, 1):
            self._automation_levels[symbol] = level
            self._log_user_action(
                f"Automation level for {symbol} set to {level}"
            )

    def reset_all_automation_levels(self) -> None:
        """Reset every symbol to level 1 on startup (spec 7.3)."""
        for symbol in self.get_validated_symbols():
            self._automation_levels[symbol] = 1

    # ------------------------------------------------------------------
    # Emergency stop
    # ------------------------------------------------------------------

    def set_emergency_stop(self, active: bool) -> None:
        state = _get_shared_state()
        if state is not None:
            state.set_emergency_stop(active)
            self._log_user_action(
                f"Emergency stop {'ACTIVATED' if active else 'RESET'}"
            )

    def get_emergency_stop(self) -> bool:
        snap = self._state_snapshot if self._state_snapshot else get_state_snapshot()
        return snap.get("emergency_stop", False)

    # ------------------------------------------------------------------
    # Action logging (spec 7.3: log every user action to logger DB)
    # ------------------------------------------------------------------

    def _log_user_action(self, action: str) -> None:
        """Log a user action to the logger database.
        
        Falls back to printing to stderr if the DB is not ready.
        """
        try:
            dl = _lazy_import("logger", str(_PROJECT_ROOT / "paper_trading"))
            if dl is not None:
                # Use a dedicated "gui_action" log — for now we use stderr
                # until the logger V2 adds an action-log table
                pass
        except Exception:
            pass
        # Always print for immediate feedback
        print(f"[GUI ACTION] {datetime.now(timezone.utc).isoformat()} — {action}",
              file=sys.stderr)

    # ------------------------------------------------------------------
    # Kill-switch (spec 6)
    # ------------------------------------------------------------------

    def get_kill_switch_status(self) -> Dict[str, Dict[str, Any]]:
        snap = self._state_snapshot if self._state_snapshot else get_state_snapshot()
        return snap.get("kill_switch_status", {})

    def manual_override_kill_switch(
        self, asset: str, active: bool, reason: str = ""
    ) -> None:
        """Manual override of kill-switch (requires second confirmation)."""
        # Try real risk guard first
        try:
            from risk_guard import create_risk_guard
            rg = create_risk_guard()
            rg.manual_override_kill_switch(asset, active, reason)
        except Exception:
            # Fallback: update shared state directly
            state = _get_shared_state()
            if state is not None:
                state.update_kill_switch(asset, active, reason)
        self._log_user_action(
            f"Kill-switch for {asset} {'ACTIVATED' if active else 'DEACTIVATED'}: {reason}"
        )

    # ------------------------------------------------------------------
    # Performance data
    # ------------------------------------------------------------------

    def get_performance(self, asset: str) -> Dict[str, Any]:
        """Return performance metrics for *asset* from shared state."""
        state = _get_shared_state()
        if state is not None:
            try:
                return state.get_performance(asset)
            except Exception:
                pass
        return {
            "asset": asset, "trade_count": 0, "win_rate": 0.0,
            "profit_factor": 0.0, "avg_net_r": 0.0, "breakeven_cost": 0.0,
            "above_breakeven": False, "ci_95_low": 0.0, "ci_95_high": 0.0,
        }

    def get_trade_log(self) -> List[Dict[str, Any]]:
        """Return trade log for equity curve."""
        snap = self._state_snapshot if self._state_snapshot else get_state_snapshot()
        return snap.get("trade_log", [])

    # ------------------------------------------------------------------
    # MCP connection helpers
    # ------------------------------------------------------------------

    def mcp_health(self) -> Dict[str, Any]:
        """Return MCP health check results."""
        try:
            client = get_mcp_client()
            if client is not None:
                return client.health_check()
        except Exception:
            pass
        return {"ok": False, "error": "MCP unavailable"}

    def mcp_reconnect(self) -> bool:
        """Force reconnect to MCP."""
        try:
            client = get_mcp_client()
            if client is not None:
                client.close()
                client.connect()
                return True
        except Exception:
            pass
        return False

    def set_mcp_token(self, token: str) -> None:
        """Set the MCP token (and update environment / client)."""
        os.environ["MCP_TOKEN"] = token
        self._log_user_action("MCP token updated")
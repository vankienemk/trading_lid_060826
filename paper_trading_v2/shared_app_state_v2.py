"""
shared_app_state_v2.py — Paper Trading V2 Shared Application State Module

Thread-safe singleton accessible by all components (signal engine, risk guard,
execution layer, GUI). Uses ``threading.RLock`` for re-entrant safe access.

Key differences from V1:
  1. Dynamic asset registry — no hardcoded XAUUSD/EURUSD. Assets are
     registered at runtime from ``configs/symbols/*.yaml``.
  2. ``mcp_connection_state`` — tracks MCP bridge status
     (``"connected"`` / ``"disconnected"`` / ``"reconnecting"``).
  3. ``mcp_token`` — settable via the GUI (replaces env-var-only approach).
  4. ``emergency_stop`` — stops ALL signal engines immediately.
  5. Snapshot includes every field the GUI needs.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SymbolConfig:
    """Runtime representation of a symbol's config from YAML."""
    name: str
    status: str                      # "validated", "candidate", "rejected"
    position_size_multiplier: float = 1.0
    active: bool = True


@dataclass
class MT5Status:
    """MT5 connection status."""
    connected: bool = False
    account_type: str = "demo"           # "demo" or "real"
    account_info: Dict[str, Any] = field(default_factory=dict)
    last_updated: float = 0.0


@dataclass
class PendingSignal:
    """A single pending signal from the Signal Engine (Level 1)."""
    asset: str
    direction: str                      # "buy" or "sell"
    entry_price: float
    stop_loss: float
    take_profit: float
    rule_score: float                   # 0.0 – 1.0
    model_probability: float            # 0.0 – 1.0
    timestamp: float                    # unix epoch
    signal_id: str = ""                 # unique id for UI buttons


@dataclass
class OpenPosition:
    """An open position being tracked."""
    asset: str
    direction: str
    entry_price: float
    current_price: float
    position_size: float                # lots
    stop_loss: float
    take_profit: float
    open_time: float
    position_id: str = ""


@dataclass
class TradeRecord:
    """Completed trade record (for performance calculation)."""
    asset: str
    direction: str
    entry_price: float
    exit_price: float
    position_size: float
    gross_r: float                      # realized R multiple (before costs)
    net_r: float                        # gross_r minus breakeven cost
    result: str                         # "win", "loss", "breakeven"
    exit_time: float


@dataclass
class KillSwitchStatus:
    """Per-asset kill-switch status."""
    asset: str
    active: bool = False
    reason: str = ""                    # empty if not active
    activated_at: float = 0.0


@dataclass
class AutomationLevel:
    """Current automation level per asset (0, 1, or 2).

    - 0 = manual (user must click Send for every signal)
    - 1 = semi-auto (signal appears in pending, auto-send after configurable
          delay if no user rejection within that window)
    - 2 = full-auto (send order immediately after risk guard passes)
    """
    level: int = 0                      # default 0 = manual on every startup


# ---------------------------------------------------------------------------
# Shared state singleton
# ---------------------------------------------------------------------------

class SharedAppState:
    """
    Thread-safe shared application state. Every component reads/writes through
    this class. Use get_instance() to access the singleton.

    Uses ``threading.RLock`` for re-entrant safe access so that nested method
    calls within the same thread do not deadlock.
    """

    _instance: Optional["SharedAppState"] = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._state_lock = threading.RLock()

        # --- Asset registry (dynamic — NOT hardcoded) ---
        # Dictionary keyed by symbol name (e.g. "XAUUSD").
        # Each value is a SymbolConfig dataclass.
        self.symbol_registry: Dict[str, SymbolConfig] = {}

        # --- Connection ---
        self.mt5_status: MT5Status = MT5Status()

        # --- MCP connection state ---
        # "connected" | "disconnected" | "reconnecting"
        self.mcp_connection_state: str = "disconnected"
        self.mcp_token: str = ""            # settable via GUI

        # --- Signals ---
        self.pending_signals: List[PendingSignal] = []

        # --- Open positions ---
        self.open_positions: List[OpenPosition] = []

        # --- Trade log (completed trades) ---
        self.trade_log: List[TradeRecord] = []

        # --- Kill-switch ---
        self.kill_switch_status: Dict[str, KillSwitchStatus] = {}

        # --- Automation (per-asset levels) ---
        # Each asset maps to an AutomationLevel dataclass
        self.automation_levels: Dict[str, AutomationLevel] = {}

        # --- Emergency stop ---
        # When True, ALL signal engines stop immediately.
        self.emergency_stop: bool = False

        # --- Performance cache (recalculated lazily) ---
        self._performance_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_dirty: bool = True

        # --- Registered asset set (for fast lookups) ---
        self._registered_assets: Set[str] = set()

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "SharedAppState":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Asset registry management
    # ------------------------------------------------------------------

    def register_symbol(self, name: str, config: SymbolConfig) -> None:
        """Register a symbol in the dynamic asset registry.

        Also creates default per-asset entries for kill-switch and automation.
        """
        with self._state_lock:
            self.symbol_registry[name] = config
            self._registered_assets.add(name)

            # Ensure kill-switch entry exists
            if name not in self.kill_switch_status:
                self.kill_switch_status[name] = KillSwitchStatus(asset=name)

            # Ensure automation level entry exists (default to 0 = manual)
            if name not in self.automation_levels:
                self.automation_levels[name] = AutomationLevel(level=0)

    def unregister_symbol(self, name: str) -> Optional[SymbolConfig]:
        """Remove a symbol from the registry. Returns its config or None."""
        with self._state_lock:
            config = self.symbol_registry.pop(name, None)
            self._registered_assets.discard(name)
            self.kill_switch_status.pop(name, None)
            self.automation_levels.pop(name, None)
            return config

    def get_registered_symbols(self) -> List[str]:
        """Return the sorted list of registered symbol names."""
        with self._state_lock:
            return sorted(self._registered_assets)

    def get_symbol_config(self, name: str) -> Optional[SymbolConfig]:
        """Return the SymbolConfig for a given symbol, or None."""
        with self._state_lock:
            return self.symbol_registry.get(name)

    # ------------------------------------------------------------------
    # Thread-safe updates
    # ------------------------------------------------------------------

    def update_mt5_status(
        self,
        connected: Optional[bool] = None,
        account_type: Optional[str] = None,
        account_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._state_lock:
            if connected is not None:
                self.mt5_status.connected = connected
            if account_type is not None:
                self.mt5_status.account_type = account_type
            if account_info is not None:
                self.mt5_status.account_info = account_info
            self.mt5_status.last_updated = time.time()

    def set_mcp_connection_state(self, state: str) -> None:
        """Set the MCP connection state.

        Args:
            state: One of ``"connected"``, ``"disconnected"``,
                   ``"reconnecting"``.
        """
        with self._state_lock:
            self.mcp_connection_state = state

    def set_mcp_token(self, token: str) -> None:
        """Set the MCP authentication token (settable via GUI)."""
        with self._state_lock:
            self.mcp_token = token

    def set_pending_signals(self, signals: List[PendingSignal]) -> None:
        with self._state_lock:
            self.pending_signals = signals

    def remove_pending_signal(self, signal_id: str) -> Optional[PendingSignal]:
        with self._state_lock:
            for i, s in enumerate(self.pending_signals):
                if s.signal_id == signal_id:
                    return self.pending_signals.pop(i)
        return None

    def set_open_positions(self, positions: List[OpenPosition]) -> None:
        with self._state_lock:
            self.open_positions = positions

    def add_trade_record(self, record: TradeRecord) -> None:
        with self._state_lock:
            self.trade_log.append(record)
            self._cache_dirty = True

    def set_trade_log(self, records: List[TradeRecord]) -> None:
        with self._state_lock:
            self.trade_log = records
            self._cache_dirty = True

    def update_kill_switch(
        self, asset: str, active: bool, reason: str = ""
    ) -> None:
        with self._state_lock:
            if asset in self.kill_switch_status:
                self.kill_switch_status[asset].active = active
                self.kill_switch_status[asset].reason = reason
                self.kill_switch_status[asset].activated_at = time.time()

    def set_automation_level(self, asset: str, level: int) -> None:
        """Set automation level for a specific asset.

        Args:
            asset: Symbol name.
            level: 0=manual, 1=semi-auto, 2=full-auto.
        """
        with self._state_lock:
            if asset in self.automation_levels:
                self.automation_levels[asset].level = max(0, min(2, level))

    def set_emergency_stop(self, active: bool) -> None:
        """Set the global emergency stop flag.

        When True, ALL signal engines must stop immediately.
        """
        with self._state_lock:
            self.emergency_stop = active

    # ------------------------------------------------------------------
    # Bulk read (atomic snapshot for GUI)
    # ------------------------------------------------------------------

    def get_snapshot(self) -> Dict[str, Any]:
        """
        Return an atomic snapshot of all current state, suitable for
        the GUI to consume in one read.
        """
        with self._state_lock:
            return {
                "symbol_registry": {
                    name: {
                        "name": cfg.name,
                        "status": cfg.status,
                        "position_size_multiplier": cfg.position_size_multiplier,
                        "active": cfg.active,
                    }
                    for name, cfg in self.symbol_registry.items()
                },
                "mt5_status": {
                    "connected": self.mt5_status.connected,
                    "account_type": self.mt5_status.account_type,
                    "account_info": self.mt5_status.account_info,
                    "last_updated": self.mt5_status.last_updated,
                },
                "mcp_connection_state": self.mcp_connection_state,
                "mcp_token_set": bool(self.mcp_token),
                "pending_signals": [
                    {
                        "asset": s.asset,
                        "direction": s.direction,
                        "entry_price": s.entry_price,
                        "stop_loss": s.stop_loss,
                        "take_profit": s.take_profit,
                        "rule_score": s.rule_score,
                        "model_probability": s.model_probability,
                        "timestamp": s.timestamp,
                        "signal_id": s.signal_id,
                    }
                    for s in self.pending_signals
                ],
                "open_positions": [
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
                    for p in self.open_positions
                ],
                "trade_log": [
                    {
                        "asset": t.asset,
                        "direction": t.direction,
                        "entry_price": t.entry_price,
                        "exit_price": t.exit_price,
                        "position_size": t.position_size,
                        "gross_r": t.gross_r,
                        "net_r": t.net_r,
                        "result": t.result,
                        "exit_time": t.exit_time,
                    }
                    for t in self.trade_log
                ],
                "kill_switch_status": {
                    asset: {
                        "active": ks.active,
                        "reason": ks.reason,
                        "activated_at": ks.activated_at,
                    }
                    for asset, ks in self.kill_switch_status.items()
                },
                "automation_levels": {
                    asset: al.level
                    for asset, al in self.automation_levels.items()
                },
                "emergency_stop": self.emergency_stop,
            }

    # ------------------------------------------------------------------
    # Performance calculations
    # ------------------------------------------------------------------

    # Breakeven costs per asset (in R units — from spec Section 7)
    # Default fallback for unregistered assets
    BREAKEVEN_COST: Dict[str, float] = {}

    def set_breakeven_cost(self, asset: str, cost: float) -> None:
        """Set the breakeven cost for a specific asset."""
        with self._state_lock:
            self.BREAKEVEN_COST[asset] = cost

    def get_performance(self, asset: str) -> Dict[str, Any]:
        """
        Return performance metrics for a given asset, calculated from trade_log:
        - trade_count, win_rate, profit_factor
        - rolling 95% CI on net R
        - comparison to breakeven cost
        """
        with self._state_lock:
            be = self.BREAKEVEN_COST.get(asset, 0.0)
            trades = [t for t in self.trade_log if t.asset == asset]

            n = len(trades)
            if n == 0:
                return {
                    "asset": asset,
                    "trade_count": 0,
                    "win_rate": 0.0,
                    "profit_factor": 0.0,
                    "avg_net_r": 0.0,
                    "breakeven_cost": be,
                    "above_breakeven": False,
                    "ci_95_low": 0.0,
                    "ci_95_high": 0.0,
                }

            wins = sum(1 for t in trades if t.net_r > 0)
            win_rate = wins / n

            gross_wins = sum(t.gross_r for t in trades if t.gross_r > 0)
            gross_losses = abs(sum(t.gross_r for t in trades if t.gross_r < 0))
            profit_factor = gross_wins / gross_losses if gross_losses != 0 else float("inf")

            net_r_values = [t.net_r for t in trades]
            avg_net_r = sum(net_r_values) / n

            # Rolling 95% CI (simple normal approximation for n >= 4)
            if n >= 4:
                import math
                mean_r = avg_net_r
                variance = sum((x - mean_r) ** 2 for x in net_r_values) / (n - 1)
                std_err = math.sqrt(variance) / math.sqrt(n)
                ci_95_low = mean_r - 1.96 * std_err
                ci_95_high = mean_r + 1.96 * std_err
            else:
                ci_95_low = 0.0
                ci_95_high = 0.0

            above_breakeven = avg_net_r > be

            return {
                "asset": asset,
                "trade_count": n,
                "win_rate": round(win_rate, 4),
                "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else float("inf"),
                "avg_net_r": round(avg_net_r, 4),
                "breakeven_cost": be,
                "above_breakeven": above_breakeven,
                "ci_95_low": round(ci_95_low, 4),
                "ci_95_high": round(ci_95_high, 4),
            }


# ---------------------------------------------------------------------------
# Convenience module-level accessor
# ---------------------------------------------------------------------------

def get_state() -> SharedAppState:
    """Short alias for SharedAppState.get_instance()."""
    return SharedAppState.get_instance()


# Legacy alias for verification compatibility
AppState = SharedAppState
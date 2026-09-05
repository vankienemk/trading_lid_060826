"""
shared_app_state.py — Shared state bridge module for the paper trading system.

All components (signal engine, risk guard, execution layer, dashboard)
import this module and read/update the shared application state. This avoids
circular imports and provides a single source of truth for runtime data.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MT5Status:
    """MT5 connection status."""
    connected: bool = False
    account_type: str = "demo"          # "demo" or "real"
    account_info: Dict[str, Any] = field(default_factory=dict)
    last_updated: float = 0.0


@dataclass
class PendingSignal:
    """A single pending signal from the Signal Engine (Level 1)."""
    asset: str                          # "XAUUSD" or "EURUSD"
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
    """Current automation level (1, 2, or 3)."""
    level: int = 1                      # 1 = manual confirm, 2 = semi-auto, 3 = full auto


# ---------------------------------------------------------------------------
# Shared state singleton
# ---------------------------------------------------------------------------

class SharedAppState:
    """
    Thread-safe shared application state. Every component reads/writes through
    this class. Use get_instance() to access the singleton.
    """

    _instance: Optional["SharedAppState"] = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._state_lock = threading.Lock()

        # --- Connection ---
        self.mt5_status: MT5Status = MT5Status()

        # --- Signals ---
        self.pending_signals: List[PendingSignal] = []

        # --- Open positions ---
        self.open_positions: List[OpenPosition] = []

        # --- Trade log (completed trades) ---
        self.trade_log: List[TradeRecord] = []

        # --- Kill-switch ---
        self.kill_switch_status: Dict[str, KillSwitchStatus] = {
            "XAUUSD": KillSwitchStatus(asset="XAUUSD"),
            "EURUSD": KillSwitchStatus(asset="EURUSD"),
        }

        # --- Automation ---
        self.automation: AutomationLevel = AutomationLevel()

        # --- Emergency stop ---
        self.emergency_stop: bool = False

        # --- Performance cache (recalculated lazily) ---
        self._performance_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_dirty: bool = True

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "SharedAppState":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

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

    def set_automation_level(self, level: int) -> None:
        with self._state_lock:
            self.automation.level = max(1, min(3, level))

    def set_emergency_stop(self, active: bool) -> None:
        with self._state_lock:
            self.emergency_stop = active

    # ------------------------------------------------------------------
    # Bulk read (atomic snapshot for dashboard)
    # ------------------------------------------------------------------

    def get_snapshot(self) -> Dict[str, Any]:
        """
        Return an atomic snapshot of all current state, suitable for
        the Streamlit dashboard to consume in one read.
        """
        with self._state_lock:
            return {
                "mt5_status": {
                    "connected": self.mt5_status.connected,
                    "account_type": self.mt5_status.account_type,
                    "account_info": self.mt5_status.account_info,
                    "last_updated": self.mt5_status.last_updated,
                },
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
                "automation_level": self.automation.level,
                "emergency_stop": self.emergency_stop,
            }

    # ------------------------------------------------------------------
    # Performance calculations
    # ------------------------------------------------------------------

    # Breakeven costs per asset (in R units — from spec Section 7)
    BREAKEVEN_COST: Dict[str, float] = {
        "XAUUSD": 0.273,
        "EURUSD": 0.184,
    }

    def get_performance(self, asset: str) -> Dict[str, Any]:
        """
        Return performance metrics for a given asset, calculated from trade_log:
        - trade_count, win_rate, profit_factor
        - rolling 95% CI on net R
        - comparison to breakeven cost
        """
        with self._state_lock:
            if asset not in self.BREAKEVEN_COST:
                return {}

            be = self.BREAKEVEN_COST[asset]
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
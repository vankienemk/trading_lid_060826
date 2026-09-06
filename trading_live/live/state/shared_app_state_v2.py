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
  6. **Model Registry** — ``ModelInfo`` dataclass + ``ModelRegistry`` for
     managing trained models from ``configs/models/index.yaml``.
  7. ``SymbolConfig.model_id`` — references a model from the registry.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SymbolConfig:
    """Runtime representation of a symbol's config from YAML.

    Only symbols with ``status="validated"`` **and** a valid ``model_id``
    receive live signal engines.  See GUI_REWORK_REQUIREMENTS.md §3.
    """
    name: str
    status: str                      # "validated", "candidate", "rejected"
    active: bool = False
    model_id: Optional[str] = None    # references ModelRegistry (e.g. "xauusd_v2_h16_20260905")
    position_size_multiplier: float = 1.0


# ---------------------------------------------------------------------------
# Model Registry data structures
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """Metadata for a single registered model."""
    model_id: str
    symbol_origin: str
    model_path: str
    calibrator_path: str
    feature_schema: str
    horizon: int
    target: str
    metrics: Dict[str, Any] = field(default_factory=dict)   # pf_oos, ci_lower, ci_upper, n_trades
    created_at: str = ""
    notes: str = ""
    _resolved_paths: Dict[str, str] = field(default_factory=dict)  # abs paths after resolution


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
# Model Registry
# ---------------------------------------------------------------------------

class ModelRegistry:
    """Thread-safe registry of available models loaded from YAML index.

    The registry is populated once via ``load_from_yaml()`` and then
    accessed read-only.  It provides the metadata lookup needed by
    ``SystemBridge``, the Symbol Onboarding GUI, and the Signal Engine.

    The YAML index lives at ``configs/models/index.yaml`` (see
    GUI_REWORK_REQUIREMENTS.md §2 for schema).
    """

    _instance: Optional["ModelRegistry"] = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._models: Dict[str, ModelInfo] = {}          # model_id → ModelInfo
        self._loaded: bool = False

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "ModelRegistry":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_from_yaml(self, index_path: Optional[str] = None) -> int:
        """Parse ``configs/models/index.yaml`` and populate the registry.

        Args:
            index_path: Absolute or workspace-relative path to index YAML.
                        Defaults to ``<workspace_root>/configs/models/index.yaml``.

        Returns:
            Number of models loaded (0 if no file or empty).
        """
        if index_path is None:
            # Walk up from this module's directory to find workspace root
            base = Path(__file__).resolve().parent.parent.parent
            index_path = str(base / "model_registry" / "index.yaml")

        p = Path(index_path)
        if not p.is_file():
            return 0

        try:
            import yaml
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception:
            return 0

        if not isinstance(data, dict):
            return 0

        models_block = data.get("models", {})
        if not isinstance(models_block, dict):
            return 0

        base_dir = p.resolve().parent.parent.parent  # workspace root

        count = 0
        with self._lock:
            self._models.clear()
            for model_id, entry in models_block.items():
                if not isinstance(entry, dict):
                    continue
                entry_model_id = entry.get("model_id", model_id)
                try:
                    info = ModelInfo(
                        model_id=entry_model_id,
                        symbol_origin=entry.get("symbol_origin", ""),
                        model_path=entry.get("model_path", ""),
                        calibrator_path=entry.get("calibrator_path", ""),
                        feature_schema=entry.get("feature_schema", ""),
                        horizon=int(entry.get("horizon", 0)),
                        target=entry.get("target", ""),
                        metrics=entry.get("metrics", {}),
                        created_at=entry.get("created_at", ""),
                        notes=entry.get("notes", ""),
                    )
                    # Resolve relative paths against workspace root
                    info._resolved_paths = {
                        "model_path": str((base_dir / info.model_path).resolve()) if info.model_path else "",
                        "calibrator_path": str((base_dir / info.calibrator_path).resolve()) if info.calibrator_path else "",
                        "feature_schema": str((base_dir / info.feature_schema).resolve()) if info.feature_schema else "",
                    }
                    self._models[entry_model_id] = info
                    count += 1
                except Exception:
                    continue
            self._loaded = count > 0
        return count

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get_available_models(self) -> List[ModelInfo]:
        """Return all registered models (sorted by model_id)."""
        with self._lock:
            return sorted(self._models.values(), key=lambda m: m.model_id)

    def get_model(self, model_id: str) -> Optional[ModelInfo]:
        """Return a single ModelInfo, or None if not found."""
        with self._lock:
            return self._models.get(model_id)

    def is_loaded(self) -> bool:
        """Return True if the registry has been successfully loaded."""
        with self._lock:
            return self._loaded

    def reload(self, index_path: Optional[str] = None) -> int:
        """Reload the registry from disk (re-reads YAML)."""
        return self.load_from_yaml(index_path)


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
        Persists the registry to disk after the change.
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

        self.save_registry()

    def unregister_symbol(self, name: str) -> Optional[SymbolConfig]:
        """Remove a symbol from the registry. Returns its config or None.
        Persists the registry to disk after the change.
        """
        with self._state_lock:
            config = self.symbol_registry.pop(name, None)
            self._registered_assets.discard(name)
            self.kill_switch_status.pop(name, None)
            self.automation_levels.pop(name, None)

        if config is not None:
            self.save_registry()
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
    # Symbol registry persistence (save/load to JSON)
    # ------------------------------------------------------------------

    _REGISTRY_FILE: str = "symbol_registry.json"

    @classmethod
    def _registry_path(cls) -> Path:
        """Return the absolute path to the persisted registry file."""
        return Path(__file__).resolve().parent.parent / "db" / cls._REGISTRY_FILE

    def save_registry(self) -> None:
        """Persist the current symbol registry + per-asset state to JSON.

        Writes to ``db/symbol_registry.json`` atomically via
        atomic-write (write to temp then rename).  Includes automation levels
        so that reactivation restores the user's manual/semi/full-auto setting.
        """
        path = self._registry_path()
        with self._state_lock:
            data: Dict[str, Any] = {
                "symbols": {
                    name: {
                        "name": cfg.name,
                        "status": cfg.status,
                        "active": cfg.active,
                        "model_id": cfg.model_id,
                        "position_size_multiplier": cfg.position_size_multiplier,
                    }
                    for name, cfg in self.symbol_registry.items()
                },
                "automation_levels": {
                    asset: al.level
                    for asset, al in self.automation_levels.items()
                },
                "version": 1,
            }
        # Atomic write: write to a temp file then rename
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            tmp.rename(path)
        except Exception:
            # Best-effort: if write fails, the registry remains in-memory only
            pass

    def load_registry(self) -> int:
        """Load the symbol registry from the persisted JSON file.

        Returns:
            Number of symbols restored (0 if no persisted file exists).
        """
        path = self._registry_path()
        if not path.is_file():
            return 0

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return 0

        if not isinstance(raw, dict):
            return 0

        symbols_raw = raw.get("symbols", {})
        if not isinstance(symbols_raw, dict):
            return 0

        count = 0
        with self._state_lock:
            self.symbol_registry.clear()
            self._registered_assets.clear()
            for name, entry in symbols_raw.items():
                if not isinstance(entry, dict):
                    continue
                try:
                    cfg = SymbolConfig(
                        name=entry.get("name", name),
                        status=entry.get("status", "pending"),
                        active=bool(entry.get("active", False)),
                        model_id=entry.get("model_id"),
                        position_size_multiplier=float(
                            entry.get("position_size_multiplier", 1.0)
                        ),
                    )
                    self.symbol_registry[name] = cfg
                    self._registered_assets.add(name)
                    count += 1
                except Exception:
                    continue

            # Restore automation levels
            levels_raw = raw.get("automation_levels", {})
            if isinstance(levels_raw, dict):
                for asset_str, level_val in levels_raw.items():
                    if asset_str in self._registered_assets:
                        self.automation_levels[asset_str] = AutomationLevel(
                            level=max(0, min(2, int(level_val)))
                        )

        return count

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

        Persists registry to disk after the change.
        """
        with self._state_lock:
            if asset in self.automation_levels:
                self.automation_levels[asset].level = max(0, min(2, level))
        self.save_registry()

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
                        "model_id": cfg.model_id,
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


def get_model_registry() -> ModelRegistry:
    """Short alias for ModelRegistry.get_instance()."""
    return ModelRegistry.get_instance()


# Legacy alias for verification compatibility
AppState = SharedAppState
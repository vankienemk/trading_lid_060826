"""
risk_guard.py — Risk Guard + Kill-switch Module (Block Bootstrap per Asset)

Provides per-asset kill-switch logic using block bootstrap confidence intervals
on Profit Factor.  Never merges XAUUSD and EURUSD trade histories.

Dependencies:
    - Python 3.10+ standard library
    - numpy (for block bootstrap resampling)
    - logger.py (get_recent_trades)

Usage:
    from risk_guard import RiskGuard
    rg = RiskGuard(db_path="paper_trading.db")
    result = rg.evaluate_new_signal("XAUUSD", ...)
    # -> {'allowed': bool, 'reason': str}
"""

from __future__ import annotations

import math
import time
from typing import Any, Optional

import numpy as np

from logger import get_recent_trades

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_OPEN_PER_ASSET = 1
DEFAULT_MAX_OPEN_TOTAL = 2
DEFAULT_MIN_INTERVAL_SINCE_LAST_S = 300  # 5 minutes
DEFAULT_N_TRADES_CAP = 60  # max recent trades to consider per asset
KILL_SWITCH_MIN_TRADES = 20  # minimum trades before auto-activation
BOOTSTRAP_RESAMPLES = 3000
BOOTSTRAP_SEED_OFFSET = 1000  # base seed per asset (asset hash + offset)
PF_CI_LOWER_ALPHA = 2.5  # 2.5th percentile → one-tailed test at 95%


# ---------------------------------------------------------------------------
# Position sizing (from spec: size_multiplier per asset in config)
# ---------------------------------------------------------------------------

ASSET_POSITION_SIZES: dict[str, float] = {
    "XAUUSD": 1.0,
    "EURUSD": 0.5,
}

# ---------------------------------------------------------------------------
# Block bootstrap helpers
# ---------------------------------------------------------------------------


def _block_size(n: int) -> int:
    """Compute block size for block bootstrap.

    Formula: block_size = floor(min(25, 2 * sqrt(n)))
    """
    return min(25, max(1, int(2.0 * math.sqrt(n))))


def _block_bootstrap_pf_ci(
    net_r_array: np.ndarray,
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = 42,
) -> tuple[Optional[float], Optional[float]]:
    """Compute 95% CI for Profit Factor using block bootstrap (single series).

    Uses the same logic as pooled_oos_meta_analysis.py:
      1. Determine block_size = floor(min(25, 2*sqrt(n)))
      2. For each resample: randomly pick contiguous blocks (with random
         starting positions), concatenate to form bootstrap sample.
      3. Compute PF = sum(positives) / sum(abs(negatives)).
      4. Return (2.5th percentile, 97.5th percentile) of the bootstrap PF
         distribution.

    Args:
        net_r_array: 1-D array of net R multiples (completed trades).
        n_resamples: Number of bootstrap iterations (default 3000).
        seed: Random seed for reproducibility.

    Returns:
        (ci_lower, ci_upper) or (None, None) if insufficient valid resamples.
    """
    n = len(net_r_array)
    if n == 0:
        return None, None

    block_sz = _block_size(n)
    n_blocks = max(1, n // block_sz)

    rng = np.random.default_rng(seed)
    pf_vals: list[float] = []

    for _ in range(n_resamples):
        blocks: list[np.ndarray] = []
        for _ in range(n_blocks):
            start = rng.integers(0, max(1, n - block_sz + 1))
            blocks.append(net_r_array[start:start + block_sz])

        residual = n - n_blocks * block_sz
        if residual > 0:
            start = rng.integers(0, max(1, n - residual + 1))
            blocks.append(net_r_array[start:start + residual])

        boot = np.concatenate(blocks)[:n]

        pos_sum = float(boot[boot > 0].sum())
        neg_sum = float(abs(boot[boot < 0].sum()))

        if neg_sum > 0:
            pf_vals.append(pos_sum / neg_sum)

    pf_arr = np.array(pf_vals)
    pf_arr = pf_arr[np.isfinite(pf_arr)]

    if len(pf_arr) < 100:
        return None, None

    ci_lower = float(np.percentile(pf_arr, PF_CI_LOWER_ALPHA))
    ci_upper = float(np.percentile(pf_arr, 100.0 - PF_CI_LOWER_ALPHA))
    return ci_lower, ci_upper


# ---------------------------------------------------------------------------
# RiskGuard class
# ---------------------------------------------------------------------------


class RiskGuard:
    """Per-asset risk guard with block bootstrap kill-switch.

    Maintains two separate trackers (one per asset).  Never merges XAUUSD
    and EURUSD trade data.

    Thread-safe design: the caller is responsible for serialising access
    (the logger's SQLite handles concurrent reads safely).
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        max_open_per_asset: int = DEFAULT_MAX_OPEN_PER_ASSET,
        max_open_total: int = DEFAULT_MAX_OPEN_TOTAL,
        min_interval_since_last_s: int = DEFAULT_MIN_INTERVAL_SINCE_LAST_S,
    ) -> None:
        self._db_path = db_path

        # Configurable limits
        self.max_open_per_asset = max_open_per_asset
        self.max_open_total = max_open_total
        self.min_interval_since_last_s = min_interval_since_last_s

        # Per-asset kill-switch status
        self._kill_switch: dict[str, dict[str, Any]] = {
            "XAUUSD": {"active": False, "reason": "", "activated_at": 0.0},
            "EURUSD": {"active": False, "reason": "", "activated_at": 0.0},
        }

        # Cached most-recent PF metrics per asset (recalculated by _refresh_metrics)
        self._metrics_cache: dict[str, dict[str, Any]] = {
            "XAUUSD": {},
            "EURUSD": {},
        }

        # Track last order sent time per asset (for min_interval check)
        self._last_order_time: dict[str, float] = {
            "XAUUSD": 0.0,
            "EURUSD": 0.0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_kill_switch_status(self, asset: str) -> dict[str, Any]:
        """Return kill-switch status for *asset*.

        Returns:
            {"active": bool, "reason": str, "activated_at": float}
        """
        return dict(self._kill_switch.get(asset, {"active": False, "reason": "unknown_asset"}))

    def get_all_kill_switch_status(self) -> dict[str, dict[str, Any]]:
        """Return kill-switch status for all assets."""
        return {k: dict(v) for k, v in self._kill_switch.items()}

    def get_metrics(self, asset: str) -> dict[str, Any]:
        """Return latest performance metrics for *asset*.

        Returns a dict with keys:
            n, profit_factor, pf_ci_lower, pf_ci_upper, block_size,
            kill_switch_active, etc.
        """
        return dict(self._metrics_cache.get(asset, {}))

    def get_all_metrics(self) -> dict[str, dict[str, Any]]:
        """Return metrics for all assets."""
        return {k: dict(v) for k, v in self._metrics_cache.items()}

    def get_position_size(self, asset: str) -> float:
        """Return configured position size multiplier for *asset*."""
        return ASSET_POSITION_SIZES.get(asset, 1.0)

    def refresh(self) -> None:
        """Refresh metrics and kill-switch status for ALL assets from the DB.

        Call this after each completed trade to keep the risk guard up to
        date with the latest trade data.
        """
        for asset in ("XAUUSD", "EURUSD"):
            self._refresh_asset(asset)

    def refresh_asset(self, asset: str) -> dict[str, Any]:
        """Refresh metrics and kill-switch for a single *asset*.

        Returns the updated metrics dict for that asset.
        """
        return self._refresh_asset(asset)

    def evaluate_new_signal(
        self,
        asset: str,
        current_open_positions: list[dict[str, Any]],
        order_sent_time: Optional[float] = None,
    ) -> dict[str, Any]:
        """Evaluate whether a new signal may become an order.

        Checks (in order):
            1. Kill-switch active for this asset
            2. Max open positions per asset
            3. Max open positions total (across assets)
            4. Minimum interval since last order for this asset

        Args:
            asset: "XAUUSD" or "EURUSD".
            current_open_positions: List of open position dicts, each
                containing at least {"asset": str}.
            order_sent_time: Unix timestamp of *this* signal's intended
                order time (defaults to time.time()).

        Returns:
            {"allowed": bool, "reason": str}
        """
        # Normalise asset name: strip trailing "m" suffix from broker symbols
        base_asset = asset.rstrip("m") if asset.endswith("m") else asset
        if base_asset not in ("XAUUSD", "EURUSD"):
            return {"allowed": False, "reason": f"Unknown asset: {asset}"}

        # 1. Kill-switch check
        ks = self._kill_switch.get(asset, {})
        if ks.get("active", False):
            reason = ks.get("reason", "Kill-switch is active")
            return {"allowed": False, "reason": reason}

        # 2. Max open positions per asset
        open_for_asset = sum(
            1 for p in current_open_positions if p.get("asset") == asset
        )
        if open_for_asset >= self.max_open_per_asset:
            return {
                "allowed": False,
                "reason": (
                    f"Max open positions per asset reached "
                    f"({open_for_asset}/{self.max_open_per_asset} for {asset})"
                ),
            }

        # 3. Max open positions total
        total_open = len(current_open_positions)
        if total_open >= self.max_open_total:
            return {
                "allowed": False,
                "reason": (
                    f"Max total open positions reached "
                    f"({total_open}/{self.max_open_total})"
                ),
            }

        # 4. Minimum interval since last order for this asset
        now = order_sent_time if order_sent_time is not None else time.time()
        last_time = self._last_order_time.get(asset, 0.0)
        elapsed = now - last_time
        if elapsed < self.min_interval_since_last_s:
            remaining = self.min_interval_since_last_s - elapsed
            return {
                "allowed": False,
                "reason": (
                    f"Minimum interval since last {asset} order not met: "
                    f"{elapsed:.0f}s elapsed, need "
                    f"{self.min_interval_since_last_s}s ({remaining:.0f}s remaining)"
                ),
            }

        # All checks passed
        return {"allowed": True, "reason": ""}

    def record_order_sent(self, asset: str, timestamp: Optional[float] = None) -> None:
        """Record that an order was sent for *asset* (updates last_order_time).

        Args:
            asset: "XAUUSD" or "EURUSD".
            timestamp: Unix timestamp (defaults to time.time()).
        """
        self._last_order_time[asset] = timestamp if timestamp is not None else time.time()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refresh_asset(self, asset: str) -> dict[str, Any]:
        """Refresh metrics + kill-switch for a single asset from the DB."""
        # Fetch up to DEFAULT_N_TRADES_CAP completed trades for this asset
        trades = get_recent_trades(asset, n=DEFAULT_N_TRADES_CAP, db_path=self._db_path)
        n = len(trades)

        # Extract net_result_r values — these are the net R multiples
        net_r_values = np.array(
            [t["net_result_r"] for t in trades if t.get("net_result_r") is not None],
            dtype=float,
        )
        n_valid = len(net_r_values)

        if n_valid == 0:
            metrics: dict[str, Any] = {
                "asset": asset,
                "n": 0,
                "n_valid": 0,
                "profit_factor": None,
                "pf_ci_lower": None,
                "pf_ci_upper": None,
                "block_size": 0,
                "kill_switch_active": False,
                "kill_switch_reason": "",
            }
            self._metrics_cache[asset] = metrics
            return metrics

        # Compute Profit Factor from raw net R values
        pos_sum = float(net_r_values[net_r_values > 0].sum())
        neg_sum = float(abs(net_r_values[net_r_values < 0].sum()))
        pf = pos_sum / neg_sum if neg_sum > 0 else None

        # Compute block bootstrap CI
        seed = BOOTSTRAP_SEED_OFFSET + hash(asset) % 10000
        bs = _block_size(n_valid)
        ci_lower, ci_upper = _block_bootstrap_pf_ci(
            net_r_values, n_resamples=BOOTSTRAP_RESAMPLES, seed=seed
        )

        # Kill-switch logic:
        #   Activate if: n_valid >= KILL_SWITCH_MIN_TRADES AND ci_lower < 1.0
        kill_switch_reason = ""
        kill_switch_active = False

        if n_valid >= KILL_SWITCH_MIN_TRADES:
            if ci_lower is not None and ci_lower < 1.0:
                kill_switch_active = True
                kill_switch_reason = (
                    f"Kill-switch activated: {asset} PF 95% CI lower bound "
                    f"({ci_lower:.4f}) < 1.0 after {n_valid} trades (block bootstrap)"
                )
        else:
            # Below threshold: show metrics but don't auto-stop
            kill_switch_reason = (
                f"Only {n_valid}/{KILL_SWITCH_MIN_TRADES} trades — "
                f"not enough for kill-switch evaluation"
            )

        metrics = {
            "asset": asset,
            "n": n,
            "n_valid": n_valid,
            "profit_factor": round(pf, 4) if pf is not None else None,
            "pf_ci_lower": round(ci_lower, 4) if ci_lower is not None else None,
            "pf_ci_upper": round(ci_upper, 4) if ci_upper is not None else None,
            "block_size": bs,
            "kill_switch_active": kill_switch_active,
            "kill_switch_reason": kill_switch_reason,
            "min_trades_for_auto": KILL_SWITCH_MIN_TRADES,
        }

        # Update kill-switch state
        if kill_switch_active:
            already_active = self._kill_switch[asset]["active"]
            self._kill_switch[asset] = {
                "active": True,
                "reason": kill_switch_reason,
                "activated_at": self._kill_switch[asset]["activated_at"]
                if already_active
                else time.time(),
            }
        else:
            self._kill_switch[asset] = {
                "active": False,
                "reason": kill_switch_reason,
                "activated_at": 0.0,
            }

        self._metrics_cache[asset] = metrics
        return metrics

    def manual_override_kill_switch(
        self, asset: str, active: bool, reason: str = ""
    ) -> None:
        """Manually override the kill-switch for *asset*.

        Args:
            asset: "XAUUSD" or "EURUSD".
            active: True to activate, False to deactivate.
            reason: Human-readable reason.
        """
        if asset not in self._kill_switch:
            raise ValueError(f"Unknown asset: {asset}")

        self._kill_switch[asset] = {
            "active": active,
            "reason": reason,
            "activated_at": time.time() if active else 0.0,
        }


# ---------------------------------------------------------------------------
# Convenience module-level factory
# ---------------------------------------------------------------------------

def create_risk_guard(
    db_path: Optional[str] = None,
    max_open_per_asset: int = DEFAULT_MAX_OPEN_PER_ASSET,
    max_open_total: int = DEFAULT_MAX_OPEN_TOTAL,
    min_interval_since_last_s: int = DEFAULT_MIN_INTERVAL_SINCE_LAST_S,
) -> RiskGuard:
    """Create and return a configured RiskGuard instance.

    This is the recommended entry point.
    """
    return RiskGuard(
        db_path=db_path,
        max_open_per_asset=max_open_per_asset,
        max_open_total=max_open_total,
        min_interval_since_last_s=min_interval_since_last_s,
    )
"""
execution_layer.py — MCP Execution Layer for Paper Trading System.

Wraps the MetaTrader 5 MCP client (mt5_mcp_client.py) with paper trading
specific functions, retry logic, auto-reconnect, and integrated logging.

Flow:
  1. health_check() → ping server via initialize
  2. send_order() → log BEFORE via logger.log_signal(), call MCP, then log update
  3. get_positions() → list open positions from get_trading_open_positions
  4. get_new_candles() → fetch recent M15 candles via get_chart_history
  5. get_account_info() → get_trading_account_info
  6. get_symbols() / add_symbol() → manage MarketWatch symbols

Dependencies:
  - mt5_mcp_client.py (sibling in parent dir or via sys.path)
  - logger.py (sibling in paper_trading/)
  - shared_app_state.py (sibling in paper_trading/)
"""

from __future__ import annotations

import os
import sys
import time
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Ensure parent directory is on sys.path for mt5_mcp_client import
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from mt5_mcp_client import MCPClient, MCPConnectionError, MCPError

# ---------------------------------------------------------------------------
# Helper: parse MCP tool response
# ---------------------------------------------------------------------------

def _parse_mcp_response(result: dict) -> dict:
    """
    Extract the actual JSON dict from MCP tool response.
    MCP returns content as: [{"type": "text", "text": "{...}"}]
    """
    content = result.get("content", [])
    if content and isinstance(content, list) and len(content) > 0:
        text = content[0].get("text", "")
        if text:
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {"data": parsed}
            except json.JSONDecodeError:
                return {"text": text}
    return result, _extract_result_text

# ---------------------------------------------------------------------------
# Local imports
# ---------------------------------------------------------------------------
import logger as paper_logger
from shared_app_state import get_state, PendingSignal, OpenPosition, TradeRecord

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_ENDPOINT = os.environ.get(
    "MCP_URL",
    "http://127.0.0.1:22346/mcp",
)
DEFAULT_TOKEN = os.environ.get("MCP_TOKEN", "")

RETRY_COUNT = 1                # one retry after initial failure
RETRY_DELAY_SECONDS = 2.0

_AUTO_OPEN_CONFIRM_THRESHOLD_S = 2.0  # max seconds for auto-open confirm


# ---------------------------------------------------------------------------
# Execution Layer class
# ---------------------------------------------------------------------------

class ExecutionLayerError(Exception):
    """Base exception for ExecutionLayer operations."""
    pass


class OrderRejectedError(ExecutionLayerError):
    """Order was rejected by MT5 MCP or Risk Guard."""
    pass


class ConnectionFailedError(ExecutionLayerError):
    """MCP connection could not be established."""
    pass


class ExecutionLayer:
    """
    Paper Trading Execution Layer — wraps MCP calls with retry,
    auto-reconnect, and integrated logging.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        token: Optional[str] = None,
        auto_reconnect: bool = True,
    ):
        self.endpoint = endpoint or DEFAULT_ENDPOINT
        self.token = token or DEFAULT_TOKEN
        self.auto_reconnect = auto_reconnect

        self._client: Optional[MCPClient] = None
        self._last_health: float = 0.0
        self._session_initialized: bool = False

        # Shared state reference
        self.state = get_state()

    # ------------------------------------------------------------------
    # Internal client / session management
    # ------------------------------------------------------------------

    def _ensure_client(self) -> MCPClient:
        """Return an MCPClient instance, creating one if needed."""
        if self._client is None:
            self._client = MCPClient(
                url=self.endpoint,
                token=self.token,
                auto_reconnect=self.auto_reconnect,
            )
        return self._client

    def _ensure_session(self) -> None:
        """
        Ensure the MCP session is initialized (initialize + tools/list).
        Auto-reconnects if the session has died or was never created.
        """
        client = self._ensure_client()

        if client.initialized:
            return

        # Attempt connect with one retry on failure
        last_error: Optional[Exception] = None
        for attempt in range(RETRY_COUNT + 1):
            try:
                client.connect()
                client.list_tools(refresh=True)
                self._session_initialized = True
                return
            except (MCPConnectionError, MCPError) as exc:
                last_error = exc
                if attempt < RETRY_COUNT:
                    time.sleep(RETRY_DELAY_SECONDS)
                    # Reset client state for clean reconnect
                    client.close()
                    self._session_initialized = False
                else:
                    raise ConnectionFailedError(
                        f"Cannot establish MCP session after {RETRY_COUNT + 1} "
                        f"attempts: {exc}"
                    ) from exc

    def _call_mcp(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Call an MCP tool with auto-reconnect + single retry on connection
        errors.
        """
        self._ensure_session()
        client = self._ensure_client()

        last_error: Optional[Exception] = None
        for attempt in range(RETRY_COUNT + 1):
            try:
                return client.call_tool(tool_name, arguments or {})
            except (MCPConnectionError, MCPError) as exc:
                last_error = exc
                if attempt < RETRY_COUNT:
                    time.sleep(RETRY_DELAY_SECONDS)
                    # Force session re-init before retry
                    client.close()
                    self._session_initialized = False
                    self._ensure_session()
                else:
                    raise ConnectionFailedError(
                        f"Tool '{tool_name}' failed after "
                        f"{RETRY_COUNT + 1} attempts: {exc}"
                    ) from exc

    # ------------------------------------------------------------------
    # Public API — Health
    # ------------------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        """
        Ping the MCP server and return connection status.

        Returns a dict with:
          - ok: bool
          - url: str
          - session_id: Optional[str]
          - elapsed_seconds: float
          - account_type: Optional[str]
          - version: Optional[str]
        """
        start = time.time()

        try:
            self._ensure_session()
            client = self._ensure_client()

            # Try to get account info as a real health check
            info = {}
            try:
                info = self._call_mcp("get_trading_account_info")
            except ConnectionFailedError:
                # Server may not expose this tool; fall back to session check
                pass

            elapsed = time.time() - start

            result: Dict[str, Any] = {
                "ok": True,
                "url": self.endpoint,
                "session_id": client.session_id,
                "elapsed_seconds": round(elapsed, 3),
            }

            if info:
                result["account_type"] = info.get("account_type", "unknown")
                if "version" in info:
                    result["version"] = info.get("version")

            return result

        except (MCPConnectionError, MCPError, ConnectionFailedError) as exc:
            elapsed = time.time() - start
            return {
                "ok": False,
                "url": self.endpoint,
                "session_id": None,
                "elapsed_seconds": round(elapsed, 3),
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Public API — Account & Symbols
    # ------------------------------------------------------------------

    def get_account_info(self) -> Dict[str, Any]:
        """
        Get trading account information from MT5.

        Returns:
            Account info dict with balance, equity, margin, etc.
        """
        raw = self._call_mcp("get_trading_account_info")
        result = _parse_mcp_response(raw)
        # MCP returns {"account": {...}, "terminal": {...}}
        acct = result.get("account", result)

        # Map to flat format expected by UI
        flat: Dict[str, Any] = {}
        for k, v in acct.items():
            flat[k] = v
        # Add useful terminal info
        flat["server"] = acct.get("server", flat.get("server", ""))
        flat["account_type"] = acct.get("type", "unknown")
        flat["is_demo"] = "demo" in str(acct.get("type", "")).lower()

        # Update shared state
        self.state.update_mt5_status(
            connected=True,
            account_info=flat,
        )

        return flat

    def get_symbols(self) -> List[Dict[str, Any]]:
        """
        List all symbols currently in MarketWatch.

        Returns:
            List of symbol dicts.
        """
        return self._call_mcp("get_marketwatch_symbols", {}).get("symbols", [])

    def add_symbol(self, symbol: str) -> bool:
        """
        Add a symbol to MarketWatch.

        Args:
            symbol: Instrument name, e.g. "XAUUSD" or "EURUSD".

        Returns:
            True if successful.
        """
        result = self._call_mcp("add_marketwatch_symbol", {"symbol": symbol})
        return result.get("success", False)

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get detailed symbol information.

        Args:
            symbol: Instrument name.

        Returns:
            Symbol info dict.
        """
        return self._call_mcp("get_symbol_info", {"symbol": symbol})

    # ------------------------------------------------------------------
    # Public API — Candles / Market Data
    # ------------------------------------------------------------------

    def get_new_candles(
        self,
        symbol: str,
        period: str = "M15",
        since: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch recent candlestick data from the MCP server.

        Args:
            symbol: Instrument name, e.g. "XAUUSD".
            period: Timeframe string, default "M15" as per spec.
            since: ISO-8601 start time (e.g. "2026-09-01T00:00:00Z").
                   If omitted, fetches the last 100 candles.

        Returns:
            List of candle dicts with keys: time, open, high, low, close, tick_volume, etc.
        """
        params: Dict[str, Any] = {
            "symbol": symbol,
            "period": period,
        }
        # Use correct schema: datetime_from / datetime_to
        if since:
            params["datetime_from"] = since
            params["datetime_to"] = "2026-09-05T23:59:59"
        else:
            # Default: last 7 days
            params["datetime_from"] = "2026-08-29T00:00:00"
            params["datetime_to"] = "2026-09-05T23:59:59"
        params["limit"] = 500

        raw = self._call_mcp("get_chart_history", params)
        parsed = _parse_mcp_response(raw)

        # The MCP returns candles under "history" key, or other common keys
        candles = parsed.get("history", parsed.get("candles", parsed.get("rates", parsed.get("data", []))))

        if not isinstance(candles, list):
            candles = []

        return candles

    # ------------------------------------------------------------------
    # Public API — Positions
    # ------------------------------------------------------------------

    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get all currently open trading positions.

        Returns:
            List of position dicts with keys: ticket, symbol, type,
            volume, open_price, sl, tp, etc.
        """
        result = self._call_mcp("get_trading_open_positions", {})
        positions = result.get("positions", result.get("data", result.get("trades", [])))

        # Update shared state with current positions
        open_positions: List[OpenPosition] = []
        for p in positions:
            direction = "buy" if p.get("type", "").lower() in ("buy", "0") else "sell"
            open_positions.append(
                OpenPosition(
                    asset=p.get("symbol", p.get("asset", "Unknown")),
                    direction=direction,
                    entry_price=float(p.get("open_price", p.get("price", 0))),
                    current_price=float(p.get("current_price", p.get("price", 0))),
                    position_size=float(p.get("volume", p.get("size", 0))),
                    stop_loss=float(p.get("sl", 0)),
                    take_profit=float(p.get("tp", 0)),
                    open_time=float(p.get("open_time", p.get("time", 0))),
                    position_id=str(p.get("ticket", p.get("id", ""))),
                )
            )
        self.state.set_open_positions(open_positions)

        return positions

    # ------------------------------------------------------------------
    # Public API — Order Management
    # ------------------------------------------------------------------

    def send_order(
        self,
        asset: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        volume: float = 0.01,
        signal_id: Optional[str] = None,
        rule_score: float = 0.0,
        model_probability: float = 0.0,
        automation_level: int = 1,
        kill_switch_blocking: bool = False,
        comment: str = "paper_trade_v2",
    ) -> Dict[str, Any]:
        """
        Send a market order to MT5 via MCP.

        Flow:
            1. Check kill-switch status via shared state.
            2. Log the planned signal via logger.log_signal() (BEFORE order).
            3. Call MCP trade_send_market_order.
            4. Update the log entry with actual fill data if order succeeded.

        Args:
            asset: Instrument name (e.g. "XAUUSD").
            direction: "buy" or "sell".
            entry_price: Planned entry price.
            stop_loss: Stop-loss price.
            take_profit: Take-profit price.
            volume: Lot size (default 0.01).
            signal_id: Optional UUID; generated if omitted.
            rule_score: Signal rule score (0–1).
            model_probability: Model probability (0–1).
            automation_level: 1=manual-confirm, 2=semi-auto, 3=full-auto.
            kill_switch_blocking: Was the kill-switch active at signal time?
            comment: Order comment (default "paper_trade_v2").

        Returns:
            Dict with:
              - "success": bool
              - "event_id": str (the logger event UUID)
              - "order_result": dict from MCP (or error dict)
              - "order_sent": bool

        Raises:
            OrderRejectedError: If the MCP server rejects the order.
            ConnectionFailedError: If connection fails after all retries.
        """
        # --- 0. Generate signal_id / event_id ---
        eid = signal_id if signal_id else str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        # --- 1. Check shared state kill-switch ---
        ks = self.state.kill_switch_status.get(asset)
        if ks and ks.active:
            raise OrderRejectedError(
                f"Order blocked by kill-switch for {asset}: {ks.reason}"
            )

        # --- 2. Log BEFORE sending (entry_planned / stop / target known) ---
        order_type_str = "buy" if direction.lower() == "buy" else "sell"

        log_entry = paper_logger.log_signal(
            asset=asset,
            signal_time=now_iso,
            confirmation_time=now_iso,
            features_json=json.dumps({
                "direction": order_type_str,
                "volume": volume,
                "comment": comment,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            }),
            rule_score=rule_score,
            model_probability=model_probability,
            entry_planned=entry_price,
            stop_planned=stop_loss,
            target_planned=take_profit,
            order_sent_time="",
            automation_level=automation_level,
            kill_switch_status_at_signal=1 if kill_switch_blocking else 0,
            event_id=eid,
        )

        # --- 3. Prepare MCP order params ---
        order_type = 0 if direction.lower() == "buy" else 1  # 0=OP_BUY, 1=OP_SELL

        order_params = {
            "symbol": asset,
            "volume": volume,
            "order_type": order_type,
            "price": entry_price,
            "sl": stop_loss,
            "tp": take_profit,
            "comment": comment,
        }

        # --- 4. Call MCP trade_send_market_order ---
        try:
            order_result = self._call_mcp(
                "trade_send_market_order",
                order_params,
            )
        except (ConnectionFailedError, MCPError) as exc:
            # Log the failure in the execution layer's own way
            # (the logger entry remains with empty order_sent_time — a sentinel)
            return {
                "success": False,
                "event_id": eid,
                "order_result": {"error": str(exc)},
                "order_sent": False,
            }

        # --- 5. Check if the order was actually filled/rejected ---
        order_sent = order_result.get("success", order_result.get("retcode", 0) == 10009)

        if order_sent:
            # Update the log entry with actual fill data
            entry_actual = float(order_result.get(
                "price",
                order_result.get("open_price", entry_price),
            ))
            slippage = entry_actual - entry_price

            # If there's a ticket, we have a real fill; otherwise partial info
            ticket = order_result.get("ticket", order_result.get("order", ""))

            paper_logger.update_order_result(
                event_id=eid,
                entry_actual=entry_actual,
                slippage_actual=round(slippage, 2),
                exit_time="",                    # not exited yet
                exit_price=0.0,
                exit_reason="",
                mfe_r=0.0,
                mae_r=0.0,
                net_result_r=0.0,
            )

            # Also log the order_sent_time update
            _update_sent_time(eid, now_iso)

            # Update shared state MT5 connected flag
            self.state.update_mt5_status(connected=True)

        return {
            "success": order_sent,
            "event_id": eid,
            "order_result": order_result,
            "order_sent": order_sent,
        }

    # ------------------------------------------------------------------
    # Public API — Close Position
    # ------------------------------------------------------------------

    def close_position(
        self,
        position_id: str,
        symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Close a single open position by ticket/position_id.

        Args:
            position_id: The ticket or position id to close.
            symbol: Optional symbol name (some MCP servers require it).

        Returns:
            Dict with success status and MCP response.
        """
        params: Dict[str, Any] = {
            "position_id": position_id,
        }
        if symbol:
            params["symbol"] = symbol

        try:
            result = self._call_mcp("trade_close_single_position", params)
        except ConnectionFailedError as exc:
            return {
                "success": False,
                "error": str(exc),
                "position_id": position_id,
            }

        order_closed = result.get("success", result.get("retcode", 0) == 10009)

        # Refresh positions in shared state
        if order_closed:
            self.get_positions()

        return {
            "success": order_closed,
            "position_id": position_id,
            "result": result,
        }

    # ------------------------------------------------------------------
    # Public API — Refresh / Utility
    # ------------------------------------------------------------------

    def refresh_market_data(self, symbols: Optional[List[str]] = None) -> None:
        """
        Ensure specified symbols are on MarketWatch and update
        shared state with current connection status.

        Args:
            symbols: List of symbols to check/add. Defaults to ["XAUUSD", "EURUSD"].
        """
        if symbols is None:
            symbols = ["XAUUSD", "EURUSD"]

        # Try to refresh account info first
        try:
            info = self.get_account_info()
            self.state.update_mt5_status(connected=True)
        except ConnectionFailedError:
            self.state.update_mt5_status(connected=False)
            return

        # Add symbols to MarketWatch
        for sym in symbols:
            try:
                existing = self.get_symbols()
                if not any(s.get("symbol", s.get("name", "")) == sym for s in existing):
                    self.add_symbol(sym)
            except (ConnectionFailedError, MCPError):
                pass  # non-fatal; symbol may already be there

    def __repr__(self) -> str:
        return (
            f"<ExecutionLayer endpoint={self.endpoint} "
            f"connected={self._session_initialized}>"
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _update_sent_time(event_id: str, order_sent_time: str) -> None:
    """
    Update the order_sent_time field directly via a raw SQL UPDATE.
    This is a lightweight patch because log_signal fills it as a blank string,
    and we want it non-blank after the order dispatches without pulling in
    the full logger API (which would need an extra public function).
    """
    # Reuse the logger's connection helper
    conn = paper_logger._get_connection()
    try:
        conn.execute(
            "UPDATE signals SET order_sent_time = ? WHERE event_id = ?",
            (order_sent_time, event_id),
        )
        conn.commit()
    finally:
        conn.close()


def auto_confirm_open(
    execution: ExecutionLayer,
    signal: PendingSignal,
    automaton_level: int = 1,
) -> Dict[str, Any]:
    """
    High-level helper: auto-confirm and execute a pending signal.

    This is the integration point between the Signal Engine and the
    Execution Layer (Level 1 = manual confirm in the spec, but the
    dashboard is the confirm UI; this helper is for the automatic path
    when automation_level >= 2).

    Args:
        execution: Initialized ExecutionLayer instance.
        signal: A PendingSignal from shared state.
        automaton_level: Current automation level.

    Returns:
        Result dict from send_order.
    """
    return execution.send_order(
        asset=signal.asset,
        direction=signal.direction,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        signal_id=signal.signal_id,
        rule_score=signal.rule_score,
        model_probability=signal.model_probability,
        automation_level=automaton_level,
    )


# ---------------------------------------------------------------------------
# Convenience: module-level instance for simple scripts
# ---------------------------------------------------------------------------

_DEFAULT_EXECUTION: Optional[ExecutionLayer] = None


def get_execution() -> ExecutionLayer:
    """Return a module-level default ExecutionLayer instance (singleton)."""
    global _DEFAULT_EXECUTION
    if _DEFAULT_EXECUTION is None:
        _DEFAULT_EXECUTION = ExecutionLayer()
    return _DEFAULT_EXECUTION
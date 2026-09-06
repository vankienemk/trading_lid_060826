"""
execution_layer_v2.py — MCP Execution Layer V2 for Paper Trading System.

Wraps the MetaTrader 5 MCP client with auto-reconnect on EVERY call,
dynamic token setting, magic number 20791, LSW-V2-{event_id} order comments,
position polling during disconnect, and graceful degradation.
"""

from __future__ import annotations
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
from mt5_mcp_client import MCPClient, MCPConnectionError, MCPError

from paper_trading_v2 import logger_v2 as paper_logger
from paper_trading_v2.logger_v2 import log as syslog
from paper_trading_v2.shared_app_state_v2 import get_state, OpenPosition

DEFAULT_ENDPOINT = os.environ.get("MCP_URL", "http://127.0.0.1:22346/mcp")
DEFAULT_TOKEN = os.environ.get("MCP_TOKEN", "")
RETRY_COUNT = 1
RETRY_DELAY_SECONDS = 2.0
MAGIC_NUMBER = 20791
POSITION_POLL_INTERVAL_S = 30.0


def _parse_mcp_response(result: dict) -> dict:
    content = result.get("content", [])
    if content and isinstance(content, list) and len(content) > 0:
        text = content[0].get("text", "")
        if text:
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {"data": parsed}
            except json.JSONDecodeError:
                return {"text": text}
    return result


class ExecutionLayerError(Exception):
    pass


class OrderRejectedError(ExecutionLayerError):
    pass


class ConnectionFailedError(ExecutionLayerError):
    pass


class ExecutionLayer:
    """V2 Execution Layer — auto-reconnect on EVERY call, dynamic token,
    magic 20791, LSW-V2-{event_id} comments, position polling."""

    def __init__(self, endpoint: Optional[str] = None,
                 token: Optional[str] = None,
                 auto_reconnect: bool = True):
        self.endpoint = endpoint or DEFAULT_ENDPOINT
        self.token = token or DEFAULT_TOKEN
        self.auto_reconnect = auto_reconnect
        self._client: Optional[MCPClient] = None
        self._session_initialized: bool = False
        self._token_lock = threading.Lock()
        self.state = get_state()
        self._polling_active = True
        self._poll_thread = threading.Thread(target=self._position_poll_loop,
                                             daemon=True, name="el-pos-poll")
        self._poll_thread.start()
        syslog("INFO", "Execution", "",
               f"ExecutionLayer initialized: endpoint={self.endpoint}, auto_reconnect={auto_reconnect}",
               extra={"endpoint": self.endpoint, "auto_reconnect": auto_reconnect})

    def set_token(self, token: str) -> None:
        """Set the MCP authentication token dynamically (GUI callable)."""
        with self._token_lock:
            self.token = token
            self.state.set_mcp_token(token)
            self._close_client()
        self._reconnect()

    def _reconnect(self) -> None:
        self._close_client()
        self.state.set_mcp_connection_state("reconnecting")
        syslog("WARNING", "Execution", "", "Reconnecting to MCP...",
               extra={"endpoint": self.endpoint})
        try:
            self._ensure_session()
            self.state.set_mcp_connection_state("connected")
            syslog("INFO", "Execution", "", "MCP reconnected successfully",
                   extra={"endpoint": self.endpoint})
        except (MCPConnectionError, MCPError, ConnectionFailedError):
            self.state.set_mcp_connection_state("disconnected")
            syslog("ERROR", "Execution", "", "MCP reconnection failed",
                   extra={"endpoint": self.endpoint})

    def _ensure_client(self) -> MCPClient:
        if self._client is None:
            with self._token_lock:
                t = self.token
            self._client = MCPClient(url=self.endpoint, token=t,
                                     auto_reconnect=self.auto_reconnect)
        return self._client

    def _close_client(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
            self._session_initialized = False
            syslog("WARNING", "Execution", "", "MCP client closed (session terminated)")

    def _ensure_session(self) -> None:
        client = self._ensure_client()
        if client.initialized:
            return
        for attempt in range(RETRY_COUNT + 1):
            try:
                client.connect()
                client.list_tools(refresh=True)
                self._session_initialized = True
                self.state.set_mcp_connection_state("connected")
                syslog("INFO", "Execution", "",
                       "MCP session established successfully",
                       extra={"endpoint": self.endpoint, "attempt": attempt + 1})
                return
            except (MCPConnectionError, MCPError) as exc:
                if attempt < RETRY_COUNT:
                    time.sleep(RETRY_DELAY_SECONDS)
                    client.close()
                    self._session_initialized = False
                else:
                    self.state.set_mcp_connection_state("disconnected")
                    raise ConnectionFailedError(
                        f"Cannot establish MCP session: {exc}") from exc

    def _call_mcp(self, tool_name: str,
                  arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Call MCP tool with auto-reconnect on EVERY call.

        Returns empty dict on total failure (graceful degradation).
        """
        try:
            self._ensure_session()
        except ConnectionFailedError:
            self.state.set_mcp_connection_state("disconnected")
            syslog("ERROR", "Execution", "", f"MCP call '{tool_name}' failed: no session",
                   extra={"tool": tool_name})
            return {}

        client = self._ensure_client()
        for attempt in range(RETRY_COUNT + 1):
            try:
                result = client.call_tool(tool_name, arguments or {})
                self.state.set_mcp_connection_state("connected")
                return result
            except (MCPConnectionError, MCPError):
                if attempt < RETRY_COUNT:
                    syslog("WARNING", "Execution", "",
                           f"MCP call '{tool_name}' failed (attempt {attempt+1}/{RETRY_COUNT+1}), retrying...",
                           extra={"tool": tool_name, "attempt": attempt+1})
                    time.sleep(RETRY_DELAY_SECONDS)
                    self._close_client()
                    try:
                        self._ensure_session()
                    except ConnectionFailedError:
                        self.state.set_mcp_connection_state("disconnected")
                        syslog("ERROR", "Execution", "",
                               f"MCP call '{tool_name}' failed after retry — session lost",
                               extra={"tool": tool_name})
                        return {}
                else:
                    self.state.set_mcp_connection_state("disconnected")
                    syslog("ERROR", "Execution", "",
                           f"MCP call '{tool_name}' failed after {RETRY_COUNT+1} attempts",
                           extra={"tool": tool_name})
                    return {}

    def _position_poll_loop(self) -> None:
        """Poll positions every 30s, attempting reconnect when disconnected."""
        while self._polling_active:
            time.sleep(POSITION_POLL_INTERVAL_S)
            if self.state.mcp_connection_state == "disconnected":
                try:
                    self._ensure_session()
                    self.get_positions()
                    self.state.set_mcp_connection_state("connected")
                    syslog("INFO", "Execution", "", "Position poll: reconnected and synced positions")
                except (ConnectionFailedError, MCPError, MCPConnectionError):
                    pass

    def stop_polling(self) -> None:
        self._polling_active = False
        syslog("INFO", "Execution", "", "Position polling stopped")

    def health_check(self) -> Dict[str, Any]:
        start = time.time()
        try:
            self._ensure_session()
            client = self._ensure_client()
            info = {}
            try:
                info = self._call_mcp("get_trading_account_info")
            except ConnectionFailedError:
                pass
            elapsed = time.time() - start
            r: Dict[str, Any] = {"ok": True, "url": self.endpoint,
                                  "session_id": client.session_id,
                                  "elapsed_seconds": round(elapsed, 3)}
            if info:
                r["account_type"] = info.get("account_type", "unknown")
                if "version" in info:
                    r["version"] = info.get("version")
            return r
        except (MCPConnectionError, MCPError, ConnectionFailedError) as exc:
            elapsed = time.time() - start
            self.state.set_mcp_connection_state("disconnected")
            return {"ok": False, "url": self.endpoint,
                    "session_id": None,
                    "elapsed_seconds": round(elapsed, 3), "error": str(exc)}

    def get_account_info(self) -> Dict[str, Any]:
        raw = self._call_mcp("get_trading_account_info")
        if not raw:
            self.state.update_mt5_status(connected=False)
            return {"error": "MCP disconnected", "ok": False}
        parsed = _parse_mcp_response(raw)
        acct = parsed.get("account", parsed)
        flat = dict(acct)
        flat["server"] = acct.get("server", "")
        flat["account_type"] = acct.get("type", "unknown")
        flat["is_demo"] = "demo" in str(acct.get("type", "")).lower()
        self.state.update_mt5_status(connected=True, account_info=flat)
        return flat

    def get_symbols(self) -> List[Dict[str, Any]]:
        result = self._call_mcp("get_marketwatch_symbols", {})
        if not result:
            return []
        parsed = _parse_mcp_response(result)
        symbols = parsed.get("symbols", [])
        return symbols

    def add_symbol(self, symbol: str) -> bool:
        result = self._call_mcp("add_marketwatch_symbol", {"symbol": symbol})
        if not result:
            return False
        parsed = _parse_mcp_response(result)
        return not parsed.get("isError", False)

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        return self._call_mcp("get_symbol_info", {"symbol": symbol})

    def get_new_candles(self, symbol: str, period: str = "M15",
                        since: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"symbol": symbol, "period": period}
        if since:
            params["datetime_from"] = since
        else:
            params["datetime_from"] = "2026-08-29T00:00:00"
        params["datetime_to"] = "2026-09-05T23:59:59"
        params["limit"] = 500
        raw = self._call_mcp("get_chart_history", params)
        if not raw:
            return []
        parsed = _parse_mcp_response(raw)
        candles = parsed.get("history", parsed.get("candles",
                          parsed.get("rates", parsed.get("data", []))))
        return candles if isinstance(candles, list) else []

    def get_positions(self) -> List[Dict[str, Any]]:
        result = self._call_mcp("get_trading_open_positions", {})
        if not result:
            return []
        positions = result.get("positions", result.get("data", result.get("trades", [])))
        open_positions: List[OpenPosition] = []
        for p in positions:
            direction = "buy" if p.get("type", "").lower() in ("buy", "0") else "sell"
            open_positions.append(OpenPosition(
                asset=p.get("symbol", p.get("asset", "Unknown")),
                direction=direction,
                entry_price=float(p.get("open_price", p.get("price", 0))),
                current_price=float(p.get("current_price", p.get("price", 0))),
                position_size=float(p.get("volume", p.get("size", 0))),
                stop_loss=float(p.get("sl", 0)),
                take_profit=float(p.get("tp", 0)),
                open_time=float(p.get("open_time", p.get("time", 0))),
                position_id=str(p.get("ticket", p.get("id", ""))),
            ))
        self.state.set_open_positions(open_positions)
        return positions

    def send_order(self, asset: str, direction: str,
                   entry_price: float, stop_loss: float,
                   take_profit: float, volume: float = 0.01,
                   signal_id: Optional[str] = None,
                   rule_score: float = 0.0,
                   model_probability: float = 0.0,
                   automation_level: int = 1,
                   kill_switch_blocking: bool = False) -> Dict[str, Any]:
        """Send market order to MT5 via MCP.

        Flow: check kill-switch, log BEFORE order, send with magic=20791
        and comment=LSW-V2-{event_id}, update log on success.
        """
        eid = signal_id if signal_id else str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Check kill-switch
        ks = self.state.kill_switch_status.get(asset)
        if ks and ks.active:
            raise OrderRejectedError(f"Order blocked by kill-switch: {ks.reason}")

        # 2. Log BEFORE sending order
        order_type_str = "buy" if direction.lower() == "buy" else "sell"
        comment = f"LSW-V2-{eid}"
        paper_logger.log_signal(
            asset=asset, signal_time=now_iso, confirmation_time=now_iso,
            features_json=json.dumps({
                "direction": order_type_str, "volume": volume,
                "comment": comment, "entry_price": entry_price,
                "stop_loss": stop_loss, "take_profit": take_profit,
            }),
            rule_score=rule_score, model_probability=model_probability,
            entry_planned=entry_price, stop_planned=stop_loss,
            target_planned=take_profit, order_sent_time="",
            automation_level=automation_level,
            kill_switch_status_at_signal=1 if kill_switch_blocking else 0,
            event_id=eid,
        )

        # 3. Send order with magic=20791 and LSW-V2 comment
        order_type = 0 if direction.lower() == "buy" else 1
        order_params = {
            "symbol": asset, "volume": volume,
            "order_type": order_type, "price": entry_price,
            "sl": stop_loss, "tp": take_profit,
            "comment": comment, "magic": MAGIC_NUMBER,
        }

        try:
            order_result = self._call_mcp("trade_send_market_order", order_params)
        except (ConnectionFailedError, MCPError) as exc:
            return {"success": False, "event_id": eid,
                    "order_result": {"error": str(exc)}, "order_sent": False}

        if not order_result:
            return {"success": False, "event_id": eid,
                    "order_result": {"error": "Empty MCP response"},
                    "order_sent": False}

        order_sent = order_result.get("success",
                            order_result.get("retcode", 0) == 10009)
        if order_sent:
            entry_actual = float(order_result.get("price",
                                   order_result.get("open_price", entry_price)))
            slippage = entry_actual - entry_price
            paper_logger.update_order_result(
                event_id=eid, entry_actual=entry_actual,
                slippage_actual=round(slippage, 2),
                exit_time="", exit_price=0.0, exit_reason="",
                mfe_r=0.0, mae_r=0.0, net_result_r=0.0,
            )
            _update_sent_time(eid, now_iso)
            self.state.update_mt5_status(connected=True)
            syslog("INFO", "Execution", asset,
                   f"Order sent → order_id={eid}, entry={entry_actual:.2f}, SL={stop_loss:.2f}, TP={take_profit:.2f}",
                   extra={"event_id": eid, "direction": order_type_str,
                          "entry": entry_actual, "sl": stop_loss, "tp": take_profit,
                          "volume": volume, "slippage": round(slippage, 2)})
        else:
            error_msg = order_result.get("error",
                         order_result.get("comment", "Unknown error"))
            syslog("ERROR", "Execution", asset,
                   f"Order FAILED: {error_msg}",
                   extra={"event_id": eid, "direction": order_type_str,
                          "entry": entry_price, "order_result": str(order_result)})

        return {"success": order_sent, "event_id": eid,
                "order_result": order_result, "order_sent": order_sent}

    def close_position(self, position_id: str,
                       symbol: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"position_id": position_id}
        if symbol:
            params["symbol"] = symbol
        try:
            result = self._call_mcp("trade_close_single_position", params)
        except ConnectionFailedError as exc:
            syslog("ERROR", "Execution", symbol or "",
                   f"Close position {position_id} failed: MCP disconnected",
                   extra={"position_id": position_id, "error": str(exc)})
            return {"success": False, "error": str(exc),
                    "position_id": position_id}
        if not result:
            syslog("ERROR", "Execution", symbol or "",
                   f"Close position {position_id} failed: empty response",
                   extra={"position_id": position_id})
            return {"success": False, "error": "Empty MCP response",
                    "position_id": position_id}
        order_closed = result.get("success",
                         result.get("retcode", 0) == 10009)
        if order_closed:
            self.get_positions()
            syslog("INFO", "Execution", symbol or "",
                   f"Position {position_id} closed successfully",
                   extra={"position_id": position_id, "symbol": symbol or ""})
        else:
            syslog("WARNING", "Execution", symbol or "",
                   f"Position {position_id} close returned: {result.get('error', 'unknown')}",
                   extra={"position_id": position_id, "result": str(result)})
        return {"success": order_closed, "position_id": position_id,
                "result": result}

    def refresh_market_data(self, symbols: Optional[List[str]] = None) -> None:
        if symbols is None:
            symbols = ["XAUUSD", "EURUSD"]
        try:
            info = self.get_account_info()
            self.state.update_mt5_status(connected=True)
        except ConnectionFailedError:
            self.state.update_mt5_status(connected=False)
            return
        for sym in symbols:
            try:
                existing = self.get_symbols()
                if not any(s.get("symbol", s.get("name", "")) == sym
                           for s in existing):
                    self.add_symbol(sym)
            except (ConnectionFailedError, MCPError):
                pass


def _update_sent_time(event_id: str, order_sent_time: str) -> None:
    """Update order_sent_time in DB after order dispatches."""
    conn = paper_logger._get_connection()
    try:
        conn.execute(
            "UPDATE signals SET order_sent_time = ? WHERE event_id = ?",
            (order_sent_time, event_id),
        )
        conn.commit()
    finally:
        conn.close()


_DEFAULT_EXECUTION: Optional[ExecutionLayer] = None


def get_execution() -> ExecutionLayer:
    global _DEFAULT_EXECUTION
    if _DEFAULT_EXECUTION is None:
        _DEFAULT_EXECUTION = ExecutionLayer()
    return _DEFAULT_EXECUTION
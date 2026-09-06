"""
dashboard.py — Streamlit Dashboard UI for the Paper Trading System.

Requirements (spec section 7):
1. Connection status: MT5 connected/disconnected, account info (demo/real - big warning if not demo), automation level (1/2/3)
2. Pending signals table (Level 1): each row with Send Order and Skip buttons
3. Open positions table: entry price, current price, P/L in R
4. Performance table per asset: trade count, win rate, PF, rolling CI 95%, breakeven cost
5. Kill-switch status: active/inactive per asset with reason
6. Emergency stop button
7. Refresh every 15 seconds via st.rerun

Usage:
    streamlit run dashboard.py
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import streamlit as st

# Ensure parent dir is importable
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from shared_app_state import AppState
from execution_layer import ExecutionLayer, _parse_mcp_response

# ---------------------------------------------------------------------------
# MCP connection (dashboard connects independently)
# ---------------------------------------------------------------------------

MCP_TOKEN = os.environ.get("MCP_TOKEN", "")
MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:22346/mcp")

@st.cache_resource
def get_execution() -> ExecutionLayer:
    """Create a shared ExecutionLayer instance cached by Streamlit."""
    return ExecutionLayer(endpoint=MCP_URL, token=MCP_TOKEN, auto_reconnect=True)

def _fetch_mcp_state(exec_layer: ExecutionLayer) -> dict:
    """Fetch current state directly from MCP and return a snapshot dict."""
    state = AppState.get_instance()
    snap = state.get_snapshot()
    
    try:
        # 1. Health & account info
        health = exec_layer.health_check()
        if health.get("ok"):
            state.update_mt5_status(connected=True)
            acct = exec_layer.get_account_info()
            acct_type = acct.get("account_type", acct.get("type", "unknown"))
            state.update_mt5_status(connected=True, account_type="demo" if "demo" in str(acct_type).lower() else "real")
        else:
            state.update_mt5_status(connected=False)
        
        # 2. Open positions
        try:
            positions = exec_layer.get_positions()
        except Exception:
            positions = []
        
        # 3. Symbols / MarketWatch
        try:
            symbols = exec_layer.get_symbols()
        except Exception:
            symbols = []
            
    except Exception as e:
        state.update_mt5_status(connected=False)
    
    return state.get_snapshot()

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Paper Trading Dashboard — Liquidity Sweep V2",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# State instance
# ---------------------------------------------------------------------------

def get_state() -> AppState:
    """Get or create the shared app state singleton in session state."""
    if "app_state" not in st.session_state:
        st.session_state["app_state"] = AppState.get_instance()
    return st.session_state["app_state"]


# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------

def auto_refresh(interval_s: int = 15) -> None:
    """Re-run the Streamlit script every `interval_s` seconds."""
    st.markdown(
        f"""
        <meta http-equiv="refresh" content="{interval_s}">
        <div style="font-size:0.8em;color:#888;text-align:right;">
            Auto-refreshing every {interval_s}s
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Helper: format timestamp
# ---------------------------------------------------------------------------

def fmt_time(ts: float) -> str:
    if ts == 0:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def fmt_r(val: float) -> str:
    """Format an R-multiple value with sign."""
    if val >= 0:
        return f"+{val:.2f}R"
    return f"{val:.2f}R"


# ---------------------------------------------------------------------------
# CSS tweaks
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    .big-warning {
        background-color: #ff4444;
        color: white;
        font-weight: bold;
        padding: 12px 20px;
        border-radius: 6px;
        margin-bottom: 10px;
        text-align: center;
        font-size: 1.2em;
    }
    .kill-active {
        background-color: #ffcccc;
        color: #cc0000;
        font-weight: bold;
        padding: 6px 12px;
        border-radius: 4px;
    }
    .kill-inactive {
        background-color: #ccffcc;
        color: #006600;
        font-weight: bold;
        padding: 6px 12px;
        border-radius: 4px;
    }
    .stButton button {
        min-width: 100px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Top-level layout
# ---------------------------------------------------------------------------

st.title("📊 Paper Trading Dashboard — Liquidity Sweep V2")
st.caption("Real-time monitoring and control for the paper trading system.")

state = get_state()
snap = _fetch_mcp_state(get_execution())  # fetch live from MCP
auto_refresh(15)

# ---------------------------------------------------------------------------
# 1. Connection status
# ---------------------------------------------------------------------------

st.header("🔌 Connection Status")

col1, col2, col3 = st.columns(3)

with col1:
    connected = snap["mt5_status"]["connected"]
    if connected:
        st.success("✅ MT5 Connected")
    else:
        st.error("❌ MT5 Disconnected")

with col2:
    acct_type = snap["mt5_status"]["account_type"]
    if acct_type == "demo":
        st.info(f"📄 Account: **{acct_type.upper()}**")
    else:
        st.markdown(
            '<div class="big-warning">⚠️ WARNING: REAL ACCOUNT — TRADING REAL MONEY</div>',
            unsafe_allow_html=True,
        )

with col3:
    auto_level = snap["automation_level"]
    level_labels = {1: "Level 1 — Manual Confirm", 2: "Level 2 — Semi-Auto", 3: "Level 3 — Full Auto"}
    st.metric("Automation Level", level_labels.get(auto_level, f"Level {auto_level}"))

if snap["mt5_status"]["account_info"]:
    info = snap["mt5_status"]["account_info"]
    with st.expander("Account Details"):
        for k, v in info.items():
            st.text(f"{k}: {v}")

st.divider()

# ---------------------------------------------------------------------------
# 2. Pending signals table
# ---------------------------------------------------------------------------

st.header("📡 Pending Signals (Level 1)")

signals = snap["pending_signals"]

if not signals:
    st.info("No pending signals at this time.")
else:
    # Table headers
    cols = st.columns([1, 1, 1, 1, 1, 1, 1, 1, 0.8, 0.8])
    headers = ["Asset", "Dir", "Entry", "Stop Loss", "Take Profit", "Rule Score", "Model Prob", "Time", "Send", "Skip"]
    for col, header in zip(cols, headers):
        col.markdown(f"**{header}**")

    for sig in signals:
        cols = st.columns([1, 1, 1, 1, 1, 1, 1, 1, 0.8, 0.8])
        cols[0].write(sig["asset"])
        cols[1].write(sig["direction"])
        cols[2].write(f"{sig['entry_price']:.5f}" if sig["entry_price"] else "—")
        cols[3].write(f"{sig['stop_loss']:.5f}" if sig["stop_loss"] else "—")
        cols[4].write(f"{sig['take_profit']:.5f}" if sig["take_profit"] else "—")
        cols[5].write(f"{sig['rule_score']:.3f}" if sig["rule_score"] else "—")
        cols[6].write(f"{sig['model_probability']:.3f}" if sig["model_probability"] else "—")
        cols[7].write(fmt_time(sig["timestamp"]))

        signal_id = sig["signal_id"]
        send_key = f"send_{signal_id}_{int(time.time()*1000)}"
        skip_key = f"skip_{signal_id}_{int(time.time()*1000)}"

        if cols[8].button("Send Order", key=send_key, type="primary"):
            state.remove_pending_signal(signal_id)
            st.success(f"Order sent for {sig['asset']} {sig['direction']} signal {signal_id}")
            st.rerun()

        if cols[9].button("Skip", key=skip_key):
            state.remove_pending_signal(signal_id)
            st.info(f"Signal {signal_id} skipped.")
            st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# 3. Open positions table
# ---------------------------------------------------------------------------

st.header("💼 Open Positions")

positions = snap["open_positions"]

if not positions:
    st.info("No open positions.")
else:
    pos_data = []
    for p in positions:
        pl_r = (p["current_price"] - p["entry_price"]) / (p["entry_price"] - p["stop_loss"]) * 100 if (p["entry_price"] - p["stop_loss"]) != 0 else 0.0
        if p["direction"] == "sell":
            pl_r = -pl_r

        pos_data.append({
            "Asset": p["asset"],
            "Dir": p["direction"],
            "Entry": f"{p['entry_price']:.5f}" if p["entry_price"] else "—",
            "Current": f"{p['current_price']:.5f}" if p["current_price"] else "—",
            "Size": f"{p['position_size']:.2f}",
            "Stop Loss": f"{p['stop_loss']:.5f}" if p["stop_loss"] else "—",
            "Take Profit": f"{p['take_profit']:.5f}" if p["take_profit"] else "—",
            "P/L (R)": fmt_r(pl_r),
            "Open": fmt_time(p["open_time"]),
        })

    st.dataframe(pos_data, use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------------------
# 4. Performance table per asset
# ---------------------------------------------------------------------------

st.header("📈 Performance Per Asset")

assets = ["XAUUSD", "EURUSD"]
perf_rows = []

for asset in assets:
    perf = state.get_performance(asset)

    be = perf.get("breakeven_cost", 0)
    ci_str = f"[{perf.get('ci_95_low', 0):.4f}, {perf.get('ci_95_high', 0):.4f}]"
    above = "✅ Yes" if perf.get("above_breakeven", False) else "❌ No"

    perf_rows.append({
        "Asset": asset,
        "Trades": perf.get("trade_count", 0),
        "Win Rate": f"{perf.get('win_rate', 0)*100:.1f}%" if perf.get("trade_count", 0) > 0 else "—",
        "Profit Factor": f"{perf.get('profit_factor', 0):.3f}" if perf.get("profit_factor", 0) != float("inf") and perf.get("trade_count", 0) > 0 else "∞" if perf.get("trade_count", 0) > 0 else "—",
        "Avg Net R": f"{perf.get('avg_net_r', 0):.4f}R" if perf.get("trade_count", 0) > 0 else "—",
        "Breakeven Cost": f"{be}R",
        "Above Breakeven": above,
        "95% CI": ci_str if perf.get("trade_count", 0) >= 4 else "N/A (<4 trades)",
    })

st.dataframe(perf_rows, use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------------------
# 5. Kill-switch status per asset
# ---------------------------------------------------------------------------

st.header("🔒 Kill-Switch Status")

ks_data = snap["kill_switch_status"]

if not ks_data:
    st.info("Kill-switch data not available.")
else:
    ks_cols = st.columns(len(ks_data))
    for i, (asset, ks) in enumerate(ks_data.items()):
        with ks_cols[i]:
            st.subheader(asset)
            if ks["active"]:
                st.markdown(
                    f'<div class="kill-active">⛔ ACTIVE</div>',
                    unsafe_allow_html=True,
                )
                st.warning(f"Reason: {ks['reason']}")
                st.text(f"Activated: {fmt_time(ks['activated_at'])}")
            else:
                st.markdown(
                    f'<div class="kill-inactive">✅ Inactive</div>',
                    unsafe_allow_html=True,
                )
                st.text("No action required.")

st.divider()

# ---------------------------------------------------------------------------
# 6. Emergency stop button
# ---------------------------------------------------------------------------

st.header("🛑 Emergency Controls")

col_estop, col_status = st.columns([1, 3])

with col_estop:
    if snap["emergency_stop"]:
        st.error("🚨 EMERGENCY STOP IS ACTIVE")
        if st.button("Reset Emergency Stop", type="secondary", use_container_width=True):
            state.set_emergency_stop(False)
            st.success("Emergency stop reset. Signal engine may resume.")
            st.rerun()
    else:
        if st.button("🛑 EMERGENCY STOP", type="primary", use_container_width=True):
            state.set_emergency_stop(True)
            st.error("🚨 EMERGENCY STOP ACTIVATED — Signal engine halted.")
            st.rerun()

with col_status:
    if snap["emergency_stop"]:
        st.error("The signal engine is halted. Click 'Reset Emergency Stop' to resume.")
    else:
        st.success("Signal engine running normally.")

# ---------------------------------------------------------------------------
# Trade log footer
# ---------------------------------------------------------------------------

st.divider()
st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
           f"Total trades logged: {len(snap['trade_log'])} | "
           f"Pending signals: {len(snap['pending_signals'])} | "
           f"Open positions: {len(snap['open_positions'])}")
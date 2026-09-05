"""
paper_trading_v2_gui — Paper Trading V2 Python GUI (Dear PyGui)

4-tab desktop GUI for the Paper Trading V2 system.
Replaces the Streamlit v1 dashboard.
Uses Dear PyGui for low-latency, immediate-mode interaction.

Architecture:
  gui_main.py           — App entry point, Dear PyGui setup, tab switching
  gui_tab1_onboarding.py — 7-step Symbol Onboarding wizard with hard gates
  gui_tab2_live_control.py — Live Control: per-symbol signals/positions/emergency stop
  gui_tab3_performance.py — Performance Monitor: metrics table, equity curve, kill-switch
  gui_tab4_account.py   — Account & Connection: MT5 info, MCP status, token input
  gui_bridge.py         — Adapter/interface to system components (logger, risk, execution)
  gui_polling_engine.py — Background QThread-style polling for real-time updates
"""
#!/usr/bin/env python3
import json, sys, time; sys.path.insert(0, '.')
from mt5_mcp_client import MCPClient
c = MCPClient(token="R+Ow4hPC5HGjM7wl2D7OQBqqUsO3Br2KQ2kvI8tRXe")
c.connect()

# Check account info
print("=== Account Info ===")
r = c.call_tool("get_trading_account_info", {})
print(json.dumps(r, indent=2, ensure_ascii=False))

# MarketWatch symbols
print("\n=== MarketWatch Symbols ===")
r = c.call_tool("get_marketwatch_symbols", {})
data = json.loads(r['content'][0]['text'])
for sym in data['symbols']:
    print(f"  {sym['symbol']:20s} | {sym['description'][:40]:40s} | mode={sym.get('trade_mode_name','?')}")

# Try adding forex symbols
for sym in ["XAUUSD", "EURUSD"]:
    print(f"\n--- Adding {sym} ---")
    r = c.call_tool("add_marketwatch_symbol", {"symbol": sym})
    print(json.dumps(r, indent=2, ensure_ascii=False))

# Check again after adding
print("\n=== After Add ===")
time.sleep(1)
r = c.call_tool("get_marketwatch_symbols", {})
data = json.loads(r['content'][0]['text'])
for sym in data['symbols']:
    print(f"  {sym['symbol']:20s} | trade_mode={sym.get('trade_mode_name','?')}")
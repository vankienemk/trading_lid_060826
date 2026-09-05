#!/usr/bin/env python3
import json, sys, time; sys.path.insert(0, '.')
from mt5_mcp_client import MCPClient
c = MCPClient(token="R+Ow4hPC5HGjM7wl2D7OQBqqUsO3Br2KQ2kvI8tRXe")
c.connect()

# Try much wider date range - 6 months back
now_ts = int(time.time())
from_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts - 86400 * 180))  # 180d
to_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts + 3600))

for sym in ["XAUUSDm", "EURUSDm"]:
    for period in ["M15", "D1"]:
        r = c.call_tool("get_chart_history", {
            "symbol": sym, "period": period,
            "datetime_from": from_iso, "datetime_to": to_iso, "limit": 5
        })
        if r.get('isError'):
            print(f"  {sym} {period}: ERROR")
        else:
            data = json.loads(r['content'][0]['text']) if r['content'][0]['text'] else {}
            candles = data.get('candles', [])
            print(f"  {sym} {period}: {len(candles)} candles")
            if candles:
                print(f"    first: {candles[0].get('time','?')} O={candles[0].get('open','?')}")
                print(f"    last:  {candles[-1].get('time','?')} O={candles[-1].get('open','?')}")

# Also check account again
r = c.call_tool("get_trading_account_info", {})
data = json.loads(r['content'][0]['text'])
print(f"\nTrade allowed: {data['terminal']['experts_trade_allowed']}")
print(f"Balance: {data['account']['balance']}")
print(f"Server: {data['account']['server']}")
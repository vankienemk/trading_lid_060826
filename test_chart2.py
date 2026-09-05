#!/usr/bin/env python3
import json, sys, time; sys.path.insert(0, '.')
from mt5_mcp_client import MCPClient
c = MCPClient(token="R+Ow4hPC5HGjM7wl2D7OQBqqUsO3Br2KQ2kvI8tRXe")
c.connect()

# Check trading account info
r = c.call_tool("get_trading_account_info", {})
print("=== Account Info ===")
data = json.loads(r['content'][0]['text'])
print(f"  Server: {data['account']['server']}")
print(f"  Login: {data['account']['login']}")
print(f"  Trade allowed: {data['terminal']['experts_trade_allowed']}")

# Check time
r = c.call_tool("get_time_information", {})
print(json.dumps(r, indent=2, ensure_ascii=False)[:500])

# Try D1 chart (more likely to have data)
now_ts = int(time.time())
from_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts - 86400 * 60))  # 60d
to_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts + 3600))

for period in ["D1", "H4", "H1"]:
    for sym in ["XAUUSDm", "EURUSDm"]:
        r = c.call_tool("get_chart_history", {
            "symbol": sym, "period": period,
            "datetime_from": from_iso, "datetime_to": to_iso, "limit": 3
        })
        if r.get('isError'):
            print(f"  {sym} {period}: ERROR {r['content'][0]['text'][:80]}")
        else:
            data = json.loads(r['content'][0]['text']) if r['content'][0]['text'] else {}
            candles = data.get('candles', [])
            print(f"  {sym} {period}: {len(candles)} candles")
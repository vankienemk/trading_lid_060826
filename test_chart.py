#!/usr/bin/env python3
import json, sys; sys.path.insert(0, '.')
from mt5_mcp_client import MCPClient
c = MCPClient(token="R+Ow4hPC5HGjM7wl2D7OQBqqUsO3Br2KQ2kvI8tRXe")
c.connect()

import time
now_ts = int(time.time())
from_ts = now_ts - 86400 * 3  # 3 days ago
from_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(from_ts))
to_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts + 3600))

for sym in ["XAUUSDm", "EURUSDm"]:
    print(f"\n=== {sym} ===")
    r = c.call_tool("get_chart_history", {
        "symbol": sym,
        "period": "M15",
        "datetime_from": from_iso,
        "datetime_to": to_iso,
        "limit": 5
    })
    result = r.get('content', [{}])[0].get('text', '')
    if r.get('isError'):
        print(f"  ERROR: {result[:200]}")
    else:
        data = json.loads(result) if result else {}
        candles = data.get('candles', [])
        print(f"  Candles returned: {len(candles)}")
        for c in candles[:3]:
            print(f"    time={c.get('time','?')} O={c.get('open','?')} H={c.get('high','?')} L={c.get('low','?')} C={c.get('close','?')}")
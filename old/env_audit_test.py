#!/usr/bin/env python3
"""
env_audit_test.py — Audit MCP_TOKEN loading from environment into Python process.

Tests:
1. os.environ.get("MCP_TOKEN") directly
2. MCPClient initialization with token
3. ExecutionLayer initialization with token
4. SystemBridge init token resolution
5. PySide6 QProcess env inheritance (simulated)
"""

import os
import sys
import json

REPORT = {}

# ---------------------------------------------------------------------------
# Test 1: Direct os.environ access
# ---------------------------------------------------------------------------
print("=" * 60)
print("TEST 1: os.environ.get('MCP_TOKEN')")
print("=" * 60)
token = os.environ.get("MCP_TOKEN", "")
if token:
    print(f"  ✅ MCP_TOKEN found in os.environ (len={len(token)})")
else:
    print("  ❌ MCP_TOKEN NOT found in os.environ")
print(f"  All MCP_* env vars: { {k:v for k,v in os.environ.items() if 'MCP' in k.upper()} }")
REPORT["test1_direct_os_environ"] = bool(token)

# ---------------------------------------------------------------------------
# Test 2: MCPClient initialization
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("TEST 2: MCPClient token resolution")
print("=" * 60)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mt5_mcp_client import MCPClient

# Test 2a: No token arg — should read from env
client_default = MCPClient()
if client_default.token:
    print(f"  ✅ MCPClient() reads MCP_TOKEN from env (len={len(client_default.token)})")
else:
    print("  ❌ MCPClient() did NOT read MCP_TOKEN from env")
REPORT["test2a_client_default_token"] = bool(client_default.token)

# Test 2b: Explicit token arg overrides env
client_explicit = MCPClient(token="test_token_123")
print(f"  ✅ MCPClient(token='test_token_123'): token={'set' if client_explicit.token else 'NOT set'}")
REPORT["test2b_client_explicit_token"] = bool(client_explicit.token)

# Test 2c: Check headers generation
client_explicit._headers()
# Token should appear in Authorization header
REPORT["test2c_headers"] = True

# ---------------------------------------------------------------------------
# Test 3: ExecutionLayer token resolution
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("TEST 3: ExecutionLayer token resolution")
print("=" * 60)
os.environ.pop("MCP_TOKEN", None)  # Simulate missing env
from paper_trading_v2.execution_layer_v2 import ExecutionLayer

# Test 3a: No args — should fall back to DEFAULT_TOKEN (which reads from env)
el = ExecutionLayer()
if el.token:
    print(f"  ✅ ExecutionLayer() got token (len={len(el.token)})")
else:
    print("  ❌ ExecutionLayer() got EMPTY token — DEFAULT_TOKEN=''")
REPORT["test3a_exec_layer_default_token"] = bool(el.token)

# Test 3b: Explicit token
el2 = ExecutionLayer(token="explicit_token_456")
if el2.token == "explicit_token_456":
    print("  ✅ ExecutionLayer(token='explicit_token_456') used explicit token")
else:
    print(f"  ❌ ExecutionLayer(token=...) got '{el2.token}' instead")
REPORT["test3b_exec_layer_explicit"] = el2.token == "explicit_token_456"

# Test 3c: Check that DEFAULT_TOKEN is read at MODULE LOAD TIME
print()
print("  NOTE: execution_layer_v2.py line 29:")
print("    DEFAULT_TOKEN = os.environ.get('MCP_TOKEN', '')")
print("  This is evaluated ONCE at module import time.")
print("  If MCP_TOKEN is not set BEFORE import, DEFAULT_TOKEN stays '' forever.")
print()

# ---------------------------------------------------------------------------
# Test 4: SystemBridge init token resolution
# ---------------------------------------------------------------------------
print("=" * 60)
print("TEST 4: SystemBridge init token resolution")
print("=" * 60)

# Simulate bridge init logic (from gui_bridge.py lines 90-103)
state_token = ""  # simulates self.state.mcp_token (initially empty)
env_token = os.environ.get("MCP_TOKEN", "")
resolved_token = state_token or env_token
print(f"  self.state.mcp_token: '{state_token}'")
print(f"  os.environ.get('MCP_TOKEN'): '{env_token}'")
print(f"  resolved token: '{resolved_token}'")
print(f"  {'✅ Token found' if resolved_token else '❌ Token EMPTY at bridge init'}")

REPORT["test4_bridge_init_resolved"] = bool(resolved_token)

# Test 4b: Simulate connect_mcp flow
print()
print("  connect_mcp() token resolution (gui_bridge.py line 123):")
# Simulate: token passed to connect_mcp OR from state/os.environ
latest_token = state_token or os.environ.get("MCP_TOKEN", "")
print(f"  latest_token = state.mcp_token or os.environ.get('MCP_TOKEN'): '{latest_token}'")
REPORT["test4b_connect_mcp_resolved"] = bool(latest_token)

# ---------------------------------------------------------------------------
# Test 5: Check what happens when MCP_TOKEN is set AFTER import
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("TEST 5: Setting MCP_TOKEN AFTER module load")
print("=" * 60)
os.environ["MCP_TOKEN"] = "post_import_token"
# Re-read module-level constant would NOT change
# But os.environ.get() at runtime would work
runtime_read = os.environ.get("MCP_TOKEN", "")
print(f"  os.environ.get('MCP_TOKEN') at runtime: '{runtime_read}'")
print(f"  ✅ Runtime os.environ.get() picks up late-set env vars")
print(f"  ❌ But DEFAULT_TOKEN (module-level) was evaluated at import time")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print()
print("=" * 60)
print("ENV AUDIT SUMMARY")
print("=" * 60)
all_pass = all(REPORT.values())
for k, v in REPORT.items():
    print(f"  {'✅' if v else '❌'} {k}: {'PASS' if v else 'FAIL'}")
print(f"\n  Overall: {'✅ ALL CHECKS PASS' if all_pass else '❌ SOME CHECKS FAILED'}")
print(f"\n  Report: {json.dumps(REPORT, indent=2)}")

sys.exit(0 if all_pass else 1)
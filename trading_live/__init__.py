"""
trading_live — Top-level package for live trading, research, and model registry.

Structure:
    live/           — Live trading code (GUI, engine, MCP, logging, state, DB)
    research/       — Research pipeline (XAUUSD Liquidity Sweep)
    artifacts/      — Trained models, datasets, reports
    model_registry/ — Model registry index (YAML)
    docs/           — Documentation and decision records
    archive/        — Legacy/moved code
"""

from __future__ import annotations

__version__ = "2.0.0"
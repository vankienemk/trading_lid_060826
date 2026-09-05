#!/usr/bin/env python3
"""
run_paper_trading_v2_gui.py — Entry point for Paper Trading V2 GUI.

Launches the PySide6 (Qt for Python) desktop application with 4 tabs.

Usage:
    python run_paper_trading_v2_gui.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ---------------------------------------------------------------------------
# Check PySide6 availability
# ---------------------------------------------------------------------------
try:
    from PySide6 import QtWidgets
except ImportError:
    print("PySide6 is not installed. Installing...", file=sys.stderr)
    import subprocess
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "PySide6-Essentials"]
    )
    from PySide6 import QtWidgets

# ---------------------------------------------------------------------------
# Run the GUI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from paper_trading_v2.gui_main import main
    sys.exit(main())
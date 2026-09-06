"""
live/db/run.py — Launch the live trading GUI.

Usage:
    python -m trading_live.live.db.run          # Launch GUI from new package
    python trading_live/live/db/run.py           # Launch directly

Both start the Paper Trading V2 GUI (PySide6).
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent.parent  # trading_live/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from live.gui.gui_main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
"""
paper_trading_v2/run.py — Integration entry point (alias for root run.py).

Usage:
    python3 -m paper_trading_v2.run          # Launch from package
    python3 run.py                            # Launch from root

Both are equivalent. See run.py at project root for full documentation.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from run import main  # noqa: E402

if __name__ == "__main__":
    main()
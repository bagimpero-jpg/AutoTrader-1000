"""Allow running the backtest as ``py -m backtest``."""
from __future__ import annotations

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("backtest.log"),
    ],
)

from backtest import run_backtest

if __name__ == "__main__":
    result = run_backtest()
    print("\n" + result.recommendation)
    sys.exit(0 if result.chunks else 1)

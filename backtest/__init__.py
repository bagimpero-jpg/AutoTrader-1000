"""Auto Trader 1000 — Backtesting package.

Usage
-----
    from backtest import run_backtest
    result = run_backtest()
"""
from __future__ import annotations

from backtest.config import BacktestConfig
from backtest.engine import BacktestEngine, BacktestResult
from backtest.optimizer import ChunkedOptimizer, OptimizationResult
from backtest.report import ReportGenerator


def run_backtest(config: BacktestConfig | None = None) -> OptimizationResult:
    """Convenience entry point: fetch data → optimize → generate reports."""
    cfg = config or BacktestConfig()

    optimizer = ChunkedOptimizer(cfg)
    opt_result = optimizer.run_full_optimization()

    reporter = ReportGenerator()
    if opt_result.chunks:
        # Use the last chunk's result for detailed trade log
        last_result = opt_result.chunks[-1].result
        reporter.generate_trade_log(last_result)
        reporter.generate_equity_csv(last_result)
    reporter.generate_summary(opt_result)
    reporter.generate_final_params(opt_result)

    return opt_result


__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "ChunkedOptimizer",
    "OptimizationResult",
    "ReportGenerator",
    "run_backtest",
]

"""Report generation for backtest results — CSV trade logs, summaries, equity curves."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import yaml

from backtest.engine import BacktestResult
from backtest.optimizer import ChunkResult, OptimizationResult

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generate CSV reports and proven parameter YAML from backtest results."""

    def __init__(self, output_dir: Path | str = "backtest/reports") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_trade_log(
        self, result: BacktestResult, filename: str = "trades.csv",
    ) -> Path:
        """Write detailed CSV of every trade."""
        rows = []
        for t in result.trades:
            rows.append({
                "ticket": t.ticket,
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2),
                "sl": round(t.sl, 2),
                "tp": round(t.tp, 2),
                "volume": t.volume,
                "pnl_pips": round(t.pnl_pips, 1),
                "pnl_dollars": round(t.pnl_dollars, 2),
                "rr_achieved": round(t.rr_achieved, 2),
                "exit_reason": t.exit_reason,
                "setup_type": t.setup_type,
                "session": t.session,
                "confluence_score": t.confluence_score,
                "risk_freed": t.risk_freed,
                "reasoning": "; ".join(t.reasoning) if t.reasoning else "",
            })

        df = pd.DataFrame(rows)
        path = self.output_dir / filename
        df.to_csv(path, index=False)
        logger.info("Trade log written: %s (%d trades)", path, len(rows))
        return path

    def generate_equity_csv(
        self, result: BacktestResult, filename: str = "equity_curve.csv",
    ) -> Path:
        """Write equity curve as CSV for charting."""
        rows = [{"time": t, "equity": round(eq, 2)} for t, eq in result.equity_curve]
        df = pd.DataFrame(rows)
        path = self.output_dir / filename
        df.to_csv(path, index=False)
        logger.info("Equity curve written: %s (%d points)", path, len(rows))
        return path

    def generate_summary(
        self, opt_result: OptimizationResult, filename: str = "summary.txt",
    ) -> Path:
        """Write human-readable summary with per-chunk breakdown."""
        lines = [
            "=" * 60,
            "AUTO TRADER 1000 — BACKTEST OPTIMIZATION REPORT",
            "=" * 60,
            "",
            opt_result.recommendation,
            "",
            "-" * 60,
            "PER-CHUNK BREAKDOWN",
            "-" * 60,
        ]

        for chunk in opt_result.chunks:
            r = chunk.result
            lines.append(
                f"\nChunk {chunk.chunk_index + 1}: "
                f"{chunk.start.strftime('%Y-%m-%d')} → {chunk.end.strftime('%Y-%m-%d')}"
            )
            lines.append(
                f"  Trades: {r.total_trades} | "
                f"Win Rate: {r.win_rate:.1f}% | "
                f"Avg RR: {r.avg_rr:.2f} | "
                f"PF: {r.profit_factor:.2f} | "
                f"Max DD: {r.max_drawdown_pct:.1f}%"
            )
            lines.append(
                f"  Balance: ${r.initial_balance:.0f} → ${r.final_balance:.0f} "
                f"({r.total_return_pct:+.1f}%)"
            )
            if chunk.adjustments_made:
                lines.append(f"  Adjustments: {chunk.adjustments_made}")

        lines.extend([
            "",
            "-" * 60,
            "PARAMETER EVOLUTION",
            "-" * 60,
        ])
        for i, params in enumerate(opt_result.param_evolution):
            label = "Initial" if i == 0 else f"After chunk {i}"
            lines.append(f"  {label}: {params}")

        lines.extend([
            "",
            "-" * 60,
            "OVERALL STATS",
            "-" * 60,
        ])
        for k, v in opt_result.overall_stats.items():
            lines.append(f"  {k}: {v}")

        path = self.output_dir / filename
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Summary written: %s", path)
        return path

    def generate_final_params(
        self, opt_result: OptimizationResult, filename: str = "proven_params.yaml",
    ) -> Path:
        """Write the final optimized parameters as YAML (drop-in for settings.yaml)."""
        params = opt_result.final_params
        output = {
            "smc": {
                "ob_lookback": int(params.get("ob_lookback", 50)),
                "fvg_min_size_pips": int(params.get("fvg_min_size_pips", 5)),
                "liquidity_touch_threshold": int(params.get("liquidity_touch_threshold", 3)),
                "confluence_min_score": int(params.get("confluence_min_score", 3)),
                "swing_lookback": int(params.get("swing_lookback", 5)),
            },
            "trading": {
                "min_rr": float(params.get("min_rr", 2.0)),
                "risk_percent": 1.0,
            },
            "optimization_stats": opt_result.overall_stats,
        }

        path = self.output_dir / filename
        with open(path, "w") as f:
            yaml.dump(output, f, default_flow_style=False, sort_keys=False)

        logger.info("Proven parameters written: %s", path)
        return path

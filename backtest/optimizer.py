"""Self-learning chunked optimization loop for backtesting."""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from backtest.config import BacktestConfig
from backtest.data_fetcher import DataFetcher
from backtest.engine import BacktestEngine, BacktestResult

logger = logging.getLogger(__name__)


@dataclass
class ChunkResult:
    """Results and analysis for a single 6-month backtest chunk."""
    chunk_index: int
    start: datetime
    end: datetime
    params_used: dict
    result: BacktestResult
    adjustments_made: dict = field(default_factory=dict)
    losing_trade_analysis: dict = field(default_factory=dict)


@dataclass
class OptimizationResult:
    """Final output of the full chunked optimization."""
    chunks: list[ChunkResult]
    final_params: dict
    param_evolution: list[dict]
    overall_trades: list
    overall_stats: dict
    recommendation: str = ""


class ChunkedOptimizer:
    """Run backtests in 6-month chunks, adjusting parameters after each."""

    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "confluence_min_score": (2, 5),
        "swing_lookback": (3, 10),
        "ob_lookback": (20, 100),
        "fvg_min_size_pips": (3, 15),
        "min_rr": (1.5, 3.0),
    }

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()
        self.fetcher = DataFetcher(self.config)

    def run_full_optimization(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> OptimizationResult:
        """Split available data into chunks and run adaptive backtesting."""
        # Load all timeframes
        logger.info("Loading data for all timeframes...")
        m15_all = self.fetcher.load_or_fetch(self.config.entry_tf)
        h1_all = self.fetcher.load_or_fetch(self.config.zone_tf)
        h4_all = self.fetcher.load_or_fetch(self.config.htf_tf)

        if m15_all.empty:
            logger.error("No M15 data available. Cannot run optimization.")
            return OptimizationResult([], {}, [], [], {}, "No data available")

        # Determine date range
        data_start = m15_all.index[0].to_pydatetime()
        data_end = m15_all.index[-1].to_pydatetime()

        if start is None:
            start = data_start
        if end is None:
            end = data_end

        logger.info("Optimization range: %s to %s", start.date(), end.date())

        # Split into chunks
        chunks_ranges = self._split_into_chunks(start, end)
        logger.info("Created %d chunks of %d months each",
                     len(chunks_ranges), self.config.chunk_months)

        # Run optimization loop
        current_params = copy.deepcopy(self.config.smc_params)
        chunk_results: list[ChunkResult] = []
        param_evolution: list[dict] = [copy.deepcopy(current_params)]
        all_trades = []

        for idx, (chunk_start, chunk_end) in enumerate(chunks_ranges):
            logger.info(
                "=== Chunk %d/%d: %s to %s ===",
                idx + 1, len(chunks_ranges),
                chunk_start.date(), chunk_end.date(),
            )

            # Slice data for this chunk
            m15_chunk = self._slice_data(m15_all, chunk_start, chunk_end)
            h1_chunk = self._slice_data(h1_all, chunk_start, chunk_end)
            h4_chunk = self._slice_data(h4_all, chunk_start, chunk_end)

            if len(m15_chunk) < 500:
                logger.warning("Chunk %d has only %d M15 bars, skipping", idx, len(m15_chunk))
                continue

            # Run backtest with current params
            engine = BacktestEngine(self.config)
            result = engine.run(m15_chunk, h1_chunk, h4_chunk, params=current_params)

            all_trades.extend(result.trades)

            # Analyze and adjust
            adjustments: dict = {}
            loss_analysis: dict = {}

            needs_adjustment = (
                result.win_rate < self.config.min_win_rate
                or result.avg_rr < self.config.min_avg_rr
            )

            if needs_adjustment and result.total_trades >= 5:
                loss_analysis = self._analyze_losing_trades(result)
                adjustments = self._compute_adjustments(
                    result, loss_analysis, current_params,
                )
                current_params = self._apply_adjustments(current_params, adjustments)
                logger.info("Adjustments applied: %s", adjustments)
            elif result.total_trades < 5:
                logger.info("Too few trades (%d), lowering confluence threshold", result.total_trades)
                adjustments = {"confluence_min_score": -1}
                current_params = self._apply_adjustments(current_params, adjustments)
            else:
                logger.info(
                    "Performance OK (WR=%.1f%%, RR=%.2f). No adjustments.",
                    result.win_rate, result.avg_rr,
                )

            chunk_results.append(ChunkResult(
                chunk_index=idx,
                start=chunk_start,
                end=chunk_end,
                params_used=copy.deepcopy(current_params),
                result=result,
                adjustments_made=adjustments,
                losing_trade_analysis=loss_analysis,
            ))
            param_evolution.append(copy.deepcopy(current_params))

        # Build overall stats
        overall_stats = self._compute_overall_stats(chunk_results)

        recommendation = self._generate_recommendation(chunk_results, current_params)

        return OptimizationResult(
            chunks=chunk_results,
            final_params=current_params,
            param_evolution=param_evolution,
            overall_trades=all_trades,
            overall_stats=overall_stats,
            recommendation=recommendation,
        )

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def _analyze_losing_trades(self, result: BacktestResult) -> dict:
        """Categorize losing trades by setup type, session, and confluence."""
        analysis: dict[str, Any] = {
            "by_session": {},
            "by_setup": {},
            "by_confluence": {},
            "counter_trend_losses": 0,
            "total_losses": 0,
        }

        losers = [t for t in result.trades if t.pnl_dollars < 0 and t.exit_reason != "PARTIAL_CLOSE"]
        winners = [t for t in result.trades if t.pnl_dollars > 0 or t.exit_reason == "TP"]
        analysis["total_losses"] = len(losers)

        # By session
        for session in ("LONDON", "NEW_YORK"):
            sess_trades = [t for t in result.trades if t.session == session and t.exit_reason != "PARTIAL_CLOSE"]
            sess_losses = [t for t in losers if t.session == session]
            count = len(sess_trades)
            analysis["by_session"][session] = {
                "count": count,
                "loss_count": len(sess_losses),
                "loss_rate": len(sess_losses) / count if count > 0 else 0,
                "avg_loss_pips": (
                    sum(t.pnl_pips for t in sess_losses) / len(sess_losses)
                    if sess_losses else 0
                ),
            }

        # By setup type
        setup_types: set[str] = {t.setup_type for t in result.trades}
        for setup in setup_types:
            setup_trades = [t for t in result.trades if t.setup_type == setup and t.exit_reason != "PARTIAL_CLOSE"]
            setup_losses = [t for t in losers if t.setup_type == setup]
            count = len(setup_trades)
            analysis["by_setup"][setup] = {
                "count": count,
                "loss_count": len(setup_losses),
                "loss_rate": len(setup_losses) / count if count > 0 else 0,
            }

        # By confluence score
        for score in range(1, 6):
            score_trades = [t for t in result.trades if t.confluence_score == score and t.exit_reason != "PARTIAL_CLOSE"]
            score_losses = [t for t in losers if t.confluence_score == score]
            count = len(score_trades)
            analysis["by_confluence"][score] = {
                "count": count,
                "loss_count": len(score_losses),
                "loss_rate": len(score_losses) / count if count > 0 else 0,
            }

        return analysis

    def _compute_adjustments(
        self,
        result: BacktestResult,
        analysis: dict,
        current_params: dict,
    ) -> dict:
        """Determine parameter changes (max 2 per chunk)."""
        adjustments: dict[str, float] = {}

        # Rule 1: Very low win rate → raise confluence
        if result.win_rate < 50.0:
            adjustments["confluence_min_score"] = 1
            logger.info("Win rate %.1f%% < 50%% → raising confluence_min_score", result.win_rate)

        # Rule 2: Low RR → raise min_rr
        if result.avg_rr < 1.5 and "confluence_min_score" not in adjustments:
            adjustments["min_rr"] = 0.5
            logger.info("Avg RR %.2f < 1.5 → raising min_rr", result.avg_rr)

        # Rule 3: Low-confluence trades losing heavily
        low_conf = analysis.get("by_confluence", {}).get(3, {})
        if low_conf.get("loss_rate", 0) > 0.6 and low_conf.get("count", 0) >= 3:
            if "confluence_min_score" not in adjustments:
                adjustments["confluence_min_score"] = 1
                logger.info("Low-confluence (3) loss rate %.0f%% → raising threshold",
                            low_conf["loss_rate"] * 100)

        # Rule 4: One session significantly worse
        by_session = analysis.get("by_session", {})
        for session, data in by_session.items():
            if data.get("loss_rate", 0) > 0.7 and data.get("count", 0) >= 5:
                if len(adjustments) < 2:
                    adjustments["fvg_min_size_pips"] = 2
                    logger.info("%s session loss rate %.0f%% → widening FVG filter",
                                session, data["loss_rate"] * 100)

        # Rule 5: OB setups losing
        for setup, data in analysis.get("by_setup", {}).items():
            if "OB" in setup.upper() and data.get("loss_rate", 0) > 0.6:
                if "ob_lookback" not in adjustments and len(adjustments) < 2:
                    adjustments["ob_lookback"] = 10
                    logger.info("OB setups losing %.0f%% → increasing ob_lookback",
                                data["loss_rate"] * 100)

        # Limit to 2 adjustments
        if len(adjustments) > 2:
            keys = list(adjustments.keys())[:2]
            adjustments = {k: adjustments[k] for k in keys}

        return adjustments

    def _apply_adjustments(self, params: dict, adjustments: dict) -> dict:
        """Apply delta adjustments with bounds clamping."""
        new_params = copy.deepcopy(params)

        for key, delta in adjustments.items():
            current = new_params.get(key, 0)
            new_val = current + delta

            if key in self.PARAM_BOUNDS:
                lo, hi = self.PARAM_BOUNDS[key]
                new_val = max(lo, min(hi, new_val))

            new_params[key] = new_val if isinstance(current, float) else int(new_val)

        return new_params

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _split_into_chunks(
        self, start: datetime, end: datetime,
    ) -> list[tuple[datetime, datetime]]:
        """Split date range into N-month periods."""
        chunks = []
        current = start
        months = self.config.chunk_months

        while current < end:
            chunk_end_year = current.year + (current.month + months - 1) // 12
            chunk_end_month = (current.month + months - 1) % 12 + 1
            chunk_end = current.replace(year=chunk_end_year, month=chunk_end_month, day=1)
            chunk_end = min(chunk_end, end)
            chunks.append((current, chunk_end))
            current = chunk_end

        return chunks

    @staticmethod
    def _slice_data(
        df: pd.DataFrame, start: datetime, end: datetime,
    ) -> pd.DataFrame:
        """Slice DataFrame by date range."""
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        return df[(df.index >= start_ts) & (df.index <= end_ts)]

    def _compute_overall_stats(self, chunks: list[ChunkResult]) -> dict:
        """Aggregate stats across all chunks."""
        if not chunks:
            return {}

        all_trades = []
        for c in chunks:
            all_trades.extend(c.result.trades)

        real_trades = [t for t in all_trades if t.exit_reason != "PARTIAL_CLOSE"]
        winners = [t for t in real_trades if t.pnl_dollars > 0]
        losers = [t for t in real_trades if t.pnl_dollars <= 0]

        total = len(real_trades)
        win_rate = len(winners) / total * 100 if total > 0 else 0

        total_profit = sum(t.pnl_dollars for t in winners)
        total_loss = abs(sum(t.pnl_dollars for t in losers))
        profit_factor = total_profit / total_loss if total_loss > 0 else float("inf")

        avg_rr = (
            sum(t.rr_achieved for t in real_trades if t.rr_achieved > 0) /
            len([t for t in real_trades if t.rr_achieved > 0])
            if any(t.rr_achieved > 0 for t in real_trades) else 0
        )

        return {
            "total_trades": total,
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "avg_rr": round(avg_rr, 2),
            "total_pnl": round(sum(t.pnl_dollars for t in real_trades), 2),
            "chunks_run": len(chunks),
        }

    def _generate_recommendation(
        self, chunks: list[ChunkResult], final_params: dict,
    ) -> str:
        """Generate human-readable recommendation."""
        stats = self._compute_overall_stats(chunks)
        lines = [
            f"Optimization complete: {stats.get('chunks_run', 0)} chunks analyzed.",
            f"Overall: {stats.get('total_trades', 0)} trades, "
            f"Win Rate: {stats.get('win_rate', 0)}%, "
            f"Avg RR: {stats.get('avg_rr', 0)}, "
            f"Profit Factor: {stats.get('profit_factor', 0)}",
            f"Total PnL: ${stats.get('total_pnl', 0):.2f}",
            "",
            "Final optimized parameters:",
        ]
        for k, v in final_params.items():
            lines.append(f"  {k}: {v}")

        wr = stats.get("win_rate", 0)
        if wr >= 60:
            lines.append("\nRECOMMENDATION: Parameters are PROVEN. Safe to deploy on demo.")
        elif wr >= 50:
            lines.append("\nRECOMMENDATION: Marginal performance. More testing recommended.")
        else:
            lines.append("\nRECOMMENDATION: Strategy needs further refinement before live deployment.")

        return "\n".join(lines)

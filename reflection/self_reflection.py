from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from cloud.trade_journal import JournalEntry, TradeJournal

logger = logging.getLogger(__name__)


@dataclass
class PatternInsight:
    """Statistical insight about a recurring trade pattern."""

    pattern_name: str
    occurrences: int
    win_rate: float
    avg_pnl: float
    suggestion: str


@dataclass
class ReflectionReport:
    """Aggregated self-reflection report over a lookback window."""

    win_rate: float
    avg_rr: float
    best_setup_type: str
    worst_setup_type: str
    best_session: str
    worst_session: str
    patterns: list[PatternInsight] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


class SelfReflection:
    """Analyzes trade history to identify strengths, weaknesses, and actionable improvements."""

    def __init__(
        self,
        journal: TradeJournal,
        failing_threshold: float = 0.4,
        winning_threshold: float = 0.6,
    ) -> None:
        self._journal = journal
        self._failing_threshold = failing_threshold
        self._winning_threshold = winning_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_recent_trades(self, lookback_days: int = 7) -> ReflectionReport:
        """Produce a full reflection report for the given lookback window."""
        try:
            entries = self._get_closed_entries(lookback_days)
            if not entries:
                return ReflectionReport(
                    win_rate=0, avg_rr=0,
                    best_setup_type="N/A", worst_setup_type="N/A",
                    best_session="N/A", worst_session="N/A",
                    recommendations=["Not enough trades to analyze."],
                )

            wins = [e for e in entries if (e.pnl_pips or 0) > 0]
            win_rate = len(wins) / len(entries) * 100 if entries else 0
            avg_rr = self._mean([e.rr_achieved or 0 for e in entries])

            setup_stats = self._group_stats(entries, "smc_setup_type")
            session_stats = self._group_stats(entries, "session")

            best_setup = max(setup_stats, key=lambda s: setup_stats[s]["win_rate"], default="N/A")
            worst_setup = min(setup_stats, key=lambda s: setup_stats[s]["win_rate"], default="N/A")
            best_session = max(session_stats, key=lambda s: session_stats[s]["win_rate"], default="N/A")
            worst_session = min(session_stats, key=lambda s: session_stats[s]["win_rate"], default="N/A")

            patterns = self._build_pattern_insights(entries)
            recommendations = self._build_recommendations(
                win_rate, avg_rr, setup_stats, session_stats, patterns,
            )

            return ReflectionReport(
                win_rate=round(win_rate, 2),
                avg_rr=round(avg_rr, 2),
                best_setup_type=best_setup,
                worst_setup_type=worst_setup,
                best_session=best_session,
                worst_session=worst_session,
                patterns=patterns,
                recommendations=recommendations,
            )
        except Exception:
            logger.exception("analyze_recent_trades failed")
            return ReflectionReport(
                win_rate=0, avg_rr=0,
                best_setup_type="N/A", worst_setup_type="N/A",
                best_session="N/A", worst_session="N/A",
                recommendations=["Analysis failed due to an internal error."],
            )

    def identify_failing_patterns(self) -> list[PatternInsight]:
        """Return patterns whose win rate is below the failing threshold."""
        try:
            entries = self._get_closed_entries(lookback_days=30)
            patterns = self._build_pattern_insights(entries)
            return [p for p in patterns if p.win_rate < self._failing_threshold * 100]
        except Exception:
            logger.exception("identify_failing_patterns failed")
            return []

    def identify_winning_patterns(self) -> list[PatternInsight]:
        """Return patterns whose win rate exceeds the winning threshold."""
        try:
            entries = self._get_closed_entries(lookback_days=30)
            patterns = self._build_pattern_insights(entries)
            return [p for p in patterns if p.win_rate > self._winning_threshold * 100]
        except Exception:
            logger.exception("identify_winning_patterns failed")
            return []

    def generate_adjustment_proposal(self) -> dict:
        """Propose parameter adjustments based on current reflection data."""
        try:
            report = self.analyze_recent_trades()
            proposals: dict = {}

            if report.win_rate < 40:
                proposals["confluence_min_score"] = "increase by 1"
                proposals["min_rr"] = "increase to 2.5"

            if report.avg_rr < 1.5:
                proposals["min_rr"] = "increase to 2.0"

            failing = self.identify_failing_patterns()
            if failing:
                names = [p.pattern_name for p in failing]
                proposals["avoid_setups"] = names

            winning = self.identify_winning_patterns()
            if winning:
                names = [p.pattern_name for p in winning]
                proposals["prefer_setups"] = names

            if report.worst_session and report.worst_session != "N/A":
                proposals["reduce_session_exposure"] = report.worst_session

            return proposals
        except Exception:
            logger.exception("generate_adjustment_proposal failed")
            return {}

    def format_report_for_llm(self) -> str:
        """Format the reflection report as a prompt suitable for Claude headless mode."""
        try:
            report = self.analyze_recent_trades()
            lines = [
                "=== AUTO TRADER 1000 — SELF-REFLECTION REPORT ===",
                "",
                f"Win Rate: {report.win_rate}%",
                f"Average RR: {report.avg_rr}",
                f"Best Setup: {report.best_setup_type}",
                f"Worst Setup: {report.worst_setup_type}",
                f"Best Session: {report.best_session}",
                f"Worst Session: {report.worst_session}",
                "",
                "--- Patterns ---",
            ]
            for p in report.patterns:
                lines.append(
                    f"  {p.pattern_name}: {p.occurrences} trades, "
                    f"{p.win_rate:.1f}% win rate, avg PnL {p.avg_pnl:.2f} pips — {p.suggestion}"
                )
            lines.append("")
            lines.append("--- Recommendations ---")
            for r in report.recommendations:
                lines.append(f"  - {r}")
            lines.append("")
            lines.append(
                "Based on this data, suggest specific parameter adjustments "
                "to improve performance. Focus on risk management, setup "
                "selection, and session timing."
            )
            return "\n".join(lines)
        except Exception:
            logger.exception("format_report_for_llm failed")
            return "Reflection report generation failed."

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_closed_entries(self, lookback_days: int) -> list[JournalEntry]:
        start = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
        end = datetime.utcnow().isoformat()
        entries = self._journal.get_entries(date_range=(start, end))
        return [e for e in entries if e.closed_at]

    @staticmethod
    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _group_stats(entries: list[JournalEntry], attr: str) -> dict[str, dict]:
        groups: dict[str, list[JournalEntry]] = defaultdict(list)
        for e in entries:
            key = getattr(e, attr, "unknown") or "unknown"
            groups[key].append(e)

        stats: dict[str, dict] = {}
        for key, group in groups.items():
            wins = [e for e in group if (e.pnl_pips or 0) > 0]
            pnl_values = [e.pnl_pips or 0 for e in group]
            stats[key] = {
                "count": len(group),
                "wins": len(wins),
                "win_rate": len(wins) / len(group) * 100 if group else 0,
                "avg_pnl": sum(pnl_values) / len(pnl_values) if pnl_values else 0,
            }
        return stats

    def _build_pattern_insights(self, entries: list[JournalEntry]) -> list[PatternInsight]:
        setup_stats = self._group_stats(entries, "smc_setup_type")
        session_stats = self._group_stats(entries, "session")

        insights: list[PatternInsight] = []
        for name, s in setup_stats.items():
            suggestion = self._suggest_for_pattern(s["win_rate"], name, "setup")
            insights.append(PatternInsight(
                pattern_name=f"setup:{name}",
                occurrences=s["count"],
                win_rate=round(s["win_rate"], 2),
                avg_pnl=round(s["avg_pnl"], 2),
                suggestion=suggestion,
            ))
        for name, s in session_stats.items():
            suggestion = self._suggest_for_pattern(s["win_rate"], name, "session")
            insights.append(PatternInsight(
                pattern_name=f"session:{name}",
                occurrences=s["count"],
                win_rate=round(s["win_rate"], 2),
                avg_pnl=round(s["avg_pnl"], 2),
                suggestion=suggestion,
            ))
        return insights

    @staticmethod
    def _suggest_for_pattern(win_rate: float, name: str, kind: str) -> str:
        if win_rate >= 60:
            return f"Keep trading this {kind}; strong edge on {name}."
        if win_rate <= 40:
            return f"Consider reducing exposure to {kind} {name} or adding filters."
        return f"Neutral performance on {kind} {name}; monitor closely."

    @staticmethod
    def _build_recommendations(
        win_rate: float,
        avg_rr: float,
        setup_stats: dict,
        session_stats: dict,
        patterns: list[PatternInsight],
    ) -> list[str]:
        recs: list[str] = []
        if win_rate < 45:
            recs.append("Overall win rate is low. Tighten entry criteria or raise confluence threshold.")
        if avg_rr < 1.5:
            recs.append("Average RR is below 1.5. Widen TP targets or tighten SL placement.")

        for p in patterns:
            if p.win_rate < 35 and p.occurrences >= 3:
                recs.append(f"Pattern '{p.pattern_name}' is consistently losing. Consider pausing it.")
            if p.win_rate > 70 and p.occurrences >= 3:
                recs.append(f"Pattern '{p.pattern_name}' is a strong edge. Increase allocation.")

        if not recs:
            recs.append("Performance is within acceptable ranges. Continue current strategy.")
        return recs

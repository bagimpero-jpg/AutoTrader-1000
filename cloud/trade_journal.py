from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class JournalEntry:
    """Single trade journal entry with full lifecycle data."""

    ticket: int
    symbol: str
    direction: str
    timeframe: str
    session: str
    smc_setup_type: str
    entry_price: float
    sl: float
    tp: float
    actual_close: float | None = None
    pnl_pips: float | None = None
    pnl_dollars: float | None = None
    rr_achieved: float | None = None
    confluence_score: float = 0.0
    reasoning: str = ""
    pre_trade_bias: str = ""
    post_trade_notes: str = ""
    screenshot_ref: str = ""
    opened_at: str = ""
    closed_at: str = ""
    duration: str = ""


class TradeJournal:
    """In-memory trade journal backed by an optional CloudLogger."""

    def __init__(self, cloud_logger: Any = None) -> None:
        self._cloud_logger = cloud_logger
        self._entries: dict[int, JournalEntry] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open_entry(self, signal: dict, execution_result: dict) -> JournalEntry:
        """Create a structured journal entry when a trade is opened."""
        try:
            ticket = execution_result.get("ticket", 0)
            entry = JournalEntry(
                ticket=ticket,
                symbol=signal.get("symbol", ""),
                direction=signal.get("direction", ""),
                timeframe=signal.get("timeframe", ""),
                session=signal.get("session", ""),
                smc_setup_type=signal.get("smc_setup_type", ""),
                entry_price=execution_result.get("entry_price", 0.0),
                sl=signal.get("sl", 0.0),
                tp=signal.get("tp", 0.0),
                confluence_score=signal.get("confluence_score", 0.0),
                reasoning=signal.get("reasoning", ""),
                pre_trade_bias=signal.get("pre_trade_bias", ""),
                opened_at=datetime.now(timezone.utc).isoformat(),
            )
            self._entries[ticket] = entry

            if self._cloud_logger:
                try:
                    self._cloud_logger.log_trade_open(self._entry_to_trade_data(entry))
                except Exception:
                    logger.exception("Cloud logger failed on open_entry")

            return entry
        except Exception:
            logger.exception("open_entry failed")
            return JournalEntry(ticket=0, symbol="", direction="", timeframe="",
                                session="", smc_setup_type="", entry_price=0.0,
                                sl=0.0, tp=0.0)

    def close_entry(self, ticket: int, close_data: dict) -> JournalEntry | None:
        """Complete a journal entry when the trade is closed."""
        try:
            entry = self._entries.get(ticket)
            if entry is None:
                logger.warning("No journal entry found for ticket %s", ticket)
                return None

            entry.actual_close = close_data.get("close_price", 0.0)
            entry.pnl_pips = close_data.get("pnl_pips", 0.0)
            entry.pnl_dollars = close_data.get("pnl_dollars", 0.0)
            entry.rr_achieved = close_data.get("rr_achieved", 0.0)
            entry.post_trade_notes = close_data.get("post_trade_notes", "")
            entry.closed_at = datetime.now(timezone.utc).isoformat()

            if entry.opened_at:
                try:
                    opened = datetime.fromisoformat(entry.opened_at)
                    closed = datetime.fromisoformat(entry.closed_at)
                    delta = closed - opened
                    entry.duration = str(delta)
                except Exception:
                    entry.duration = ""

            if self._cloud_logger:
                try:
                    result = {
                        "result_pips": entry.pnl_pips,
                        "result_dollars": entry.pnl_dollars,
                        "duration": entry.duration,
                    }
                    self._cloud_logger.log_trade_close(
                        self._entry_to_trade_data(entry), result,
                    )
                except Exception:
                    logger.exception("Cloud logger failed on close_entry")

            return entry
        except Exception:
            logger.exception("close_entry failed")
            return None

    def add_screenshot_ref(self, ticket: int, screenshot_path: str) -> None:
        """Attach a screenshot reference to an existing journal entry."""
        try:
            entry = self._entries.get(ticket)
            if entry:
                entry.screenshot_ref = screenshot_path
        except Exception:
            logger.exception("add_screenshot_ref failed")

    def get_entries(
        self,
        date_range: tuple[str, str] | None = None,
        filters: dict | None = None,
    ) -> list[JournalEntry]:
        """Return journal entries matching the given date range and filters."""
        try:
            results: list[JournalEntry] = []
            for entry in self._entries.values():
                if date_range:
                    start, end = date_range
                    if not (start <= entry.opened_at <= end):
                        continue
                if filters:
                    if not all(getattr(entry, k, None) == v for k, v in filters.items()):
                        continue
                results.append(entry)
            return results
        except Exception:
            logger.exception("get_entries failed")
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _entry_to_trade_data(entry: JournalEntry) -> dict:
        return {
            "timestamp": entry.opened_at,
            "symbol": entry.symbol,
            "direction": entry.direction,
            "entry": entry.entry_price,
            "sl": entry.sl,
            "tp": entry.tp,
            "lot_size": 0.0,
            "rr_ratio": entry.rr_achieved or 0.0,
            "confluence_score": entry.confluence_score,
            "reasoning": entry.reasoning,
            "session": entry.session,
            "smc_setup_type": entry.smc_setup_type,
        }

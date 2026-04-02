"""High-impact news event filter for FTMO compliance."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

HIGH_IMPACT_EVENTS = [
    "NFP",
    "FOMC",
    "CPI",
    "PPI",
    "GDP",
    "Retail Sales",
    "Interest Rate Decision",
    "ECB Press Conference",
    "BOE Interest Rate",
    "Unemployment Claims",
    "PMI",
    "Core PCE",
]


@dataclass
class NewsEvent:
    """A scheduled high-impact news event."""
    name: str
    timestamp: datetime
    currency: str
    impact: str = "HIGH"
    buffer_minutes: int = 30


class NewsFilter:
    """Blocks trading around high-impact news events to comply with FTMO rules."""

    def __init__(self, events_path: str = "config/news_events.yaml", buffer_minutes: int = 30) -> None:
        self.buffer_minutes = buffer_minutes
        self.events: list[NewsEvent] = []
        self._load_events(events_path)

    def _load_events(self, path: str) -> None:
        events_file = Path(path)
        if not events_file.exists():
            logger.info("No news events file at %s — news filter inactive", path)
            return

        try:
            with open(events_file, "r") as f:
                data = yaml.safe_load(f)

            for entry in data.get("events", []):
                self.events.append(NewsEvent(
                    name=entry["name"],
                    timestamp=datetime.fromisoformat(entry["timestamp"]).replace(tzinfo=timezone.utc),
                    currency=entry.get("currency", "USD"),
                    impact=entry.get("impact", "HIGH"),
                    buffer_minutes=entry.get("buffer_minutes", self.buffer_minutes),
                ))
            logger.info("Loaded %d news events", len(self.events))
        except Exception:
            logger.exception("Failed to load news events from %s", path)

    def is_blocked(self, symbol: str, utc_now: datetime | None = None) -> tuple[bool, str]:
        if utc_now is None:
            utc_now = datetime.now(timezone.utc)

        symbol_currencies = self._extract_currencies(symbol)

        for event in self.events:
            if event.currency not in symbol_currencies:
                continue

            buffer = timedelta(minutes=event.buffer_minutes)
            window_start = event.timestamp - buffer
            window_end = event.timestamp + buffer

            if window_start <= utc_now <= window_end:
                reason = f"{event.name} ({event.currency}) at {event.timestamp.isoformat()}"
                logger.warning("Trading blocked for %s: %s", symbol, reason)
                return True, reason

        return False, ""

    def add_event(self, name: str, timestamp: datetime, currency: str = "USD") -> None:
        self.events.append(NewsEvent(
            name=name,
            timestamp=timestamp,
            currency=currency,
            buffer_minutes=self.buffer_minutes,
        ))

    def cleanup_past_events(self, utc_now: datetime | None = None) -> int:
        if utc_now is None:
            utc_now = datetime.now(timezone.utc)
        before = len(self.events)
        self.events = [e for e in self.events if e.timestamp + timedelta(hours=1) > utc_now]
        removed = before - len(self.events)
        if removed:
            logger.info("Cleaned up %d past news events", removed)
        return removed

    @staticmethod
    def _extract_currencies(symbol: str) -> set[str]:
        symbol = symbol.upper()
        currencies = set()
        known = ["EUR", "USD", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]
        for c in known:
            if c in symbol:
                currencies.add(c)
        if "XAU" in symbol:
            currencies.add("USD")
        return currencies

"""High-impact news event filter for FTMO compliance.

Fetches economic calendar from Forex Factory (free, no auth) and blocks
trading within a configurable buffer around high-impact USD events.
Falls back to local YAML if the API is unreachable.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

import yaml

logger = logging.getLogger(__name__)

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

HIGH_IMPACT_KEYWORDS = [
    "nonfarm", "nfp", "fomc", "cpi", "ppi", "gdp", "retail sales",
    "interest rate", "ecb", "boe", "unemployment", "pmi", "core pce",
    "fed chair", "monetary policy", "consumer confidence",
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
        self._events_path = events_path
        self._last_fetch: datetime | None = None
        self._refresh_interval = timedelta(hours=6)

        # Try live calendar first, fall back to local YAML
        fetched = self._fetch_calendar()
        if not fetched:
            self._load_events_yaml(events_path)

    # ------------------------------------------------------------------
    # Live calendar fetch
    # ------------------------------------------------------------------

    def _fetch_calendar(self) -> bool:
        """Fetch this week's economic calendar from Forex Factory. Returns True on success."""
        try:
            req = Request(CALENDAR_URL, headers={"User-Agent": "AutoTrader1000/1.0"})
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            count = 0
            for item in data:
                # Only keep high-impact events
                impact = item.get("impact", "").strip().lower()
                if impact != "high":
                    continue

                # Parse date/time
                date_str = item.get("date", "")
                if not date_str:
                    continue
                try:
                    ts = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue

                title = item.get("title", "Unknown Event")
                currency = item.get("country", "USD").upper()

                self.events.append(NewsEvent(
                    name=title,
                    timestamp=ts,
                    currency=currency,
                    impact="HIGH",
                    buffer_minutes=self.buffer_minutes,
                ))
                count += 1

            self._last_fetch = datetime.now(timezone.utc)
            if count > 0:
                logger.info("Fetched %d high-impact news events from calendar API", count)
            else:
                logger.info("Calendar API returned 0 high-impact events this week")
            return True

        except (URLError, json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to fetch calendar API: %s — falling back to local YAML", e)
            return False

    def _should_refresh(self) -> bool:
        """Return True if calendar data is stale (>6 hours old)."""
        if self._last_fetch is None:
            return True
        return datetime.now(timezone.utc) - self._last_fetch > self._refresh_interval

    def _maybe_refresh(self) -> None:
        """Refresh calendar if data is stale."""
        if self._should_refresh():
            old_count = len(self.events)
            self.events.clear()
            if not self._fetch_calendar():
                self._load_events_yaml(self._events_path)
            logger.info("Calendar refreshed: %d → %d events", old_count, len(self.events))

    # ------------------------------------------------------------------
    # Local YAML fallback
    # ------------------------------------------------------------------

    def _load_events_yaml(self, path: str) -> None:
        events_file = Path(path)
        if not events_file.exists():
            logger.warning("No news events file at %s and API failed — news filter has NO events!", path)
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
            logger.info("Loaded %d news events from local YAML", len(self.events))
        except Exception:
            logger.exception("Failed to load news events from %s", path)

    # ------------------------------------------------------------------
    # Core check
    # ------------------------------------------------------------------

    def is_blocked(self, symbol: str, utc_now: datetime | None = None) -> tuple[bool, str]:
        if utc_now is None:
            utc_now = datetime.now(timezone.utc)

        # Auto-refresh calendar every 6 hours
        self._maybe_refresh()

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

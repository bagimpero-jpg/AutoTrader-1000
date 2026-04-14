from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional

import pandas as pd


SESSION_TIMES = {
    "ASIAN":    (time(0, 0), time(8, 0)),
    "LONDON":   (time(8, 0), time(12, 0)),
    "NEW_YORK": (time(13, 0), time(17, 0)),
}

EXECUTION_SESSIONS = {"LONDON", "NEW_YORK"}


class SessionProfiler:
    """Identifies trading sessions and detects AMD (Accumulation-Manipulation-Distribution) phases."""

    def get_current_session(self, utc_now: datetime) -> str:
        """Return the active session name based on UTC time.

        Sessions: Asian 00:00-08:00, London 08:00-12:00, NY 13:00-17:00 UTC.
        Gaps between sessions return DEAD_ZONE.
        """
        t = utc_now.time()
        for name, (start, end) in SESSION_TIMES.items():
            if start <= t < end:
                return name
        return "DEAD_ZONE"

    def is_execution_allowed(self, utc_now: datetime) -> bool:
        """Return True only during London or New York sessions."""
        return self.get_current_session(utc_now) in EXECUTION_SESSIONS

    def get_asian_range(self, df: pd.DataFrame, date: datetime) -> dict:
        """Calculate the Asian session high, low, and midpoint for a given date.

        Filters the dataframe to bars between 00:00 and 08:00 UTC on `date`.
        Handles both DatetimeIndex and 'time' column DataFrames (from MT5 bridge).
        """
        start = pd.Timestamp(datetime.combine(date, time(0, 0)), tz="UTC")
        end = pd.Timestamp(datetime.combine(date, time(8, 0)), tz="UTC")

        # MT5 bridge returns 'time' as a column, not the index — handle both cases
        if "time" in df.columns:
            time_col = pd.to_datetime(df["time"], utc=True)
            mask = (time_col >= start) & (time_col < end)
        elif isinstance(df.index, pd.DatetimeIndex):
            idx = df.index
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            mask = (idx >= start) & (idx < end)
        else:
            return {"high": None, "low": None, "midpoint": None}

        session_df = df.loc[mask]

        if session_df.empty:
            return {"high": None, "low": None, "midpoint": None}

        high = float(session_df["high"].max())
        low = float(session_df["low"].min())
        return {"high": high, "low": low, "midpoint": (high + low) / 2}

    def detect_amd_phase(
        self,
        df: pd.DataFrame,
        session_range: dict,
    ) -> str:
        """Classify the current AMD phase relative to a session range.

        Accumulation: price stays within the range.
        Manipulation: price breaks above or below the range then reverses.
        Distribution: sustained directional move after manipulation.
        """
        if session_range["high"] is None:
            return "ACCUMULATION"

        if df.empty:
            return "ACCUMULATION"

        # Normalize index if needed (same fix as get_asian_range)
        if "time" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
            df = df.set_index("time")
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")

        latest = df.iloc[-1]
        high = session_range["high"]
        low = session_range["low"]

        price_above = latest["high"] > high
        price_below = latest["low"] < low
        close_inside = low <= latest["close"] <= high

        if not price_above and not price_below:
            return "ACCUMULATION"

        if (price_above or price_below) and close_inside:
            return "MANIPULATION"

        return "DISTRIBUTION"

    def get_session_bias(
        self,
        asian_range: dict,
        london_open_action: str,
    ) -> str:
        """Derive directional bias from Asian range and London open behaviour.

        If London sweeps the Asian low → BULLISH (sell-side taken, expect up).
        If London sweeps the Asian high → BEARISH (buy-side taken, expect down).
        Otherwise NEUTRAL.
        """
        if asian_range["high"] is None:
            return "NEUTRAL"

        action = london_open_action.upper()
        if action == "SWEEP_LOW":
            return "BULLISH"
        if action == "SWEEP_HIGH":
            return "BEARISH"
        return "NEUTRAL"

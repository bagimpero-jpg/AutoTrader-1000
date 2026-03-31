from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd


class SwingType(Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class BreakType(Enum):
    BOS = "BOS"
    CHOCH = "CHOCH"


class TrendDirection(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGING = "RANGING"


@dataclass
class SwingPoint:
    """A detected swing high or swing low in price data."""
    index: int
    price: float
    swing_type: SwingType
    timestamp: pd.Timestamp


@dataclass
class StructureBreak:
    """A break of market structure (BOS or CHOCH)."""
    break_type: BreakType
    level: float
    timestamp: pd.Timestamp
    direction: TrendDirection


class StructureAnalyzer:
    """Detects market structure: swing points, BOS, CHOCH, and current trend."""

    def detect_swing_points(self, df: pd.DataFrame, lookback: int = 5) -> list[SwingPoint]:
        """Identify swing highs and swing lows using a rolling lookback window.

        A swing high is a bar whose high is the highest within `lookback` bars
        on each side.  A swing low is the mirror image using the low column.
        """
        swing_points: list[SwingPoint] = []
        highs = df["high"].values
        lows = df["low"].values

        for i in range(lookback, len(df) - lookback):
            # Swing high: current high is the max in the window
            window_highs = highs[i - lookback: i + lookback + 1]
            if highs[i] == window_highs.max() and list(window_highs).count(highs[i]) == 1:
                swing_points.append(SwingPoint(
                    index=i,
                    price=float(highs[i]),
                    swing_type=SwingType.HIGH,
                    timestamp=pd.Timestamp(df.index[i]),
                ))

            # Swing low: current low is the min in the window
            window_lows = lows[i - lookback: i + lookback + 1]
            if lows[i] == window_lows.min() and list(window_lows).count(lows[i]) == 1:
                swing_points.append(SwingPoint(
                    index=i,
                    price=float(lows[i]),
                    swing_type=SwingType.LOW,
                    timestamp=pd.Timestamp(df.index[i]),
                ))

        swing_points.sort(key=lambda sp: sp.index)
        return swing_points

    def detect_bos(
        self,
        swing_points: list[SwingPoint],
        direction: TrendDirection,
    ) -> list[StructureBreak]:
        """Detect Break of Structure events that continue the current trend.

        Bullish BOS: price breaks above a prior swing high.
        Bearish BOS: price breaks below a prior swing low.
        """
        breaks: list[StructureBreak] = []
        swing_highs = [sp for sp in swing_points if sp.swing_type == SwingType.HIGH]
        swing_lows = [sp for sp in swing_points if sp.swing_type == SwingType.LOW]

        if direction == TrendDirection.BULLISH:
            for i in range(1, len(swing_highs)):
                if swing_highs[i].price > swing_highs[i - 1].price:
                    breaks.append(StructureBreak(
                        break_type=BreakType.BOS,
                        level=swing_highs[i - 1].price,
                        timestamp=swing_highs[i].timestamp,
                        direction=TrendDirection.BULLISH,
                    ))
        elif direction == TrendDirection.BEARISH:
            for i in range(1, len(swing_lows)):
                if swing_lows[i].price < swing_lows[i - 1].price:
                    breaks.append(StructureBreak(
                        break_type=BreakType.BOS,
                        level=swing_lows[i - 1].price,
                        timestamp=swing_lows[i].timestamp,
                        direction=TrendDirection.BEARISH,
                    ))

        return breaks

    def detect_choch(
        self,
        swing_points: list[SwingPoint],
        current_trend: TrendDirection,
    ) -> Optional[StructureBreak]:
        """Detect Change of Character — the first break against the current trend.

        In a bullish trend a CHOCH occurs when price breaks below the most
        recent swing low.  In a bearish trend it occurs when price breaks above
        the most recent swing high.
        """
        if current_trend == TrendDirection.RANGING:
            return None

        swing_highs = [sp for sp in swing_points if sp.swing_type == SwingType.HIGH]
        swing_lows = [sp for sp in swing_points if sp.swing_type == SwingType.LOW]

        if current_trend == TrendDirection.BULLISH and len(swing_lows) >= 2:
            for i in range(1, len(swing_lows)):
                if swing_lows[i].price < swing_lows[i - 1].price:
                    return StructureBreak(
                        break_type=BreakType.CHOCH,
                        level=swing_lows[i - 1].price,
                        timestamp=swing_lows[i].timestamp,
                        direction=TrendDirection.BEARISH,
                    )

        if current_trend == TrendDirection.BEARISH and len(swing_highs) >= 2:
            for i in range(1, len(swing_highs)):
                if swing_highs[i].price > swing_highs[i - 1].price:
                    return StructureBreak(
                        break_type=BreakType.CHOCH,
                        level=swing_highs[i - 1].price,
                        timestamp=swing_highs[i].timestamp,
                        direction=TrendDirection.BULLISH,
                    )

        return None

    def get_current_trend(self, swing_points: list[SwingPoint]) -> TrendDirection:
        """Determine trend from the last two swing highs and two swing lows.

        Bullish: higher highs and higher lows.
        Bearish: lower highs and lower lows.
        Otherwise: ranging.
        """
        swing_highs = [sp for sp in swing_points if sp.swing_type == SwingType.HIGH]
        swing_lows = [sp for sp in swing_points if sp.swing_type == SwingType.LOW]

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return TrendDirection.RANGING

        hh = swing_highs[-1].price > swing_highs[-2].price
        hl = swing_lows[-1].price > swing_lows[-2].price
        lh = swing_highs[-1].price < swing_highs[-2].price
        ll = swing_lows[-1].price < swing_lows[-2].price

        if hh and hl:
            return TrendDirection.BULLISH
        if lh and ll:
            return TrendDirection.BEARISH
        return TrendDirection.RANGING

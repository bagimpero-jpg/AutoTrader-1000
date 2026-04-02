from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd

from strategy.structures import BreakType, StructureBreak, SwingPoint, SwingType, TrendDirection


class ZoneType(Enum):
    BULLISH_OB = "BULLISH_OB"
    BEARISH_OB = "BEARISH_OB"
    BULLISH_FVG = "BULLISH_FVG"
    BEARISH_FVG = "BEARISH_FVG"
    BULLISH_BREAKER = "BULLISH_BREAKER"
    BEARISH_BREAKER = "BEARISH_BREAKER"
    FLIP_ZONE = "FLIP_ZONE"


class ZoneStatus(Enum):
    FRESH = "FRESH"
    TESTED = "TESTED"
    BROKEN = "BROKEN"


@dataclass
class Zone:
    """A price zone derived from SMC analysis (order block, breaker, flip zone)."""
    high: float
    low: float
    midpoint: float
    zone_type: ZoneType
    status: ZoneStatus
    timestamp: pd.Timestamp


@dataclass
class FVG:
    """A Fair Value Gap identified from a 3-candle pattern."""
    high: float
    low: float
    midpoint: float
    fvg_type: ZoneType
    status: ZoneStatus
    index: int
    timestamp: pd.Timestamp


class ZoneDetector:
    """Detects SMC zones: order blocks, fair value gaps, breaker blocks, and flip zones."""

    def detect_order_blocks(
        self,
        df: pd.DataFrame,
        swing_points: list[SwingPoint],
        structure_breaks: list[StructureBreak],
    ) -> list[Zone]:
        """Find order blocks — the last opposite candle before a BOS.

        A bullish OB is the last bearish candle before a bullish BOS.
        A bearish OB is the last bullish candle before a bearish BOS.
        """
        zones: list[Zone] = []

        for sb in structure_breaks:
            if sb.break_type != BreakType.BOS:
                continue

            # Find the bar index of the break timestamp
            break_mask = df.index == sb.timestamp
            if not break_mask.any():
                continue
            break_idx = int(break_mask.argmax())

            if sb.direction == TrendDirection.BULLISH:
                # Walk backward to find the last bearish candle before the break
                for i in range(break_idx - 1, max(break_idx - 20, -1), -1):
                    if df.iloc[i]["close"] < df.iloc[i]["open"]:
                        zones.append(Zone(
                            high=float(df.iloc[i]["high"]),
                            low=float(df.iloc[i]["low"]),
                            midpoint=float((df.iloc[i]["high"] + df.iloc[i]["low"]) / 2),
                            zone_type=ZoneType.BULLISH_OB,
                            status=ZoneStatus.FRESH,
                            timestamp=pd.Timestamp(df.index[i]),
                        ))
                        break

            elif sb.direction == TrendDirection.BEARISH:
                for i in range(break_idx - 1, max(break_idx - 20, -1), -1):
                    if df.iloc[i]["close"] > df.iloc[i]["open"]:
                        zones.append(Zone(
                            high=float(df.iloc[i]["high"]),
                            low=float(df.iloc[i]["low"]),
                            midpoint=float((df.iloc[i]["high"] + df.iloc[i]["low"]) / 2),
                            zone_type=ZoneType.BEARISH_OB,
                            status=ZoneStatus.FRESH,
                            timestamp=pd.Timestamp(df.index[i]),
                        ))
                        break

        return zones

    def detect_fvg(self, df: pd.DataFrame) -> list[FVG]:
        """Detect Fair Value Gaps from 3-candle imbalance patterns.

        Bullish FVG: candle1.high < candle3.low (gap up).
        Bearish FVG: candle1.low > candle3.high (gap down).
        """
        fvgs: list[FVG] = []

        for i in range(2, len(df)):
            c1 = df.iloc[i - 2]
            c3 = df.iloc[i]

            # Bullish FVG
            if c1["high"] < c3["low"]:
                gap_high = float(c3["low"])
                gap_low = float(c1["high"])
                fvgs.append(FVG(
                    high=gap_high,
                    low=gap_low,
                    midpoint=(gap_high + gap_low) / 2,
                    fvg_type=ZoneType.BULLISH_FVG,
                    status=ZoneStatus.FRESH,
                    index=i - 1,
                    timestamp=pd.Timestamp(df.index[i - 1]),
                ))

            # Bearish FVG
            if c1["low"] > c3["high"]:
                gap_high = float(c1["low"])
                gap_low = float(c3["high"])
                fvgs.append(FVG(
                    high=gap_high,
                    low=gap_low,
                    midpoint=(gap_high + gap_low) / 2,
                    fvg_type=ZoneType.BEARISH_FVG,
                    status=ZoneStatus.FRESH,
                    index=i - 1,
                    timestamp=pd.Timestamp(df.index[i - 1]),
                ))

        return fvgs

    def detect_breaker_blocks(
        self,
        order_blocks: list[Zone],
        structure_breaks: list[StructureBreak],
    ) -> list[Zone]:
        """Detect breaker blocks — order blocks that failed and flipped polarity.

        A bullish OB that gets broken becomes a bearish breaker, and vice versa.
        """
        breakers: list[Zone] = []

        for ob in order_blocks:
            for sb in structure_breaks:
                if sb.timestamp <= ob.timestamp:
                    continue

                # Bullish OB broken by bearish move → bearish breaker
                if (
                    ob.zone_type == ZoneType.BULLISH_OB
                    and sb.direction == TrendDirection.BEARISH
                    and sb.level < ob.low
                ):
                    breakers.append(Zone(
                        high=ob.high,
                        low=ob.low,
                        midpoint=ob.midpoint,
                        zone_type=ZoneType.BEARISH_BREAKER,
                        status=ZoneStatus.FRESH,
                        timestamp=ob.timestamp,
                    ))
                    break

                # Bearish OB broken by bullish move → bullish breaker
                if (
                    ob.zone_type == ZoneType.BEARISH_OB
                    and sb.direction == TrendDirection.BULLISH
                    and sb.level > ob.high
                ):
                    breakers.append(Zone(
                        high=ob.high,
                        low=ob.low,
                        midpoint=ob.midpoint,
                        zone_type=ZoneType.BULLISH_BREAKER,
                        status=ZoneStatus.FRESH,
                        timestamp=ob.timestamp,
                    ))
                    break

        return breakers

    def detect_flip_zones(
        self,
        df: pd.DataFrame,
        key_levels: list[float],
    ) -> list[Zone]:
        """Detect flip zones — levels that acted as both support and resistance.

        A key level becomes a flip zone if price has crossed it in both
        directions within the data.
        """
        zones: list[Zone] = []
        closes = df["close"].values

        for level in key_levels:
            above_count = 0
            below_count = 0
            crossings = 0
            prev_side: Optional[str] = None

            for close_val in closes:
                side = "above" if close_val > level else "below"
                if prev_side is not None and side != prev_side:
                    crossings += 1
                if side == "above":
                    above_count += 1
                else:
                    below_count += 1
                prev_side = side

            if crossings >= 2 and above_count > 0 and below_count > 0:
                tolerance = df["high"].mean() * 0.0005  # 0.05% band
                zones.append(Zone(
                    high=level + tolerance,
                    low=level - tolerance,
                    midpoint=level,
                    zone_type=ZoneType.FLIP_ZONE,
                    status=ZoneStatus.FRESH,
                    timestamp=pd.Timestamp(df.index[-1]),
                ))

        return zones

    def check_zone_mitigation(
        self,
        zones: list[Zone],
        current_price: float,
    ) -> list[Zone]:
        """Update zone statuses based on how current price relates to each zone.

        FRESH → TESTED when price touches the zone.
        TESTED → BROKEN when price closes through the zone.
        """
        for zone in zones:
            if zone.status == ZoneStatus.BROKEN:
                continue

            price_in_zone = zone.low <= current_price <= zone.high

            if zone.zone_type in (ZoneType.BULLISH_OB, ZoneType.BULLISH_FVG, ZoneType.BULLISH_BREAKER):
                if current_price < zone.low:
                    zone.status = ZoneStatus.BROKEN
                elif price_in_zone and zone.status == ZoneStatus.FRESH:
                    zone.status = ZoneStatus.TESTED

            elif zone.zone_type in (ZoneType.BEARISH_OB, ZoneType.BEARISH_FVG, ZoneType.BEARISH_BREAKER):
                if current_price > zone.high:
                    zone.status = ZoneStatus.BROKEN
                elif price_in_zone and zone.status == ZoneStatus.FRESH:
                    zone.status = ZoneStatus.TESTED

            elif zone.zone_type == ZoneType.FLIP_ZONE:
                if price_in_zone and zone.status == ZoneStatus.FRESH:
                    zone.status = ZoneStatus.TESTED

        return zones

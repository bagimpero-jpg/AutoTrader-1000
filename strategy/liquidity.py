from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from strategy.structures import SwingPoint, SwingType


class PoolType(Enum):
    BSL = "BSL"  # Buy-side liquidity (above swing highs)
    SSL = "SSL"  # Sell-side liquidity (below swing lows)


@dataclass
class LiquidityPool:
    """A cluster of resting orders at a key price level."""
    level: float
    pool_type: PoolType
    strength: int  # number of swing points forming the cluster


@dataclass
class SweepEvent:
    """Records when price sweeps through a liquidity pool."""
    pool: LiquidityPool
    sweep_candle_index: int
    swept: bool


@dataclass
class EqualLevel:
    """Two or more swing points at approximately the same price."""
    level: float
    pool_type: PoolType
    points: list[SwingPoint]


class LiquidityAnalyzer:
    """Identifies liquidity pools, sweep events, and equal highs/lows."""

    def detect_liquidity_pools(
        self,
        swing_points: list[SwingPoint],
        threshold: int = 3,
    ) -> list[LiquidityPool]:
        """Cluster nearby swing highs / lows into liquidity pools.

        Groups swing points whose prices fall within a small tolerance band.
        A pool is created when `threshold` or more points cluster together.
        """
        pools: list[LiquidityPool] = []

        highs = sorted(
            [sp for sp in swing_points if sp.swing_type == SwingType.HIGH],
            key=lambda sp: sp.price,
        )
        lows = sorted(
            [sp for sp in swing_points if sp.swing_type == SwingType.LOW],
            key=lambda sp: sp.price,
        )

        for group, pool_type in [(highs, PoolType.BSL), (lows, PoolType.SSL)]:
            if not group:
                continue
            avg_price = sum(sp.price for sp in group) / len(group)
            tolerance = avg_price * 0.001  # 0.1 % band

            clusters: list[list[SwingPoint]] = []
            current_cluster: list[SwingPoint] = [group[0]]

            for sp in group[1:]:
                if sp.price - current_cluster[0].price <= tolerance:
                    current_cluster.append(sp)
                else:
                    clusters.append(current_cluster)
                    current_cluster = [sp]
            clusters.append(current_cluster)

            for cluster in clusters:
                if len(cluster) >= threshold:
                    avg = sum(sp.price for sp in cluster) / len(cluster)
                    pools.append(LiquidityPool(
                        level=avg,
                        pool_type=pool_type,
                        strength=len(cluster),
                    ))

        return pools

    def detect_liquidity_sweep(
        self,
        df: pd.DataFrame,
        liquidity_pools: list[LiquidityPool],
    ) -> list[SweepEvent]:
        """Detect candles that sweep through a liquidity pool then reverse.

        A sweep occurs when a candle's wick exceeds the pool level but the
        close stays on the original side, indicating a stop-hunt.
        """
        events: list[SweepEvent] = []

        for pool in liquidity_pools:
            for i in range(len(df)):
                row = df.iloc[i]

                if pool.pool_type == PoolType.BSL:
                    # Wick above the level, close below — sweep of buy-side
                    if row["high"] > pool.level and row["close"] < pool.level:
                        events.append(SweepEvent(
                            pool=pool,
                            sweep_candle_index=i,
                            swept=True,
                        ))
                        break  # one sweep per pool

                elif pool.pool_type == PoolType.SSL:
                    # Wick below the level, close above — sweep of sell-side
                    if row["low"] < pool.level and row["close"] > pool.level:
                        events.append(SweepEvent(
                            pool=pool,
                            sweep_candle_index=i,
                            swept=True,
                        ))
                        break

        return events

    def detect_equal_highs_lows(
        self,
        swing_points: list[SwingPoint],
        tolerance_pips: float = 3.0,
    ) -> list[EqualLevel]:
        """Find clusters of swing highs or lows at nearly identical prices.

        Equal highs / lows signal resting liquidity that smart money may
        target.  `tolerance_pips` is the maximum pip distance between points
        to consider them equal.
        """
        pip_value = 0.0001  # standard for most FX pairs
        tolerance = tolerance_pips * pip_value

        levels: list[EqualLevel] = []

        for swing_type, pool_type in [
            (SwingType.HIGH, PoolType.BSL),
            (SwingType.LOW, PoolType.SSL),
        ]:
            filtered = sorted(
                [sp for sp in swing_points if sp.swing_type == swing_type],
                key=lambda sp: sp.price,
            )
            if len(filtered) < 2:
                continue

            used: set[int] = set()
            for i, sp_a in enumerate(filtered):
                if i in used:
                    continue
                cluster = [sp_a]
                for j in range(i + 1, len(filtered)):
                    if j in used:
                        continue
                    if abs(filtered[j].price - sp_a.price) <= tolerance:
                        cluster.append(filtered[j])
                        used.add(j)

                if len(cluster) >= 2:
                    avg = sum(sp.price for sp in cluster) / len(cluster)
                    levels.append(EqualLevel(
                        level=avg,
                        pool_type=pool_type,
                        points=cluster,
                    ))
                    used.add(i)

        return levels

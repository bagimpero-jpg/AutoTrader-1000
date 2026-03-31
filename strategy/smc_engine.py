from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from strategy.structures import (
    BreakType,
    StructureAnalyzer,
    StructureBreak,
    SwingPoint,
    TrendDirection,
)
from strategy.zones import FVG, Zone, ZoneDetector, ZoneStatus, ZoneType
from strategy.liquidity import (
    LiquidityAnalyzer,
    LiquidityPool,
    SweepEvent,
)
from strategy.session_profiler import SessionProfiler


@dataclass
class AnalysisResult:
    """Complete SMC analysis output for a symbol/timeframe."""
    trend: TrendDirection
    structure_breaks: list[StructureBreak]
    active_zones: list[Zone]
    liquidity_pools: list[LiquidityPool]
    sweeps: list[SweepEvent]
    session_info: str
    amd_phase: str
    fvgs: list[FVG] = field(default_factory=list)
    swing_points: list[SwingPoint] = field(default_factory=list)


@dataclass
class TradeSignal:
    """A generated trade signal with full reasoning."""
    symbol: str
    direction: TrendDirection
    entry_price: float
    sl: float
    tp: float
    rr_ratio: float
    confluence_score: int  # 1-5
    reasoning: list[str]


class SMCEngine:
    """Main orchestrator that combines all SMC components to produce trade signals."""

    def __init__(
        self,
        structure_analyzer: StructureAnalyzer | None = None,
        zone_detector: ZoneDetector | None = None,
        liquidity_analyzer: LiquidityAnalyzer | None = None,
        session_profiler: SessionProfiler | None = None,
    ) -> None:
        self.structure = structure_analyzer or StructureAnalyzer()
        self.zones = zone_detector or ZoneDetector()
        self.liquidity = liquidity_analyzer or LiquidityAnalyzer()
        self.session = session_profiler or SessionProfiler()
        self.knowledge: list[dict] = []

    # ------------------------------------------------------------------
    # Knowledge base
    # ------------------------------------------------------------------

    def load_knowledge_base(self, path: str | Path) -> None:
        """Read all .md files from `path` and parse them into structured dicts.

        Each file is expected to follow the template with sections:
        Definition, Entry Rules, Exit Rules, Confluence Factors, Session Context.
        """
        kb_path = Path(path)
        self.knowledge = []

        if not kb_path.exists():
            return

        for md_file in sorted(kb_path.glob("*.md")):
            if md_file.name in ("README.md", "template.md"):
                continue
            text = md_file.read_text(encoding="utf-8")
            entry: dict = {"file": md_file.name, "raw": text}

            # Extract concept name
            match = re.search(r"#\s*Concept:\s*(.+)", text)
            if match:
                entry["concept"] = match.group(1).strip()

            # Extract sections
            for section in (
                "Definition",
                "Entry Rules",
                "Exit Rules",
                "Confluence Factors",
                "Session Context",
            ):
                pattern = rf"##\s*{section}\s*\n(.*?)(?=\n##|\Z)"
                sec_match = re.search(pattern, text, re.DOTALL)
                if sec_match:
                    entry[section.lower().replace(" ", "_")] = sec_match.group(1).strip()

            self.knowledge.append(entry)

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
    ) -> AnalysisResult:
        """Run the full SMC analysis pipeline on the provided OHLCV dataframe."""
        # 1. Structure
        swing_points = self.structure.detect_swing_points(df)
        trend = self.structure.get_current_trend(swing_points)
        bos_list = self.structure.detect_bos(swing_points, trend)
        choch = self.structure.detect_choch(swing_points, trend)

        structure_breaks = list(bos_list)
        if choch is not None:
            structure_breaks.append(choch)

        # 2. Zones
        order_blocks = self.zones.detect_order_blocks(df, swing_points, structure_breaks)
        fvgs = self.zones.detect_fvg(df)
        breakers = self.zones.detect_breaker_blocks(order_blocks, structure_breaks)

        current_price = float(df.iloc[-1]["close"])
        all_zones = order_blocks + breakers
        self.zones.check_zone_mitigation(all_zones, current_price)
        active_zones = [z for z in all_zones if z.status != ZoneStatus.BROKEN]

        # 3. Liquidity
        pools = self.liquidity.detect_liquidity_pools(swing_points)
        sweeps = self.liquidity.detect_liquidity_sweep(df, pools)

        # 4. Session
        utc_now = datetime.now(timezone.utc)
        session_info = self.session.get_current_session(utc_now)
        asian_range = self.session.get_asian_range(df, utc_now)
        amd_phase = self.session.detect_amd_phase(df, asian_range)

        return AnalysisResult(
            trend=trend,
            structure_breaks=structure_breaks,
            active_zones=active_zones,
            liquidity_pools=pools,
            sweeps=sweeps,
            session_info=session_info,
            amd_phase=amd_phase,
            fvgs=fvgs,
            swing_points=swing_points,
        )

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def generate_signals(
        self, analysis: AnalysisResult, symbol: str = "", timeframe: str = ""
    ) -> list[TradeSignal]:
        """Produce trade signals from an AnalysisResult.

        Requires confluence of: OB/FVG zone + BOS/CHOCH + liquidity sweep
        + valid session.  Minimum 1:2 risk/reward.  Only during London/NY.
        """
        signals: list[TradeSignal] = []

        # Only trade during execution sessions
        if analysis.session_info not in ("LONDON", "NEW_YORK"):
            return signals

        has_sweep = len(analysis.sweeps) > 0
        has_structure = len(analysis.structure_breaks) > 0

        for zone in analysis.active_zones:
            reasoning: list[str] = []
            score = 0

            # ---- Direction alignment ----
            if zone.zone_type in (ZoneType.BULLISH_OB, ZoneType.BULLISH_BREAKER):
                direction = TrendDirection.BULLISH
            elif zone.zone_type in (ZoneType.BEARISH_OB, ZoneType.BEARISH_BREAKER):
                direction = TrendDirection.BEARISH
            else:
                continue

            # ---- Confluence scoring ----
            # 1. Zone present
            score += 1
            reasoning.append(f"Active {zone.zone_type.value} zone at {zone.midpoint:.5f}")

            # 2. Trend alignment
            if analysis.trend == direction:
                score += 1
                reasoning.append(f"Trend aligned: {analysis.trend.value}")

            # 3. Structure break confirmation
            if has_structure:
                latest_break = analysis.structure_breaks[-1]
                if latest_break.direction == direction:
                    score += 1
                    reasoning.append(
                        f"{latest_break.break_type.value} confirmed at {latest_break.level:.5f}"
                    )

            # 4. Liquidity sweep
            if has_sweep:
                score += 1
                reasoning.append("Liquidity sweep detected before entry zone")

            # 5. FVG overlap
            for fvg in analysis.fvgs:
                overlaps = fvg.low <= zone.high and fvg.high >= zone.low
                fvg_aligned = (
                    (direction == TrendDirection.BULLISH and fvg.fvg_type == ZoneType.BULLISH_FVG)
                    or (direction == TrendDirection.BEARISH and fvg.fvg_type == ZoneType.BEARISH_FVG)
                )
                if overlaps and fvg_aligned:
                    score += 1
                    reasoning.append("FVG overlaps with entry zone")
                    break

            score = min(score, 5)

            # ---- Entry / SL / TP ----
            if direction == TrendDirection.BULLISH:
                entry_price = zone.midpoint
                sl = zone.low - (zone.high - zone.low) * 0.1
                risk = entry_price - sl
                tp = entry_price + risk * 2  # minimum 1:2 RR
            else:
                entry_price = zone.midpoint
                sl = zone.high + (zone.high - zone.low) * 0.1
                risk = sl - entry_price
                tp = entry_price - risk * 2

            if risk <= 0:
                continue

            rr = abs(entry_price - tp) / risk

            if rr < 2.0:
                continue

            signals.append(TradeSignal(
                symbol=symbol,
                direction=direction,
                entry_price=round(entry_price, 5),
                sl=round(sl, 5),
                tp=round(tp, 5),
                rr_ratio=round(rr, 2),
                confluence_score=score,
                reasoning=reasoning,
            ))

        # Sort by confluence descending
        signals.sort(key=lambda s: s.confluence_score, reverse=True)
        return signals

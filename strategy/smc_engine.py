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
    SwingType,
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
    htf_level_confirmed: bool = False
    counter_trend: bool = False
    timeframe: str = ""


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
    # Fibonacci OTE zone check
    # ------------------------------------------------------------------

    @staticmethod
    def _is_in_ote_zone(
        entry_price: float,
        swing_low: float,
        swing_high: float,
        direction: TrendDirection,
    ) -> bool:
        """Check if *entry_price* sits in the 0.618-0.66 Fibonacci retracement
        zone (Optimal Trade Entry).

        For a bullish setup the retracement is measured from swing_high down
        toward swing_low.  For bearish, from swing_low up toward swing_high.

        Returns True when the price falls within the OTE band.
        """
        span = swing_high - swing_low
        if span <= 0:
            return False

        if direction == TrendDirection.BULLISH:
            # Retracement measured downward from the high
            ote_upper = swing_high - 0.618 * span  # 0.618 level
            ote_lower = swing_high - 0.66 * span   # 0.66  level
            return ote_lower <= entry_price <= ote_upper
        else:
            # Retracement measured upward from the low
            ote_lower = swing_low + 0.618 * span
            ote_upper = swing_low + 0.66 * span
            return ote_lower <= entry_price <= ote_upper

    @staticmethod
    def _is_in_premium_discount_skip_zone(
        entry_price: float,
        swing_low: float,
        swing_high: float,
        direction: TrendDirection,
    ) -> bool:
        """Return True when the POI sits in the unfavourable half of the range.

        For buys: skip if the entry is above the 0.5 level (premium zone).
        For sells: skip if the entry is below the 0.5 level (discount zone).
        """
        span = swing_high - swing_low
        if span <= 0:
            return False

        midpoint = swing_low + 0.5 * span

        if direction == TrendDirection.BULLISH:
            return entry_price > midpoint  # buying in premium — skip
        else:
            return entry_price < midpoint  # selling in discount — skip

    # ------------------------------------------------------------------
    # 2nd Break Rule (Anti-Trap) helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _count_counter_trend_breaks(
        structure_breaks: list[StructureBreak],
        counter_direction: TrendDirection,
    ) -> int:
        """Count how many breaks exist in the given *counter_direction*.

        Used by the 2nd-break anti-trap rule to ensure there are at least 2
        breaks in the counter-trend direction before confirming a bias shift.
        """
        return sum(
            1 for sb in structure_breaks
            if sb.direction == counter_direction
        )

    # ------------------------------------------------------------------
    # A+ Order Block validation
    # ------------------------------------------------------------------

    @staticmethod
    def _is_a_plus_ob(
        zone: Zone,
        analysis: AnalysisResult,
    ) -> bool:
        """Validate whether an order block qualifies as A+ grade.

        An OB is A+ if at least one of the following is true:
          Scenario A — there is a consolidation range (>= 3 candles between OB
                       formation and the nearest swing point after it), meaning
                       price did NOT V-shape straight back.
          Scenario B — the impulsive move that created the OB was preceded by a
                       liquidity sweep.

        Returns True when at least one scenario is satisfied.
        """
        # Scenario B: liquidity sweep before the OB
        sweep_before_ob = any(
            sweep.pool.level != 0  # valid sweep exists
            for sweep in analysis.sweeps
        )
        if sweep_before_ob:
            return True

        # Scenario A: consolidation between OB and current structure
        # If there are at least 3 swing points after the OB timestamp we treat
        # that as evidence of a consolidation range (not a V-shape).
        swings_after_ob = [
            sp for sp in analysis.swing_points
            if sp.timestamp > zone.timestamp
        ]
        if len(swings_after_ob) >= 3:
            return True

        return False

    # ------------------------------------------------------------------
    # LTF structure shift confirmation
    # ------------------------------------------------------------------

    @staticmethod
    def _has_recent_ltf_structure_shift(
        structure_breaks: list[StructureBreak],
        direction: TrendDirection,
        lookback: int = 5,
    ) -> bool:
        """Check that there is a recent BOS or CHOCH in *direction* among the
        last *lookback* structure breaks.  This serves as Step 4 of the entry
        funnel — LTF confirmation.
        """
        recent = structure_breaks[-lookback:] if len(structure_breaks) > lookback else structure_breaks
        return any(sb.direction == direction for sb in recent)

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def generate_signals(
        self, analysis: AnalysisResult, symbol: str = "", timeframe: str = "",
        htf_zones: list[Zone] | None = None,
    ) -> list[TradeSignal]:
        """Produce trade signals from an AnalysisResult.

        Implements the full Entry Funnel 4-step check:
          Step 1 — Trend determined
          Step 2 — Zone / POI identified
          Step 3 — Session is London or NY
          Step 4 — LTF structure shift confirmed (recent BOS/CHOCH)

        Additional Hasan methodology filters:
          - 2nd Break Rule (anti-trap) for CHOCH bias shifts
          - A+ Order Block grading
          - Fibonacci OTE zone confluence
          - HTF nesting requirement
          - Counter-trend risk reduction

        Parameters
        ----------
        analysis : AnalysisResult
            Output from ``self.analyze()``.
        symbol : str
            Instrument identifier.
        timeframe : str
            Timeframe label (e.g. "M15", "H1").
        htf_zones : list[Zone] | None
            Higher-timeframe zones for nesting validation.  When provided,
            signals whose POI overlaps an HTF zone receive extra confluence.
        """
        signals: list[TradeSignal] = []

        # ----------------------------------------------------------------
        # Step 1 — Trend determined
        # ----------------------------------------------------------------
        if analysis.trend == TrendDirection.RANGING:
            return signals

        # ----------------------------------------------------------------
        # Step 3 — Only trade during execution sessions (London / NY)
        # ----------------------------------------------------------------
        if analysis.session_info not in ("LONDON", "NEW_YORK"):
            return signals

        # ----------------------------------------------------------------
        # 2nd Break Rule (Anti-Trap): If there's a CHOCH, ensure at least 2
        # counter-trend breaks before treating the bias shift as confirmed.
        # A single break is flagged as Pending_Bias_Shift only.
        # ----------------------------------------------------------------
        choch_breaks = [
            sb for sb in analysis.structure_breaks
            if sb.break_type == BreakType.CHOCH
        ]
        bias_shift_confirmed = True
        pending_bias_direction: TrendDirection | None = None

        if choch_breaks:
            latest_choch = choch_breaks[-1]
            counter_dir = latest_choch.direction
            counter_count = self._count_counter_trend_breaks(
                analysis.structure_breaks, counter_dir,
            )
            if counter_count < 2:
                # First break only — not enough to confirm the shift
                bias_shift_confirmed = False
                pending_bias_direction = counter_dir

        has_sweep = len(analysis.sweeps) > 0
        has_structure = len(analysis.structure_breaks) > 0

        # Derive the most recent swing range for Fibonacci checks
        swing_highs = [sp for sp in analysis.swing_points if sp.swing_type == SwingType.HIGH]
        swing_lows = [sp for sp in analysis.swing_points if sp.swing_type == SwingType.LOW]
        last_swing_high = swing_highs[-1].price if swing_highs else None
        last_swing_low = swing_lows[-1].price if swing_lows else None

        for zone in analysis.active_zones:
            reasoning: list[str] = []
            score = 0

            # ---- Step 2 — Zone / POI identified ----
            # ---- Direction alignment ----
            if zone.zone_type in (ZoneType.BULLISH_OB, ZoneType.BULLISH_BREAKER):
                direction = TrendDirection.BULLISH
            elif zone.zone_type in (ZoneType.BEARISH_OB, ZoneType.BEARISH_BREAKER):
                direction = TrendDirection.BEARISH
            else:
                continue

            # ---- 2nd Break Rule gate ----
            # If bias shift is pending (not confirmed) and this signal is in
            # the counter-trend direction, flag it but do not skip — allow
            # reduced-confidence processing.
            if not bias_shift_confirmed and direction == pending_bias_direction:
                reasoning.append(
                    "Pending_Bias_Shift: 1st counter-trend break only — "
                    "awaiting 2nd break for confirmation"
                )

            # ---- Step 4 — LTF structure shift confirmed ----
            if not self._has_recent_ltf_structure_shift(
                analysis.structure_breaks, direction,
            ):
                continue  # no recent BOS/CHOCH in signal direction — skip

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

            # ---- A+ Order Block Filter ----
            if zone.zone_type in (ZoneType.BULLISH_OB, ZoneType.BEARISH_OB):
                if self._is_a_plus_ob(zone, analysis):
                    reasoning.append("A+ Order Block (consolidation or sweep-preceded)")
                else:
                    score -= 1
                    reasoning.append(
                        "Low-probability OB: no consolidation range and no preceding sweep (-1)"
                    )

            # ---- Fibonacci OTE zone filter ----
            entry_price = zone.midpoint
            if last_swing_high is not None and last_swing_low is not None:
                # Skip POIs sitting in the wrong half (premium for buys,
                # discount for sells)
                if self._is_in_premium_discount_skip_zone(
                    entry_price, last_swing_low, last_swing_high, direction,
                ):
                    reasoning.append(
                        "Skipped: POI in premium zone (buys) or discount zone (sells)"
                    )
                    continue

                if self._is_in_ote_zone(
                    entry_price, last_swing_low, last_swing_high, direction,
                ):
                    score += 1
                    reasoning.append("POI sits in Fibonacci OTE zone (0.618-0.66) (+1)")

            # ---- HTF Nesting Requirement ----
            htf_level_confirmed = False
            if htf_zones:
                for htf_zone in htf_zones:
                    htf_overlaps = htf_zone.low <= zone.high and htf_zone.high >= zone.low
                    if htf_overlaps:
                        htf_level_confirmed = True
                        score += 1
                        reasoning.append(
                            f"Nested inside HTF zone ({htf_zone.zone_type.value}) (+1)"
                        )
                        break

            if not htf_level_confirmed:
                reasoning.append(
                    "WARNING: Signal NOT nested inside an HTF level — use caution"
                )

            # ---- Counter-Trend Risk Reduction ----
            counter_trend = False
            if analysis.trend != direction and analysis.trend != TrendDirection.RANGING:
                counter_trend = True
                reasoning.append(
                    "Counter-trend: signal opposes HTF trend — reduce risk by 50%"
                )

            score = max(min(score, 5), 0)

            # ---- Entry / SL / TP ----
            if direction == TrendDirection.BULLISH:
                sl = zone.low - (zone.high - zone.low) * 0.1
                risk = entry_price - sl
                tp = entry_price + risk * 2  # minimum 1:2 RR
            else:
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
                htf_level_confirmed=htf_level_confirmed,
                counter_trend=counter_trend,
                timeframe=timeframe,
            ))

        # Sort by confluence descending
        signals.sort(key=lambda s: s.confluence_score, reverse=True)
        return signals

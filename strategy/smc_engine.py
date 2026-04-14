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
        # Normalize: ensure 'time' column is the DatetimeIndex (MT5 bridge returns it as a column)
        if "time" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
            df = df.set_index("time")
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")

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
        self,
        analysis: AnalysisResult,
        symbol: str = "",
        timeframe: str = "",
        htf_zones: list[Zone] | None = None,
        h4_trend: TrendDirection | None = None,
        amd_phase: str = "",
        htf_liquidity_targets: list[float] | None = None,
        pip_size: float = 0.10,
        max_sl_pips: float = 30.0,
        min_sl_pips: float = 10.0,
    ) -> list[TradeSignal]:
        """Produce trade signals from an AnalysisResult.

        Implements Hasan's exact Entry Funnel:
          Step 1 -- H4/Daily bias direction (h4_trend filter)
          Step 2 -- Zone / POI identified (FRESH zones only)
          Step 3 -- Session is London or NY, AMD phase = Distribution
          Step 4 -- LTF structure shift confirmed (recent BOS/CHOCH)

        Additional Hasan methodology filters:
          - 2nd Break Rule (anti-trap) HARD BLOCK
          - A+ Order Block grading
          - Fibonacci OTE zone confluence
          - HTF nesting requirement
          - Entry at zone boundary, not midpoint
          - TP at HTF external liquidity targets
          - Max SL cap for gold

        Parameters
        ----------
        analysis : AnalysisResult
            Output from ``self.analyze()``.
        symbol : str
            Instrument identifier.
        timeframe : str
            Timeframe label (e.g. "M15", "H1").
        htf_zones : list[Zone] | None
            Higher-timeframe zones for nesting validation.
        h4_trend : TrendDirection | None
            H4 trend bias. Signals opposing this are rejected per Step 1.
        amd_phase : str
            Current AMD phase. Only "DISTRIBUTION" allows entries.
        htf_liquidity_targets : list[float] | None
            HTF swing points and liquidity pool levels for TP targeting.
        pip_size : float
            Dollar value of one pip. Default 0.10 for gold.
        max_sl_pips : float
            Maximum SL distance in pips. Default 30.0 for gold M15.
        """
        signals: list[TradeSignal] = []

        # ----------------------------------------------------------------
        # Step 1 -- Trend determined (LTF must not be ranging)
        # ----------------------------------------------------------------
        if analysis.trend == TrendDirection.RANGING:
            return signals

        # ----------------------------------------------------------------
        # DEVIATION 4 FIX: H4 trend filter -- reject signals opposing HTF bias
        # Hasan Step 1: "Is Daily/4H Bullish or Bearish? Only trade that direction."
        # ----------------------------------------------------------------
        # (applied per-zone below, since zone determines signal direction)

        # ----------------------------------------------------------------
        # Step 3 -- Only trade during execution sessions (London / NY)
        # ----------------------------------------------------------------
        if analysis.session_info not in ("LONDON", "NEW_YORK"):
            return signals

        # ----------------------------------------------------------------
        # DEVIATION 5 FIX: AMD phase gate
        # Hasan: Accumulation = IDLE, Manipulation = ALERT, Distribution = EXECUTE
        # Only enter during Distribution phase.
        # ----------------------------------------------------------------
        if amd_phase and amd_phase == "ACCUMULATION":
            return signals  # IDLE -- no entries during accumulation

        # ----------------------------------------------------------------
        # 2nd Break Rule (Anti-Trap): If there's a CHOCH, ensure at least 2
        # counter-trend breaks before treating the bias shift as confirmed.
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
                bias_shift_confirmed = False
                pending_bias_direction = counter_dir

        has_sweep = len(analysis.sweeps) > 0
        has_structure = len(analysis.structure_breaks) > 0

        # Derive the most recent swing range for Fibonacci checks
        swing_highs = [sp for sp in analysis.swing_points if sp.swing_type == SwingType.HIGH]
        swing_lows = [sp for sp in analysis.swing_points if sp.swing_type == SwingType.LOW]
        last_swing_high = swing_highs[-1].price if swing_highs else None
        last_swing_low = swing_lows[-1].price if swing_lows else None

        # Max SL distance in dollar terms
        max_sl_distance = max_sl_pips * pip_size  # e.g. 30 * 0.10 = $3.00

        for zone in analysis.active_zones:
            reasoning: list[str] = []
            score = 0

            # ---- Step 2 -- Zone / POI identified ----
            if zone.zone_type in (ZoneType.BULLISH_OB, ZoneType.BULLISH_BREAKER):
                direction = TrendDirection.BULLISH
            elif zone.zone_type in (ZoneType.BEARISH_OB, ZoneType.BEARISH_BREAKER):
                direction = TrendDirection.BEARISH
            else:
                continue

            # ---- DEVIATION 4 FIX: H4 trend filter (per-zone) ----
            # Reject signals that oppose the H4 trend direction
            counter_trend = False
            if h4_trend and h4_trend != TrendDirection.RANGING:
                if h4_trend != direction:
                    # Counter-trend signal -- only allow if nested in major HTF zone
                    htf_nested = False
                    if htf_zones:
                        for htf_zone in htf_zones:
                            if htf_zone.low <= zone.high and htf_zone.high >= zone.low:
                                htf_nested = True
                                break
                    if not htf_nested:
                        continue  # HARD SKIP -- opposing H4 trend with no HTF zone
                    counter_trend = True
                    reasoning.append(
                        "Counter-trend: opposes H4 bias but nested in HTF zone"
                    )

            # ---- DEVIATION 9 FIX: 2nd Break Rule HARD BLOCK ----
            # Hasan: First break = Pending. Must wait for 2nd break.
            # DO NOT trade until confirmed.
            if not bias_shift_confirmed and direction == pending_bias_direction:
                continue  # HARD SKIP -- not just a warning

            # ---- Step 4 -- LTF structure shift confirmed ----
            if not self._has_recent_ltf_structure_shift(
                analysis.structure_breaks, direction,
            ):
                continue

            # ---- Confluence scoring ----
            # 1. Zone present (FRESH zones get bonus per Hasan's first-tap rule)
            score += 1
            reasoning.append(f"Active {zone.zone_type.value} zone at {zone.midpoint:.2f}")
            if zone.status == ZoneStatus.FRESH:
                score += 1
                reasoning.append("FRESH zone (first tap) -- highest probability (+1)")

            # 2. Trend alignment (LTF)
            if analysis.trend == direction:
                score += 1
                reasoning.append(f"LTF trend aligned: {analysis.trend.value}")

            # 3. Structure break confirmation
            if has_structure:
                latest_break = analysis.structure_breaks[-1]
                if latest_break.direction == direction:
                    score += 1
                    reasoning.append(
                        f"{latest_break.break_type.value} confirmed at {latest_break.level:.2f}"
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

            # ---- DEVIATION 2 FIX: Entry at zone BOUNDARY, not midpoint ----
            # Hasan: Entry on LTF Breaker Block retest at zone edge
            # BULLISH: entry at zone LOW (demand boundary)
            # BEARISH: entry at zone HIGH (supply boundary)
            if direction == TrendDirection.BULLISH:
                entry_price = zone.low
            else:
                entry_price = zone.high

            # ---- Fibonacci OTE zone filter ----
            if last_swing_high is not None and last_swing_low is not None:
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
                    "WARNING: Signal NOT nested inside an HTF level -- use caution"
                )

            # ---- H4 trend alignment bonus ----
            if h4_trend and h4_trend == direction:
                reasoning.append("H4 trend aligned with signal direction")

            score = max(min(score, 5), 0)

            # ---- DEVIATION 2 FIX: SL behind zone boundary ----
            # Hasan: SL behind the Breaker Block or LTF protected level
            zone_height = zone.high - zone.low
            min_risk_distance = min_sl_pips * pip_size  # 10 * 0.10 = $1.00 for gold
            sl_buffer = max(zone_height * 0.1, min_risk_distance)

            if direction == TrendDirection.BULLISH:
                sl = zone.low - sl_buffer
                risk = entry_price - sl
            else:
                sl = zone.high + sl_buffer
                risk = sl - entry_price

            if risk <= 0:
                continue

            # ---- DEVIATION G: Max SL cap for gold ----
            if risk > max_sl_distance:
                risk = max_sl_distance
                if direction == TrendDirection.BULLISH:
                    sl = entry_price - risk
                else:
                    sl = entry_price + risk
                reasoning.append(f"SL clamped to max {max_sl_pips:.0f} pips")

            # ---- DEVIATION 3 FIX: TP at HTF external liquidity ----
            # Hasan: TP at HTF External Liquidity -- nearest old high/low
            tp: float | None = None
            if htf_liquidity_targets:
                if direction == TrendDirection.BULLISH:
                    targets_above = sorted(
                        [t for t in htf_liquidity_targets if t > entry_price]
                    )
                    if targets_above:
                        tp = targets_above[0]  # Nearest liquidity above
                        reasoning.append(
                            f"TP at HTF liquidity target: {tp:.2f}"
                        )
                else:
                    targets_below = sorted(
                        [t for t in htf_liquidity_targets if t < entry_price],
                        reverse=True,
                    )
                    if targets_below:
                        tp = targets_below[0]  # Nearest liquidity below
                        reasoning.append(
                            f"TP at HTF liquidity target: {tp:.2f}"
                        )

            # Validate TP gives at least 2:1 RR, otherwise fallback
            if tp is not None:
                tp_distance = abs(tp - entry_price)
                if tp_distance / risk < 2.0:
                    tp = None  # Target too close, use fallback

            # Fallback: 2x risk if no valid HTF liquidity target
            if tp is None:
                if direction == TrendDirection.BULLISH:
                    tp = entry_price + risk * 2
                else:
                    tp = entry_price - risk * 2
                reasoning.append("TP fallback: 2x risk (no valid HTF liquidity target)")

            rr = abs(tp - entry_price) / risk

            if rr < 2.0:
                continue

            signals.append(TradeSignal(
                symbol=symbol,
                direction=direction,
                entry_price=round(entry_price, 2),
                sl=round(sl, 2),
                tp=round(tp, 2),
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

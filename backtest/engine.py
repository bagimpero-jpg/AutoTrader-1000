"""Event-driven bar-by-bar backtest engine with multi-timeframe SMC analysis."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from strategy.smc_engine import AnalysisResult, SMCEngine, TradeSignal
from strategy.structures import StructureAnalyzer, TrendDirection
from strategy.zones import Zone, ZoneDetector
from strategy.liquidity import LiquidityAnalyzer
from strategy.session_profiler import SessionProfiler
from backtest.config import BacktestConfig
from backtest.simulator import ClosedTrade, SimulatedBroker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BacktestSMCEngine — fixes look-ahead bias by injecting bar timestamp
# ---------------------------------------------------------------------------

class BacktestSMCEngine(SMCEngine):
    """Subclass that uses the bar's timestamp instead of datetime.now()."""

    def analyze(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        bar_timestamp: datetime | None = None,
        swing_lookback: int = 5,
    ) -> AnalysisResult:
        """Identical to parent but uses bar_timestamp for session detection."""
        from strategy.zones import ZoneStatus

        # 1. Structure (with configurable lookback)
        swing_points = self.structure.detect_swing_points(df, lookback=swing_lookback)
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
        # Zones: FRESH (untouched) and TESTED (touched but held) are valid.
        # Only BROKEN zones (price closed through) are invalidated.
        # FRESH zones get priority in signal generation via confluence scoring.
        active_zones = [z for z in all_zones if z.status != ZoneStatus.BROKEN]

        # 3. Liquidity
        pools = self.liquidity.detect_liquidity_pools(swing_points)
        sweeps = self.liquidity.detect_liquidity_sweep(df, pools)

        # 4. Session — USE BAR TIMESTAMP (no look-ahead)
        utc_now = bar_timestamp or datetime.now(timezone.utc)
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


# ---------------------------------------------------------------------------
# BacktestResult
# ---------------------------------------------------------------------------

@dataclass
class BacktestResult:
    """Complete results from a single backtest run."""
    trades: list[ClosedTrade]
    equity_curve: list[tuple[pd.Timestamp, float]]
    final_balance: float
    initial_balance: float
    total_return_pct: float
    win_rate: float
    avg_rr: float
    profit_factor: float
    max_drawdown_pct: float
    max_drawdown_dollars: float
    sharpe_ratio: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win_pips: float
    avg_loss_pips: float
    best_trade_pips: float
    worst_trade_pips: float
    avg_trade_duration: timedelta
    trades_by_session: dict[str, dict] = field(default_factory=dict)
    trades_by_setup: dict[str, dict] = field(default_factory=dict)
    params_used: dict = field(default_factory=dict)
    start_date: datetime | None = None
    end_date: datetime | None = None


# ---------------------------------------------------------------------------
# BacktestEngine
# ---------------------------------------------------------------------------

class BacktestEngine:
    """Event-driven bar-by-bar backtester with multi-timeframe SMC analysis."""

    WARMUP_BARS = 200  # Minimum bars before generating signals

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    def run(
        self,
        m15_data: pd.DataFrame,
        h1_data: pd.DataFrame,
        h4_data: pd.DataFrame,
        params: dict | None = None,
    ) -> BacktestResult:
        """Run a complete backtest over the provided data.

        Parameters
        ----------
        m15_data : Entry timeframe bars (M15).
        h1_data  : Zone context bars (H1).
        h4_data  : HTF trend/bias bars (H4).
        params   : Optional SMC parameter overrides.
        """
        smc_params = {**self.config.smc_params, **(params or {})}
        swing_lookback = smc_params.get("swing_lookback", 5)
        confluence_min = smc_params.get("confluence_min_score", 3)
        min_rr = params.get("min_rr", self.config.min_rr) if params else self.config.min_rr

        # Create fresh engine and broker
        engine = BacktestSMCEngine()
        broker = SimulatedBroker(self.config)

        # State for HTF caching
        last_h4_bar_time: pd.Timestamp | None = None
        last_h1_bar_time: pd.Timestamp | None = None
        h4_trend = TrendDirection.RANGING
        h4_zones: list[Zone] = []
        h1_zones: list[Zone] = []
        h4_analysis: AnalysisResult | None = None
        htf_liq_targets: list[float] = []

        # DEVIATION 7 FIX: Pending signals as limit orders
        # Each entry is (signal, bars_remaining) — cancel after 4 bars (1 hour)
        pending_orders: list[tuple[TradeSignal, int]] = []
        MAX_PENDING_BARS = 4  # Cancel unfilled limit orders after 4 M15 bars

        # BUG FIX: Track used zone midpoints so each zone only fires ONE signal
        used_zone_keys: set[str] = set()

        total_bars = len(m15_data)
        logger.info(
            "Starting backtest: %d M15 bars, params=%s",
            total_bars, smc_params,
        )

        for i in range(self.WARMUP_BARS, total_bars):
            bar = m15_data.iloc[i]
            bar_time = pd.Timestamp(m15_data.index[i])

            # --- Execute pending LIMIT ORDERS ---
            # BUY limit: fill only if bar low <= signal.entry_price
            # SELL limit: fill only if bar high >= signal.entry_price
            # Orders expire after MAX_PENDING_BARS if unfilled.
            still_pending: list[tuple[TradeSignal, int]] = []
            for sig, bars_left in pending_orders:
                if bars_left <= 0:
                    continue  # Expired — cancel this order

                if not broker.can_open_trade(bar_time):
                    still_pending.append((sig, bars_left - 1))
                    continue

                dir_name = getattr(sig.direction, "name", str(sig.direction)).upper()
                is_buy = "BULL" in dir_name

                bar_open = float(bar["open"])
                bar_low = float(bar["low"])
                bar_high = float(bar["high"])

                filled = False
                if is_buy:
                    if bar_low <= sig.entry_price:
                        fill_price = sig.entry_price
                        filled = True
                    elif bar_open < sig.entry_price:
                        fill_price = bar_open
                        filled = True
                else:
                    if bar_high >= sig.entry_price:
                        fill_price = sig.entry_price
                        filled = True
                    elif bar_open > sig.entry_price:
                        fill_price = bar_open
                        filled = True

                if filled:
                    broker.open_position(sig, bar_time, fill_price)
                else:
                    still_pending.append((sig, bars_left - 1))

            pending_orders = still_pending

            # --- Update open positions (SL/TP/partial close checks) ---
            # Done BEFORE generating new signals so new signals don't
            # get checked against the same bar they were created on.
            broker.update_positions(bar, bar_time)

            # --- Update H4 analysis (only when new H4 bar completes) ---
            h4_current = self._get_latest_htf_bar_time(h4_data, bar_time)
            if h4_current is not None and h4_current != last_h4_bar_time:
                last_h4_bar_time = h4_current
                h4_window = self._get_window(h4_data, bar_time, self.WARMUP_BARS)
                if len(h4_window) >= 20:
                    h4_analysis = engine.analyze(
                        h4_window, self.config.symbol, "H4",
                        bar_timestamp=bar_time.to_pydatetime(),
                        swing_lookback=swing_lookback,
                    )
                    h4_trend = h4_analysis.trend
                    h4_zones = h4_analysis.active_zones

                    # DEVIATION 3 FIX: Extract HTF liquidity targets for TP
                    htf_liq_targets = []
                    for pool in h4_analysis.liquidity_pools:
                        htf_liq_targets.append(pool.level)
                    for sp in h4_analysis.swing_points:
                        htf_liq_targets.append(sp.price)

            # --- Update H1 analysis (only when new H1 bar completes) ---
            h1_current = self._get_latest_htf_bar_time(h1_data, bar_time)
            if h1_current is not None and h1_current != last_h1_bar_time:
                last_h1_bar_time = h1_current
                h1_window = self._get_window(h1_data, bar_time, self.WARMUP_BARS)
                if len(h1_window) >= 20:
                    h1_analysis = engine.analyze(
                        h1_window, self.config.symbol, "H1",
                        bar_timestamp=bar_time.to_pydatetime(),
                        swing_lookback=swing_lookback,
                    )
                    h1_zones = h1_analysis.active_zones
                    # Also add H1 liquidity targets
                    for pool in h1_analysis.liquidity_pools:
                        if pool.level not in htf_liq_targets:
                            htf_liq_targets.append(pool.level)

            # --- M15 analysis (rolling 200-bar window) ---
            m15_window = m15_data.iloc[max(0, i - self.WARMUP_BARS + 1) : i + 1]

            analysis = engine.analyze(
                m15_window, self.config.symbol, "M15",
                bar_timestamp=bar_time.to_pydatetime(),
                swing_lookback=swing_lookback,
            )

            # --- Generate signals (DEVIATION 4, 5, 3 FIX) ---
            # Pass h4_trend, amd_phase, and htf_liquidity_targets
            signals = engine.generate_signals(
                analysis,
                symbol=self.config.symbol,
                timeframe="M15",
                htf_zones=h1_zones if h1_zones else None,
                h4_trend=h4_trend,
                amd_phase=analysis.amd_phase,
                htf_liquidity_targets=htf_liq_targets if htf_liq_targets else None,
                pip_size=self.config.pip_size,
                max_sl_pips=self.config.max_sl_pips,
                min_sl_pips=self.config.min_sl_pips,
            )

            # --- Filter and STORE as pending (executed next bar) ---
            # BUG FIX: Only allow ONE signal per unique zone (by entry+sl key)
            for sig in signals:
                if sig.confluence_score < confluence_min:
                    continue
                if sig.rr_ratio < min_rr:
                    continue

                # Unique zone key: round to nearest pip for gold ($0.10)
                zone_key = f"{round(sig.entry_price, 1)}_{round(sig.sl, 1)}"
                if zone_key in used_zone_keys:
                    continue  # Already traded this zone
                used_zone_keys.add(zone_key)
                pending_orders.append((sig, MAX_PENDING_BARS))

            # --- Equity snapshot ---
            broker.snapshot_equity(bar_time, float(bar["close"]))

            # Progress logging every 5000 bars
            if i % 5000 == 0 and i > self.WARMUP_BARS:
                logger.info(
                    "Progress: %d/%d bars (%.1f%%) | Balance: $%.2f | Trades: %d",
                    i, total_bars, 100 * i / total_bars,
                    broker.balance, len(broker.closed_trades),
                )

        # --- Close remaining positions at end ---
        if m15_data.empty:
            final_price = 0.0
            final_time = pd.Timestamp.now(tz="UTC")
        else:
            final_bar = m15_data.iloc[-1]
            final_price = float(final_bar["close"])
            final_time = pd.Timestamp(m15_data.index[-1])

        broker.close_all_positions(final_time, final_price)

        # --- Build result ---
        stats = broker.get_stats()
        start_dt = m15_data.index[0].to_pydatetime() if not m15_data.empty else None
        end_dt = m15_data.index[-1].to_pydatetime() if not m15_data.empty else None

        return BacktestResult(
            trades=broker.closed_trades,
            equity_curve=broker.equity_curve,
            final_balance=broker.balance,
            initial_balance=self.config.initial_balance,
            total_return_pct=stats.get("total_return_pct", 0.0),
            win_rate=stats.get("win_rate", 0.0),
            avg_rr=stats.get("avg_rr", 0.0),
            profit_factor=stats.get("profit_factor", 0.0),
            max_drawdown_pct=stats.get("max_drawdown_pct", 0.0),
            max_drawdown_dollars=stats.get("max_drawdown_dollars", 0.0),
            sharpe_ratio=stats.get("sharpe_ratio", 0.0),
            total_trades=stats.get("total_trades", 0),
            winning_trades=stats.get("winning_trades", 0),
            losing_trades=stats.get("losing_trades", 0),
            avg_win_pips=stats.get("avg_win_pips", 0.0),
            avg_loss_pips=stats.get("avg_loss_pips", 0.0),
            best_trade_pips=stats.get("best_trade_pips", 0.0),
            worst_trade_pips=stats.get("worst_trade_pips", 0.0),
            avg_trade_duration=stats.get("avg_trade_duration", timedelta()),
            trades_by_session=stats.get("trades_by_session", {}),
            trades_by_setup=stats.get("trades_by_setup", {}),
            params_used=smc_params,
            start_date=start_dt,
            end_date=end_dt,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_latest_htf_bar_time(
        htf_data: pd.DataFrame, current_time: pd.Timestamp,
    ) -> pd.Timestamp | None:
        """Return the timestamp of the latest HTF bar that has completed."""
        mask = htf_data.index <= current_time
        if mask.any():
            return pd.Timestamp(htf_data.index[mask][-1])
        return None

    @staticmethod
    def _get_window(
        data: pd.DataFrame, current_time: pd.Timestamp, window_size: int,
    ) -> pd.DataFrame:
        """Return the most recent `window_size` bars up to current_time (inclusive)."""
        mask = data.index <= current_time
        return data[mask].tail(window_size)

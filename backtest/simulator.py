"""Simulated broker for backtesting — fills, partials, slippage, risk limits."""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from backtest.config import BacktestConfig

# Avoid importing from core/strategy (they pull in MetaTrader5).
# Use duck-typing for TradeSignal and inline lot-size calculation.

logger = logging.getLogger(__name__)


@dataclass
class SimulatedPosition:
    """An open simulated position."""
    ticket: int
    symbol: str
    direction: str  # "BUY" or "SELL"
    entry_price: float
    sl: float
    tp: float
    volume: float
    entry_time: datetime
    setup_type: str = ""
    session: str = ""
    confluence_score: int = 0
    reasoning: list[str] = field(default_factory=list)
    partial_closed: bool = False
    original_volume: float = 0.0
    original_sl: float = 0.0


@dataclass
class ClosedTrade:
    """A completed trade with full details."""
    ticket: int
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    sl: float
    tp: float
    volume: float
    entry_time: datetime
    exit_time: datetime
    pnl_pips: float
    pnl_dollars: float
    rr_achieved: float
    exit_reason: str  # "TP", "SL", "PARTIAL_CLOSE", "END_OF_DATA"
    setup_type: str = ""
    session: str = ""
    confluence_score: int = 0
    reasoning: list[str] = field(default_factory=list)
    risk_freed: bool = False


class SimulatedBroker:
    """Simulated broker that handles order execution, SL/TP, partial closes, and risk."""

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()
        self.balance: float = self.config.initial_balance
        self.equity: float = self.config.initial_balance
        self.open_positions: list[SimulatedPosition] = []
        self.closed_trades: list[ClosedTrade] = []
        self.equity_curve: list[tuple[pd.Timestamp, float]] = []

        self._next_ticket: int = 1000
        self._consecutive_losses: int = 0
        self._trades_today: int = 0
        self._daily_pnl: float = 0.0
        self._current_day: str = ""
        self._daily_shutdown: bool = False

    # ------------------------------------------------------------------
    # Risk percent (mirrors RiskManager dynamic scaling)
    # ------------------------------------------------------------------

    def get_current_risk_percent(self) -> float:
        """Dynamic risk based on consecutive losses."""
        if self._consecutive_losses >= self.config.deep_recovery_consec_losses:
            return 0.25
        if self._consecutive_losses >= self.config.recovery_consec_losses:
            return 0.50
        return self.config.risk_percent

    # ------------------------------------------------------------------
    # Trade gating
    # ------------------------------------------------------------------

    def can_open_trade(self, bar_time: pd.Timestamp) -> bool:
        """Check if a new trade is allowed (daily limits, drawdown, etc.)."""
        self._reset_daily_if_needed(bar_time)

        if self._daily_shutdown:
            return False

        if self._trades_today >= self.config.max_trades_per_day:
            return False

        # Max total drawdown check
        total_dd = (self.config.initial_balance - self.balance) / self.config.initial_balance * 100
        if total_dd >= self.config.max_total_loss_pct:
            return False

        return True

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    def open_position(
        self,
        signal: object,
        bar_time: pd.Timestamp,
        current_price: float,
    ) -> SimulatedPosition | None:
        """Open a new simulated position from a TradeSignal (duck-typed)."""
        self._reset_daily_if_needed(bar_time)

        if not self.can_open_trade(bar_time):
            return None

        # signal.direction is a TrendDirection enum; .name gives "BULLISH"/"BEARISH"
        dir_name = getattr(signal.direction, "name", str(signal.direction)).upper()
        direction = "BUY" if "BULL" in dir_name else "SELL"

        # Apply spread + slippage
        spread = self.config.spread_pips * self.config.pip_size
        slippage = random.uniform(0, self.config.max_slippage_pips) * self.config.pip_size

        if direction == "BUY":
            entry = current_price + spread / 2 + slippage
        else:
            entry = current_price - spread / 2 - slippage

        # FIX 2: Recalculate SL/TP from actual fill price to preserve
        # the signal's intended risk and reward distances.
        # Without this, spread+slippage can make TP land behind entry
        # or inflate actual risk far beyond what the signal intended.
        signal_risk = abs(signal.entry_price - signal.sl)
        signal_reward = abs(signal.tp - signal.entry_price)

        if direction == "BUY":
            adjusted_sl = entry - signal_risk
            adjusted_tp = entry + signal_reward
        else:
            adjusted_sl = entry + signal_risk
            adjusted_tp = entry - signal_reward

        # Reject if signal risk is degenerate
        if signal_risk <= 0 or signal_reward <= 0:
            return None

        # FIX 4: Max SL guard — clamp risk to max allowed distance
        max_allowed_risk = self.config.max_sl_pips * self.config.pip_size
        actual_risk = abs(entry - adjusted_sl)
        if actual_risk > max_allowed_risk:
            if direction == "BUY":
                adjusted_sl = entry - max_allowed_risk
                adjusted_tp = entry + max_allowed_risk * 2  # maintain 2:1 min
            else:
                adjusted_sl = entry + max_allowed_risk
                adjusted_tp = entry - max_allowed_risk * 2

        # Reject if fill price has already moved past the adjusted TP
        if direction == "BUY":
            if entry >= adjusted_tp:
                return None
            if entry <= adjusted_sl:
                return None
        else:
            if entry <= adjusted_tp:
                return None
            if entry >= adjusted_sl:
                return None

        # Calculate SL distance and lot size
        sl_distance = abs(entry - adjusted_sl)
        if sl_distance <= 0:
            return None

        risk_pct = self.get_current_risk_percent()

        try:
            volume = self._calculate_lot_size(sl_distance, risk_pct)
        except ValueError as e:
            logger.warning("Lot size calculation failed: %s", e)
            return None

        ticket = self._next_ticket
        self._next_ticket += 1

        # Determine session from bar time
        hour = bar_time.hour
        if 8 <= hour < 12:
            session = "LONDON"
        elif 13 <= hour < 17:
            session = "NEW_YORK"
        else:
            session = "OTHER"

        # Infer setup type from reasoning
        setup_type = self._infer_setup_type(signal.reasoning)

        pos = SimulatedPosition(
            ticket=ticket,
            symbol=signal.symbol,
            direction=direction,
            entry_price=entry,
            sl=adjusted_sl,
            tp=adjusted_tp,
            volume=volume,
            entry_time=bar_time.to_pydatetime(),
            setup_type=setup_type,
            session=session,
            confluence_score=signal.confluence_score,
            reasoning=signal.reasoning,
            original_volume=volume,
            original_sl=adjusted_sl,
        )

        self.open_positions.append(pos)
        self._trades_today += 1

        logger.debug(
            "Opened %s %s @ %.2f | SL=%.2f TP=%.2f | Vol=%.2f | Risk=%.2f%%",
            direction, signal.symbol, entry, adjusted_sl, adjusted_tp, volume, risk_pct,
        )

        return pos

    # ------------------------------------------------------------------
    # Position updates (per bar)
    # ------------------------------------------------------------------

    def update_positions(self, bar: pd.Series, bar_time: pd.Timestamp) -> None:
        """Check all open positions against the bar's high/low for SL/TP hits."""
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])

        to_close: list[tuple[SimulatedPosition, float, str]] = []

        for pos in self.open_positions:
            # Check partial close first
            if not pos.partial_closed:
                self._check_partial_close(pos, high, low, bar_time)

            if pos.direction == "BUY":
                sl_hit = low <= pos.sl
                tp_hit = high >= pos.tp
            else:
                sl_hit = high >= pos.sl
                tp_hit = low <= pos.tp

            # Conservative: if both could hit, SL wins
            if sl_hit and tp_hit:
                to_close.append((pos, pos.sl, "SL"))
            elif sl_hit:
                to_close.append((pos, pos.sl, "SL"))
            elif tp_hit:
                to_close.append((pos, pos.tp, "TP"))

        for pos, exit_price, reason in to_close:
            self._close_position(pos, exit_price, reason, bar_time)

    def _check_partial_close(
        self,
        pos: SimulatedPosition,
        high: float,
        low: float,
        bar_time: pd.Timestamp,
    ) -> None:
        """Close half at 1:1 RR and move SL to breakeven."""
        sl_distance = abs(pos.entry_price - pos.original_sl)
        if sl_distance <= 0:
            return

        if pos.direction == "BUY":
            target_1r = pos.entry_price + sl_distance
            if high >= target_1r:
                self._do_partial_close(pos, target_1r, bar_time)
        else:
            target_1r = pos.entry_price - sl_distance
            if low <= target_1r:
                self._do_partial_close(pos, target_1r, bar_time)

    def _do_partial_close(
        self,
        pos: SimulatedPosition,
        exit_price: float,
        bar_time: pd.Timestamp,
    ) -> None:
        """Execute partial close: close fraction, move SL to breakeven."""
        close_volume = round(pos.volume * self.config.partial_close_fraction, 2)
        if close_volume < self.config.volume_min:
            close_volume = pos.volume  # Close all if remainder too small

        pnl_pips, pnl_dollars = self._calc_pnl(pos, exit_price, close_volume)

        self.closed_trades.append(ClosedTrade(
            ticket=pos.ticket,
            symbol=pos.symbol,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            sl=pos.sl,
            tp=pos.tp,
            volume=close_volume,
            entry_time=pos.entry_time,
            exit_time=bar_time.to_pydatetime(),
            pnl_pips=pnl_pips,
            pnl_dollars=pnl_dollars,
            rr_achieved=1.0,
            exit_reason="PARTIAL_CLOSE",
            setup_type=pos.setup_type,
            session=pos.session,
            confluence_score=pos.confluence_score,
            reasoning=pos.reasoning,
            risk_freed=True,
        ))

        self.balance += pnl_dollars
        self._daily_pnl += pnl_dollars

        # Update position
        pos.volume = round(pos.volume - close_volume, 2)
        pos.partial_closed = True
        pos.sl = pos.entry_price  # Move SL to breakeven

        if pos.volume < self.config.volume_min:
            # Remainder too small, remove from open
            if pos in self.open_positions:
                self.open_positions.remove(pos)

    # ------------------------------------------------------------------
    # Close position
    # ------------------------------------------------------------------

    def _close_position(
        self,
        pos: SimulatedPosition,
        exit_price: float,
        reason: str,
        bar_time: pd.Timestamp,
    ) -> None:
        """Fully close a position and record the trade."""
        pnl_pips, pnl_dollars = self._calc_pnl(pos, exit_price, pos.volume)
        sl_distance = abs(pos.entry_price - pos.original_sl)
        rr_achieved = abs(exit_price - pos.entry_price) / sl_distance if sl_distance > 0 else 0

        if pnl_dollars < 0:
            rr_achieved = -rr_achieved

        self.closed_trades.append(ClosedTrade(
            ticket=pos.ticket,
            symbol=pos.symbol,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            sl=pos.original_sl,
            tp=pos.tp,
            volume=pos.volume,
            entry_time=pos.entry_time,
            exit_time=bar_time.to_pydatetime(),
            pnl_pips=pnl_pips,
            pnl_dollars=pnl_dollars,
            rr_achieved=round(rr_achieved, 2),
            exit_reason=reason,
            setup_type=pos.setup_type,
            session=pos.session,
            confluence_score=pos.confluence_score,
            reasoning=pos.reasoning,
            risk_freed=pos.partial_closed,
        ))

        self.balance += pnl_dollars
        self._daily_pnl += pnl_dollars

        # Update consecutive losses
        if pnl_dollars < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        # Check daily shutdown
        daily_dd_pct = abs(self._daily_pnl) / self.config.initial_balance * 100
        if self._daily_pnl < 0 and daily_dd_pct >= self.config.daily_loss_shutdown_pct:
            if not self._daily_shutdown:
                logger.info("Daily loss shutdown triggered: $%.2f (%.1f%%)",
                            self._daily_pnl, daily_dd_pct)
            self._daily_shutdown = True

        if pos in self.open_positions:
            self.open_positions.remove(pos)

    # ------------------------------------------------------------------
    # Close all (end of backtest)
    # ------------------------------------------------------------------

    def close_all_positions(self, bar_time: pd.Timestamp, current_price: float) -> None:
        """Force-close all remaining positions at current price."""
        for pos in list(self.open_positions):
            self._close_position(pos, current_price, "END_OF_DATA", bar_time)

    # ------------------------------------------------------------------
    # Trade aggregation (combines partial close + final close per ticket)
    # ------------------------------------------------------------------

    def _aggregate_trades_by_ticket(self) -> list[ClosedTrade]:
        """Group closed trades by ticket, combining partial close + final close.

        A trade with a partial close produces two ClosedTrade records:
        1. PARTIAL_CLOSE (profitable half at 1:1 RR)
        2. SL/TP/END_OF_DATA (remaining half)

        This method combines them into ONE logical trade per ticket so that
        win rate and RR calculations reflect the total trade outcome.
        """
        from collections import defaultdict
        grouped: dict[int, list[ClosedTrade]] = defaultdict(list)
        for t in self.closed_trades:
            grouped[t.ticket].append(t)

        aggregated: list[ClosedTrade] = []
        for ticket, trades in grouped.items():
            if len(trades) == 1:
                aggregated.append(trades[0])
                continue

            # Multiple records for same ticket — combine
            total_pnl_pips = sum(t.pnl_pips for t in trades)
            total_pnl_dollars = sum(t.pnl_dollars for t in trades)
            total_volume = sum(t.volume for t in trades)

            # Use the final (non-partial) record as the base
            final = [t for t in trades if t.exit_reason != "PARTIAL_CLOSE"]
            base = final[-1] if final else trades[-1]

            # Recalculate RR from total PnL
            sl_dist = abs(base.entry_price - base.sl)
            sl_dist_pips = sl_dist / self.config.pip_size if sl_dist > 0 else 1.0
            rr = total_pnl_pips / sl_dist_pips if sl_dist_pips > 0 else 0.0

            aggregated.append(ClosedTrade(
                ticket=ticket,
                symbol=base.symbol,
                direction=base.direction,
                entry_price=base.entry_price,
                exit_price=base.exit_price,
                sl=base.sl,
                tp=base.tp,
                volume=total_volume,
                entry_time=trades[0].entry_time,
                exit_time=base.exit_time,
                pnl_pips=total_pnl_pips,
                pnl_dollars=total_pnl_dollars,
                rr_achieved=round(rr, 2),
                exit_reason=base.exit_reason,
                setup_type=base.setup_type,
                session=base.session,
                confluence_score=base.confluence_score,
                reasoning=base.reasoning,
                risk_freed=any(t.risk_freed for t in trades),
            ))

        return aggregated

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Compute comprehensive performance statistics."""
        # FIX 1: Use aggregated trades so partial close profits count
        real_trades = self._aggregate_trades_by_ticket()

        if not real_trades:
            return {
                "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
                "win_rate": 0.0, "avg_rr": 0.0, "profit_factor": 0.0,
                "total_return_pct": 0.0, "max_drawdown_pct": 0.0,
                "max_drawdown_dollars": 0.0, "sharpe_ratio": 0.0,
                "avg_win_pips": 0.0, "avg_loss_pips": 0.0,
                "best_trade_pips": 0.0, "worst_trade_pips": 0.0,
                "avg_trade_duration": timedelta(),
                "trades_by_session": {}, "trades_by_setup": {},
            }

        winners = [t for t in real_trades if t.pnl_dollars > 0]
        losers = [t for t in real_trades if t.pnl_dollars <= 0]

        total = len(real_trades)
        win_rate = len(winners) / total * 100

        total_profit = sum(t.pnl_dollars for t in winners)
        total_loss = abs(sum(t.pnl_dollars for t in losers))
        profit_factor = total_profit / total_loss if total_loss > 0 else float("inf")

        avg_rr = (
            sum(t.rr_achieved for t in winners) / len(winners)
            if winners else 0.0
        )

        avg_win_pips = sum(t.pnl_pips for t in winners) / len(winners) if winners else 0.0
        avg_loss_pips = sum(t.pnl_pips for t in losers) / len(losers) if losers else 0.0
        all_pips = [t.pnl_pips for t in real_trades]

        durations = [
            (t.exit_time - t.entry_time)
            for t in real_trades
            if isinstance(t.exit_time, datetime) and isinstance(t.entry_time, datetime)
        ]
        avg_duration = (
            sum(durations, timedelta()) / len(durations) if durations else timedelta()
        )

        # Max drawdown from equity curve
        max_dd_pct, max_dd_dollars = self._calc_max_drawdown()

        # Sharpe ratio (daily returns)
        sharpe = self._calc_sharpe()

        # By session
        trades_by_session: dict = {}
        for session in ("LONDON", "NEW_YORK", "OTHER"):
            st = [t for t in real_trades if t.session == session]
            if st:
                sw = [t for t in st if t.pnl_dollars > 0]
                trades_by_session[session] = {
                    "count": len(st),
                    "win_rate": len(sw) / len(st) * 100,
                    "avg_pnl": sum(t.pnl_dollars for t in st) / len(st),
                }

        # By setup
        trades_by_setup: dict = {}
        setup_types = {t.setup_type for t in real_trades}
        for setup in setup_types:
            st = [t for t in real_trades if t.setup_type == setup]
            if st:
                sw = [t for t in st if t.pnl_dollars > 0]
                trades_by_setup[setup] = {
                    "count": len(st),
                    "win_rate": len(sw) / len(st) * 100,
                    "avg_pnl": sum(t.pnl_dollars for t in st) / len(st),
                }

        return {
            "total_trades": total,
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "win_rate": round(win_rate, 1),
            "avg_rr": round(avg_rr, 2),
            "profit_factor": round(profit_factor, 2),
            "total_return_pct": round(
                (self.balance - self.config.initial_balance) / self.config.initial_balance * 100, 2
            ),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "max_drawdown_dollars": round(max_dd_dollars, 2),
            "sharpe_ratio": round(sharpe, 2),
            "avg_win_pips": round(avg_win_pips, 1),
            "avg_loss_pips": round(avg_loss_pips, 1),
            "best_trade_pips": round(max(all_pips), 1) if all_pips else 0.0,
            "worst_trade_pips": round(min(all_pips), 1) if all_pips else 0.0,
            "avg_trade_duration": avg_duration,
            "trades_by_session": trades_by_session,
            "trades_by_setup": trades_by_setup,
        }

    # ------------------------------------------------------------------
    # Equity curve
    # ------------------------------------------------------------------

    def snapshot_equity(self, bar_time: pd.Timestamp, current_price: float) -> None:
        """Record equity at this bar (balance + unrealized PnL)."""
        unrealized = 0.0
        for pos in self.open_positions:
            _, pnl = self._calc_pnl(pos, current_price, pos.volume)
            unrealized += pnl

        self.equity = self.balance + unrealized
        self.equity_curve.append((bar_time, self.equity))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reset_daily_if_needed(self, bar_time: pd.Timestamp) -> None:
        """Reset daily counters when a new trading day starts."""
        day_str = bar_time.strftime("%Y-%m-%d")
        if day_str != self._current_day:
            self._current_day = day_str
            self._trades_today = 0
            self._daily_pnl = 0.0
            self._daily_shutdown = False

    def _calc_pnl(
        self, pos: SimulatedPosition, exit_price: float, volume: float,
    ) -> tuple[float, float]:
        """Calculate PnL in pips and dollars."""
        if pos.direction == "BUY":
            pnl_raw = exit_price - pos.entry_price
        else:
            pnl_raw = pos.entry_price - exit_price

        pnl_pips = pnl_raw / self.config.pip_size
        pnl_dollars = (pnl_raw / self.config.point) * self.config.tick_value * volume

        return round(pnl_pips, 2), round(pnl_dollars, 2)

    def _calc_max_drawdown(self) -> tuple[float, float]:
        """Calculate max drawdown % and $ from equity curve."""
        if len(self.equity_curve) < 2:
            return 0.0, 0.0

        peak = self.equity_curve[0][1]
        max_dd_dollars = 0.0

        for _, eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd_dollars:
                max_dd_dollars = dd

        max_dd_pct = max_dd_dollars / self.config.initial_balance * 100
        return max_dd_pct, max_dd_dollars

    def _calc_sharpe(self, risk_free_rate: float = 0.0) -> float:
        """Calculate annualized Sharpe ratio from equity curve daily returns."""
        if len(self.equity_curve) < 10:
            return 0.0

        # Sample daily (take last equity value per day)
        daily: dict[str, float] = {}
        for ts, eq in self.equity_curve:
            day = ts.strftime("%Y-%m-%d")
            daily[day] = eq

        values = list(daily.values())
        if len(values) < 2:
            return 0.0

        returns = [(values[i] - values[i - 1]) / values[i - 1]
                    for i in range(1, len(values))]

        if not returns:
            return 0.0

        import numpy as np
        mean_r = float(np.mean(returns))
        std_r = float(np.std(returns))

        if std_r == 0:
            return 0.0

        daily_sharpe = (mean_r - risk_free_rate / 252) / std_r
        return daily_sharpe * (252 ** 0.5)

    def _calculate_lot_size(self, sl_distance: float, risk_percent: float) -> float:
        """Inline lot-size calc (mirrors OrderManager.calculate_lot_size)."""
        si = self.config.symbol_info
        point = si["point"]
        tick_value = si["tick_value"]
        volume_step = si["volume_step"]
        volume_min = si["volume_min"]
        volume_max = si["volume_max"]

        if sl_distance <= 0 or point <= 0 or tick_value <= 0:
            raise ValueError(f"Invalid lot calc inputs: sl={sl_distance}, pt={point}, tv={tick_value}")

        risk_amount = self.balance * risk_percent / 100.0
        raw_lots = risk_amount / (sl_distance / point * tick_value)
        steps = math.floor(raw_lots / volume_step)
        lots = round(steps * volume_step, 8)
        return max(volume_min, min(lots, volume_max))

    @staticmethod
    def _infer_setup_type(reasoning: list[str]) -> str:
        """Infer setup type from signal reasoning list."""
        text = " ".join(reasoning).upper()
        if "BREAKER" in text:
            return "BREAKER_BLOCK"
        if "ORDER BLOCK" in text or "OB" in text:
            return "ORDER_BLOCK"
        if "FVG" in text or "FAIR VALUE" in text:
            return "FVG"
        if "LIQUIDITY" in text or "SWEEP" in text:
            return "LIQUIDITY_SWEEP"
        if "BOS" in text:
            return "BOS"
        if "CHOCH" in text:
            return "CHOCH"
        return "UNKNOWN"

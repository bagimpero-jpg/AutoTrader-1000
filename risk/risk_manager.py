"""Risk management gate — enforces FTMO challenge limits."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class RiskManager:
    """Enforces per-trade, daily, and total drawdown limits for FTMO compliance."""

    def __init__(self, trading_config: dict, bridge, state_manager=None) -> None:
        self.config = trading_config
        self.bridge = bridge
        self._state_manager = state_manager
        self.base_balance: float = trading_config.get("base_balance", 10_000)
        self.max_risk_percent: float = trading_config.get("risk_percent", 1.0)
        self.min_rr: float = trading_config.get("min_rr", 2.0)
        self.max_daily_loss: float = self.base_balance * trading_config.get("max_daily_loss_percent", 5.0) / 100
        self.max_total_loss: float = self.base_balance * trading_config.get("max_total_loss_percent", 10.0) / 100
        self._daily_loss_today: float = 0.0
        self._last_reset_date: str = ""

        # Recovery mode — dynamic risk scaling based on consecutive losses
        self._consecutive_losses: int = 0

        # Max trades per day
        self._max_trades_per_day: int = trading_config.get("max_trades_per_day", 3)
        self._trades_today: int = 0
        self._trades_reset_date: str = ""

        # Conservative daily loss shutdown (2% of base balance = $200 on $10k)
        self._daily_loss_shutdown: float = self.base_balance * trading_config.get(
            "daily_loss_shutdown_percent", 2.0
        ) / 100

        # Restore persisted state (survives restarts)
        self._load_persisted_state()

    # ------------------------------------------------------------------
    # Dynamic risk (recovery mode)
    # ------------------------------------------------------------------

    def get_current_risk_percent(self) -> float:
        """Return dynamic risk percent based on consecutive loss count.

        Standard mode : 1.0%
        Recovery mode  (3-4 consecutive losses): 0.50%
        Deep recovery  (5+  consecutive losses): 0.25%
        """
        if self._consecutive_losses >= 5:
            return 0.25
        if self._consecutive_losses >= 3:
            return 0.50
        return self.max_risk_percent

    # ------------------------------------------------------------------
    # Trade count gate
    # ------------------------------------------------------------------

    def can_open_new_trade(self) -> bool:
        """Return True if the daily trade count limit has not been reached."""
        self._maybe_reset_daily_counters()
        if self._trades_today >= self._max_trades_per_day:
            logger.warning(
                "MAX TRADES PER DAY reached: %d/%d",
                self._trades_today,
                self._max_trades_per_day,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Counter-trend risk
    # ------------------------------------------------------------------

    def get_risk_for_signal(self, signal) -> float:
        """Return risk percent for a signal, halving it for counter-trend setups."""
        base_risk = self.get_current_risk_percent()
        counter_trend = getattr(signal, "counter_trend", False)
        if isinstance(signal, dict):
            counter_trend = signal.get("counter_trend", False)
        if counter_trend:
            return base_risk / 2.0
        return base_risk

    # ------------------------------------------------------------------
    # Core gate
    # ------------------------------------------------------------------

    def can_trade(self) -> bool:
        try:
            account = self.bridge.get_account_info()
            if not account:
                return False

            equity = account["equity"]
            balance = account["balance"]

            # Total drawdown check
            total_loss = self.base_balance - equity
            if total_loss >= self.max_total_loss:
                logger.critical("TOTAL DRAWDOWN LIMIT: loss=$%.2f >= max=$%.2f", total_loss, self.max_total_loss)
                return False

            # Daily drawdown check (reset at midnight UTC)
            self._maybe_reset_daily_counters()

            daily_loss = balance - equity + self._daily_loss_today
            if daily_loss >= self.max_daily_loss:
                logger.warning("DAILY DRAWDOWN LIMIT: loss=$%.2f >= max=$%.2f", daily_loss, self.max_daily_loss)
                return False

            # Conservative daily loss shutdown (2% default — tighter than FTMO's 5%)
            if self._daily_loss_today >= self._daily_loss_shutdown:
                logger.warning(
                    "DAILY LOSS SHUTDOWN: realized loss=$%.2f >= shutdown=$%.2f",
                    self._daily_loss_today,
                    self._daily_loss_shutdown,
                )
                return False

            # Max trades per day
            if not self.can_open_new_trade():
                return False

            return True

        except Exception as e:
            logger.error("Risk check failed: %s. Blocking trades as precaution.", e)
            return False

    def validate_trade(self, signal) -> bool:
        try:
            account = self.bridge.get_account_info()
            if not account:
                return False

            # Check RR ratio
            if hasattr(signal, "rr_ratio") and signal.rr_ratio < self.min_rr:
                logger.info("Signal rejected: RR %.2f < min %.2f", signal.rr_ratio, self.min_rr)
                return False

            # Use dynamic risk for budget check
            risk_percent = self.get_risk_for_signal(signal)
            risk_amount = account["balance"] * risk_percent / 100
            if risk_amount > self.max_daily_loss - self._daily_loss_today:
                logger.warning("Trade would exceed remaining daily risk budget")
                return False

            # B2: Total exposure check — max 2% of base balance across all open positions
            max_exposure_pct = self.config.get("max_total_exposure_percent", 2.0)
            max_exposure = self.base_balance * max_exposure_pct / 100
            current_exposure = self._get_total_open_risk()
            if current_exposure + risk_amount > max_exposure:
                logger.warning(
                    "Total exposure $%.2f + new $%.2f > max $%.2f (%.1f%%) — trade rejected",
                    current_exposure, risk_amount, max_exposure, max_exposure_pct,
                )
                return False

            return True

        except Exception as e:
            logger.error("Trade validation failed: %s", e)
            return False

    def _get_total_open_risk(self) -> float:
        """Sum dollar risk across all open positions using live MT5 data."""
        try:
            positions = self.bridge.get_open_positions()
            total_risk = 0.0
            for pos in positions:
                sl = pos.get("sl", 0.0)
                price_open = pos.get("price_open", 0.0)
                volume = pos.get("volume", 0.0)
                if sl <= 0 or price_open <= 0:
                    continue
                sl_dist = abs(price_open - sl)
                # For gold: 1 lot = 100 oz, $1 move = $100/lot
                risk = sl_dist * 100 * volume
                total_risk += risk
            return total_risk
        except Exception:
            logger.exception("Failed to calculate total open risk")
            return 0.0

    def record_trade_result(self, pnl: float) -> None:
        if pnl < 0:
            self._daily_loss_today += abs(pnl)
            self._consecutive_losses += 1
            logger.info(
                "Loss recorded. Consecutive losses: %d | Risk now: %.2f%%",
                self._consecutive_losses,
                self.get_current_risk_percent(),
            )
        else:
            if self._consecutive_losses > 0:
                logger.info(
                    "Win recorded. Resetting consecutive losses from %d to 0.",
                    self._consecutive_losses,
                )
            self._consecutive_losses = 0
        self._persist_state()

    def record_trade_opened(self) -> None:
        """Increment the daily trade counter. Call after a trade is executed."""
        self._maybe_reset_daily_counters()
        self._trades_today += 1
        logger.info("Trades today: %d/%d", self._trades_today, self._max_trades_per_day)
        self._persist_state()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _maybe_reset_daily_counters(self) -> None:
        """Reset daily loss and trade count at midnight UTC."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_reset_date:
            self._daily_loss_today = 0.0
            self._trades_today = 0
            self._last_reset_date = today
            self._trades_reset_date = today
            self._persist_state()

    # ------------------------------------------------------------------
    # State persistence (survives restarts)
    # ------------------------------------------------------------------

    def _persist_state(self) -> None:
        """Save risk counters to state manager JSON."""
        if self._state_manager is None:
            return
        try:
            state = self._state_manager.load_state()
            state["risk_state"] = {
                "daily_loss_today": self._daily_loss_today,
                "consecutive_losses": self._consecutive_losses,
                "trades_today": self._trades_today,
                "last_reset_date": self._last_reset_date,
            }
            self._state_manager.save_state(state)
        except Exception:
            logger.exception("Failed to persist risk state")

    def _load_persisted_state(self) -> None:
        """Restore risk counters from state manager JSON."""
        if self._state_manager is None:
            return
        try:
            state = self._state_manager.load_state()
            risk = state.get("risk_state", {})
            if not risk:
                return

            saved_date = risk.get("last_reset_date", "")
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            if saved_date == today:
                # Same day — restore counters
                self._daily_loss_today = risk.get("daily_loss_today", 0.0)
                self._trades_today = risk.get("trades_today", 0)
                logger.info(
                    "Restored risk state: daily_loss=$%.2f, trades=%d, consecutive_losses=%d",
                    self._daily_loss_today, self._trades_today, risk.get("consecutive_losses", 0),
                )
            else:
                # New day — only restore consecutive losses (carries across days)
                logger.info("New trading day — daily counters reset, keeping consecutive losses")

            # Consecutive losses always persist (not day-bound)
            self._consecutive_losses = risk.get("consecutive_losses", 0)
            self._last_reset_date = today

        except Exception:
            logger.exception("Failed to load persisted risk state")

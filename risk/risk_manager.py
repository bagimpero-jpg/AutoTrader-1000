"""Risk management gate — enforces FTMO challenge limits."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class RiskManager:
    """Enforces per-trade, daily, and total drawdown limits for FTMO compliance."""

    def __init__(self, trading_config: dict, bridge) -> None:
        self.config = trading_config
        self.bridge = bridge
        self.base_balance: float = trading_config.get("base_balance", 10_000)
        self.max_risk_percent: float = trading_config.get("risk_percent", 1.0)
        self.min_rr: float = trading_config.get("min_rr", 2.0)
        self.max_daily_loss: float = self.base_balance * trading_config.get("max_daily_loss_percent", 5.0) / 100
        self.max_total_loss: float = self.base_balance * trading_config.get("max_total_loss_percent", 10.0) / 100
        self._daily_loss_today: float = 0.0
        self._last_reset_date: str = ""

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
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today != self._last_reset_date:
                self._daily_loss_today = 0.0
                self._last_reset_date = today

            daily_loss = balance - equity + self._daily_loss_today
            if daily_loss >= self.max_daily_loss:
                logger.warning("DAILY DRAWDOWN LIMIT: loss=$%.2f >= max=$%.2f", daily_loss, self.max_daily_loss)
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

            # Check risk amount doesn't exceed limit
            risk_amount = account["balance"] * self.max_risk_percent / 100
            if risk_amount > self.max_daily_loss - self._daily_loss_today:
                logger.warning("Trade would exceed remaining daily risk budget")
                return False

            return True

        except Exception as e:
            logger.error("Trade validation failed: %s", e)
            return False

    def record_trade_result(self, pnl: float) -> None:
        if pnl < 0:
            self._daily_loss_today += abs(pnl)

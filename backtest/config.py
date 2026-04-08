"""Backtest configuration — all tunable parameters in one place."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BacktestConfig:
    """Central configuration for the backtesting engine.

    Gold-specific defaults. Override fields or pass a custom instance
    to ``BacktestEngine`` / ``ChunkedOptimizer``.
    """

    # --- Symbol ---
    symbol: str = "XAUUSD"
    entry_tf: str = "M15"
    zone_tf: str = "H1"
    htf_tf: str = "H4"

    # --- Gold instrument specifics ---
    point: float = 0.01        # Smallest price increment for gold
    pip_size: float = 0.10     # 1 pip = $0.10 move
    tick_value: float = 1.0    # $ per tick per 1 lot (standard gold)
    volume_step: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 100.0
    spread_pips: float = 3.0   # Simulated spread in pips
    max_slippage_pips: float = 1.0  # Random 0-1 pip slippage

    # --- Risk ---
    initial_balance: float = 10_000.0
    risk_percent: float = 1.0       # % of equity per trade
    min_rr: float = 2.0             # Minimum risk:reward ratio
    max_trades_per_day: int = 3
    daily_loss_shutdown_pct: float = 2.0   # Shut down after 2% daily loss
    max_daily_loss_pct: float = 5.0        # FTMO hard limit
    max_total_loss_pct: float = 10.0       # FTMO hard limit

    # --- Recovery mode thresholds (mirrors RiskManager) ---
    recovery_consec_losses: int = 3    # Switch to 0.50% risk
    deep_recovery_consec_losses: int = 5  # Switch to 0.25% risk

    # --- SMC strategy parameters (overridable by optimizer) ---
    smc_params: dict = field(default_factory=lambda: {
        "ob_lookback": 50,
        "fvg_min_size_pips": 5,
        "liquidity_touch_threshold": 3,
        "confluence_min_score": 3,
        "swing_lookback": 5,
    })

    # --- Optimizer ---
    chunk_months: int = 6
    min_win_rate: float = 60.0   # % — trigger adjustment below this
    min_avg_rr: float = 2.0      # Trigger adjustment below this

    # --- SL cap (gold M15) ---
    max_sl_pips: float = 30.0  # Max SL distance in pips for gold

    # --- Partial close ---
    partial_close_rr: float = 1.0   # Close half at 1:1 RR
    partial_close_fraction: float = 0.5  # Close 50% of position

    # --- Data ---
    data_dir: str = "backtest/data"
    reports_dir: str = "backtest/reports"

    @property
    def symbol_info(self) -> dict:
        """Return a symbol_info dict compatible with OrderManager.calculate_lot_size()."""
        return {
            "point": self.point,
            "tick_value": self.tick_value,
            "volume_step": self.volume_step,
            "volume_min": self.volume_min,
            "volume_max": self.volume_max,
        }

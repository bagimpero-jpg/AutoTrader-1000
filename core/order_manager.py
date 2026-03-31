from __future__ import annotations

import logging
import math
from typing import Any

from core.mt5_bridge import MT5Bridge
from core.state_manager import StateManager

logger = logging.getLogger(__name__)


class OrderManager:
    """Trade execution, lot-size calculation, and startup reconciliation."""

    def __init__(self, bridge: MT5Bridge, state_manager: StateManager) -> None:
        self._bridge = bridge
        self._state = state_manager

    # ------------------------------------------------------------------
    # Lot-size calculation
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_lot_size(
        symbol_info: dict[str, Any],
        sl_distance: float,
        risk_percent: float,
        account_balance: float,
    ) -> float:
        """Compute position size based on risk.

        Formula
        -------
        risk_amount  = balance * risk_percent / 100
        raw_lots     = risk_amount / (sl_distance / point * tick_value)
        lots         = round to nearest volume_step, clamp to [volume_min, volume_max]
        """
        point: float = symbol_info["point"]
        tick_value: float = symbol_info["tick_value"]
        volume_step: float = symbol_info["volume_step"]
        volume_min: float = symbol_info["volume_min"]
        volume_max: float = symbol_info["volume_max"]

        if sl_distance <= 0 or point <= 0 or tick_value <= 0:
            raise ValueError(
                f"Invalid inputs for lot calculation: "
                f"sl_distance={sl_distance}, point={point}, tick_value={tick_value}"
            )

        risk_amount = account_balance * risk_percent / 100.0
        raw_lots = risk_amount / (sl_distance / point * tick_value)

        # Round down to nearest volume_step
        steps = math.floor(raw_lots / volume_step)
        lots = round(steps * volume_step, 8)

        lots = max(volume_min, min(lots, volume_max))
        return lots

    # ------------------------------------------------------------------
    # Trade execution
    # ------------------------------------------------------------------

    def execute_trade(self, signal: dict[str, Any]) -> int:
        """Execute a trade from a signal dict and persist to state.

        Signal keys
        -----------
        symbol, direction (BUY/SELL), entry_price, sl, tp, risk_percent, comment
        """
        symbol: str = signal["symbol"]
        direction: str = signal["direction"].upper()
        sl: float = signal["sl"]
        tp: float = signal["tp"]
        risk_percent: float = signal["risk_percent"]
        comment: str = signal.get("comment", "")

        try:
            account = self._bridge.get_account_info()
            sym_info = self._bridge.get_symbol_info(symbol)

            sl_distance = abs(signal["entry_price"] - sl)
            volume = self.calculate_lot_size(
                symbol_info=sym_info,
                sl_distance=sl_distance,
                risk_percent=risk_percent,
                account_balance=account["balance"],
            )

            ticket = self._bridge.place_order(
                symbol=symbol,
                order_type=direction,
                volume=volume,
                price=signal.get("entry_price"),
                sl=sl,
                tp=tp,
                comment=comment,
            )

            self._state.update_position(
                ticket,
                {
                    "symbol": symbol,
                    "direction": direction,
                    "volume": volume,
                    "entry_price": signal["entry_price"],
                    "sl": sl,
                    "tp": tp,
                    "comment": comment,
                },
            )

            logger.info(
                "Trade executed: ticket=%d %s %s %.2f lots",
                ticket,
                direction,
                symbol,
                volume,
            )
            return ticket

        except Exception:
            logger.exception("Failed to execute trade for signal: %s", signal)
            raise

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def close_all_positions(self) -> list[int]:
        """Emergency close every open position. Returns list of closed tickets."""
        closed: list[int] = []
        positions = self._bridge.get_open_positions()

        for pos in positions:
            ticket = pos["ticket"]
            try:
                self._bridge.close_position(ticket)
                self._state.remove_position(ticket)
                closed.append(ticket)
                logger.info("Emergency close: ticket %d", ticket)
            except Exception:
                logger.exception("Failed to emergency-close ticket %d", ticket)

        logger.info("Emergency close complete: %d/%d closed", len(closed), len(positions))
        return closed

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def reconcile_on_startup(self) -> dict[str, Any]:
        """Compare MT5 live positions with saved state and fix discrepancies."""
        report: dict[str, Any] = {
            "orphaned_in_state": [],
            "missing_from_state": [],
            "synced": 0,
        }

        try:
            recovery = self._state.get_recovery_data()
            saved_tickets: set[int] = {
                p["ticket"] for p in recovery.get("open_positions", [])
            }

            live_positions = self._bridge.get_open_positions()
            live_tickets: set[int] = {p["ticket"] for p in live_positions}

            # Positions in state but not on MT5 -- already closed externally
            orphaned = saved_tickets - live_tickets
            for ticket in orphaned:
                self._state.remove_position(ticket)
                report["orphaned_in_state"].append(ticket)
                logger.warning(
                    "Reconcile: position %d in state but not on MT5, removed", ticket
                )

            # Positions on MT5 but not in state -- opened externally or state lost
            missing = live_tickets - saved_tickets
            for pos in live_positions:
                if pos["ticket"] in missing:
                    self._state.update_position(pos["ticket"], pos)
                    report["missing_from_state"].append(pos["ticket"])
                    logger.warning(
                        "Reconcile: position %d on MT5 but not in state, added",
                        pos["ticket"],
                    )

            report["synced"] = len(live_tickets & saved_tickets)
            logger.info(
                "Reconciliation complete: synced=%d orphaned=%d missing=%d",
                report["synced"],
                len(report["orphaned_in_state"]),
                len(report["missing_from_state"]),
            )

        except Exception:
            logger.exception("Reconciliation failed")
            raise

        return report

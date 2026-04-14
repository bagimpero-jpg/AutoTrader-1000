from __future__ import annotations

import logging
import time
from typing import Any

import MetaTrader5 as mt5
import pandas as pd

logger = logging.getLogger(__name__)

TIMEFRAME_MAP: dict[str, int] = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


class MT5Bridge:
    """Bridge to MetaTrader 5 terminal providing account, market data, and trade execution."""

    def __init__(self) -> None:
        self._connected: bool = False
        self._credentials: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(
        self,
        login: int,
        password: str,
        server: str,
        mt5_path: str | None = None,
    ) -> bool:
        """Initialize MT5 terminal with aggressive retry (up to 10 attempts, exponential backoff)."""
        max_retries = 10
        base_delay = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                logger.info("MT5 connection attempt %d/%d", attempt, max_retries)

                init_kwargs: dict[str, Any] = {
                    "login": login,
                    "password": password,
                    "server": server,
                }
                if mt5_path:
                    init_kwargs["path"] = mt5_path

                if not mt5.initialize(**init_kwargs):
                    error = mt5.last_error()
                    logger.warning(
                        "MT5 initialize failed (attempt %d): %s", attempt, error
                    )
                    if attempt < max_retries:
                        delay = base_delay * (2 ** (attempt - 1))
                        logger.info("Retrying in %.1f s ...", delay)
                        time.sleep(delay)
                    continue

                self._connected = True
                self._credentials = {
                    "login": login, "password": password,
                    "server": server, "mt5_path": mt5_path,
                }
                logger.info("MT5 connected successfully on attempt %d", attempt)
                return True

            except Exception:
                logger.exception("Unexpected error on connection attempt %d", attempt)
                if attempt < max_retries:
                    delay = base_delay * (2 ** (attempt - 1))
                    time.sleep(delay)

        logger.error("Failed to connect to MT5 after %d attempts", max_retries)
        return False

    def disconnect(self) -> None:
        """Safely shut down MT5 connection."""
        try:
            if self._connected:
                mt5.shutdown()
                self._connected = False
                logger.info("MT5 disconnected")
        except Exception:
            logger.exception("Error during MT5 disconnect")

    def ensure_connected(self) -> bool:
        """Check MT5 connection is alive; reconnect if dropped."""
        try:
            info = mt5.terminal_info()
            if info is not None:
                return True
        except Exception:
            pass

        logger.warning("MT5 connection lost. Attempting reconnect...")
        self._connected = False

        if not self._credentials:
            logger.error("No stored credentials — cannot auto-reconnect")
            return False

        return self.connect(
            login=self._credentials["login"],
            password=self._credentials["password"],
            server=self._credentials["server"],
            mt5_path=self._credentials.get("mt5_path"),
        )

    # ------------------------------------------------------------------
    # Account & Market Data
    # ------------------------------------------------------------------

    def get_account_info(self) -> dict[str, float]:
        """Return core account metrics."""
        try:
            info = mt5.account_info()
            if info is None:
                raise RuntimeError(f"account_info failed: {mt5.last_error()}")
            return {
                "balance": info.balance,
                "equity": info.equity,
                "margin": info.margin,
                "free_margin": info.margin_free,
                "profit": info.profit,
            }
        except Exception:
            logger.exception("Failed to get account info")
            raise

    def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        """Return symbol specification details."""
        try:
            info = mt5.symbol_info(symbol)
            if info is None:
                raise RuntimeError(
                    f"symbol_info({symbol}) failed: {mt5.last_error()}"
                )
            return {
                "bid": info.bid,
                "ask": info.ask,
                "spread": info.spread,
                "point": info.point,
                "digits": info.digits,
                "tick_size": info.trade_tick_size,
                "tick_value": info.trade_tick_value,
                "volume_min": info.volume_min,
                "volume_max": info.volume_max,
                "volume_step": info.volume_step,
            }
        except Exception:
            logger.exception("Failed to get symbol info for %s", symbol)
            raise

    def get_candles(
        self, symbol: str, timeframe: str, count: int = 500
    ) -> pd.DataFrame:
        """Fetch OHLCV candles as a DataFrame."""
        try:
            tf = TIMEFRAME_MAP.get(timeframe.upper())
            if tf is None:
                raise ValueError(
                    f"Unknown timeframe '{timeframe}'. "
                    f"Valid: {', '.join(TIMEFRAME_MAP)}"
                )

            rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
            if rates is None or len(rates) == 0:
                raise RuntimeError(
                    f"copy_rates_from_pos({symbol}, {timeframe}) "
                    f"failed: {mt5.last_error()}"
                )

            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            return df[["time", "open", "high", "low", "close", "tick_volume"]].rename(
                columns={"tick_volume": "volume"}
            )
        except Exception:
            logger.exception("Failed to get candles for %s %s", symbol, timeframe)
            raise

    # ------------------------------------------------------------------
    # Order Execution
    # ------------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        order_type: str,
        volume: float,
        price: float | None = None,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = "",
    ) -> int:
        """Place an order and return the ticket number."""
        try:
            type_map: dict[str, int] = {
                "BUY": mt5.ORDER_TYPE_BUY,
                "SELL": mt5.ORDER_TYPE_SELL,
                "BUY_LIMIT": mt5.ORDER_TYPE_BUY_LIMIT,
                "SELL_LIMIT": mt5.ORDER_TYPE_SELL_LIMIT,
                "BUY_STOP": mt5.ORDER_TYPE_BUY_STOP,
                "SELL_STOP": mt5.ORDER_TYPE_SELL_STOP,
            }

            mt5_type = type_map.get(order_type.upper())
            if mt5_type is None:
                raise ValueError(f"Unknown order_type '{order_type}'")

            if price is None:
                tick = mt5.symbol_info_tick(symbol)
                if tick is None:
                    raise RuntimeError(
                        f"symbol_info_tick({symbol}) failed: {mt5.last_error()}"
                    )
                price = tick.ask if "BUY" in order_type.upper() else tick.bid

            request: dict[str, Any] = {
                "action": mt5.TRADE_ACTION_DEAL
                if mt5_type in (mt5.ORDER_TYPE_BUY, mt5.ORDER_TYPE_SELL)
                else mt5.TRADE_ACTION_PENDING,
                "symbol": symbol,
                "volume": volume,
                "type": mt5_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            # Retry loop for transient failures (requotes, rejects)
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                result = mt5.order_send(request)

                if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(
                        "Order placed: ticket=%d symbol=%s type=%s vol=%.2f (attempt %d)",
                        result.order, symbol, order_type, volume, attempt,
                    )
                    return result.order

                retcode = result.retcode if result else -1
                error_detail = result.comment if result else str(mt5.last_error())

                # Requote — refresh price and retry
                if retcode == mt5.TRADE_RETCODE_REQUOTE and attempt < max_attempts:
                    logger.warning("Requote on %s (attempt %d) — refreshing price", symbol, attempt)
                    tick = mt5.symbol_info_tick(symbol)
                    if tick:
                        request["price"] = tick.ask if "BUY" in order_type.upper() else tick.bid
                    time.sleep(0.3)
                    continue

                # Temporary reject — wait and retry
                if retcode == mt5.TRADE_RETCODE_REJECT and attempt < max_attempts:
                    logger.warning("Order rejected on %s (attempt %d) — retrying", symbol, attempt)
                    time.sleep(0.5)
                    continue

                # Permanent failure
                raise RuntimeError(f"order_send failed after {attempt} attempts: {error_detail}")

            raise RuntimeError(f"order_send failed after {max_attempts} attempts")

        except Exception:
            logger.exception("Failed to place order %s %s", order_type, symbol)
            raise

    def modify_order(self, ticket: int, sl: float, tp: float) -> bool:
        """Modify SL/TP of an existing position."""
        try:
            position = mt5.positions_get(ticket=ticket)
            if not position:
                raise RuntimeError(f"Position {ticket} not found")

            pos = position[0]
            request: dict[str, Any] = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "symbol": pos.symbol,
                "sl": sl,
                "tp": tp,
            }

            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                error_detail = result.comment if result else mt5.last_error()
                raise RuntimeError(f"modify_order failed: {error_detail}")

            logger.info("Position %d modified: sl=%.5f tp=%.5f", ticket, sl, tp)
            return True

        except Exception:
            logger.exception("Failed to modify position %d", ticket)
            raise

    def close_partial(self, ticket: int, volume: float) -> bool:
        """Partially close a position by closing the specified volume."""
        try:
            position = mt5.positions_get(ticket=ticket)
            if not position:
                raise RuntimeError(f"Position {ticket} not found")

            pos = position[0]
            close_type = (
                mt5.ORDER_TYPE_SELL
                if pos.type == mt5.ORDER_TYPE_BUY
                else mt5.ORDER_TYPE_BUY
            )

            tick = mt5.symbol_info_tick(pos.symbol)
            if tick is None:
                raise RuntimeError(
                    f"symbol_info_tick({pos.symbol}) failed: {mt5.last_error()}"
                )

            price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

            request: dict[str, Any] = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": volume,
                "type": close_type,
                "position": ticket,
                "price": price,
                "comment": "partial_close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                error_detail = result.comment if result else mt5.last_error()
                raise RuntimeError(f"close_partial failed: {error_detail}")

            logger.info("Position %d partially closed: %.2f lots", ticket, volume)
            return True

        except Exception:
            logger.exception("Failed to partially close position %d", ticket)
            raise

    def close_position(self, ticket: int) -> bool:
        """Close an open position by ticket."""
        try:
            position = mt5.positions_get(ticket=ticket)
            if not position:
                raise RuntimeError(f"Position {ticket} not found")

            pos = position[0]
            close_type = (
                mt5.ORDER_TYPE_SELL
                if pos.type == mt5.ORDER_TYPE_BUY
                else mt5.ORDER_TYPE_BUY
            )

            tick = mt5.symbol_info_tick(pos.symbol)
            if tick is None:
                raise RuntimeError(
                    f"symbol_info_tick({pos.symbol}) failed: {mt5.last_error()}"
                )

            price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

            request: dict[str, Any] = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": close_type,
                "position": ticket,
                "price": price,
                "comment": "close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                error_detail = result.comment if result else mt5.last_error()
                raise RuntimeError(f"close_position failed: {error_detail}")

            logger.info("Position %d closed", ticket)
            return True

        except Exception:
            logger.exception("Failed to close position %d", ticket)
            raise

    # ------------------------------------------------------------------
    # Margin Calculation
    # ------------------------------------------------------------------

    def get_max_volume_for_margin(
        self, symbol: str, direction: str, desired_volume: float, max_margin: float,
    ) -> float:
        """Return the largest volume <= desired_volume that fits within max_margin."""
        try:
            order_type = mt5.ORDER_TYPE_BUY if "BUY" in direction.upper() else mt5.ORDER_TYPE_SELL
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return desired_volume  # can't check, proceed with original

            price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid
            sym = mt5.symbol_info(symbol)
            if sym is None:
                return desired_volume

            # Check margin for desired volume
            margin = mt5.order_calc_margin(order_type, symbol, desired_volume, price)
            if margin is not None and margin <= max_margin:
                return desired_volume  # fits fine

            # Binary search for max affordable volume
            vol_min = sym.volume_min
            vol_max = desired_volume
            best = vol_min

            for _ in range(20):  # converges fast
                mid = round((vol_min + vol_max) / 2, 2)
                if mid < sym.volume_min:
                    break
                m = mt5.order_calc_margin(order_type, symbol, mid, price)
                if m is not None and m <= max_margin:
                    best = mid
                    vol_min = mid + sym.volume_step
                else:
                    vol_max = mid - sym.volume_step

            # Snap to volume_step
            steps = int(best / sym.volume_step)
            return round(steps * sym.volume_step, 2)

        except Exception:
            logger.exception("Margin calc failed for %s — using desired volume", symbol)
            return desired_volume

    # ------------------------------------------------------------------
    # Deal History
    # ------------------------------------------------------------------

    def get_deal_profit(self, ticket: int) -> float:
        """Fetch total realized P&L for a position ticket from MT5 deal history."""
        try:
            # Search deals from last 30 days for this position
            from datetime import datetime, timedelta, timezone
            date_from = datetime.now(timezone.utc) - timedelta(days=30)
            date_to = datetime.now(timezone.utc) + timedelta(days=1)

            deals = mt5.history_deals_get(date_from, date_to, position=ticket)
            if deals is None or len(deals) == 0:
                logger.warning("No deals found for ticket %d", ticket)
                return 0.0

            total_profit = sum(d.profit + d.commission + d.swap for d in deals)
            logger.info("Deal history for ticket %d: profit=%.2f (from %d deals)",
                        ticket, total_profit, len(deals))
            return total_profit

        except Exception:
            logger.exception("Failed to get deal profit for ticket %d", ticket)
            return 0.0

    # ------------------------------------------------------------------
    # Position / Order Queries
    # ------------------------------------------------------------------

    def get_open_positions(self) -> list[dict[str, Any]]:
        """Return all open positions as a list of dicts."""
        try:
            positions = mt5.positions_get()
            if positions is None:
                logger.warning("positions_get returned None: %s", mt5.last_error())
                return []

            return [
                {
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                    "volume": p.volume,
                    "price_open": p.price_open,
                    "price_current": p.price_current,
                    "sl": p.sl,
                    "tp": p.tp,
                    "profit": p.profit,
                    "comment": p.comment,
                    "time": p.time,
                }
                for p in positions
            ]

        except Exception:
            logger.exception("Failed to get open positions")
            return []

    def get_pending_orders(self) -> list[dict[str, Any]]:
        """Return all pending orders as a list of dicts."""
        try:
            orders = mt5.orders_get()
            if orders is None:
                logger.warning("orders_get returned None: %s", mt5.last_error())
                return []

            return [
                {
                    "ticket": o.ticket,
                    "symbol": o.symbol,
                    "type": o.type,
                    "volume_current": o.volume_current,
                    "price_open": o.price_open,
                    "sl": o.sl,
                    "tp": o.tp,
                    "comment": o.comment,
                    "time_setup": o.time_setup,
                }
                for o in orders
            ]

        except Exception:
            logger.exception("Failed to get pending orders")
            return []

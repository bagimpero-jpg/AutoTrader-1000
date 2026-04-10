"""Auto Trader 1000 — Autonomous FTMO Challenge Trading Bot."""
from __future__ import annotations

import csv
import logging
import os
import signal
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

from core.mt5_bridge import MT5Bridge
from core.state_manager import StateManager
from core.order_manager import OrderManager
from strategy.smc_engine import SMCEngine
from strategy.structures import StructureAnalyzer
from strategy.zones import ZoneDetector
from strategy.liquidity import LiquidityAnalyzer
from strategy.session_profiler import SessionProfiler
from cloud.cloud_logger import GoogleSheetsLogger, PostgresLogger, CompositeLogger
from cloud.trade_journal import TradeJournal
from reflection.self_reflection import SelfReflection
from risk.risk_manager import RiskManager
from risk.news_filter import NewsFilter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("autotrader.log"),
    ],
)
logger = logging.getLogger("AutoTrader1000")


class AutoTrader:
    """Main orchestrator that wires all modules and runs the trading loop."""

    def __init__(self, config_path: str = "config/settings.yaml"):
        self.config = self._load_config(config_path)
        self.running = False

        # Core
        self.bridge = MT5Bridge()
        self.state = StateManager()
        self.order_mgr = OrderManager(self.bridge, self.state)

        # Strategy
        self.structure_analyzer = StructureAnalyzer()
        self.zone_detector = ZoneDetector()
        self.liquidity_analyzer = LiquidityAnalyzer()
        self.session_profiler = SessionProfiler()
        self.smc_engine = SMCEngine(
            structure_analyzer=self.structure_analyzer,
            zone_detector=self.zone_detector,
            liquidity_analyzer=self.liquidity_analyzer,
            session_profiler=self.session_profiler,
        )

        # Risk (pass state_manager for persistence across restarts)
        self.risk_manager = RiskManager(self.config["trading"], self.bridge, self.state)
        self.news_filter = NewsFilter()

        # Cloud & Reflection
        self.cloud_logger = self._init_cloud_logger()
        self.journal = TradeJournal(self.cloud_logger)
        self.reflection = SelfReflection(self.journal)

        # Load knowledge base
        kb_path = Path("knowledge_base")
        if kb_path.exists():
            self.smc_engine.load_knowledge_base(str(kb_path))

    def _load_config(self, path: str) -> dict:
        with open(path, "r") as f:
            return yaml.safe_load(f)

    def _init_cloud_logger(self) -> CompositeLogger:
        loggers = []
        cloud_cfg = self.config.get("cloud", {})
        backend = cloud_cfg.get("backend", "google_sheets")

        if backend in ("google_sheets", "both"):
            gs_cfg = cloud_cfg.get("google_sheets", {})
            creds = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", gs_cfg.get("credentials_path", ""))
            if creds:
                try:
                    loggers.append(GoogleSheetsLogger(
                        spreadsheet_name=gs_cfg.get("spreadsheet_name", "AutoTrader1000_Journal"),
                        credentials_path=creds,
                    ))
                except Exception as e:
                    logger.warning("Google Sheets logger init failed: %s", e)

        if backend in ("postgres", "both"):
            pg_url = os.environ.get("POSTGRES_URL", cloud_cfg.get("postgres", {}).get("url", ""))
            if pg_url:
                try:
                    loggers.append(PostgresLogger(connection_url=pg_url))
                except Exception as e:
                    logger.warning("Postgres logger init failed: %s", e)

        return CompositeLogger(loggers=loggers)

    def start(self) -> None:
        logger.info("=== Auto Trader 1000 starting ===")

        # Connect to MT5
        self.bridge.connect(
            login=int(os.environ.get("FTMO_LOGIN", 0)),
            password=os.environ.get("FTMO_PASSWORD", ""),
            server=os.environ.get("FTMO_SERVER", ""),
            mt5_path=os.environ.get("MT5_PATH", ""),
        )

        # Reconcile state after restart
        self.order_mgr.reconcile_on_startup()

        # Register shutdown handler
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

        self.running = True
        logger.info("Bot is live. Entering main loop.")
        self._main_loop()

    def _main_loop(self) -> None:
        cfg = self.config["trading"]
        symbols = cfg["symbols"]
        smc_cfg = self.config.get("smc", {})
        swing_lookback = smc_cfg.get("swing_lookback", 5)
        confluence_min = smc_cfg.get("confluence_min_score", 3)
        poll_interval = cfg.get("poll_interval_seconds", 30)

        while self.running:
            try:
                # Ensure MT5 is still connected (auto-reconnect if dropped)
                if not self.bridge.ensure_connected():
                    logger.error("MT5 reconnect failed. Retrying in 30s...")
                    time.sleep(30)
                    continue

                utc_now = datetime.now(timezone.utc)

                # Check session
                if not self.session_profiler.is_execution_allowed(utc_now):
                    session = self.session_profiler.get_current_session(utc_now)
                    if session == "ASIAN":
                        self._run_asian_profiling(symbols, utc_now)
                    time.sleep(poll_interval)
                    continue

                # Check risk limits before scanning
                if not self.risk_manager.can_trade():
                    logger.warning("Risk limits reached. Pausing new entries.")
                    time.sleep(poll_interval)
                    continue

                # Multi-TF scan: H4 bias → H1 zones → M15 entry (matches backtest)
                for symbol in symbols:
                    self._scan_multi_tf(symbol, utc_now, swing_lookback, confluence_min)

                # Check existing positions for management
                self._manage_open_positions()

                time.sleep(poll_interval)

            except Exception as e:
                logger.error("Main loop error: %s", e, exc_info=True)
                self.cloud_logger.log_error({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "error": str(e),
                    "context": "main_loop",
                })
                time.sleep(5)

    def _scan_multi_tf(
        self, symbol: str, utc_now: datetime,
        swing_lookback: int = 5, confluence_min: int = 3,
    ) -> None:
        """Multi-timeframe scan matching the proven backtest approach.

        H4 → trend bias, H1 → zone context, M15 → entry signals.
        """
        from strategy.structures import TrendDirection

        # Check news filter
        blocked, reason = self.news_filter.is_blocked(symbol, utc_now)
        if blocked:
            logger.info("Skipping %s: news block — %s", symbol, reason)
            return

        # Step 1: H4 trend bias
        h4_df = self.bridge.get_candles(symbol, "H4", count=200)
        h4_trend = TrendDirection.RANGING
        htf_liq_targets: list[float] = []
        if h4_df is not None and len(h4_df) >= 20:
            h4_analysis = self.smc_engine.analyze(h4_df, symbol, "H4")
            h4_trend = h4_analysis.trend
            for pool in h4_analysis.liquidity_pools:
                htf_liq_targets.append(pool.level)
            for sp in h4_analysis.swing_points:
                htf_liq_targets.append(sp.price)

        # Step 2: H1 zone context
        h1_df = self.bridge.get_candles(symbol, "H1", count=200)
        h1_zones = []
        if h1_df is not None and len(h1_df) >= 20:
            h1_analysis = self.smc_engine.analyze(h1_df, symbol, "H1")
            h1_zones = h1_analysis.active_zones
            for pool in h1_analysis.liquidity_pools:
                if pool.level not in htf_liq_targets:
                    htf_liq_targets.append(pool.level)

        # Step 3: M15 entry signals
        m15_df = self.bridge.get_candles(symbol, "M15", count=200)
        if m15_df is None or m15_df.empty:
            return

        m15_analysis = self.smc_engine.analyze(m15_df, symbol, "M15")

        signals = self.smc_engine.generate_signals(
            m15_analysis,
            symbol=symbol,
            timeframe="M15",
            htf_zones=h1_zones if h1_zones else None,
            h4_trend=h4_trend,
            amd_phase=m15_analysis.amd_phase,
            htf_liquidity_targets=htf_liq_targets if htf_liq_targets else None,
            pip_size=0.10,       # Gold
            max_sl_pips=30.0,
            min_sl_pips=10.0,
        )

        # Spread check — skip if spread is too wide (spike protection)
        max_spread_pips = self.config["trading"].get("max_spread_pips", 5.0)
        try:
            sym_info = self.bridge.get_symbol_info(symbol)
            current_spread = sym_info["spread"] * sym_info["point"] / 0.10  # convert to pips for gold
            if current_spread > max_spread_pips:
                logger.info("Skipping %s: spread %.1f pips > max %.1f pips", symbol, current_spread, max_spread_pips)
                return
        except Exception:
            pass  # proceed if spread check fails — better to trade than miss everything

        min_rr = self.config["trading"]["min_rr"]
        for sig in signals:
            if sig.confluence_score < confluence_min:
                continue
            if sig.rr_ratio < min_rr:
                continue
            if not self.risk_manager.validate_trade(sig):
                continue

            direction_str = sig.direction.value if hasattr(sig.direction, "value") else str(sig.direction)
            trade_dict = {
                "symbol": sig.symbol,
                "direction": direction_str,
                "entry_price": sig.entry_price,
                "sl": sig.sl,
                "tp": sig.tp,
                "risk_percent": self.risk_manager.get_risk_for_signal(sig),
                "comment": f"SMC|{sig.confluence_score}|{'|'.join(sig.reasoning[:2])}",
            }

            try:
                execution_result = self.order_mgr.execute_trade(trade_dict)
            except Exception as e:
                logger.error("Order execution failed: %s", e)
                continue

            if execution_result:
                self.risk_manager.record_trade_opened()
                ticket = execution_result["ticket"]
                signal_dict = asdict(sig)
                signal_dict["direction"] = direction_str
                signal_dict["session"] = self.session_profiler.get_current_session(utc_now)
                signal_dict["smc_setup_type"] = sig.reasoning[0] if sig.reasoning else ""
                self.journal.open_entry(signal_dict, execution_result)
                logger.info("Trade opened: %s %s @ %.2f | SL: %.2f | TP: %.2f | RR: %.2f",
                            direction_str, sig.symbol, sig.entry_price, sig.sl, sig.tp, sig.rr_ratio)

    def _run_asian_profiling(self, symbols: list[str], utc_now: datetime) -> None:
        for symbol in symbols:
            df = self.bridge.get_candles(symbol, "M15", count=100)
            if df is not None and not df.empty:
                asian_range = self.session_profiler.get_asian_range(df, utc_now.date())
                if asian_range:
                    logger.debug("Asian range %s: H=%.5f L=%.5f",
                                 symbol, asian_range["high"], asian_range["low"])

    def _manage_open_positions(self) -> None:
        # Partial close at 1:1 RR and move SL to break-even
        risk_freed = self.order_mgr.check_and_manage_positions()
        if risk_freed:
            logger.info("Risk-freed positions this cycle: %s", risk_freed)

        positions = self.bridge.get_open_positions()
        if not positions:
            return

        # Detect recently closed trades for journaling
        saved = self.state.load_state()
        saved_tickets = {p["ticket"] for p in saved.get("open_positions", [])}
        live_tickets = {p["ticket"] for p in positions}

        closed_tickets = saved_tickets - live_tickets
        for ticket in closed_tickets:
            # Fetch REAL PnL from MT5 deal history (not 0.0)
            actual_pnl = self.bridge.get_deal_profit(ticket)
            self._on_trade_closed(ticket, pnl=actual_pnl)

    def _on_trade_closed(self, ticket: int, pnl: float = 0.0) -> None:
        logger.info("Trade %d closed (PnL: $%.2f). Running post-trade analysis.", ticket, pnl)

        # Grab position data BEFORE removing from state (needed for CSV log)
        saved = self.state.load_state()
        pos_data = None
        for p in saved.get("open_positions", []):
            if p.get("ticket") == ticket:
                pos_data = p
                break

        self.risk_manager.record_trade_result(pnl)
        self.state.remove_position(ticket)

        # Log to CSV
        try:
            self._log_trade_csv(ticket, pnl, pos_data)
        except Exception as e:
            logger.error("CSV trade log error: %s", e)

        # Log closure to journal
        try:
            self.journal.close_entry(ticket, {
                "closed_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.error("Failed to journal trade close: %s", e)

        # Run reflection if enough trades
        try:
            report = self.reflection.analyze_recent_trades()
            if report and report.patterns:
                failing = self.reflection.identify_failing_patterns()
                if failing:
                    logger.warning("Failing patterns detected: %s",
                                   [p.pattern_name for p in failing])
        except Exception as e:
            logger.error("Reflection error: %s", e)

    # ── CSV Trade Logger ─────────────────────────────────────────────

    TRADE_CSV_HEADERS = [
        "date", "symbol", "direction", "entry_price", "exit_price",
        "sl", "tp", "pnl_dollars", "rr_achieved", "result",
        "session", "duration_mins",
    ]

    def _log_trade_csv(
        self, ticket: int, pnl: float, pos_data: dict | None,
    ) -> None:
        """Append a closed trade row to logs/trades.csv."""
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        csv_path = logs_dir / "trades.csv"

        write_header = not csv_path.exists()

        now = datetime.now(timezone.utc)
        symbol = pos_data.get("symbol", "XAUUSD") if pos_data else "XAUUSD"
        direction = pos_data.get("type", "") if pos_data else ""
        entry_price = pos_data.get("price_open", 0.0) if pos_data else 0.0
        sl = pos_data.get("sl", 0.0) if pos_data else 0.0
        tp = pos_data.get("tp", 0.0) if pos_data else 0.0

        # Get current price as approximate exit
        try:
            info = self.bridge.get_symbol_info(symbol)
            exit_price = info.get("bid", 0.0) if direction == "BUY" else info.get("ask", 0.0)
        except Exception:
            exit_price = 0.0

        # RR achieved
        risk_dist = abs(entry_price - sl) if entry_price and sl else 1.0
        rr = round(abs(pnl / 100.0) / risk_dist, 2) if risk_dist > 0 else 0.0  # pnl per 0.01 lot approx

        result = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BE"
        session = self.session_profiler.get_current_session(now)

        # Duration
        open_time = pos_data.get("time", 0) if pos_data else 0
        if open_time:
            duration = int((now.timestamp() - open_time) / 60)
        else:
            duration = 0

        row = {
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "symbol": symbol,
            "direction": direction,
            "entry_price": f"{entry_price:.2f}",
            "exit_price": f"{exit_price:.2f}",
            "sl": f"{sl:.2f}",
            "tp": f"{tp:.2f}",
            "pnl_dollars": f"{pnl:.2f}",
            "rr_achieved": f"{rr:.2f}",
            "result": result,
            "session": session,
            "duration_mins": str(duration),
        }

        with open(csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.TRADE_CSV_HEADERS)
            if write_header:
                writer.writeheader()
            writer.writerow(row)

        logger.info("Trade %d logged to CSV: %s %s PnL=$%.2f RR=%.2f",
                     ticket, result, symbol, pnl, rr)

        # Check if we need a monthly summary
        self._maybe_write_monthly_summary(csv_path, now)

    def _maybe_write_monthly_summary(self, csv_path: Path, now: datetime) -> None:
        """Append a MONTH_SUMMARY row if the month just changed."""
        marker_path = Path("logs/.last_summary_month")
        current_month = now.strftime("%Y-%m")

        if marker_path.exists():
            last_month = marker_path.read_text().strip()
            if last_month == current_month:
                return  # already summarized this month
            summary_month = last_month
        else:
            marker_path.parent.mkdir(exist_ok=True)
            marker_path.write_text(current_month)
            return  # first trade ever, no previous month to summarize

        # Read all trades for the previous month
        try:
            with open(csv_path, "r", newline="") as f:
                all_trades = [r for r in csv.DictReader(f)
                              if r.get("date", "").startswith(summary_month)
                              and r.get("result") in ("WIN", "LOSS", "BE")]
        except Exception:
            marker_path.write_text(current_month)
            return

        if not all_trades:
            marker_path.write_text(current_month)
            return

        total_pnl = sum(float(t.get("pnl_dollars", 0)) for t in all_trades)
        wins = sum(1 for t in all_trades if t["result"] == "WIN")
        win_rate = (wins / len(all_trades) * 100) if all_trades else 0
        rr_vals = [float(t.get("rr_achieved", 0)) for t in all_trades if float(t.get("rr_achieved", 0)) > 0]
        avg_rr = sum(rr_vals) / len(rr_vals) if rr_vals else 0

        summary = {
            "date": f"MONTH_SUMMARY_{summary_month}",
            "symbol": "XAUUSD",
            "direction": "-",
            "entry_price": "-",
            "exit_price": "-",
            "sl": "-",
            "tp": "-",
            "pnl_dollars": f"{total_pnl:.2f}",
            "rr_achieved": f"{avg_rr:.2f}",
            "result": f"{win_rate:.1f}%WR",
            "session": f"{len(all_trades)}trades",
            "duration_mins": "-",
        }

        with open(csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.TRADE_CSV_HEADERS)
            writer.writerow(summary)

        logger.info("Monthly summary for %s: PnL=$%.2f | WR=%.1f%% | Avg RR=%.2f | %d trades",
                     summary_month, total_pnl, win_rate, avg_rr, len(all_trades))
        marker_path.write_text(current_month)

    def _shutdown_handler(self, signum: int, frame) -> None:
        logger.info("Shutdown signal received. Cleaning up...")
        self.running = False
        self.state.save_state(self.state.load_state())
        self.bridge.disconnect()
        logger.info("Auto Trader 1000 shut down cleanly.")
        sys.exit(0)


if __name__ == "__main__":
    bot = AutoTrader()
    bot.start()

"""Auto Trader 1000 — Autonomous FTMO Challenge Trading Bot."""
from __future__ import annotations

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

        # Risk
        self.risk_manager = RiskManager(self.config["trading"], self.bridge)
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
        timeframes = cfg["timeframes"]
        poll_interval = cfg.get("poll_interval_seconds", 30)

        while self.running:
            try:
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

                # Scan each symbol/timeframe for signals
                for symbol in symbols:
                    for tf in timeframes:
                        self._scan_and_execute(symbol, tf, utc_now)

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

    def _scan_and_execute(self, symbol: str, timeframe: str, utc_now: datetime) -> None:
        df = self.bridge.get_candles(symbol, timeframe, count=200)
        if df is None or df.empty:
            return

        # Check news filter
        blocked, reason = self.news_filter.is_blocked(symbol, utc_now)
        if blocked:
            logger.info("Skipping %s: news block — %s", symbol, reason)
            return

        analysis = self.smc_engine.analyze(df, symbol, timeframe)
        signals = self.smc_engine.generate_signals(analysis, symbol=symbol, timeframe=timeframe)

        for sig in signals:
            if sig.rr_ratio < self.config["trading"]["min_rr"]:
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
                logger.info("Trade opened: %s %s @ %.5f | SL: %.5f | TP: %.5f | RR: %.2f",
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
            self._on_trade_closed(ticket)

    def _on_trade_closed(self, ticket: int, pnl: float = 0.0) -> None:
        logger.info("Trade %d closed (PnL: $%.2f). Running post-trade analysis.", ticket, pnl)
        self.risk_manager.record_trade_result(pnl)
        self.state.remove_position(ticket)

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

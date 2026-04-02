from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class CloudLogger(ABC):
    """Abstract base class for cloud logging backends."""

    @abstractmethod
    def log_trade_open(self, trade_data: dict) -> None: ...

    @abstractmethod
    def log_trade_close(self, trade_data: dict, result: dict) -> None: ...

    @abstractmethod
    def log_daily_summary(self, summary: dict) -> None: ...

    @abstractmethod
    def log_error(self, error_data: dict) -> None: ...

    @abstractmethod
    def get_trade_history(self, filters: dict | None = None) -> list[dict]: ...

    @abstractmethod
    def get_performance_stats(self, date_range: tuple[str, str] | None = None) -> dict: ...


class GoogleSheetsLogger(CloudLogger):
    """Cloud logger that persists trade data to Google Sheets via gspread."""

    TRADE_HEADERS = [
        "timestamp", "symbol", "direction", "entry", "sl", "tp", "lot_size",
        "rr_ratio", "confluence_score", "reasoning", "result_pips",
        "result_dollars", "duration", "session", "smc_setup_type",
    ]

    def __init__(self, spreadsheet_name: str, credentials_path: str) -> None:
        self._spreadsheet_name = spreadsheet_name
        self._credentials_path = credentials_path
        self._client: Any = None
        self._spreadsheet: Any = None
        self._connect()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        try:
            import gspread
            self._client = gspread.service_account(filename=self._credentials_path)
            self._spreadsheet = self._client.open(self._spreadsheet_name)
        except Exception:
            logger.exception("Failed to connect to Google Sheets")

    def _get_or_create_sheet(self, title: str, headers: list[str] | None = None) -> Any:
        try:
            return self._spreadsheet.worksheet(title)
        except Exception:
            ws = self._spreadsheet.add_worksheet(title=title, rows=1000, cols=20)
            if headers:
                ws.append_row(headers)
            return ws

    def _safe_append(self, sheet_title: str, row: list, headers: list[str] | None = None) -> None:
        try:
            ws = self._get_or_create_sheet(sheet_title, headers)
            ws.append_row(row)
        except Exception:
            logger.exception("Google Sheets append failed for sheet %s", sheet_title)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_trade_open(self, trade_data: dict) -> None:
        try:
            row = [
                trade_data.get("timestamp", datetime.utcnow().isoformat()),
                trade_data.get("symbol", ""),
                trade_data.get("direction", ""),
                trade_data.get("entry", ""),
                trade_data.get("sl", ""),
                trade_data.get("tp", ""),
                trade_data.get("lot_size", ""),
                trade_data.get("rr_ratio", ""),
                trade_data.get("confluence_score", ""),
                trade_data.get("reasoning", ""),
                "",  # result_pips — filled on close
                "",  # result_dollars
                "",  # duration
                trade_data.get("session", ""),
                trade_data.get("smc_setup_type", ""),
            ]
            self._safe_append("trades", row, self.TRADE_HEADERS)
        except Exception:
            logger.exception("log_trade_open failed")

    def log_trade_close(self, trade_data: dict, result: dict) -> None:
        try:
            row = [
                trade_data.get("timestamp", datetime.utcnow().isoformat()),
                trade_data.get("symbol", ""),
                trade_data.get("direction", ""),
                trade_data.get("entry", ""),
                trade_data.get("sl", ""),
                trade_data.get("tp", ""),
                trade_data.get("lot_size", ""),
                trade_data.get("rr_ratio", ""),
                trade_data.get("confluence_score", ""),
                trade_data.get("reasoning", ""),
                result.get("result_pips", ""),
                result.get("result_dollars", ""),
                result.get("duration", ""),
                trade_data.get("session", ""),
                trade_data.get("smc_setup_type", ""),
            ]
            self._safe_append("trades", row, self.TRADE_HEADERS)
        except Exception:
            logger.exception("log_trade_close failed")

    def log_daily_summary(self, summary: dict) -> None:
        try:
            headers = ["date", "total_trades", "wins", "losses", "pnl_pips", "pnl_dollars", "win_rate"]
            row = [
                summary.get("date", datetime.utcnow().strftime("%Y-%m-%d")),
                summary.get("total_trades", 0),
                summary.get("wins", 0),
                summary.get("losses", 0),
                summary.get("pnl_pips", 0),
                summary.get("pnl_dollars", 0),
                summary.get("win_rate", 0),
            ]
            self._safe_append("daily_summary", row, headers)
        except Exception:
            logger.exception("log_daily_summary failed")

    def log_error(self, error_data: dict) -> None:
        try:
            headers = ["timestamp", "error_type", "message", "traceback", "context"]
            row = [
                error_data.get("timestamp", datetime.utcnow().isoformat()),
                error_data.get("error_type", ""),
                error_data.get("message", ""),
                error_data.get("traceback", ""),
                str(error_data.get("context", "")),
            ]
            self._safe_append("errors", row, headers)
        except Exception:
            logger.exception("log_error failed")

    def get_trade_history(self, filters: dict | None = None) -> list[dict]:
        try:
            ws = self._get_or_create_sheet("trades", self.TRADE_HEADERS)
            records = ws.get_all_records()
            if not filters:
                return records
            filtered: list[dict] = []
            for rec in records:
                if all(rec.get(k) == v for k, v in filters.items()):
                    filtered.append(rec)
            return filtered
        except Exception:
            logger.exception("get_trade_history failed")
            return []

    def get_performance_stats(self, date_range: tuple[str, str] | None = None) -> dict:
        try:
            ws = self._get_or_create_sheet("performance")
            records = ws.get_all_records()
            if date_range:
                start, end = date_range
                records = [r for r in records if start <= r.get("date", "") <= end]
            return {"records": records, "count": len(records)}
        except Exception:
            logger.exception("get_performance_stats failed")
            return {}


class PostgresLogger(CloudLogger):
    """Cloud logger that persists trade data to PostgreSQL via psycopg2."""

    def __init__(self, connection_url: str) -> None:
        self._connection_url = connection_url
        self._conn: Any = None
        self._connect()
        self.create_tables()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        try:
            import psycopg2
            self._conn = psycopg2.connect(self._connection_url)
            self._conn.autocommit = True
        except Exception:
            logger.exception("Failed to connect to PostgreSQL")

    def _execute(self, query: str, params: tuple | None = None) -> None:
        try:
            with self._conn.cursor() as cur:
                cur.execute(query, params)
        except Exception:
            logger.exception("PostgreSQL execute failed")

    def _fetchall(self, query: str, params: tuple | None = None) -> list[dict]:
        try:
            with self._conn.cursor() as cur:
                cur.execute(query, params)
                columns = [desc[0] for desc in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
        except Exception:
            logger.exception("PostgreSQL fetchall failed")
            return []

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def create_tables(self) -> None:
        try:
            self._execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ DEFAULT NOW(),
                    symbol VARCHAR(20),
                    direction VARCHAR(10),
                    entry DOUBLE PRECISION,
                    sl DOUBLE PRECISION,
                    tp DOUBLE PRECISION,
                    lot_size DOUBLE PRECISION,
                    rr_ratio DOUBLE PRECISION,
                    confluence_score DOUBLE PRECISION,
                    reasoning TEXT,
                    result_pips DOUBLE PRECISION,
                    result_dollars DOUBLE PRECISION,
                    duration VARCHAR(50),
                    session VARCHAR(20),
                    smc_setup_type VARCHAR(50)
                )
            """)
            self._execute("""
                CREATE TABLE IF NOT EXISTS daily_summary (
                    id SERIAL PRIMARY KEY,
                    date DATE UNIQUE,
                    total_trades INT,
                    wins INT,
                    losses INT,
                    pnl_pips DOUBLE PRECISION,
                    pnl_dollars DOUBLE PRECISION,
                    win_rate DOUBLE PRECISION
                )
            """)
            self._execute("""
                CREATE TABLE IF NOT EXISTS errors (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ DEFAULT NOW(),
                    error_type VARCHAR(100),
                    message TEXT,
                    traceback TEXT,
                    context TEXT
                )
            """)
            self._execute("""
                CREATE TABLE IF NOT EXISTS performance (
                    id SERIAL PRIMARY KEY,
                    date DATE,
                    metric VARCHAR(50),
                    value DOUBLE PRECISION,
                    details JSONB
                )
            """)
        except Exception:
            logger.exception("create_tables failed")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_trade_open(self, trade_data: dict) -> None:
        try:
            self._execute(
                """
                INSERT INTO trades
                    (timestamp, symbol, direction, entry, sl, tp, lot_size,
                     rr_ratio, confluence_score, reasoning, session, smc_setup_type)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    trade_data.get("timestamp", datetime.utcnow().isoformat()),
                    trade_data.get("symbol"),
                    trade_data.get("direction"),
                    trade_data.get("entry"),
                    trade_data.get("sl"),
                    trade_data.get("tp"),
                    trade_data.get("lot_size"),
                    trade_data.get("rr_ratio"),
                    trade_data.get("confluence_score"),
                    trade_data.get("reasoning"),
                    trade_data.get("session"),
                    trade_data.get("smc_setup_type"),
                ),
            )
        except Exception:
            logger.exception("log_trade_open failed")

    def log_trade_close(self, trade_data: dict, result: dict) -> None:
        try:
            self._execute(
                """
                INSERT INTO trades
                    (timestamp, symbol, direction, entry, sl, tp, lot_size,
                     rr_ratio, confluence_score, reasoning, result_pips,
                     result_dollars, duration, session, smc_setup_type)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    trade_data.get("timestamp", datetime.utcnow().isoformat()),
                    trade_data.get("symbol"),
                    trade_data.get("direction"),
                    trade_data.get("entry"),
                    trade_data.get("sl"),
                    trade_data.get("tp"),
                    trade_data.get("lot_size"),
                    trade_data.get("rr_ratio"),
                    trade_data.get("confluence_score"),
                    trade_data.get("reasoning"),
                    result.get("result_pips"),
                    result.get("result_dollars"),
                    result.get("duration"),
                    trade_data.get("session"),
                    trade_data.get("smc_setup_type"),
                ),
            )
        except Exception:
            logger.exception("log_trade_close failed")

    def log_daily_summary(self, summary: dict) -> None:
        try:
            self._execute(
                """
                INSERT INTO daily_summary (date, total_trades, wins, losses, pnl_pips, pnl_dollars, win_rate)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (date) DO UPDATE SET
                    total_trades = EXCLUDED.total_trades,
                    wins = EXCLUDED.wins,
                    losses = EXCLUDED.losses,
                    pnl_pips = EXCLUDED.pnl_pips,
                    pnl_dollars = EXCLUDED.pnl_dollars,
                    win_rate = EXCLUDED.win_rate
                """,
                (
                    summary.get("date", datetime.utcnow().strftime("%Y-%m-%d")),
                    summary.get("total_trades", 0),
                    summary.get("wins", 0),
                    summary.get("losses", 0),
                    summary.get("pnl_pips", 0),
                    summary.get("pnl_dollars", 0),
                    summary.get("win_rate", 0),
                ),
            )
        except Exception:
            logger.exception("log_daily_summary failed")

    def log_error(self, error_data: dict) -> None:
        try:
            self._execute(
                """
                INSERT INTO errors (timestamp, error_type, message, traceback, context)
                VALUES (%s,%s,%s,%s,%s)
                """,
                (
                    error_data.get("timestamp", datetime.utcnow().isoformat()),
                    error_data.get("error_type"),
                    error_data.get("message"),
                    error_data.get("traceback"),
                    str(error_data.get("context", "")),
                ),
            )
        except Exception:
            logger.exception("log_error failed")

    def get_trade_history(self, filters: dict | None = None) -> list[dict]:
        try:
            if not filters:
                return self._fetchall("SELECT * FROM trades ORDER BY timestamp DESC")
            clauses = [f"{k} = %s" for k in filters]
            where = " AND ".join(clauses)
            return self._fetchall(
                f"SELECT * FROM trades WHERE {where} ORDER BY timestamp DESC",
                tuple(filters.values()),
            )
        except Exception:
            logger.exception("get_trade_history failed")
            return []

    def get_performance_stats(self, date_range: tuple[str, str] | None = None) -> dict:
        try:
            if date_range:
                rows = self._fetchall(
                    "SELECT * FROM daily_summary WHERE date BETWEEN %s AND %s ORDER BY date",
                    date_range,
                )
            else:
                rows = self._fetchall("SELECT * FROM daily_summary ORDER BY date")
            total_trades = sum(r.get("total_trades", 0) for r in rows)
            total_wins = sum(r.get("wins", 0) for r in rows)
            total_pnl = sum(r.get("pnl_dollars", 0) for r in rows)
            return {
                "days": len(rows),
                "total_trades": total_trades,
                "total_wins": total_wins,
                "win_rate": (total_wins / total_trades * 100) if total_trades else 0,
                "total_pnl_dollars": total_pnl,
                "daily_records": rows,
            }
        except Exception:
            logger.exception("get_performance_stats failed")
            return {}


class CompositeLogger(CloudLogger):
    """Logs to multiple CloudLogger backends; one failure does not block others."""

    def __init__(self, loggers: list[CloudLogger]) -> None:
        self._loggers = loggers

    def _broadcast(self, method: str, *args: Any, **kwargs: Any) -> None:
        for backend in self._loggers:
            try:
                getattr(backend, method)(*args, **kwargs)
            except Exception:
                logger.exception("CompositeLogger: %s failed on %s", method, type(backend).__name__)

    def log_trade_open(self, trade_data: dict) -> None:
        self._broadcast("log_trade_open", trade_data)

    def log_trade_close(self, trade_data: dict, result: dict) -> None:
        self._broadcast("log_trade_close", trade_data, result)

    def log_daily_summary(self, summary: dict) -> None:
        self._broadcast("log_daily_summary", summary)

    def log_error(self, error_data: dict) -> None:
        self._broadcast("log_error", error_data)

    def get_trade_history(self, filters: dict | None = None) -> list[dict]:
        for backend in self._loggers:
            try:
                return backend.get_trade_history(filters)
            except Exception:
                logger.exception("CompositeLogger: get_trade_history failed on %s", type(backend).__name__)
        return []

    def get_performance_stats(self, date_range: tuple[str, str] | None = None) -> dict:
        for backend in self._loggers:
            try:
                return backend.get_performance_stats(date_range)
            except Exception:
                logger.exception("CompositeLogger: get_performance_stats failed on %s", type(backend).__name__)
        return {}

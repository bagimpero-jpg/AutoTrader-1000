"""Historical data fetcher with parquet caching."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

from backtest.config import BacktestConfig

logger = logging.getLogger(__name__)


class DataFetcher:
    """Download, resample, cache, and load OHLCV data for backtesting."""

    # Map our timeframe labels to pandas resample rules
    _TF_MAP: dict[str, str] = {
        "M1": "1min",
        "M5": "5min",
        "M15": "15min",
        "H1": "1h",
        "H4": "4h",
        "D1": "1D",
    }

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()
        self.data_dir = Path(self.config.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_or_fetch(self, timeframe: str) -> pd.DataFrame:
        """Load cached parquet if available, otherwise fetch and cache.

        Parameters
        ----------
        timeframe : One of M1, M5, M15, H1, H4, D1.

        Returns
        -------
        DataFrame with UTC DatetimeIndex and columns: open, high, low, close, volume.
        """
        cache_path = self._cache_path(timeframe)

        if cache_path.exists():
            logger.info("Loading cached data: %s", cache_path)
            df = pd.read_parquet(cache_path)
            logger.info("Loaded %d bars for %s %s", len(df), self.config.symbol, timeframe)
            return df

        # Try fetching data — MT5 fetches the exact TF natively
        raw = self._fetch_raw(timeframe)
        if raw.empty:
            logger.warning("No data fetched. Returning empty DataFrame.")
            return raw

        # Check if raw data IS the requested timeframe (from MT5)
        # by comparing bar frequency to expected
        expected_minutes = self._tf_to_minutes(timeframe)
        if len(raw) >= 2:
            actual_minutes = (raw.index[1] - raw.index[0]).total_seconds() / 60
            if abs(actual_minutes - expected_minutes) < 1:
                # MT5 gave us native data — cache and return directly
                raw.to_parquet(cache_path)
                logger.info("Cached MT5 %s: %d bars -> %s", timeframe, len(raw), cache_path)
                return raw

        # Otherwise resample from raw and cache all timeframes
        self._cache_all_timeframes(raw)

        if cache_path.exists():
            return pd.read_parquet(cache_path)

        return self._resample(raw, timeframe)

    def fetch_mt5(self, timeframe: str, count: int = 99999) -> pd.DataFrame:
        """Fetch historical data directly from MT5 terminal.

        Parameters
        ----------
        timeframe : One of M1, M5, M15, H1, H4, D1.
        count     : Number of bars to fetch (MT5 max ~100k per call).

        Returns
        -------
        Standardized OHLCV DataFrame with UTC DatetimeIndex.
        """
        try:
            import MetaTrader5 as mt5
        except ImportError:
            logger.warning("MetaTrader5 package not installed. Skipping MT5 fetch.")
            return pd.DataFrame()

        tf_map = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }

        mt5_tf = tf_map.get(timeframe)
        if mt5_tf is None:
            logger.error("Unknown MT5 timeframe: %s", timeframe)
            return pd.DataFrame()

        # Initialize MT5 if not already connected
        if not mt5.initialize():
            # Try with credentials from .env
            login = os.environ.get("FTMO_LOGIN", "")
            password = os.environ.get("FTMO_PASSWORD", "")
            server = os.environ.get("FTMO_SERVER", "")
            mt5_path = os.environ.get("MT5_PATH", "")

            init_kwargs: dict = {}
            if login:
                init_kwargs["login"] = int(login)
            if password:
                init_kwargs["password"] = password
            if server:
                init_kwargs["server"] = server
            if mt5_path:
                init_kwargs["path"] = mt5_path

            if not mt5.initialize(**init_kwargs):
                logger.error("MT5 initialization failed: %s", mt5.last_error())
                return pd.DataFrame()

        logger.info("Fetching %s %s from MT5 (%d bars)...",
                     self.config.symbol, timeframe, count)

        rates = mt5.copy_rates_from_pos(self.config.symbol, mt5_tf, 0, count)

        if rates is None or len(rates) == 0:
            logger.warning("MT5 returned no data for %s %s: %s",
                           self.config.symbol, timeframe, mt5.last_error())
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time")

        # Rename columns to standard OHLCV
        rename = {}
        if "tick_volume" in df.columns:
            rename["tick_volume"] = "volume"
        if rename:
            df = df.rename(columns=rename)

        # Keep only OHLCV
        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep]

        if "volume" not in df.columns:
            df["volume"] = 0

        logger.info("MT5 returned %d %s bars for %s", len(df), timeframe, self.config.symbol)
        return df

    def fetch_yfinance(self, period: str = "2y", interval: str = "1h") -> pd.DataFrame:
        """Fetch gold futures data from yfinance.

        Parameters
        ----------
        period   : yfinance period string (e.g. "2y", "max").
        interval : yfinance interval (e.g. "1h", "1d").

        Returns
        -------
        Standardized OHLCV DataFrame with UTC DatetimeIndex.
        """
        try:
            import yfinance as yf
        except ImportError:
            logger.error("yfinance not installed. Run: pip install yfinance")
            return pd.DataFrame()

        logger.info("Fetching %s from yfinance (period=%s, interval=%s)",
                     self.config.symbol, period, interval)

        ticker = yf.Ticker("GC=F")  # Gold futures
        df = ticker.history(period=period, interval=interval)

        if df.empty:
            logger.warning("yfinance returned no data for GC=F")
            return df

        return self._standardize(df)

    def fetch_dukascopy(self) -> pd.DataFrame:
        """Fetch M1 gold data from Dukascopy (10+ years if available).

        Requires ``dukascopy-python`` package.
        """
        try:
            from dukascopy import historical  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("dukascopy-python not installed. Skipping Dukascopy fetch.")
            return pd.DataFrame()

        logger.info("Fetching XAUUSD M1 from Dukascopy...")

        try:
            df = historical.fetch(
                instrument="XAUUSD",
                from_date="2015-01-01",
                to_date="2026-01-01",
                timeframe="m1",
            )
        except Exception as e:
            logger.error("Dukascopy fetch failed: %s", e)
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        return self._standardize(df)

    # ------------------------------------------------------------------
    # Resampling
    # ------------------------------------------------------------------

    def resample(self, df: pd.DataFrame, target_tf: str) -> pd.DataFrame:
        """Public resample wrapper."""
        return self._resample(df, target_tf)

    def _resample(self, df: pd.DataFrame, target_tf: str) -> pd.DataFrame:
        """Resample OHLCV data to the target timeframe."""
        rule = self._TF_MAP.get(target_tf)
        if rule is None:
            logger.error("Unknown timeframe: %s", target_tf)
            return df

        resampled = df.resample(rule).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna()

        logger.info("Resampled to %s: %d bars", target_tf, len(resampled))
        return resampled

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_raw(self, timeframe: str | None = None) -> pd.DataFrame:
        """Try MT5 first (native timeframe), then Dukascopy, then yfinance."""
        # MT5: fetch the exact timeframe requested (no resampling needed)
        if timeframe:
            df = self.fetch_mt5(timeframe)
            if not df.empty:
                return df

        # Dukascopy M1
        df = self.fetch_dukascopy()
        if not df.empty:
            return df

        # yfinance: try hourly first, fall back to daily
        df = self.fetch_yfinance(period="2y", interval="1h")
        if not df.empty:
            return df

        logger.info("Hourly data unavailable, trying daily...")
        df = self.fetch_yfinance(period="max", interval="1d")
        return df

    def _cache_all_timeframes(self, raw: pd.DataFrame) -> None:
        """Resample raw data to all standard timeframes and save as parquet."""
        # Determine the raw frequency
        if len(raw) < 2:
            return

        freq_minutes = (raw.index[1] - raw.index[0]).total_seconds() / 60

        for tf, rule in self._TF_MAP.items():
            tf_minutes = self._tf_to_minutes(tf)
            if tf_minutes < freq_minutes:
                continue  # Can't upsample

            resampled = self._resample(raw, tf)
            if not resampled.empty:
                path = self._cache_path(tf)
                resampled.to_parquet(path)
                logger.info("Cached %s: %d bars -> %s", tf, len(resampled), path)

    def _cache_path(self, timeframe: str) -> Path:
        """Return the parquet cache file path for a given timeframe."""
        return self.data_dir / f"{self.config.symbol}_{timeframe}.parquet"

    @staticmethod
    def _standardize(df: pd.DataFrame) -> pd.DataFrame:
        """Standardize column names and ensure UTC DatetimeIndex."""
        df = df.copy()

        # Normalize column names to lowercase
        df.columns = [c.lower().replace(" ", "_") for c in df.columns]

        # Rename common variants
        rename_map: dict[str, str] = {}
        for col in df.columns:
            if "open" in col and col != "open":
                rename_map[col] = "open"
            elif "high" in col and col != "high":
                rename_map[col] = "high"
            elif "low" in col and col != "low":
                rename_map[col] = "low"
            elif "close" in col and col != "close":
                rename_map[col] = "close"
            elif "vol" in col and col != "volume":
                rename_map[col] = "volume"
        if rename_map:
            df = df.rename(columns=rename_map)

        # Ensure required columns
        required = {"open", "high", "low", "close"}
        if not required.issubset(set(df.columns)):
            missing = required - set(df.columns)
            raise ValueError(f"Missing columns after standardization: {missing}")

        if "volume" not in df.columns:
            df["volume"] = 0

        # Keep only OHLCV
        df = df[["open", "high", "low", "close", "volume"]]

        # Ensure UTC DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        df = df.sort_index()
        return df

    @staticmethod
    def _tf_to_minutes(tf: str) -> float:
        """Convert timeframe string to minutes."""
        mapping = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}
        return mapping.get(tf, 0)

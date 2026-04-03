"""Historical data fetcher with parquet caching."""
from __future__ import annotations

import logging
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

        # Try fetching raw data at the finest available granularity
        raw = self._fetch_raw()
        if raw.empty:
            logger.warning("No data fetched. Returning empty DataFrame.")
            return raw

        # Cache every timeframe we can resample to
        self._cache_all_timeframes(raw)

        if cache_path.exists():
            return pd.read_parquet(cache_path)

        # If requested TF doesn't exist (e.g. raw was too coarse), resample
        return self._resample(raw, timeframe)

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

    def _fetch_raw(self) -> pd.DataFrame:
        """Try Dukascopy first (finer granularity), fall back to yfinance."""
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
                logger.info("Cached %s: %d bars → %s", tf, len(resampled), path)

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

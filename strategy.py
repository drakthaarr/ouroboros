"""
strategy.py

Pairs Trading Strategy for BTC/USDT and ETH/USDT on Binance Spot.

This module is intentionally decoupled from any order execution logic.
It is responsible solely for data fetching, signal computation, and logging.
All methods are designed to be independently testable without a live exchange
connection by injecting a mock ccxt-compatible client.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

import ccxt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Module-level logger — the root logger and its handlers are configured
# in main.py, so we simply request a child logger here.
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal Definition
# ---------------------------------------------------------------------------

class Signal(Enum):
    """
    Enumeration of all possible trading signals produced by the strategy.

    Using an Enum (rather than raw strings or ints) makes downstream
    consumption in main.py explicit and eliminates magic-value bugs.
    """
    LONG_BTC_SHORT_ETH = auto()   # Z-score < -entry_threshold
    SHORT_BTC_LONG_ETH = auto()   # Z-score >  entry_threshold
    CLOSE_POSITIONS    = auto()   # Z-score crosses zero
    HOLD               = auto()   # No actionable condition


@dataclass
class StrategyResult:
    """
    Immutable result object returned by get_signal().

    Bundling all computed values into a single object keeps the public
    interface clean and makes unit-testing trivial — just assert on fields.
    """
    signal:         Signal
    z_score:        float
    spread:         float
    spread_mean:    float
    spread_std:     float
    btc_close:      float
    eth_close:      float
    is_data_valid:  bool = True
    error_message:  Optional[str] = None


# ---------------------------------------------------------------------------
# Strategy Class
# ---------------------------------------------------------------------------

class PairsTradingStrategy:
    """
    Statistical Arbitrage (Pairs Trading) strategy for BTC/USDT and ETH/USDT.

    Design principles
    -----------------
    - The class owns zero global state beyond what is passed in at construction.
    - All expensive operations (network I/O, DataFrame math) are isolated into
      small, single-purpose methods that can each be unit-tested independently.
    - No order placement logic lives here. This class is a pure signal engine.

    Parameters
    ----------
    exchange : ccxt.Exchange
        A fully initialised ccxt exchange instance (or a compatible mock).
    symbol_base : str
        The first leg of the pair (default: 'BTC/USDT').
    symbol_quote : str
        The second leg of the pair (default: 'ETH/USDT').
    timeframe : str
        ccxt-compatible candle timeframe string (e.g. '1h', '15m').
    lookback_limit : int
        Number of historical candles to fetch per symbol.
    rolling_window : int
        Period for the rolling mean / std used in Z-score computation.
    entry_threshold : float
        |Z-score| level that triggers a trade signal (classic value: 2.0).
    exit_threshold : float
        |Z-score| level that triggers a close signal (classic value: 0.0).
    """

    # Column names used throughout — defined as class constants so a typo
    # in one place doesn't silently produce a wrong column.
    _OHLCV_COLUMNS: list[str] = [
        "timestamp", "open", "high", "low", "close", "volume"
    ]

    def __init__(
        self,
        exchange: ccxt.Exchange,
        symbol_base:      str   = "BTC/USDT",
        symbol_quote:     str   = "ETH/USDT",
        timeframe:        str   = "1h",
        lookback_limit:   int   = 100,
        rolling_window:   int   = 20,
        entry_threshold:  float = 2.0,
        exit_threshold:   float = 0.0,
    ) -> None:
        self._exchange        = exchange
        self.symbol_base      = symbol_base
        self.symbol_quote     = symbol_quote
        self.timeframe        = timeframe
        self.lookback_limit   = lookback_limit
        self.rolling_window   = rolling_window
        self.entry_threshold  = entry_threshold
        self.exit_threshold   = exit_threshold

        logger.info(
            "PairsTradingStrategy initialised | pair=(%s, %s) | "
            "timeframe=%s | window=%d | entry_z=±%.1f | exit_z=±%.1f",
            self.symbol_base, self.symbol_quote,
            self.timeframe, self.rolling_window,
            self.entry_threshold, self.exit_threshold,
        )

    # ------------------------------------------------------------------
    # Public Interface
    # ------------------------------------------------------------------

    def get_signal(self) -> StrategyResult:
        """
        Top-level method: orchestrates data fetching → spread calc → signal.

        This is the single method main.py calls on every loop iteration.

        Returns
        -------
        StrategyResult
            Contains the Signal enum value plus all intermediate metrics so
            the orchestrator can log or act on them without re-computing.
        """
        logger.info("--- Strategy cycle starting ---")

        # --- 1. Fetch raw data ---
        df_base  = self.fetch_data(self.symbol_base,  self.timeframe, self.lookback_limit)
        df_quote = self.fetch_data(self.symbol_quote, self.timeframe, self.lookback_limit)

        if df_base is None or df_quote is None:
            logger.error("Data fetch failed for one or both symbols. Returning HOLD.")
            return StrategyResult(
                signal=Signal.HOLD,
                z_score=0.0, spread=0.0,
                spread_mean=0.0, spread_std=0.0,
                btc_close=0.0, eth_close=0.0,
                is_data_valid=False,
                error_message="OHLCV fetch returned None for one or both symbols.",
            )

        # --- 2. Calculate spread and Z-score ---
        spread_series = self._calculate_spread(df_base["close"], df_quote["close"])

        if spread_series is None or spread_series.dropna().empty:
            logger.error("Spread calculation produced an empty or None series.")
            return StrategyResult(
                signal=Signal.HOLD,
                z_score=0.0, spread=0.0,
                spread_mean=0.0, spread_std=0.0,
                btc_close=df_base["close"].iloc[-1],
                eth_close=df_quote["close"].iloc[-1],
                is_data_valid=False,
                error_message="Spread series is empty after calculation.",
            )

        z_score_series = self._calculate_z_score(spread_series)

        if z_score_series is None or z_score_series.dropna().empty:
            logger.error("Z-score calculation produced an empty or None series.")
            return StrategyResult(
                signal=Signal.HOLD,
                z_score=0.0, spread=0.0,
                spread_mean=0.0, spread_std=0.0,
                btc_close=df_base["close"].iloc[-1],
                eth_close=df_quote["close"].iloc[-1],
                is_data_valid=False,
                error_message="Z-score series is empty after calculation.",
            )

        # --- 3. Extract the most recent (current) values ---
        current_z_score    = float(z_score_series.iloc[-1])
        current_spread     = float(spread_series.iloc[-1])

        # Rolling stats from the last complete window — these are the values
        # the current Z-score was computed from.
        rolling_mean = spread_series.rolling(window=self.rolling_window).mean()
        rolling_std  = spread_series.rolling(window=self.rolling_window).std()
        current_mean = float(rolling_mean.iloc[-1])
        current_std  = float(rolling_std.iloc[-1])

        btc_close = float(df_base["close"].iloc[-1])
        eth_close = float(df_quote["close"].iloc[-1])

        logger.info(
            "Latest values | BTC=%.2f | ETH=%.2f | spread=%.6f | "
            "mean=%.6f | std=%.6f | z_score=%.4f",
            btc_close, eth_close, current_spread,
            current_mean, current_std, current_z_score,
        )

        # --- 4. Determine signal ---
        signal = self._determine_signal(current_z_score, z_score_series)

        logger.info("Signal generated: %s", signal.name)

        return StrategyResult(
            signal=signal,
            z_score=current_z_score,
            spread=current_spread,
            spread_mean=current_mean,
            spread_std=current_std,
            btc_close=btc_close,
            eth_close=eth_close,
        )

    def fetch_data(
        self,
        symbol:    str,
        timeframe: str,
        limit:     int,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candle data from the exchange for a single symbol.

        Parameters
        ----------
        symbol : str
            Market symbol in ccxt format, e.g. 'BTC/USDT'.
        timeframe : str
            Candle duration string, e.g. '1h', '15m', '1d'.
        limit : int
            Number of most-recent candles to retrieve.

        Returns
        -------
        pd.DataFrame or None
            DataFrame with columns [timestamp, open, high, low, close, volume]
            where 'timestamp' is a timezone-aware UTC DatetimeIndex, or None
            if any network/exchange error occurs.

        Error Handling
        --------------
        ccxt.NetworkError  — transient connectivity issues; logged as WARNING
                             so the bot can retry on the next cycle.
        ccxt.ExchangeError — exchange-side issues (bad symbol, rate limit, etc.);
                             logged as ERROR.
        Exception          — unexpected errors; logged as CRITICAL with a full
                             traceback via exc_info=True.
        """
        logger.info("Fetching %d x %s candles for %s", limit, timeframe, symbol)

        try:
            raw_ohlcv: list[list] = self._exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=limit
            )
        except ccxt.NetworkError as exc:
            logger.warning(
                "Network error while fetching %s: %s — will retry next cycle.",
                symbol, exc,
            )
            return None
        except ccxt.ExchangeError as exc:
            logger.error(
                "Exchange error while fetching %s: %s", symbol, exc
            )
            return None
        except Exception as exc:  # noqa: BLE001
            logger.critical(
                "Unexpected error while fetching %s: %s",
                symbol, exc, exc_info=True,
            )
            return None

        if not raw_ohlcv:
            logger.warning("Exchange returned empty OHLCV list for %s.", symbol)
            return None

        df = pd.DataFrame(raw_ohlcv, columns=self._OHLCV_COLUMNS)

        # Convert millisecond timestamps → proper UTC DatetimeIndex.
        # Using utc=True avoids the AmbiguousTimeError that can bite you on
        # DST boundaries when timestamps are naïvely localised.
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)

        # Cast price/volume columns to float64 explicitly.
        # ccxt can occasionally return strings for certain exchange adapters.
        numeric_cols = ["open", "high", "low", "close", "volume"]
        df[numeric_cols] = df[numeric_cols].astype(np.float64)

        logger.debug(
            "Successfully parsed %d candles for %s. Latest close=%.4f",
            len(df), symbol, df["close"].iloc[-1],
        )
        return df

    # ------------------------------------------------------------------
    # Private Computation Methods (independently unit-testable)
    # ------------------------------------------------------------------

    def _calculate_spread(
        self,
        base_prices:  pd.Series,
        quote_prices: pd.Series,
    ) -> Optional[pd.Series]:
        """
        Compute the price ratio spread between the two assets.

        Spread = base_close / quote_close  (i.e. BTC_price / ETH_price)

        We use the RATIO rather than a raw price difference because:
        - BTC trades at ~$60k and ETH at ~$3k, so a raw difference is
          dominated by BTC's absolute price level and is not stationary.
        - The ratio normalises for price-level differences and is a more
          meaningful measure of relative value between the two assets.

        Parameters
        ----------
        base_prices  : pd.Series — closing prices for the base asset (BTC).
        quote_prices : pd.Series — closing prices for the quote asset (ETH).

        Returns
        -------
        pd.Series or None
        """
        if base_prices.empty or quote_prices.empty:
            logger.error("Cannot calculate spread: one or both price series are empty.")
            return None

        if (quote_prices == 0).any():
            logger.error("Division by zero detected in quote price series. Aborting.")
            return None

        spread = base_prices / quote_prices
        spread.name = "spread"

        logger.debug(
            "Spread calculated | min=%.4f | max=%.4f | latest=%.4f",
            spread.min(), spread.max(), spread.iloc[-1],
        )
        return spread

    def _calculate_z_score(
        self,
        spread: pd.Series,
    ) -> Optional[pd.Series]:
        """
        Compute the rolling Z-score of the spread series.

        Z = (x - μ) / σ

        where μ and σ are computed over the last `rolling_window` periods.
        A Z-score tells us how many standard deviations the current spread
        is away from its recent mean — the core of every mean-reversion model.

        NaN values will appear for the first (rolling_window - 1) rows because
        there are not yet enough data points to form a complete window. This is
        expected and correct — we only ever consume iloc[-1] in get_signal().

        Parameters
        ----------
        spread : pd.Series — the ratio series produced by _calculate_spread().

        Returns
        -------
        pd.Series or None
        """
        if spread.empty:
            logger.error("Cannot calculate Z-score: spread series is empty.")
            return None

        if len(spread) < self.rolling_window:
            logger.error(
                "Not enough data to compute Z-score: have %d candles, need %d.",
                len(spread), self.rolling_window,
            )
            return None

        rolling_mean = spread.rolling(window=self.rolling_window).mean()
        rolling_std  = spread.rolling(window=self.rolling_window).std()

        # Guard against a flat spread (std == 0) which would produce inf.
        # Replace std values of 0 with NaN so the Z-score is NaN (not inf),
        # and log a warning so it's visible in the log file.
        zero_std_mask = rolling_std == 0
        if zero_std_mask.any():
            logger.warning(
                "Rolling std is zero for %d periods — Z-score will be NaN "
                "for those rows. This may indicate a stale data feed.",
                zero_std_mask.sum(),
            )
            rolling_std = rolling_std.replace(0, np.nan)

        z_score = (spread - rolling_mean) / rolling_std
        z_score.name = "z_score"

        logger.debug(
            "Z-score calculated | latest=%.4f | non-NaN count=%d",
            z_score.iloc[-1] if not pd.isna(z_score.iloc[-1]) else float("nan"),
            z_score.notna().sum(),
        )
        return z_score

    def _determine_signal(
        self,
        current_z: float,
        z_score_history: pd.Series,
    ) -> Signal:
        """
        Map a Z-score value to an actionable Signal enum member.

        Signal Logic
        ------------
        Z > +entry_threshold : Spread is abnormally WIDE.
            BTC is expensive relative to ETH → mean reversion predicts the
            spread will compress → SHORT BTC / LONG ETH.

        Z < -entry_threshold : Spread is abnormally NARROW.
            BTC is cheap relative to ETH → mean reversion predicts the
            spread will widen → LONG BTC / SHORT ETH.

        |Z| crosses zero     : The spread has reverted to its mean.
            Close all open positions to bank the profit.
            We detect a zero-cross by checking whether the previous Z-score
            and the current Z-score have opposite signs.

        Everything else      : HOLD — no edge, no trade.

        Parameters
        ----------
        current_z       : float — most recent Z-score value.
        z_score_history : pd.Series — full series (needed for zero-cross check).

        Returns
        -------
        Signal
        """
        if pd.isna(current_z):
            logger.warning("Current Z-score is NaN — insufficient data for signal.")
            return Signal.HOLD

        # --- Entry signals ---
        if current_z > self.entry_threshold:
            logger.info(
                "Z-score %.4f > entry threshold %.1f → SHORT_BTC_LONG_ETH",
                current_z, self.entry_threshold,
            )
            return Signal.SHORT_BTC_LONG_ETH

        if current_z < -self.entry_threshold:
            logger.info(
                "Z-score %.4f < -entry threshold %.1f → LONG_BTC_SHORT_ETH",
                current_z, self.entry_threshold,
            )
            return Signal.LONG_BTC_SHORT_ETH

        # --- Exit / zero-cross signal ---
        # We need at least 2 non-NaN values to detect a sign change.
        valid_z = z_score_history.dropna()
        if len(valid_z) >= 2:
            previous_z = float(valid_z.iloc[-2])
            crossed_zero = (previous_z > self.exit_threshold > current_z) or \
                           (previous_z < self.exit_threshold < current_z)
            if crossed_zero:
                logger.info(
                    "Z-score crossed zero (%.4f → %.4f) → CLOSE_POSITIONS",
                    previous_z, current_z,
                )
                return Signal.CLOSE_POSITIONS

        logger.info("Z-score %.4f within bounds → HOLD", current_z)
        return Signal.HOLD
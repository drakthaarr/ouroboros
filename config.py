"""
config.py

Centralised configuration for the trading bot.

All runtime parameters live here so that main.py, strategy.py, and
exchange.py never contain hard-coded values or raw os.getenv() calls.
If a required secret is absent the module raises immediately at import
time — this is the "fail-fast" pattern that prevents the bot from
starting in a silently broken state.

Optional integrations (Telegram) use os.getenv with a None default so
their absence never blocks the bot from starting.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Locate and load the .env file
# ---------------------------------------------------------------------------
# Resolve the project root relative to this file so the bot works correctly
# regardless of the working directory it is launched from.
_PROJECT_ROOT: Path = Path(__file__).resolve().parent
_ENV_FILE: Path = _PROJECT_ROOT / ".env"

if not _ENV_FILE.exists():
    raise FileNotFoundError(
        f".env file not found at expected location: {_ENV_FILE}\n"
        "Create one with BINANCE_API_KEY and BINANCE_API_SECRET set."
    )

load_dotenv(dotenv_path=_ENV_FILE)

# ---------------------------------------------------------------------------
# Secrets — loaded from environment, never hard-coded
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    """
    Retrieve a required environment variable or raise ValueError.

    Parameters
    ----------
    name : str
        The environment variable key to look up.

    Returns
    -------
    str
        The non-empty string value of the variable.

    Raises
    ------
    ValueError
        If the variable is missing or empty. Raised at import time so the
        bot refuses to start rather than failing mid-trade.
    """
    value: str | None = os.getenv(name)
    if not value:
        raise ValueError(
            f"Required environment variable '{name}' is missing or empty. "
            f"Please add it to your .env file."
        )
    return value


BINANCE_API_KEY:    str = _require_env("BINANCE_API_KEY")
BINANCE_API_SECRET: str = _require_env("BINANCE_API_SECRET")

# ---------------------------------------------------------------------------
# Dry Run (Paper Trading) Mode
# ---------------------------------------------------------------------------
# SAFETY DEFAULT: This intentionally defaults to True if the variable is
# absent from the .env file. You must explicitly set DRY_RUN=False in your
# .env to enable live trading. This prevents accidental real-money execution
# on a fresh clone or misconfigured environment.
#
# .env values that activate live trading:  DRY_RUN=False / false / FALSE
# Everything else (missing, "True", typos): paper trading mode is used.
DRY_RUN: bool = os.getenv("DRY_RUN", "True").lower() == "true"

# ---------------------------------------------------------------------------
# Optional: Telegram notifications
# ---------------------------------------------------------------------------
# Both values default to None if absent. notifier.py checks for None before
# making any network call, so missing credentials silently disable Telegram
# without affecting any other part of the bot.
TELEGRAM_BOT_TOKEN: str | None = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID:   str | None = os.getenv("TELEGRAM_CHAT_ID")

# ---------------------------------------------------------------------------
# Asset pair configuration
# ---------------------------------------------------------------------------

SYMBOL_BASE:  str = "BTC/USDT"   # The asset we buy when signal is LONG_BTC
SYMBOL_QUOTE: str = "ETH/USDT"   # The asset we buy when signal is LONG_ETH

# ---------------------------------------------------------------------------
# Strategy parameters
# ---------------------------------------------------------------------------

TIMEFRAME:        str = "15m"   # Candle duration passed to ccxt fetch_ohlcv
LOOKBACK_LIMIT:   int = 100     # Number of historical candles to fetch
ROLLING_WINDOW:   int = 20      # Z-score rolling window (periods)
ENTRY_THRESHOLD: float = 2.0    # |Z-score| level that opens a position
EXIT_THRESHOLD:  float = 0.0    # |Z-score| level that closes a position

# ---------------------------------------------------------------------------
# Risk & position sizing
# ---------------------------------------------------------------------------

# The USDT amount committed to each individual trade leg.
# Kept deliberately small for the initial $200 live-test bankroll.
# Adjust upward only after validating live behaviour.
TRADE_AMOUNT_USDT: float = 150.0

# ---------------------------------------------------------------------------
# Operational settings
# ---------------------------------------------------------------------------

# How long the main loop sleeps between strategy evaluations (seconds).
# 300 s = 5 minutes, aligned with the 15-minute candle timeframe so we
# evaluate roughly 3 times per candle — enough granularity without
# hammering the exchange rate-limits.
POLL_INTERVAL_SECONDS: int = 5

# Fake coin balance returned by _get_coin_balance() in dry-run mode.
# Must be a positive float so the state machine's pre-sell balance check
# (balance > 0) passes and the simulated exit logic completes correctly.
DRY_RUN_FAKE_BALANCE: float = 99.0

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR:          str = str(_PROJECT_ROOT / "logs")
LOG_FILE:         str = "bot.log"
LOG_MAX_BYTES:    int = 5 * 1024 * 1024   # 5 MB per file
LOG_BACKUP_COUNT: int = 3                 # Keep 3 rotated files
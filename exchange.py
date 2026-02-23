"""
exchange.py

Provides a thin, safe wrapper around the ccxt Binance exchange object.

The sole responsibility of this module is to construct a correctly
configured ccxt.binance instance and expose it via the `exchange`
attribute. All higher-level operations (balance checks, order placement)
are intentionally left to the callers in main.py and strategy.py, keeping
this module focused and independently testable.
"""

import logging

import ccxt

# ---------------------------------------------------------------------------
# Module-level logger — inherits handlers configured in main.py at runtime.
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


class BinanceClient:
    """
    Lightweight wrapper that constructs and validates a ccxt Binance instance.

    Attributes
    ----------
    exchange : ccxt.binance
        The fully configured ccxt exchange object. All callers should
        interact with the exchange exclusively through this attribute.

    Parameters
    ----------
    api_key : str
        Binance API key loaded from the .env file via config.py.
    api_secret : str
        Binance API secret loaded from the .env file via config.py.

    Raises
    ------
    ccxt.AuthenticationError
        If ccxt rejects the provided credentials during initialisation.
    ccxt.ExchangeError
        If ccxt fails to build the exchange object for any other
        exchange-side reason.
    Exception
        Re-raised for any unexpected error so the caller (main.py) can
        decide whether to abort or retry.
    """

    def __init__(self, api_key: str, api_secret: str) -> None:
        logger.info("Initialising ccxt Binance client...")

        try:
            self.exchange: ccxt.binance = ccxt.binance({
                # --- Credentials ---
                "apiKey": api_key,
                "secret": api_secret,

                # --- Safety: respect Binance rate limits automatically ---
                # ccxt will insert short sleeps between requests when this is
                # True, preventing HTTP 429 bans without any extra code on our
                # side. Always enable this for a live bot.
                "enableRateLimit": True,

                # --- Routing: force all operations to the Spot market ---
                # Without this, certain ccxt methods can silently fall back to
                # the USD-M Futures endpoint, which would be catastrophic for a
                # spot-only strategy. This option pins every request to Spot.
                "options": {
                    "defaultType": "spot",
                },
            })

        except ccxt.AuthenticationError as exc:
            logger.error(
                "Authentication failed — check that BINANCE_API_KEY and "
                "BINANCE_API_SECRET in your .env file are correct: %s", exc,
            )
            raise

        except ccxt.ExchangeError as exc:
            logger.error(
                "ccxt raised an ExchangeError while building the Binance "
                "client. The exchange may be temporarily unavailable: %s", exc,
            )
            raise

        except Exception as exc:
            logger.critical(
                "Unexpected error during BinanceClient initialisation: %s",
                exc, exc_info=True,
            )
            raise

        logger.info(
            "BinanceClient initialised successfully. "
            "market=spot | rate_limit_enabled=True",
        )
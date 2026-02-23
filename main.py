"""
main.py

Bot Orchestrator — the single entry point for the trading bot.

Responsibilities
----------------
1. Configure production-grade logging (console + rotating file).
2. Initialise the exchange client and strategy engine.
3. Track the current position state (PositionState enum).
4. Run the main event loop:
   a. Call strategy.get_signal() every POLL_INTERVAL_SECONDS.
   b. Drive the position state machine.
   c. Execute market orders on state transitions.
   d. Send Telegram push notifications on key events.
   e. Handle all ccxt exceptions so the loop never crashes.

Spot-market pseudo-pairs logic
-------------------------------
Because Binance Spot does not support shorting, we simulate pairs
exposure by selectively holding one leg of the pair:
  - LONG_BTC signal -> buy BTC  (profit if BTC outperforms ETH)
  - LONG_ETH signal -> buy ETH  (profit if ETH outperforms BTC)
  - CLOSE signal    -> sell whatever we hold -> back to cash (USDT)
"""

import logging
import logging.handlers
import sys
import time
from enum import Enum, auto
from pathlib import Path

import ccxt

import config
from exchange import BinanceClient
from notifier import send_telegram_message
from strategy import PairsTradingStrategy, Signal, StrategyResult


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _configure_logging() -> logging.Logger:
    """
    Build and return the root logger with two handlers:

    - StreamHandler        : INFO+ to stdout (human-readable in console).
    - RotatingFileHandler  : DEBUG+ to logs/bot.log (full audit trail).

    All child loggers (strategy, exchange, notifier, etc.) propagate to
    this root logger automatically, so this single call instruments the
    whole application.

    Returns
    -------
    logging.Logger
        The configured root logger.
    """
    log_dir = Path(config.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / config.LOG_FILE

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)   # Capture everything; handlers filter

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # --- Console handler (INFO and above) ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # --- Rotating file handler (DEBUG and above) ---
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_path,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    return root_logger


# ---------------------------------------------------------------------------
# Position State Machine definition
# ---------------------------------------------------------------------------

class PositionState(Enum):
    """
    Represents which asset the bot currently holds.

    NONE        : All capital is in USDT. No open position.
    HOLDING_BTC : We purchased BTC and are waiting for a close signal.
    HOLDING_ETH : We purchased ETH and are waiting for a close signal.

    The state machine is the single source of truth for position tracking.
    All order execution is gated behind state checks, which prevents
    double-buying: entry signals are only acted on when state is NONE.
    """
    NONE        = auto()
    HOLDING_BTC = auto()
    HOLDING_ETH = auto()


# ---------------------------------------------------------------------------
# Startup mode banner
# ---------------------------------------------------------------------------

def _log_startup_banner(logger: logging.Logger) -> None:
    """
    Emit a highly visible startup banner so the operator instantly knows
    whether the bot is in paper-trading or live-trading mode.

    The banner uses repeated characters so it cannot be missed when
    tailing logs or reading the console.

    Parameters
    ----------
    logger : logging.Logger
    """
    if config.DRY_RUN:
        logger.info("*" * 60)
        logger.info("*%s*", " PAPER TRADING MODE ACTIVE ".center(58))
        logger.info("*%s*", " DRY_RUN = True ".center(58))
        logger.info("*%s*", " Real market data - FAKE order execution ".center(58))
        logger.info("*%s*", " No real funds will be used. ".center(58))
        logger.info("*" * 60)
    else:
        logger.warning("!" * 60)
        logger.warning("!%s!", " WARNING: LIVE TRADING MODE ".center(58))
        logger.warning("!%s!", " DRY_RUN = False ".center(58))
        logger.warning("!%s!", " REAL MONEY IS AT RISK. ".center(58))
        logger.warning("!%s!", " Verify config before proceeding. ".center(58))
        logger.warning("!" * 60)


# ---------------------------------------------------------------------------
# Order execution helpers
# ---------------------------------------------------------------------------

def _execute_buy(
    exchange: ccxt.Exchange,
    symbol: str,
    usdt_amount: float,
    logger: logging.Logger,
) -> bool:
    """
    Place a market BUY order and return True on success, False on failure.

    In dry-run mode the order is fully simulated: no network call is made,
    the function logs a prominent notice and returns True immediately so
    the state machine advances exactly as it would in live trading.

    In live mode, the 'quoteOrderQty' parameter tells Binance to spend
    exactly `usdt_amount` of USDT regardless of the current price — safer
    than calculating a coin quantity ourselves with a potentially stale price.

    Parameters
    ----------
    exchange    : ccxt.Exchange — live exchange instance (unused in dry run).
    symbol      : str           — e.g. 'BTC/USDT'.
    usdt_amount : float         — USDT to spend.
    logger      : logging.Logger

    Returns
    -------
    bool
        True if the order was accepted (or simulated), False on live failure.
    """
    if config.DRY_RUN:
        logger.info(
            "[DRY RUN] Simulated market order: MARKET BUY | symbol=%s | "
            "spend=%.2f USDT | no real order was placed.",
            symbol, usdt_amount,
        )
        return True

    # --- Live execution path ---
    logger.info(
        "Attempting MARKET BUY | symbol=%s | spend=%.2f USDT",
        symbol, usdt_amount,
    )
    try:
        order = exchange.create_market_buy_order(
            symbol=symbol,
            amount=usdt_amount,
            params={"quoteOrderQty": usdt_amount},
        )
        logger.info(
            "BUY order filled | id=%s | symbol=%s | status=%s",
            order.get("id"), symbol, order.get("status"),
        )
        return True

    except ccxt.InsufficientFunds as exc:
        logger.error("Insufficient funds for BUY %s: %s", symbol, exc)
    except ccxt.InvalidOrder as exc:
        logger.error("Invalid order parameters for BUY %s: %s", symbol, exc)
    except ccxt.NetworkError as exc:
        logger.warning(
            "Network error during BUY %s: %s - will retry next cycle.",
            symbol, exc,
        )
    except ccxt.ExchangeError as exc:
        logger.error("Exchange error during BUY %s: %s", symbol, exc)
    except Exception as exc:  # noqa: BLE001
        logger.critical(
            "Unexpected error during BUY %s: %s", symbol, exc, exc_info=True
        )

    return False


def _execute_sell(
    exchange: ccxt.Exchange,
    symbol: str,
    coin_amount: float,
    logger: logging.Logger,
) -> bool:
    """
    Place a market SELL order for the full `coin_amount` and return True
    on success, False on failure.

    In dry-run mode the sell is fully simulated: the function logs a
    prominent notice and returns True so the state machine transitions
    back to NONE exactly as it would after a real fill.

    We always sell the entire position (no partial closes) to keep the
    state machine binary: either we hold an asset or we do not.

    Parameters
    ----------
    exchange    : ccxt.Exchange — live exchange instance (unused in dry run).
    symbol      : str           — e.g. 'BTC/USDT'.
    coin_amount : float         — exact coin quantity to sell.
    logger      : logging.Logger

    Returns
    -------
    bool
        True if the order was accepted (or simulated), False on live failure.
    """
    if config.DRY_RUN:
        logger.info(
            "[DRY RUN] Simulated market order: MARKET SELL | symbol=%s | "
            "qty=%.8f | no real order was placed.",
            symbol, coin_amount,
        )
        return True

    # --- Live execution path ---
    logger.info(
        "Attempting MARKET SELL | symbol=%s | qty=%.8f",
        symbol, coin_amount,
    )
    try:
        order = exchange.create_market_sell_order(
            symbol=symbol,
            amount=coin_amount,
        )
        logger.info(
            "SELL order filled | id=%s | symbol=%s | status=%s",
            order.get("id"), symbol, order.get("status"),
        )
        return True

    except ccxt.InsufficientFunds as exc:
        logger.error("Insufficient funds for SELL %s: %s", symbol, exc)
    except ccxt.InvalidOrder as exc:
        logger.error("Invalid order parameters for SELL %s: %s", symbol, exc)
    except ccxt.NetworkError as exc:
        logger.warning(
            "Network error during SELL %s: %s - will retry next cycle.",
            symbol, exc,
        )
    except ccxt.ExchangeError as exc:
        logger.error("Exchange error during SELL %s: %s", symbol, exc)
    except Exception as exc:  # noqa: BLE001
        logger.critical(
            "Unexpected error during SELL %s: %s", symbol, exc, exc_info=True
        )

    return False


def _get_coin_balance(
    exchange: ccxt.Exchange,
    currency: str,
    logger: logging.Logger,
) -> float:
    """
    Fetch the free (available) balance for a single currency.

    In dry-run mode a pre-configured fake balance is returned so the
    state machine's exit guard (balance > 0 required before selling)
    always passes without querying the real account. The fake value is
    defined in config.DRY_RUN_FAKE_BALANCE.

    In live mode, balance is fetched fresh from the exchange on every
    call rather than tracked locally — this prevents stale state if a
    previous order was only partially filled or if a network error
    interrupted the last cycle mid-execution.

    Parameters
    ----------
    exchange : ccxt.Exchange — live exchange instance (unused in dry run).
    currency : str           — e.g. 'BTC', 'ETH', 'USDT'.
    logger   : logging.Logger

    Returns
    -------
    float
        Free balance, a fake positive float in dry-run mode, or 0.0 on error.
    """
    if config.DRY_RUN:
        logger.info(
            "[DRY RUN] Providing fake balance | currency=%s | "
            "fake_balance=%.2f",
            currency, config.DRY_RUN_FAKE_BALANCE,
        )
        return config.DRY_RUN_FAKE_BALANCE

    # --- Live execution path ---
    try:
        balance = exchange.fetch_balance()
        free_amount = float(balance.get("free", {}).get(currency, 0.0))
        logger.debug("Balance query | %s free=%.8f", currency, free_amount)
        return free_amount

    except ccxt.NetworkError as exc:
        logger.warning(
            "Network error fetching balance for %s: %s", currency, exc
        )
    except ccxt.ExchangeError as exc:
        logger.error(
            "Exchange error fetching balance for %s: %s", currency, exc
        )
    except Exception as exc:  # noqa: BLE001
        logger.critical(
            "Unexpected error fetching balance: %s", exc, exc_info=True
        )

    return 0.0


# ---------------------------------------------------------------------------
# State machine transition logic
# ---------------------------------------------------------------------------

def _process_signal(
    signal: Signal,
    result: StrategyResult,
    state: PositionState,
    exchange: ccxt.Exchange,
    logger: logging.Logger,
) -> PositionState:
    """
    Core state machine: map (current_state, signal) -> action -> next_state.

    This function is the single gatekeeper for all order execution. Making
    every state transition explicit here guarantees that:
      - Only one position can be open at a time.
      - An entry signal is only acted on when state is NONE.
      - An exit signal is only acted on when we actually hold something.

    Telegram notifications are sent here rather than inside the execution
    helpers so that the helpers remain pure (no notification side-effects)
    and can be unit-tested in isolation.

    Parameters
    ----------
    signal   : Signal         — output from PairsTradingStrategy.get_signal().
    result   : StrategyResult — full result object (for logging context).
    state    : PositionState  — the bot's current position state.
    exchange : ccxt.Exchange  — live exchange instance for order placement.
    logger   : logging.Logger

    Returns
    -------
    PositionState
        The new state after processing the signal. If an order fails, the
        state is left unchanged so the bot retries on the next cycle.
    """
    logger.info(
        "State machine | state=%s | signal=%s | z_score=%.4f",
        state.name, signal.name, result.z_score,
    )

    # ------------------------------------------------------------------
    # Branch 1: No open position — look for entry signals
    # ------------------------------------------------------------------
    if state is PositionState.NONE:

        if signal is Signal.LONG_BTC_SHORT_ETH:
            logger.info(
                "Entry condition met: LONG_BTC | spending %.2f USDT on %s",
                config.TRADE_AMOUNT_USDT, config.SYMBOL_BASE,
            )
            success = _execute_buy(
                exchange, config.SYMBOL_BASE, config.TRADE_AMOUNT_USDT, logger
            )
            if success:
                logger.info("State transition: NONE -> HOLDING_BTC")
                send_telegram_message(
                    text=(
                        f"{'[DRY RUN] ' if config.DRY_RUN else ''}"
                        f"BUY executed\n"
                        f"Coin   : {config.SYMBOL_BASE}\n"
                        f"Amount : {config.TRADE_AMOUNT_USDT:.2f} USDT\n"
                        f"Signal : LONG_BTC_SHORT_ETH\n"
                        f"Z-score: {result.z_score:.4f}"
                    ),
                    logger=logger,
                )
                return PositionState.HOLDING_BTC
            else:
                logger.error("BUY BTC failed — remaining in NONE state.")
                return PositionState.NONE

        if signal is Signal.SHORT_BTC_LONG_ETH:
            logger.info(
                "Entry condition met: LONG_ETH | spending %.2f USDT on %s",
                config.TRADE_AMOUNT_USDT, config.SYMBOL_QUOTE,
            )
            success = _execute_buy(
                exchange, config.SYMBOL_QUOTE, config.TRADE_AMOUNT_USDT, logger
            )
            if success:
                logger.info("State transition: NONE -> HOLDING_ETH")
                send_telegram_message(
                    text=(
                        f"{'[DRY RUN] ' if config.DRY_RUN else ''}"
                        f"BUY executed\n"
                        f"Coin   : {config.SYMBOL_QUOTE}\n"
                        f"Amount : {config.TRADE_AMOUNT_USDT:.2f} USDT\n"
                        f"Signal : SHORT_BTC_LONG_ETH\n"
                        f"Z-score: {result.z_score:.4f}"
                    ),
                    logger=logger,
                )
                return PositionState.HOLDING_ETH
            else:
                logger.error("BUY ETH failed — remaining in NONE state.")
                return PositionState.NONE

        # CLOSE_POSITIONS or HOLD while already flat — nothing to do.
        logger.info("State=NONE and signal=%s — no action taken.", signal.name)
        return PositionState.NONE

    # ------------------------------------------------------------------
    # Branch 2: Holding BTC — look for exit signals
    # ------------------------------------------------------------------
    if state is PositionState.HOLDING_BTC:

        # Exit on mean reversion (CLOSE) or on a trend reversal against us.
        should_exit = signal in (
            Signal.CLOSE_POSITIONS,
            Signal.SHORT_BTC_LONG_ETH,
        )

        if should_exit:
            btc_balance = _get_coin_balance(exchange, "BTC", logger)
            if btc_balance <= 0:
                logger.error(
                    "Exit triggered but BTC balance is %.8f — cannot sell. "
                    "Forcing state reset to NONE to avoid being stuck.",
                    btc_balance,
                )
                return PositionState.NONE

            logger.info(
                "Exit condition met: SELL BTC | qty=%.8f | trigger=%s",
                btc_balance, signal.name,
            )
            success = _execute_sell(
                exchange, config.SYMBOL_BASE, btc_balance, logger
            )
            if success:
                logger.info("State transition: HOLDING_BTC -> NONE")
                send_telegram_message(
                    text=(
                        f"{'[DRY RUN] ' if config.DRY_RUN else ''}"
                        f"SELL executed\n"
                        f"Coin    : {config.SYMBOL_BASE}\n"
                        f"Qty     : {btc_balance:.8f}\n"
                        f"Trigger : {signal.name}\n"
                        f"Z-score : {result.z_score:.4f}"
                    ),
                    logger=logger,
                )
                return PositionState.NONE
            else:
                logger.error("SELL BTC failed — remaining in HOLDING_BTC state.")
                return PositionState.HOLDING_BTC

        logger.info(
            "State=HOLDING_BTC and signal=%s — holding position.", signal.name
        )
        return PositionState.HOLDING_BTC

    # ------------------------------------------------------------------
    # Branch 3: Holding ETH — look for exit signals
    # ------------------------------------------------------------------
    if state is PositionState.HOLDING_ETH:

        should_exit = signal in (
            Signal.CLOSE_POSITIONS,
            Signal.LONG_BTC_SHORT_ETH,   # Trend reversal against our ETH hold
        )

        if should_exit:
            eth_balance = _get_coin_balance(exchange, "ETH", logger)
            if eth_balance <= 0:
                logger.error(
                    "Exit triggered but ETH balance is %.8f — cannot sell. "
                    "Forcing state reset to NONE.",
                    eth_balance,
                )
                return PositionState.NONE

            logger.info(
                "Exit condition met: SELL ETH | qty=%.8f | trigger=%s",
                eth_balance, signal.name,
            )
            success = _execute_sell(
                exchange, config.SYMBOL_QUOTE, eth_balance, logger
            )
            if success:
                logger.info("State transition: HOLDING_ETH -> NONE")
                send_telegram_message(
                    text=(
                        f"{'[DRY RUN] ' if config.DRY_RUN else ''}"
                        f"SELL executed\n"
                        f"Coin    : {config.SYMBOL_QUOTE}\n"
                        f"Qty     : {eth_balance:.8f}\n"
                        f"Trigger : {signal.name}\n"
                        f"Z-score : {result.z_score:.4f}"
                    ),
                    logger=logger,
                )
                return PositionState.NONE
            else:
                logger.error("SELL ETH failed — remaining in HOLDING_ETH state.")
                return PositionState.HOLDING_ETH

        logger.info(
            "State=HOLDING_ETH and signal=%s — holding position.", signal.name
        )
        return PositionState.HOLDING_ETH

    # Defensive fallback — should never be reached with a complete Enum.
    logger.critical(
        "Unknown PositionState '%s' encountered — resetting to NONE.", state
    )
    return PositionState.NONE


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Bot entry point. Initialises all components and runs the trading loop.

    The main loop is structured so that:
    - A crashed strategy cycle is logged and skipped (loop continues).
    - A KeyboardInterrupt (Ctrl-C) triggers a clean shutdown message.
    - An unrecoverable startup error (bad API keys, missing config)
      raises immediately before the loop starts.
    """
    logger = _configure_logging()

    # Emit the mode banner as the very first log output so the operator
    # cannot miss it before any trading logic runs.
    _log_startup_banner(logger)

    logger.info("=" * 60)
    logger.info("Trading bot starting up.")
    logger.info("Pair: %s / %s", config.SYMBOL_BASE, config.SYMBOL_QUOTE)
    logger.info(
        "Timeframe: %s | Trade amount: %.2f USDT",
        config.TIMEFRAME, config.TRADE_AMOUNT_USDT,
    )
    logger.info("=" * 60)

    # --- Initialise exchange client ---
    try:
        client = BinanceClient(
            api_key=config.BINANCE_API_KEY,
            api_secret=config.BINANCE_API_SECRET,
        )
        logger.info("BinanceClient initialised successfully.")
    except Exception as exc:
        logger.critical(
            "Failed to initialise BinanceClient: %s", exc, exc_info=True
        )
        sys.exit(1)

    # --- Initialise strategy ---
    strategy = PairsTradingStrategy(
        exchange=client.exchange,
        symbol_base=config.SYMBOL_BASE,
        symbol_quote=config.SYMBOL_QUOTE,
        timeframe=config.TIMEFRAME,
        lookback_limit=config.LOOKBACK_LIMIT,
        rolling_window=config.ROLLING_WINDOW,
        entry_threshold=config.ENTRY_THRESHOLD,
        exit_threshold=config.EXIT_THRESHOLD,
    )
    logger.info("PairsTradingStrategy initialised successfully.")

    # --- Notify operator that the bot is live ---
    send_telegram_message(
        text=(
            f"Robot started\n"
            f"Pair    : {config.SYMBOL_BASE} / {config.SYMBOL_QUOTE}\n"
            f"Mode    : {'DRY RUN (paper trading)' if config.DRY_RUN else 'LIVE TRADING'}\n"
            f"TF      : {config.TIMEFRAME}\n"
            f"Amount  : {config.TRADE_AMOUNT_USDT:.2f} USDT per trade"
        ),
        logger=logger,
    )

    # --- Position state (in-memory; starts flat on every launch) ---
    # NOTE: If the bot is restarted while holding a position it will start
    # in NONE state and may miss the corresponding exit signal. A future
    # improvement is to persist state to a JSON file and reload on startup.
    position_state: PositionState = PositionState.NONE
    logger.info("Initial position state: %s", position_state.name)

    # --- Main trading loop ---
    logger.info(
        "Entering main loop. Polling every %d seconds.",
        config.POLL_INTERVAL_SECONDS,
    )

    cycle: int = 0

    while True:
        try:
            # Sleep first so we avoid acting on a candle that is not yet
            # fully closed and to respect exchange rate limits on startup.
            logger.info(
                "Sleeping %d seconds before cycle %d...",
                config.POLL_INTERVAL_SECONDS, cycle + 1,
            )
            time.sleep(config.POLL_INTERVAL_SECONDS)
            cycle += 1
            logger.info("--- Cycle %d starting ---", cycle)

            
            result: StrategyResult = strategy.get_signal()
            # ====================================================
            # ВРЕМЕННЫЙ ХАК ДЛЯ ТЕСТА СТЕЙТ-МАШИНЫ И ТЕЛЕГРАМА
            # ====================================================
            logger.info("[TEST] Подменяем реальный сигнал для проверки Telegram...")
            if position_state == PositionState.NONE:
                # Если ничего нет - заставляем его купить Биткоин
                result.signal = Signal.LONG_BTC_SHORT_ETH
            elif position_state == PositionState.HOLDING_BTC:
                # На следующем цикле заставляем его продать Биткоин
                result.signal = Signal.CLOSE_POSITIONS
            # ====================================================
            if not result.is_data_valid:
                logger.warning(
                    "Cycle %d: Strategy returned invalid data (%s) "
                    "— skipping execution.",
                    cycle, result.error_message,
                )
                continue  

            
            position_state = _process_signal(
                signal=result.signal,
                result=result,
                state=position_state,
                exchange=client.exchange,
                logger=logger,
            )

            logger.info(
                "Cycle %d complete | new_state=%s | z_score=%.4f",
                cycle, position_state.name, result.z_score,
            )

        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received — shutting down gracefully.")
            logger.info(
                "Final state at shutdown: %s | cycles completed: %d",
                position_state.name, cycle,
            )
            send_telegram_message(
                text=(
                    f"Bot stopped by operator (KeyboardInterrupt)\n"
                    f"Final state : {position_state.name}\n"
                    f"Cycles run  : {cycle}"
                ),
                logger=logger,
            )
            sys.exit(0)

        except Exception as exc:  # noqa: BLE001
            # Catch-all for any unhandled bug or edge case. We log with a
            # full traceback, send a Telegram alert, and continue the loop
            # rather than crashing — a sleeping bot is safer than a dead one.
            logger.critical(
                "Unhandled exception in main loop (cycle %d): %s",
                cycle, exc, exc_info=True,
            )
            send_telegram_message(
                text=(
                    f"CRITICAL ERROR in main loop\n"
                    f"Cycle : {cycle}\n"
                    f"State : {position_state.name}\n"
                    f"Error : {type(exc).__name__}: {exc}\n"
                    f"Bot is still running and will retry next cycle."
                ),
                logger=logger,
            )
            logger.info("Recovering — sleeping before next cycle.")
            time.sleep(config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
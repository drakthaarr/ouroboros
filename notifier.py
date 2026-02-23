"""
notifier.py

Provides a single, fire-and-forget function for sending Telegram push
notifications to a configured chat.

Design decisions
----------------
- This module is intentionally simple: one public function, no classes,
  no async, no retry loops. The bot's trading logic must never be blocked
  or crashed by a notification failure.
- If either Telegram credential is absent from config, the function
  returns immediately without raising. Telegram is optional infrastructure.
- All network errors are caught and logged as warnings, never re-raised.
"""

import logging

import requests

import config

# The Telegram Bot API endpoint template.
# Token is interpolated at call time (not at import time) so that a missing
# token at module load does not cause an AttributeError here.
_TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

# Timeout (seconds) for the outbound HTTP POST.
# Kept short: a slow Telegram server should never stall the trading loop.
_REQUEST_TIMEOUT_SECONDS: int = 10


def send_telegram_message(text: str, logger: logging.Logger) -> None:
    """
    Send a plain-text message to the configured Telegram chat.

    The function is intentionally fire-and-forget:
    - Returns immediately (None) whether the send succeeded or failed.
    - Never raises an exception to the caller.
    - Silently skips if credentials are not configured.

    Parameters
    ----------
    text : str
        The message body to send. Telegram supports up to 4096 characters;
        longer strings are silently truncated to avoid API rejections.
    logger : logging.Logger
        The logger instance from the calling module, used to record
        skips, successes, and failures without coupling to a module-level
        logger here.
    """
    # --- Guard: skip gracefully if credentials are not configured ---
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.debug(
            "Telegram credentials not configured — skipping notification."
        )
        return

    # Truncate to Telegram's hard character limit to prevent API errors.
    truncated_text = text[:4096]

    url = _TELEGRAM_API_URL.format(token=config.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": truncated_text,
        # "parse_mode" is intentionally omitted so that no special
        # markdown syntax in log messages can break the API call.
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        # Raise an HTTPError for 4xx / 5xx responses so we can log them.
        response.raise_for_status()

        logger.debug(
            "Telegram notification sent successfully | status=%d",
            response.status_code,
        )

    except requests.exceptions.HTTPError as exc:
        # The server responded but with an error code (e.g. 401 bad token,
        # 400 bad chat_id). Log the response body for easy debugging.
        logger.warning(
            "Telegram API returned an HTTP error: %s | response=%s",
            exc,
            exc.response.text if exc.response is not None else "N/A",
        )
    except requests.exceptions.ConnectionError as exc:
        logger.warning(
            "Could not connect to Telegram API (network issue): %s", exc
        )
    except requests.exceptions.Timeout:
        logger.warning(
            "Telegram API request timed out after %ds — notification dropped.",
            _REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:
        # Catch-all for any other requests-layer failure.
        logger.warning("Unexpected error sending Telegram notification: %s", exc)
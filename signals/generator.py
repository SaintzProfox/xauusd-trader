"""
signals/generator.py
Main orchestration loop: fetch data → generate signal → save → notify.
Runs on a schedule (configured in config.py).
"""

import logging
import logging.handlers
import signal as os_signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import schedule

# Make sure parent package is importable when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import SIGNAL_INTERVAL_MINUTES, LOG_FILE, LOG_LEVEL
from db.database import (
    init_db, save_signal, mark_signal_notified,
    get_recent_signals, get_stats,
)
from signals.data_fetcher import fetch_ohlcv
from signals.strategy import generate_signal
from signals.notifier import (
    TelegramCommandHandler, notify_signal,
    notify_startup, notify_error,
)

# ── Logging setup ─────────────────────────────────────────────────────────────

Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("generator")

# ── Globals ───────────────────────────────────────────────────────────────────
_running        = True
_last_action    = "hold"   # Avoid spamming duplicate signals


# ── Core job ──────────────────────────────────────────────────────────────────

def run_signal_job():
    global _last_action
    logger.info("── Signal job started ──")
    try:
        df     = fetch_ohlcv()
        signal = generate_signal(df)

        action = signal["action"]

        # Skip holds; skip duplicate consecutive actions
        if action == "hold":
            logger.info("Signal: HOLD – nothing to do")
            return

        if action == _last_action:
            logger.info("Signal: %s (same as last – skipped to avoid spam)", action.upper())
            return

        # Persist
        signal_id = save_signal(signal)
        logger.info("Signal saved (ID=%d): %s @ %.2f", signal_id, action.upper(), signal["price"])

        # Notify
        ok = notify_signal(signal, signal_id)
        if ok:
            mark_signal_notified(signal_id)

        _last_action = action

    except Exception as exc:
        logger.error("Signal job error: %s", exc, exc_info=True)
        notify_error(str(exc))


# ── Telegram command polling thread ───────────────────────────────────────────

def _start_telegram_polling():
    handler = TelegramCommandHandler(
        db_stats_fn=get_stats,
        recent_signals_fn=get_recent_signals,
    )

    def poll_loop():
        logger.info("Telegram polling thread started")
        while _running:
            handler.poll()
            time.sleep(1)

    t = threading.Thread(target=poll_loop, name="telegram-poll", daemon=True)
    t.start()
    return t


# ── Graceful shutdown ─────────────────────────────────────────────────────────

def _shutdown(signum, frame):
    global _running
    logger.info("Shutdown signal received – stopping…")
    _running = False
    sys.exit(0)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("XAUUSD Trading Signal Generator starting…")
    logger.info("=" * 60)

    # Register OS signal handlers
    os_signal.signal(os_signal.SIGTERM, _shutdown)
    os_signal.signal(os_signal.SIGINT,  _shutdown)

    # Init DB
    init_db()
    logger.info("Database initialised")

    # Startup notification
    notify_startup()

    # Run once immediately
    run_signal_job()

    # Schedule recurring job
    schedule.every(SIGNAL_INTERVAL_MINUTES).minutes.do(run_signal_job)

    # Optional: daily summary at 23:55 UTC
    schedule.every().day.at("23:55").do(lambda: notify_startup())

    # Start Telegram polling
    _start_telegram_polling()

    logger.info("Scheduler active – checking every %d minutes", SIGNAL_INTERVAL_MINUTES)

    while _running:
        schedule.run_pending()
        time.sleep(5)


if __name__ == "__main__":
    main()

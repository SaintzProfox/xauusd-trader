"""
signals/notifier.py
Sends trade signals and alerts via Telegram bot.
"""

import json
import logging
import requests
from datetime import datetime

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


# ── Core send helpers ─────────────────────────────────────────────────────────

def _send_message(text: str, parse_mode: str = "HTML", silent: bool = False) -> bool:
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.warning("Telegram not configured – printing to console:\n%s", text)
        print("\n" + "=" * 60)
        print(text)
        print("=" * 60 + "\n")
        return False
    try:
        resp = requests.post(
            f"{BASE_URL}/sendMessage",
            json={
                "chat_id":              TELEGRAM_CHAT_ID,
                "text":                 text,
                "parse_mode":           parse_mode,
                "disable_notification": silent,
            },
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            logger.error("Telegram send failed: %s", data)
            return False
        return True
    except Exception as exc:
        logger.error("Telegram request error: %s", exc)
        return False


def _send_photo(photo_url: str, caption: str = "") -> bool:
    try:
        resp = requests.post(
            f"{BASE_URL}/sendPhoto",
            json={"chat_id": TELEGRAM_CHAT_ID, "photo": photo_url, "caption": caption},
            timeout=10,
        )
        return resp.json().get("ok", False)
    except Exception as exc:
        logger.error("Telegram photo error: %s", exc)
        return False


# ── Signal formatter ──────────────────────────────────────────────────────────

def _format_signal_message(signal: dict, signal_id: int | None = None) -> str:
    action  = signal["action"].upper()
    price   = signal["price"]
    volume  = signal["volume"]
    ts      = signal.get("timestamp", "")
    strat   = signal.get("strategy", "N/A")
    indic   = signal.get("indicators", {})

    # Emoji cues
    emoji   = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⚪"
    arrow   = "📈" if action == "BUY" else "📉" if action == "SELL" else "➡️"

    # Indicator summary
    rsi        = indic.get("rsi", "N/A")
    ema_fast   = indic.get("ema_fast", "N/A")
    ema_slow   = indic.get("ema_slow", "N/A")
    atr        = indic.get("atr", "N/A")
    confidence = indic.get("confidence_pct", "N/A")

    lines = [
        f"{emoji} <b>XAUUSD {action}</b> {arrow}",
        "",
        f"💰 <b>Price:</b>    {price:.2f}",
        f"📦 <b>Volume:</b>   {volume} lots",
        f"🧮 <b>Strategy:</b> {strat}",
        "",
        "── Indicators ──",
        f"  RSI({indic.get('rsi_period', 14)}): {rsi}",
        f"  EMA Fast:  {ema_fast}",
        f"  EMA Slow:  {ema_slow}",
        f"  ATR:       {atr}",
    ]

    if confidence != "N/A":
        lines.append(f"  Confidence: {confidence}%")

    ma_cross = indic.get("ma_cross", "")
    if ma_cross and ma_cross != "none":
        lines.append(f"  MA Cross:  {ma_cross.capitalize()}")

    lines += [
        "",
        f"🕐 <b>Time:</b> {ts}",
    ]

    if signal_id:
        lines.append(f"🆔 Signal ID: #{signal_id}")

    lines += [
        "",
        "⚠️ <i>Manual execution required on MT5 mobile.</i>",
        "📱 Open MT5 → New Order → Confirm before placing.",
    ]

    return "\n".join(lines)


# ── Public notification functions ─────────────────────────────────────────────

def notify_signal(signal: dict, signal_id: int | None = None) -> bool:
    """Send a trade signal notification. Only sends for buy/sell (not hold)."""
    if signal.get("action") == "hold":
        logger.info("Signal is HOLD – skipping Telegram notification")
        return True

    msg = _format_signal_message(signal, signal_id)
    ok  = _send_message(msg)
    if ok:
        logger.info("Signal notification sent (ID=%s)", signal_id)
    return ok


def notify_startup() -> bool:
    ts  = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        "🚀 <b>XAUUSD Trading Bot Started</b>\n\n"
        f"⏰ Time: {ts}\n"
        "📡 Monitoring market for signals…\n\n"
        "Use /status in this chat to check bot health."
    )
    return _send_message(msg)


def notify_error(error_msg: str) -> bool:
    msg = (
        "❌ <b>Bot Error</b>\n\n"
        f"<code>{error_msg[:400]}</code>\n\n"
        "Check VPS logs for details."
    )
    return _send_message(msg, silent=True)


def notify_daily_summary(stats: dict) -> bool:
    msg = (
        "📊 <b>Daily Summary – XAUUSD</b>\n\n"
        f"Total Signals:  {stats.get('total_signals', 0)}\n"
        f"  🟢 Buy:       {stats.get('buy_signals', 0)}\n"
        f"  🔴 Sell:      {stats.get('sell_signals', 0)}\n\n"
        f"Closed Trades:  {stats.get('closed_trades', 0)}\n"
        f"Total P&L:      {stats.get('total_pnl', 0):.2f} USD (est.)\n"
        f"Win Rate:       {stats.get('win_rate', 0):.1f}%\n\n"
        "⚠️ <i>P&L is estimated; always verify on MT5.</i>"
    )
    return _send_message(msg)


# ── Telegram bot command handler (polling, lightweight) ───────────────────────

class TelegramCommandHandler:
    """
    Minimal long-polling handler for Telegram slash commands.
    Run in a background thread alongside the main scheduler.
    """

    def __init__(self, db_stats_fn, recent_signals_fn):
        self.db_stats_fn        = db_stats_fn
        self.recent_signals_fn  = recent_signals_fn
        self.last_update_id     = 0

    def poll(self):
        if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
            return
        try:
            resp = requests.get(
                f"{BASE_URL}/getUpdates",
                params={"offset": self.last_update_id + 1, "timeout": 30},
                timeout=35,
            )
            updates = resp.json().get("result", [])
            for update in updates:
                self.last_update_id = update["update_id"]
                msg = update.get("message", {})
                text = msg.get("text", "").strip().lower()
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if chat_id != str(TELEGRAM_CHAT_ID):
                    continue
                self._handle_command(text)
        except Exception as exc:
            logger.warning("Telegram poll error: %s", exc)

    def _handle_command(self, text: str):
        if text in ("/start", "/help"):
            _send_message(
                "🤖 <b>XAUUSD Bot Commands</b>\n\n"
                "/status – Bot status & latest price\n"
                "/signals – Last 5 signals\n"
                "/stats – Performance summary\n"
                "/help – This message"
            )
        elif text == "/status":
            from signals.data_fetcher import get_current_price
            try:
                price = get_current_price()
                _send_message(f"✅ Bot is running\n💰 XAUUSD: <b>{price:.2f}</b>")
            except Exception as exc:
                _send_message(f"⚠️ Price fetch error: {exc}")
        elif text == "/signals":
            signals = self.recent_signals_fn(5)
            if not signals:
                _send_message("No signals yet.")
                return
            lines = ["📋 <b>Last 5 Signals</b>\n"]
            for s in signals:
                emoji = "🟢" if s["action"] == "buy" else "🔴" if s["action"] == "sell" else "⚪"
                lines.append(f"{emoji} {s['action'].upper()} @ {s['price']:.2f}  [{s['timestamp'][:16]}]")
            _send_message("\n".join(lines))
        elif text == "/stats":
            stats = self.db_stats_fn()
            notify_daily_summary(stats)

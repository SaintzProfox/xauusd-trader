"""
XAUUSD Trading System - Configuration
Edit these values before running.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "YOUR_CHAT_ID_HERE")

# ── Market Data ───────────────────────────────────────────────────────────────
# Options: "yfinance" | "alphavantage" | "twelvedata"
DATA_PROVIDER      = os.getenv("DATA_PROVIDER", "yfinance")
ALPHA_VANTAGE_KEY  = os.getenv("ALPHA_VANTAGE_KEY", "demo")
TWELVE_DATA_KEY    = os.getenv("TWELVE_DATA_KEY",   "demo")

# Symbol used by provider (yfinance uses GC=F for gold futures; XAU/USD spot via "GC=F")
SYMBOL_YFINANCE    = "GC=F"          # Gold futures – closest free proxy for XAUUSD
SYMBOL_DISPLAY     = "XAUUSD"

# ── Strategy ──────────────────────────────────────────────────────────────────
STRATEGY           = "combined"      # "ma_crossover" | "rsi" | "combined"

# Moving Average crossover
MA_FAST            = 9
MA_SLOW            = 21

# RSI
RSI_PERIOD         = 14
RSI_OVERBOUGHT     = 70
RSI_OVERSOLD       = 30

# Default trade volume (lots)
DEFAULT_VOLUME     = 0.1

# ── Scheduler ─────────────────────────────────────────────────────────────────
SIGNAL_INTERVAL_MINUTES = 15         # How often to check for signals

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH            = os.getenv("DB_PATH", "db/trading.db")

# ── Web Dashboard ─────────────────────────────────────────────────────────────
DASHBOARD_HOST     = "0.0.0.0"
DASHBOARD_PORT     = int(os.getenv("PORT", 8000))
SECRET_KEY         = os.getenv("SECRET_KEY", "change-me-in-production-please")

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_FILE           = "logs/trader.log"
LOG_LEVEL          = "INFO"

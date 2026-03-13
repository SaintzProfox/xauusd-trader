"""
signals/data_fetcher.py
Fetch OHLCV data for XAUUSD from free providers.
Provider priority: yfinance → Alpha Vantage → Twelve Data → cached fallback.
"""

import logging
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from config import (
    DATA_PROVIDER, SYMBOL_YFINANCE,
    ALPHA_VANTAGE_KEY, TWELVE_DATA_KEY,
    SYMBOL_DISPLAY,
)
from db.database import cache_price, get_latest_cached_price

logger = logging.getLogger(__name__)


# ── yfinance ──────────────────────────────────────────────────────────────────

def _fetch_yfinance(period: str = "5d", interval: str = "15m") -> pd.DataFrame:
    """Uses yfinance (no API key needed). GC=F = Gold Futures ≈ XAUUSD."""
    import yfinance as yf
    ticker = yf.Ticker(SYMBOL_YFINANCE)
    df = ticker.history(period=period, interval=interval, auto_adjust=True)
    if df.empty:
        raise ValueError("yfinance returned empty dataframe")
    df.index = pd.to_datetime(df.index, utc=True)
    df.rename(columns={"Open": "open", "High": "high",
                        "Low": "low", "Close": "close",
                        "Volume": "volume"}, inplace=True)
    return df[["open", "high", "low", "close", "volume"]].copy()


# ── Alpha Vantage ─────────────────────────────────────────────────────────────

def _fetch_alphavantage(interval: str = "15min") -> pd.DataFrame:
    url = (
        "https://www.alphavantage.co/query"
        f"?function=FX_INTRADAY&from_symbol=XAU&to_symbol=USD"
        f"&interval={interval}&outputsize=compact&apikey={ALPHA_VANTAGE_KEY}"
    )
    data = requests.get(url, timeout=15).json()
    key = [k for k in data if "Time Series" in k]
    if not key:
        raise ValueError(f"Alpha Vantage error: {data.get('Note', data)}")
    ts = data[key[0]]
    records = [
        {
            "timestamp": pd.Timestamp(k, tz="UTC"),
            "open":   float(v["1. open"]),
            "high":   float(v["2. high"]),
            "low":    float(v["3. low"]),
            "close":  float(v["4. close"]),
            "volume": 0.0,
        }
        for k, v in ts.items()
    ]
    df = pd.DataFrame(records).set_index("timestamp").sort_index()
    return df


# ── Twelve Data ───────────────────────────────────────────────────────────────

def _fetch_twelvedata(interval: str = "15min", outputsize: int = 100) -> pd.DataFrame:
    url = (
        "https://api.twelvedata.com/time_series"
        f"?symbol=XAU/USD&interval={interval}"
        f"&outputsize={outputsize}&apikey={TWELVE_DATA_KEY}"
    )
    data = requests.get(url, timeout=15).json()
    if data.get("status") == "error":
        raise ValueError(f"Twelve Data error: {data.get('message')}")
    values = data["values"]
    records = [
        {
            "timestamp": pd.Timestamp(v["datetime"], tz="UTC"),
            "open":   float(v["open"]),
            "high":   float(v["high"]),
            "low":    float(v["low"]),
            "close":  float(v["close"]),
            "volume": float(v.get("volume", 0)),
        }
        for v in values
    ]
    df = pd.DataFrame(records).set_index("timestamp").sort_index()
    return df


# ── Public interface ──────────────────────────────────────────────────────────

def fetch_ohlcv(retries: int = 3) -> pd.DataFrame:
    """
    Returns a DataFrame with columns [open, high, low, close, volume]
    indexed by UTC timestamp.  Falls back through providers on error.
    """
    providers = {
        "yfinance":      _fetch_yfinance,
        "alphavantage":  _fetch_alphavantage,
        "twelvedata":    _fetch_twelvedata,
    }
    order = [DATA_PROVIDER] + [p for p in providers if p != DATA_PROVIDER]

    last_error = None
    for provider in order:
        for attempt in range(1, retries + 1):
            try:
                logger.info("Fetching OHLCV via %s (attempt %d)", provider, attempt)
                df = providers[provider]()
                latest_price = float(df["close"].iloc[-1])
                cache_price(SYMBOL_DISPLAY, latest_price)
                logger.info("Fetched %d bars; latest close=%.2f", len(df), latest_price)
                return df
            except Exception as exc:
                last_error = exc
                logger.warning("%s attempt %d failed: %s", provider, attempt, exc)
                time.sleep(2 ** attempt)

    # All providers failed – try cached price to build a stub frame
    cached = get_latest_cached_price(SYMBOL_DISPLAY)
    if cached:
        logger.error("All providers failed; using cached price %.2f", cached)
        now = datetime.now(timezone.utc)
        df = pd.DataFrame(
            [{"open": cached, "high": cached, "low": cached,
              "close": cached, "volume": 0.0}],
            index=pd.DatetimeIndex([now]),
        )
        return df

    raise RuntimeError(f"All data providers failed. Last error: {last_error}")


def get_current_price() -> float:
    """Quick helper – returns latest close price."""
    df = fetch_ohlcv()
    return float(df["close"].iloc[-1])

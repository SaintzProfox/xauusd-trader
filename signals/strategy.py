"""
signals/strategy.py
Trading strategy implementations for XAUUSD.

Strategies
──────────
  ma_crossover  – EMA fast/slow crossover
  rsi           – RSI overbought/oversold
  combined      – MA crossover confirmed by RSI (fewer false signals)
"""

import logging
from datetime import datetime, timezone

import pandas as pd
import numpy as np

from config import (
    STRATEGY, MA_FAST, MA_SLOW,
    RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD,
    DEFAULT_VOLUME, SYMBOL_DISPLAY,
)

logger = logging.getLogger(__name__)


# ── Indicator helpers ─────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl  = df["high"] - df["low"]
    hpc = (df["high"] - df["close"].shift()).abs()
    lpc = (df["low"]  - df["close"].shift()).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


# ── Strategy implementations ──────────────────────────────────────────────────

def _strategy_ma_crossover(df: pd.DataFrame) -> dict:
    """Buy when fast EMA crosses above slow EMA; sell on cross below."""
    close      = df["close"]
    fast_ema   = ema(close, MA_FAST)
    slow_ema   = ema(close, MA_SLOW)
    atr        = compute_atr(df)
    rsi_series = compute_rsi(close, RSI_PERIOD)

    prev_fast, curr_fast = fast_ema.iloc[-2], fast_ema.iloc[-1]
    prev_slow, curr_slow = slow_ema.iloc[-2], slow_ema.iloc[-1]

    action = "hold"
    if prev_fast <= prev_slow and curr_fast > curr_slow:
        action = "buy"
    elif prev_fast >= prev_slow and curr_fast < curr_slow:
        action = "sell"

    return {
        "action": action,
        "indicators": {
            "ema_fast":    round(curr_fast, 2),
            "ema_slow":    round(curr_slow, 2),
            "rsi":         round(float(rsi_series.iloc[-1]), 2),
            "atr":         round(float(atr.iloc[-1]), 2),
            "ma_spread":   round(curr_fast - curr_slow, 2),
        },
    }


def _strategy_rsi(df: pd.DataFrame) -> dict:
    """Buy on RSI oversold bounce; sell on RSI overbought reversal."""
    close      = df["close"]
    rsi_series = compute_rsi(close, RSI_PERIOD)
    atr        = compute_atr(df)
    fast_ema   = ema(close, MA_FAST)
    slow_ema   = ema(close, MA_SLOW)

    prev_rsi = float(rsi_series.iloc[-2])
    curr_rsi = float(rsi_series.iloc[-1])

    action = "hold"
    # Bounce up from oversold
    if prev_rsi < RSI_OVERSOLD and curr_rsi >= RSI_OVERSOLD:
        action = "buy"
    # Turn down from overbought
    elif prev_rsi > RSI_OVERBOUGHT and curr_rsi <= RSI_OVERBOUGHT:
        action = "sell"

    return {
        "action": action,
        "indicators": {
            "rsi":      round(curr_rsi, 2),
            "ema_fast": round(float(fast_ema.iloc[-1]), 2),
            "ema_slow": round(float(slow_ema.iloc[-1]), 2),
            "atr":      round(float(atr.iloc[-1]), 2),
        },
    }


def _strategy_combined(df: pd.DataFrame) -> dict:
    """
    Combined: MA crossover direction confirmed by RSI not being extreme
    in the opposite direction.  Reduces whipsaws.
    """
    close      = df["close"]
    fast_ema   = ema(close, MA_FAST)
    slow_ema   = ema(close, MA_SLOW)
    rsi_series = compute_rsi(close, RSI_PERIOD)
    atr        = compute_atr(df)

    prev_fast, curr_fast = float(fast_ema.iloc[-2]), float(fast_ema.iloc[-1])
    prev_slow, curr_slow = float(slow_ema.iloc[-2]), float(slow_ema.iloc[-1])
    curr_rsi             = float(rsi_series.iloc[-1])
    prev_rsi             = float(rsi_series.iloc[-2])

    # Crossover direction
    ma_cross = "none"
    if prev_fast <= prev_slow and curr_fast > curr_slow:
        ma_cross = "bullish"
    elif prev_fast >= prev_slow and curr_fast < curr_slow:
        ma_cross = "bearish"

    # RSI confirmation
    action = "hold"
    confidence = 0.0

    if ma_cross == "bullish" and curr_rsi < RSI_OVERBOUGHT:
        action = "buy"
        # Higher confidence when RSI was previously in oversold territory
        confidence = round(min(100, 50 + (RSI_OVERSOLD - curr_rsi) * 1.5 + abs(curr_fast - curr_slow) / atr.iloc[-1] * 10), 1)
    elif ma_cross == "bearish" and curr_rsi > RSI_OVERSOLD:
        action = "sell"
        confidence = round(min(100, 50 + (curr_rsi - RSI_OVERBOUGHT) * 1.5 + abs(curr_fast - curr_slow) / atr.iloc[-1] * 10), 1)

    # Price action: check if price is above/below both EMAs
    price_above_emas = close.iloc[-1] > max(curr_fast, curr_slow)
    price_below_emas = close.iloc[-1] < min(curr_fast, curr_slow)

    trend = "neutral"
    if price_above_emas:
        trend = "bullish"
    elif price_below_emas:
        trend = "bearish"

    return {
        "action": action,
        "indicators": {
            "ema_fast":       round(curr_fast, 2),
            "ema_slow":       round(curr_slow, 2),
            "rsi":            round(curr_rsi, 2),
            "prev_rsi":       round(prev_rsi, 2),
            "atr":            round(float(atr.iloc[-1]), 2),
            "ma_cross":       ma_cross,
            "trend":          trend,
            "confidence_pct": confidence,
        },
    }


# ── Public interface ──────────────────────────────────────────────────────────

STRATEGY_MAP = {
    "ma_crossover": _strategy_ma_crossover,
    "rsi":          _strategy_rsi,
    "combined":     _strategy_combined,
}


def generate_signal(df: pd.DataFrame) -> dict:
    """
    Run the configured strategy and return a signal dict.
    Signal JSON format:
        {
            "symbol":     "XAUUSD",
            "action":     "buy" | "sell" | "hold",
            "volume":     0.1,
            "price":      1970.50,
            "strategy":   "combined",
            "indicators": {...},
            "timestamp":  "2026-03-13T10:00:00Z"
        }
    """
    if len(df) < max(MA_SLOW, RSI_PERIOD) + 5:
        logger.warning("Not enough bars (%d) to compute indicators", len(df))
        return _empty_signal(df)

    strategy_fn = STRATEGY_MAP.get(STRATEGY, _strategy_combined)

    try:
        result = strategy_fn(df)
    except Exception as exc:
        logger.error("Strategy error: %s", exc, exc_info=True)
        return _empty_signal(df)

    current_price = float(df["close"].iloc[-1])
    timestamp     = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    signal = {
        "symbol":     SYMBOL_DISPLAY,
        "action":     result["action"],
        "volume":     DEFAULT_VOLUME,
        "price":      round(current_price, 2),
        "strategy":   STRATEGY,
        "indicators": result.get("indicators", {}),
        "timestamp":  timestamp,
    }

    logger.info(
        "Signal generated: %s @ %.2f  RSI=%.1f",
        signal["action"].upper(),
        signal["price"],
        signal["indicators"].get("rsi", 0),
    )
    return signal


def _empty_signal(df: pd.DataFrame) -> dict:
    price = float(df["close"].iloc[-1]) if not df.empty else 0.0
    return {
        "symbol":     SYMBOL_DISPLAY,
        "action":     "hold",
        "volume":     DEFAULT_VOLUME,
        "price":      round(price, 2),
        "strategy":   STRATEGY,
        "indicators": {},
        "timestamp":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

"""
backtest.py
───────────
Two modes:

1. BACKTEST  – run the strategy over historical OHLCV data and print a
               full performance report (Sharpe, max drawdown, win rate…)

2. PAPER     – forward paper-trading tracker: every time the signal
               generator fires a real signal it is also mirrored into a
               paper portfolio so you can watch simulated P&L grow in
               real time on the dashboard.

Usage
─────
  # Run a backtest from the command line:
  cd ~/xauusd-trader
  source venv/bin/activate
  python backtest.py --mode backtest --days 90

  # Paper trading is automatic – just leave the generator running.
  # View results at  GET /api/paper
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from config import (
    MA_FAST, MA_SLOW, RSI_PERIOD,
    RSI_OVERBOUGHT, RSI_OVERSOLD,
    DEFAULT_VOLUME, SYMBOL_DISPLAY, STRATEGY,
)
from signals.strategy import ema, compute_rsi, compute_atr

logger = logging.getLogger("backtest")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")


# ══════════════════════════════════════════════════════════════════════════════
# Data helpers
# ══════════════════════════════════════════════════════════════════════════════

def fetch_historical(days: int = 90) -> pd.DataFrame:
    """
    Pull daily OHLCV for backtesting.
    Tries Twelve Data → Alpha Vantage → yfinance in order.
    """
    from config import TWELVE_DATA_KEY, ALPHA_VANTAGE_KEY, SYMBOL_YFINANCE
    import requests

    HEADERS = {"User-Agent": "Mozilla/5.0"}

    # ── Twelve Data daily ──────────────────────────────────────────────────
    if TWELVE_DATA_KEY not in ("demo", "your_twelvedata_key", ""):
        try:
            url = (
                f"https://api.twelvedata.com/time_series"
                f"?symbol=XAU/USD&interval=1day&outputsize={days}"
                f"&apikey={TWELVE_DATA_KEY}"
            )
            data = requests.get(url, headers=HEADERS, timeout=15).json()
            if data.get("status") != "error" and data.get("values"):
                records = [
                    {
                        "timestamp": pd.Timestamp(v["datetime"], tz="UTC"),
                        "open": float(v["open"]), "high": float(v["high"]),
                        "low":  float(v["low"]),  "close": float(v["close"]),
                        "volume": 0.0,
                    }
                    for v in data["values"]
                ]
                df = pd.DataFrame(records).set_index("timestamp").sort_index()
                logger.info("Historical data: %d daily bars via Twelve Data", len(df))
                return df
        except Exception as exc:
            logger.warning("Twelve Data historical failed: %s", exc)

    # ── Alpha Vantage daily ────────────────────────────────────────────────
    if ALPHA_VANTAGE_KEY not in ("demo", "your_alphavantage_key", ""):
        try:
            url = (
                "https://www.alphavantage.co/query"
                f"?function=FX_DAILY&from_symbol=XAU&to_symbol=USD"
                f"&outputsize=full&apikey={ALPHA_VANTAGE_KEY}"
            )
            data = requests.get(url, headers=HEADERS, timeout=15).json()
            ts_key = [k for k in data if "Time Series" in k]
            if ts_key:
                ts = data[ts_key[0]]
                records = [
                    {
                        "timestamp": pd.Timestamp(k, tz="UTC"),
                        "open":  float(v["1. open"]),
                        "high":  float(v["2. high"]),
                        "low":   float(v["3. low"]),
                        "close": float(v["4. close"]),
                        "volume": 0.0,
                    }
                    for k, v in ts.items()
                ]
                df = pd.DataFrame(records).set_index("timestamp").sort_index()
                df = df.tail(days)
                logger.info("Historical data: %d daily bars via Alpha Vantage", len(df))
                return df
        except Exception as exc:
            logger.warning("Alpha Vantage historical failed: %s", exc)

    # ── yfinance fallback ──────────────────────────────────────────────────
    try:
        import yfinance as yf
        import requests as req
        session = req.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        ticker = yf.Ticker(SYMBOL_YFINANCE, session=session)
        df = ticker.history(period=f"{days}d", interval="1d", auto_adjust=True)
        if not df.empty:
            df.index = pd.to_datetime(df.index, utc=True)
            df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                                "Close": "close", "Volume": "volume"}, inplace=True)
            logger.info("Historical data: %d daily bars via yfinance", len(df))
            return df[["open", "high", "low", "close", "volume"]]
    except Exception as exc:
        logger.warning("yfinance historical failed: %s", exc)

    raise RuntimeError("Could not fetch historical data from any provider.")


# ══════════════════════════════════════════════════════════════════════════════
# Signal generation over full DataFrame (vectorised)
# ══════════════════════════════════════════════════════════════════════════════

def generate_signals_series(df: pd.DataFrame) -> pd.Series:
    """
    Returns a Series of  1 (buy), -1 (sell), 0 (hold)
    for every bar in df, using the combined strategy.
    """
    close    = df["close"]
    fast_ema = ema(close, MA_FAST)
    slow_ema = ema(close, MA_SLOW)
    rsi      = compute_rsi(close, RSI_PERIOD)

    prev_fast = fast_ema.shift(1)
    prev_slow = slow_ema.shift(1)

    bullish_cross = (prev_fast <= prev_slow) & (fast_ema > slow_ema)
    bearish_cross = (prev_fast >= prev_slow) & (fast_ema < slow_ema)

    signals = pd.Series(0, index=df.index)
    signals[bullish_cross & (rsi < RSI_OVERBOUGHT)] =  1
    signals[bearish_cross & (rsi > RSI_OVERSOLD)]   = -1

    return signals


# ══════════════════════════════════════════════════════════════════════════════
# Backtest engine
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    direction:   int        # 1=buy, -1=sell
    entry_price: float
    entry_date:  str
    exit_price:  float = 0.0
    exit_date:   str   = ""
    pnl:         float = 0.0
    pnl_pct:     float = 0.0
    status:      str   = "open"   # open | closed


@dataclass
class BacktestResult:
    symbol:           str
    strategy:         str
    period_days:      int
    total_bars:       int
    start_date:       str
    end_date:         str
    start_price:      float
    end_price:        float
    # Trade stats
    total_trades:     int   = 0
    winning_trades:   int   = 0
    losing_trades:    int   = 0
    win_rate:         float = 0.0
    # P&L
    total_pnl:        float = 0.0
    total_pnl_pct:    float = 0.0
    avg_win:          float = 0.0
    avg_loss:         float = 0.0
    profit_factor:    float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio:     float = 0.0
    # Buy & hold comparison
    buy_hold_pct:     float = 0.0
    trades:           list  = field(default_factory=list)
    equity_curve:     list  = field(default_factory=list)


def run_backtest(df: pd.DataFrame, initial_capital: float = 10_000.0,
                 lot_size: float = DEFAULT_VOLUME,
                 sl_atr_mult: float = 1.5,
                 tp_atr_mult: float = 2.5) -> BacktestResult:
    """
    Event-driven backtest with ATR-based stop-loss and take-profit.

    Parameters
    ──────────
    initial_capital : starting cash in USD
    lot_size        : contract lots per trade (1 lot = 100 oz gold)
    sl_atr_mult     : stop-loss = entry ± ATR × this
    tp_atr_mult     : take-profit = entry ± ATR × this
    """
    signals = generate_signals_series(df)
    atr     = compute_atr(df)

    capital   = initial_capital
    equity    = [capital]
    peak      = capital
    max_dd    = 0.0
    trades    = []
    open_trade: Trade | None = None
    returns   = []

    for i in range(1, len(df)):
        bar        = df.iloc[i]
        prev_bar   = df.iloc[i - 1]
        sig        = signals.iloc[i]
        bar_atr    = float(atr.iloc[i])
        close      = float(bar["close"])
        date_str   = str(df.index[i].date())

        # ── Check if open trade hits SL or TP ────────────────────────────
        if open_trade and open_trade.status == "open":
            d = open_trade.direction
            hit_sl = (d ==  1 and bar["low"]  <= open_trade.exit_price and open_trade.pnl < 0) or \
                     (d == -1 and bar["high"] >= open_trade.exit_price and open_trade.pnl < 0)
            # Recalculate: exit_price set at open; check SL/TP levels
            sl = open_trade.entry_price - d * bar_atr * sl_atr_mult
            tp = open_trade.entry_price + d * bar_atr * tp_atr_mult

            closed = False
            exit_p = close

            if d == 1:   # long
                if bar["low"] <= sl:
                    exit_p, closed = sl, True
                elif bar["high"] >= tp:
                    exit_p, closed = tp, True
            else:        # short
                if bar["high"] >= sl:
                    exit_p, closed = sl, True
                elif bar["low"] <= tp:
                    exit_p, closed = tp, True

            # Also close on opposite signal
            if sig != 0 and sig != d:
                exit_p, closed = close, True

            if closed:
                pnl     = d * (exit_p - open_trade.entry_price) * lot_size * 100
                pnl_pct = pnl / capital * 100
                capital += pnl
                ret     = pnl / (open_trade.entry_price * lot_size * 100)
                returns.append(ret)

                open_trade.exit_price = round(exit_p, 2)
                open_trade.exit_date  = date_str
                open_trade.pnl        = round(pnl, 2)
                open_trade.pnl_pct    = round(pnl_pct, 4)
                open_trade.status     = "closed"
                trades.append(open_trade)
                open_trade = None

        # ── Open new trade on signal ──────────────────────────────────────
        if sig != 0 and open_trade is None:
            open_trade = Trade(
                direction   = int(sig),
                entry_price = close,
                entry_date  = date_str,
                status      = "open",
            )

        # ── Equity & drawdown ─────────────────────────────────────────────
        equity.append(round(capital, 2))
        if capital > peak:
            peak = capital
        dd = (peak - capital) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Close any open trade at last bar
    if open_trade and open_trade.status == "open":
        last_close = float(df["close"].iloc[-1])
        pnl        = open_trade.direction * (last_close - open_trade.entry_price) * lot_size * 100
        open_trade.exit_price = round(last_close, 2)
        open_trade.exit_date  = str(df.index[-1].date())
        open_trade.pnl        = round(pnl, 2)
        open_trade.status     = "closed"
        trades.append(open_trade)
        capital += pnl
        returns.append(pnl / (open_trade.entry_price * lot_size * 100))

    # ── Aggregate stats ───────────────────────────────────────────────────
    closed   = [t for t in trades if t.status == "closed"]
    wins     = [t for t in closed if t.pnl > 0]
    losses   = [t for t in closed if t.pnl <= 0]
    total_pnl = sum(t.pnl for t in closed)

    gross_profit = sum(t.pnl for t in wins)   or 0
    gross_loss   = abs(sum(t.pnl for t in losses)) or 1
    pf           = round(gross_profit / gross_loss, 2)

    arr   = np.array(returns)
    sharpe = round(float(np.mean(arr) / (np.std(arr) + 1e-9) * np.sqrt(252)), 2) if len(arr) > 1 else 0.0

    bh_pct = round((float(df["close"].iloc[-1]) - float(df["close"].iloc[0]))
                   / float(df["close"].iloc[0]) * 100, 2)

    result = BacktestResult(
        symbol           = SYMBOL_DISPLAY,
        strategy         = STRATEGY,
        period_days      = len(df),
        total_bars       = len(df),
        start_date       = str(df.index[0].date()),
        end_date         = str(df.index[-1].date()),
        start_price      = round(float(df["close"].iloc[0]), 2),
        end_price        = round(float(df["close"].iloc[-1]), 2),
        total_trades     = len(closed),
        winning_trades   = len(wins),
        losing_trades    = len(losses),
        win_rate         = round(len(wins) / max(len(closed), 1) * 100, 1),
        total_pnl        = round(total_pnl, 2),
        total_pnl_pct    = round(total_pnl / initial_capital * 100, 2),
        avg_win          = round(gross_profit / max(len(wins), 1), 2),
        avg_loss         = round(-abs(sum(t.pnl for t in losses)) / max(len(losses), 1), 2),
        profit_factor    = pf,
        max_drawdown_pct = round(max_dd, 2),
        sharpe_ratio     = sharpe,
        buy_hold_pct     = bh_pct,
        trades           = [asdict(t) for t in closed[-50:]],  # last 50
        equity_curve     = equity,
    )
    return result


def print_report(r: BacktestResult):
    """Pretty-print backtest report to terminal."""
    print("\n" + "═" * 56)
    print(f"  BACKTEST REPORT  –  {r.symbol}  ({r.strategy})")
    print("═" * 56)
    print(f"  Period      : {r.start_date} → {r.end_date}  ({r.period_days} bars)")
    print(f"  Price range : {r.start_price:.2f} → {r.end_price:.2f}")
    print(f"  Buy & Hold  : {r.buy_hold_pct:+.2f}%")
    print("─" * 56)
    print(f"  Total Trades  : {r.total_trades}")
    print(f"  Win Rate      : {r.win_rate:.1f}%  ({r.winning_trades}W / {r.losing_trades}L)")
    print(f"  Avg Win       : ${r.avg_win:.2f}")
    print(f"  Avg Loss      : ${r.avg_loss:.2f}")
    print(f"  Profit Factor : {r.profit_factor:.2f}  (>1.5 is good)")
    print("─" * 56)
    print(f"  Total P&L     : ${r.total_pnl:+.2f}  ({r.total_pnl_pct:+.2f}%)")
    print(f"  Max Drawdown  : -{r.max_drawdown_pct:.2f}%")
    print(f"  Sharpe Ratio  : {r.sharpe_ratio:.2f}  (>1.0 is good)")
    print("═" * 56)
    if r.total_trades == 0:
        print("  ⚠  No trades generated. Try --days 180 for more history.")
    print()


# ══════════════════════════════════════════════════════════════════════════════
# Paper Trading
# ══════════════════════════════════════════════════════════════════════════════

class PaperTrader:
    """
    Mirrors live signals into a paper portfolio stored in SQLite.
    Call .on_signal(signal) every time the generator fires.
    Call .on_price(price)  every tick to update open trade P&L.
    """

    def __init__(self, initial_capital: float = 10_000.0,
                 lot_size: float = DEFAULT_VOLUME):
        from db.database import get_connection
        self.get_connection  = get_connection
        self.initial_capital = initial_capital
        self.lot_size        = lot_size
        self._init_tables()

    def _init_tables(self):
        with self.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    direction   INTEGER NOT NULL,   -- 1=buy, -1=sell
                    entry_price REAL    NOT NULL,
                    exit_price  REAL,
                    entry_date  TEXT    NOT NULL,
                    exit_date   TEXT,
                    lot_size    REAL    NOT NULL,
                    pnl         REAL    DEFAULT 0,
                    status      TEXT    DEFAULT 'open'
                );
                CREATE TABLE IF NOT EXISTS paper_state (
                    id       INTEGER PRIMARY KEY CHECK (id = 1),
                    capital  REAL NOT NULL,
                    updated  TEXT NOT NULL
                );
            """)
            # Seed capital if first run
            exists = conn.execute(
                "SELECT 1 FROM paper_state WHERE id=1"
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO paper_state (id, capital, updated) VALUES (1, ?, ?)",
                    (self.initial_capital,
                     datetime.now(timezone.utc).isoformat())
                )

    # ── External API ─────────────────────────────────────────────────────────

    def on_signal(self, signal: dict):
        """Call this when a new live signal fires."""
        action = signal.get("action")
        price  = float(signal.get("price", 0))
        if action not in ("buy", "sell") or price <= 0:
            return

        direction = 1 if action == "buy" else -1
        now       = datetime.now(timezone.utc).isoformat()

        with self.get_connection() as conn:
            # Close any existing open trade first
            open_t = conn.execute(
                "SELECT * FROM paper_trades WHERE status='open' LIMIT 1"
            ).fetchone()
            if open_t:
                self._close_trade(conn, dict(open_t), price, now)

            # Open new trade
            conn.execute(
                """INSERT INTO paper_trades
                   (direction, entry_price, entry_date, lot_size, status)
                   VALUES (?, ?, ?, ?, 'open')""",
                (direction, price, now, self.lot_size)
            )
        logger.info("Paper: opened %s @ %.2f", action.upper(), price)

    def on_price(self, price: float):
        """Call this each tick to update unrealised P&L on open trade."""
        with self.get_connection() as conn:
            open_t = conn.execute(
                "SELECT * FROM paper_trades WHERE status='open' LIMIT 1"
            ).fetchone()
            if not open_t:
                return
            t   = dict(open_t)
            pnl = t["direction"] * (price - t["entry_price"]) * t["lot_size"] * 100
            conn.execute(
                "UPDATE paper_trades SET pnl=? WHERE id=?",
                (round(pnl, 2), t["id"])
            )

    def get_summary(self) -> dict:
        with self.get_connection() as conn:
            state    = conn.execute(
                "SELECT capital FROM paper_state WHERE id=1"
            ).fetchone()
            capital  = float(state[0]) if state else self.initial_capital

            closed   = conn.execute(
                "SELECT * FROM paper_trades WHERE status='closed' ORDER BY id DESC"
            ).fetchall()
            open_t   = conn.execute(
                "SELECT * FROM paper_trades WHERE status='open' LIMIT 1"
            ).fetchone()

            closed_l  = [dict(r) for r in closed]
            wins      = [t for t in closed_l if t["pnl"] > 0]
            losses    = [t for t in closed_l if t["pnl"] <= 0]
            total_pnl = sum(t["pnl"] for t in closed_l)
            unrealised = float(open_t["pnl"]) if open_t else 0.0

            return {
                "initial_capital":  self.initial_capital,
                "current_capital":  round(capital + total_pnl, 2),
                "total_pnl":        round(total_pnl, 2),
                "unrealised_pnl":   round(unrealised, 2),
                "total_pnl_pct":    round(total_pnl / self.initial_capital * 100, 2),
                "total_trades":     len(closed_l),
                "winning_trades":   len(wins),
                "losing_trades":    len(losses),
                "win_rate":         round(len(wins) / max(len(closed_l), 1) * 100, 1),
                "open_trade":       dict(open_t) if open_t else None,
                "recent_trades":    closed_l[:20],
            }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _close_trade(self, conn, trade: dict, price: float, now: str):
        pnl = trade["direction"] * (price - trade["entry_price"]) * trade["lot_size"] * 100
        conn.execute(
            """UPDATE paper_trades
               SET exit_price=?, exit_date=?, pnl=?, status='closed'
               WHERE id=?""",
            (round(price, 2), now, round(pnl, 2), trade["id"])
        )
        # Update capital
        conn.execute(
            "UPDATE paper_state SET capital=capital+?, updated=? WHERE id=1",
            (round(pnl, 2), now)
        )
        logger.info("Paper: closed trade #%d  PnL=%.2f", trade["id"], pnl)


# ══════════════════════════════════════════════════════════════════════════════
# CLI entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="XAUUSD Backtester")
    parser.add_argument("--mode",    default="backtest",
                        choices=["backtest", "paper_summary"],
                        help="backtest = historical simulation; paper_summary = show paper P&L")
    parser.add_argument("--days",    type=int, default=90,
                        help="Days of history to backtest (default 90)")
    parser.add_argument("--capital", type=float, default=10_000,
                        help="Starting capital in USD (default 10000)")
    parser.add_argument("--lot",     type=float, default=DEFAULT_VOLUME,
                        help="Lot size per trade (default 0.1)")
    parser.add_argument("--sl",      type=float, default=1.5,
                        help="Stop-loss ATR multiplier (default 1.5)")
    parser.add_argument("--tp",      type=float, default=2.5,
                        help="Take-profit ATR multiplier (default 2.5)")
    parser.add_argument("--json",    action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    if args.mode == "paper_summary":
        pt      = PaperTrader(initial_capital=args.capital, lot_size=args.lot)
        summary = pt.get_summary()
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print("\n── Paper Trading Summary ──")
            print(f"  Capital     : ${summary['current_capital']:,.2f}")
            print(f"  Total P&L   : ${summary['total_pnl']:+,.2f}  ({summary['total_pnl_pct']:+.2f}%)")
            print(f"  Unrealised  : ${summary['unrealised_pnl']:+,.2f}")
            print(f"  Trades      : {summary['total_trades']}  ({summary['winning_trades']}W/{summary['losing_trades']}L)")
            print(f"  Win Rate    : {summary['win_rate']:.1f}%")
            if summary["open_trade"]:
                ot = summary["open_trade"]
                d  = "BUY" if ot["direction"] == 1 else "SELL"
                print(f"  Open Trade  : {d} @ {ot['entry_price']:.2f}  PnL={ot['pnl']:+.2f}")
        return

    # Backtest mode
    print(f"Fetching {args.days} days of XAUUSD history…")
    df     = fetch_historical(args.days)
    result = run_backtest(df, initial_capital=args.capital,
                          lot_size=args.lot,
                          sl_atr_mult=args.sl,
                          tp_atr_mult=args.tp)
    if args.json:
        d = asdict(result) if hasattr(result, '__dataclass_fields__') else vars(result)
        print(json.dumps(d, indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    main()

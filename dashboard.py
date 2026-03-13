"""
dashboard.py – FastAPI web dashboard for XAUUSD trading bot.
Serves a mobile-friendly interface showing signals, trades, and stats.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))

from config import SECRET_KEY, SYMBOL_DISPLAY
from db.database import (
    init_db, get_recent_signals, get_trades, get_stats,
    close_trade, save_signal, get_latest_cached_price,
)
from signals.data_fetcher import get_current_price
from backtest import run_backtest, fetch_historical, PaperTrader

_paper = PaperTrader()

logger = logging.getLogger("dashboard")

app = FastAPI(
    title="XAUUSD Trading Dashboard",
    description="Semi-automated gold trading signal system",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files if directory exists
static_dir = Path("static")
if static_dir.exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()
    logger.info("Dashboard started – DB initialised")


# ── API Routes ────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/price")
async def price():
    try:
        p = get_current_price()
        return {"symbol": SYMBOL_DISPLAY, "price": p,
                "fetched_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        cached = get_latest_cached_price(SYMBOL_DISPLAY)
        if cached:
            return {"symbol": SYMBOL_DISPLAY, "price": cached,
                    "cached": True, "error": str(exc)}
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/api/signals")
async def signals(limit: int = Query(default=50, le=200)):
    rows = get_recent_signals(limit)
    for r in rows:
        if isinstance(r.get("indicators"), str):
            try:
                r["indicators"] = json.loads(r["indicators"])
            except Exception:
                r["indicators"] = {}
    return {"signals": rows, "count": len(rows)}


@app.get("/api/trades")
async def trades(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
):
    return {"trades": get_trades(status, limit)}


@app.get("/api/stats")
async def stats():
    return get_stats()


class CloseTradeRequest(BaseModel):
    close_price: float
    notes: str = ""


@app.post("/api/trades/{trade_id}/close")
async def close_trade_endpoint(trade_id: int, body: CloseTradeRequest):
    close_trade(trade_id, body.close_price, body.notes)
    return {"message": f"Trade #{trade_id} closed at {body.close_price}"}


@app.get("/api/paper")
async def paper_summary():
    return _paper.get_summary()


class BacktestRequest(BaseModel):
    days:    int   = 90
    capital: float = 10_000
    lot:     float = 0.1
    sl:      float = 1.5
    tp:      float = 2.5


@app.post("/api/backtest")
async def backtest_endpoint(body: BacktestRequest):
    try:
        df     = fetch_historical(body.days)
        result = run_backtest(df, initial_capital=body.capital,
                              lot_size=body.lot,
                              sl_atr_mult=body.sl,
                              tp_atr_mult=body.tp)
        # Convert dataclass to dict
        import dataclasses
        return dataclasses.asdict(result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Web Dashboard (single-page HTML) ─────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>XAUUSD Dashboard</title>
<style>
  :root {
    --bg:      #0f1117;
    --surface: #1a1d27;
    --border:  #2a2d3a;
    --gold:    #f5c842;
    --green:   #22c55e;
    --red:     #ef4444;
    --text:    #e2e8f0;
    --muted:   #64748b;
    --radius:  12px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; }
  header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 16px 20px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 10; }
  header h1 { color: var(--gold); font-size: 18px; letter-spacing: .5px; }
  .badge { background: var(--green); color: #000; font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 99px; }
  .container { max-width: 640px; margin: 0 auto; padding: 16px; }
  .price-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; text-align: center; margin-bottom: 16px; }
  .price-card .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }
  .price-card .value { color: var(--gold); font-size: 40px; font-weight: 700; margin: 6px 0; }
  .price-card .sub   { color: var(--muted); font-size: 12px; }
  .stats-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 16px; }
  .stat-card  { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; }
  .stat-card .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; }
  .stat-card .value { font-size: 22px; font-weight: 700; margin-top: 4px; }
  .green { color: var(--green); } .red { color: var(--red); } .gold { color: var(--gold); }
  .section { margin-bottom: 20px; }
  .section-title { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 10px; }
  .signal-item { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
  .signal-action { font-weight: 700; font-size: 15px; }
  .signal-meta   { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .signal-price  { text-align: right; }
  .signal-price .p { font-weight: 600; font-size: 15px; }
  .signal-price .t { font-size: 10px; color: var(--muted); }
  .pill { display: inline-block; padding: 2px 10px; border-radius: 99px; font-size: 11px; font-weight: 600; }
  .pill-buy  { background: rgba(34,197,94,.15); color: var(--green); }
  .pill-sell { background: rgba(239,68,68,.15);  color: var(--red); }
  .pill-hold { background: rgba(100,116,139,.15); color: var(--muted); }
  .ind-row   { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .ind-chip  { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 2px 8px; font-size: 11px; color: var(--muted); }
  .tab-bar   { display: flex; gap: 8px; margin-bottom: 14px; }
  .tab       { flex: 1; text-align: center; padding: 8px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; cursor: pointer; font-size: 13px; color: var(--muted); transition: all .2s; }
  .tab.active { border-color: var(--gold); color: var(--gold); }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  .refresh-btn { background: none; border: 1px solid var(--border); color: var(--muted); padding: 6px 14px; border-radius: 8px; cursor: pointer; font-size: 12px; }
  .refresh-btn:hover { border-color: var(--gold); color: var(--gold); }
  .empty { text-align: center; color: var(--muted); padding: 30px; font-size: 13px; }
  .pnl-pos { color: var(--green); } .pnl-neg { color: var(--red); }
  footer { text-align: center; color: var(--muted); font-size: 11px; padding: 20px; }
</style>
</head>
<body>

<header>
  <h1>⚡ XAUUSD Bot</h1>
  <span class="badge" id="status-badge">LIVE</span>
</header>

<div class="container">

  <!-- Price card -->
  <div class="price-card">
    <div class="label">XAUUSD Spot (approx.)</div>
    <div class="value" id="current-price">—</div>
    <div class="sub" id="price-time">Fetching…</div>
  </div>

  <!-- Stats -->
  <div class="stats-grid">
    <div class="stat-card">
      <div class="label">Total Signals</div>
      <div class="value gold" id="stat-signals">—</div>
    </div>
    <div class="stat-card">
      <div class="label">Win Rate</div>
      <div class="value green" id="stat-winrate">—</div>
    </div>
    <div class="stat-card">
      <div class="label">Est. P&L</div>
      <div class="value" id="stat-pnl">—</div>
    </div>
    <div class="stat-card">
      <div class="label">Closed Trades</div>
      <div class="value gold" id="stat-trades">—</div>
    </div>
  </div>

  <!-- Tabs -->
  <div class="tab-bar">
    <div class="tab active" onclick="switchTab('signals')">Signals</div>
    <div class="tab"        onclick="switchTab('trades')">Trades</div>
    <div class="tab"        onclick="switchTab('paper')">Paper</div>
    <div class="tab"        onclick="switchTab('backtest')">Backtest</div>
  </div>

  <!-- Signals tab -->
  <div class="tab-content active section" id="tab-signals">
    <div class="section-title">
      Recent Signals
      <button class="refresh-btn" onclick="loadSignals()" style="float:right">↻ Refresh</button>
    </div>
    <div id="signals-list"><div class="empty">Loading…</div></div>
  </div>

  <!-- Trades tab -->
  <div class="tab-content section" id="tab-trades">
    <div class="section-title">
      Trade History
      <button class="refresh-btn" onclick="loadTrades()" style="float:right">↻ Refresh</button>
    </div>
    <div id="trades-list"><div class="empty">Loading…</div></div>
  </div>

  <!-- Paper trading tab -->
  <div class="tab-content section" id="tab-paper">
    <div class="section-title">
      Paper Trading
      <button class="refresh-btn" onclick="loadPaper()" style="float:right">↻ Refresh</button>
    </div>
    <div id="paper-summary"><div class="empty">Loading…</div></div>
  </div>

  <!-- Backtest tab -->
  <div class="tab-content section" id="tab-backtest">
    <div class="section-title">Run Backtest</div>
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px;margin-bottom:12px">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
        <div>
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">Days of history</div>
          <input id="bt-days" type="number" value="90" min="30" max="500"
            style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text);font-size:14px">
        </div>
        <div>
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">Capital (USD)</div>
          <input id="bt-capital" type="number" value="10000"
            style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text);font-size:14px">
        </div>
        <div>
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">SL multiplier (ATR×)</div>
          <input id="bt-sl" type="number" value="1.5" step="0.1"
            style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text);font-size:14px">
        </div>
        <div>
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">TP multiplier (ATR×)</div>
          <input id="bt-tp" type="number" value="2.5" step="0.1"
            style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text);font-size:14px">
        </div>
      </div>
      <button onclick="runBacktest()"
        style="width:100%;background:var(--gold);color:#000;font-weight:700;border:none;border-radius:8px;padding:10px;font-size:14px;cursor:pointer">
        ▶ Run Backtest
      </button>
    </div>
    <div id="backtest-result"><div class="empty">Configure and run a backtest above.</div></div>
  </div>

  <footer>XAUUSD Bot · MT5 Manual Execution · <span id="footer-time"></span></footer>
</div>

<script>
const API = '';   // same origin

async function fetchJSON(path) {
  const r = await fetch(API + path);
  return r.json();
}

// ── Price ──────────────────────────────────────────────────────────────────
async function loadPrice() {
  try {
    const d = await fetchJSON('/api/price');
    document.getElementById('current-price').textContent = d.price.toFixed(2);
    const t = new Date(d.fetched_at).toLocaleTimeString();
    document.getElementById('price-time').textContent = (d.cached ? '⚠️ Cached · ' : '') + t;
  } catch {
    document.getElementById('current-price').textContent = 'Error';
  }
}

// ── Stats ──────────────────────────────────────────────────────────────────
async function loadStats() {
  try {
    const d = await fetchJSON('/api/stats');
    document.getElementById('stat-signals').textContent  = d.total_signals;
    document.getElementById('stat-winrate').textContent  = d.win_rate + '%';
    const pnlEl  = document.getElementById('stat-pnl');
    pnlEl.textContent   = (d.total_pnl >= 0 ? '+' : '') + d.total_pnl.toFixed(2);
    pnlEl.className     = 'value ' + (d.total_pnl >= 0 ? 'pnl-pos' : 'pnl-neg');
    document.getElementById('stat-trades').textContent   = d.closed_trades;
  } catch {}
}

// ── Signals ────────────────────────────────────────────────────────────────
function pillClass(a) { return a === 'buy' ? 'pill-buy' : a === 'sell' ? 'pill-sell' : 'pill-hold'; }

async function loadSignals() {
  const el = document.getElementById('signals-list');
  try {
    const d = await fetchJSON('/api/signals?limit=30');
    if (!d.signals.length) { el.innerHTML = '<div class="empty">No signals yet.</div>'; return; }
    el.innerHTML = d.signals.map(s => {
      const ind  = s.indicators || {};
      const time = new Date(s.timestamp).toLocaleString();
      const chips = Object.entries(ind)
        .filter(([k]) => ['rsi','ema_fast','ema_slow','atr','confidence_pct'].includes(k))
        .map(([k, v]) => `<span class="ind-chip">${k}: ${typeof v === 'number' ? v.toFixed(2) : v}</span>`)
        .join('');
      return `
        <div class="signal-item" style="flex-direction:column;align-items:flex-start">
          <div style="display:flex;justify-content:space-between;width:100%">
            <div>
              <span class="pill ${pillClass(s.action)}">${s.action.toUpperCase()}</span>
              <span class="signal-action" style="margin-left:8px">#${s.id}</span>
              <div class="signal-meta">${s.strategy || ''} · ${time}</div>
            </div>
            <div class="signal-price">
              <div class="p">${s.price.toFixed(2)}</div>
              <div class="t">${s.volume} lot</div>
            </div>
          </div>
          ${chips ? `<div class="ind-row">${chips}</div>` : ''}
        </div>`;
    }).join('');
  } catch(e) { el.innerHTML = `<div class="empty">Error: ${e.message}</div>`; }
}

// ── Trades ─────────────────────────────────────────────────────────────────
async function loadTrades() {
  const el = document.getElementById('trades-list');
  try {
    const d = await fetchJSON('/api/trades?limit=30');
    if (!d.trades.length) { el.innerHTML = '<div class="empty">No trades yet.</div>'; return; }
    el.innerHTML = d.trades.map(t => {
      const pnl  = t.pnl != null ? ((t.pnl >= 0 ? '+' : '') + t.pnl.toFixed(2)) : '—';
      const cls  = t.pnl > 0 ? 'pnl-pos' : t.pnl < 0 ? 'pnl-neg' : '';
      const time = t.opened_at ? new Date(t.opened_at).toLocaleDateString() : '—';
      return `
        <div class="signal-item">
          <div>
            <span class="pill ${pillClass(t.action)}">${t.action.toUpperCase()}</span>
            <div class="signal-meta">${t.symbol} · ${t.volume} lot · ${time}</div>
            <div class="signal-meta">Open: ${t.open_price?.toFixed(2) || '—'} → Close: ${t.close_price?.toFixed(2) || '—'}</div>
          </div>
          <div class="signal-price">
            <div class="p ${cls}">${pnl}</div>
            <div class="t">${t.status}</div>
          </div>
        </div>`;
    }).join('');
  } catch(e) { el.innerHTML = `<div class="empty">Error: ${e.message}</div>`; }
}

// ── Paper Trading ──────────────────────────────────────────────────────────
async function loadPaper() {
  const el = document.getElementById('paper-summary');
  try {
    const d = await fetchJSON('/api/paper');
    const pnlCls = d.total_pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
    const uCls   = d.unrealised_pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
    let html = `
      <div class="stats-grid" style="margin-bottom:12px">
        <div class="stat-card"><div class="label">Capital</div><div class="value gold">$${d.current_capital.toLocaleString()}</div></div>
        <div class="stat-card"><div class="label">Total P&L</div><div class="value ${pnlCls}">${d.total_pnl >= 0 ? '+' : ''}$${d.total_pnl.toFixed(2)}</div></div>
        <div class="stat-card"><div class="label">Unrealised</div><div class="value ${uCls}">${d.unrealised_pnl >= 0 ? '+' : ''}$${d.unrealised_pnl.toFixed(2)}</div></div>
        <div class="stat-card"><div class="label">Win Rate</div><div class="value green">${d.win_rate}%</div></div>
      </div>`;
    if (d.open_trade) {
      const ot  = d.open_trade;
      const dir = ot.direction === 1 ? '🟢 BUY' : '🔴 SELL';
      const pc  = ot.pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
      html += `<div class="signal-item" style="margin-bottom:12px">
        <div><div style="font-weight:700">${dir} (open)</div>
        <div class="signal-meta">Entry: ${ot.entry_price.toFixed(2)} · ${ot.entry_date.slice(0,16)}</div></div>
        <div class="signal-price"><div class="p ${pc}">${ot.pnl >= 0 ? '+' : ''}$${ot.pnl.toFixed(2)}</div><div class="t">unrealised</div></div>
      </div>`;
    }
    html += '<div class="section-title" style="margin-top:4px">Recent Paper Trades</div>';
    if (!d.recent_trades.length) { html += '<div class="empty">No closed trades yet.</div>'; }
    else {
      d.recent_trades.forEach(t => {
        const dir = t.direction === 1 ? '🟢 BUY' : '🔴 SELL';
        const pc  = t.pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
        html += `<div class="signal-item">
          <div><div style="font-weight:600">${dir}</div>
          <div class="signal-meta">${t.entry_price.toFixed(2)} → ${t.exit_price?.toFixed(2) || '—'}</div></div>
          <div class="signal-price"><div class="p ${pc}">${t.pnl >= 0 ? '+' : ''}$${t.pnl.toFixed(2)}</div></div>
        </div>`;
      });
    }
    el.innerHTML = html;
  } catch(e) { el.innerHTML = `<div class="empty">Error: ${e.message}</div>`; }
}

// ── Backtest ────────────────────────────────────────────────────────────────
async function runBacktest() {
  const el  = document.getElementById('backtest-result');
  const btn = document.querySelector('#tab-backtest button');
  btn.textContent = '⏳ Running…';
  btn.disabled    = true;
  el.innerHTML    = '<div class="empty">Fetching historical data and running simulation…</div>';
  try {
    const resp = await fetch('/api/backtest', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        days:    parseInt(document.getElementById('bt-days').value),
        capital: parseFloat(document.getElementById('bt-capital').value),
        sl:      parseFloat(document.getElementById('bt-sl').value),
        tp:      parseFloat(document.getElementById('bt-tp').value),
        lot:     0.1
      })
    });
    const r = await resp.json();
    if (!resp.ok) { el.innerHTML = `<div class="empty">Error: ${r.detail}</div>`; return; }

    const pnlCls = r.total_pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
    const bhCls  = r.buy_hold_pct >= 0 ? 'pnl-pos' : 'pnl-neg';

    // Equity sparkline
    const eq   = r.equity_curve;
    const minE = Math.min(...eq), maxE = Math.max(...eq);
    const pts  = eq.map((v, i) => {
      const x = (i / (eq.length - 1)) * 280;
      const y = 50 - ((v - minE) / (maxE - minE + 1)) * 45;
      return `${x},${y}`;
    }).join(' ');
    const lineColor = eq[eq.length-1] >= eq[0] ? '#22c55e' : '#ef4444';

    el.innerHTML = `
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px;margin-bottom:10px">
        <div style="font-size:11px;color:var(--muted);margin-bottom:8px">${r.start_date} → ${r.end_date} · ${r.total_bars} bars · ${r.strategy}</div>
        <svg viewBox="0 0 280 55" style="width:100%;height:55px;margin-bottom:10px">
          <polyline points="${pts}" fill="none" stroke="${lineColor}" stroke-width="1.5"/>
        </svg>
        <div class="stats-grid">
          <div class="stat-card"><div class="label">Total P&L</div><div class="value ${pnlCls}">${r.total_pnl >= 0 ? '+' : ''}$${r.total_pnl.toFixed(2)}</div></div>
          <div class="stat-card"><div class="label">Win Rate</div><div class="value green">${r.win_rate}%</div></div>
          <div class="stat-card"><div class="label">Max Drawdown</div><div class="value red">-${r.max_drawdown_pct}%</div></div>
          <div class="stat-card"><div class="label">Sharpe</div><div class="value gold">${r.sharpe_ratio}</div></div>
          <div class="stat-card"><div class="label">Profit Factor</div><div class="value gold">${r.profit_factor}</div></div>
          <div class="stat-card"><div class="label">Buy & Hold</div><div class="value ${bhCls}">${r.buy_hold_pct >= 0 ? '+' : ''}${r.buy_hold_pct}%</div></div>
        </div>
        <div style="margin-top:10px;font-size:12px;color:var(--muted)">
          ${r.total_trades} trades · ${r.winning_trades}W / ${r.losing_trades}L · 
          Avg win $${r.avg_win} · Avg loss $${r.avg_loss}
        </div>
      </div>
      <div class="section-title">Last Trades</div>
      ${(r.trades || []).slice(-10).reverse().map(t => {
        const dir = t.direction === 1 ? '🟢 BUY' : '🔴 SELL';
        const pc  = t.pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
        return `<div class="signal-item">
          <div><div style="font-weight:600">${dir}</div>
          <div class="signal-meta">${t.entry_date} · ${t.entry_price.toFixed(2)} → ${t.exit_price.toFixed(2)}</div></div>
          <div class="signal-price"><div class="p ${pc}">${t.pnl >= 0 ? '+' : ''}$${t.pnl.toFixed(2)}</div></div>
        </div>`;
      }).join('')}
    `;
  } catch(e) { el.innerHTML = `<div class="empty">Error: ${e.message}</div>`; }
  finally { btn.textContent = '▶ Run Backtest'; btn.disabled = false; }
}

// ── Tabs ───────────────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  const tabs = ['signals','trades','paper','backtest'];
  const idx  = tabs.indexOf(name);
  document.querySelectorAll('.tab')[idx].classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'signals')  loadSignals();
  else if (name === 'trades')   loadTrades();
  else if (name === 'paper')    loadPaper();
}

// ── Init & auto-refresh ────────────────────────────────────────────────────
function init() {
  document.getElementById('footer-time').textContent = new Date().toLocaleString();
  loadPrice();
  loadStats();
  loadSignals();
  // Auto-refresh every 60 seconds
  setInterval(() => { loadPrice(); loadStats(); }, 60000);
}

document.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_alias():
    return HTMLResponse(content=DASHBOARD_HTML)


# ── Run directly ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    from config import DASHBOARD_HOST, DASHBOARD_PORT
    uvicorn.run("dashboard:app", host=DASHBOARD_HOST, port=DASHBOARD_PORT,
                reload=False, workers=1)

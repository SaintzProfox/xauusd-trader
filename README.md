# XAUUSD Semi-Automated Trading System
### Oracle Cloud VPS + Telegram + MT5 Mobile

---

## Architecture Overview

```
Oracle Cloud Free VPS (Linux x86)
├── signals/generator.py   ← Scheduler: runs every 15 min
│   ├── data_fetcher.py    ← Pulls OHLCV from yfinance (free)
│   ├── strategy.py        ← MA Crossover + RSI logic
│   └── notifier.py        ← Telegram bot push notifications
├── dashboard.py           ← FastAPI web UI (port 8000)
├── db/database.py         ← SQLite persistence
└── config.py              ← All settings in one place
                                      │
                              Telegram Bot API
                                      │
                              📱 Your Phone
                              ├── Telegram app  ← receives signal
                              └── MT5 Mobile    ← manual execution
```

**Signal flow:**
1. VPS fetches gold price data every 15 min
2. Strategy computes indicators → generates BUY/SELL/HOLD
3. Signal saved to SQLite database
4. Telegram bot sends formatted alert to your phone
5. You open MT5 Mobile → place trade manually

---

## Quick Start

### Step 1 – Get Your Oracle Cloud Free VPS

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com) (free tier)
2. Create an **Always Free** Compute instance:
   - Shape: `VM.Standard.E2.1.Micro` (free) or `VM.Standard.A1.Flex`
   - OS: **Ubuntu 22.04 LTS** (recommended)
3. Download the SSH key pair, note your **Public IP**
4. In your VCN → Security List → Add **Ingress Rule**:
   - Source CIDR: `0.0.0.0/0`
   - Protocol: TCP, Port: `8000`

### Step 2 – SSH into your VPS

```bash
ssh -i your-key.pem ubuntu@YOUR_VPS_IP
```

### Step 3 – Upload and run setup

```bash
# On your VPS
git clone https://github.com/YOUR_REPO/xauusd-trader.git  # or sftp upload
cd xauusd-trader
chmod +x setup_vps.sh
./setup_vps.sh
```

### Step 4 – Create your Telegram Bot

1. Open Telegram → search **@BotFather** → `/newbot`
2. Choose a name (e.g. `MyGoldTrader`) and username (e.g. `mygoldtrader_bot`)
3. Copy the **HTTP API token** (looks like `123456789:ABCdef…`)
4. Send a message to your new bot (any text)
5. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates`
6. Find `"chat":{"id": 987654321}` – that's your Chat ID

### Step 5 – Configure environment

```bash
nano ~/xauusd-trader/.env
```

Fill in:
```env
TELEGRAM_BOT_TOKEN=123456789:ABCdef_your_token_here
TELEGRAM_CHAT_ID=987654321
DATA_PROVIDER=yfinance
```

### Step 6 – Start services

```bash
sudo systemctl enable --now xauusd-generator
sudo systemctl enable --now xauusd-dashboard
```

### Step 7 – Access dashboard on your phone

Open your phone browser: `http://YOUR_VPS_IP:8000`

---

## Configuration Reference (`config.py`)

| Setting | Default | Description |
|---|---|---|
| `STRATEGY` | `combined` | `ma_crossover`, `rsi`, or `combined` |
| `MA_FAST` | `9` | Fast EMA period |
| `MA_SLOW` | `21` | Slow EMA period |
| `RSI_PERIOD` | `14` | RSI lookback |
| `RSI_OVERBOUGHT` | `70` | RSI sell threshold |
| `RSI_OVERSOLD` | `30` | RSI buy threshold |
| `DEFAULT_VOLUME` | `0.1` | Lot size shown in signals |
| `SIGNAL_INTERVAL_MINUTES` | `15` | How often to scan |
| `DATA_PROVIDER` | `yfinance` | Free data source |

---

## Strategy Details

### Combined (Recommended)
Requires **both** conditions to generate a signal:
- **BUY**: Fast EMA crosses above Slow EMA **AND** RSI < 70
- **SELL**: Fast EMA crosses below Slow EMA **AND** RSI > 30
- Includes a `confidence_pct` score (higher = stronger signal)

### MA Crossover Only
- **BUY**: EMA(9) crosses above EMA(21)
- **SELL**: EMA(9) crosses below EMA(21)

### RSI Only
- **BUY**: RSI bounces up from below 30
- **SELL**: RSI turns down from above 70

---

## Signal JSON Format

```json
{
    "symbol":    "XAUUSD",
    "action":    "buy",
    "volume":    0.1,
    "price":     2345.80,
    "strategy":  "combined",
    "indicators": {
        "ema_fast":       2343.10,
        "ema_slow":       2338.60,
        "rsi":            42.5,
        "atr":            8.3,
        "ma_cross":       "bullish",
        "trend":          "bullish",
        "confidence_pct": 68.2
    },
    "timestamp": "2026-03-13T10:00:00Z"
}
```

---

## MT5 Mobile Execution (Manual)

When you receive a Telegram alert:

1. Open **MetaTrader 5** on your phone
2. Tap **Trade** tab → **+** (New Order)
3. Select `XAUUSD`
4. Set **Volume** to the signal's lot size (e.g. 0.10)
5. Set **Order Type**: Market Execution
6. Tap **Buy** or **Sell** as directed
7. Confirm the trade

> ⚠️ Always use your own risk management. Set Stop Loss and Take Profit on MT5 based on the ATR value in the signal (1–2× ATR for SL, 2–3× ATR for TP is a common starting point).

---

## Telegram Bot Commands

Send these to your bot in Telegram:

| Command | Response |
|---|---|
| `/status` | Current price + bot health |
| `/signals` | Last 5 signals |
| `/stats` | P&L summary + win rate |
| `/help` | Command list |

---

## Web Dashboard API

| Endpoint | Description |
|---|---|
| `GET /` | Mobile dashboard UI |
| `GET /api/price` | Latest XAUUSD price |
| `GET /api/signals?limit=50` | Recent signals |
| `GET /api/trades?status=closed` | Trade history |
| `GET /api/stats` | Performance stats |
| `POST /api/trades/{id}/close` | Mark trade closed |

---

## Managing Services

```bash
# Status
sudo systemctl status xauusd-generator
sudo systemctl status xauusd-dashboard

# Live logs
sudo journalctl -u xauusd-generator -f
sudo journalctl -u xauusd-dashboard -f

# Restart after config change
sudo systemctl restart xauusd-generator

# View SQLite database
sqlite3 ~/xauusd-trader/db/trading.db
.tables
SELECT * FROM signals ORDER BY id DESC LIMIT 5;
.quit
```

---

## Free Data Provider Notes

| Provider | Key Required | Rate Limit | Gold Symbol |
|---|---|---|---|
| **yfinance** | No | ~2000/day | `GC=F` (Gold Futures) |
| Alpha Vantage | Yes (free) | 25/day | `XAU/USD` |
| Twelve Data | Yes (free) | 800/day | `XAU/USD` |

yfinance (`GC=F`) uses **Gold Futures** as a proxy for XAUUSD spot. Prices are very close but may differ by $1–5. For live trading, use your MT5 price as the actual reference.

---

## Security Recommendations

1. **Change `SECRET_KEY`** in `.env` to a random 32+ char string
2. **Restrict dashboard access**: Add nginx with basic auth or change port to something non-standard
3. **Never expose your `.env`** – it contains your Telegram token
4. **Set up SSH key auth only** – disable password SSH login:
   ```bash
   sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
   sudo systemctl restart sshd
   ```

---

## Troubleshooting

| Issue | Solution |
|---|---|
| No Telegram messages | Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`; ensure you messaged the bot first |
| `yfinance` errors | Try `DATA_PROVIDER=alphavantage` with a free key |
| Dashboard not loading | Check `sudo ufw allow 8000/tcp` and Oracle VCN security list |
| Duplicate signals | Normal — same action is suppressed; only direction changes generate alerts |
| Market closed | yfinance returns no data on weekends; bot logs a warning and retries |

---

## Disclaimer

This system is for **educational and informational purposes only**. Gold (XAUUSD) trading involves substantial risk. Always:
- Use strict risk management (never risk more than 1–2% per trade)
- Verify signals on your MT5 chart before executing
- Paper trade first before using real funds
- Consult a licensed financial advisor

*Past strategy performance does not guarantee future results.*

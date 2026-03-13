#!/usr/bin/env bash
# install_services.sh
# Run this from /home/ubuntu/xauusd-trader to install systemd services.
# Usage: bash install_services.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$PROJECT_DIR/venv"
USER="$(whoami)"

echo "[1/4] Project dir: $PROJECT_DIR"
echo "[1/4] Running as:  $USER"

# ── Create venv if missing ────────────────────────────────────────────────────
if [ ! -f "$VENV/bin/python" ]; then
    echo "[2/4] Creating virtual environment…"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip -q
    "$VENV/bin/pip" install -r "$PROJECT_DIR/requirements.txt" -q
    echo "[2/4] Dependencies installed."
else
    echo "[2/4] Virtual environment already exists — skipping."
fi

# ── Write service files ───────────────────────────────────────────────────────
echo "[3/4] Writing systemd service files…"

sudo tee /etc/systemd/system/xauusd-generator.service > /dev/null <<EOF
[Unit]
Description=XAUUSD Signal Generator
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$VENV/bin/python -m signals.generator
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal
SyslogIdentifier=xauusd-generator

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/xauusd-dashboard.service > /dev/null <<EOF
[Unit]
Description=XAUUSD Web Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$VENV/bin/python $PROJECT_DIR/dashboard.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=xauusd-dashboard

[Install]
WantedBy=multi-user.target
EOF

# ── Enable and start ──────────────────────────────────────────────────────────
echo "[4/4] Enabling and starting services…"
sudo systemctl daemon-reload
sudo systemctl enable --now xauusd-generator
sudo systemctl enable --now xauusd-dashboard

echo ""
echo "══════════════════════════════════════════════"
echo "  Done! Service status:"
echo "══════════════════════════════════════════════"
sudo systemctl status xauusd-generator --no-pager -l
echo "──────────────────────────────────────────────"
sudo systemctl status xauusd-dashboard --no-pager -l
echo ""
echo "Live logs:"
echo "  sudo journalctl -u xauusd-generator -f"
echo "  sudo journalctl -u xauusd-dashboard -f"

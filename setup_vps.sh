#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# setup_vps.sh  –  XAUUSD Trading Bot VPS Setup Script
# Tested on: Oracle Cloud Free Tier (Oracle Linux 8 / Ubuntu 22.04)
#
# Usage:
#   chmod +x setup_vps.sh
#   ./setup_vps.sh
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

PROJECT_DIR="$HOME/xauusd-trader"

# ── Detect OS ────────────────────────────────────────────────────────────────
if command -v apt-get &>/dev/null; then
    OS="ubuntu"
elif command -v dnf &>/dev/null; then
    OS="oracle"
else
    error "Unsupported OS. Install Python 3.11+ manually."; exit 1
fi

# ── 1. System packages ────────────────────────────────────────────────────────
info "Installing system dependencies (OS: $OS)…"
if [ "$OS" = "ubuntu" ]; then
    sudo apt-get update -y
    sudo apt-get install -y python3.11 python3.11-venv python3.11-dev \
        python3-pip git curl wget unzip sqlite3 ufw
else
    sudo dnf install -y python3.11 python3.11-devel gcc git curl wget \
        sqlite sqlite-devel firewalld
    sudo systemctl enable --now firewalld
fi

# ── 2. Firewall ───────────────────────────────────────────────────────────────
info "Configuring firewall…"
if [ "$OS" = "ubuntu" ]; then
    sudo ufw allow OpenSSH
    sudo ufw allow 8000/tcp comment "XAUUSD Dashboard"
    sudo ufw --force enable
else
    sudo firewall-cmd --permanent --add-service=ssh
    sudo firewall-cmd --permanent --add-port=8000/tcp
    sudo firewall-cmd --reload
fi
info "Port 8000 opened for dashboard access."

# ── 3. Project directory ──────────────────────────────────────────────────────
info "Setting up project at $PROJECT_DIR…"
mkdir -p "$PROJECT_DIR"/{db,logs,signals,static}

# Copy files if this script is run from the project root
if [ -f "requirements.txt" ]; then
    cp -r . "$PROJECT_DIR/" 2>/dev/null || true
fi

# ── 4. Python virtual environment ─────────────────────────────────────────────
info "Creating Python virtual environment…"
python3.11 -m venv "$PROJECT_DIR/venv"
source "$PROJECT_DIR/venv/bin/activate"

info "Installing Python packages…"
pip install --upgrade pip wheel setuptools
pip install -r "$PROJECT_DIR/requirements.txt"

# ── 5. .env configuration ─────────────────────────────────────────────────────
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    warn "Created .env from template. Edit it now:"
    warn "  nano $PROJECT_DIR/.env"
    warn ""
    warn "  Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before starting!"
fi

# ── 6. Systemd services ───────────────────────────────────────────────────────
info "Installing systemd services…"

# Generator service
sudo tee /etc/systemd/system/xauusd-generator.service > /dev/null <<EOF
[Unit]
Description=XAUUSD Signal Generator
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/venv/bin/python -m signals.generator
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal
SyslogIdentifier=xauusd-generator

[Install]
WantedBy=multi-user.target
EOF

# Dashboard service
sudo tee /etc/systemd/system/xauusd-dashboard.service > /dev/null <<EOF
[Unit]
Description=XAUUSD Web Dashboard
After=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/venv/bin/python dashboard.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=xauusd-dashboard

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload

info "Systemd services created."
info ""
info "══════════════════════════════════════════════════════"
info "  SETUP COMPLETE – Next Steps"
info "══════════════════════════════════════════════════════"
info ""
info "1. Edit your .env file:"
info "   nano $PROJECT_DIR/.env"
info ""
info "2. Start services:"
info "   sudo systemctl enable --now xauusd-generator"
info "   sudo systemctl enable --now xauusd-dashboard"
info ""
info "3. View logs:"
info "   sudo journalctl -u xauusd-generator -f"
info "   sudo journalctl -u xauusd-dashboard -f"
info ""
info "4. Access dashboard in your phone browser:"
info "   http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_VPS_IP'):8000"
info ""
warn "Remember: Oracle Cloud also requires an Ingress Rule in your"
warn "VCN security list to allow TCP port 8000 from 0.0.0.0/0."
info "══════════════════════════════════════════════════════"

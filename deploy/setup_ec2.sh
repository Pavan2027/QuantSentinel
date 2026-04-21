#!/bin/bash
# deploy/setup_ec2.sh
# Run this once on a fresh Ubuntu 22.04 EC2 instance
# Usage: bash deploy/setup_ec2.sh

set -e  # exit on any error

echo "======================================"
echo " QuantSentinel EC2 Setup"
echo "======================================"

# --- System packages ---
echo "[1/7] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3-pip python3-venv git curl \
    build-essential libssl-dev libffi-dev

# --- Clone repo (skip if already cloned) ---
if [ ! -d "/home/ubuntu/QuantSentinel" ]; then
    echo "[2/7] Cloning repository..."
    echo "Enter your GitHub repo URL (e.g. https://github.com/USER/QuantSentinel.git):"
    read REPO_URL
    git clone "$REPO_URL" /home/ubuntu/QuantSentinel
else
    echo "[2/7] Repository already exists — pulling latest..."
    cd /home/ubuntu/QuantSentinel && git pull
fi

cd /home/ubuntu/QuantSentinel

# --- Virtual environment ---
echo "[3/7] Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# --- Install dependencies ---
echo "[4/7] Installing Python dependencies (this takes 3-5 minutes)..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install flask -q    # for webhook server

# --- Environment file ---
echo "[5/7] Setting up environment..."
if [ ! -f ".env" ]; then
    echo "Creating .env file — please fill in your credentials:"
    cat > .env << 'EOF'
# QuantSentinel Environment — FILL IN YOUR VALUES

# Paper/Live trading
LIVE_TRADING=false
PAPER_TRADING_CAPITAL=100000

# Upstox (required for live trading)
UPSTOX_API_KEY=
UPSTOX_API_SECRET=
UPSTOX_SANDBOX=false

# News APIs
NEWS_API_KEY=
NEWSDATA_API_KEY=
NEWS_REFRESH_HOURS=6

# Telegram alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Scheduler
CYCLE_INTERVAL_HOURS=2
EOF
    echo "⚠️  Please edit .env now: nano .env"
    echo "Press Enter when done..."
    read
else
    echo "   .env already exists — skipping"
fi

# --- systemd services ---
echo "[6/7] Installing systemd services..."
sudo cp deploy/quantsentinel.service     /etc/systemd/system/
sudo cp deploy/quantsentinel-ui.service  /etc/systemd/system/
sudo cp deploy/quantsentinel-webhook.service /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable quantsentinel
sudo systemctl enable quantsentinel-ui
sudo systemctl enable quantsentinel-webhook

# --- Initialize database ---
echo "[7/7] Initializing database..."
source venv/bin/activate
python -c "from utils.db import init_db; init_db(); print('DB initialized')"

echo ""
echo "======================================"
echo " Setup Complete!"
echo "======================================"
echo ""
echo "Start all services:"
echo "  sudo systemctl start quantsentinel-webhook"
echo "  sudo systemctl start quantsentinel"
echo "  sudo systemctl start quantsentinel-ui"
echo ""
echo "Check status:"
echo "  sudo systemctl status quantsentinel"
echo "  sudo journalctl -u quantsentinel -f"
echo ""
echo "Dashboard URL: http://$(curl -s ifconfig.me):8501"
echo ""
echo "IMPORTANT: Before live trading:"
echo "  1. Set LIVE_TRADING=true in .env"
echo "  2. Run: python main.py --auth  (each morning)"
echo "  3. Update Upstox webhook URL to: http://$(curl -s ifconfig.me):5000/webhook/token"
echo ""
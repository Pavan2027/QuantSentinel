# QuantSentinel

> An autonomous, AI-driven algorithmic trading bot built for the Indian stock market (NSE). Continuously analyzes live news sentiment, calculates technical indicators, and autonomously executes mathematically rigorous trades.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?style=flat-square&logo=streamlit)
![HuggingFace](https://img.shields.io/badge/FinBERT-HuggingFace-F9AB00?style=flat-square&logo=huggingface)
![Upstox](https://img.shields.io/badge/Upstox-V3_API-6200EA?style=flat-square)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat-square&logo=sqlite)

---

## Overview

QuantSentinel is a fully autonomous trading engine that combines **Natural Language Processing (NLP)** and **Quantitative Technical Analysis** to remove human emotion from trading. It scans the top 250 NSE stocks, evaluates their real-time financial news sentiment using `FinBERT`, confirms the trend using complex mathematical momentum indicators, and mathematically sizes portfolio entries to maximize alpha while heavily restricting risk.

It features a built-in Paper Trader engine identical to the live production client, allowing safe testing before flipping a single switch to execute real trades via the Upstox V3 API.

---

## Features

### Core Modules
| Module | Description |
|---|---|
| **Sentiment Analysis Engine** | Uses a local HuggingFace `FinBERT` model to quantify bearish/bullish sentiment from real-time Yahoo Finance / Marketaux news feeds. |
| **Technical Analysis Engine** | Calculates EMA crossovers, MACD, RSI, ATR volatility, and Volume spikes to mathematically confirm momentum. |
| **Dynamic Risk Manager** | Continuously evaluates the portfolio. Shifts between `GREEN`, `YELLOW`, and `RED` risk states, dynamically tightening stop losses when the market crashes. |
| **Signal & Execution Engine** | Generates perfect `BUY`/`SELL`/`HOLD` signals. Autonomously handles Hard Stop-Losses, Take-Profit targets, and Trailing Stops. |
| **Live Upstox API V3 Client** | Executes identical API calls across the virtual paper trader and live market broker. Handles daily token negotiations via built-in Webhook. |
| **Streamlit UI Dashboard** | Beautiful frontend to monitor open positions, bot PnL, activity logs, and manually trigger emergency overrides. |

### Architecture Highlights
- **100% Autonomous Scheduler** — Runs 24/7 as an Ubuntu `systemd` service via `APScheduler`. Evaluates the market every 30 minutes between 9:15 AM and 3:00 PM.
- **Tightly Coupled SQLite State** — Every single tick, signal, cash balance, and control flag is accurately persisted locally in `bot.db`. Zero ghost trades.
- **Automated Morning Authentication** — Uses `crontab` to ping the Upstox API at 8:45 AM, sending a push notification to your phone to approve the day's trades. 
- **Modern UI** — The dashboard uses asynchronous `@st.fragment` architecture to live-refresh metrics without reloading the browser.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend Logic | Python 3.10+ |
| Machine Learning | `transformers` (HuggingFace), `torch`, `FinBERT` |
| Technical Analysis | `pandas`, `numpy`, `pandas_ta` |
| Database | SQLite (Local) |
| Frontend Dashboard | Streamlit 1.35+ (`st.fragment`) |
| Auth & Webhook | Flask, `pyngrok` / `cloudflared` |
| Broker API | `upstox-python-sdk` |

---

## Project Structure

```
QuantSentinel/
├── config/
│   ├── settings.py           # Core variables, DB paths, global toggles
│   └── market_calendar.py    # IST timezone handling & NSE holiday tracking
├── data/
│   └── news_provider.py      # Universal news aggregator (yfinance / RSS / Marketaux)
├── deploy/
│   ├── refresh_token.py      # Autocalled at 8:45 AM to ping user's WhatsApp
│   └── setup_ec2.sh          # Server bootstrapping script
├── execution/
│   ├── paper_trader.py       # Simulated exchange matching the live client
│   ├── upstox_auth.py        # Token negotiation via V3 Upstox endpoints 
│   └── upstox_client.py      # Live broker API wrapper
├── features/
│   ├── technicals.py         # Advanced momentum indicator calculations
│   └── corporate_actions.py  # Filters out bad stock split data
├── risk/
│   ├── exposure_limits.py    # Enforces 20% max pos limits & sector diversification
│   └── risk_manager.py       # Dynamically adjusts states based on PnL drawdown
├── scheduler/
│   └── job_runner.py         # APScheduler chron jobs
├── strategy/
│   ├── scoring.py            # Mathematical weights dictating BUY/SELL rules
│   ├── signal_engine.py      # Trailing stop algorithms
│   └── universe.py           # NSE top 250 stock list definition
├── ui/
│   └── dashboard.py          # Streamlit single-page application
├── utils/
│   ├── db.py                 # SQLite initialization & schemas
│   └── notifier.py           # Error alerts
├── requirements.txt          # Python dependencies
├── main.py                   # Application Entry-Point
└── webhook_server.py         # Flask server catching Upstox tokens
```

---

## Getting Started

### Prerequisites

- Python 3.10+
- An Upstox Brokerage Account (for live trading)
- AWS EC2 Ubuntu Server (Optional, but recommended for 24/7 uptime)

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/QuantSentinel.git
cd QuantSentinel
```

### 2. Environment Setup

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies (Downloads large ML models, may take a few minutes)
pip install -r requirements.txt
```

### 3. Configuration

Copy `.env.example` to `.env` and configure your keys:

```env
# Trading Mode (Set FALSE to use simulated paper money initially)
LIVE_TRADING=false
PAPER_TRADING_CAPITAL=100000

# Upstox Developer API (Needed for UPSTOX_CLIENT)
UPSTOX_API_KEY="your-key"
UPSTOX_API_SECRET="your-secret"
UPSTOX_REDIRECT_URI="http://localhost:5000/webhook/token"
```

### 4. Running the Bot

There are two primary ways to run QuantSentinel depending on your goals:

**A. Full Autonomous Mode**
Starts the daemon that automatically runs trades on a schedule.
```bash
python main.py
```

**B. Monitoring Dashboard Mode**
Starts the Streamlit UI to monitor logs and metrics manually:
```bash
python main.py --ui-only

# Or directly:
streamlit run ui/dashboard.py
```

---

## Daily Operations Workflow (Live Trading)

Because Indian regulations strictly forbid autonomous headless logins, the bot uses an elegant daily handshake to stay legal:

1. At `08:45 AM` IST, run `python deploy/refresh_token.py` (or automate this via `crontab`).
2. The bot sends a live token request to Upstox.
3. You will receive an immediate push notification on your phone via WhatsApp/Upstox.
4. Simply tap **"Approve"**.
5. Your background `webhook_server.py` securely captures the token and saves it to `.db`.
6. At `09:15 AM`, the bot wakes up, hits the live market, and runs completely silently until 3:30 PM.

---

## License

This project is proprietary algorithmic trading software. Use at your own risk. Past performance is not indicative of future results. 

---

<p align="center">Built with Python + Streamlit · 2026</p>

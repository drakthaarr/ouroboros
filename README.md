
# 🐉 Ouroboros: Quantitative Pairs Trading Bot

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![CCXT](https://img.shields.io/badge/ccxt-latest-green.svg)
![Pandas](https://img.shields.io/badge/pandas-data_analysis-orange.svg)
![Status](https://img.shields.io/badge/status-live_on_vps-success.svg)

![Logo Name](ouroboros/assets/Logo.png)



**Ouroboros** is an automated, fully autonomous algorithmic trading bot designed for the Binance Spot market. It implements a **Statistical Arbitrage (Pairs Trading)** strategy using mean-reversion mathematics on highly correlated cryptocurrency pairs (e.g., BTC/USDT and ETH/USDT).

This project was built with a strong focus on **software engineering best practices**: modular architecture, robust state management, rate-limit handling, and decoupled quantitative analysis.

---

## ⚙️ Core Architecture & Engineering Highlights

Unlike basic trading scripts, Ouroboros is structured as an enterprise-grade micro-application:

* **Stateless Quantitative Engine (`strategy.py`)**: The math module computes the price spread and rolling **Z-score** using `pandas` and `numpy`. It is completely decoupled from execution logic, making it 100% unit-testable.
* **Deterministic State Machine (`main.py`)**: Order execution is governed by a strict state machine (`NONE`, `HOLDING_BTC`, `HOLDING_ETH`). This mathematically prevents "double-buying" or overlapping orders, even during API timeouts or network blips.
* **Spot-Market Pseudo-Pairs Logic**: Since traditional shorting is unavailable on Spot markets, the bot simulates pairs exposure by longing the undervalued asset relative to the spread, automatically closing positions upon mean reversion.
* **Dry Run (Paper Trading) Mode**: A built-in fail-safe that uses live market data (OHLCV) to calculate real Z-scores but simulates order execution locally to test logic without risking capital.
* **Asynchronous-like Event Loop & Rate Limiting**: Implements smart polling intervals and CCXT's native rate-limiter to prevent exchange IP bans.
* **Production Logging & Alerting**: Utilizes rotating file handlers (`logs/bot.log`) for deep audit trails and integrates a lightweight Telegram push-notification module (`notifier.py`) for live execution alerts.

## 📂 Project Structure

```text
ouroboros/
├── pyproject.toml       # Poetry dependency management
├── poetry.lock          # Deterministic builds
├── .env.example         # Environment variables template
├── config.py            # Centralized configuration & secrets loader
├── exchange.py          # CCXT Binance wrapper with error handling
├── strategy.py          # Math, Pandas DataFrames, and Z-score logic
├── notifier.py          # Telegram HTTP API integration
├── main.py              # Orchestrator, State Machine, and Event Loop
└── logs/                # Rotating runtime logs
```

📊 The Mathematics (Mean Reversion)

    Spread Calculation: Evaluates the ratio between the base asset (BTC) and quote asset (ETH).

    Rolling Z-Score: Normalizes the spread using a 20-period rolling mean and standard deviation.

    Signal Generation:

        Z-score > 2.0: Spread is abnormally wide (BTC overvalued vs ETH) → Buy ETH.

        Z-score < -2.0: Spread is abnormally narrow (BTC undervalued vs ETH) → Buy BTC.

        Z-score crosses 0: Spread reverted to the mean → Close Positions (Take Profit).

🚀 Deployment Guide

This bot is designed to run 24/7 on a Linux VPS using tmux or systemd.
1. Prerequisites

Ensure you have Python 3.11+ and Poetry installed.
Bash

git clone [https://github.com/yourusername/ouroboros.git](https://github.com/yourusername/ouroboros.git)
cd ouroboros
poetry install

2. Configuration

Copy the environment template and add your API keys:
Bash

cp .env.example .env
nano .env

(Ensure BINANCE_API_KEY and BINANCE_API_SECRET are set. Add Telegram tokens for push notifications).
3. Execution

Start the bot in a detached terminal session:
Bash

tmux new -s ouroboros_bot
poetry run python main.py

⚠️ Disclaimer

This software is for educational purposes and portfolio demonstration only. Cryptocurrency trading carries significant financial risk. Do not trade with capital you cannot afford to lose. The author is not responsible for any financial losses incurred using this codebase.

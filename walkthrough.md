# Walkthrough - Advanced Trading Bot Features

## Overview
In this session, we significantly upgraded the `SpiderTradingBot` from a basic Martingale bot to a smart, semi-autonomous trading system with advanced risk management and AI capabilities.

## Completed Features

### 1. Multi-Timeframe Analysis 📊
- **Logic:** Combines **4H Trend** (EMA50/EMA200) with **15M Entry** (RSI).
- **Benefit:** Prevents trading against the major trend.
- **Visual:** `/smart` command now shows "Macro Trend" and "Micro RSI".

### 2. Dynamic Risk Management (ATR) 🛡️
- **Stop Loss:** Calculated as `2 * ATR` (Average True Range).
- **Take Profit:** Calculated as `4 * ATR` (Risk/Reward 1:2).
- **Benefit:** Adapts stops to market volatility (wider stops in volatile markets, tighter in stable ones).

### 3. Chandelier Exit (Trailing Stop) 🕯️
- **Logic:** Updates Stop Loss as price moves in favor.
- **Formula:** `Highest High - (2.5 * ATR)` for Longs.
- **Benefit:** Locks in profits automatically.

### 4. AI Price Prediction 🤖
- **Model:** Linear Regression on last 50 candles (4H).
- **Function:** Predicts next candle close.
- **Usage:** Acts as a confirmation signal (e.g., "AI confirms BULLISH").

## Phase 1: Central Risk Engine (Completed)
Successfully implemented and integrated a centralized Risk Engine.

### Key Changes:
- **[NEW] [risk_engine.py](file:///c:/trade/me/ok/risk_engine.py)**: A standalone module for global risk validation.
- **[MODIFY] [spider_trading_bot.py](file:///c:/trade/me/ok/spider_trading_bot.py)**: Integrated risk checks into all trading commands (`/spot`, `/future`, `/smart`, `/snipe`) and initialization flow.
- **[MODIFY] [spider_strategy.py](file:///c:/trade/me/ok/spider_strategy.py)**: Added mandatory risk validation before automated order placement.
- **[MODIFY] [gln_strategy.py](file:///c:/trade/me/ok/gln_strategy.py)**: Added risk validation for GLN signals.

### Verification Results:
- ✅ **Kill-Switch**: Trades are blocked immediately when `kill_switch_enabled` is set to `True` in the database.
- ✅ **Leverage Cap**: Futures trades with leverage exceeding `global_max_leverage` are blocked.
- ✅ **Min Amount**: Trades below $5 are rejected with a Persian error message.
- ✅ **Global Gatekeeper**: Both manual and automated trades are now protected by the same risk rules.

### 5. Pyramiding Strategy (Smart Add) 🏗️
- **Logic:** Instead of closing on Take Profit, the bot adds volume.
- **Trigger:** When PnL >= 1.5%.
- **Action:** Buys same amount again, moves Stop Loss to Break-Even.
- **Benefit:** Compounds profit in strong trends while securing principal.

### 6. Performance Dashboard 📈
- **Command:** `/dashboard`
- **Stats:** Total PnL, Win Rate, Active Positions.
- **Database:** Stores trade history in SQLite for persistent tracking.

## Usage Guide
1.  **Start Trading:**
    ```
    /smart BTCUSDT 100 5
    ```
    (Analyzes 4H trend, 15M RSI, checks AI prediction, and enters if valid).

2.  **Check Performance:**
    ```
    /dashboard
    ```
    (See your profit/loss and win rate).

## Next Steps (Phase 3 Continued)
-   **Correlation Trading:** Trade pairs together (e.g. ETH follows BTC).
-   **News Sentiment:** Filter trades based on market mood (FUD/Hype).

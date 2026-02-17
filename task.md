# Adaptive Trading Bot Task List

- [x] **Design & Architecture**
    - [x] Define `MarketAnalyzer` class for technical analysis (EMA, RSI, ATR) <!-- id: 0 -->
    - [x] Define strategy logic for different market conditions (Trend vs Range) <!-- id: 1 -->
    - [x] Update `implementation_plan.md` with the new design <!-- id: 2 -->

- [x] **Core Implementation**
    - [x] Install/Import `pandas` and `pandas_ta` (or implement basic indicators with `numpy` to keep it light) <!-- id: 3 -->
    - [x] Implement `MarketAnalyzer` to fetch OHLCV and calculate indicators <!-- id: 4 -->
    - [x] Implement `SmartStrategy` class that inherits from or uses `SpiderStrategy` <!-- id: 5 -->

- [x] **Strategy Logic Implementation**
    - [x] **Trend Detection:** uses EMA 50 & 200 + RSI to detect Uptrend/Downtrend/Range <!-- id: 6 -->
    - [x] **Entry Logic:**
        - [x] Uptrend: Buy signal on RSI dip or EMA crossover <!-- id: 7 -->
        - [x] Downtrend: Sell signal (for Futures) on RSI spike <!-- id: 8 -->
        - [x] Range: Enable Martingale/Grid (Current Spider Logic) <!-- id: 9 -->

- [ ] **Phase 2: Advanced Features (Priority: Database)**
    - [x] **State Persistence (SQLite):** Save active trades to recover after restart <!-- id: 20 -->
    - [x] **Multi-Timeframe Analysis:** Check 4h (Trend) + 15m (Entry) for confirmation <!-- id: 13 -->
    - [x] **Dynamic Risk Management (ATR):** Adjust Stop-Loss/TP based on volatility <!-- id: 14 -->
    - [ ] **Volume Confirmation:** Use Volume to validate trend strength <!-- id: 15 -->
    - [x] **Trailing Stop (Chandelier Exit):** Implement advanced trailing stop logic <!-- id: 16 -->
    - [ ] **Pyramiding Strategy:** Add volume on profit & lock stop loss (User Request) <!-- id: 22 -->

- [ ] **Phase 3: Ultra Advanced (AI & Correlation)**
    - [x] **AI Price Prediction:** Simple Linear Regression to predict next candle close <!-- id: 17 -->
    - [ ] **Correlation Trading:** Trade pairs together (e.g. ETH follows BTC) <!-- id: 18 -->
    - [x] **Performance Dashboard:** Visual PnL and trade history via /dashboard <!-- id: 21 -->
    - [x] **Deployment Automation:** Create scripts to deploy to VPS (Windows/Linux support) <!-- id: 23 -->
    - [ ] **News/Sentiment Analysis:** (Optional) Filter trades based on market mood <!-- id: 19 -->

- [x] **Integration & Testing**
    - [x] Integrate `SmartStrategy` into `TradingBot` class <!-- id: 10 -->
    - [x] Add `/smart` command to Telegram bot <!-- id: 11 -->
    - [x] Test with live/paper market data <!-- id: 12 -->

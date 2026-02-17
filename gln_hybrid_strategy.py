import asyncio
import logging
from datetime import datetime, time
import pytz
from market_analyzer import MarketAnalyzer
from risk_engine import TradeRequest
from execution_engine import ExecutionEngine, ExecutionResult

logger = logging.getLogger(__name__)

class GLNHybridStrategy:
    def __init__(self, execution_engine: ExecutionEngine, symbol, initial_investment, market_type='future', leverage=1, db_manager=None, strategy_id=None, message_callback=None, position_tracker=None):
        self.execution_engine = execution_engine
        self.position_tracker = position_tracker
        self.symbol = symbol
        self.initial_investment = initial_investment
        self.market_type = market_type
        self.leverage = leverage
        self.db_manager = db_manager
        self.strategy_id = strategy_id
        self.message_callback = message_callback
        
        self.side = None # Hybrid side is dynamic
        self.running = True
        self.auto_mode = True # /auto_on /auto_off
        
        # Q-Channel State
        self.q_high = 0.0
        self.q_low = float('inf')
        self.is_q_locked = False
        self.candle_count = 0
        
        # Indicators
        self.atr_value = 0
        self.ema9 = 0
        self.ema20 = 0
        
        # Position Management (Spider Mode)
        self.positions = []
        self.current_sl = 0
        self.entry_score = 0
        self.last_sync_time = 0
        
        # Daily Reset Tracker
        self.last_reset_date = None # type: ignore
        self.user_id = 0 # Default for logs

    def save_state(self):
        if self.db_manager and self.strategy_id:
            state = {
                'positions': self.positions,
                'q_high': self.q_high,
                'q_low': self.q_low,
                'is_q_locked': self.is_q_locked,
                'candle_count': self.candle_count,
                'atr_value': self.atr_value,
                'ema9': self.ema9,
                'ema20': self.ema20,
                'current_sl': self.current_sl,
                'entry_score': self.entry_score,
                'auto_mode': self.auto_mode,
                'last_reset_date': self.last_reset_date.isoformat() if self.last_reset_date else None,
                'type': 'hybrid'
            }
            self.db_manager.save_strategy(
                self.strategy_id, self.symbol, self.market_type, 'hybrid', 
                self.initial_investment, self.leverage, state
            )

    async def initialize(self):
        logger.info(f"GLN Hybrid: Initializing for {self.symbol}")
        await self.send_notification(f"🏛 **موتور هوشمند GLN HYBRID فعال شد**\nنماد: `{self.symbol}`\nحالت: Auto={'✅' if self.auto_mode else '❌'}")
        
    def get_ny_time(self):
        ny_tz = pytz.timezone('America/New_York')
        return datetime.now(ny_tz)

    async def check_reset(self):
        """Resets daily levels at 09:25 NY."""
        now_ny = self.get_ny_time()
        today = now_ny.date()
        
        if self.last_reset_date != today:
            reset_time = time(9, 25)
            if now_ny.time() >= reset_time:
                logger.info(f"GLN Hybrid: Daily Reset triggered at {now_ny}")
                self.q_high = 0.0
                self.q_low = float('inf')
                self.is_q_locked = False
                self.candle_count = 0
                self.last_reset_date = today
                await self.send_notification("♻️ **GLN Hybrid: ریست روزانه انجام شد (09:25 NY)**\nدر انتظار شروع مارکت...")

    async def calculate_score(self, current_price):
        """
        MODULE 3 — ENTRY SCORE (0–100)
        Q Breakout: 30
        EMA/HMA: 25
        MACD: 20
        ATR: 15
        Correlation: 10
        """
        score = 0
        details = []
        
        analyzer = MarketAnalyzer(self.execution_engine, self.symbol)
        
        try:
            # 1. Q-Breakout (30 pts)
            if self.is_q_locked:
                if current_price > self.q_high or current_price < self.q_low:
                    score += 30
                    details.append(f"Q-Break: +30")
            
            # 2. EMA/HMA Alignment (25 pts)
            ohlcv = await self.execution_engine.fetch_market_data(self.symbol, data_type='ohlcv', timeframe='5m', limit=100)
            if ohlcv:
                closes = [c[4] for c in ohlcv]
                ema9 = analyzer.calculate_ema(closes, 9)
                ema20 = analyzer.calculate_ema(closes, 20)
                hma = analyzer.calculate_hma(closes, 20)
                
                self.ema9 = ema9
                self.ema20 = ema20
                
                # Check alignment with breakout direction
                if current_price > self.q_high: # Trying for LONG
                    if ema9 > ema20 and (hma and current_price > hma):
                        score += 25
                        details.append(f"EMA/HMA Bullish: +25")
                elif current_price < self.q_low: # Trying for SHORT
                    if ema9 < ema20 and (hma and current_price < hma):
                        score += 25
                        details.append(f"EMA/HMA Bearish: +25")

            # 3. MACD (20 pts)
            macd, sig, hist = analyzer.calculate_macd(closes)
            if macd is not None and sig is not None:
                 if current_price > self.q_high and macd > sig:
                     score += 20
                     details.append(f"MACD Bullish: +20")
                 elif current_price < self.q_low and macd < sig:
                     score += 20
                     details.append(f"MACD Bearish: +20")

            # 4. ATR Volatility (15 pts)
            # Higher score if ATR is expanding or above avg? 
            # Simple check: ATR > 0 confirms volatility
            highs = [c[2] for c in ohlcv]
            lows = [c[3] for c in ohlcv]
            atr = analyzer.calculate_atr(highs, lows, closes)
            self.atr_value = atr
            if atr > 0:
                score += 15
                details.append(f"ATR Vol: +15")
            
            # 5. Correlation (10 pts)
            # Placeholder for BTC/NASDAQ correlation
            score += 10 # Default for now
            details.append(f"Corr: +10")

        except Exception as e:
            logger.error(f"Scoring Error: {e}")
            
        self.entry_score = score
        return score, details

    async def check_market(self):
        if not self.running: return
        
        try:
            await self.check_reset()
            
            now_ny = self.get_ny_time()
            market_open = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
            
            if now_ny < market_open: return
            
            # Module 1: Time Engine
            diff_mins = (now_ny - market_open).total_seconds() / 60
            candle_num = int(diff_mins // 5) + 1
            
            ticker = await self.execution_engine.fetch_market_data(self.symbol)
            if not ticker: return
            current_price = ticker['last']
            
            # Track Channel (C1 - C18)
            if 1 <= candle_num <= 18:
                if current_price > self.q_high: self.q_high = current_price
                if current_price < self.q_low: self.q_low = current_price
                
                # Report milestones
                if candle_num != self.candle_count:
                    self.candle_count = candle_num
                    if candle_num in [1, 7, 12, 18]:
                        await self.report_candle(candle_num, current_price)
                        if candle_num == 18:
                            self.is_q_locked = True
            
            # Execution Phase
            if self.is_q_locked:
                if not self.positions:
                     # Check Entry
                     cp = float(current_price)
                     qh = float(self.q_high)
                     ql = float(self.q_low)
                     if cp > qh or cp < ql:
                         score, details = await self.calculate_score(cp)
                         side = 'buy' if cp > qh else 'sell'
                         
                         if score >= 70:
                             if self.auto_mode:
                                 await self.execute_trade(side, score, details)
                             else:
                                 await self.send_notification(f"🚨 **فرصت معاملاتی شناسایی شد (Score: {score})**\nحالت خودکار خاموش است. از دکمه‌های پنل استفاده کنید.")
                else:
                    # Manage Position (Spider Risk System)
                    if self.ema9 > 0 and self.ema20 > 0:
                        await self.manage_position(current_price)

        except Exception as e:
            logger.error(f"GLN Hybrid Loop Error: {e}")

    async def execute_trade(self, side, score, details):
        """Module 4 & 5: Execution."""
        logger.info(f"GLN Hybrid: Executing {side} with score {score}")
        
        ticker = await self.execution_engine.fetch_market_data(self.symbol)
        price = ticker['last']
        
        # Initial SL = 1.5 ATR
        sl_dist = 1.5 * self.atr_value if self.atr_value > 0 else price * 0.01
        sl_price = price - sl_dist if side == 'buy' else price + sl_dist
        
        req = TradeRequest(
            symbol=self.symbol,
            amount=self.initial_investment,
            leverage=self.leverage,
            side=side,
            market_type=self.market_type,
            user_id=0
        )
        
        res = await self.execution_engine.execute(req)
        
        if res.success:
            self.current_sl = sl_price
            self.positions.append({
                'entry': price,
                'amount': self.initial_investment, # USDT
                'type': 'initial'
            })
            
            msg = (
                f"🚀 **ترید GLN Hybrid باز شد**\n"
                f"سمت: `{'LONG' if side == 'buy' else 'SHORT'}`\n"
                f"امتیاز: `{score}/100`\n"
                f"قیمت: `{price}`\n"
                f"حد ضرر: `{sl_price:.2f}`\n\n"
                f"📑 جزئیات: {', '.join(details)}"
            )
            await self.send_notification(msg)
        else:
            logger.error(f"GLN Hybrid: Execution failed: {res.message}")

    async def manage_position(self, current_price):
        """Module 4: Spider Risk Management."""
        side = 'buy' if self.positions[0]['entry'] < self.current_sl else 'sell' # Logic simplified
        if self.positions[0].get('side'): side = self.positions[0]['side'] # Better track side
        
        # Real side detection from first entry logic
        initial_side = 'buy' if self.current_sl < self.positions[0]['entry'] else 'sell'
        
        # 1. Exit on EMA Cross (EMA9 cross EMA20)
        if initial_side == 'buy' and self.ema9 < self.ema20:
            await self.close_position("EMA Cross (9 < 20)")
            return
        elif initial_side == 'sell' and self.ema9 > self.ema20:
            await self.close_position("EMA Cross (9 > 20)")
            return
            
        # 2. Add on Pullback (EMA9/20)
        # Only add once for simplicity in this version
        if len(self.positions) == 1:
            pullback_hit = False
            if initial_side == 'buy' and current_price <= self.ema9 and current_price > self.ema20:
                pullback_hit = True
            elif initial_side == 'sell' and current_price >= self.ema9 and current_price < self.ema20:
                pullback_hit = True
                
            if pullback_hit:
                 # SPIDER MODE: Add on pullback
                 logger.info("GLN Hybrid: Pullback detected. Adding to position.")
                 await self.place_spider_order(initial_side, current_price)

        # 3. Check SL
        if (initial_side == 'buy' and current_price <= self.current_sl) or \
           (initial_side == 'sell' and current_price >= self.current_sl):
            await self.close_position("Stop Loss Hit")

    async def place_spider_order(self, side, price):
        req = TradeRequest(
            symbol=self.symbol,
            amount=self.initial_investment,
            leverage=self.leverage,
            side=side,
            market_type=self.market_type,
            user_id=0
        )
        res = await self.execution_engine.execute(req)
        if res.success:
            self.positions.append({'entry': price, 'amount': self.initial_investment, 'type': 'pullback'})
            # Move SL to Entry
            self.current_sl = self.positions[0]['entry']
            await self.send_notification("🧗 **پله دوم اضافه شد (EMA Pullback)**\nحد ضرر به نقطه ورود منتقل شد (Risk Free).")

    async def close_position(self, reason):
        # Implementation for closing all via ExecutionEngine
        await self.execution_engine.close_position(self.symbol, self.market_type)
        self.positions = []
        await self.send_notification(f"🏁 **پوزیشن هیبرید بسته شد**\nدلیل: {reason}")

    async def report_candle(self, num, current_price):
        gap = abs(current_price - self.q_high) # Logic for gap can be improved
        msg = (
            f"📅 **گزارش Q-Timer (کندل {num})**\n"
            f"قیمت: `{current_price}`\n"
            f"سقف کانال: `{self.q_high}`\n"
            f"کف کانال: `{self.q_low}`\n"
            f"وضعیت: {'🔒 قفل شد' if num == 18 else '⏳ در حال تشکیل'}"
        )
        await self.send_notification(msg)

    async def send_notification(self, msg):
        if self.message_callback:
            await self.message_callback(msg)

    async def get_status(self):
        return (
            f"🏛 **وضعیت GLN Hybrid**\n"
            f"نماد: `{self.symbol}`\n"
            f"کندل: `{self.candle_count}/18`\n"
            f"امتیاز فعلی: `{self.entry_score}`\n"
            f"وضعیت Q: {'Locked' if self.is_q_locked else 'Tracking'}\n"
            f"پوزیشن: {'Active' if self.positions else 'None'}\n"
            f"Auto: {'On' if self.auto_mode else 'Off'}"
        )

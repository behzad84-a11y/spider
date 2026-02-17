import asyncio
import logging
from datetime import datetime, timedelta, time
import pytz

logger = logging.getLogger(__name__)

# Helper for sync CCXT
async def async_run(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)

from market_analyzer import MarketAnalyzer
from risk_engine import TradeRequest
from execution_engine import ExecutionEngine, ExecutionResult

class GLNStrategy:
    def __init__(self, execution_engine: ExecutionEngine, symbol, initial_investment, side, market_type='future', leverage=1, db_manager=None, strategy_id=None, message_callback=None, position_tracker=None, scanner_registry=None, event_reporter=None):
        self.execution_engine = execution_engine
        self.position_tracker = position_tracker
        
        self.symbol = symbol
        self.initial_investment = initial_investment
        self.side = side.lower() if side else 'long' # Default to long, but GLN is dynamic
        self.market_type = market_type
        self.leverage = leverage
        self.db_manager = db_manager
        self.strategy_id = strategy_id
        self.message_callback = message_callback
        self.scanner_registry = scanner_registry  # For registry integration
        self.event_reporter = event_reporter  # For event reporting
        
        # State
        self.running = True
        self.positions = []
        
        # GLN Specifics
        self.pdh = 0.0 # Previous Day High
        self.pdl = 0.0 # Previous Day Low
        self.pdc = 0.0 # Previous Day Close (Body)
        self.today_open = 0.0
        
        self.gap_filled = False
        self.trend_direction = None # 'bullish' (unfilled gap up), 'bearish' (unfilled gap down), 'range' (filled)
        
        # Q-Timer (18 Candle Rule)
        self.ny_open_utc = None # Will be calculated
        self.candle_count = 0
        self.q_high = 0.0
        self.q_low = float('inf')
        self.is_q_channel_set = False
        self.atr_value = 0 # Average True Range
        self.signal_sent = False # Anti-flooding flag
        self.last_signal_side = None # For Fakeout
        self.q_probability = 0 # Official Q-Timer Probability (20-90%)
        
        # Daily Reset Tracking
        self.last_reset_date = None  # Date of last daily reset
        self.daily_reset_logged = False  # Flag to prevent duplicate dashboard logs
        
        # Risk Config
        self.stop_loss_percent = 0.005 # Tight SL (0.5%) - Placeholder, will be dynamic
        self.take_profit_percent = 0.015 # Target next level
        
    async def initialize(self):
        """Calculates daily levels and syncs time. Implements late-start resume logic."""
        # Load saved state first (if exists)
        self.load_state()
        
        await self.sync_with_exchange()
        await self.calculate_daily_levels()
        
        # RESUME LOGIC: Compute correct candle index based on current NY session time
        now_ny = self.get_ny_time()
        today = now_ny.date()
        
        # Check if daily reset needed (09:25 NY)
        reset_time, open_time, session_end = self.get_session_bounds(today)
        if self.last_reset_date != today and now_ny.time() >= reset_time.time():
            await self.perform_daily_reset(today)
            # After reset, we're in PREOPEN state
            state, candle_index, minutes = self.compute_candle_index(now_ny)
            status_msg = await self.format_status_snapshot(state, candle_index, minutes, now_ny)
            await self.send_notification(status_msg)
            self.save_state()
            return
        
        # Compute current session state and candle index
        state, candle_index, minutes_into_session = self.compute_candle_index(now_ny)
        
        if state == 'PREOPEN':
            # Before 09:30 NY - set to waiting state
            self.candle_count = 0
            self.q_probability = 0
            status_msg = await self.format_status_snapshot(state, 0, 0, now_ny)
            await self.send_notification(status_msg)
        elif state == 'TRACKING':
            # During Q-window (candles 1-18) - resume from correct candle
            self.candle_count = candle_index
            
            # Sync Q-high/Q-low from historical bars if not already set
            if self.q_high == 0.0 or self.q_low == float('inf'):
                logger.info(f"Resuming QGLN at candle {candle_index}/18. Syncing Q-channel from historical data...")
                await self.sync_from_recent_bars(open_time, max_candles=candle_index)
            
            # Update probability based on current candle
            if candle_index >= 18:
                self.q_probability = 90
            elif candle_index >= 12:
                self.q_probability = 70
            elif candle_index >= 7:
                self.q_probability = 50
            elif candle_index >= 1:
                self.q_probability = 20
            
            status_msg = await self.format_status_snapshot(state, candle_index, minutes_into_session, now_ny)
            await self.send_notification(status_msg)
        elif state == 'POST_QWINDOW':
            # After candle 18 but still in session - channel should be locked
            self.candle_count = 18
            
            # Sync Q-high/Q-low if not already set
            if self.q_high == 0.0 or self.q_low == float('inf'):
                logger.info("Q-window finished. Syncing Q-channel from first 18 candles...")
                await self.sync_from_recent_bars(open_time, max_candles=18)
            
            if not self.is_q_channel_set:
                self.is_q_channel_set = True
            
            self.q_probability = 90
            status_msg = await self.format_status_snapshot(state, 18, minutes_into_session, now_ny)
            await self.send_notification(status_msg)
        elif state == 'POSTSESSION':
            # After session end - keep last state
            self.candle_count = 18
            if not self.is_q_channel_set and (self.q_high > 0 or self.q_low < float('inf')):
                self.is_q_channel_set = True
            status_msg = await self.format_status_snapshot(state, 18, minutes_into_session, now_ny)
            await self.send_notification(status_msg)
        
        # Update registry with current state
        ticker = await self.execution_engine.fetch_market_data(self.symbol)
        current_price = ticker['last'] if ticker else 0
        self.update_registry_state(self.candle_count, current_price)
        
        # Save state after initialization
        self.save_state()
        
        # Send initial notification (legacy format for compatibility)
        await self.send_notification(f"🚀 **موتور GLN روشن شد!**\nنماد: {self.symbol}\nاهرم: {self.leverage}x")

    async def calculate_daily_levels(self):
        """Fetches daily OHLCV to find PDH, PDL, PDC."""
        try:
            # Fetch daily candles
            ohlcv = await self.execution_engine.fetch_market_data(self.symbol, data_type='ohlcv', timeframe='1d', limit=5)
            if not ohlcv:
                logger.error("No daily data fetched.")
                return

            # Get yesterday's candle (index -2, as -1 is today's forming candle)
            yesterday = ohlcv[-2]
            
            # Note: CCXT ohlcv format: [timestamp, open, high, low, close, volume]
            self.pdh = yesterday[2]
            self.pdl = yesterday[3]
            self.pdc = yesterday[4] # Standard Close. For "Body Close", we might need to compare Open/Close? 
            # User Key Level 3 says: "Specifically the Close of the candle body"
            # Usually 'Close' IS the body close. Wick top is High.
            
            self.today_open = ohlcv[-1][1]
            
            # Gap Calculation
            gap_percent = ((self.today_open - self.pdc) / self.pdc) * 100
            
            msg = (
                f"📊 **تحلیل روزانه GLN**\n"
                f"سقف دیروز (PDH): {self.pdh}\n"
                f"کف دیروز (PDL): {self.pdl}\n"
                f"بسته دیروز (PDC): {self.pdc}\n"
                f"باز شدن امروز: {self.today_open}\n"
                f"گپ: {gap_percent:.2f}%"
            )
            logger.info(msg)
            await self.send_notification(msg)
            
        except Exception as e:
            logger.error(f"Error calculating daily levels: {e}")
            await self.send_notification(f"❌ خطا در محاسبه سطوح روزانه: {e}")

    def get_ny_time(self):
        """Get current NY time (DST-safe)."""
        ny_tz = pytz.timezone('America/New_York')
        return datetime.now(ny_tz)
    
    def get_market_open_ny(self, date=None):
        """
        Get NY market open time (09:30) for a specific date (DST-safe).
        If date is None, uses today's date.
        """
        ny_tz = pytz.timezone('America/New_York')
        if date is None:
            now_ny = datetime.now(ny_tz)
            date = now_ny.date()
        
        # Create datetime at 09:30 for the given date in NY timezone (DST-safe)
        market_open = ny_tz.localize(datetime.combine(date, time(9, 30)))
        return market_open
    
    def get_session_bounds(self, ny_date=None):
        """
        Get session bounds for a given NY date.
        Returns: (reset_time, open_time, session_end_time) as timezone-aware datetimes.
        """
        ny_tz = pytz.timezone('America/New_York')
        if ny_date is None:
            now_ny = datetime.now(ny_tz)
            ny_date = now_ny.date()
        
        reset_time = ny_tz.localize(datetime.combine(ny_date, time(9, 25)))
        open_time = ny_tz.localize(datetime.combine(ny_date, time(9, 30)))
        session_end = ny_tz.localize(datetime.combine(ny_date, time(16, 0)))
        
        return reset_time, open_time, session_end
    
    def compute_candle_index(self, ny_now=None):
        """
        Compute current candle index in Q-window (1-18) based on NY session time.
        Returns: (state, candle_index, minutes_into_session)
        state: 'PREOPEN', 'TRACKING', 'POST_QWINDOW', 'POSTSESSION'
        candle_index: 0 if PREOPEN, 1-18 if TRACKING, 18 if POST_QWINDOW
        minutes_into_session: minutes since 09:30 NY (0 if PREOPEN)
        """
        if ny_now is None:
            ny_now = self.get_ny_time()
        
        today = ny_now.date()
        reset_time, open_time, session_end = self.get_session_bounds(today)
        
        # Check if before reset (previous day still active)
        if ny_now < reset_time:
            return ('PREOPEN', 0, 0)
        
        # Check if before market open
        if ny_now < open_time:
            return ('PREOPEN', 0, 0)
        
        # Check if after session end
        if ny_now >= session_end:
            return ('POSTSESSION', 18, int((session_end - open_time).total_seconds() / 60))
        
        # During session: compute candle index
        minutes_into_session = int((ny_now - open_time).total_seconds() / 60)
        candle_index = (minutes_into_session // 5) + 1
        
        # Clamp to Q-window (1-18)
        if candle_index <= 18:
            return ('TRACKING', candle_index, minutes_into_session)
        else:
            # After Q-window but still in session
            return ('POST_QWINDOW', 18, minutes_into_session)
    
    def is_trading_day(self):
        """Checks if today is Monday (0) to Friday (4) in NY timezone."""
        now_ny = self.get_ny_time()
        weekday = now_ny.weekday()
        return weekday < 5 # 0=Mon, 4=Fri, 5=Sat, 6=Sun

    async def check_market(self):
        """Main loop: Checks Q-Timer, Gap, and Signals."""
        if not self.running:
            return

        # 0. Workday Filter
        if not self.is_trading_day():
            if self.candle_count % 60 == 0: # Log once an hour
                logger.info("Weekend detected. GLN Strategy sleeping.")
            return

        # 0.5. Guard Check (Fail-safe)
        if self.db_manager:
            guard = self.db_manager.get_guard_status('GLN_Q')
            if not guard['is_enabled']:
                if self.candle_count % 30 == 0:
                    logger.warning(f"GLN Guard is ACTIVE. Strategy {self.symbol} is paused until {guard.get('disabled_until')}.")
                return

        try:
            # 1. Update Current Price and Time
            ticker = await self.execution_engine.fetch_market_data(self.symbol)
            current_price = ticker['last'] if ticker else 0
            now = datetime.now(pytz.utc)
            
            # 2. Daily Reset Check (09:25 NY - before market open)
            now_ny = self.get_ny_time()
            today = now_ny.date()
            
            if self.last_reset_date != today:
                reset_time = time(9, 25)
                if now_ny.time() >= reset_time:
                    await self.perform_daily_reset(today)
            
            # 3. Logic: 18th Candle Timer (Dynamic NY Time - DST-safe)
            market_open_ny = self.get_market_open_ny(today)
            
            if now_ny < market_open_ny:
                # Before Market Open
                time_elapsed = -1
            else:
                time_elapsed = (now_ny - market_open_ny).total_seconds() / 60 # minutes
            
            
            if time_elapsed > 0:
                current_candle_num = int(time_elapsed // 5) + 1
                self.candle_count = current_candle_num
                
                # Update Registry with Candle# and Q-Channel State
                self.update_registry_state(current_candle_num, current_price)
                
                if current_candle_num <= 18:
                    # Tracking Phase
                    if current_price > self.q_high:
                        self.q_high = current_price
                    if current_price < self.q_low:
                        self.q_low = current_price
                        
                    if current_candle_num == 1:
                        self.q_probability = 20
                        logger.info(f"Q-Timer Candle 1: 20% Probability. Tracking...")
                    elif current_candle_num == 7:
                        self.q_probability = 50
                        await self.check_gap(current_price, current_candle_num)
                        logger.info(f"Q-Timer Candle 7: 50% Probability. Gap Check performed.")
                    elif current_candle_num == 12:
                        self.q_probability = 70
                        logger.info(f"Q-Timer Candle 12: 70% Probability.")
                    elif current_candle_num == 18:
                        self.q_probability = 90
                        await self.check_gap(current_price, current_candle_num)
                        logger.info(f"Q-Timer Candle 18: 90% Probability. Q-Channel Lock imminent.")
                        
                elif current_candle_num > 18:
                    # If we just crossed 18, OR if we started late and missed it
                    if not self.is_q_channel_set:
                        # 🚨 LATE START RECOVERY 🚨
                        # If q_high is 0, it means we weren't tracking during the first 18 candles.
                        # We must fetch historical data.
                        if self.q_high == 0 or self.q_low == float('inf'):
                            logger.info("Bot started late. Fetching historical Q-Channel data...")
                            await self.sync_from_recent_bars(market_open_ny, max_candles=18)

                        self.is_q_channel_set = True
                        msg = (
                            f"🎰 **کانال Q ثبت شد (کندل ۱۸)**\n"
                            f"سقف (QH): {self.q_high}\n"
                            f"کف (QL): {self.q_low}\n"
                            f"منتظر شکست (Breakout) یا برگشت (Reversal)..."
                        )
                        await self.send_notification(msg)
                
                # 3. Execution Phase (After Candle 18)
                if self.is_q_channel_set:
                    await self.check_signals(current_price)

        except Exception as e:
            logger.error(f"GLN Loop Error: {e}")

    async def check_gap(self, current_price, candle_num):
        """Checks if the daily gap has been filled."""
        if self.gap_filled:
            return

        # Check if price touched PDC
        # Simple proximity check
        dist = abs(current_price - self.pdc) / self.pdc
        if dist < 0.0005: # 0.05% tolerance
            self.gap_filled = True
            gap_status = "FILLED"
            await self.send_notification(f"✅ **گپ پر شد!** (در کندل {candle_num})\nاستراتژی: رنج / بازگشت (Reversal)")
            
            # Report Gap Status to Event Reporter (report() takes event_type, data dict only)
            if self.event_reporter:
                await self.event_reporter.report(
                    'QGLN',
                    {'symbol': self.symbol, 'detail': f"Gap Filled at Candle {candle_num}", 'candle': candle_num, 'price': current_price, 'pdc': self.pdc, 'status': 'FILLED'}
                )
        else:
            gap_status = "OPEN"
            if candle_num == 18:
                await self.send_notification(f"⚠️ **گپ پر نشد!** (پایان کندل ۱۸)\nاستراتژی: روند قدرتمند (Trend) در جهت گپ")
                
                # Report Gap Status to Event Reporter (report() takes event_type, data dict only)
                if self.event_reporter:
                    await self.event_reporter.report(
                        'QGLN',
                        {'symbol': self.symbol, 'detail': "Gap Open at Candle 18", 'candle': 18, 'price': current_price, 'pdc': self.pdc, 'status': 'OPEN'}
                    )
        
        # Update Registry with Gap Status
        if self.scanner_registry:
            self.scanner_registry.update('QGLN', gap_status=gap_status, gap_filled=self.gap_filled)

    async def check_correlation(self, side):
        """Checks US100/BTC correlation for crypto trades."""
        if "BTC" not in self.symbol.upper():
            return True # Logic only applied to BTC
        
        try:
            # Fetch US100 (Nasdaq) data if possible, or skip
            # This is a master spec requirement
            logger.info(f"Checking correlation for {self.symbol}...")
            # Placeholder for future MT5/Crypto cross-check if Nasdaq is available
            return True 
        except Exception as e:
            logger.error(f"Correlation check failed: {e}")
            return True

    async def check_signals(self, current_price):
        """Looks for entries based on Q Channel breakout or reversal."""
        if self.positions or self.signal_sent:
            # Hysteresis Buffer: Reset ONLY if price moves deeply back into the channel
            # We use 10% of the channel width as a buffer, with a minimum of 0.1% of the price
            channel_width = self.q_high - self.q_low
            buffer = max(channel_width * 0.1, current_price * 0.001)
            
            # --- FAKEOUT LOGIC ---
            if self.signal_sent and not self.positions:
                 # If price is DEEP inside channel (Fakeout)
                 if (self.q_low + buffer) < current_price < (self.q_high - buffer):
                     # If we are here, it means we have active position OR signal_sent is True.
                     
                     if self.positions:
                         # Check if position is failing?
                         last_pos = self.positions[-1]
                         if last_pos['side'] == self.last_signal_side:
                             # We are in a trade, and price went back deep.
                             # This is likely a stop loss hit in real life.
                             # Trigger Reversal
                             logger.warning(f"⚠️ Fakeout Detected on {self.symbol} (Crypto)! Reversing...")
                             
                             # Close current (Clean up list)
                             self.positions.pop() 
                             
                             # Reverse
                             reverse_side = 'sell' if self.last_signal_side == 'buy' else 'buy'
                             self.signal_sent = False
                             self.last_signal_side = None
                             await self.place_order(reverse_side, "Fakeout Reversal")
                             return

            if (self.q_low + buffer) < current_price < (self.q_high - buffer):
                if self.signal_sent:
                    logger.info(f"Price returned deep into Q-Channel (Buffer: {buffer}). Resetting signal gate for {self.symbol}.")
                    self.signal_sent = False
                    self.last_signal_side = None # Reset side too
            return 
            
        if current_price > self.q_high:
            # Long Breakout
            await self.place_order('buy', "Breakout Q-High")
        elif current_price < self.q_low:
            # Short Breakout
            await self.place_order('sell', "Breakout Q-Low")

    async def place_order(self, side, reason):
        """Places a trade via ExecutionEngine and sets SL/TP."""
        
        current_price = 0
        try:
             ticker = await self.execution_engine.fetch_market_data(self.symbol)
             current_price = ticker['last'] if ticker else 0
        except:
             current_price = self.q_high if side == 'buy' else self.q_low

        # 1. Execution Engine (Includes Risk Engine Gatekeeper)
        margin = self.initial_investment
        leverage = self.leverage
        req = TradeRequest(
            symbol=self.symbol,
            amount=margin,
            leverage=leverage,
            side=side,
            market_type=self.market_type,
            user_id=0 # TODO: Pass real user_id
        )
        
        res = await self.execution_engine.execute(req)
        
        if not res.success:
            logger.warning(f"🚫 GLN Signal blocked for {self.symbol}: {res.message}")
            if "Risk Engine" in res.message:
                await self.send_notification(f"🛡️ **سیگنال GLN توسط مدیریت ریسک مسدود شد**\nعلت: {res.message}")
            return

        # 2. Calculate SL/TP (STRUCTURAL)
        sl_price = 0
        try:
             ohlcv = await self.execution_engine.fetch_market_data(self.symbol, data_type='ohlcv', timeframe='5m', limit=5)
             last_candle = ohlcv[-2] if ohlcv and len(ohlcv) >= 2 else None
             
             if side == 'buy' and last_candle:
                 struct_low = last_candle[3]
                 if (current_price - struct_low) / current_price < 0.0005:
                     sl_price = self.q_low if self.q_low > 0 else current_price * 0.995
                 else:
                     sl_price = struct_low
             else:
                 struct_high = last_candle[2]
                 if (struct_high - current_price) / current_price < 0.0005:
                     sl_price = self.q_high if self.q_high > 0 else current_price * 1.005
                 else:
                     sl_price = struct_high
        except Exception:
             sl_price = current_price * (1 - 0.005) if side == 'buy' else current_price * (1 + 0.005)

        risk = abs(current_price - sl_price)
        tp_price = current_price + (risk * 3) if side == 'buy' else current_price - (risk * 3)
            
        # 3. Position Details
        volume = (margin * leverage) / current_price if current_price > 0 else 0
        
        # 4. Analyze with AI
        ai_msg = ""
        ai_emoji = ""
        ai_score = 0
        try:
            analyzer = MarketAnalyzer(self.execution_engine, self.symbol)
            trend, data = await analyzer.analyze()
            
            if data:
                ai_score = data.get('ai_confidence', 0)
                rsi = data.get('rsi_15m', 50)
                is_aligned = (side == 'buy' and trend == 'UPTREND') or (side == 'sell' and trend == 'DOWNTREND')
                
                if is_aligned and ai_score > 0.5:
                    ai_emoji = "🤖✅ High Confidence"
                elif not is_aligned:
                    ai_emoji = "🤖⚠️ Counter-Trend"
                else:
                    ai_emoji = "🤖⚖️ Neutral"
                    
                ai_msg = (
                    f"🧠 **تحلیل هوشمند:**\n"
                    f"   • Trend: {trend}\n"
                    f"   • RSI: {rsi:.1f}\n"
                    f"   • AI Score: {ai_score:.2f} {ai_emoji}\n"
                )
        except Exception as e:
            logger.error(f"AI Analysis Failed: {e}")

        # 5. Format Message
        emoji = "🔵" if side == 'buy' else "🔴"
        direction = "LONG (BUY)" if side == 'buy' else "SHORT (SELL)"
        
        msg = (
            f"{emoji} **سیگنال {direction}** ✅ **اجرا شد**\n\n"
            f"⚡ علت: {reason}\n"
            f"📍 نقطه ورود: {current_price}\n"
            f"🚫 حد ضرر (SL): {sl_price:.2f}\n"
            f"✅ حد سود (TP): {tp_price:.2f}\n\n"
            f"{ai_msg}\n"
            f"⚙️ مدیریت سرمایه:\n"
            f"💵 مارجین: {margin}$\n"
            f"🎰 اهرم: {leverage}x\n"
            f"📦 حجم پوزیشن: {volume:.4f} {self.symbol.split('/')[0]}\n"
            f"🆔 سفارش: `{res.order_id if res.order_id else 'N/A'}`"
        )
        
        logger.info(f"Signal Executed: {side} @ {current_price}")
        
        self.positions.append({
            'symbol': self.symbol,
            'side': side,
            'entryPrice': current_price,
            'sl': sl_price,
            'tp': tp_price,
            'timestamp': datetime.now().timestamp(),
            'order_id': res.order_id
        })
        
        signal_data = {
            'symbol': self.symbol,
            'side': side,
            'price': current_price,
            'sl': sl_price,
            'tp': tp_price,
            'margin': margin,
            'leverage': leverage,
            'reason': reason,
            'strategy_type': 'GLN',
            'order_id': res.order_id
        }
        
        self.signal_sent = True # Lock until return to channel
        self.last_signal_side = side
        await self.send_notification(msg, signal_data=signal_data)

    async def sync_with_exchange(self):
        if self.position_tracker:
            await self.position_tracker.sync()
    
    def save_state(self):
        """Saves strategy state to database for persistence after restart."""
        if not self.db_manager or not self.strategy_id:
            return
        
        state = {
            'positions': self.positions,
            'pdh': self.pdh,
            'pdl': self.pdl,
            'pdc': self.pdc,
            'today_open': self.today_open,
            'gap_filled': self.gap_filled,
            'trend_direction': self.trend_direction,
            'candle_count': self.candle_count,
            'q_high': self.q_high,
            'q_low': self.q_low if self.q_low != float('inf') else 0.0,
            'is_q_channel_set': self.is_q_channel_set,
            'atr_value': self.atr_value,
            'signal_sent': self.signal_sent,
            'last_signal_side': self.last_signal_side,
            'q_probability': self.q_probability,
            'last_reset_date': self.last_reset_date.isoformat() if self.last_reset_date else None,
            'type': 'gln'
        }
        
        try:
            self.db_manager.save_strategy(
                self.strategy_id, self.symbol, self.market_type, 'gln',
                self.initial_investment, self.leverage, state
            )
            logger.debug(f"QGLN State saved for {self.symbol}")
        except Exception as e:
            logger.error(f"Failed to save QGLN state: {e}")
    
    def load_state(self):
        """Loads strategy state from database after restart."""
        if not self.db_manager or not self.strategy_id:
            return
        
        try:
            strategy_data = self.db_manager.get_strategy(self.strategy_id)
            if not strategy_data or not strategy_data.get('state'):
                logger.info(f"No saved state found for {self.strategy_id}")
                return
            
            state = strategy_data['state']
            
            # Restore state
            self.positions = state.get('positions', [])
            self.pdh = state.get('pdh', 0.0)
            self.pdl = state.get('pdl', 0.0)
            self.pdc = state.get('pdc', 0.0)
            self.today_open = state.get('today_open', 0.0)
            self.gap_filled = state.get('gap_filled', False)
            self.trend_direction = state.get('trend_direction')
            self.candle_count = state.get('candle_count', 0)
            self.q_high = state.get('q_high', 0.0)
            q_low_val = state.get('q_low', 0.0)
            self.q_low = q_low_val if q_low_val > 0 else float('inf')
            self.is_q_channel_set = state.get('is_q_channel_set', False)
            self.atr_value = state.get('atr_value', 0)
            self.signal_sent = state.get('signal_sent', False)
            self.last_signal_side = state.get('last_signal_side')
            self.q_probability = state.get('q_probability', 0)
            
            reset_date_str = state.get('last_reset_date')
            if reset_date_str:
                from dateutil.parser import parse
                self.last_reset_date = parse(reset_date_str).date()
            
            logger.info(f"QGLN State loaded for {self.symbol}: Candle={self.candle_count}, Q-Channel={self.is_q_channel_set}, Gap={self.gap_filled}")
        except Exception as e:
            logger.error(f"Failed to load QGLN state: {e}")
    
    async def perform_daily_reset(self, reset_date):
        """Performs daily reset at 09:25 NY time."""
        logger.info(f"QGLN Daily Reset: {reset_date} for {self.symbol}")
        
        # Reset Q-Channel state
        self.q_high = 0.0
        self.q_low = float('inf')
        self.is_q_channel_set = False
        self.candle_count = 0
        self.q_probability = 0
        self.signal_sent = False
        self.last_signal_side = None
        self.gap_filled = False
        self.trend_direction = None
        self.daily_reset_logged = False
        
        # Recalculate daily levels
        await self.calculate_daily_levels()
        
        self.last_reset_date = reset_date
        
        # Log to Dashboard via Event Reporter (report() takes event_type, data dict only)
        if self.event_reporter:
            await self.event_reporter.report(
                'QGLN',
                {'symbol': self.symbol, 'detail': f"Daily Reset: {reset_date.isoformat()}", 'date': reset_date.isoformat(), 'pdh': self.pdh, 'pdl': self.pdl, 'pdc': self.pdc}
            )
        
        # Update Registry
        if self.scanner_registry:
            self.scanner_registry.update('QGLN', 
                last_reset_date=reset_date.isoformat(),
                candle_count=0,
                q_high=0.0,
                q_low=float('inf'),
                is_q_channel_set=False,
                gap_filled=False
            )
        
        await self.send_notification(f"♻️ **ریست روزانه QGLN**\nنماد: {self.symbol}\nتاریخ: {reset_date}")
    
    def update_registry_state(self, candle_num, current_price):
        """Updates scanner registry with current QGLN state."""
        if not self.scanner_registry:
            return
        
        state_data = {
            'candle_count': candle_num,
            'q_high': self.q_high,
            'q_low': self.q_low if self.q_low != float('inf') else 0.0,
            'is_q_channel_set': self.is_q_channel_set,
            'q_probability': self.q_probability,
            'current_price': current_price,
            'gap_filled': self.gap_filled,
            'trend_direction': self.trend_direction,
            'last_update': datetime.now().isoformat()
        }
        
        # Update QGLN scanner state
        self.scanner_registry.update('QGLN', **state_data)
        
        # Also update active_symbols if this symbol is tracked
        current_symbols = self.scanner_registry.get('QGLN').get('active_symbols', [])
        symbol_key = self.symbol.replace('/', '').replace(':', '')
        if symbol_key not in current_symbols:
            current_symbols.append(symbol_key)
            self.scanner_registry.update('QGLN', active_symbols=current_symbols)

    async def sync_from_recent_bars(self, market_open_ny, max_candles=18):
        """
        Sync Q-high/Q-low from historical 5m bars since market open.
        Used for late-start resume to restore correct Q-channel state.
        
        Args:
            market_open_ny: datetime in NY timezone for market open (09:30)
            max_candles: Maximum number of candles to sync (default 18 for Q-window)
        """
        try:
            # Convert market open to UTC timestamp for API
            market_open_utc = market_open_ny.astimezone(pytz.utc)
            since_ts = int(market_open_utc.timestamp() * 1000)
            
            # Fetch OHLCV 5m bars (fetch more than needed to ensure we get all since open)
            ohlcv = await self.execution_engine.fetch_market_data(self.symbol, data_type='ohlcv', timeframe='5m', limit=100)
            if not ohlcv:
                logger.warning("No OHLCV data available for Q-channel sync")
                return
            
            # Filter candles since market_open
            ohlcv = [c for c in ohlcv if c[0] >= since_ts]
            
            if not ohlcv:
                logger.warning("No candles found since market open for Q-channel sync")
                return
            
            # Take first max_candles candles (or all if less)
            q_candles = ohlcv[:max_candles]
            
            if q_candles:
                # ohlcv format: [timestamp, open, high, low, close, volume]
                highs = [c[2] for c in q_candles]
                lows = [c[3] for c in q_candles]
                
                self.q_high = max(highs)
                self.q_low = min(lows)
                
                # Calculate ATR from recent bars
                ohlcv_atr = await self.execution_engine.fetch_market_data(self.symbol, data_type='ohlcv', timeframe='5m', limit=30)
                if ohlcv_atr:
                    ranges = [c[2] - c[3] for c in ohlcv_atr[-14:]]
                    self.atr_value = sum(ranges) / len(ranges) if ranges else 0
                
                logger.info(f"Synced Q-Channel: High={self.q_high}, Low={self.q_low}, ATR={self.atr_value} (from {len(q_candles)} candles)")
        except Exception as e:
            logger.error(f"Failed to sync Q-channel from historical bars: {e}")
            # Don't send notification to avoid spam - just log
    
    async def format_status_snapshot(self, state, candle_index, minutes_into_session, ny_now):
        """
        Format status message on QGLN enable showing current session state.
        
        Args:
            state: 'PREOPEN', 'TRACKING', 'POST_QWINDOW', 'POSTSESSION'
            candle_index: Current candle index (0-18)
            minutes_into_session: Minutes since 09:30 NY
            ny_now: Current NY datetime
        """
        ny_time_str = ny_now.strftime("%H:%M")
        
        # State labels
        state_labels = {
            'PREOPEN': '⏳ در انتظار باز شدن (قبل از 09:30)',
            'TRACKING': '📊 در حال ردیابی (Q-Window)',
            'POST_QWINDOW': '🔒 کانال قفل شده (بعد از کندل 18)',
            'POSTSESSION': '🏁 پایان سشن'
        }
        state_label = state_labels.get(state, state)
        
        lines = [
            f"🕒 **وضعیت QGLN**",
            f"⏰ زمان NY: {ny_time_str}",
            f"📊 وضعیت سشن: {state_label}",
            f"🕯️ کندل: {candle_index}/18"
        ]
        
        # Q-Channel info
        if self.q_high > 0 and self.q_low < float('inf'):
            channel_status = "🔒 قفل شده" if self.is_q_channel_set else "📊 در حال ردیابی"
            lines.append(f"📦 کانال Q: {channel_status}")
            lines.append(f"   🔼 سقف: {self.q_high:.2f}")
            lines.append(f"   🔽 کف: {self.q_low:.2f}")
        else:
            lines.append(f"📦 کانال Q: ⏳ در حال تشکیل")
        
        # Gap status
        gap_status = "✅ پر شده" if self.gap_filled else "❌ باز"
        lines.append(f"🌐 گپ: {gap_status}")
        
        # Next milestone
        if state == 'TRACKING' and candle_index < 18:
            milestones = {1: 0, 7: 6, 12: 11, 18: 17}
            next_milestone = None
            for milestone_candle in sorted(milestones.keys()):
                if candle_index < milestone_candle:
                    next_milestone = milestone_candle
                    break
            
            if next_milestone:
                candles_remaining = next_milestone - candle_index
                minutes_remaining = candles_remaining * 5
                lines.append(f"⏭️ بعدی: کندل {next_milestone} ({minutes_remaining} دقیقه)")
        
        # Probability
        if candle_index > 0:
            lines.append(f"📈 احتمال Q: {self.q_probability}%")
        
        return "\n".join(lines)
    
    async def send_notification(self, msg, signal_data=None):
        if self.message_callback:
            try:
                await self.message_callback(msg, signal_data=signal_data)
            except TypeError:
                await self.message_callback(msg)

    async def get_status(self, current_price=None):
        """Returns a string summary of the strategy status."""
        if current_price is None:
            ticker = await self.execution_engine.fetch_market_data(self.symbol)
            current_price = ticker['last'] if ticker else 0
                
        status_code = "🟢 فعال" if self.running else "🔴 متوقف"
        pos_status = "✅ دارای پوزیشن" if self.positions else "💤 بدون پوزیشن"
        
        # Determine Zone
        zone = "داخل کانال"
        if self.is_q_channel_set:
            if current_price > self.q_high:
                zone = "🚀 بالا (Breakout Buy)"
            elif current_price < self.q_low:
                zone = "📉 پایین (Breakout Sell)"
        else:
            zone = "در حال تشکیل کانال (Tracking)"

        return (
            f"🤖 **وضعیت استراتژی GLN (Crypto)**\n"
            f"نماد: `{self.symbol}`\n"
            f"وضعیت: {status_code}\n"
            f"پوزیشن: {pos_status}\n"
            f"⏱ پیشرفت القای Q: `{self.q_probability}%` (کندل {self.candle_count}/18)\n"
            f"📏 کانال Q:\n"
            f"   🔼 سقف: `{self.q_high}`\n"
            f"   🔽 کف: `{self.q_low}`\n"
            f"📍 قیمت فعلی: `{current_price}`\n"
            f"🌐 ناحیه: **{zone}**\n"
            f"🛡 گپ: {'✅ پر شده' if self.gap_filled else '❌ باز'}"
        )
    
    # Unit test simulation for candle index computation
    @staticmethod
    def _test_candle_index_simulation():
        """
        Simulates compute_candle_index for different NY times.
        Run this when file is executed as main to verify logic.
        """
        print("=" * 60)
        print("QGLN Candle Index Simulation Test")
        print("=" * 60)
        
        # Mock GLNStrategy for testing (minimal init)
        class MockStrategy:
            def get_ny_time(self):
                return datetime.now(pytz.timezone('America/New_York'))
            
            def get_session_bounds(self, ny_date=None):
                ny_tz = pytz.timezone('America/New_York')
                if ny_date is None:
                    now_ny = datetime.now(ny_tz)
                    ny_date = now_ny.date()
                reset_time = ny_tz.localize(datetime.combine(ny_date, time(9, 25)))
                open_time = ny_tz.localize(datetime.combine(ny_date, time(9, 30)))
                session_end = ny_tz.localize(datetime.combine(ny_date, time(16, 0)))
                return reset_time, open_time, session_end
            
            def compute_candle_index(self, ny_now=None):
                if ny_now is None:
                    ny_now = self.get_ny_time()
                today = ny_now.date()
                reset_time, open_time, session_end = self.get_session_bounds(today)
                if ny_now < reset_time:
                    return ('PREOPEN', 0, 0)
                if ny_now < open_time:
                    return ('PREOPEN', 0, 0)
                if ny_now >= session_end:
                    return ('POSTSESSION', 18, int((session_end - open_time).total_seconds() / 60))
                minutes_into_session = int((ny_now - open_time).total_seconds() / 60)
                candle_index = (minutes_into_session // 5) + 1
                if candle_index <= 18:
                    return ('TRACKING', candle_index, minutes_into_session)
                else:
                    return ('POST_QWINDOW', 18, minutes_into_session)
        
        mock = MockStrategy()
        ny_tz = pytz.timezone('America/New_York')
        today = datetime.now(ny_tz).date()
        
        test_times = [
            (time(9, 26), "09:26 NY - Before reset"),
            (time(9, 29), "09:29 NY - Before open"),
            (time(9, 31), "09:31 NY - Candle 1"),
            (time(10, 0), "10:00 NY - Candle 7"),
            (time(10, 30), "10:30 NY - Candle 13"),
            (time(11, 0), "11:00 NY - Candle 19 (>18)"),
            (time(16, 0), "16:00 NY - Session end"),
        ]
        
        for test_time, label in test_times:
            ny_dt = ny_tz.localize(datetime.combine(today, test_time))
            state, candle_idx, minutes = mock.compute_candle_index(ny_dt)
            print(f"{label}")
            print(f"  State: {state}")
            print(f"  Candle: {candle_idx}/18")
            print(f"  Minutes into session: {minutes}")
            print()
        
        print("=" * 60)
        print("Expected Results:")
        print("09:26 -> PREOPEN, candle 0")
        print("09:29 -> PREOPEN, candle 0")
        print("09:31 -> TRACKING, candle 1")
        print("10:00 -> TRACKING, candle 7")
        print("10:30 -> TRACKING, candle 13")
        print("11:00 -> POST_QWINDOW, candle 18")
        print("16:00 -> POSTSESSION, candle 18")
        print("=" * 60)

if __name__ == "__main__":
    GLNStrategy._test_candle_index_simulation()

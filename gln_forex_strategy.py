import MetaTrader5 as mt5
import asyncio
import logging
from datetime import datetime, timedelta
import pytz
import pandas as pd
from risk_engine import TradeRequest
from execution_engine import ExecutionEngine

logger = logging.getLogger(__name__)

class GLNForexStrategy:
    def __init__(self, execution_engine: ExecutionEngine, symbol, lots, db_manager=None, strategy_id=None, message_callback=None, position_tracker=None):
        self.execution_engine = execution_engine
        self.position_tracker = position_tracker # Type execution_engine
        self.symbol = symbol
        self.volume = lots # In Lots (e.g. 0.01)
        self.side = None # 'buy', 'sell', or None (Auto) - Defaulted to None as it's no longer a parameter
        self.leverage = 100 # Not directly used in MT5 order sent, but for info - Defaulted to 100 as it's no longer a parameter
        self.db_manager = db_manager
        self.strategy_id = strategy_id
        self.message_callback = message_callback
        
        self.status = "INITIALIZING"
        self.running = True
        self.in_position = False
        self.current_ticket = None
        self.us30_symbol = None # Will be auto-detected
        
        # GLN Levels
        self.pdh = 0.0
        self.pdl = 0.0
        self.pdc = 0.0
        self.today_open = 0.0
        self.gap_percent = 0.0
        self.gap_filled = False
        
        # Q-Channel (18 Candles)
        self.q_high = 0.0
        self.q_low = float('inf')
        self.candle_count = 0
        self.is_q_channel_set = False
        self.atr_value = 0
        self.atr_value = 0
        self.signal_sent = False
        self.last_signal_side = None # For Fakeout detection
        self.q_probability = 0 # Official Q-Timer Probability (20-90%)
        self.last_signal_side = None # For Fakeout detection

    async def initialize(self):
        """Prepares the strategy."""
        # Check symbol
        if not mt5.symbol_select(self.symbol, True):
             logger.error(f"Symbol {self.symbol} not selected")
             return False

        # Auto-detect US30 Symbol
        for s in ["US30", ".US30", "DJI", "Dow Jones", "US30.cash"]:
            if mt5.symbol_select(s, True):
                self.us30_symbol = s
                logger.info(f"Correlation Symbol Detected: {s}")
                break
        if not self.us30_symbol:
            logger.warning("US30 Symbol not found! Correlation filter disabled.")

        await self.calculate_daily_levels()
        await self.send_notification(f"🌍 **GLN Forex Started**\nSymbol: {self.symbol}\nVolume: {self.volume} lot")
        return True

    async def send_notification(self, msg):
        """Helper to send Telegram messages."""
        if self.message_callback:
            if asyncio.iscoroutinefunction(self.message_callback):
                await self.message_callback(msg)
            else:
                self.message_callback(msg)

    async def calculate_daily_levels(self):
        """Fetches Daily candles from MT5."""
        # Copy 2 daily candles (Yesterday, Today)
        rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_D1, 0, 2)
        if rates is None or len(rates) < 2:
            logger.error("Failed to fetch daily rates")
            return

        # rates is a numpy array (struct)
        # Index 0 = Yesterday, Index 1 = Today (incomplete)
        yesterday = rates[0]
        today = rates[1]
        
        self.pdh = yesterday['high']
        self.pdl = yesterday['low']
        self.pdc = yesterday['close']
        self.today_open = today['open']
        
        if self.pdc != 0:
            self.gap_percent = ((self.today_open - self.pdc) / self.pdc) * 100
        
        msg = (
            f"📊 **Daily Analysis (MT5)**\n"
            f"Symbol: `{self.symbol}`\n"
            f"PDH: `{self.pdh}`\n"
            f"PDL: `{self.pdl}`\n"
            f"PDC: `{self.pdc}`\n"
            f"Open: `{self.today_open}`\n"
            f"Gap: `{self.gap_percent:.2f}%`"
        )
        await self.send_notification(msg)

    async def check_market(self):
        """Main Loop: Q-Strategy with Gilani Rules."""
        if not self.running: return

        try:
            # 1. NY Time Sync
            ny_tz = pytz.timezone('America/New_York')
            now_ny = datetime.now(ny_tz)
            market_open = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
            
            # Wait for Market Open
            if now_ny < market_open: return

            # Calc Candle Count (5m) from 09:30
            diff_mins = (now_ny - market_open).total_seconds() / 60
            candle_num = int(diff_mins // 5) + 1
            self.candle_count = candle_num
            
            # Get Current Price
            tick = mt5.symbol_info_tick(self.symbol)
            if not tick: return
            current_price = tick.last

            # --- MANAGE ACTIVE POSITION (15m Trailing) ---
            if self.in_position:
                await self.manage_position_trailing(current_price)
                return # Skip entry logic if already in position

            # --- TRACKING PHASE (Candles 1-18) ---
            if 1 <= candle_num <= 18:
                if current_price > self.q_high: self.q_high = current_price
                if current_price < self.q_low: self.q_low = current_price
                
                # Q-Timer Alerts & Probability (Official Steps)
                if candle_num == 1:
                    self.q_probability = 20
                elif candle_num == 7: # Gap Check Time (10:05 NY)
                    self.q_probability = 50
                    await self.check_gap_status()
                elif candle_num == 12:
                    self.q_probability = 70
                elif candle_num == 18:
                    self.q_probability = 90
                    if (diff_mins % 5 > 4.8): # End of Candle 18
                        await self.send_notification(f"🔒 **Q-Channel Locked!**\nHigh: {self.q_high}\nLow: {self.q_low}")

            # --- EXECUTION PHASE (After Candle 18) ---
            elif candle_num > 18:
                if not self.is_q_channel_set:
                    self.is_q_channel_set = True # Channel is finalized

                # 4. Breakout Check with Correlation
                # Wait for Candle Close (simulated by checking newly formed candle High/Low vs previous close? 
                # Ideally we check on new bar event. For loop, we check continuously)
                
                # Check for Breakout
                if current_price > self.q_high:
                    if await self.check_correlation("buy"):
                         await self.execute_trade('buy', "Breakout Q-High")
                elif current_price < self.q_low:
                     if await self.check_correlation("sell"):
                         await self.execute_trade('sell', "Breakout Q-Low")

                # 5. Fakeout / Reversal Logic
                if self.signal_sent and not self.in_position and self.last_signal_side:
                    # Check if price returned to channel
                    is_inside = self.q_low < current_price < self.q_high
                    
                    if is_inside:
                        # Confirmation: Close inside (implied by current_price check on loop, 
                        # ideally check previous candle close, but live price deep inside is strong enough)
                        
                        reverse_side = 'sell' if self.last_signal_side == 'buy' else 'buy'
                        logger.warning(f"⚠️ Fakeout Detected on {self.symbol}! Reversing to {reverse_side.upper()}")
                        
                        # Reset flags to allow new trade
                        self.signal_sent = False 
                        self.last_signal_side = None
                        
                        await self.execute_trade(reverse_side, "Fakeout Reversal")

                # Signal Gate Reset (Hysteresis) - Only if NOT a fakeout immediately
                # If we are just hovering near entry, keep signal_sent True.
                # If we go deep inside, reset.
                if self.signal_sent and not self.in_position:
                    channel_width = self.q_high - self.q_low
                    buffer = max(channel_width * 0.1, current_price * 0.001)
                    
                    # If strictly inside buffer zone (Deep inside)
                    if (self.q_low + buffer) < current_price < (self.q_high - buffer):
                         # Only reset if we haven't already triggered fakeout? 
                         # Actually checking fakeout above handles the critical reversal.
                         # This acts as a general reset for manual closures.
                         self.signal_sent = False
                         self.last_signal_side = None

        except Exception as e:
            logger.error(f"GLN Forex Loop Error: {e}")

    async def check_gap_status(self):
        """Checks if daily gap is filled at Candle 7."""
        if self.gap_filled: return
        
        # Get Current Price
        tick = mt5.symbol_info_tick(self.symbol)
        if not tick: return
        current_price = tick.last
        
        # Check if price touched PDC
        # Simple proximity check (e.g. 0.05%)
        if self.pdc == 0: return

        dist = abs(current_price - self.pdc) / self.pdc
        if dist < 0.0005: 
            self.gap_filled = True
            await self.send_notification(f"✅ **گپ پر شد!** (در کندل {self.candle_count})\nاستراتژی: رنج / بازگشت (Reversal)")
        else:
            if self.candle_count == 7: # Notify only at check time
                 await self.send_notification(f"⚠️ **گپ پر نشد!** (پایان کندل ۷)\nاستراتژی: روند قدرتمند (Trend) در جهت گپ")

    async def check_correlation(self, side):
        """Checks US30 (Dow Jones) for confirmation."""
        if not self.us30_symbol:
            return True # Pass if no symbol found

        try:
            # 1. Fetch US30 Data for Q-Channel (09:30 - 11:00 NY)
            ny_tz = pytz.timezone('America/New_York')
            now_ny = datetime.now(ny_tz)
            market_open = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
            
            # Convert to UTC for MT5
            utc_open = market_open.astimezone(pytz.utc)
            
            # Fetch M5 candles since Open
            rates = mt5.copy_rates_from(self.us30_symbol, mt5.TIMEFRAME_M5, utc_open, 20)
            if rates is None or len(rates) < 18:
                return True # Not enough data, skip check
            
            # Calculate US30 Q-Levels
            q_candles = rates[:18]
            us30_high = max(c['high'] for c in q_candles)
            us30_low = min(c['low'] for c in q_candles)
            
            # Get Current US30 Price
            tick = mt5.symbol_info_tick(self.us30_symbol)
            if not tick: return True
            us30_price = tick.last
            
            # 2. Check Logic
            # If Buy (Long): US30 should NOT be rejecting from Resistance (High)
            # "Rejecting" means it touched High but is now below it?
            # User Rule: "IF US30 is hitting a strong Resistance (e.g. its own Candle 18 High) and rejecting"
            
            buffer = (us30_high - us30_low) * 0.05 # 5% zone
            
            if side == 'buy':
                # If US30 is near High (> High - Buffer) AND Price < High (Rejecting?)
                # Or simply: If US30 is stuck at High. 
                # Let's say: If US30 is BELOW its Q-High but close to it, and NOT breaking out.
                if (us30_high - buffer) < us30_price < us30_high:
                    logger.warning(f"Correlation Block: US30 at Resistance ({us30_high})")
                    return False
            
            elif side == 'sell':
                # If US30 at Support
                if us30_low < us30_price < (us30_low + buffer):
                    logger.warning(f"Correlation Block: US30 at Support ({us30_low})")
                    return False
                    
            return True
        except Exception as e:
            logger.error(f"Correlation Check Error: {e}")
            return True

    async def sync_with_market(self):
        try:
            if not self.position_tracker:
                 pos = await asyncio.to_thread(mt5.positions_get, symbol=self.symbol)
            else:
                 await self.position_tracker.sync()
                 positions = self.position_tracker.get_positions(market_type='forex', symbol=self.symbol)
                 pos = positions
            
            if not pos:
                self.current_ticket = None
                self.status = "MARKET_OPEN"
                return None # Return None if no position
            
            # PositionTracker returns normalized dicts, mt5 returns tuples of objects
            if isinstance(pos[0], dict):
                 self.current_ticket = pos[0]['ticket']
                 self.status = "POSITION_OPEN"
                 return pos[0] # Return the normalized position dict
            else:
                 self.current_ticket = pos[0].ticket
                 self.status = "POSITION_OPEN"
                 return pos[0] # Return the mt5 position object
                 
        except Exception as e:
            logger.error(f"Sync error: {e}")
            return None # Return None on error

    async def manage_position_trailing(self, current_price):
        """Trailing Stop on 15m Timeframe."""
        try:
            # Get Position info
            pos = await self.sync_with_market()
            if not pos:
                self.in_position = False
                # Position closed (hit SL/TP or manual). Reset state?
                # Check for Fakeout here if recently closed with loss?
                return

            # Adapt to whether pos is a dict (from PositionTracker) or an mt5.TradePosition object
            if isinstance(pos, dict):
                ticket = pos['ticket']
                entry_price = pos['price_open']
                current_sl = pos['sl']
                pos_type = pos['type'] # 0=Buy, 1=Sell
            else: # Assume mt5.TradePosition object
                ticket = pos.ticket
                entry_price = pos.price_open
                current_sl = pos.sl
                pos_type = pos.type # 0=Buy, 1=Sell
            
            # Only start trailing if in profit? Or immediately?
            # User: "Once the trade is in profit"
            in_profit = (current_price > entry_price) if pos_type == 0 else (current_price < entry_price)
            if not in_profit: return

            # Get last closed 15m candle
            rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M15, 1, 1)
            if rates is None: return
            last_candle = rates[0]
            
            new_sl = 0.0
            
            if pos_type == 0: # Buy
                # Trail behind Low of previous 15m
                candidate_sl = last_candle['low']
                if candidate_sl > current_sl: # Only move SL up
                    new_sl = candidate_sl
            else: # Sell
                # Trail behind High
                candidate_sl = last_candle['high']
                if current_sl == 0 or candidate_sl < current_sl: # Only move SL down
                    new_sl = candidate_sl
            
            if new_sl != 0:
                # Modify Order
                request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "position": ticket,
                    "sl": new_sl,
                    "tp": pos.tp, # Keep TP or Remove? User: "Continue this until stopped out" -> Remove TP? 
                                  # Let's keep TP for safety, but maybe widen it? User didn't specify.
                                  # "Detailed... run huge trends". Maybe remove TP?
                                  # I'll keep TP for now to avoid error, 
                                  # but if price approaches TP we might want to extend.
                    "magic": 234001
                }
                res = mt5.order_send(request)
                if res.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(f"Trailing SL Updated to {new_sl}")
                    
        except Exception as e:
            logger.error(f"Trailing Logic Error: {e}")

    async def execute_trade(self, side, reason):
        """Sends Order with Structural SL via ExecutionEngine."""
        if self.in_position: return
        
        tick = mt5.symbol_info_tick(self.symbol)
        price = tick.ask if side == 'buy' else tick.bid
        
        # 1. Structural SL
        rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M5, 1, 1)
        if rates is not None:
            last_candle = rates[0]
            if side == 'buy':
                structural_sl = last_candle['low']
                if (price - structural_sl) / price < 0.0005:
                    structural_sl = self.q_low 
            else:
                structural_sl = last_candle['high']
                if (structural_sl - price) / price < 0.0005:
                    structural_sl = self.q_high
        else:
            structural_sl = price * 0.995 if side == 'buy' else price * 1.005

        sl = structural_sl
        risk = abs(price - sl)
        tp = price + (risk * 3) if side == 'buy' else price - (risk * 3)
        
        # 2. Execute via Engine
        req = TradeRequest(
            symbol=self.symbol,
            amount=self.volume,
            leverage=self.leverage,
            side=side,
            market_type='forex',
            user_id=0,
            params={
                'sl': sl,
                'tp': tp,
                'comment': f"GLN {reason}",
                'magic': 234001
            }
        )
        
        res = await self.execution_engine.execute(req)
        
        if res.success:
            self.in_position = True
            msg = (
                f"⚡ **GLN SIGNAL EXECUTED**\n"
                f"Type: {side.upper()}\n"
                f"Entry: {price}\n"
                f"SL: {sl} (Structural)\n"
                f"TP: {tp} (Initial)\n"
                f"Reason: {reason}\n"
                f"Ticket: {res.order_id}"
            )
            self.signal_sent = True
            self.last_signal_side = side
            await self.send_notification(msg)
        else:
            logger.error(f"Order failed: {res.message}")

    async def get_status(self):
        """Returns a string summary of the strategy status."""
        status_code = "🟢 فعال" if self.running else "🔴 متوقف"
        pos_status = "✅ دارای پوزیشن" if self.in_position else "💤 بدون پوزیشن"
        
        return (
            f"🤖 **وضعیت استراتژی GLN (Forex)**\n"
            f"نماد: `{self.symbol}`\n"
            f"وضعیت: {status_code}\n"
            f"پوزیشن: {pos_status}\n"
            f"⏱ پیشرفت القای Q: `{self.q_probability}%` (کندل {self.candle_count}/18)\n"
            f"📏 کانال Q:\n"
            f"   🔼 سقف: `{self.q_high}`\n"
            f"   🔽 کف: `{self.q_low}`\n"
            f"🛡 گپ: {'✅ پر شده' if self.gap_filled else '❌ باز'}"
        )

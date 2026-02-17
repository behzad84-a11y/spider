import asyncio
import logging
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from risk_engine import TradeRequest
from execution_engine import ExecutionEngine, ExecutionResult

logger = logging.getLogger(__name__)

# Helper for sync CCXT
async def async_run(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)

class SpiderStrategy:
    def __init__(self, execution_engine: ExecutionEngine, symbol, initial_investment, side, market_type='spot', leverage=1, db_manager=None, strategy_id=None, atr=None, use_martingale=True, message_callback=None, position_tracker=None):
        self.execution_engine = execution_engine
        self.position_tracker = position_tracker
        self.symbol = symbol
        self.initial_investment = initial_investment
        self.side = side.lower()
        self.market_type = market_type.lower()
        self.leverage = leverage
        self.db_manager = db_manager
        self.strategy_id = strategy_id
        self.initial_atr = atr
        self.message_callback = message_callback
        
        self.positions = []
        self.total_invested = 0
        self.step_count = 0
        
        # تنظیمات استراتژی عنکبوتی (مارتینگل)
        self.use_martingale = use_martingale
        
        if not self.use_martingale:
            # Sniper Mode: Use FULL amount for entry
            self.base_order_size = initial_investment
            self.safety_order_size = 0
        else:
            # Standard Martingale: Start small (5%)
            self.base_order_size = initial_investment * 0.05
            self.safety_order_size = initial_investment * 0.05

        self.max_safety_orders = 10
        self.safety_order_step_scale = 1.5
        self.safety_order_volume_scale = 1.2
        self.take_profit_percent = 0.015 # 1.5% سود
        self.stop_loss_percent = 0.05 # 5% ضرر کل

        # وضعیت فعلی
        self.current_step = 0
        self.last_buy_price = 0
        self.avg_price = 0
        self.total_volume = 0
        self.running = True
        self.stop_reason = None  # دلیل توقف (برای نمایش به کاربر)
        self.consecutive_zero_volume = 0  # 5-strike counter before kill
        self.last_pyramid_price = 0 # آخرین قیمتی که در آن حجم اضافه شده (Pyramiding)

        # استاپ لاس متحرک (Chandelier Exit)
        self.trailing_stop_active = True
        self.trailing_stop_type = 'chandelier' 
        self.atr_value = 0
        self.highest_price = 0
        self.lowest_price = float('inf')
        self.current_stop_loss = 0
        self.take_profit_price = 0
        self.sl_order_id = None  # Exchange SL order ID
        
        self.start_time = datetime.now() # Track strategy start time for sync grace period
        self.consecutive_missing_pos = 0 # Track missing position checks for safety

    def save_state(self):
        if self.db_manager and self.strategy_id:
            state = {
                'positions': self.positions,
                'total_invested': self.total_invested,
                'step_count': self.step_count,
                'current_step': self.current_step,
                'last_buy_price': self.last_buy_price,
                'avg_price': self.avg_price,
                'total_volume': self.total_volume,
                'trailing_stop_active': self.trailing_stop_active,
                'highest_price': self.highest_price,
                'current_stop_loss': self.current_stop_loss,
                'last_pyramid_price': getattr(self, 'last_pyramid_price', 0),
                'use_martingale': self.use_martingale,
                'sl_order_id': getattr(self, 'sl_order_id', None)
            }
            self.db_manager.save_strategy(
                self.strategy_id, self.symbol, self.market_type, self.side, 
                self.initial_investment, self.leverage, state
            )

    async def initialize(self):
        try:
            if self.market_type == 'future':
                ticker = await self.execution_engine.fetch_market_data(self.symbol)
                check_price = ticker.get('last', 1) if ticker else 1
                
                original_leverage = self.leverage
                if check_price < 0.001:
                    self.leverage = min(self.leverage, 3)
                elif check_price < 0.01:
                    self.leverage = min(self.leverage, 5)
                elif check_price < 0.1:
                    self.leverage = min(self.leverage, 10)
                
                if self.leverage != original_leverage:
                    logger.warning(f"Auto-capped leverage from {original_leverage}x to {self.leverage}x for low-priced coin {self.symbol} (price: {check_price})")
                
                logger.info(f"Setting leverage {self.leverage}x for {self.symbol}")
                await self.execution_engine.set_leverage(self.leverage, self.symbol, self.market_type)
                await self.execution_engine.set_margin_mode('isolated', self.symbol, self.market_type)

            ticker = await self.execution_engine.fetch_market_data(self.symbol)

            if self.side == 'buy':
                current_price = ticker['ask'] if ticker and ticker.get('ask') else (ticker['last'] if ticker else 0)
            else:
                current_price = ticker['bid'] if ticker and ticker.get('bid') else (ticker['last'] if ticker else 0)
            
            if not self.positions:
                if self.atr_value == 0:
                    self.atr_value = await self.calculate_atr()
                    logger.info(f"Calculated ATR: {self.atr_value}")

                order = await self.place_order(current_price, self.base_order_size)
                if not order:
                    logger.error(f"Failed to place initial order for {self.symbol}. Stopping strategy.")
                    self.stop_reason = "خطا در ثبت اردر اولیه"
                    self.running = False
                    return

                if self.atr_value > 0:
                    if self.side == 'buy':
                        self.take_profit_price = current_price + (4 * self.atr_value)
                        self.current_stop_loss = current_price - (1.5 * self.atr_value)
                    else:
                        self.take_profit_price = current_price - (4 * self.atr_value)
                        self.current_stop_loss = current_price + (1.5 * self.atr_value)
                    
                    logger.info(f"ATR Setup: SL={self.current_stop_loss}, TP={self.take_profit_price} (ATR={self.atr_value})")
                else:
                    if self.side == 'buy':
                        self.take_profit_price = self.avg_price * (1 + self.take_profit_percent)
                        self.current_stop_loss = self.avg_price * (1 - self.stop_loss_percent)
                    else:
                        self.take_profit_price = self.avg_price * (1 - self.take_profit_percent)
                        self.current_stop_loss = self.avg_price * (1 + self.stop_loss_percent)
                    logger.info(f"Percentage Setup: SL={self.current_stop_loss}, TP={self.take_profit_price}")
                
                if self.market_type == 'future' and self.current_stop_loss > 0:
                    sl_order = await self.place_exchange_stop_loss(self.current_stop_loss)
                    if sl_order:
                        logger.info(f"✅ Exchange SL placed successfully at {self.current_stop_loss}")
                    else:
                        logger.warning(f"⚠️ Failed to place Exchange SL at {self.current_stop_loss}. Bot will close position manually.")
                
                await self.send_position_report("🔫 **پوزیشن جدید باز شد**")
            else:
                logger.info(f"Strategy for {self.symbol} restored with {len(self.positions)} positions.")
                
            await asyncio.sleep(2)
            await self.sync_with_exchange()
        except Exception as e:
            logger.error(f"Error initializing strategy: {e}")
            self.stop_reason = str(e)[:100] if str(e) else "خطا در راه‌اندازی"
            self.running = False
            raise e

    async def sync_with_exchange(self):
        try:
            if not self.position_tracker:
                # Fallback to execution engine if tracker not injected
                positions = await self.execution_engine.fetch_positions([self.symbol], self.market_type)
            else:
                await self.position_tracker.sync()
                positions = self.position_tracker.get_positions(market_type=self.market_type, symbol=self.symbol)
            
            target_pos = None
            expected_pos_side = 'long' if self.side == 'buy' else 'short'
            
            for p in positions:
                # Handle variation in CCXT vs PositionTracker normalized formats if any
                if p.get('symbol') == self.symbol and p.get('side', expected_pos_side) == expected_pos_side:
                    target_pos = p
                    break
            
            if not target_pos:
                current_contracts = 0
            else:
                current_contracts = float(target_pos.get('contracts', 0) or target_pos.get('amount', 0))

            time_since_start = (datetime.now() - self.start_time).total_seconds()
            
            if current_contracts == 0 and (self.positions or self.total_volume > 0):
                if time_since_start < 60:
                    logger.info(f"SYNC: Position not found yet, but in grace period ({time_since_start:.1f}s). Waiting...")
                    return

                self.consecutive_missing_pos = getattr(self, 'consecutive_missing_pos', 0) + 1
                
                if self.consecutive_missing_pos < 5:
                    logger.warning(f"SYNC: Position missing for {self.symbol}. Strike {self.consecutive_missing_pos}/5.")
                    return

                logger.warning(f"SYNC: Position for {self.symbol} is closed on exchange (5 Strikes). Stopping strategy.")
                self.stop_reason = "پوزیشن روی صرافی بسته شده (۵ بار چک)"
                self.running = False
                if self.db_manager and self.strategy_id:
                    self.db_manager.delete_strategy(self.strategy_id)
                return
            
            self.consecutive_missing_pos = 0

            if current_contracts > 0:
                real_entry = float(target_pos.get('entryPrice', 0) or target_pos.get('price', 0))
                if real_entry > 0 and (self.avg_price == 0 or abs(self.avg_price - real_entry) / real_entry > 0.001):
                    logger.info(f"SYNC: Updating {self.symbol} AvgPrice {self.avg_price} -> {real_entry}")
                    self.avg_price = real_entry
                
                if self.total_volume != current_contracts:
                    logger.info(f"SYNC: Updating {self.symbol} Volume {self.total_volume} -> {current_contracts}")
                    self.total_volume = current_contracts
                
                if not self.positions:
                     self.positions.append({
                        'price': real_entry if real_entry > 0 else self.avg_price,
                        'amount': current_contracts,
                        'usdt_value': (current_contracts * real_entry) / self.leverage if self.market_type == 'future' else current_contracts * real_entry,
                        'id': 'synced'
                    })

        except Exception as e:
            logger.error(f"Error syncing with exchange: {e}")

    async def calculate_atr(self, period=14):
        try:
            ohlcv = await self.execution_engine.fetch_market_data(self.symbol, data_type='ohlcv', timeframe='1h', limit=period + 1)
            if not ohlcv or len(ohlcv) < period:
                logger.warning(f"Not enough data for ATR calculation")
                return 0
            
            true_ranges = []
            for i in range(1, len(ohlcv)):
                high = ohlcv[i][2]
                low = ohlcv[i][3]
                prev_close = ohlcv[i-1][4]
                
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)
            
            atr = sum(true_ranges[-period:]) / period
            logger.info(f"Calculated ATR for {self.symbol}: {atr}")
            return atr
        except Exception as e:
            logger.error(f"Error calculating ATR: {e}")
            return 0

    async def place_exchange_stop_loss(self, stop_price, amount=None):
        try:
            if amount is None:
                amount = self.total_volume
            
            if amount <= 0:
                logger.warning("No position volume for SL order")
                return None
            
            # Cancel old SL if exists
            if hasattr(self, 'sl_order_id') and self.sl_order_id:
                await self.execution_engine.cancel_order(self.symbol, self.sl_order_id, self.market_type)

            # ExecutionEngine handles precision and exchange-specific params
            request = TradeRequest(
                symbol=self.symbol,
                amount=amount,
                side=self.side,
                market_type=self.market_type,
                leverage=self.leverage,
                user_id=getattr(self, 'user_id', 0),
                params={'reduceOnly': True}
            )

            res = await self.execution_engine.create_trigger_order(request, stop_price)
            
            if res.success:
                self.sl_order_id = res.order_id
                logger.info(f"✅ SL Order placed on exchange: {self.symbol} @ trigger {stop_price} | ID: {self.sl_order_id}")
                return res.raw_response
            else:
                logger.error(f"Failed to place exchange SL: {res.message}")
                return None
            
        except Exception as e:
            logger.error(f"Error placing exchange SL: {e}")
            return None

    async def place_order(self, price, usdt_amount):
        """Places a market order via the ExecutionEngine."""
        try:
            req = TradeRequest(
                symbol=self.symbol,
                amount=usdt_amount,
                leverage=self.leverage,
                side=self.side,
                market_type=self.market_type,
                user_id=getattr(self, 'user_id', 0)
            )
            
            # Execute trade via centralized engine (includes Risk check + Retries)
            res = await self.execution_engine.execute(req)
            
            if not res.success:
                logger.warning(f"🚫 Trade blocked/failed for {self.symbol}: {res.message}")
                if self.message_callback and "Risk Engine" in res.message:
                    await self.message_callback(f"🛡️ **سیستم مدیریت ریسک فعال شد**\nریسک شناسایی شد: {res.message}\nترید متوقف شد.")
                return None

            order = res.raw_response
            
            self.positions.append({
                'price': price,
                'amount': float(order['amount']),
                'usdt_value': usdt_amount,
                'id': order['id']
            })
            
            self.last_buy_price = price
            self.total_invested += usdt_amount
            self.step_count += 1
            self.current_step += 1
            self.total_volume = sum(p['amount'] for p in self.positions)

            total_val = sum(p['price'] * p['amount'] for p in self.positions)
            if self.total_volume > 0:
                self.avg_price = total_val / self.total_volume
            
            if self.side == 'buy':
                self.current_stop_loss = self.avg_price * (1 - self.stop_loss_percent)
            else:
                self.current_stop_loss = self.avg_price * (1 + self.stop_loss_percent)

            self.save_state()
            self.update_average_price()
            return order
            
        except Exception as e:
            logger.error(f"Error in strategy place_order: {e}")
            return None

    def update_average_price(self):
        total_vol = sum(p['amount'] for p in self.positions)
        total_val = sum(p['price'] * p['amount'] for p in self.positions)
        if total_vol > 0:
            self.avg_price = total_val / total_vol
            self.total_volume = total_vol

    async def check_market(self):
        if not self.running:
            return
            
        if self.total_volume == 0:
             await self.sync_with_exchange()
             if self.total_volume == 0:
                 self.consecutive_zero_volume = getattr(self, 'consecutive_zero_volume', 0) + 1
                 if self.consecutive_zero_volume < 5:
                     logger.warning(f"Strategy {self.symbol} has 0 volume after sync. Strike {self.consecutive_zero_volume}/5. Waiting...")
                     return
                 logger.warning(f"Strategy {self.symbol} has 0 volume after 5 sync attempts. Stopping.")
                 self.stop_reason = "حجم صفر بعد از ۵ بار همگام‌سازی با صرافی"
                 self.running = False
                 if self.db_manager and self.strategy_id:
                     self.db_manager.delete_strategy(self.strategy_id)
                 return
        else:
            self.consecutive_zero_volume = 0
        
        await self.sync_with_exchange()

        try:
            ticker = await self.execution_engine.fetch_market_data(self.symbol)
            current_price = ticker['last'] if ticker else 0
            
            if self.atr_value > 0:
                if self.side == 'buy':
                    if current_price > self.highest_price:
                        self.highest_price = current_price
                        new_stop = self.highest_price - (2.5 * self.atr_value)
                        if new_stop > self.current_stop_loss:
                            self.current_stop_loss = new_stop
                            logger.info(f"Trailing Stop Updated (Long): {self.current_stop_loss}")
                else:
                    if self.lowest_price == float('inf') or current_price < self.lowest_price:
                        self.lowest_price = current_price
                        new_stop = self.lowest_price + (2.5 * self.atr_value)
                        if self.current_stop_loss == 0 or new_stop < self.current_stop_loss:
                            self.current_stop_loss = new_stop
                            logger.info(f"Trailing Stop Updated (Short): {self.current_stop_loss}")
            else:
                 pass

            if self.current_stop_loss > 0:
                if self.side == 'buy' and current_price <= self.current_stop_loss:
                     await self.close_position(f"Stop Loss Hit: {current_price}")
                     if self.message_callback:
                          await self.message_callback(f"🛑 **استاپ لاس خورد!**\nقیمت: {current_price}\nپوزیشن بسته شد.")
                     return
                elif self.side == 'sell' and current_price >= self.current_stop_loss:
                     await self.close_position(f"Stop Loss Hit: {current_price}")
                     if self.message_callback:
                          await self.message_callback(f"🛑 **استاپ لاس خورد!**\nقیمت: {current_price}\nپوزیشن بسته شد.")
                     return

            pnl_percent = self.calculate_pnl_percent(current_price)
            pyramid_threshold = self.take_profit_percent * 100 
            
            if pnl_percent >= pyramid_threshold:
                if not hasattr(self, 'last_pyramid_price'):
                    self.last_pyramid_price = 0
                
                price_diff_percent = 0
                if self.last_pyramid_price > 0:
                    price_diff_percent = abs((current_price - self.last_pyramid_price) / self.last_pyramid_price) * 100
                
                if self.last_pyramid_price == 0 or (price_diff_percent >= 0.5 and current_price > self.last_pyramid_price if self.side == 'buy' else current_price < self.last_pyramid_price):
                    logger.info(f"💰 Pyramiding Triggered! PnL: {pnl_percent:.2f}%")
                    try:
                        await self.place_order(current_price, self.base_order_size)
                        if self.message_callback:
                            await self.message_callback(f"🧗‍♂️ **پله جدید (Pyramid) اضافه شد!**\nسود فعلی: {pnl_percent:.2f}%\nقیمت: {current_price}")
                    except Exception as e:
                        logger.warning(f"Failed to place pyramid order: {e}")
                        if self.message_callback:
                             await self.message_callback(f"⚠️ **عدم موجودی برای پله بعدی**\nفقط استاپ‌لاس مدیریت می‌شود.")
                    
                    self.last_pyramid_price = current_price
                    
                    if self.side == 'buy':
                        new_stop = self.avg_price * 1.002 
                        if new_stop > self.current_stop_loss:
                            self.current_stop_loss = new_stop
                            if self.market_type == 'future':
                                await self.place_exchange_stop_loss(self.current_stop_loss)
                            if self.message_callback:
                                await self.message_callback(f"🛡️ **ریسک‌فری شد!**\nاستاپ‌لاس آمد روی: {self.current_stop_loss}")
                    else:
                        new_stop = self.avg_price * 0.998 
                        if self.current_stop_loss == 0 or new_stop < self.current_stop_loss:
                            self.current_stop_loss = new_stop
                            if self.market_type == 'future':
                                await self.place_exchange_stop_loss(self.current_stop_loss)
                            if self.message_callback:
                                await self.message_callback(f"🛡️ **ریسک‌فری شد!**\nاستاپ‌لاس آمد روی: {self.current_stop_loss}")
                    return

            if self.use_martingale and self.step_count < self.max_safety_orders:
                drop_percent = 0
                if self.side == 'buy':
                    drop_percent = (self.last_buy_price - current_price) / self.last_buy_price
                else:
                    drop_percent = (current_price - self.last_buy_price) / self.last_buy_price

                required_drop = (self.safety_order_step_scale ** (self.step_count - 1)) * 0.01 
                
                if drop_percent >= required_drop:
                    new_amount = self.safety_order_size * (self.safety_order_volume_scale ** (self.step_count - 1))
                    logger.info(f"Martingale Step {self.step_count+1}: Drop {drop_percent:.2f}% >= {required_drop:.2f}%")
                    await self.place_order(current_price, new_amount)
                    return

            stop_hit = False
            if self.side == 'buy':
                if current_price <= self.current_stop_loss:
                    stop_hit = True
            else:
                if current_price >= self.current_stop_loss:
                    stop_hit = True
            
            if stop_hit:
                await self.close_position(f"Stop Loss hit at {current_price}")

        except Exception as e:
            logger.error(f"Error checking market: {e}")

    def calculate_pnl_percent(self, current_price):
        if self.avg_price == 0:
            return 0
        if self.side == 'buy':
            return ((current_price - self.avg_price) / self.avg_price) * 100
        else:
            return ((self.avg_price - current_price) / self.avg_price) * 100

    def calculate_pnl(self, current_price):
        if self.avg_price == 0:
            return 0
        diff = current_price - self.avg_price if self.side == 'buy' else self.avg_price - current_price
        return diff * self.total_volume

    async def close_position(self, reason):
        """Closes the entire position via the ExecutionEngine."""
        logger.info(f"Closing position for {self.symbol}. Reason: {reason}")
        try:
            side = 'sell' if self.side == 'buy' else 'buy'
            amount = self.total_volume
            
            # Use centralized close_position (returns ExecutionResult)
            res = await self.execution_engine.close_position(
                self.symbol, self.market_type, amount, side
            )
            
            if not res.success:
                logger.error(f"Failed to close position: {res.message}")
                return False

            pnl_percent = 0
            pnl_amount = 0
            close_price = 0
            
            try:
                ticker = await self.execution_engine.fetch_market_data(self.symbol)
                close_price = ticker['last'] if ticker else 0
                pnl_amount = self.calculate_pnl(close_price)
                pnl_percent = self.calculate_pnl_percent(close_price)
                
                if self.db_manager:
                    self.db_manager.save_trade_history(self.symbol, self.side, pnl_amount, close_price)
                    
                emoji = "✅" if pnl_amount >= 0 else "❌"
                final_invested = self.total_invested if self.total_invested > 0 else (self.total_volume * self.avg_price / self.leverage if self.leverage else 0)

                msg = (
                    f"🏁 **گزارش نهایی معامله** {emoji}\n"
                    f"🏷️ ارز: {self.symbol}\n"
                    f"🎰 اهرم: {self.leverage}x\n"
                    f"💵 سرمایه درگیر: {final_invested:.2f}$\n"
                    f"💰 قیمت بسته شدن: {close_price}\n"
                    f"📊 **سود/ضرر:** {pnl_amount:.2f}$ ({pnl_percent:.2f}%)\n"
                    f"📝 دلیل بستن: {reason}"
                )
                if self.message_callback:
                    await self.message_callback(msg)
            except Exception as e:
                logger.error(f"Error saving history/notifying: {e}")

            self.stop_reason = reason if getattr(self, 'stop_reason', None) is None else self.stop_reason
            self.running = False
            if self.db_manager and self.strategy_id:
                self.db_manager.delete_strategy(self.strategy_id)
            return True
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return False

    async def send_position_report(self, title="🔫 Position Report"):
        if not self.message_callback:
            return
        try:
            direction_emoji = "🟢" if self.side == 'buy' else "🔴"
            direction_str = "LONG" if self.side == 'buy' else "SHORT"
            sl_str = f"{self.current_stop_loss}" if self.current_stop_loss > 0 else "None"
            tp_str = f"{self.take_profit_price}" if self.take_profit_price > 0 else "None"
            
            msg = (
                f"{title}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{direction_emoji} <b>{direction_str}</b> {self.symbol}\n"
                f"💰 Volume: {self.total_volume}\n"
                f"💵 Entry: {self.avg_price:.4f}\n"
                f"🎰 Leverage: {self.leverage}x\n"
                f"🛑 SL: {sl_str} (On Exchange ✅)\n"
                f"🎯 TP: {tp_str}\n"
                f"🆔 ID: <code>{self.strategy_id}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )
            keyboard = [[InlineKeyboardButton("❌ Close Position", callback_data=f"close_{self.strategy_id}"), InlineKeyboardButton("⚙️ Change Lev", callback_data=f"editlev_{self.strategy_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await self.message_callback(msg, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error sending report: {e}")

    async def get_status(self):
        try:
            current_price = 0
            try:
                ticker = await self.execution_engine.fetch_market_data(self.symbol)
                current_price = ticker['last'] if ticker else 0
            except:
                pass

            side_emoji = "🟢 LONG" if self.side == 'buy' else "🔴 SHORT"
            market_emoji = "⚓ SPOT" if self.market_type == 'spot' else "🚀 FUTURES"
            
            def fmt(val):
                if val == 0: return "0.00"
                return f"{val:.8f}".rstrip('0').rstrip('.') if val < 1 else f"{val:.2f}"

            msg = f"{market_emoji} **{self.symbol}**\n"
            msg += f"• سمت: {side_emoji}\n"
            msg += f"• اهرم: {self.leverage}x\n"
            msg += f"• قیمت ورود: {fmt(self.avg_price)}\n"
            
            if current_price > 0:
                msg += f"• قیمت فعلی: {fmt(current_price)}\n"
                pnl = self.calculate_pnl(current_price)
                pnl_percent = (pnl / self.total_invested * 100) if self.total_invested > 0 else 0
            else:
                msg += f"• قیمت فعلی: ⚠️ دریافت نشد\n"
                pnl, pnl_percent = 0, 0
            
            msg += f"• سرمایه درگیر: {self.total_invested:.2f} $\n"
            msg += f"• پله‌های خرید: {self.step_count} / {self.max_safety_orders}\n"
            msg += f"• حد ضرر (Trailing): {fmt(self.current_stop_loss) if self.current_stop_loss > 0 else 'تنظیم نشده'}\n"
            
            if current_price > 0:
                pnl_emoji = "✅" if pnl >= 0 else "❌"
                msg += f"• سود/ضرر: {pnl_emoji} {pnl:.2f} $ ({pnl_percent:.2f}%)\n"
            
            return msg
        except Exception as e:
            logger.error(f"Status error: {e}")
            return f"Error getting status for {self.symbol}: {e}"

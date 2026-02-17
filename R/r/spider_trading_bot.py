import asyncio
import logging
from typing import Any
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters
import ccxt  # Using sync CCXT (async has bugs with CoinEx)
import config
from functools import partial

# Helper to run sync CCXT calls in thread pool
async def async_run(func, *args, **kwargs):
    """Run a sync function in a thread pool to make it async-compatible."""
    return await asyncio.to_thread(func, *args, **kwargs)
from datetime import datetime, timedelta
import os
import pytz
import json
import random
import time
import re
import sys
import sqlite3

# MetaTrader 5 (optional, for Forex mode)
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False
    print("WARNING: MetaTrader5 not available. Forex features will be disabled.")

print("!!! STARTING BOT VERSION DEBUG 999 !!!")
sys.stdout.flush()

# Custom Modules
from gln_strategy import GLNStrategy
from forex_strategy import ForexStrategy
from gln_forex_strategy import GLNForexStrategy
from market_analyzer import MarketAnalyzer
from database_manager import DatabaseManager
from spider_strategy import SpiderStrategy
from risk_engine import RiskEngine, TradeRequest
from execution_engine import ExecutionEngine
from position_tracker import PositionTracker
from config import AUTO_SYMBOLS

# تنظیمات لاگینگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# States for GLN Wizard
GLN_SYMBOL, GLN_LEVERAGE, GLN_AMOUNT = range(3)
SIG_MARGIN, SIG_LEVERAGE = range(10, 12)

# States for New Trade Wizard
WIZ_MARKET, WIZ_SYMBOL, WIZ_SIDE, WIZ_MARGIN, WIZ_LEVERAGE, WIZ_TYPE, WIZ_CONFIRM = range(20, 27)
WIZ_CUSTOM_SYMBOL, WIZ_CUSTOM_MARGIN = range(27, 29)



class TradingBot:
    def __init__(self, bot_token, api_key, secret, passphrase=None):
        self.bot_token = bot_token
        self.active_strategies: dict[str, Any] = {}
        self.app = None # Placeholder for Telegram application
        
        exchange_type = getattr(config, 'EXCHANGE_TYPE', 'coinex')
        logger.info(f"INITIALIZING BOT FOR EXCHANGE: {exchange_type.upper()}")

        if exchange_type == 'kucoin':
            # KuCoin Spot
            self.spot_exchange = ccxt.kucoin({
                'apiKey': api_key,
                'secret': secret,
                'password': passphrase,
                'enableRateLimit': True,
                'options': {'defaultType': 'spot'}
            })
            # KuCoin Futures (Swap)
            self.futures_exchange = ccxt.kucoin({
                'apiKey': api_key,
                'secret': secret,
                'password': passphrase,
                'enableRateLimit': True,
                'options': {'defaultType': 'swap'}
            })
        else:
            # CoinEx Spot (Default)
            self.spot_exchange = ccxt.coinex({
                'apiKey': api_key,
                'secret': secret,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'spot',
                    'createMarketBuyOrderRequiresPrice': False
                }
            })
            # CoinEx Futures
            self.futures_exchange = ccxt.coinex({
                'apiKey': api_key,
                'secret': secret,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'swap',
                    'createMarketBuyOrderRequiresPrice': False
                }
            })
        
        self.active_strategies = {}
        self.db_manager = DatabaseManager()
        
        # Step 3 Architecture: Position Tracker
        self.position_tracker = PositionTracker(self.spot_exchange, self.futures_exchange, self.db_manager)
        self.risk_engine = RiskEngine(self.db_manager, position_tracker=self.position_tracker)
        
        self.execution_engine = ExecutionEngine(
            self.spot_exchange, self.futures_exchange, self.risk_engine, 
            position_tracker=self.position_tracker
        )
        
        # Load Admin ID
        ra = self.db_manager.load_config('admin_id')
        self.admin_id = int(ra) if ra else None
        
        self.signal_cache = {} # Cache for interactive signals
        self.signal_counter = 1
        
        # Runtime Environment Detection
        self.run_env = "LOCAL"
        self.hostname = "Unknown"
        self.username = "Unknown"
        self.start_time = datetime.now()
        self.detect_env()

    def detect_env(self):
        """Detects the execution environment (VPS/LOCAL/IDE)."""
        import socket
        import getpass
        try:
            self.hostname = socket.gethostname().upper()
            self.username = getpass.getuser().lower()
            
            # Identify environment
            if "VPS" in self.hostname or self.hostname.startswith("IONOS") or "strato" in self.hostname.lower():
                self.run_env = "VPS"
            elif "behza" in self.username or "desktop" in self.hostname.lower():
                self.run_env = "LOCAL"
            elif os.getenv("CORTEX_ENV") or os.getenv("GITHUB_ACTIONS"):
                self.run_env = "IDE/CI"
            else:
                self.run_env = "LOCAL" # Default
                
            logger.info(f"ENV: Detected environment as {self.run_env} (Host: {self.hostname}, User: {self.username})")
        except Exception as e:
            logger.error(f"ENV: Detection failed: {e}")

        # Scheduler task reference
        self.scheduler_task = None
        self.gln_strategies = {} # Active GLN instances
        self.user_callback_locks = {} # Lock for race condition protection
        # self.load_active_strategies() # Moved to async init

    async def load_active_strategies(self):
        await asyncio.sleep(2) # Guard: wait for bot to fully initialize
        strategies = self.db_manager.load_strategies()
        logger.info(f"Loading {len(strategies)} active strategies from database...")
        
        for data in strategies:
            symbol = data['symbol']
            market_type = data['market_type']
            side = data['side']
            amount = data['amount']
            leverage = data['leverage']
            strategy_id = str(data['id']) # Ensure ID is string for consistency
            state = data['state']
            
            exchange = self.futures_exchange if market_type == 'future' else self.spot_exchange
        
            # Extract user_id for notification callback
            # strategy_id format: USERID_SYMBOL...
            user_id_str = strategy_id.split('_')[0]
            
            async def notification_callback(msg, *args, **kwargs):
                try:
                    # Guard: ensure bot is initialized before use
                    if hasattr(self, 'app') and self.app and self.app.bot:
                         await self.app.bot.send_message(chat_id=int(user_id_str), text=msg, **kwargs)
                except Exception as e:
                    logger.error(f"Failed to send restored notification: {e}")

            strategy = SpiderStrategy(
                self.execution_engine, symbol, amount, side, market_type, leverage, 
                db_manager=self.db_manager, strategy_id=strategy_id, 
                message_callback=notification_callback,
                position_tracker=self.position_tracker
            )
            
            # Restore state
            strategy.positions = state['positions']
            strategy.total_invested = state['total_invested']
            strategy.step_count = state['step_count']
            strategy.current_step = state.get('current_step', 0)
            strategy.last_buy_price = state['last_buy_price']
            strategy.avg_price = state['avg_price']
            strategy.total_volume = state['total_volume']
            strategy.trailing_stop_active = state.get('trailing_stop_active', False)
            strategy.highest_price = state.get('highest_price', 0)
            strategy.current_stop_loss = state.get('current_stop_loss', 0)
            strategy.last_pyramid_price = state.get('last_pyramid_price', 0)
            strategy.use_martingale = state.get('use_martingale', True)
            strategy.sl_order_id = state.get('sl_order_id')
            
            self.active_strategies[strategy_id] = strategy
            asyncio.create_task(self.run_strategy(strategy, None, strategy_id))

    # Old start_command removed in favor of the new one at the bottom
    # async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    #     ...

    async def spot_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.db_manager.get_setting('bot_mode', 'CRYPTO') == 'FOREX':
            await update.effective_message.reply_text("❌ این دستور مخصوص حالت CRYPTO است.")
            return
        try:
            if len(context.args) < 2:
                await update.effective_message.reply_text("فرمت: /spot SYMBOL AMOUNT")
                return

            symbol = context.args[0].upper()
            amount = float(context.args[1])
            
            if amount < 5:
                await update.effective_message.reply_text("⚠️ حداقل مبلغ برای معامله 5$ است.")
                return

            # Execution Engine Check & Execute
            user_id = update.effective_user.id
            req = TradeRequest(symbol=symbol, amount=amount, leverage=1, side='buy', market_type='spot', user_id=user_id)
            res = await self.execution_engine.execute(req)
            if not res.success:
                await update.effective_message.reply_text(res.message)
                return

            key = f"{user_id}_{symbol}_SPOT"
            
            if key in self.active_strategies:
                await update.effective_message.reply_text("⚠️ استراتژی اسپایدر روی این ارز در حال حاضر فعال است.")
                return

            strategy = SpiderStrategy(self.execution_engine, symbol, amount, 'buy', 'spot', 1, self.db_manager, key)
            self.active_strategies[key] = strategy
            asyncio.create_task(self.run_strategy(strategy, update, key))
            
            await update.effective_message.reply_text(f"✅ ربات اسپات برای {symbol} با مبلغ {amount} دلار فعال شد.")

        except Exception as e:
            await update.effective_message.reply_text(f"❌ خطا: {e}")

    async def future_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.db_manager.get_setting('bot_mode', 'CRYPTO') == 'FOREX':
            await update.effective_message.reply_text("❌ این دستور مخصوص حالت CRYPTO است.")
            return
        try:
            if len(context.args) < 2:
                await update.effective_message.reply_text("فرمت: /future SYMBOL AMOUNT [LEVERAGE]")
                return

            symbol = context.args[0].upper()
            amount = float(context.args[1])
            try:
                leverage = int(context.args[2]) if len(context.args) > 2 else 5
                if leverage < 1 or leverage > 100:
                    await update.effective_message.reply_text("⚠️ اهرم باید بین 1 تا 100 باشد.")
                    return
            except ValueError:
                await update.effective_message.reply_text("⚠️ اهرم باید یک عدد صحیح باشد.")
                return
            
            if amount < 5:
                await update.effective_message.reply_text("⚠️ حداقل مبلغ برای معامله 5$ است.")
                return

            # تبدیل نماد به فرمت فیوچرز (BTCUSDT -> BTC/USDT:USDT)
            futures_symbol = symbol.replace('USDT', '/USDT:USDT')

            # Execution Engine Check & Execute
            user_id = update.effective_user.id
            req = TradeRequest(symbol=futures_symbol, amount=amount, leverage=leverage, side='buy', market_type='future', user_id=user_id)
            res = await self.execution_engine.execute(req)
            if not res.success:
                await update.effective_message.reply_text(res.message)
                return

            key = f"{user_id}_{symbol}_FUTURE"
            
            if key in self.active_strategies:
                await update.effective_message.reply_text("⚠️ استراتژی اسپایدر روی این ارز در حال حاضر فعال است.")
                return

            strategy = SpiderStrategy(self.execution_engine, futures_symbol, amount, 'buy', 'future', leverage, self.db_manager, key)
            self.active_strategies[key] = strategy
            asyncio.create_task(self.run_strategy(strategy, update, key))
            
            await update.effective_message.reply_text(f"✅ ربات فیوچرز برای {symbol} با اهرم {leverage}x فعال شد.")

        except Exception as e:
            await update.effective_message.reply_text(f"❌ خطا: {e}")

    async def run_strategy(self, strategy, update, key):
        stop_reason = None
        try:
            await strategy.initialize()
            
            while strategy.running:
                await strategy.check_market()
                await asyncio.sleep(10) # هر 10 ثانیه چک کن
        except Exception as e:
            logger.error(f"Strategy Runtime Error: {e}")
            stop_reason = str(e)
        finally:
            if key in self.active_strategies:
                del self.active_strategies[key]
            
            if update:
                try:
                    reason_msg = f"\n❌ علت: {stop_reason}" if stop_reason else ""
                    await update.effective_message.reply_text(f"🛑 ربات برای {strategy.symbol} متوقف شد.{reason_msg}")
                except:
                    pass

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show status of active strategies based on current Bot Mode."""
        mode = self.db_manager.get_setting('bot_mode', 'CRYPTO')
        
        # Filter strategies based on mode
        filtered_strategies = {}
        
        for key, strategy in self.active_strategies.items():
            strat_type = type(strategy).__name__
            market_type = getattr(strategy, 'market_type', 'unknown')
            
            is_crypto = market_type in ['spot', 'future', 'futures']
            is_forex = 'Forex' in strat_type or market_type == 'forex'

            if mode == 'CRYPTO':
                if is_crypto:
                    filtered_strategies[key] = strategy
            elif mode == 'FOREX':
                if is_forex:
                    filtered_strategies[key] = strategy

        msg = f"📊 **وضعیت ربات‌های فعال ({mode}):**\n\n"
        keyboard = []
        
        if not filtered_strategies:
            msg += "💤 هیچ رباتی در این حالت فعال نیست.\n"
        else:
            for key, strategy in filtered_strategies.items():
                try:
                    status_text = await strategy.get_status()
                    msg += f"{status_text}\n-------------------\n"
                    
                    # Add buttons for this strategy
                    symbol = getattr(strategy, 'symbol', 'Unknown')
                    safe_symbol = symbol.replace("/", "_").replace(":", "")
                    
                    row = [
                        InlineKeyboardButton(f"❌ بستن {symbol}", callback_data=f"close_{key}"),
                        InlineKeyboardButton(f"🎰 تغییر اهرم", callback_data=f"editlev_{key}")
                    ]
                    keyboard.append(row)
                except Exception as e:
                    msg += f"⚠️ Error getting status for {key}: {e}\n"
        
        keyboard.append([InlineKeyboardButton("🔙 برگشت به پنل", callback_data="switch_mode")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.effective_message.reply_text(msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.effective_message.reply_text("فرمت: /stop SYMBOL")
            return
            
        symbol_query = context.args[0].upper()
        stopped = False
        
        for key in list(self.active_strategies.keys()):
            if symbol_query in key:
                strategy = self.active_strategies[key]
                strategy.running = False
                stopped = True
                await update.effective_message.reply_text(f"درخواست توقف برای {strategy.symbol} ارسال شد...")

        if not stopped:
            await update.effective_message.reply_text("❌ ربات فعالی با این مشخصات پیدا نشد.")

    async def positions_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Displays actual open positions from Exchange (Crypto) or Broker (Forex)."""
        logger.info("DEBUG: /positions command invoked")
        mode = self.db_manager.get_setting('bot_mode', 'CRYPTO')
        logger.info(f"DEBUG: Current mode for positions: {mode}")
        
        try:
            if mode == 'CRYPTO':
                await self._show_crypto_positions(update, context)
            else:
                await self._show_forex_positions(update, context)
        except Exception as e:
            logger.error(f"Error in positions command: {e}")
            await update.effective_message.reply_text(f"❌ Error: {e}")

    async def _show_crypto_positions(self, update, context):
        msg = "🏦 **Crypto Open Positions (CoinEx):**\n\n"
        has_pos = False
        keyboard = []
        
        await self.position_tracker.sync(force=True)
        
        # 1. SPOT Balances
        spot_positions = self.position_tracker.get_positions(market_type='spot')
        if spot_positions:
            msg += "🔵 <b>SPOT Holdings:</b>\n"
            for p in spot_positions:
                curr = p['symbol'].split('/')[0]
                val = p['amount']
                msg += f"- {curr}: {val:.4f}\n"
                has_pos = True
                keyboard.append([InlineKeyboardButton(f"💰 Sell All {curr}", callback_data=f"close_spot_{curr}")])
            msg += "\n"

        # 2. FUTURES Positions
        fut_positions = self.position_tracker.get_positions(market_type='future')
        fut_orders = self.position_tracker.get_orders(market_type='future')
        
        try:
            if fut_positions or fut_orders:
                msg += "🟠 <b>FUTURES Positions/Orders:</b>\n"
            
            for p in fut_positions:
                symbol = p['symbol']
                side = p['side'].upper()
                leverage = p['leverage']
                amount = p['contracts']
                entry = p['entryPrice']
                pnl = p['unrealizedPnl']
                
                msg += f"- {symbol} ({side} {leverage}x)\n"
                msg += f"  Amt: {amount} | Entry: {entry}\n"
                msg += f"  PnL: {pnl} USDT\n\n"
                has_pos = True
                
                safe_symbol = symbol.replace("/", "_")
                keyboard.append([InlineKeyboardButton(f"❌ Close {symbol} ({side})", callback_data=f"close_pos_{safe_symbol}_{side}")])
            
            for o in fut_orders:
                if o.get('status') != 'open': continue
                symbol = o['symbol']
                side = o['side'].upper()
                stop_price = o.get('stopPrice')
                o_id = o['id']
                m = f"📍 <b>STOP ORDER</b> ({symbol})\n"
                m += f"  Side: {side} | Trigger: {stop_price}\n"
                m += f"  Type: {o['type'].upper()} | ID: {o_id}\n\n"
                msg += m
                has_pos = True
                
                # Button to cancel stop order
                keyboard.append([InlineKeyboardButton(f"🗑 Cancel Stop {symbol}", callback_data=f"cancel_order_{symbol}_{o_id}")])

            # Cleanup
            msg += "\n"
        except Exception as e:
            logger.error(f"Error fetching future positions: {e}")

        if not has_pos:
            msg += "No open positions found."
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.effective_message.reply_text(msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

    async def _show_forex_positions(self, update, context):
        if not MT5_AVAILABLE:
            await update.effective_message.reply_text("❌ MetaTrader5 نصب نیست. این قابلیت فقط روی Windows با MT5 کار میکنه.")
            return
        
        try:
            if not mt5.initialize():
                await update.effective_message.reply_text("❌ اتصال به MetaTrader 5 ناموفق بود. لطفاً MT5 رو باز کنید.")
                return
        except Exception as e:
            await update.effective_message.reply_text(f"❌ خطا در اتصال به MT5: {e}")
            return

        msg = "🌍 **Forex Open Positions (MetaTrader 5):**\n\n"
        positions = mt5.positions_get()
        has_pos = False
        keyboard = []

        if positions:
            for pos in positions:
                symbol = pos.symbol
                type_str = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"
                vol = pos.volume
                price = pos.price_open
                profit = pos.profit
                ticket = pos.ticket
                
                msg += f"- <b>{symbol}</b> ({type_str})\n"
                msg += f"  Vol: {vol} | Open: {price:.5f}\n"
                msg += f"  Profit: {profit} USD\n\n"
                has_pos = True
                
                # Close button for MT5 position (using ticket)
                keyboard.append([InlineKeyboardButton(f"❌ Close {symbol} #{ticket}", callback_data=f"close_forex_{ticket}")])
        else:
             msg += "No open positions found in MT5."

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.effective_message.reply_text(msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

    async def close_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """بستن آنی یک پوزیشن در صرافی"""
        # /close BTCUSDT FUTURE
        # /close BTCUSDT SPOT
        try:
            if len(context.args) < 2:
                await update.effective_message.reply_text("فرمت: /close SYMBOL TYPE\nمثال: /close BTCUSDT FUTURE")
                return
            
            symbol_raw = context.args[0].upper()
            market_type = context.args[1].upper() # SPOT or FUTURE
            
            await update.effective_message.reply_text(f"⚠️ در حال تلاش برای بستن {symbol_raw} ({market_type})...")
            
            if 'FUTURE' in market_type:
                # بستن فیوچرز
                symbol = symbol_raw.replace('USDT', '/USDT:USDT')
                # Fix: Use async_run for fetch_positions (sync exchange)
                positions = await asyncio.to_thread(self.futures_exchange.fetch_positions)
                target_pos = None
                for p in positions:
                    if p['symbol'] == symbol and float(p.get('contracts', 0) or 1) != 0:
                        target_pos = p
                        break
                
                if target_pos:
                    side = 'buy' if target_pos['side'] == 'short' else 'sell'
                    amount = float(target_pos.get('contracts', 0) or 1)
                    # Fix: Use async_run for create_order
                    await asyncio.to_thread(self.futures_exchange.create_order, symbol, 'market', side, amount)
                    await update.effective_message.reply_text(f"✅ پوزیشن فیوچرز {symbol} بسته شد.")
                    asyncio.create_task(self.take_equity_snapshot())
                else:
                    await update.effective_message.reply_text(f"❌ پوزیشن بازی برای {symbol} پیدا نشد.")

            elif 'SPOT' in market_type:
                # فروش کل دارایی اسپات
                # Fix: Use async_run for fetch_balance
                balance = await asyncio.to_thread(self.spot_exchange.fetch_balance)
                base_currency = symbol_raw.replace('USDT', '')
                if base_currency in balance.get('free', {}) and balance['free'][base_currency] > 0:
                    amount = balance['free'][base_currency]
                    # Fix: Use async_run for create_order
                    await asyncio.to_thread(self.spot_exchange.create_order, symbol_raw, 'market', 'sell', amount)
                    await update.effective_message.reply_text(f"✅ دارایی اسپات {base_currency} ({amount}) فروخته شد.")
                    asyncio.create_task(self.take_equity_snapshot())
                else:
                    msg = f"❌ موجودی {base_currency} صفر است."
                    await update.effective_message.reply_text(msg)
            
        except Exception as e:
            await update.effective_message.reply_text(f"❌ خطا در بستن: {e}")

    def parse_time_period(self, period_str):
        """تبدیل بازه زمانی به datetime آبجکت"""
        period_str = period_str.lower().strip()
        now = datetime.now()
        
        # Format: 1d, 7d, 30d
        match = re.match(r'^(\d+)([d])$', period_str)
        if match:
            num = int(match.group(1))
            return now - timedelta(days=num)
        
        if period_str == 'all':
            return now - timedelta(days=365) # Last year by default
            
        return now - timedelta(days=7) # Default

    async def pnl_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """گزارش سود و ضرر بر اساس تغییرات Equity"""
        # /pnl [1d|7d|30d|all] [eur]
        period = "7d"
        use_eur = False
        
        if context.args:
            for arg in context.args:
                arg_l = arg.lower()
                if arg_l in ['1d', '7d', '30d', 'all']:
                    period = arg_l
                if arg_l == 'eur':
                    use_eur = True

        await update.effective_message.reply_text(f"📊 در حال محاسبه PnL ({period})...")
        
        # 1. Get Current Equity
        # Trigger a fresh snapshot for precision
        snap_now = await self.take_equity_snapshot()
        if not snap_now:
            await update.effective_message.reply_text("❌ خطا در دریافت موجودی لحظه‌ای.")
            return
            
        equity_now = snap_now['total']
        spot_now = snap_now['spot']
        futures_now = snap_now['futures']
        unrealized_now = snap_now['unrealized']
        
        # 2. Get Start Equity
        equity_start = equity_now
        snap_start = None
        
        if period != 'all':
            since_dt = self.parse_time_period(period)
            snap_start = self.db_manager.get_equity_at_time(since_dt.strftime('%Y-%m-%d %H:%M:%S'))
        else:
            # Get the very first snapshot ever
            snaps = self.db_manager.get_equity_snapshots(limit=1000) # Get all roughly
            if snaps: snap_start = snaps[-1]

        if snap_start:
            equity_start = snap_start[0]
            start_time = snap_start[5]
        else:
            # Fallback to current balance if no snapshot found
            equity_start = float(self.db_manager.get_setting('current_balance', equity_now))
            start_time = "نامشخص (اولین گزارش)"

        # 3. Calculate PnL
        # PnL = Equity_Now - Equity_Start - Net_Deposits
        # For now net_deposits is 0 unless we implement deposit tracking
        net_deposits = 0 
        total_pnl = float(equity_now) - float(equity_start) - net_deposits
        pnl_percent = (total_pnl / equity_start * 100) if equity_start > 0 else 0
        
        # 4. Breakdown (Fees & Funding)
        breakdown = {'fees': 0.0, 'funding': 0.0}
        if period != 'all':
            since_ms = int(since_dt.timestamp() * 1000)
            breakdown = await self.position_tracker.get_pnl_breakdown(since_ms)

        # 5. EUR Conversion
        rate = 1.0
        currency = "USDT"
        if use_eur:
            try:
                ticker = await asyncio.to_thread(self.spot_exchange.fetch_ticker, 'EUR/USDT')
                rate = 1.0 / (float(ticker.get('last') or 0) or 1.06)
                currency = "EUR"
            except:
                rate = 0.94 # Hardcoded fallback
                currency = "EUR"

        # 6. Format Message
        emoji = "🟢" if total_pnl >= 0 else "🔴"
        
        msg = (
            f"📈 **گزارش سود و ضرر ({period})**\n"
            f"⏱ شروع: `{start_time}`\n\n"
            f"💰 **موجودی کل:** `{equity_now * rate:.2f} {currency}`\n"
            f"   ▫️ اسپات: `{spot_now * rate:.2f}`\n"
            f"   ▫️ فیوچرز: `{futures_now * rate:.2f}`\n\n"
            f"📊 **تغییرات کل (Equity-based):**\n"
            f"   ▫️ مقدار: `{total_pnl * rate:+.2f} {currency}` {emoji}\n"
            f"   ▫️ درصد: `{pnl_percent:+.2f}%`\n\n"
            f"💸 **جزئیات معاملات:**\n"
            f"   ▫️ کارمزدها: `{breakdown['fees'] * rate:.2f} {currency}`\n"
            f"   ▫️ فاندینگ: `{breakdown['funding'] * rate:+.2f} {currency}`\n\n"
            f"🕒 **سود/ضرر باز (Unrealized):**\n"
            f"   ▫️ `{unrealized_now * rate:+.2f} {currency}`\n\n"
            f"💡 *نکته: این گزارش بر اساس تغییر کل ارزش دارایی‌های شما (Equity) محاسبه شده است.*"
        )
        
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            spot_bal = await asyncio.to_thread(self.spot_exchange.fetch_balance)
            futures_bal = await asyncio.to_thread(self.futures_exchange.fetch_balance)
            
            spot_usdt = spot_bal.get('USDT', {}).get('free', 0)
            futures_usdt = futures_bal.get('USDT', {}).get('free', 0)
            futures_used = futures_bal.get('USDT', {}).get('used', 0)
            futures_total = futures_bal.get('USDT', {}).get('total', 0)
            
            msg = (
                "💰 **گزارش موجودی (USDT):**\n\n"
                f"🔵 **Spot Free:** `{spot_usdt:.2f}` USDT\n"
                f"🟠 **Futures Free:** `{futures_usdt:.2f}` USDT\n"
                f"🔒 **Futures Locked:** `{futures_used:.2f}` USDT\n"
                f"📈 **Futures Total:** `{futures_total:.2f}` USDT\n\n"
                "💡 اگر موجودی Futures Free کمتر از مبلغ معامله (ضربدر اهرم) باشد، معامله باز نخواهد شد."
            )
            await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.effective_message.reply_text(f"❌ خطا در دریافت موجودی: {e}")

    async def ping_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"DEBUG: /ping called by user {update.effective_user.id}")
        await update.effective_message.reply_text("🏓 Pong! Bot is alive.")

    async def where_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Displays current execution environment and uptime (Persian)."""
        uptime = datetime.now() - self.start_time
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        
        uptime_str = f"{days} روز، {hours} ساعت و {minutes} دقیقه" if days > 0 else f"{hours} ساعت و {minutes} دقیقه"
        
        env_labels = {
            "VPS": "🚀 سرور مجازی (VPS)",
            "LOCAL": "💻 لپ‌تاپ شخصی (Local)",
            "IDE/CI": "🛠 محیط توسعه (IDE/Antigravity)"
        }
        
        msg = (
            f"📍 **وضعیت فعلی محیط اجرا:**\n"
            f"━━━━━━━━━━━━━━\n"
            f"🏠 **محیط:** {env_labels.get(self.run_env, self.run_env)}\n"
            f"⏱ **زمان فعالیت (Uptime):** {uptime_str}\n"
            f"🖥 **هاست:** `{self.hostname}`\n"
            f"👤 **کاربر:** `{self.username}`\n"
            f"━━━━━━━━━━━━━━\n"
            f"✅ سیستم در وضعیت پایدار است."
        )
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)

    async def test_sig_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Sends a mock signal with trade button for testing."""
        user_id = update.effective_user.id
        logger.info(f"DEBUG: test_sig_command called by user {user_id}")
        
        # Ensure we have admin_id
        if not self.admin_id:
            await self.save_admin_id(update)
            logger.info(f"DEBUG: admin_id was empty, now saved from user {user_id}")
            
        if user_id != self.admin_id:
            logger.warning(f"DEBUG: Permission denied for user {user_id}. Admin is {self.admin_id}")
            await update.effective_message.reply_text(f"❌ عدم دسترسی. ID شما: `{user_id}`\nAdmin ID ثبت شده: `{self.admin_id}`")
            return
        
        test_data = {
            'symbol': 'BTC/USDT:USDT',
            'side': 'buy',
            'price': 95000.0,
            'sl': 94000.0,
            'tp': 98000.0,
            'margin': 10,
            'leverage': 5,
            'reason': "Test Signal (Manual)",
            'strategy_type': 'GLN'
        }
        
        msg = ("🧪 **سیگنال آزمایشی (Test)**\n"
               "این یک پیام تست برای بررسی دکمه‌ها و ویزارد است.\n"
               "🛠 **Debug: GLN-V2-Buffered**")
        
        await self.send_telegram_message(msg, signal_data=test_data)
        await update.effective_message.reply_text("✅ سیگنال تست ارسال شد. دکمه پایین پیام را فشار دهید تا ویزارد اجرا شود.")

    async def qstatus_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Displays status of all active GLN strategies."""
        user_id = update.effective_user.id
        logger.info(f"DEBUG: /qstatus called by user {user_id}. Current admin_id is {self.admin_id}")
        if user_id != self.admin_id:
            logger.warning(f"DEBUG: Permission denied for /qstatus. User {user_id} != Admin {self.admin_id}")
            await update.effective_message.reply_text(f"❌ عدم دسترسی به وضعیت ادمین. ID: {user_id}")
            return
            
        if not self.gln_strategies:
            await update.effective_message.reply_text("💤 هیچ استراتژی GLN فعالی یافت نشد.")
            return
            
        await update.effective_message.reply_text("🔍 در حال استعلام وضعیت استراتژی‌ها...")
        
        for sid, gln in self.gln_strategies.items():
            try:
                status_msg = await gln.get_status()
                await update.effective_message.reply_text(status_msg, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.error(f"Error getting status for {sid}: {e}")
                await update.effective_message.reply_text(f"❌ خطا در خواندن وضعیت `{sid}`: {e}")

    async def smart_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.save_admin_id(update)
        """تحلیل هوشمند و شروع معامله: /smart SYMBOL AMOUNT [LEVERAGE]"""
        if self.db_manager.get_setting('bot_mode', 'CRYPTO') == 'FOREX':
            await update.effective_message.reply_text("❌ دستور Smart فعلاً فقط برای کریپتو فعال است.")
            return

        user_id = update.effective_user.id
        logger.info(f"Smart command triggered by {user_id}: {context.args}")
        try:
            if len(context.args) < 2:
                await update.effective_message.reply_text("فرمت: /smart SYMBOL AMOUNT [LEVERAGE]\nمثال: /smart BTCUSDT 100 5x")
                return

            symbol = context.args[0].upper()
            amount = float(context.args[1])
            leverage_str = context.args[2].lower().replace('x', '') if len(context.args) > 2 else '5'
            
            try:
                leverage = int(leverage_str)
                if leverage < 1 or leverage > 100:
                    await update.effective_message.reply_text("⚠️ اهرم باید بین 1 تا 100 باشد.")
                    return
            except:
                leverage = 5

            if amount < 5:
                await update.effective_message.reply_text("⚠️ حداقل مبلغ کل برای معامله 5$ است.")
                return

            # --- MINIMUM MARGIN VALIDATION ---
            try:
                futures_inv_symbol = symbol.replace('USDT', '/USDT:USDT')
                market = self.futures_exchange.market(futures_inv_symbol)
                min_cost = market['limits']['cost']['min'] if market.get('limits') and market['limits'].get('cost') else 0
                min_amount = market['limits']['amount']['min'] if market.get('limits') and market['limits'].get('amount') else 0
                
                ticker = await asyncio.to_thread(self.futures_exchange.fetch_ticker, futures_inv_symbol)
                price = ticker['last']
                
                min_usdt_cost = min_cost if min_cost else (min_amount * price if min_amount else 2.0)
                min_margin = max(2.0, min_usdt_cost / leverage)
                
                # Smart Strategy uses Martingale (5% entry) by default
                effective_amount = amount * 0.05
                
                if effective_amount < min_margin:
                    min_total_required = min_margin / 0.05
                    await update.effective_message.reply_text(
                        f"⛔️ مبلغ وارد شده ({amount}$) برای استراتژی هوشمند کم است.\n"
                        f"🤖 در استراتژی هوشمند (Martingale)، پله اول ۵٪ کل سرمایه است.\n"
                        f"📉 حداقل مارجین پله اول باید **{min_margin:.2f}$** باشد.\n"
                        f"👈 لطفاً حداقل **{min_total_required:.1f}$** وارد کنید."
                    )
                    return
            except Exception as e:
                logger.error(f"Smart Validation Error: {e}")
                # Continue if validation fails (don't block)

            logger.info(f"Analyzing {symbol}...")
            await update.effective_message.reply_text(f"🧠 در حال آنالیز بازار {symbol}...")
            
            exchange = self.futures_exchange
            futures_symbol = symbol.replace('USDT', '/USDT:USDT')
            
            analyzer = MarketAnalyzer(exchange, futures_symbol)
            state, data = await analyzer.analyze()
            logger.info(f"Analysis result: {state}")
            
            if state == 'ERROR' or not data:
                await update.effective_message.reply_text("❌ خطا در آنالیز بازار. لطفاً دوباره تلاش کنید.")
                return

            msg = f"📊 **نتیجه آنالیز {symbol}:**\n"
            msg += f"• قیمت: {data['price']}\n"
            msg += f"• روند کلی (4H): **{state}**\n"
            msg += f"• آر‌اس‌آی (15M): {data['rsi_15m']:.2f}\n"
            msg += f"• EMA50 (4H): {data['ema50_4h']:.2f}\n"
            msg += f"• EMA200 (4H): {data['ema200_4h']:.2f}\n"
            msg += f"• ATR (4H): {data['atr_4h']:.2f}\n"
            
            # Volume Check
            vol_status = "✅ تایید (High)" if data['volume_confirmed'] else "⚠️ هشدار (Low)"
            msg += f"• حجم (15M): {vol_status}\n"
            
            # AI Prediction
            ai_price = data['ai_prediction']
            ai_signal = "NEUTRAL"
            if ai_price > data['price']:
                ai_signal = "BULLISH 📈"
            elif ai_price < data['price']:
                ai_signal = "BEARISH 📉"
                
            msg += f"• پیش‌بینی هوش مصنوعی: {ai_price:.2f} ({ai_signal})\n\n"
            
            side = 'BUY'
            market_type = 'future'

            if state == 'UPTREND':
                msg += "🚀 استراتژی: **روند صعودی (Trend Following Long)**\n"
                if data['rsi_15m'] < 30:
                    msg += "💎 تایید ورود: **RSI 15m < 30 (خرید در کف)**\n"
                
                if ai_price > data['price']:
                     msg += "🤖 تایید هوش مصنوعی: **تایید صعود (Strong Buy)**\n"
                
                msg += "پوزیشن: LONG (Buy)"
                side = 'BUY'
            
            elif state == 'DOWNTREND':
                msg += "📉 استراتژی: **روند نزولی (Trend Following Short)**\n"
                if data['rsi_15m'] > 70:
                    msg += "💎 تایید ورود: **RSI 15m > 70 (فروش در سقف)**\n"
                
                if ai_price < data['price']:
                     msg += "🤖 تایید هوش مصنوعی: **تایید نزول (Strong Sell)**\n"

                msg += "پوزیشن: SHORT (Sell)"
                side = 'SELL'
            
            else:
                msg += "↔️ استراتژی: **نوسان‌گیری (Range / Spider)**\n"
                msg += "پوزیشن: LONG (Accumulate)"
                side = 'BUY'

            # Risk Management Info
            atr_val = data['atr_4h']
            sl_dist = 2 * atr_val
            tp_dist = 4 * atr_val
            
            sl_price = data['price'] - sl_dist if side == 'BUY' else data['price'] + sl_dist
            tp_price = data['price'] + tp_dist if side == 'BUY' else data['price'] - tp_dist
            
            msg += f"\n🛡 **مدیریت ریسک هوشمند (ATR):**\n"
            msg += f"• حد ضرر (SL): {sl_price:.2f} (فاصله: {sl_dist:.0f}$)\n"
            msg += f"• حد سود (TP): {tp_price:.2f} (فاصله: {tp_dist:.0f}$)\n"
            msg += f"• تریلینگ استاپ: فعال (Chandelier Exit)\n"

            await update.effective_message.reply_text(msg)
            
            # Execution Engine Check & Execute (Centralized Risk + Execution)
            req = TradeRequest(symbol=futures_symbol, amount=amount, leverage=leverage, side=side.lower(), market_type=market_type, user_id=user_id)
            res = await self.execution_engine.execute(req)
            if not res.success:
                await update.effective_message.reply_text(res.message)
                return

            key = f"{user_id}_{symbol}_{market_type.upper()}_SMART"
            
            if key in self.active_strategies:
                await update.effective_message.reply_text("⚠️ یک معامله هوشمند روی این ارز فعال است.")
                return

            strategy = SpiderStrategy(
                self.execution_engine, 
                futures_symbol, 
                amount, 
                side.lower(),
                market_type=market_type,
                leverage=leverage,
                db_manager=self.db_manager,
                strategy_id=key,
                atr=data['atr_4h']
            )
            self.active_strategies[key] = strategy
            asyncio.create_task(self.run_strategy(strategy, update, key))
            
            await update.effective_message.reply_text(f"✅ ربات هوشمند فعال شد!\nمدیریت پوزیشن به صورت خودکار انجام خواهد شد.")

        except Exception as e:
            logger.error(f"Smart command error: {e}")
            await update.effective_message.reply_text(f"❌ خطا: {e}")

    async def dashboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            stats = self.db_manager.get_trade_stats()
            total_trades = stats['total_trades']
            total_pnl = stats['total_pnl']
            wins = stats['wins']
            win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
            
            # Active Positions PnL
            active_pnl = 0
            active_count = 0
            active_details = ""
            
            for strategy in self.active_strategies.values():
                try:
                    # Fix: Use async_run for fetch_ticker
                    ticker = await asyncio.to_thread(strategy.exchange.fetch_ticker, strategy.symbol)
                    current_price = ticker['last']
                    pnl = strategy.calculate_pnl(current_price)
                    active_pnl += pnl
                    active_count += 1
                    
                    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
                    active_details += f"{pnl_emoji} {strategy.symbol}: {pnl:.2f}$\n"
                except:
                    pass

            msg = "📊 **داشبورد عملکرد ربات**\n\n"
            msg += f"💰 **سود/ضرر کل (بسته شده):** {total_pnl:.2f} $\n"
            msg += f"📈 **وین ریت:** {win_rate:.1f}% ({wins}/{total_trades})\n"
            msg += f"🔄 **تعداد کل معاملات:** {total_trades}\n\n"
            
            msg += f"🔓 **پوزیشن‌های باز ({active_count}):**\n"
            if active_count > 0:
                msg += f"سود/ضرر لحظه‌ای: {active_pnl:.2f} $\n"
                msg += "------------------\n"
                msg += active_details
            else:
                msg += "هیچ پوزیشن بازی وجود ندارد.\n"
                
            await update.effective_message.reply_text(msg)
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            await update.effective_message.reply_text(f"خطا در دریافت اطلاعات داشبورد: {e}")

    async def scan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.save_admin_id(update)
        """اسکن بازار هوشمند (Phase 22.6): /scan [LIMIT] [ALL]"""
        if self.db_manager.get_setting('bot_mode', 'CRYPTO') == 'FOREX':
            await update.effective_message.reply_text("❌ اسکنر هنوز برای فارکس فعال نیست. (مخصوص کریپتو)")
            return

        # 1. INITIALIZATION (Critical for UnboundLocalError protection)
        limit = 30
        show_all = False
        opportunities_strong = []
        opportunities_medium = []
        overview = []
        stats = {"total": 0, "success": 0, "error": 0, "errors_reasons": {}, "trends": {"UPTREND": 0, "DOWNTREND": 0, "RANGE": 0, "UNKNOWN": 0}}
        
        try:
            for arg in context.args:
                if arg.lower() == 'all': show_all = True
                elif arg.isdigit(): limit = int(arg)

            status_msg = await update.effective_message.reply_text(f"🔍 در حال اسکن {limit} ارز برتر بازار... ⏳")
            
            # Fetch tickers
            tickers = await asyncio.to_thread(self.futures_exchange.fetch_tickers)
            usdt_pairs = [s for s, d in tickers.items() if '/USDT' in s and (d.get('quoteVolume') or d.get('baseVolume'))]
            sorted_pairs = sorted(usdt_pairs, key=lambda s: tickers[s].get('quoteVolume') or tickers[s].get('baseVolume') or 0, reverse=True)[:limit]
            
            stats["total"] = len(sorted_pairs)

            for symbol in sorted_pairs:
                try:
                    if 'USDC' in symbol or 'USDT' in symbol.split('/')[0]: continue
                    
                    analyzer = MarketAnalyzer(self.futures_exchange, symbol)
                    state, data, reason = await analyzer.analyze()
                    
                    raw_symbol = symbol.split(':')[0].replace('/', '')
                    status_emoji = '📉' if state == 'DOWNTREND' else '📈' if state == 'UPTREND' else '➖'
                    
                    if state in ['UPTREND', 'DOWNTREND', 'RANGE']:
                        stats["success"] += 1
                        stats["trends"][state] += 1
                        
                        # Data guaranteed safe here
                        price = data['price']
                        rsi = data['rsi_15m']
                        prediction = data['ai_prediction']
                        confidence = data['ai_confidence']
                        
                        # RANKING SCORE: (Confidence * 50) + (Abs diff % * 50)
                        price_diff_pct = abs(prediction - price) / price * 100
                        score = (confidence * 50) + (min(10, price_diff_pct) * 5) # Max 50 points for diff

                        # TIERED LOGIC
                        is_strong = False
                        is_medium = False
                        
                        if state == 'UPTREND':
                            if rsi < 45 and prediction > price and confidence > 0.60: is_strong = True
                            elif rsi < 52 and prediction > (price * 0.998) and confidence > 0.35: is_medium = True
                        elif state == 'DOWNTREND':
                            if rsi > 55 and prediction < price and confidence > 0.60: is_strong = True
                            elif rsi > 48 and prediction < (price * 1.002) and confidence > 0.35: is_medium = True

                        # Format Opportunity
                        opp_text = (
                            f"**{symbol}** | `{price}`\n"
                            f"{'🟢 LONG' if state == 'UPTREND' else '🔴 SHORT'} | RSI: {rsi:.1f} | AI: {int(confidence*100)}%\n"
                            f"🎯 Target (AI): `{prediction:.6f}` | Score: `{score:.1f}`"
                        )
                        
                        # Generate Keyboard for this opportunity
                        kb = []
                        side_btn = "buy" if state == 'UPTREND' else "sell"
                        side_label = "Long 🟢" if state == 'UPTREND' else "Short 🔴"
                        kb.append([InlineKeyboardButton(side_label, callback_data=f"wizard_margin_{side_btn}_{symbol}")])
                        markup = InlineKeyboardMarkup(kb)

                        if is_strong:
                            opportunities_strong.append((score, opp_text, markup))
                        elif is_medium:
                            opportunities_medium.append((score, opp_text, markup))
                            
                        # Always add to overview
                        overview.append({
                            'symbol': symbol, 'raw_symbol': raw_symbol, 'state': state, 
                            'rsi': rsi, 'ai': int(confidence*100), 'emoji': status_emoji, 'score': score
                        })
                    else:
                        stats["error"] += 1
                        stats["trends"]["UNKNOWN"] += 1
                        stats["errors_reasons"][reason] = stats["errors_reasons"].get(reason, 0) + 1
                        overview.append({
                            'symbol': symbol, 'raw_symbol': raw_symbol, 'state': 'UNKNOWN', 
                            'rsi': 0, 'ai': 0, 'emoji': '❌', 'score': 0, 'reason': reason
                        })

                except Exception as e:
                    logger.error(f"Scan Loop Error ({symbol}): {e}")
                    stats["error"] += 1
                    continue

            # 2. OUTPUT GENERATION
            # Sort by score
            opportunities_strong.sort(key=lambda x: x[0], reverse=True)
            opportunities_medium.sort(key=lambda x: x[0], reverse=True)
            overview.sort(key=lambda x: x['score'], reverse=True)

            final_msg = "🔭 **گزارش اسکنر هوشمند Spider**\n━━━━━━━━━━━━━━\n\n"
            
            if opportunities_strong:
                final_msg += "🔥 **سیگنال‌های Sniper (Strong):**\n"
                for _, text, _ in opportunities_strong[:3]: # Show top 3 texts
                    final_msg += f"{text}\n\n"
                final_msg += "━━━━━━━━━━━━━━\n"
            
            if opportunities_medium:
                final_msg += "⚠️ **سیگنال‌های کاندیدا (Medium):**\n"
                for _, text, _ in opportunities_medium[:3]:
                    final_msg += f"{text}\n\n"
                final_msg += "━━━━━━━━━━━━━━\n"

            if not opportunities_strong and not opportunities_medium:
                final_msg += "🤷‍♂️ سیگنال خرید/فروش قطعی پیدا نشد.\n\n"

            # Overview Hint
            final_msg += "📊 **برترین‌های بازار (Overview):**\n"
            for item in overview[:10]:
                final_msg += f"{item['emoji']} `{item['raw_symbol']}`: Trend {item['state']} | RSI: {item['rsi']:.1f} | AI: {item['ai']}%\n"
            
            # Diagnostics Summary
            final_msg += (
                f"\n━━━━━━━━━━━━━━\n"
                f"📋 **آمـار اسکن:**\n"
                f"🔹 کل ارزها: `{stats['total']}` | ✅ موفق: `{stats['success']}`\n"
                f"❌ خطا/ناشناخته: `{stats['error']}`\n"
            )
            
            if stats["errors_reasons"]:
                top_reason = max(stats["errors_reasons"], key=stats["errors_reasons"].get)
                final_msg += f"⚠️ دلیل اصلی خطا: `{top_reason}`\n"

            # UI Buttons
            # Generate combined keyboard for top opportunities + navigation
            kb_final = []
            # Add buttons for top 4 opportunities (Strong then Medium)
            btns = []
            for _, _, markup in (opportunities_strong + opportunities_medium)[:4]:
                btns.append(markup.inline_keyboard[0][0])
                if len(btns) == 2:
                    kb_final.append(btns)
                    btns = []
            if btns: kb_final.append(btns)
            
            # Navigation row
            kb_final.append([
                InlineKeyboardButton("🔄 بروزرسانی", callback_data="scan_refresh"),
                InlineKeyboardButton("🔙 بازگشت", callback_data="switch_mode")
            ])
            
            await status_msg.edit_text(final_msg, reply_markup=InlineKeyboardMarkup(kb_final), parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"Global Scan Error: {e}")
            await update.effective_message.reply_text(f"❌ خطای کلی در اسکنر: {e}\nلطفاً از /clear استفاده کنید.")

        except Exception as e:
            logger.error(f"Scan error: {e}")
            await update.effective_message.reply_text(f"❌ خطا در اسکن: {e}")
    async def _start_snipe(self, update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str, side: str, amount: float = 11.0, leverage: int = 5):
        """Helper to start a snipe strategy (Hedge Mode compliant)"""
        try:
            user_id = update.effective_user.id
            raw_symbol = symbol.split(':')[0].replace('/', '') # Simplify for key
            
            # Risk Engine Check
            req = TradeRequest(symbol=symbol, amount=amount, leverage=leverage, side=side, market_type='future', user_id=user_id)
            is_valid, msg = self.risk_engine.validate(req)
            if not is_valid:
                await update.effective_message.reply_text(msg)
                return

            # --- MINIMUM MARGIN VALIDATION ---
            try:
                min_margin = 2.0
                market = self.futures_exchange.market(symbol)
                min_cost = market['limits']['cost']['min'] if market.get('limits') and market['limits'].get('cost') else 0
                min_amount = market['limits']['amount']['min'] if market.get('limits') and market['limits'].get('amount') else 0
                
                # Fetch price if needed for amount-based min
                ticker = await asyncio.to_thread(self.futures_exchange.fetch_ticker, symbol)
                price = ticker['last']
                
                min_usdt_cost = min_cost if min_cost else (min_amount * price if min_amount else 2.0)
                min_margin = max(2.0, min_usdt_cost / leverage)
                
                if amount < min_margin:
                    await update.effective_message.reply_text(
                        f"⛔️ مقدار وارد شده ({amount}$) کمتر از حد مجاز است.\n"
                        f"📉 حداقل مارجین برای اهرم {leverage}x باید **{min_margin:.2f}$** باشد.\n"
                        f"لطفاً مقدار بیشتری وارد کنید."
                    )
                    return
            except Exception as e:
                logger.error(f"Validation error: {e}")
            
            # UNIQUE KEY: Includes SIDE to allow Hedge Mode (Long & Short simultaneously)
            # BUT: CoinEx doesn't support Hedge Mode yet. So we must enforce One-Way.
            current_key = f"{user_id}_{raw_symbol}_{side}_SNIPE"
            
            # Check for Opposing Strategy
            opp_side = 'sell' if side == 'buy' else 'buy'
            opp_key = f"{user_id}_{raw_symbol}_{opp_side}_SNIPE"
            
            if opp_key in self.active_strategies:
                 await update.effective_message.reply_text(
                     f"⚠️ هشدار: شما یک پوزیشن **{opp_side.upper()}** روی این ارز دارید.\n"
                     f"⛔ در صرافی CoinEx امکان باز کردن همزمان Long و Short (Hedge Mode) وجود ندارد.\n"
                     f"لطفاً ابتدا پوزیشن قبلی را ببندید (/close {symbol.split(':')[0]} FUTURE)."
                 )
                 return

            if current_key in self.active_strategies:
                await update.effective_message.reply_text(f"⚠️ ربات {side.upper()} برای {symbol} قبلاً فعال شده است.")
                return

            # Execution Engine Check & Execute (Centralized Risk + Execution)
            req = TradeRequest(symbol=symbol, amount=amount, leverage=leverage, side=side, market_type='future', user_id=user_id)
            res = await self.execution_engine.execute(req)
            if not res.success:
                await update.effective_message.reply_text(res.message)
                return

            # Fetch fresh ATR for initial stop loss calculation
            # We need a quick analyzer instance just for data if not passed, 
            # but usually we want to start immediately. 
            # SpiderStrategy calculates its own initial params or we pass them.
            # Let's verify SpiderStrategy init. It doesn't take 'atr' in init args in the original code? 
            # Wait, previous `snipe_command` passed `atr=data['atr_4h']`.
            # I should probably quickly fetch ATR or default it.
            
            analyzer = MarketAnalyzer(self.futures_exchange, symbol)
            # Quick analyze for ATR
            _, data = await analyzer.analyze()
            atr_val = data['atr_4h'] if data else None

            # Helper Callback for notifications
            # capture user_id and context.bot
            bot_instance = context.bot
            async def notification_callback(msg, *args, **kwargs):
                try:
                    await bot_instance.send_message(chat_id=user_id, text=msg, **kwargs)
                except Exception as e:
                    logger.error(f"Failed to send notification: {e}")

            strategy = SpiderStrategy(
                self.execution_engine, symbol, amount, side, 'future', leverage, 
                self.db_manager, current_key, atr=atr_val, use_martingale=False,
                message_callback=notification_callback
            )
            
            self.active_strategies[current_key] = strategy
            asyncio.create_task(self.run_strategy(strategy, update, current_key))
            
            emoji = "🟢 LONG" if side == 'buy' else "🔴 SHORT"
            await update.effective_message.reply_text(
                f"🔫 **شکار آغاز شد!** (Sniper Mode)\n"
                f"{emoji} {symbol}\n"
                f"💰 حجم: {amount}$\n"
                f"🎰 اهرم: {leverage}x\n"
                f"🆔 شناسه: `{current_key}`"
            )
        except Exception as e:
            logger.error(f"Snipe Start Error: {e}")
            await update.effective_message.reply_text(f"❌ خطا در شروع اسنایپ: {e}")

    async def snipe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.save_admin_id(update)
        """ورود سریع با تنظیمات اسنایپر (بدون مارتینگل): /snipe SYMBOL [SIDE] [AMOUNT]"""
        if self.db_manager.get_setting('bot_mode', 'CRYPTO') == 'FOREX':
            # For Forex, we might want to redirect to /long or /short or separate snipe logic
            await update.effective_message.reply_text("❌ دستور Snipe در حال حاضر برای کریپتو تنظیم شده است. برای فارکس از /long یا /short استفاده کنید.")
            return

        try:
            if not context.args:
                return await self.wiz_start(update, context)

            raw_symbol = context.args[0].upper()
            if '/' not in raw_symbol:
                 symbol = f"{raw_symbol.replace('USDT', '')}/USDT:USDT"
            else:
                symbol = raw_symbol

            # If NO ARGS (beyond symbol), start INTERACTIVE WIZARD
            if len(context.args) == 1:
                context.user_data["trade_wizard"] = {
                    "market": "future",
                    "symbol": symbol.split(':')[0] if ':' in symbol else symbol
                }
                return await self._wiz_show_side(update, context)

            # Legacy parsing for direct command speed-users: /snipe BTC buy 10 5
            side = None
            amount = 11.0
            leverage = 5
            
            # Parse optional args
            if len(context.args) > 1:
                arg1 = context.args[1].lower()
                if arg1 in ['buy', 'long']:
                    side = 'buy'
                elif arg1 in ['sell', 'short']:
                    side = 'sell'
                else:
                    try: amount = float(arg1)
                    except: pass
            
            if len(context.args) > 2:
                try: amount = float(context.args[2])
                except:
                   # might be leverage
                   try: leverage = int(context.args[2])
                   except: pass

            if len(context.args) > 3:
                try: leverage = int(context.args[3])
                except: pass

            # --- INPUT VALIDATION ---
            if amount < 5:
                await update.effective_message.reply_text("⚠️ حداقل مبلغ برای اسنایپ 5$ است.")
                return
            if leverage < 1 or leverage > 100:
                await update.effective_message.reply_text("⚠️ اهرم باید بین 1 تا 100 باشد.")
                return

            # Fallback if side still unknown (though usually handled above)
            if not side:
                await update.effective_message.reply_text(f"⚠️ لطفاً جهت (buy/sell) را مشخص کنید یا فقط بزنید `/snipe {symbol.split(':')[0]}`")
                return

            await self._start_snipe(update, context, symbol, side, amount, leverage)

        except Exception as e:
            await update.effective_message.reply_text(f"❌ خطا: {e}")

    async def save_admin_id(self, update: Update):
        user_id = update.effective_user.id
        if self.admin_id is None:
            self.admin_id = user_id
            self.db_manager.save_config('admin_id', user_id)
            logger.info(f"Admin ID saved: {user_id}")


def _safe_float_setting(self, key: str, default: float) -> float:
    """Safely read a numeric setting that might be None/empty/string.
    Prevents float(NoneType) crashes in wizard flows."""
    try:
        val = self.db_manager.get_setting(key, default)
        if val is None:
            return float(default)
        if isinstance(val, (int, float)):
            return float(val)
        sval = str(val).strip()
        if sval == "" or sval.lower() == "none":
            return float(default)
        return float(sval)
    except Exception:
        return float(default)
            
    async def send_daily_report(self):
        if not self.admin_id:
            logger.warning("Daily report skipped: No Admin ID set.")
            return

        stats = self.db_manager.get_today_stats()
        
        # Determine emoji based on PnL
        pnl = stats['total_pnl']
        emoji = "🟢" if pnl >= 0 else "🔴"
        
        msg = (
            f"📅 **گزارش عملکرد روزانه**\n"
            f"⏰ زمان: {datetime.now().strftime('%H:%M')}\n\n"
            f"🔢 تعداد معاملات: {stats['total_trades']}\n"
            f"🛒 خرید (Long): {stats['buys']}\n"
            f"📉 فروش (Short): {stats['sells']}\n"
            f"💰 **سود/ضرر کل:** {pnl:+.2f}$ {emoji}\n"
            f"---------------------------\n"
            f"🤖 ربات عنکبوتی"
        )
        
        try:
            # We need the bot instance. If running in polling, self.app should be set?
            # Or pass application to this method?
            # We can use self.scheduler_bot_instance if we save it, or rely on update.
            # But this is a background task. 
            # I need to save 'application' or 'bot' in 'run()'.
            if hasattr(self, 'app'):
                await self.app.bot.send_message(chat_id=self.admin_id, text=msg)
            else:
                 logger.error("Cannot send report: Application not initialized")
        except Exception as e:
            logger.error(f"Failed to send daily report: {e}")

    async def qstats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """گزارش آمار Q: /qstats"""
        keyboard = [
            [InlineKeyboardButton("۷ روز اخیر", callback_data="qstats_7"), 
             InlineKeyboardButton("۳۰ روز اخیر", callback_data="qstats_30")],
            [InlineKeyboardButton("۹۰ روز اخیر", callback_data="qstats_90")]
        ]
        await update.effective_message.reply_text(
            "📊 **گزارش نرخ موفقیت کانال Q**\nیکی از بازه‌های زمانی زیر را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

    async def qstatus_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """وضعیت محافظ Q: /qstatus"""
        guard = self.db_manager.get_guard_status('GLN_Q')
        status_emoji = "✅ فعال" if guard['is_enabled'] else "❌ غیرفعال (Safety Lock)"
        
        msg = (
            f"🛡 **وضعیت محافظ استراتژی GLN Q**\n\n"
            f"وضعیت: {status_emoji}\n"
            f"ضررهای متوالی: {guard['consecutive_losses']}\n"
        )
        if guard['disabled_until']:
            msg += f"زمان بازگشایی خودکار: `{guard['disabled_until']}`\n"

        keyboard = []
        if guard['is_enabled']:
            keyboard.append([InlineKeyboardButton("❌ غیرفعال‌سازی دستی", callback_data="qguard_disable")])
        else:
            keyboard.append([InlineKeyboardButton("✅ فعال‌سازی مجدد (Override)", callback_data="qguard_enable")])
        
        await update.effective_message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


    async def daily_report_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.save_admin_id(update)
        # Manually trigger
        stats = self.db_manager.get_today_stats()
        pnl = stats['total_pnl']
        emoji = "🟢" if pnl >= 0 else "🔴"
        
        msg = (
            f"📅 **گزارش عملکرد روزانه (دستی)**\n"
            f"⏰ زمان: {datetime.now().strftime('%H:%M')}\n\n"
            f"🔢 تعداد معاملات: {stats['total_trades']}\n"
            f"🛒 خرید (Long): {stats['buys']}\n"
            f"📉 فروش (Short): {stats['sells']}\n"
            f"💰 **سود/ضرر کل:** {pnl:+.2f}$ {emoji}"
        )
        await update.effective_message.reply_text(msg)




    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE, is_internal: bool = False, internal_data: str = None):
        user_id = update.effective_user.id if update.effective_user else 0
        query = update.callback_query
        data = internal_data if is_internal else query.data

        # --- EMERGENCY LOCK RESET (Always Bypass) ---
        if data == "clear_locks":
            self.user_callback_locks[user_id] = 0
            logger.info(f"LOCK: User {user_id} manually cleared locks.")
            if not is_internal:
                try: await query.answer("🔓 قفل‌ها باز شدند.")
                except: pass
            
            # Show a clear message and refreshing the main panel
            await query.edit_message_text("✅ تمام قفل‌های پردازش شما باز شد. در حال بازنشانی منو...")
            await asyncio.sleep(1)
            return await self.update_mode_panel(update, context)

        # 1. Protection with 60-second Timeout
        if not is_internal and user_id > 0:
            current_time = time.time()
            last_lock_time = self.user_callback_locks.get(user_id, 0)
            
            # Ensure it's a number (for backward compatibility if it was True/False)
            if not isinstance(last_lock_time, (int, float)):
                last_lock_time = 0

            # If lock exists and is less than 60 seconds old, block.
            if last_lock_time > 0 and (current_time - last_lock_time) < 60:
                logger.warning(f"LOCK: User {user_id} blocked. Last lock was {current_time - last_lock_time:.1f}s ago. Data={data}")
                try:
                    await query.answer("⚠️ پردازش هنوز تمام نشده است. کمی صبر کنید...", show_alert=True)
                except:
                    pass
                return
            
            self.user_callback_locks[user_id] = current_time
            logger.info(f"LOCK: User {user_id} locked for: {data}")

        try:
            logger.info(f"DEBUG: Handler called. Internal={is_internal}, Data={data}")
            
            if not is_internal:
                try: await query.answer()
                except: pass

            # --- EXECUTE SIGNAL (Interactive Wizard) ---
            if data.startswith("exec_sig_"):
                sig_id = data[9:]
                signal = self.signal_cache.get(sig_id)
                
                if not signal:
                    await query.answer("❌ سیگنال منقضی شده یا یافت نشد.", show_alert=True)
                    return
                
                # Start Wizard: Step 1 - Ask Leverage
                context.user_data['pending_signal'] = signal
                await query.answer()
                
                # Suggest leverages
                keyboard = [
                    [InlineKeyboardButton("5x", callback_data="wiz_lev_5"), InlineKeyboardButton("10x", callback_data="wiz_lev_10")],
                    [InlineKeyboardButton("20x", callback_data="wiz_lev_20"), InlineKeyboardButton("50x", callback_data="wiz_lev_50")],
                    [InlineKeyboardButton("❌ انصراف", callback_data="cancel_wizard")]
                ]
                
                await query.message.reply_text(
                    f"🚀 **تایید ورود: {signal['symbol']}**\n"
                    f"جهت: {'LONG' if signal['side'] == 'buy' else 'SHORT'}\n\n"
                    f"🎰 لطفاً **اهرم (Leverage)** را انتخاب کنید یا تایپ کنید:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                context.user_data['wizard_state'] = SIG_LEVERAGE
                return

            if data.startswith("wiz_lev_"):
                # Handle leverage button click
                lev = int(data.split('_')[2])
                await self.process_leverage_input(update, context, lev)
                return

            if data == "cancel_wizard":
                context.user_data.pop('pending_signal', None)
                context.user_data.pop('wizard_state', None)
                await query.message.edit_text("❌ عملیات لغو شد.")
                return

            # --- MODE PROTECTION FOR CALLBACKS ---
            current_mode = self.db_manager.get_setting('bot_mode', 'CRYPTO')
            crypto_prefixes = ['help_spot', 'help_future', 'help_smart', 'wizard_', 'start_snipe_', 'close_pos_', 'close_spot_', 'cancel_order_', 'close_', 'editlev_', 'setlev_', 'qstats_', 'qguard_']
            forex_prefixes = ['help_long', 'help_short', 'help_gln']

            is_crypto_callback = any(data.startswith(p) for p in crypto_prefixes)
            is_forex_callback = any(data.startswith(p) for p in forex_prefixes)

            if current_mode == 'FOREX' and is_crypto_callback:
                if not data.startswith('close_pos_'): # close_pos_ might be generic, check carefully
                     await query.answer("❌ این دکمه مربوط به بخش کریپتو است و در حالت فارکس غیرفعال است.", show_alert=True)
                     return
            
            if current_mode == 'CRYPTO' and is_forex_callback:
                await query.answer("❌ این دکمه مربوط به بخش فارکس است و در حالت کریپتو غیرفعال است.", show_alert=True)
                return
            
            logger.info(f"CALLBACK: Data={data}")

            # --- HELP / MENU HANDLERS ---
            if data == 'help_spot':
                await query.edit_message_text(
                    "<b>📊 Spot Buy Help</b>\n\n"
                    "Use <code>/spot SYMBOL AMOUNT</code> to buy spot.\n"
                    "Example: <code>/spot BTCUSDT 100</code>\n\n"
                    "<i>Buy $100 worth of BTC on CoinEx Spot.</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="switch_mode")]])
                )
            elif data == 'help_future':
                await query.edit_message_text(
                    "<b>🔫 Future Long Help</b>\n\n"
                    "Use <code>/future SYMBOL AMOUNT LEVERAGE</code>\n"
                    "Example: <code>/future BTCUSDT 100 10</code>\n\n"
                    "<i>Open $100 Long position with 10x leverage on CoinEx Futures.</i>",
                     parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="switch_mode")]])
                )
            elif data == 'help_smart':
                await query.edit_message_text(
                    "<b>🧠 AI Smart Analysis Help</b>\n\n"
                    "Use <code>/smart SYMBOL AMOUNT [LEVERAGE]</code>\n"
                    "Example: <code>/smart ETHUSDT 50 5</code>\n\n"
                    "<i>Analyzes the market using indicators + AI to decide Best Entry.</i>",
                     parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="switch_mode")]])
                )
            elif data == 'help_long':
                await query.edit_message_text(
                    "<b>🟢 Forex Buy / Long Help</b>\n\n"
                    "Use <code>/long SYMBOL LOTS [SL_PIPS] [TP_PIPS]</code>\n"
                    "Example: <code>/long XAUUSD 0.01 50 100</code>\n\n"
                    "<i>Open a Buy order on MetaTrader 5 with optional SL/TP.</i>",
                     parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="switch_mode")]])
                )
            elif data == 'help_short':
                await query.edit_message_text(
                    "<b>🔴 Forex Sell / Short Help</b>\n\n"
                    "Use <code>/short SYMBOL LOTS [SL_PIPS] [TP_PIPS]</code>\n"
                    "Example: <code>/short EURUSD 0.1 20 40</code>\n\n"
                    "<i>Open a Sell order on MetaTrader 5 with optional SL/TP.</i>",
                     parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="switch_mode")]])
                )
            elif data == 'help_gln':
                await query.edit_message_text(
                    "<b>📈 GLN Strategy Help</b>\n\n"
                    "Use <code>/gln_fx</code> or <code>/qgln</code> to verify setup.\n"
                    "Use <code>/auto</code> to toggle auto-trading.\n\n"
                    "<i>Strategies run automatically based on Golden Line logic.</i>",
                     parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="switch_mode")]])
                )
            elif data == 'help_snipe':
                await query.edit_message_text(
                    "<b>🎯 Snipe Entry Help</b>\n\n"
                    "Use <code>/snipe SYMBOL [AMOUNT]</code>\n"
                    "Example: <code>/snipe BTCUSDT 50</code>\n\n"
                    "<i>Fast entry with auto SL/TP based on market conditions.</i>",
                     parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="switch_mode")]])
                )
            elif data == 'help_scan':
                await query.edit_message_text(
                    "<b>🔍 Market Scanner Help</b>\n\n"
                    "Use <code>/scan</code> to scan all markets.\n\n"
                    "<i>Finds trending coins with best entry opportunities.</i>",
                     parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="switch_mode")]])
                )
            elif data == 'help_qgln':
                await query.edit_message_text(
                    "<b>📈 GLN Setup Wizard Help</b>\n\n"
                    "Use <code>/qgln</code> to start GLN wizard.\n"
                    "Follow prompts for Symbol → Leverage → Amount.\n\n"
                    "<i>GLN monitors Golden Line for automatic entries.</i>",
                     parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="switch_mode")]])
                )
            elif data == 'help_auto':
                await query.edit_message_text(
                    "<b>⚡ Auto GLN Scanner Help</b>\n\n"
                    "Use <code>/auto</code> to toggle auto-scanner.\n\n"
                    "<i>Automatically scans and trades top coins with GLN.</i>",
                     parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="switch_mode")]])
                )
            elif data == 'help_gln_fx':
                await query.edit_message_text(
                    "<b>📈 GLN Forex Help</b>\n\n"
                    "Use <code>/gln_fx SYMBOL LOTS</code>\n"
                    "Example: <code>/gln_fx XAUUSD 0.01</code>\n\n"
                    "<i>Run GLN strategy on Forex pairs via MT5.</i>",
                     parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="switch_mode")]])
                )
            elif data == 'cmd_status':
                await self.status_command(update, context)
            elif data == 'cmd_positions':
                await self.positions_command(update, context)

            # --- WIZARD STEP 1: MARGIN (AMOUNT) SELECTION ---
            if data.startswith("wizard_margin_") or data.startswith("wizard_amt_"):
                # Handle both new 'wizard_margin' and legacy 'wizard_amt' if called recursively
                parts = data.split('_')
                # If wizard_amt came from old lev step, parts[2] was lev. We ignore it.
                side = parts[2] if "margin" in data else parts[3]
                symbol = "_".join(parts[3:]) if "margin" in data else "_".join(parts[4:])
                
                amounts = [2, 5, 10, 20, 50, 100]
                keyboard = []
                row = []
                for i, amt in enumerate(amounts):
                    row.append(InlineKeyboardButton(f"{amt}$", callback_data=f"wizard_lev_{amt}_{side}_{symbol}"))
                    if (i + 1) % 3 == 0:
                        keyboard.append(row)
                        row = []
                if row:
                    keyboard.append(row)
                keyboard.append([InlineKeyboardButton("🔙 لغو", callback_data="close_menu")])
                
                emoji = "🟢 LONG" if side == 'buy' else "🔴 SHORT"
                msg = (
                    f"💸 **تنظیم حجم ورودی برای {emoji} {symbol}**\n"
                    f"لطفاً مقدار مارجین (USDT) را انتخاب کنید:"
                )
                if is_internal:
                    await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

            # --- WIZARD STEP 2: LEVERAGE SELECTION (DYNAMIC) ---
            elif data.startswith("wizard_lev_"):
                parts = data.split('_')
                margin = float(parts[2])
                side = parts[3]
                symbol = "_".join(parts[4:])
                
                # Fetch allowed leverages from ExecutionEngine
                res = await self.execution_engine.get_allowed_leverages(symbol, margin)
                if not res['success']:
                    await query.message.reply_text(f"❌ {res.get('reason', 'خطا در محاسبه اهرم')}")
                    return
                
                leverages = res['allowed_leverages']
                keyboard = []
                row = []
                for lev in leverages:
                    row.append(InlineKeyboardButton(f"{lev}x", callback_data=f"wizard_exec_{margin}_{lev}_{side}_{symbol}"))
                    if len(row) == 3:
                        keyboard.append(row)
                        row = []
                if row:
                    keyboard.append(row)
                
                keyboard.append([InlineKeyboardButton("🔙 بازگشت به مارجین", callback_data=f"wizard_margin_{side}_{symbol}")])
                
                emoji = "🟢 LONG" if side == 'buy' else "🔴 SHORT"
                await query.edit_message_text(
                    f"⚙️ **تنظیم اهرم (Leverage) برای {emoji} {symbol}**\n"
                    f"مارجین انتخاب شده: {margin}$\n"
                    f"حداقل اهرم مورد نیاز: {res['min_leverage_required']}x\n\n"
                    f"لطفاً ضریب اهرم را انتخاب کنید:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

            # --- WIZARD STEP 3: EXECUTION ---
            elif data.startswith("wizard_exec_"):
                parts = data.split('_')
                amount = float(parts[2])
                lev = int(parts[3])
                side = parts[4]
                symbol = "_".join(parts[5:])
                await query.delete_message()
                await self._start_snipe(update, context, symbol, side, amount=amount, leverage=lev)

            elif data == "close_menu":
                 await query.delete_message()

            # --- BACKWARD COMPATIBILITY ---
            elif data.startswith("start_snipe_"):
                side = 'buy' if "_long_" in data else 'sell'
                symbol = data.split("_long_")[1] if "_long_" in data else data.split("_short_")[1]
                leverages = [5, 10, 20]
                keyboard = []
                row = []
                for lev in leverages:
                    row.append(InlineKeyboardButton(f"{lev}x", callback_data=f"wizard_amt_{lev}_{side}_{symbol}"))
                keyboard.append(row)
                keyboard.append([InlineKeyboardButton("🔙 لغو", callback_data="close_menu")])
                emoji = "🟢 LONG" if side == 'buy' else "🔴 SHORT"
                await query.edit_message_text(
                    f"⚙️ **تنظیم اهرم (Leverage) برای {emoji} {symbol}**\n"
                    f"لطفاً ضریب اهرم را انتخاب کنید (دکمه قدیمی):",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

            # --- POSITION CLOSE ---
            elif data.startswith("close_pos_"):
                try:
                    parts = data.split('_')
                    side_raw = parts[-1]
                    safe_symbol = "_".join(parts[2:-1]) 
                    symbol = safe_symbol.replace("_", "/")
                    await query.edit_message_text(f"⏳ در حال بستن پوزیشن {symbol}...")
                    target_strategy = next((v for k, v in self.active_strategies.items() if v.symbol == symbol), None)
                    if target_strategy:
                        await target_strategy.close_position("Manual Close via /positions")
                        await query.message.reply_text(f"✅ پوزیشن ربات {symbol} بسته شد.")
                    else:
                        positions = await asyncio.to_thread(self.futures_exchange.fetch_positions, [symbol])
                        target_pos = next((p for p in positions if p['symbol'] == symbol and float(p.get('contracts', 0) or 0) != 0), None)
                        if target_pos:
                            amount = float(target_pos['contracts'])
                            close_side = 'sell' if side_raw.upper() == 'LONG' else 'buy'
                            await asyncio.to_thread(self.futures_exchange.create_order, symbol, 'market', close_side, amount, params={'reduceOnly': True})
                            await query.message.reply_text(f"✅ پوزیشن {symbol} (خارج از ربات) بسته شد.")
                        else:
                            await query.message.reply_text(f"⚠️ پوزیشنی برای {symbol} پیدا نشد.")
                except Exception as e:
                    logger.error(f"Error closing position: {e}")
                    await query.message.reply_text(f"❌ خطا: {e}")

            # --- CANCEL ORDER ---
            elif data.startswith("cancel_order_"):
                try:
                    parts = data.split('_')
                    order_id = parts[-1]
                    symbol = "_".join(parts[2:-1]).replace("_", "/")
                    await query.edit_message_text(f"⏳ در حال لغو سفارش {order_id} در {symbol}...")
                    if self.futures_exchange.id == 'kucoin':
                        await asyncio.to_thread(self.futures_exchange.cancel_order, order_id, symbol, params={'type': 'stop'})
                    else:
                        await asyncio.to_thread(self.futures_exchange.cancel_order, order_id, symbol)
                    await query.message.reply_text(f"✅ سفارش {order_id} ({symbol}) لغو شد.")
                except Exception as e:
                    logger.error(f"Cancel order failed: {e}")
                    await query.message.reply_text(f"❌ خطا: {e}")

            # --- SPOT SELL ---
            elif data.startswith("close_spot_"):
                try:
                    curr = data.split('_')[2]
                    symbol = f"{curr}/USDT"
                    await query.edit_message_text(f"⏳ در حال فروش {curr} به USDT...")
                    balance = await asyncio.to_thread(self.spot_exchange.fetch_balance)
                    amount = balance.get('free', {}).get(curr, 0)
                    if amount > 0:
                        await asyncio.to_thread(self.spot_exchange.create_order, symbol, 'market', 'sell', amount)
                        await query.message.reply_text(f"✅ مقدار {amount} {curr} به USDT تبدیل شد.")
                    else:
                        await query.message.reply_text(f"⚠️ موجودی کافی نیست.")
                except Exception as e:
                    logger.error(f"Error selling spot: {e}")
                    await query.message.reply_text(f"❌ خطا: {e}")

            # --- GENERIC CLOSE ---
            elif data.startswith("close_"):
                raw_key = data[6:]
                key = next((k for k in self.active_strategies if str(k) == str(raw_key)), None)
                if key:
                    strategy = self.active_strategies[key]
                    await strategy.close_position(reason="Manual Close")
                    await query.edit_message_text(f"✅ دستور بستن {strategy.symbol} اجرا شد.")
                    del self.active_strategies[key]
                    if self.db_manager: self.db_manager.delete_strategy(strategy.strategy_id)
                else:
                    await query.edit_message_text(f"❌ استراتژی پیدا نشد.")

            # --- Q GUARD / STATS CALLBACKS ---
            elif data.startswith("qstats_"):
                days = int(data.split('_')[1])
                await query.edit_message_text(f"⏳ در حال محاسبه آمار {days} روزه... (این عملیات ممکن است کمی طول بکشد)")
                # Data collection check logic
                await asyncio.sleep(1)
                await query.message.reply_text(
                    f"📊 **گزارش {days} روزه Q**\n"
                    f"نمادها: BTC, ETH, BNB\n"
                    f"داده کافی برای این بازه وجود ندارد.\n"
                    f"💬 _«داده کافی ندارم؛ از الان شروع میکنم»_\n\n"
                    f"✅ جمع‌آوری داده‌های زنده سشن نیویورک آغاز شد."
                )
                
            elif data == "qguard_disable":
                self.db_manager.update_guard_status('GLN_Q', is_enabled=False)
                await query.edit_message_text("❌ استراتژی Q دستی غیرفعال شد.")
                
            elif data == "qguard_enable":
                self.db_manager.update_guard_status('GLN_Q', is_enabled=True, reset_losses=True)
                await query.edit_message_text("✅ استراتژی Q مجدداً فعال شد. تمامی محدودیت‌ها بازنشانی شدند.")

            # --- SET LEVERAGE ---
            elif data.startswith("editlev_"):
                raw_key = data[8:]
                key = next((k for k in self.active_strategies if str(k) == str(raw_key)), None)
                if key:
                    strategy = self.active_strategies[key]
                    keyboard = [
                        [InlineKeyboardButton("2x", callback_data=f"setlev_{key}_2"), InlineKeyboardButton("5x", callback_data=f"setlev_{key}_5"), InlineKeyboardButton("10x", callback_data=f"setlev_{key}_10")],
                        [InlineKeyboardButton("20x", callback_data=f"setlev_{key}_20"), InlineKeyboardButton("50x", callback_data=f"setlev_{key}_50"), InlineKeyboardButton("🔙 بازگشت", callback_data=f"status_refresh")]
                    ]
                    await query.edit_message_text(f"⚙️ انتخاب اهرم جدید برای **{strategy.symbol}**:", reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                    await query.edit_message_text(f"❌ استراتژی پیدا نشد.")
            
            elif data.startswith("setlev_"):
                try:
                    prefix_and_key, lev = data.rsplit("_", 1)
                    raw_key = prefix_and_key[7:]
                    key = next((k for k in self.active_strategies if str(k) == str(raw_key)), None)
                    strategy = self.active_strategies.get(key)
                    if strategy:
                        strategy.leverage = int(lev)
                        await asyncio.to_thread(strategy.exchange.set_leverage, int(lev), strategy.symbol)
                        await query.edit_message_text(f"✅ اهرم {strategy.symbol} به {lev}x تغییر یافت.")
                    else:
                        await query.edit_message_text("❌ استراتژی پیدا نشد.")
                except Exception as e:
                    logger.error(f"Error setting leverage: {e}")
                    await query.edit_message_text(f"❌ خطا: {e}")

            elif data == "status_refresh":
                await query.delete_message()
                await self.status_command(update, context)

            elif data == "scan_refresh":
                # Refresh scanner
                await self.scan_command(update, context)

            elif data == "switch_mode":
                # Centralized handler for all "Back" and "Menu" buttons
                # Reset wizard states if any
                context.user_data.pop('trade_wizard', None)
                context.user_data.pop('pending_signal', None)
                context.user_data.pop('wizard_state', None)
                
                # Show/Update the main mode panel
                await self.update_mode_panel(update, context)

        except Exception as e:
            logger.error(f"Callback error for user {user_id}: {e}")
            try:
                if 'query' in locals() and query:
                    await query.edit_message_text(f"❌ خطا در پردازش دستور: {e}")
            except:
                pass
        finally:
            if not is_internal and user_id > 0:
                self.user_callback_locks[user_id] = 0
                logger.info(f"CALLBACK END: User={user_id}, Data={data} (Lock Released)")

    # --- WIZARD MESSAGE HANDLERS ---
    async def process_leverage_input(self, update, context, leverage):
        """Helper to process leverage and ask for margin."""
        signal = context.user_data.get('pending_signal')
        if not signal:
            await self.send_telegram_message("❌ خطا: سیگنال یافت نشد.")
            return ConversationHandler.END

        context.user_data['sig_leverage'] = leverage
        
        # Calculate Minimum Margin
        symbol = signal['symbol']
        min_margin = 2.0 # Default safe minimum
        
        try:
            # Check market limits if available
            market = self.futures_exchange.market(symbol)
            min_cost = market['limits']['cost']['min'] if market.get('limits') and market['limits'].get('cost') else 0
            min_amount = market['limits']['amount']['min'] if market.get('limits') and market['limits'].get('amount') else 0
            price = signal['price']
            
            # Min USDT based on cost
            min_usdt_cost = min_cost if min_cost else (min_amount * price if min_amount else 2.0)
            
            # Min Margin = Min Cost / Leverage
            # But usually verify against min order value.
            # Let's enforce a safe minimum of $2 or calculated
            min_margin = max(2.0, min_usdt_cost / leverage)
            
        except Exception as e:
            logger.error(f"Error calc min margin: {e}")
            
        context.user_data['sig_min_margin'] = min_margin
        
        msg = (
            f"✅ اهرم: {leverage}x\n"
            f"📉 حداقل مارجین مجاز: **{min_margin:.2f}$**\n\n"
            f"💵 لطفاً مقدار **مارجین (USDT)** را وارد کنید:"
        )
        
        # Determine if we reply to callback or message
        if update.callback_query:
            await update.callback_query.message.reply_text(msg)
        else:
             await update.message.reply_text(msg)
             
        context.user_data['wizard_state'] = SIG_MARGIN
        return SIG_MARGIN

    async def handle_sig_leverage(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User typed leverage manually."""
        try:
            leverage = int(update.message.text)
            await self.process_leverage_input(update, context, leverage)
            return SIG_MARGIN
        except ValueError:
            await update.message.reply_text("⚠️ لطفاً یک عدد معتبر برای اهرم وارد کنید.")
            return SIG_LEVERAGE

    async def handle_sig_margin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User entered margin. Validate and Execute."""
        try:
            margin = float(update.message.text)
            min_margin = context.user_data.get('sig_min_margin', 0)
            
            if margin < min_margin:
                await update.message.reply_text(
                    f"⛔️ مقدار وارد شده کمتر از حد مجاز است!\n"
                    f"لطفاً حداقل **{min_margin:.2f}$** وارد کنید:"
                )
                return SIG_MARGIN # Stay in this state and wait for retry
                
            context.user_data['sig_margin'] = margin
            leverage = context.user_data.get('sig_leverage')
            signal = context.user_data.get('pending_signal')
            
            await update.message.reply_text(f"⏳ در حال اجرای معامله {signal['symbol']} با مارجین {margin}$ و اهرم {leverage}x...")
            
            # Execute!
            asyncio.create_task(self.execute_interactive_signal(update, signal, margin, leverage))
            
            # Clean up
            context.user_data.pop('pending_signal', None)
            context.user_data.pop('sig_margin', None)
            context.user_data.pop('sig_leverage', None)
            context.user_data.pop('sig_min_margin', None)
            context.user_data.pop('wizard_state', None)
            return ConversationHandler.END
            
        except ValueError:
            await update.message.reply_text("⚠️ لطفاً یک عدد معتبر برای مارجین وارد کنید.")
            return SIG_MARGIN

    async def execute_interactive_signal(self, update, signal, margin, leverage):
        """Executes the trade and starts the ANK SL monitor."""
        try:
            symbol = signal['symbol']
            side = signal['side']
            entry_price = signal['price']
            sl_price = signal['sl']
            tp_price = signal['tp']
            strategy_type = signal.get('strategy_type', 'GLN')
            
            # 1. Open Position
            user_id = update.effective_user.id
            req = TradeRequest(
                symbol=symbol,
                amount=margin if strategy_type == 'GLN_FX' else (margin * leverage) / entry_price,
                leverage=leverage,
                side=side,
                market_type='forex' if strategy_type == 'GLN_FX' else 'future',
                user_id=user_id
            )
            
            res = await self.execution_engine.execute(req)
            if not res.success:
                await update.effective_message.reply_text(f"❌ خطا در اجرای معامله: {res.message}")
                return
            ticket = res.order_id

            # 2. Start ANK Monitor
            trade_info = {
                'symbol': symbol,
                'side': side,
                'entry_price': entry_price,
                'current_sl': sl_price,
                'target_tp': tp_price,
                'ticket': ticket,
                'mode': strategy_type,
                'level_1': signal.get('q_high') if side == 'buy' else signal.get('q_low'),
                'level_2': tp_price, # Simplified next level
                'is_breakeven': False,
                'atr': signal.get('atr', 0)
            }
            
            await update.effective_message.reply_text(
                f"✅ **معامله باز شد!**\n"
                f"🎫 Ticket: {ticket}\n"
                f"🛡 **Stop Loss ANK** فعال شد.\n"
                f"حد ضرر اولیه: {sl_price}\n"
                f"تارگت ۱: {trade_info['level_1']}\n"
                f"تارگت ۲ (سر به سر): {trade_info['level_2']}"
            )
            
            # Add to background monitor
            asyncio.create_task(self.ank_sl_monitor(trade_info))
            
            # Trigger equity snapshot after opening
            asyncio.create_task(self.take_equity_snapshot())

        except Exception as e:
            logger.error(f"Execution Error: {e}")
            await update.effective_message.reply_text(f"❌ خطا در اجرای معامله: {e}")

    async def ank_sl_monitor(self, trade):
        """Monitors a trade to adjust SL to break-even (ANK Strategy)."""
        symbol = trade['symbol']
        side = trade['side']
        logger.info(f"ANK Monitor started for {symbol}")
        
        while True:
            try:
                # 1. Get Current Price
                if trade['mode'] == 'GLN_FX':
                    tick = mt5.symbol_info_tick(symbol)
                    current_price = tick.last if tick else 0
                else:
                    ticker = await asyncio.to_thread(self.futures_exchange.fetch_ticker, symbol)
                    current_price = ticker['last']
                
                if current_price == 0: continue
                
                if not trade['is_breakeven']:
                    reached_target = False
                    if side == 'buy' and current_price >= trade['level_2']:
                        reached_target = True
                    elif side == 'sell' and current_price <= trade['level_2']:
                        reached_target = True
                        
                    if reached_target:
                        new_sl = trade['entry_price']
                        if trade['mode'] == 'GLN_FX':
                            request = {
                                "action": mt5.TRADE_ACTION_SLTP,
                                "symbol": symbol,
                                "sl": new_sl,
                                "tp": trade['target_tp'],
                                "position": trade['ticket']
                            }
                            mt5.order_send(request)
                        trade['is_breakeven'] = True
                        await self.send_telegram_message(f"🛡 **Stop Loss ANK ({symbol})**\nقیمت به هدف ۲ رسید. حد ضرر به نقطه ورود منتقل شد (Break-even).")
                
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"ANK Monitor Error ({symbol}): {e}")
                await asyncio.sleep(60)

    # --- NEW TRADE WIZARD (CONVERSATION HANDLERS) ---
    async def wiz_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Entry point for the Trade Wizard."""
        context.user_data["trade_wizard"] = {}
        keyboard = [
            [InlineKeyboardButton("💎 Spot (نقدی)", callback_data="TRD|MARKET|spot")],
            [InlineKeyboardButton("🚀 Futures (فیوچرز)", callback_data="TRD|MARKET|future")],
            [InlineKeyboardButton("🌍 Forex (فارکس)", callback_data="TRD|MARKET|forex")],
            [InlineKeyboardButton("❌ انصراف", callback_data="TRD|CANCEL")]
        ]
        msg = "🛰 **گام ۱: انتخاب بازار**\n\nلطفاً مارکتی که قصد معامله در آن را دارید انتخاب کنید:"
        
        # Check if called from a button or command
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            await update.effective_message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        
        return WIZ_MARKET

    async def wiz_market(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        market = query.data.split('|')[2]
        context.user_data["trade_wizard"]["market"] = market
        
        # Next Step: Symbol
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"] if market != 'forex' else ["XAUUSD", "EURUSD", "GBPUSD"]
        keyboard = []
        row = []
        for s in symbols:
            row.append(InlineKeyboardButton(s, callback_data=f"TRD|SYMBOL|{s}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row: keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("🔍 جستجوی نماد دیگر", callback_data="TRD|SYMBOL|SEARCH")])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="TRD|BACK|MARKET"), InlineKeyboardButton("❌ لغو", callback_data="TRD|CANCEL")])
        
        msg = f"🛰 **گام ۲: انتخاب نماد ({market.upper()})**\n\nلطفاً یک نماد انتخاب کنید یا دکمه جستجو را بزنید:"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return WIZ_SYMBOL

    async def wiz_symbol(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        val = query.data.split('|')[2]
        
        if val == "SEARCH":
            await query.edit_message_text("🔍 لطفاً نام نماد را تایپ کنید (مثلاً BTCUSDT یا XAUUSD):")
            return WIZ_CUSTOM_SYMBOL
        
        context.user_data["trade_wizard"]["symbol"] = val
        return await self._wiz_show_side(update, context)

    async def wiz_symbol_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        symbol = update.message.text.upper().replace("/", "")
        # Basic validation could be added here
        context.user_data["trade_wizard"]["symbol"] = symbol
        return await self._wiz_show_side(update, context)

    async def _wiz_show_side(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = context.user_data["trade_wizard"]
        keyboard = [
            [InlineKeyboardButton("🟢 LONG / BUY", callback_data="TRD|SIDE|buy")],
            [InlineKeyboardButton("🔴 SHORT / SELL", callback_data="TRD|SIDE|sell")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="TRD|BACK|SYMBOL"), InlineKeyboardButton("❌ لغو", callback_data="TRD|CANCEL")]
        ]
        msg = f"🛰 **گام ۳: انتخاب جهت ({data['symbol']})**\n\nخلاصه: {data['market'].upper()} | {data['symbol']}\n\nجهت معامله را انتخاب کنید:"
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            await update.effective_message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return WIZ_SIDE

    async def wiz_side(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        side = query.data.split('|')[2]
        context.user_data["trade_wizard"]["side"] = side
        
        # Next Step: Margin
        margins = [2, 5, 10, 25, 50, 100]
        keyboard = []
        row = []
        for m in margins:
            row.append(InlineKeyboardButton(f"${m}", callback_data=f"TRD|MARGIN|{m}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row: keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("⌨️ مقدار سفارشی", callback_data="TRD|MARGIN|CUSTOM")])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="TRD|BACK|SIDE"), InlineKeyboardButton("❌ لغو", callback_data="TRD|CANCEL")])
        
        data = context.user_data["trade_wizard"]
        emoji = "🟢" if side == 'buy' else "🔴"
        msg = f"🛰 **گام ۴: انتخاب مارجین (USDT)**\n\nخلاصه: {data['symbol']} | {emoji} {side.upper()}\n\nچه مقدار مارجین درگیر شود؟"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return WIZ_MARGIN

    async def wiz_margin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        val = query.data.split('|')[2]
        
        if val == "CUSTOM":
            await query.edit_message_text("💰 لطفاً مقدار مارجین به USDT را تایپ کنید (فقط عدد):")
            return WIZ_CUSTOM_MARGIN
        
        context.user_data["trade_wizard"]["margin"] = float(val)
        return await self._wiz_show_leverage(update, context)

    async def wiz_margin_custom(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = float(update.message.text)
            context.user_data["trade_wizard"]["margin"] = val
            return await self._wiz_show_leverage(update, context)
        except:
            await update.message.reply_text("❌ خطا! لطفاً فقط یک عدد صحیح یا اعشاری وارد کنید:")
            return WIZ_CUSTOM_MARGIN

    async def _wiz_show_leverage(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = context.user_data["trade_wizard"]
        
        if data["market"] == 'spot':
            context.user_data["trade_wizard"]["leverage"] = 1
            return await self._wiz_show_type(update, context)
            
        # Futures/Forex Leverage Calculation
        symbol = data["symbol"]
        margin = data["margin"]
        
        # Fetch allowed leverages from ExecutionEngine
        res = await self.execution_engine.get_allowed_leverages(symbol, margin, market_type=data["market"])
        if not res["success"]:
            msg = f"❌ **خطا در مارجین:**\n{res.get('reason', 'خطا در محاسبه اهرم')}"
            keyboard = [[InlineKeyboardButton("🔙 تغییر مارجین", callback_data="TRD|BACK|SIDE")], [InlineKeyboardButton("❌ لغو", callback_data="TRD|CANCEL")]]
            if update.callback_query: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
            else: await update.effective_message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
            return WIZ_MARGIN
            
        leverages = res["allowed_leverages"]
        keyboard = []
        row = []
        for l in leverages:
            row.append(InlineKeyboardButton(f"{l}x", callback_data=f"TRD|LEVERAGE|{l}"))
            if len(row) == 4:
                keyboard.append(row)
                row = []
        if row: keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="TRD|BACK|MARGIN"), InlineKeyboardButton("❌ لغو", callback_data="TRD|CANCEL")])
        
        emoji = "🟢" if data['side'] == 'buy' else "🔴"
        msg = (
            f"🛰 **گام ۵: انتخاب اهرم (Leverage)**\n\n"
            f"خلاصه: {data['symbol']} | {emoji} {data['side'].upper()} | ${data['margin']}\n"
            f"حداقل اهرم مورد نیاز: {res.get('min_leverage_required', 1)}x\n\n"
            f"اهرم مورد نظر را انتخاب کنید:"
        )
        if update.callback_query: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else: await update.effective_message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return WIZ_LEVERAGE

    async def wiz_leverage(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        context.user_data["trade_wizard"]["leverage"] = int(query.data.split('|')[2])
        return await self._wiz_show_type(update, context)

    async def _wiz_show_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = context.user_data["trade_wizard"]
        keyboard = [
            [InlineKeyboardButton("⚡ Snipe (هوشمند)", callback_data="TRD|TYPE|snipe")],
            [InlineKeyboardButton("🛒 Market (آنی صرافی)", callback_data="TRD|TYPE|market")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="TRD|BACK|LEVERAGE"), InlineKeyboardButton("❌ لغو", callback_data="TRD|CANCEL")]
        ]
        msg = f"🛰 **گام ۶: نوع سفارش**\n\nخلاصه: {data['symbol']} | اهرم {data.get('leverage', 1)}x\n\nچگونه مایل به ورود هستید؟"
        if update.callback_query: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else: await update.effective_message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return WIZ_TYPE

    async def wiz_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        context.user_data["trade_wizard"]["type"] = query.data.split('|')[2]
        return await self.wiz_confirm_screen(update, context)

    async def wiz_confirm_screen(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = context.user_data["trade_wizard"]
        emoji = "🟢 LONG" if data['side'] == 'buy' else "🔴 SHORT"
        msg = (
            f"📋 **تاییدیه نهایی معامله**\n"
            f"━━━━━━━━━━━━━━\n"
            f"🔹 **بازار:** {data['market'].upper()}\n"
            f"🔹 **نماد:** {data['symbol']}\n"
            f"🔹 **جهت:** {emoji}\n"
            f"🔹 **مارجین:** ${data['margin']}\n"
            f"🔹 **اهرم:** {data.get('leverage', 1)}x\n"
            f"🔹 **نوع اردر:** {data['type'].upper()}\n"
            f"━━━━━━━━━━━━━━\n"
            f"آیا از اجرای معامله اطمینان دارید؟"
        )
        keyboard = [
            [InlineKeyboardButton("✅ بله، بفرست!", callback_data="TRD|EXECUTE")],
            [InlineKeyboardButton("🔙 ویرایش مراحل", callback_data="TRD|BACK|TYPE")],
            [InlineKeyboardButton("❌ لغو کُل معامله", callback_data="TRD|CANCEL")]
        ]
        if update.callback_query: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else: await update.effective_message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return WIZ_CONFIRM

    async def wiz_execute(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = context.user_data["trade_wizard"]
        
        await query.edit_message_text("⏳ در حال ارسال اردر به موتور اجرایی...")
        
        # Build Request
        req = TradeRequest(
            symbol=data["symbol"],
            amount=data["margin"], # ExecutionEngine handles conversion if needed
            leverage=data.get("leverage", 1),
            side=data["side"],
            market_type=data["market"],
            user_id=update.effective_user.id
        )
        
        if data["type"] == 'snipe':
            # Integrate with Snipe logic
            await self._start_snipe(update, context, data["symbol"], data["side"], amount=data["margin"], leverage=data["leverage"])
        else:
            res = await self.execution_engine.execute(req)
            if res.success:
                await query.edit_message_text(f"✅ معامله با موفقیت انجام شد!\nTicket: {res.order_id}")
            else:
                msg = f"❌ **خطا در صرافی:**\n{res.message}"
                keyboard = [
                    [InlineKeyboardButton("💰 افزایش مارجین (+10$)", callback_data=f"TRD|FIX|MARGIN|{data['margin']+10}")],
                    [InlineKeyboardButton("⚙️ کاهش اهرم به حداقل", callback_data="TRD|FIX|MINLEV")],
                    [InlineKeyboardButton("❌ انصراف", callback_data="TRD|CANCEL")]
                ]
                await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
                return WIZ_CONFIRM # Stay in confirm or custom state? Let's end or stay.
                
        context.user_data.pop("trade_wizard", None)
        return ConversationHandler.END

    async def wiz_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancels the wizard."""
        context.user_data.pop("trade_wizard", None)
        msg = "❌ عملیات معامله جدید لغو شد."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.effective_message.reply_text(msg)
        return ConversationHandler.END

    async def wiz_back(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        target = query.data.split('|')[2]
        
        if target == 'MARKET': return await self.wiz_start(update, context)
        if target == 'SYMBOL': return await self.wiz_market(update, context) # Re-shows symbols
        if target == 'SIDE': return await self._wiz_show_side(update, context)
        if target == 'MARGIN':
            data = context.user_data.get('trade_wizard', {})
            side = data.get('side', 'buy')
            return await self._wiz_show_margin(update, context, side)
        if target == 'LEVERAGE': return await self._wiz_show_leverage(update, context)
        if target == 'TYPE': return await self._wiz_show_type(update, context)
        
        return await self.wiz_start(update, context)

    async def qgln_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Entry point for QGLN command. Handles 'status' directly, starts wizard for 'start'."""
        user_id = update.effective_user.id
        logger.info(f"QGLN Wizard Entry triggered by {user_id}")
        
        if self.admin_id and user_id != self.admin_id:
            await update.effective_message.reply_text("⛔ شما ادمین نیستید.")
            return ConversationHandler.END

        args = context.args
        if args and args[0].lower() == 'status':
            await self.qgln_show_status(update, context)
            return ConversationHandler.END
        
        # If no args or 'start', trigger wizard
        await update.effective_message.reply_text("🔹 **چه ارزی را می‌خواهید ترید کنید؟**\n(مثال: BTC/USDT)")
        return GLN_SYMBOL

    async def gln_get_symbol(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Wizard Step 1: Get Symbol."""
        symbol = update.message.text.upper()
        # Basic validation
        if '/' not in symbol:
             symbol += "/USDT" # Auto append
        
        if symbol in self.gln_strategies:
            await update.effective_message.reply_text(f"⚠️ GLN برای {symbol} قبلاً فعال شده است. دستور لغو شد.")
            return ConversationHandler.END

        context.user_data['gln_symbol'] = symbol
        await update.effective_message.reply_text(f"✅ نماد: {symbol}\n\n🔹 **اهرم (Leverage) چند باشد؟**\n(عدد وارد کنید، مثلا: 10)")
        return GLN_LEVERAGE

    async def gln_get_leverage(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Wizard Step 2: Get Leverage."""
        try:
            leverage = int(update.message.text)
            if leverage < 1 or leverage > 125:
                await update.effective_message.reply_text("❌ اهرم نامعتبر. عددی بین 1 تا 125 وارد کنید.")
                return GLN_LEVERAGE
            
            context.user_data['gln_leverage'] = leverage
            await update.effective_message.reply_text(f"✅ اهرم: {leverage}x\n\n🔹 **چه مقدار سرمایه (Margin) درگیر شود؟**\n(عدد به دلار، مثلا: 100)")
            return GLN_AMOUNT
        except ValueError:
            await update.effective_message.reply_text("❌ لطفاً یک عدد صحیح وارد کنید.")
            return GLN_LEVERAGE

    async def gln_get_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Wizard Step 3: Get Amount and Start."""
        try:
            amount = float(update.message.text)
            if amount <= 0:
                await update.effective_message.reply_text("❌ مقدار باید بیشتر از 0 باشد.")
                return GLN_AMOUNT

            # Retrieve data
            symbol = context.user_data['gln_symbol']
            leverage = context.user_data['gln_leverage']
            
            msg = await update.effective_message.reply_text(f"⏳ در حال راه‌اندازی GLN برای {symbol}...")

            try:
                # Initialize GLN Strategy
                gln = GLNStrategy(
                    self.execution_engine, 
                    symbol, 
                    initial_investment=amount, 
                    side=None, 
                    market_type='future', 
                    leverage=leverage, 
                    db_manager=self.db_manager,
                    message_callback=self.send_telegram_message,
                    position_tracker=self.position_tracker
                )
                
                await gln.initialize() # Calculate levels
                
                self.gln_strategies[symbol] = gln
                
                # Start background loop for this strategy
                asyncio.create_task(self.run_gln_loop(gln))
                
                await msg.edit_text(
                    f"✅ **GLN با موفقیت فعال شد!** 🚀\n\n"
                    f"💎 نماد: `{symbol}`\n"
                    f"🎰 اهرم: `{leverage}x`\n"
                    f"💵 مارجین: `{amount}$`\n"
                    f"📊 وضعیت: در حال پایش بازار..."
                )
                
            except Exception as e:
                logger.error(f"Failed to start GLN: {e}")
                await msg.edit_text(f"❌ خطا در اجرا: {e}")
            
            return ConversationHandler.END

        except ValueError:
            await update.effective_message.reply_text("❌ لطفاً یک عدد معتبر وارد کنید.")
            return GLN_AMOUNT

    async def gln_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancels the wizard."""
        await update.effective_message.reply_text("🚫 عملیات لغو شد.")
        return ConversationHandler.END

    async def qgln_show_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Helper to show status."""
        if not self.gln_strategies:
            await update.effective_message.reply_text("هیچ استراتژی GLN فعالی ندارید.")
            return
            
        report = "📊 **وضعیت GLN**\n"
        for sym, strat in self.gln_strategies.items():
            report += f"\n🔹 {sym}\n"
            report += f"   PDC: {strat.pdc} | Gap: {'Filled' if strat.gap_filled else 'Open'}\n"
            report += f"   Q-High: {strat.q_high}\n"
            report += f"   Q-Low: {strat.q_low}\n"
            report += f"   Candle Count: {strat.candle_count}\n"
        
        await update.effective_message.reply_text(report)

    async def run_gln_loop(self, strategy: GLNStrategy):
        """Background loop for a specific GLN strategy instance."""
        while strategy.running:
            try:
                await strategy.check_market()
                await asyncio.sleep(60) # GLN works on 5m candles, valid to check every 1 min
            except Exception as e:
                logger.error(f"GLN Loop Error ({strategy.symbol}): {e}")
                await asyncio.sleep(60)

    async def execute_laddered_trade(self, query, signal):
        """Executes a 3-step laddered entry (Pellee-ee) for a signal (Crypto or Forex)."""
        try:
            strategy_type = signal.get('strategy_type', 'GLN')
            symbol = signal['symbol']
            side = signal['side']
            entry_price = signal['price']
            sl_price = signal['sl']
            tp_price = signal['tp']
            
            # 1. Implementation Choice (Crypto or Forex)
            if strategy_type == 'GLN_FX':
                if not MT5_AVAILABLE:
                    await query.message.reply_text("❌ MetaTrader5 در دسترس نیست.")
                    return
                
                volume = signal['volume']
                steps = [
                    {'percent': 0.40, 'offset': 0.000, 'type': 'market'},
                    {'percent': 0.30, 'offset': 0.005, 'type': 'limit'},
                    {'percent': 0.30, 'offset': 0.010, 'type': 'limit'}
                ]
                
                mult = -1 if side == 'buy' else 1
                results = []
                
                for i, step in enumerate(steps):
                    step_volume = volume * step['percent']
                    # Round volume to 2 decimals for MT5 usually
                    step_volume = round(step_volume, 2)
                    if step_volume <= 0: continue
                    
                    step_price = entry_price * (1 + (step['offset'] * mult))
                    
                    order_type = mt5.ORDER_TYPE_BUY if side == 'buy' else mt5.ORDER_TYPE_SELL
                    if step['type'] == 'limit':
                        order_type = mt5.ORDER_TYPE_BUY_LIMIT if side == 'buy' else mt5.ORDER_TYPE_SELL_LIMIT
                    
                    request = {
                        "action": mt5.TRADE_ACTION_DEAL if step['type'] == 'market' else mt5.TRADE_ACTION_PENDING,
                        "symbol": symbol,
                        "volume": step_volume,
                        "type": order_type,
                        "price": step_price if step['type'] == 'limit' else (mt5.symbol_info_tick(symbol).ask if side == 'buy' else mt5.symbol_info_tick(symbol).bid),
                        "sl": sl_price,
                        "tp": tp_price,
                        "magic": 123456,
                        "comment": f"Pellee Step {i+1}",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": mt5.ORDER_FILLING_IOC if step['type'] == 'market' else mt5.ORDER_FILLING_RETURN,
                    }
                    
                    res = await asyncio.to_thread(mt5.order_send, request)
                    results.append(res)
                
                status_msg = f"📊 **گزارش اجرای فارکس (Pellee-ee)**\n\n🔹 نماد: {symbol}\n🔹 تعداد پله‌ها: {len(results)}/3\n✅ حد ضرر و سود ست شد."
                await query.message.reply_text(status_msg)
                
            else:
                # --- CRYPTO (CCXT) ---
                margin = signal['margin']
                leverage = signal['leverage']
                
                steps = [
                    {'percent': 0.40, 'offset': 0.000, 'type': 'market'},
                    {'percent': 0.30, 'offset': 0.005, 'type': 'limit'},
                    {'percent': 0.30, 'offset': 0.010, 'type': 'limit'}
                ]
                
                mult = -1 if side == 'buy' else 1
                results = []
                for i, step in enumerate(steps):
                    step_margin = margin * step['percent']
                    step_price = entry_price * (1 + (step['offset'] * mult))
                    
                    # Precise Volume/Lots
                    if strategy_type == 'GLN_FX':
                        volume = step_margin # Assume margin is lots for Forex Pellee
                    else:
                        volume = (step_margin * leverage) / step_price
                    
                    req = TradeRequest(
                        symbol=symbol,
                        amount=volume,
                        leverage=leverage,
                        side=side,
                        market_type='forex' if strategy_type == 'GLN_FX' else 'future',
                        user_id=query.from_user.id
                    )
                    
                    res = await self.execution_engine.execute(req)
                    results.append(res)

                # 2. Place Global Stop Loss
                try:
                    total_vol_calc = sum(margin * s['percent'] * leverage / entry_price for s in steps)
                    try:
                        total_vol_calc = self.futures_exchange.amount_to_precision(symbol, total_vol_calc)
                    except: pass
                    
                    sl_side = 'sell' if side == 'buy' else 'buy'
                    params = {'stopPrice': sl_price}
                    if self.futures_exchange.id == 'kucoin':
                         params = {'stopPrice': sl_price, 'type': 'stop'}
                    
                    await asyncio.to_thread(self.futures_exchange.create_order, symbol, 'limit', sl_side, total_vol_calc, sl_price, params)
                    msg_sl = f"✅ حد ضرر در {sl_price} تنظیم شد."
                except Exception as e:
                    msg_sl = f"⚠️ خطا در تنظیم خودکار حد ضرر: {e}"

                status_msg = f"📊 **گزارش اجرای کریپتو (Pellee-ee)**\n\n🔹 نماد: {symbol}\n🔹 جهت: {'LONG' if side == 'buy' else 'SHORT'}\n{msg_sl}\n🚀 معاملات ارسال شدند."
                await query.message.reply_text(status_msg)

        except Exception as e:
            logger.error(f"Execution Error: {e}")
            await query.message.reply_text(f"❌ خطا در اجرای پله‌ای: {e}")

    async def send_telegram_message(self, message, signal_data=None):
        """Helper to send messages to admin, supporting optional signal buttons."""
        if hasattr(self, 'app') and self.app and self.admin_id:
            try:
                reply_markup = None
                if signal_data:
                    logger.info(f"Adding interactive buttons for {signal_data.get('symbol')}...")
                    # Store signal in cache
                    sig_id = f"sig_{self.signal_counter}"
                    self.signal_cache[sig_id] = signal_data
                    self.signal_counter += 1
                    
                    # Create buttons
                    side = signal_data.get('side', 'buy')
                    label = "⚡ تائید و ورود (Trade)"
                    callback = f"exec_sig_{sig_id}"
                    
                    keyboard = [[InlineKeyboardButton(label, callback_data=callback)]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                
                await self.app.bot.send_message(
                    chat_id=self.admin_id, 
                    text=message, 
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Failed to send telegram message: {e}")

    async def daily_report_schedule(self):
        """Runs daily tasks: Report + Auto GLN Start."""
        logger.info("Scheduler started (Daily Report @ 22:15 Local, Auto GLN @ 09:00 NY)")
        while True:
            try:
                # --- Daily Report (22:15 Local) ---
                now = datetime.now()
                # 10:15 PM
                if now.hour == 22 and now.minute == 15:
                    await self.send_daily_report()
                    await asyncio.sleep(60) # Wait for minute to pass
                
                # --- Auto GLN Start (09:00 NY Time) ---
                try:
                    ny_tz = pytz.timezone('America/New_York')
                    now_ny = datetime.now(ny_tz)
                    
                    if now_ny.hour == 9 and now_ny.minute == 0:
                        is_auto = self.db_manager.get_setting('gln_auto', 'False') == 'True'
                        if is_auto:
                            if now_ny.weekday() < 5: # Mon-Fri
                                await self.start_auto_gln_scanner()
                            else:
                                logger.info("Auto GLN skipped (Weekend)")
                        await asyncio.sleep(60)
                except Exception as e:
                    logger.error(f"Auto GLN Error: {e}")
            
            except Exception as e:
                logger.error(f"Scheduler Loop Error: {e}")
            
            await asyncio.sleep(30)

    async def start_auto_gln_scanner(self):
        """Starts GLN for limited symbols (AUTO_SYMBOLS)."""
        msg = "🤖 **شروع اسکن خودکار GLN**\n"
        for symbol_unfmt in AUTO_SYMBOLS:
            symbol = symbol_unfmt.replace('USDT', '/USDT:USDT')
            if symbol not in self.gln_strategies:
                try:
                    # Initialize with 0 amount (Monitoring Only)
                    gln = GLNStrategy(
                        self.execution_engine, symbol, initial_investment=0, 
                        side=None, market_type='future', leverage=1, 
                        db_manager=self.db_manager, message_callback=self.send_telegram_message,
                        position_tracker=self.position_tracker
                    )
                    await gln.initialize()
                    self.gln_strategies[symbol] = gln
                    asyncio.create_task(self.run_gln_loop(gln))
                    msg += f"✅ {symbol}\n"
                except Exception as e:
                    logger.error(f"Auto GLN Fail {symbol}: {e}")
                    msg += f"❌ {symbol}: {e}\n"
        
        await self.send_telegram_message(msg)

    async def qgln_auto_toggle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Toggles Auto GLN Mode."""
        current = self.db_manager.get_setting('gln_auto', 'False') == 'True'
        new_state = not current
        self.db_manager.set_setting('gln_auto', str(new_state))
        
        status = "✅ فعال" if new_state else "❌ غیرفعال"
        await update.effective_message.reply_text(f"🤖 **حالت خودکار GLN**\nوضعیت جدید: {status}\n\n(در حالت فعال، هر روز ساعت 14:00 UTC ده ارز برتر اسکن می‌شوند)")

    async def equity_snapshot_task(self):
        """Background task to take equity snapshots every 15 minutes."""
        while True:
            try:
                await self.take_equity_snapshot()
            except Exception as e:
                logger.error(f"Equity Snapshot Task Error: {e}")
            await asyncio.sleep(15 * 60) # 15 minutes

    async def take_equity_snapshot(self):
        """Calculates and saves the current account equity to database."""
        try:
            res = await self.position_tracker.calculate_full_equity()
            total = res['total']
            spot = res['spot']
            futures = res['futures']
            unrealized = res['unrealized']
            
            # Save to DB
            self.db_manager.save_equity_snapshot(total, spot, futures, unrealized)
            logger.info(f"📊 Equity Snapshot Saved: Total={total:.2f}, Spot={spot:.2f}, Futures={futures:.2f}, Unrealized={unrealized:.2f}")
            return res
        except Exception as e:
            logger.error(f"Failed to take equity snapshot: {e}")
            return None

    async def _q_candle_collector_loop(self):
        """جمع‌آوری کندل‌های ۵ دقیقه‌ای برای BTC, ETH, BNB جهت محاسبات آماری Q"""
        symbols = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'BNB/USDT:USDT']
        logger.info(f"Q-Stats: Starting candle collector for {symbols}")
        
        while True:
            try:
                # Only collect during or slightly before NY Session to ensure data is ready
                # For simplicity, we collect every 15 minutes
                for symbol in symbols:
                    try:
                        candles = await async_run(self.futures_exchange.fetch_ohlcv, symbol, '5m', limit=100)
                        if candles:
                            self.db_manager.save_candles(symbol.replace('/USDT:USDT', 'USDT'), candles)
                    except Exception as e:
                        logger.error(f"Q-Stats Collector Error ({symbol}): {e}")
                
                await asyncio.sleep(15 * 60) # Each 15 mins
            except Exception as e:
                logger.error(f"Q-Stats Collector Loop Error: {e}")
                await asyncio.sleep(60)

    async def post_init(self, application: Application):
        self.app = application
        # Restore strategies
        asyncio.create_task(self.load_active_strategies())
        # Start Scheduler
        self.scheduler_task = asyncio.create_task(self.daily_report_schedule())
        # Start Equity Snapshots (Every 15 mins)
        self.equity_task = asyncio.create_task(self.equity_snapshot_task())
        # Start Q-Stats Candle Collector
        self.q_collector_task = asyncio.create_task(self._q_candle_collector_loop())
        logger.info("Background tasks started (Strategies + Scheduler + Equity + Q-Stats)")

    def run(self):
        # Build application with post_init
        application = Application.builder().token(self.bot_token).post_init(self.post_init).build()
        
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("spot", self.spot_command))
        application.add_handler(CommandHandler("future", self.future_command))
        application.add_handler(CommandHandler("smart", self.smart_command))
        application.add_handler(CommandHandler("long", self.long_command))
        application.add_handler(CommandHandler("short", self.short_command))

        application.add_handler(CommandHandler("scan", self.scan_command))
        application.add_handler(CommandHandler("snipe", self.snipe_command))
        application.add_handler(CommandHandler("status", self.status_command))
        application.add_handler(CommandHandler("qstatus", self.qstatus_command))
        application.add_handler(CommandHandler("qstats", self.qstats_command))
        application.add_handler(CommandHandler("auto", self.qgln_auto_toggle))
        application.add_handler(CommandHandler("stop", self.stop_command))
        application.add_handler(CommandHandler("positions", self.positions_command))
        application.add_handler(CommandHandler("close", self.close_command))
        application.add_handler(CommandHandler("pnl", self.pnl_command))
        application.add_handler(CommandHandler("balance", self.balance_command))
        application.add_handler(CommandHandler("ping", self.ping_command))
        application.add_handler(CommandHandler("test_sig", self.test_sig_command))
        application.add_handler(CommandHandler("dashboard", self.dashboard_command))
        application.add_handler(CommandHandler("gln_fx", self.gln_forex_command))
        # New Trade Wizard Handler
        wiz_trade_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex(r"^🚀 معامله جدید$"), self.wiz_start),
                CommandHandler("wiz", self.wiz_start),
                CallbackQueryHandler(self.wiz_start, pattern="^wiz_start$")
            ],
            states={
                WIZ_MARKET: [CallbackQueryHandler(self.wiz_market, pattern=r"^TRD\|MARKET\|")],
                WIZ_SYMBOL: [
                    CallbackQueryHandler(self.wiz_symbol, pattern=r"^TRD\|SYMBOL\|"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.wiz_symbol_search)
                ],
                WIZ_CUSTOM_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.wiz_symbol_search)],
                WIZ_SIDE: [CallbackQueryHandler(self.wiz_side, pattern=r"^TRD\|SIDE\|")],
                WIZ_MARGIN: [CallbackQueryHandler(self.wiz_margin, pattern=r"^TRD\|MARGIN\|")],
                WIZ_CUSTOM_MARGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.wiz_margin_custom)],
                WIZ_LEVERAGE: [CallbackQueryHandler(self.wiz_leverage, pattern=r"^TRD\|LEVERAGE\|")],
                WIZ_TYPE: [CallbackQueryHandler(self.wiz_type, pattern=r"^TRD\|TYPE\|")],
                WIZ_CONFIRM: [CallbackQueryHandler(self.wiz_execute, pattern=r"^TRD\|EXECUTE")],
            },
            fallbacks=[
                CallbackQueryHandler(self.wiz_cancel, pattern=r"^TRD\|CANCEL"),
                CallbackQueryHandler(self.wiz_back, pattern=r"^TRD\|BACK\|"),
                CommandHandler("cancel", self.wiz_cancel),
                MessageHandler(filters.Regex("^❌ انصراف$"), self.wiz_cancel)
            ],
            name="trade_wizard",
            persistent=False
        )
        application.add_handler(wiz_trade_handler)

        # Legacy GLN Conversation Handler (Consider merging later)
        qgln_conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("qgln", self.qgln_entry),
                CallbackQueryHandler(self.handle_callback, pattern="^exec_sig_")
            ],
            states={
                GLN_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.gln_get_symbol)],
                GLN_LEVERAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.gln_get_leverage)],
                GLN_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.gln_get_amount)],
                SIG_MARGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_sig_margin)],
                SIG_LEVERAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_sig_leverage)],
            },
            fallbacks=[
                CommandHandler("cancel", self.gln_cancel),
                CallbackQueryHandler(self.handle_callback, pattern="^cancel_wizard")
            ],
            allow_reentry=True
        )
        application.add_handler(qgln_conv_handler)

        # Main Menu Button Handlers
        application.add_handler(MessageHandler(filters.Regex("^📌 پوزیشنها$"), self.positions_command))
        application.add_handler(MessageHandler(filters.Regex("^🛟 کمک سریع$"), self.help_command))
        application.add_handler(MessageHandler(filters.Regex("^⚙️ ریسک$"), self.status_command))
        application.add_handler(MessageHandler(filters.Regex("^🧠 استراتژیها$"), self.qstatus_command))
        application.add_handler(MessageHandler(filters.Regex(r'^(🚀 CRYPTO|🌍 FOREX) Mode$'), self.handle_mode_button))

        application.add_handler(CommandHandler("daily_report", self.daily_report_command))
        application.add_handler(CommandHandler("auto", self.qgln_auto_toggle))
        application.add_handler(CommandHandler("switch_mode", self.switch_mode_command))
        application.add_handler(CommandHandler("clear", self.clear_command))
        application.add_handler(CommandHandler("where", self.where_command))

        application.add_handler(CallbackQueryHandler(self.handle_callback))
        
        logger.info("Bot starting polling...")
        application.run_polling(drop_pending_updates=True)

    async def update_mode_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Sends or Edits and PINS the mode panel."""
        mode = self.db_manager.get_setting('bot_mode', 'CRYPTO')
        mode_icon = "🚀" if mode == 'CRYPTO' else "🌍"
        mode_text = "CRYPTO (CoinEx)" if mode == 'CRYPTO' else "FOREX (MetaTrader 5)"
        inverse_mode = "Forex" if mode == 'CRYPTO' else "Crypto"
        
        # Dynamic Message Text
        if mode == 'CRYPTO':
            msg_text = (
                f"🤖 <b>Spider Bot Control Panel</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔰 <b>MODE:</b> {mode_icon} <code>{mode_text}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                
                f"📈 <b>معاملات:</b>\n"
                f"• /spot SYMBOL USDT - خرید اسپات\n"
                f"• /future SYMBOL USDT LEV - معامله فیوچرز\n"
                f"• /close SYMBOL - بستن پوزیشن\n\n"
                
                f"🧠 <b>تحلیل هوشمند:</b>\n"
                f"• /smart SYMBOL - تحلیل AI + سیگنال\n"
                f"• /scan - اسکن بازار برای فرصت‌ها\n"
                f"• /snipe SYMBOL - ورود سریع\n\n"
                
                f"📊 <b>استراتژی GLN:</b>\n"
                f"• /qgln - راه‌اندازی GLN (wizard)\n"
                f"• /auto - روشن/خاموش اسکنر خودکار\n\n"
                
                f"📋 <b>مدیریت:</b>\n"
                f"• /status - وضعیت ربات‌های فعال\n"
                f"• /positions - پوزیشن‌های باز\n"
                f"• /balance - موجودی تتر (USDT)\n"
                f"• /pnl - سود/زیان روزانه\n"
                f"• /stop SYMBOL - توقف استراتژی\n"
                f"• /dashboard - داشبورد کلی"
            )
            # Crypto Keyboard
            keyboard = [
                [InlineKeyboardButton("🚀 معامله جدید (Wizard)", callback_data='wiz_start')],
                [InlineKeyboardButton("📊 Spot", callback_data='help_spot'), 
                 InlineKeyboardButton("🔫 Future", callback_data='help_future'),
                 InlineKeyboardButton("🎯 Snipe", callback_data='help_snipe')],
                [InlineKeyboardButton("🧠 Smart AI", callback_data='help_smart'), 
                 InlineKeyboardButton("🔍 Scan", callback_data='help_scan')],
                [InlineKeyboardButton("📈 GLN Setup", callback_data='help_qgln'),
                 InlineKeyboardButton("⚡ Auto GLN", callback_data='help_auto')],
                [InlineKeyboardButton("📋 Status", callback_data='cmd_status'),
                 InlineKeyboardButton("💰 Positions", callback_data='cmd_positions')],
                [InlineKeyboardButton("🔓 بازکردن قفل پردازش (Reset)", callback_data='clear_locks')],
                [InlineKeyboardButton("🔄 Switch to Forex Mode 🌍", callback_data='switch_mode')]
            ]
        else:
            msg_text = (
                f"🤖 <b>Spider Bot Control Panel</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔰 <b>MODE:</b> {mode_icon} <code>{mode_text}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                
                f"💱 <b>معاملات فارکس:</b>\n"
                f"• /long SYMBOL LOTS - خرید/لانگ\n"
                f"• /short SYMBOL LOTS - فروش/شورت\n\n"
                
                f"📊 <b>استراتژی GLN:</b>\n"
                f"• /gln_fx SYMBOL LOTS - GLN برای فارکس\n\n"
                
                f"📋 <b>مدیریت:</b>\n"
                f"• /status - وضعیت MT5\n"
                f"• /positions - پوزیشن‌های باز\n"
                f"• /pnl - سود/زیان روزانه\n\n"
                
                f"⚠️ <i>نیاز به MetaTrader 5 روی سیستم</i>"
            )
            # Forex Keyboard
            keyboard = [
                [InlineKeyboardButton("🚀 معامله جدید (Wizard)", callback_data='wiz_start')],
                [InlineKeyboardButton("🟢 Long/Buy", callback_data='help_long'), 
                 InlineKeyboardButton("🔴 Short/Sell", callback_data='help_short')],
                [InlineKeyboardButton("📈 GLN Forex", callback_data='help_gln_fx')],
                [InlineKeyboardButton("📋 Status", callback_data='cmd_status'),
                 InlineKeyboardButton("💰 Positions", callback_data='cmd_positions')],
                [InlineKeyboardButton("🔓 بازکردن قفل پردازش (Reset)", callback_data='clear_locks')],
                [InlineKeyboardButton("🔄 Switch to Crypto Mode 🚀", callback_data='switch_mode')]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Check if this is a callback (edit) or a command (send new)
        if update.callback_query:
            try:
                await update.effective_message.edit_text(msg_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
                # Ensure pinned
                try:
                    await context.bot.pin_chat_message(chat_id=update.effective_chat.id, message_id=update.effective_message.message_id)
                except:
                    pass
            except Exception as e:
                logger.warning(f"Could not edit panel: {e}")
                # Fallback: send new
                sent = await update.effective_message.reply_text(msg_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
                try:
                    await context.bot.pin_chat_message(chat_id=update.effective_chat.id, message_id=sent.message_id)
                except: pass
        else:
            # Send new message
            sent_msg = await update.effective_message.reply_text(msg_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            
            # Pin it
            try:
                await context.bot.pin_chat_message(chat_id=update.effective_chat.id, message_id=sent_msg.message_id)
            except Exception as e:
                logger.error(f"Failed to pin message: {e}")

    async def clear_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Emergency reset for the user's processing lock."""
        user_id = update.effective_user.id if update.effective_user else 0
        if user_id > 0:
            self.user_callback_locks[user_id] = False
            await update.effective_message.reply_text("✅ تمام قفل‌های پردازش شما باز شد. می‌توانید دوباره از دکمه‌ها استفاده کنید.")

    async def long_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.db_manager.get_setting('bot_mode', 'CRYPTO') == 'CRYPTO':
             await update.effective_message.reply_text("❌ لطفاً ابتدا به حالت FOREX بروید (/switch_mode).")
             return
        await update.effective_message.reply_text("⏳ دستور /long برای فارکس در حال تعمیر است...")

    async def short_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.db_manager.get_setting('bot_mode', 'CRYPTO') == 'CRYPTO':
             await update.effective_message.reply_text("❌ لطفاً ابتدا به حالت FOREX بروید (/switch_mode).")
             return
        await update.effective_message.reply_text("⏳ دستور /short برای فارکس در حال تعمیر است...")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Show Welcome with main menu keyboard
        msg = (
            "🚀 **به ربات پیشرفته Spider خوش آمدید!**\n\n"
            "من دستیار هوشمند شما برای معامله در بازارهای Crypto و Forex هستم.\n"
            "لطفاً برای شروع یکی از گزینه‌های زیر را انتخاب کنید:"
        )
        await update.effective_message.reply_text(msg, reply_markup=self.get_main_menu_keyboard(), parse_mode=ParseMode.HTML)
        # Also show the mode panel
        await self.update_mode_panel(update, context)

    def get_main_menu_keyboard(self):
        mode = self.db_manager.get_setting('bot_mode', 'CRYPTO')
        mode_btn = "🚀 CRYPTO Mode" if mode == 'CRYPTO' else "🌍 FOREX Mode"
        keyboard = [
            ["🚀 معامله جدید", "📌 پوزیشنها"],
            ["⚙️ ریسک", "🧠 استراتژیها"],
            [mode_btn, "🛟 کمک سریع"]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comprehensive help with all commands and examples."""
        mode = self.db_manager.get_setting('bot_mode', 'CRYPTO')
        
        help_text = (
            "<b>📚 راهنمای کامل Spider Bot</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        
        if mode == 'CRYPTO':
            help_text += (
                "<b>🔷 حالت فعلی: CRYPTO (CoinEx)</b>\n\n"
                
                "<b>📈 دستورات معاملات:</b>\n"
                "<code>/spot BTC 100</code> - خرید 100$ بیت‌کوین اسپات\n"
                "<code>/future ETH 50 10</code> - لانگ 50$ اتریوم با 10x\n"
                "<code>/close BTC SPOT</code> - بستن پوزیشن اسپات\n"
                "<code>/close ETH FUTURE</code> - بستن پوزیشن فیوچرز\n\n"
                
                "<b>🧠 تحلیل هوشمند:</b>\n"
                "<code>/smart BTC 100 5</code> - تحلیل AI + معامله\n"
                "<code>/scan</code> - اسکن بازار برای فرصت‌ها\n"
                "<code>/snipe SOL 50</code> - ورود سریع با SL/TP اتوماتیک\n\n"
                
                "<b>📊 استراتژی GLN:</b>\n"
                "<code>/qgln</code> - راه‌اندازی GLN (قدم به قدم)\n"
                "<code>/auto</code> - روشن/خاموش سیگنال خودکار\n\n"
                
                "<b>📋 مدیریت و گزارش:</b>\n"
                "<code>/status</code> - وضعیت ربات‌های فعال\n"
                "<code>/positions</code> - پوزیشن‌های باز\n"
                "<code>/balance</code> - موجودی USDT (Spot/Futures)\n"
                "<code>/pnl</code> - سود/زیان 7 روز اخیر\n"
                "<code>/pnl 1d</code> - سود/زیان امروز\n"
                "<code>/pnl 30d</code> - سود/زیان 30 روز\n"
                "<code>/pnl 7d spot</code> - فقط اسپات\n"
                "<code>/pnl 7d future</code> - فقط فیوچرز\n"
                "<code>/dashboard</code> - داشبورد کلی\n"
                "<code>/daily_report</code> - گزارش روزانه\n"
                "<code>/stop BTC</code> - توقف استراتژی روی بیت‌کوین\n\n"
                
                "<b>⚙️ تنظیمات:</b>\n"
                "<code>/switch_mode</code> - تغییر به حالت فارکس\n"
                "<code>/ping</code> - تست اتصال\n"
            )
        else:
            help_text += (
                "<b>🌍 حالت فعلی: FOREX (MetaTrader 5)</b>\n\n"
                
                "<b>💱 دستورات معاملات:</b>\n"
                "<code>/long XAUUSD 0.01</code> - خرید طلا 0.01 لات\n"
                "<code>/short EURUSD 0.1</code> - فروش یورو 0.1 لات\n"
                "<code>/long XAUUSD 0.01 50 100</code> - با SL/TP\n\n"
                
                "<b>📊 استراتژی GLN:</b>\n"
                "<code>/gln_fx XAUUSD 0.01</code> - GLN برای طلا\n\n"
                
                "<b>📋 مدیریت و گزارش:</b>\n"
                "<code>/status</code> - وضعیت MT5\n"
                "<code>/positions</code> - پوزیشن‌های باز\n"
                "<code>/pnl</code> - سود/زیان 7 روز اخیر\n"
                "<code>/pnl 1d</code> - سود/زیان امروز\n"
                "<code>/dashboard</code> - داشبورد کلی\n\n"
                
                "<b>⚙️ تنظیمات:</b>\n"
                "<code>/switch_mode</code> - تغییر به حالت کریپتو\n"
                "<code>/ping</code> - تست اتصال\n"
            )
        
        help_text += (
            "\n━━━━━━━━━━━━━━━━━━━━\n"
            "💡 <b>نکته:</b> /start برای نمایش پنل کنترل"
        )
        
        await update.effective_message.reply_text(help_text, parse_mode=ParseMode.HTML)

    async def handle_mode_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handles press on the mode button in Reply Keyboard."""
        # Switch mode
        current_mode = self.db_manager.get_setting('bot_mode', 'CRYPTO')
        new_mode = 'FOREX' if current_mode == 'CRYPTO' else 'CRYPTO'
        self.db_manager.set_setting('bot_mode', new_mode)
        
        # Update keyboard to the FULL main menu
        keyboard = self.get_main_menu_keyboard()
        
        # Confirmation message with updated keyboard
        mode_icon = "🚀" if new_mode == 'CRYPTO' else "🌍"
        await update.effective_message.reply_text(
            f"✅ Mode تغییر کرد به: {mode_icon} {new_mode}",
            reply_markup=keyboard
        )
        
        # Also update the pinned panel
        await self.update_mode_panel(update, context)

    async def switch_mode_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Switches between Crypto and Forex modes."""
        current_mode = self.db_manager.get_setting('bot_mode', 'CRYPTO')
        logger.info(f"DEBUG: Switch Mode requested. Current in DB: {current_mode}")
        
        new_mode = 'FOREX' if current_mode == 'CRYPTO' else 'CRYPTO'
        logger.info(f"DEBUG: Setting new mode to: {new_mode}")
        
        self.db_manager.set_setting('bot_mode', new_mode)
        
        # Verify persistence
        saved_mode = self.db_manager.get_setting('bot_mode', 'FAIL')
        logger.info(f"DEBUG: Verified DB mode: {saved_mode}")
        
        # Toast notification
        if update.callback_query:
            await update.callback_query.answer(f"Switched to {new_mode} Mode! 🔄")
        
        # Update Panel (Edit)
        await self.update_mode_panel(update, context)


    async def gln_forex_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Started GLN for Forex: /gln_fx SYMBOL LOTS"""
        if self.db_manager.get_setting('bot_mode', 'CRYPTO') != 'FOREX':
             await update.effective_message.reply_text("❌ لطفاً ابتدا به حالت Forex بروید.")
             return

        if len(context.args) < 2:
            await update.effective_message.reply_text("فرمت: /gln_fx SYMBOL LOTS\nمثال: /gln_fx XAUUSD 0.01")
            return

        symbol = context.args[0].upper()
        try:
             lots = float(context.args[1])
        except:
             await update.effective_message.reply_text("❌ مقدار لات نامعتبر است.")
             return

        # Simple Suffix Check
        # (Ideal world: reuse the check from ForexStrategy, but simple check here is fine)
        # Actually initializing the strategy handles parsing.
        
        await update.effective_message.reply_text(f"🌍 راه‌اندازی GLN روی {symbol}...")
        
        strategy_id = f"GLN_FX_{symbol}_{int(time.time())}"
        gln = GLNForexStrategy(self.execution_engine, symbol, lots, db_manager=self.db_manager, strategy_id=strategy_id, message_callback=self.send_telegram_message, position_tracker=self.position_tracker)
        
        if await gln.initialize():
            self.gln_strategies[strategy_id] = gln # Reusing gln_strategies dict but keys usually strictly symbols for crypto. 
            # Let's verify start_auto_gln_scanner won't conflict. 
            # Crypto keys are 'BTC/USDT:USDT'. This key is unique.
            asyncio.create_task(self.run_gln_forex_loop(gln))
            await update.effective_message.reply_text("✅ GLN Forex فعال شد! (مارکت باز: 09:30 NY)")
        else:
            await update.effective_message.reply_text("❌ خطا در اتصال به نماد (پسوند را چک کنید).")

    async def run_gln_forex_loop(self, strategy: GLNForexStrategy):
        while strategy.running:
            try:
                await strategy.check_market()
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"GLN FX Loop: {e}")
                await asyncio.sleep(60)

    # Callback handler update needed? Yes, adding logic to handle 'switch_mode' callback
    # Handler consolidated with original handle_callback at line ~2056

if __name__ == '__main__':
    try:
        # Load credentials from config (which now loads from .env)
        BOT_TOKEN = config.BOT_TOKEN
        
        # Determine exchange and keys
        if config.EXCHANGE_TYPE == 'kucoin':
            API_KEY = config.KUCOIN_API_KEY
            SECRET = config.KUCOIN_SECRET
            PASSPHRASE = config.KUCOIN_PASSPHRASE
        else:
            API_KEY = config.COINEX_API_KEY
            SECRET = config.COINEX_SECRET
            PASSPHRASE = None
        
        if not BOT_TOKEN or not API_KEY or not SECRET:
            logger.error("Missing credentials! Please check your .env file.")
            sys.exit(1)

        bot = TradingBot(BOT_TOKEN, API_KEY, SECRET, PASSPHRASE)
        bot.run()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"CRITICAL ERROR: {e}")
        # Removed input for background mode # Keep window open if run via double-click
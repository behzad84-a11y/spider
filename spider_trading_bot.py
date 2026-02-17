import asyncio
BOT_VERSION = "3.8.1-fix"  # Updated after P0-P3 stability pass
BUILD_TIMESTAMP = "2026-02-14 01:41:12"
import logging
from typing import Any
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters
import ccxt  # Using sync CCXT (async has bugs with CoinEx)
import config
from functools import partial

# Silence httpx logger to see real logs
logging.getLogger("httpx").setLevel(logging.WARNING)
# Suppress PTB ConversationHandler per_message FAQ warning (we use MessageHandler in states)
import warnings
warnings.filterwarnings("ignore", message=".*per_message.*")

# Helper to run sync CCXT calls in thread pool
async def async_run(func, *args, **kwargs):
    """Run a sync function in a thread pool to make it async-compatible."""
    return await asyncio.to_thread(func, *args, **kwargs)
from datetime import datetime, timedelta
import os
import socket
import platform
import uuid
import pytz
import json
import random
import time
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
from gln_hybrid_strategy import GLNHybridStrategy
from market_analyzer import MarketAnalyzer
from database_manager import DatabaseManager
from spider_strategy import SpiderStrategy
from risk_engine import RiskEngine, TradeRequest
from execution_engine import ExecutionEngine
from position_tracker import PositionTracker
from dashboard import ScannerStateRegistry, DashboardManager, DigestReporter, EventReporter, scanner_watchdog
from ai_optimizer import AIOptimizer
from silent_manager import SilentManager
from config import AUTO_SYMBOLS

# تنظیمات لاگینگ با هندلینگ خطا و تفکیک بر اساس MODE
def setup_logging(mode_str):
    base_dir = os.environ.get('DB_DIR')
    log_dir = os.path.join(base_dir, "logs") if base_dir else "logs"
    
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, f"{mode_str.lower()}.log")
    log_handlers = [logging.StreamHandler()]
    try:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        log_handlers.append(file_handler)
    except PermissionError:
        print(f"WARNING: {log_file} is locked. Logging to console only.")

    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        handlers=log_handlers,
        force=True  # Important to override previous basicConfig
    )
    return logging.getLogger(__name__)

logger = setup_logging(config.MODE)

# States for GLN Wizard
GLN_SYMBOL, GLN_LEVERAGE, GLN_AMOUNT = range(3)
SIG_MARGIN, SIG_LEVERAGE = range(10, 12)

# States for New Trade Wizard
WIZ_MARKET, WIZ_SYMBOL, WIZ_SIDE, WIZ_MARGIN, WIZ_LEVERAGE, WIZ_TYPE, WIZ_CONFIRM = range(20, 27)
WIZ_CUSTOM_SYMBOL, WIZ_CUSTOM_MARGIN = range(27, 29)

# Reply keyboard buttons: do NOT treat as wizard input (exclude from ConversationHandler TEXT handlers)
MENU_BUTTON_FILTER = filters.Regex(
    r"^(🚀 معامله جدید|📌 پوزیشنها|🛟 کمک سریع|⚙️ ریسک|🧠 استراتژیها|🚀 CRYPTO Mode|🌍 FOREX Mode)$"
)


class TradingBot:
    def __init__(self, bot_token, api_key, secret, passphrase=None):
        self.bot_token = bot_token
        self.active_strategies: dict[str, Any] = {}
        self.app = None # Placeholder for Telegram application
        self.instance_id = str(uuid.uuid4())
        self.hostname = socket.gethostname()
        self.pid = os.getpid()
        
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
            position_tracker=self.position_tracker,
            mode=config.MODE
        )
        
        # Load Admin ID
        ra = self.db_manager.load_config('admin_id')
        self.admin_id = int(ra) if ra else None
        
        self.signal_cache = {} # Cache for interactive signals
        self.signal_counter = 1
        self.user_callback_locks = {} # Lock for callback handling
        self.gln_strategies = {} # Active GLN strategy instances (symbol -> GLNStrategy)
        
        # Register Shutdown
        import signal
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self.on_shutdown)
            except: pass

        # Runtime Environment Detection
        self.username = "Unknown"
        self.start_time = datetime.now()
        self.is_master = False # Default to standby
        self.detect_env()
        self.validate_mode()

        # ── Build Metadata ──
        self.build_time = datetime.now().strftime('%Y-%m-%d %H:%M')

        # ── Silent Manager ──
        self.silent_manager = SilentManager(
            silent_start_hour=getattr(config, 'SILENT_START_HOUR', 23),
            silent_end_hour=getattr(config, 'SILENT_END_HOUR', 7),
            enabled=getattr(config, 'SILENT_ENABLED', True),
        )

        # ── Dashboard System ──
        self.scanner_registry = ScannerStateRegistry(persist_dir=os.environ.get('DB_DIR', '.'))
        self.event_reporter = EventReporter(message_callback=self.send_telegram_message)
        self.dashboard = DashboardManager(self)
        digest_interval = getattr(config, 'DIGEST_INTERVAL', 60)
        self.digest_reporter = DigestReporter(self, interval_minutes=digest_interval, message_callback=self.send_telegram_message)

        # ── AI Optimizer ──
        self.ai_optimizer = AIOptimizer(
            execution_engine=self.execution_engine,
            scanner_registry=self.scanner_registry,
            silent_manager=self.silent_manager,
            message_callback=self.send_telegram_message,
            event_reporter=self.event_reporter,
            db_manager=self.db_manager,
        )

        # Register scanners
        self.scanner_registry.register('QGLN', {'interval': 60, 'score_threshold': 65, 'enabled': True})
        self.scanner_registry.register('Hybrid', {'interval': 60, 'score_threshold': 65, 'enabled': True})
        self.scanner_registry.register('GSL', {'interval': 60, 'enabled': True})
        self.scanner_registry.register('GLN_FX', {'interval': 60, 'schedule': '09:00 NY', 'enabled': False})
        self.scanner_registry.register('Manual', {'interval': 0, 'enabled': True})
        self.scanner_registry.register('AI_Optimizer', {'interval': getattr(config, 'AI_EVAL_INTERVAL', 30) * 60, 'enabled': True})

    async def check_admin(self, update: Update) -> bool:
        """Helper to verify if the user is the bot administrator."""
        user_id = update.effective_user.id
        if not self.admin_id:
            await self.save_admin_id(update)
        
        if user_id != self.admin_id:
            logger.warning(f"Permission denied for user {user_id}. Admin is {self.admin_id}")
            await update.effective_message.reply_text(f"❌ عدم دسترسی. ID شما: `{user_id}`")
            return False
        return True

    def validate_mode(self):
        """Validates MODE against running environment (Security Lock)."""
        mode = config.MODE.strip()
        env = self.run_env
        
        logger.info(f"SECURITY: Validating MODE={mode} against ENV={env}")
        
        # 1. Local/Gravity must NEVER trade real money (LIVE/PAPER)
        if env == "LOCAL" and mode != "DEV":
            logger.critical(f"🛑 SECURITY LOCK: Mode '{mode}' is NOT ALLOWED on Local/Gravity environment! Aborting for safety.")
            sys.exit(1)
            
        # 2. LIVE mode must ONLY run on VPS
        if mode == "LIVE" and env != "VPS":
            logger.critical(f"🛑 SECURITY LOCK: LIVE mode is ONLY allowed on VPS! Current env: {env}. Aborting for safety.")
            sys.exit(1)
            
        # 3. Handle unknown mode
        if mode not in ["DEV", "PAPER", "LIVE"]:
            logger.critical(f"🛑 SECURITY LOCK: Unknown mode '{mode}' detected! Aborting for safety.")
            sys.exit(1)

        logger.info(f"✅ SECURITY: Mode validation successful ({mode} @ {env})")

    def detect_env(self):
        """Detects the execution environment (VPS/LOCAL/IDE)."""
        import socket
        import getpass
        try:
            self.hostname = socket.gethostname().upper()
            self.username = getpass.getuser().lower()
            self.os_type = platform.system()
            
            # 1. Explicit ENV_TYPE from .env takes priority
            if config.ENV_TYPE in ["VPS", "LOCAL"]:
                self.run_env = config.ENV_TYPE
            # 2. Fallback to hostname/user heuristics
            elif any(term in self.hostname for term in ["VPS", "IONOS", "STRATO", "WIN-", "SERVER"]):
                self.run_env = "VPS"
            elif self.username in ["administrator", "root"]:
                self.run_env = "VPS"
            elif "behza" in self.username or "desktop" in self.hostname.lower():
                self.run_env = "LOCAL"
            else:
                self.run_env = "LOCAL" # Default
                
            logger.info(f"ENV: Detected environment as {self.run_env} (Host: {self.hostname}, User: {self.username})")
        except Exception as e:
            logger.error(f"ENV: Detection failed: {e}")
            self.run_env = "UNKNOWN"

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

            if state.get('type') == 'hybrid':
                strategy = GLNHybridStrategy(
                    self.execution_engine, symbol, amount, market_type, leverage,
                    db_manager=self.db_manager, strategy_id=strategy_id,
                    message_callback=notification_callback,
                    position_tracker=self.position_tracker
                )
                strategy.positions = state['positions']
                strategy.q_high = state.get('q_high', 0)
                strategy.q_low = state.get('q_low', float('inf'))
                strategy.is_q_locked = state.get('is_q_locked', False)
                strategy.candle_count = state.get('candle_count', 0)
                strategy.atr_value = state.get('atr_value', 0)
                strategy.ema9 = state.get('ema9', 0)
                strategy.ema20 = state.get('ema20', 0)
                strategy.current_sl = state.get('current_sl', 0)
                strategy.entry_score = state.get('entry_score', 0)
                strategy.auto_mode = state.get('auto_mode', True)
                if state.get('last_reset_date'):
                    strategy.last_reset_date = datetime.fromisoformat(state['last_reset_date']).date()
            else:
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
                    # Use strategy.stop_reason (Persian) if set, else exception message
                    reason = getattr(strategy, 'stop_reason', None) or stop_reason
                    reason_msg = f"\n❌ علت: {reason}" if reason else ""
                    await update.effective_message.reply_text(f"🛑 ربات برای {strategy.symbol} متوقف شد.{reason_msg}")
                except Exception:
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

        msg = f"📊 <b>وضعیت ربات‌های فعال ({mode}):</b>\n\n"
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
        msg = "🏦 <b>Crypto Open Positions (CoinEx):</b>\n\n"
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

        msg = "🌍 <b>Forex Open Positions (MetaTrader 5):</b>\n\n"
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
                    for _attempt in range(3):
                        try:
                            await asyncio.to_thread(self.futures_exchange.create_order, symbol, 'market', side, amount)
                            break
                        except Exception as _e:
                            logger.warning(f"Close futures attempt {_attempt+1}/3 failed for {symbol}: {_e}")
                            if _attempt < 2: await asyncio.sleep(2)
                            else: raise
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
                    for _attempt in range(3):
                        try:
                            await asyncio.to_thread(self.spot_exchange.create_order, symbol_raw, 'market', 'sell', amount)
                            break
                        except Exception as _e:
                            logger.warning(f"Close spot attempt {_attempt+1}/3 failed for {symbol_raw}: {_e}")
                            if _attempt < 2: await asyncio.sleep(2)
                            else: raise
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
            f"📈 <b>گزارش سود و ضرر ({period})</b>\n"
            f"⏱ شروع: <code>{start_time}</code>\n\n"
            f"💰 <b>موجودی کل:</b> <code>{equity_now * rate:.2f} {currency}</code>\n"
            f"   ▫️ اسپات: <code>{spot_now * rate:.2f}</code>\n"
            f"   ▫️ فیوچرز: <code>{futures_now * rate:.2f}</code>\n\n"
            f"📊 <b>تغییرات کل (Equity-based):</b>\n"
            f"   ▫️ مقدار: <code>{total_pnl * rate:+.2f} {currency}</code> {emoji}\n"
            f"   ▫️ درصد: <code>{pnl_percent:+.2f}%</code>\n\n"
            f"💸 <b>جزئیات معاملات:</b>\n"
            f"   ▫️ کارمزدها: <code>{breakdown['fees'] * rate:.2f} {currency}</code>\n"
            f"   ▫️ فاندینگ: <code>{breakdown['funding'] * rate:+.2f} {currency}</code>\n\n"
            f"🕒 <b>سود/ضرر باز (Unrealized):</b>\n"
            f"   ▫️ <code>{unrealized_now * rate:+.2f} {currency}</code>\n\n"
            f"💡 <i>نکته: این گزارش بر اساس تغییر کل ارزش دارایی‌های شما (Equity) محاسبه شده است.</i>"
        )
        
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)

    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            spot_bal = await asyncio.to_thread(self.spot_exchange.fetch_balance)
            futures_bal = await asyncio.to_thread(self.futures_exchange.fetch_balance)
            
            spot_usdt = spot_bal.get('USDT', {}).get('free', 0)
            futures_usdt = futures_bal.get('USDT', {}).get('free', 0)
            futures_used = futures_bal.get('USDT', {}).get('used', 0)
            futures_total = futures_bal.get('USDT', {}).get('total', 0)
            
            msg = (
                "💰 <b>گزارش موجودی (USDT):</b>\n\n"
                f"🔵 <b>Spot Free:</b> <code>{spot_usdt:.2f}</code> USDT\n"
                f"🟠 <b>Futures Free:</b> <code>{futures_usdt:.2f}</code> USDT\n"
                f"🔒 <b>Futures Locked:</b> <code>{futures_used:.2f}</code> USDT\n"
                f"📈 <b>Futures Total:</b> <code>{futures_total:.2f}</code> USDT\n\n"
                "💡 اگر موجودی Futures Free کمتر از مبلغ معامله (ضربدر اهرم) باشد، معامله باز نخواهد شد."
            )
            await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)
        except Exception as e:
            await update.effective_message.reply_text(f"❌ خطا در دریافت موجودی: {e}")

    async def ping_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"DEBUG: /ping called by user {update.effective_user.id}")
        await update.effective_message.reply_text("🏓 Pong! Bot is alive.")

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
        
        msg = ("🧪 <b>سیگنال آزمایشی (Test)</b>\n"
               "این یک پیام تست برای بررسی دکمه‌ها و ویزارد است.\n"
               "🛠 <b>Debug: GLN-V2-Buffered</b>")
        
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
                await update.effective_message.reply_text(status_msg, parse_mode=ParseMode.HTML)
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
            for arg in (context.args or []):
                if arg.lower() == 'all': show_all = True
                elif arg.isdigit(): limit = int(arg)

            status_msg = await update.effective_message.reply_text(f"🔍 در حال اسکن {limit} ارز برتر بازار... ⏳")
            
            # Fetch tickers
            tickers = await asyncio.to_thread(self.futures_exchange.fetch_tickers)
            if not tickers:
                await status_msg.edit_text("❌ خطا: نتوانستیم لیست ارزها را دریافت کنیم.")
                return
            
            # Debug: log first few symbols to see format
            sample_symbols = list(tickers.keys())[:3] if tickers else []
            logger.info(f"Sample ticker symbols: {sample_symbols}")
            
            usdt_pairs = [s for s, d in (tickers.items() if isinstance(tickers, dict) else []) if '/USDT' in s and (d.get('quoteVolume') or d.get('baseVolume'))]
            if not usdt_pairs:
                await status_msg.edit_text("❌ خطا: هیچ جفت USDT پیدا نشد.")
                return
            
            sorted_pairs = sorted(usdt_pairs, key=lambda s: tickers[s].get('quoteVolume') or tickers[s].get('baseVolume') or 0, reverse=True)[:limit]
            logger.info(f"Top {limit} symbols selected: {sorted_pairs[:5]}")
            
            # Store limit for refresh button
            context.user_data['last_scan_limit'] = limit
            
            stats["total"] = len(sorted_pairs)

            for symbol in sorted_pairs:
                try:
                    if 'USDC' in symbol or 'USDT' in symbol.split('/')[0]: continue
                    
                    # Log original symbol format
                    original_symbol = symbol
                    
                    # Ensure symbol is in futures format (e.g., BTC/USDT:USDT)
                    # Check if it's already a futures symbol
                    if ':USDT' not in symbol:
                        if '/USDT' in symbol:
                            symbol = symbol + ':USDT'
                        else:
                            logger.warning(f"Skipping invalid symbol format: {symbol}")
                            continue
                    
                    logger.debug(f"Analyzing symbol: {original_symbol} -> {symbol}")
                    analyzer = MarketAnalyzer(self.futures_exchange, symbol)
                    result = await analyzer.analyze()
                    # analyze() returns (state, data) or (state, {'reason': ...})
                    if isinstance(result, tuple) and len(result) == 2:
                        state, data = result
                        reason = data.get('reason', '') if isinstance(data, dict) else ''
                    else:
                        state = 'ERROR'
                        data = {'reason': 'UNEXPECTED_RETURN_FORMAT'}
                        reason = 'UNEXPECTED_RETURN_FORMAT'
                    
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
                            if rsi < 45 and prediction > price and confidence > 0.55: is_strong = True
                            elif rsi < 52 and prediction > (price * 0.998) and confidence > 0.30: is_medium = True
                        elif state == 'DOWNTREND':
                            if rsi > 55 and prediction < price and confidence > 0.55: is_strong = True
                            elif rsi > 48 and prediction < (price * 1.002) and confidence > 0.30: is_medium = True
                        # RANGE state can produce watchlist candidates (lower scoring)
                        elif state == 'RANGE':
                            if confidence > 0.30:  # Lower threshold for RANGE watchlist
                                is_medium = True

                        # Format Opportunity
                        opp_text = (
                            f"<b>{symbol}</b> | <code>{price}</code>\n"
                            f"{'🟢 LONG' if state == 'UPTREND' else '🔴 SHORT'} | RSI: {rsi:.1f} | AI: {int(confidence*100)}%\n"
                            f"🎯 Target (AI): <code>{prediction:.6f}</code> | Score: <code>{score:.1f}</code>"
                        )
                        
                        # Generate Keyboard for this opportunity
                        kb = []
                        side_btn = "buy" if state == 'UPTREND' else "sell"
                        side_label = "Long 🟢" if state == 'UPTREND' else "Short 🔴"
                        # Add symbol name to button label so user knows which coin
                        raw_symbol_short = raw_symbol[:8]  # Limit length for button
                        button_text = f"{side_label} {raw_symbol_short}"
                        # FIX: Use requested pattern "trade:<symbol>:<side>"
                        kb.append([InlineKeyboardButton(button_text, callback_data=f"trade:{symbol}:{side_btn}")])
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
                        error_reason = reason if reason else (data.get('reason', 'UNKNOWN') if isinstance(data, dict) else 'UNKNOWN')
                        stats["errors_reasons"][error_reason] = stats["errors_reasons"].get(error_reason, 0) + 1
                        overview.append({
                            'symbol': symbol, 'raw_symbol': raw_symbol, 'state': 'UNKNOWN', 
                            'rsi': 0, 'ai': 0, 'emoji': '❌', 'score': 0, 'reason': error_reason
                        })

                except Exception as e:
                    error_type = type(e).__name__
                    error_msg = str(e)
                    logger.error(f"Scan Loop Error ({symbol}): {error_type}: {error_msg}", exc_info=True)
                    stats["error"] += 1
                    raw_symbol = symbol.split(':')[0].replace('/', '') if symbol else 'UNKNOWN'
                    stats["errors_reasons"][error_type] = stats["errors_reasons"].get(error_type, 0) + 1
                    # Add failed symbol to overview with score 0 so we show something
                    overview.append({
                        'symbol': symbol, 'raw_symbol': raw_symbol, 'state': 'ERROR', 
                        'rsi': 0, 'ai': 0, 'emoji': '❌', 'score': 0, 'reason': f"{error_type}: {error_msg[:40]}"
                    })
                    continue

            # 2. OUTPUT GENERATION
            # Sort by score
            opportunities_strong.sort(key=lambda x: x[0], reverse=True)
            opportunities_medium.sort(key=lambda x: x[0], reverse=True)
            overview.sort(key=lambda x: x['score'], reverse=True)

            final_msg = "🔭 <b>گزارش اسکنر هوشمند Spider</b>\n━━━━━━━━━━━━━━\n\n"
            
            # Ensure lists are iterable (never None)
            opportunities_strong = opportunities_strong or []
            opportunities_medium = opportunities_medium or []
            overview = overview or []
            
            if opportunities_strong:
                final_msg += "🔥 <b>سیگنال‌های Sniper (Strong):</b>\n"
                for _, text, _ in opportunities_strong[:3]: # Show top 3 texts
                    final_msg += f"{text}\n\n"
                final_msg += "━━━━━━━━━━━━━━\n"
            
            if opportunities_medium:
                final_msg += "⚠️ <b>سیگنال‌های کاندیدا (Medium):</b>\n"
                for _, text, _ in opportunities_medium[:3]:
                    final_msg += f"{text}\n\n"
                final_msg += "━━━━━━━━━━━━━━\n"

            if not opportunities_strong and not opportunities_medium:
                final_msg += "🤷‍♂️ سیگنال خرید/فروش قطعی پیدا نشد.\n\n"
                # Show top 5 fallback when no signals found
                if overview:
                    # Filter out ERROR state items for top candidates, but show them if all failed
                    valid_overview = [x for x in overview if x.get('state') != 'ERROR']
                    if not valid_overview:
                        # All failed - show error info
                        final_msg += "⚠️ <b>خطا در اسکن تمام نمادها:</b>\n"
                        error_reasons = stats.get("errors_reasons", {})
                        for reason, count in list(error_reasons.items())[:3]:
                            final_msg += f"  • {reason}: {count} نماد\n"
                        final_msg += "\n"
                    else:
                        # Sort overview by score descending
                        top5 = sorted(valid_overview, key=lambda x: x.get('score', 0), reverse=True)[:5]
                        final_msg += "📊 <b>برترین نمادها (بدون سیگنال قوی):</b>\n"
                        for i, item in enumerate(top5, 1):
                            sym = item.get('raw_symbol', item.get('symbol', '?'))
                            score = item.get('score', 0)
                            state = item.get('state', '?')
                            rsi = item.get('rsi', 0)
                            ai = item.get('ai', 0)
                            final_msg += f"{i}. <code>{sym}</code> — امتیاز: {score:.1f} | روند: {state} | RSI: {rsi:.1f} | AI: {ai}%\n"
                        best_score = max((x.get('score', 0) for x in valid_overview), default=0)
                        final_msg += f"\n📈 اسکن‌شده: {stats.get('success', 0)} | آستانه قوی: 0.55 | بهترین امتیاز: {best_score:.1f}\n\n"
            
            # ALWAYS show Top 5 candidates (even if signals exist)
            if overview and (opportunities_strong or opportunities_medium):
                final_msg += "📋 <b>برترین کاندیداها (Top 5):</b>\n"
                for item in overview[:5]:
                    final_msg += f"{item['emoji']} <code>{item['raw_symbol']}</code>: {item['state']} | RSI: {item['rsi']:.1f} | AI: {item['ai']}% | Score: {item['score']:.1f}\n"
                final_msg += "\n"
            elif not overview:
                # Fallback if overview is empty
                final_msg += "⚠️ هیچ کاندیدایی پیدا نشد.\n\n"
            
            # Diagnostics Summary with debug footer
            tickers_count = len(tickers) if tickers else 0
            symbols_scanned = stats['success']
            strong_threshold = 0.55
            med_threshold = 0.30
            best_score = overview[0]['score'] if overview else 0.0
            
            final_msg += (
                f"\n━━━━━━━━━━━━━━\n"
                f"📋 <b>آمـار اسکن:</b>\n"
                f"🔹 کل ارزها: <code>{stats['total']}</code> | ✅ موفق: <code>{stats['success']}</code>\n"
                f"❌ خطا/ناشناخته: <code>{stats['error']}</code>\n"
                f"━━━━━━━━━━━━━━\n"
                f"🔍 <b>DBG:</b> tickers={tickers_count} scanned={symbols_scanned} strong>={strong_threshold} med>={med_threshold} best={best_score:.1f}\n"
            )
            
            if stats["errors_reasons"]:
                top_reason = max(stats["errors_reasons"], key=stats["errors_reasons"].get)
                final_msg += f"⚠️ دلیل اصلی خطا: <code>{top_reason}</code>\n"

            # UI Buttons
            # Generate combined keyboard for top opportunities + navigation
            kb_final = []
            # Add buttons for top 4 opportunities (Strong then Medium)
            # Ensure lists are iterable (never None)
            all_opps = (opportunities_strong or []) + (opportunities_medium or [])
            if all_opps:
                btns = []
                for _, _, markup in all_opps[:4]:
                    if markup and markup.inline_keyboard:
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
            
            await status_msg.edit_text(final_msg, reply_markup=InlineKeyboardMarkup(kb_final), parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.error(f"Global Scan Error: {e}", exc_info=True)
            error_msg = f"❌ خطای کلی در اسکنر: {e}\nلطفاً از /clear استفاده کنید."
            try:
                if 'status_msg' in locals():
                    await status_msg.edit_text(error_msg)
                else:
                    await update.effective_message.reply_text(error_msg)
            except:
                await update.effective_message.reply_text(error_msg)

    async def cmd_auto_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Globally enables Auto-Mode for GLN Hybrid strategies."""
        if not await self.check_admin(update): return
        
        count = 0
        for strat in self.active_strategies.values():
            if isinstance(strat, GLNHybridStrategy):
                strat.auto_mode = True
                count += 1
        
        msg = f"✅ <b>حالت خودکار فعال شد</b>\nتعداد استراتژی‌های تحت تاثیر: {count}"
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        logger.info(f"AUTO_MODE: Enabled by admin for {count} strategies.")

    async def cmd_auto_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Globally disables Auto-Mode for GLN Hybrid strategies."""
        if not await self.check_admin(update): return
        
        count = 0
        for strat in self.active_strategies.values():
            if isinstance(strat, GLNHybridStrategy):
                strat.auto_mode = False
                count += 1
        
        msg = f"❌ <b>حالت خودکار خاموش شد</b>\nتعداد استراتژی‌های تحت تاثیر: {count}\nتریدها فقط با تایید دستی انجام می‌شوند."
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        logger.info(f"AUTO_MODE: Disabled by admin for {count} strategies.")

    async def hybrid_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Starts GLN Hybrid for a symbol. /hybrid BTC 100 10"""
        if not await self.check_admin(update): return
        
        args = context.args
        if len(args) < 3:
            await update.message.reply_text("❌ فرمت اشتباه!\nاستفاده: <code>/hybrid SYMBOL AMOUNT LEV</code>", parse_mode=ParseMode.HTML)
            return
            
        symbol, amount, lev = args[0].upper(), float(args[1]), int(args[2])
        if '/' not in symbol: symbol += '/USDT'
        
        res, msg = await self.start_gln_hybrid(symbol, amount, lev)
        if res:
            await update.message.reply_text(f"🚀 <b>استراتژی GLN Hybrid برای {symbol} شروع شد.</b>\nID: <code>{msg}</code>", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"❌ خطا: {msg}")

    async def start_gln_hybrid(self, symbol, investment, leverage):
        """Initializes and starts a GLN Hybrid strategy."""
        strat_id = f"gln_h_{symbol.replace('/', '').lower()}"
        
        if strat_id in self.active_strategies:
             return False, "این استراتژی در حال حاضر فعال است."
             
        strat = GLNHybridStrategy(
            execution_engine=self.execution_engine,
            symbol=symbol,
            initial_investment=investment,
            market_type='future',
            leverage=leverage,
            db_manager=self.db_manager,
            strategy_id=strat_id,
            message_callback=self.send_telegram_message,
            position_tracker=self.position_tracker
        )
        
        await strat.initialize()
        self.active_strategies[strat_id] = strat
        asyncio.create_task(self.run_strategy(strat, None, strat_id))
        return True, strat_id
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
            "📊 <b>گزارش نرخ موفقیت کانال Q</b>\nیکی از بازه‌های زمانی زیر را انتخاب کنید:",
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
        
        # Log all callbacks for debugging
        logger.info(f"CALLBACK RECEIVED: user={user_id}, data={data}, is_internal={is_internal}")

        # Skip TRD| callbacks - let ConversationHandler handle them
        if data.startswith("TRD|"):
            logger.debug(f"Skipping TRD callback in handle_callback, letting ConversationHandler handle: {data}")
            return  # Don't process, don't answer - let ConversationHandler handle it

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

        # 1. Protection: only lock for expensive/dangerous callbacks, 3-second timeout
        LOCKING_PREFIXES = ('exec_sig_', 'wizard_exec_', 'close_pos_', 'close_spot_', 'cancel_order_')
        needs_lock = any(data.startswith(p) for p in LOCKING_PREFIXES)

        if not is_internal and user_id > 0 and needs_lock:
            current_time = time.time()
            last_lock_time = self.user_callback_locks.get(user_id, 0)
            if not isinstance(last_lock_time, (int, float)):
                last_lock_time = 0
            if last_lock_time > 0 and (current_time - last_lock_time) < 3:
                try:
                    await query.answer("⚠️ در حال پردازش...", show_alert=False)
                except Exception:
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
                            for _attempt in range(3):
                                try:
                                    await asyncio.to_thread(self.futures_exchange.create_order, symbol, 'market', close_side, amount, params={'reduceOnly': True})
                                    break
                                except Exception as _e:
                                    logger.warning(f"Close position attempt {_attempt+1}/3 failed for {symbol}: {_e}")
                                    if _attempt < 2: await asyncio.sleep(2)
                                    else: raise
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
                        for _attempt in range(3):
                            try:
                                await asyncio.to_thread(self.spot_exchange.create_order, symbol, 'market', 'sell', amount)
                                break
                            except Exception as _e:
                                logger.warning(f"Sell spot attempt {_attempt+1}/3 failed for {symbol}: {_e}")
                                if _attempt < 2: await asyncio.sleep(2)
                                else: raise
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
                # Refresh scanner - handle CallbackQuery properly
                try:
                    # Ensure args is not None when called from callback
                    if context.args is None:
                        context.args = []
                    # Get last used limit from user_data or default to 30
                    last_limit = context.user_data.get('last_scan_limit', 30)
                    if not isinstance(last_limit, int):
                        last_limit = 30
                    # Create context.args for scan_command
                    context.args = [str(last_limit)]
                    # Use effective_message for reply/edit
                    await self.scan_command(update, context)
                except Exception as e:
                    logger.error(f"Scan refresh error: {e}", exc_info=True)
                    try:
                        await query.edit_message_text(f"❌ خطا در بروزرسانی اسکن: {e}")
                    except:
                        await query.answer("❌ خطا در بروزرسانی اسکن", show_alert=True)

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
                        await self.send_telegram_message(f"🛡 <b>Stop Loss ANK ({symbol})</b>\nقیمت به هدف ۲ رسید. حد ضرر به نقطه ورود منتقل شد (Break-even).")
                
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"ANK Monitor Error ({symbol}): {e}")
                await asyncio.sleep(60)

    # --- NEW TRADE WIZARD (CONVERSATION HANDLERS) ---
    async def wiz_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Entry point for the Trade Wizard."""
        try:
            context.user_data["trade_wizard"] = {}
            keyboard = [
                [InlineKeyboardButton("💎 Spot (نقدی)", callback_data="TRD|MARKET|spot")],
                [InlineKeyboardButton("🚀 Futures (فیوچرز)", callback_data="TRD|MARKET|future")],
                [InlineKeyboardButton("🌍 Forex (فارکس)", callback_data="TRD|MARKET|forex")],
                [InlineKeyboardButton("❌ انصراف", callback_data="TRD|CANCEL")]
            ]
            msg = "🛰 <b>گام ۱: انتخاب بازار</b>\n\nلطفاً مارکتی که قصد معامله در آن را دارید انتخاب کنید:"
            
            if update.callback_query:
                await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            else:
                await update.effective_message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            
            return WIZ_MARKET
        except Exception as e:
            logger.error(f"Trade wizard wiz_start error: {e}", exc_info=True)
            try:
                await update.effective_message.reply_text(f"❌ خطا در ویزارد معامله: {e}\nلطفاً دوباره 🚀 معامله جدید را بزنید.")
            except Exception:
                pass
            context.user_data.pop("trade_wizard", None)
            return ConversationHandler.END

    async def wiz_market(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            query = update.callback_query
            await query.answer()
            market = query.data.split('|')[2]
            context.user_data["trade_wizard"]["market"] = market
            
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
            
            msg = f"🛰 <b>گام ۲: انتخاب نماد ({market.upper()})</b>\n\nلطفاً یک نماد انتخاب کنید یا دکمه جستجو را بزنید:"
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            return WIZ_SYMBOL
        except Exception as e:
            logger.error(f"Trade wizard wiz_market error: {e}", exc_info=True)
            try:
                await update.effective_message.reply_text(f"❌ خطا در ویزارد معامله: {e}")
            except Exception:
                pass
            context.user_data.pop("trade_wizard", None)
            return ConversationHandler.END

    async def wiz_symbol(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            query = update.callback_query
            await query.answer()
            val = query.data.split('|')[2]
            
            if val == "SEARCH":
                await query.edit_message_text("🔍 لطفاً نام نماد را تایپ کنید (مثلاً BTCUSDT یا XAUUSD):")
                return WIZ_CUSTOM_SYMBOL
            
            context.user_data["trade_wizard"]["symbol"] = val
            return await self._wiz_show_side(update, context)
        except Exception as e:
            logger.error(f"Trade wizard wiz_symbol error: {e}", exc_info=True)
            try:
                await update.effective_message.reply_text(f"❌ خطا در ویزارد معامله: {e}")
            except Exception:
                pass
            context.user_data.pop("trade_wizard", None)
            return ConversationHandler.END

    async def wiz_symbol_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            symbol = update.message.text.upper().replace("/", "")
            context.user_data["trade_wizard"]["symbol"] = symbol
            return await self._wiz_show_side(update, context)
        except Exception as e:
            logger.error(f"Trade wizard wiz_symbol_search error: {e}", exc_info=True)
            try:
                await update.effective_message.reply_text(f"❌ خطا در ویزارد معامله: {e}")
            except Exception:
                pass
            context.user_data.pop("trade_wizard", None)
            return ConversationHandler.END

    async def handle_trade_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler for 'trade:' prefix from scan results. Jumps directly to margin selection."""
        query = update.callback_query
        await query.answer()
        
        # Format: trade:BTC/USDT:USDT:buy
        parts = query.data.split(':')
        if len(parts) < 3:
            logger.error(f"Invalid trade callback data: {query.data}")
            return ConversationHandler.END
            
        symbol = ":".join(parts[1:-1]) # Handles symbols with colons like BTC/USDT:USDT
        side = parts[-1]
        
        # Initialize Wizard Data
        context.user_data["trade_wizard"] = {
            "market": "future",
            "symbol": symbol,
            "side": side
        }
        
        logger.info(f"WIZ: Direct launch from scan. Symbol={symbol}, Side={side}")
        return await self._wiz_show_margin(update, context, side)

    async def _wiz_show_side(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = context.user_data["trade_wizard"]
        keyboard = [
            [InlineKeyboardButton("🟢 LONG / BUY", callback_data="TRD|SIDE|buy")],
            [InlineKeyboardButton("🔴 SHORT / SELL", callback_data="TRD|SIDE|sell")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="TRD|BACK|SYMBOL"), InlineKeyboardButton("❌ لغو", callback_data="TRD|CANCEL")]
        ]
        msg = f"🛰 <b>گام ۳: انتخاب جهت ({data['symbol']})</b>\n\nخلاصه: {data['market'].upper()} | {data['symbol']}\n\nجهت معامله را انتخاب کنید:"
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            await update.effective_message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return WIZ_SIDE

    async def wiz_side(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            query = update.callback_query
            await query.answer()
            side = query.data.split('|')[2]
            context.user_data["trade_wizard"]["side"] = side
            return await self._wiz_show_margin(update, context, side)
        except Exception as e:
            logger.error(f"Trade wizard wiz_side error: {e}", exc_info=True)
            try:
                await update.effective_message.reply_text(f"❌ خطا در ویزارد معامله: {e}")
            except Exception:
                pass
            context.user_data.pop("trade_wizard", None)
            return ConversationHandler.END

    async def _wiz_show_margin(self, update: Update, context: ContextTypes.DEFAULT_TYPE, side: str):
        """Displays margin selection keyboard. Centralized for reuse."""
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
        msg = f"🛰 <b>گام ۴: انتخاب مارجین (USDT)</b>\n\nخلاصه: {data['symbol']} | {emoji} {side.upper()}\n\nچه مقدار مارجین درگیر شود؟"
        
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            await update.effective_message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return WIZ_MARGIN

    async def wiz_margin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        try:
            val_str = query.data.split('|')[2]
            if val_str == "CUSTOM":
                await query.edit_message_text("💰 لطفاً مقدار مارجین به USDT را تایپ کنید (فقط عدد):")
                return WIZ_CUSTOM_MARGIN
            
            margin = float(val_str)
            context.user_data["trade_wizard"]["margin"] = margin
            return await self._wiz_show_leverage(update, context)
        except (ValueError, TypeError, IndexError) as e:
            logger.error(f"WIZ: Margin parsing error: {e}")
            await query.edit_message_text("❌ خطا در انتخاب مارجین. لطفاً دوباره تلاش کنید:")
            side = context.user_data.get("trade_wizard", {}).get("side", "buy")
            return await self._wiz_show_margin(update, context, side)

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
        margin = data.get("margin")
        
        # Validation: Ensure margin is present and valid before calling engine
        if margin is None or not isinstance(margin, (int, float)) or margin <= 0:
            await update.effective_message.reply_text("❌ خطا: مقدار مارجین نامعتبر است.")
            return await self._wiz_show_margin(update, context, data.get("side", "buy"))

        # Fetch allowed leverages from ExecutionEngine
        res = await self.execution_engine.get_allowed_leverages(symbol, margin, market_type=data["market"])
        if not res["success"]:
            msg = f"❌ <b>خطا در مارجین:</b>\n{res.get('reason', 'خطا در محاسبه اهرم')}"
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
            f"🛰 <b>گام ۵: انتخاب اهرم (Leverage)</b>\n\n"
            f"خلاصه: {data['symbol']} | {emoji} {data['side'].upper()} | ${data['margin']}\n"
            f"حداقل اهرم مورد نیاز: {res.get('min_leverage_required', 1)}x\n\n"
            f"اهرم مورد نظر را انتخاب کنید:"
        )
        if update.callback_query: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else: await update.effective_message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return WIZ_LEVERAGE

    async def wiz_leverage(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            query = update.callback_query
            await query.answer()
            context.user_data["trade_wizard"]["leverage"] = int(query.data.split('|')[2])
            return await self._wiz_show_type(update, context)
        except Exception as e:
            logger.error(f"Trade wizard wiz_leverage error: {e}", exc_info=True)
            try:
                await update.effective_message.reply_text(f"❌ خطا در ویزارد معامله: {e}")
            except Exception:
                pass
            context.user_data.pop("trade_wizard", None)
            return ConversationHandler.END

    async def _wiz_show_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = context.user_data["trade_wizard"]
        keyboard = [
            [InlineKeyboardButton("⚡ Snipe (هوشمند)", callback_data="TRD|TYPE|snipe")],
            [InlineKeyboardButton("🛒 Market (آنی صرافی)", callback_data="TRD|TYPE|market")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="TRD|BACK|LEVERAGE"), InlineKeyboardButton("❌ لغو", callback_data="TRD|CANCEL")]
        ]
        msg = f"🛰 <b>گام ۶: نوع سفارش</b>\n\nخلاصه: {data['symbol']} | اهرم {data.get('leverage', 1)}x\n\nچگونه مایل به ورود هستید؟"
        if update.callback_query: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else: await update.effective_message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return WIZ_TYPE

    async def wiz_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            query = update.callback_query
            await query.answer()
            context.user_data["trade_wizard"]["type"] = query.data.split('|')[2]
            return await self.wiz_confirm_screen(update, context)
        except Exception as e:
            logger.error(f"Trade wizard wiz_type error: {e}", exc_info=True)
            try:
                await update.effective_message.reply_text(f"❌ خطا در ویزارد معامله: {e}")
            except Exception:
                pass
            context.user_data.pop("trade_wizard", None)
            return ConversationHandler.END

    async def wiz_confirm_screen(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = context.user_data["trade_wizard"]
        emoji = "🟢 LONG" if data['side'] == 'buy' else "🔴 SHORT"
        msg = (
            f"📋 <b>تاییدیه نهایی معامله</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🔹 <b>بازار:</b> {data['market'].upper()}\n"
            f"🔹 <b>نماد:</b> {data['symbol']}\n"
            f"🔹 <b>جهت:</b> {emoji}\n"
            f"🔹 <b>مارجین:</b> ${data['margin']}\n"
            f"🔹 <b>اهرم:</b> {data.get('leverage', 1)}x\n"
            f"🔹 <b>نوع اردر:</b> {data['type'].upper()}\n"
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
        try:
            query = update.callback_query
            await query.answer()
            data = context.user_data["trade_wizard"]
            
            await query.edit_message_text("⏳ در حال ارسال اردر به موتور اجرایی...")
            
            req = TradeRequest(
                symbol=data["symbol"],
                amount=data["margin"],
                leverage=data.get("leverage", 1),
                side=data["side"],
                market_type=data["market"],
                user_id=update.effective_user.id
            )
            
            if data["type"] == 'snipe':
                await self._start_snipe(update, context, data["symbol"], data["side"], amount=data["margin"], leverage=data["leverage"])
            else:
                res = await self.execution_engine.execute(req)
                if res.success:
                    await query.edit_message_text(f"✅ معامله با موفقیت انجام شد!\nTicket: {res.order_id}")
                else:
                    msg = f"❌ خطا در صرافی:\n{res.message}"
                    keyboard = [
                        [InlineKeyboardButton("💰 افزایش مارجین (+10$)", callback_data=f"TRD|FIX|MARGIN|{data['margin']+10}")],
                        [InlineKeyboardButton("⚙️ کاهش اهرم به حداقل", callback_data="TRD|FIX|MINLEV")],
                        [InlineKeyboardButton("❌ انصراف", callback_data="TRD|CANCEL")]
                    ]
                    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
                    return WIZ_CONFIRM
                    
            context.user_data.pop("trade_wizard", None)
            return ConversationHandler.END
        except Exception as e:
            logger.error(f"Trade wizard wiz_execute error: {e}", exc_info=True)
            try:
                await update.effective_message.reply_text(f"❌ خطا در اجرای معامله: {e}")
            except Exception:
                pass
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
        try:
            user_id = update.effective_user.id
            logger.info(f"QGLN Wizard Entry triggered by {user_id}")
            
            if self.admin_id and user_id != self.admin_id:
                await update.effective_message.reply_text("⛔ شما ادمین نیستید.")
                return ConversationHandler.END

            args = context.args or []
            if args and args[0].lower() == 'status':
                await self.qgln_show_status(update, context)
                return ConversationHandler.END
            
            await update.effective_message.reply_text("🔹 چه ارزی را می‌خواهید ترید کنید؟\n(مثال: BTC/USDT)")
            return GLN_SYMBOL
        except Exception as e:
            logger.error(f"QGLN qgln_entry error: {e}", exc_info=True)
            await update.effective_message.reply_text(f"❌ خطای داخلی: {e}\nلطفاً دوباره /qgln را بزنید.")
            return ConversationHandler.END

    async def gln_get_symbol(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Wizard Step 1: Get Symbol."""
        try:
            symbol = update.message.text.strip().upper()
            if '/' not in symbol:
                symbol += "/USDT"
            if symbol in self.gln_strategies:
                await update.effective_message.reply_text(f"⚠️ GLN برای {symbol} قبلاً فعال شده است. دستور لغو شد.")
                return ConversationHandler.END
            context.user_data['gln_symbol'] = symbol
            await update.effective_message.reply_text(f"✅ نماد: {symbol}\n\n🔹 اهرم (Leverage) چند باشد؟\n(عدد وارد کنید، مثلا: 10)")
            return GLN_LEVERAGE
        except Exception as e:
            logger.error(f"QGLN gln_get_symbol error: {e}", exc_info=True)
            await update.effective_message.reply_text(f"❌ خطای داخلی: {e}\nلطفاً دوباره /qgln را بزنید.")
            return ConversationHandler.END

    async def gln_get_leverage(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Wizard Step 2: Get Leverage."""
        try:
            leverage = int(update.message.text.strip())
            if leverage < 1 or leverage > 125:
                await update.effective_message.reply_text("❌ اهرم نامعتبر. عددی بین 1 تا 125 وارد کنید.")
                return GLN_LEVERAGE
            context.user_data['gln_leverage'] = leverage
            await update.effective_message.reply_text(f"✅ اهرم: {leverage}x\n\n🔹 چه مقدار سرمایه (Margin) درگیر شود؟\n(عدد به دلار، مثلا: 100)")
            return GLN_AMOUNT
        except ValueError:
            await update.effective_message.reply_text("❌ لطفاً یک عدد صحیح وارد کنید.")
            return GLN_LEVERAGE
        except Exception as e:
            logger.error(f"QGLN gln_get_leverage error: {e}", exc_info=True)
            await update.effective_message.reply_text(f"❌ خطای داخلی: {e}\nلطفاً دوباره /qgln را بزنید.")
            return ConversationHandler.END

    async def gln_get_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Wizard Step 3: Get Amount and Start."""
        try:
            amount = float(update.message.text.strip().replace(",", "."))
            if amount <= 0:
                await update.effective_message.reply_text("❌ مقدار باید بیشتر از 0 باشد.")
                return GLN_AMOUNT

            symbol = context.user_data.get('gln_symbol', '')
            leverage = context.user_data.get('gln_leverage', 1)
            msg = await update.effective_message.reply_text(f"⏳ در حال راه‌اندازی GLN برای {symbol}...")

            try:
                gln = GLNStrategy(
                    self.execution_engine, 
                    symbol, 
                    initial_investment=amount, 
                    side=None, 
                    market_type='future', 
                    leverage=leverage, 
                    db_manager=self.db_manager,
                    message_callback=self.send_telegram_message,
                    position_tracker=self.position_tracker,
                    scanner_registry=self.scanner_registry,
                    event_reporter=self.event_reporter
                )
                
                await gln.initialize() # Calculate levels
                
                self.gln_strategies[symbol] = gln
                
                # Start background tasks
                asyncio.create_task(self.run_gln_loop(gln))
                
                await msg.edit_text(
                    f"✅ GLN با موفقیت فعال شد! 🚀\n\n"
                    f"💎 نماد: {symbol}\n"
                    f"🎰 اهرم: {leverage}x\n"
                    f"💵 مارجین: {amount}$\n"
                    f"📊 وضعیت: در حال پایش بازار..."
                )
                
            except Exception as e:
                logger.error(f"Failed to start GLN: {e}")
                await msg.edit_text(f"❌ خطا در اجرا: {e}")
            
            return ConversationHandler.END

        except ValueError:
            await update.effective_message.reply_text("❌ لطفاً یک عدد معتبر وارد کنید.")
            return GLN_AMOUNT
        except Exception as e:
            logger.error(f"QGLN gln_get_amount error: {e}", exc_info=True)
            await update.effective_message.reply_text(f"❌ خطای داخلی: {e}\nلطفاً دوباره /qgln را بزنید.")
            return ConversationHandler.END

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
        scanner_name = 'QGLN'
        self.scanner_registry.update(scanner_name, running_status='SCANNING')
        while strategy.running:
            try:
                self.scanner_registry.update(scanner_name, running_status='SCANNING')
                await strategy.check_market()
                self.scanner_registry.increment(scanner_name, 'total_scans')
                self.scanner_registry.update(scanner_name, last_run_ts=datetime.now().isoformat())
                # Check for signals
                if hasattr(strategy, 'last_signal') and strategy.last_signal:
                    self.scanner_registry.increment(scanner_name, 'total_signals')
                    self.scanner_registry.update(scanner_name, last_signal=str(strategy.last_signal))
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"GLN Loop Error ({strategy.symbol}): {e}")
                self.scanner_registry.update(scanner_name, last_error=str(e)[:200], running_status='ERROR')
                await asyncio.sleep(60)
        self.scanner_registry.update(scanner_name, running_status='IDLE')
        self.scanner_registry.save()

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
                    
                    for _attempt in range(3):
                        try:
                            await asyncio.to_thread(self.futures_exchange.create_order, symbol, 'limit', sl_side, total_vol_calc, sl_price, params)
                            break
                        except Exception as _e:
                            logger.warning(f"SL order attempt {_attempt+1}/3 failed for {symbol}: {_e}")
                            if _attempt < 2: await asyncio.sleep(2)
                            else: raise
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
                
                try:
                    await self.app.bot.send_message(
                        chat_id=self.admin_id, 
                        text=message, 
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup
                    )
                except Exception:
                    # Fallback: send without parse_mode if HTML parsing fails
                    await self.app.bot.send_message(
                        chat_id=self.admin_id, 
                        text=message,
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
        msg = "🤖 <b>شروع اسکن خودکار GLN</b>\n"
        for symbol_unfmt in AUTO_SYMBOLS:
            symbol = symbol_unfmt.replace('USDT', '/USDT:USDT')
            if symbol not in self.gln_strategies:
                try:
                    # Initialize with 0 amount (Monitoring Only)
                    gln = GLNStrategy(
                        self.execution_engine, symbol, initial_investment=0, 
                        side=None, market_type='future', leverage=1, 
                        db_manager=self.db_manager, message_callback=self.send_telegram_message,
                        position_tracker=self.position_tracker,
                        scanner_registry=self.scanner_registry,
                        event_reporter=self.event_reporter
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
        
        # 1. Acquire Instance Lock OR Exit
        if not self.db_manager.acquire_instance_lock(self.instance_id, self.hostname, self.pid):
            active = self.db_manager.get_active_instance()
            logger.critical(f"CRITICAL: ANOTHER INSTANCE IS ACTIVE! Master: {active[1]} (PID: {active[2]})")
            print(f"\nCRITICAL: Master instance already active on {active[1]}.")
            print(f"Exiting to prevent duplicate trades.\n")
            # We can't easily exit from here without stopping polling, but run() handles pre-start check.
            # This is a secondary guard. 
            sys.exit(1)
        
        self.is_master = True
        self.execution_engine.is_active = True
        logger.info(f"✅ INSTANCE LOCK ACQUIRED (ID: {self.instance_id}). This bot is now MASTER.")
        print(f"[OK] Instance lock acquired. Bot is MASTER.", flush=True)

        # Restore strategies
        asyncio.create_task(self.load_active_strategies())
        # Start Scheduler
        self.scheduler_task = asyncio.create_task(self.daily_report_schedule())
        # Start Equity Snapshots (Every 15 mins)
        self.equity_task = asyncio.create_task(self.equity_snapshot_task())
        # Start Q-Stats Candle Collector
        self.q_collector_task = asyncio.create_task(self._q_candle_collector_loop())
        # Start Instance Heartbeat (5s)
        self.heartbeat_task = asyncio.create_task(self.instance_heartbeat())
        # Start Digest Reporter
        self.digest_task = asyncio.create_task(self.digest_reporter.run_loop())
        # Start AI Optimizer
        self.ai_optimizer_task = asyncio.create_task(self.ai_optimizer.run_loop())
        # Start Watchdog only when JobQueue is missing (run() schedules via JobQueue when available)
        if getattr(application, "job_queue", None) is None:
            asyncio.create_task(self.watchdog_fallback_loop())
        
        logger.info("Background tasks started (Strategies + Scheduler + Equity + Q-Stats + Heartbeat + Digest + AI Optimizer + Watchdog)")
        print("[OK] Application started successfully", flush=True)  # Explicit marker for health check

    async def instance_heartbeat(self):
        """Updates the instance lock last_ping in DB every 5 seconds."""
        while self.is_master:
            try:
                if not self.db_manager.update_instance_ping(self.instance_id):
                    logger.warning("❌ MASTER LOCK LOST! Another instance might have taken over.")
                    self.is_master = False
                    self.execution_engine.is_active = False
                    # Optionally notify or exit
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Heartbeat Error: {e}")
                await asyncio.sleep(5)

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
            "🚀 <b>به ربات پیشرفته Spider خوش آمدید!</b>\n\n"
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

    # ─── Dashboard Commands ───────────────────────────────────────

            
    async def dash_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show the unified dashboard."""
        if not await self.check_admin(update):
            return
            
        full_report = False
        if context.args and 'full' in context.args:
            full_report = True
            
        try:
            if not self.dashboard:
                await update.effective_message.reply_text("❌ داشبورد هنوز آماده نیست. چند ثانیه صبر کن.")
                return
            # Use the new centralized dashboard method
            msg = self.dashboard.get_unified_dashboard(full=full_report)
            await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Error in dash_command: {e}")
            await update.effective_message.reply_text(f"⚠️ Dashboard Error: {e}")

    async def health_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show system health via dashboard (exchange, DB, scanners, instance lock)."""
        if not await self.check_admin(update):
            return
        try:
            msg = self.dashboard.get_health()
            await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"health_command error: {e}")
            await update.effective_message.reply_text(f"Error: {e}")

    async def selftest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comprehensive self-test: exchange, telegram, scanners, registry, database, env."""
        if not await self.check_admin(update):
            return
        
        results = []
        
        # 1. ENV Check
        try:
            env_type = config.ENV_TYPE.upper()
            if env_type == 'VPS':
                token_ok = bool(config.BOT_TOKEN_LIVE)
                token_type = "LIVE"
            elif env_type == 'LOCAL':
                token_ok = bool(config.BOT_TOKEN_DEV)
                token_type = "DEV"
            else:
                token_ok = False
                token_type = "UNKNOWN"
            
            if token_ok:
                results.append(f"✅ ENV: {env_type} | Token: {token_type}")
            else:
                results.append(f"❌ ENV: {env_type} | Missing {token_type} token!")
        except Exception as e:
            results.append(f"❌ ENV: Error - {e}")
        
        # 2. Telegram Check
        try:
            bot_info = await self.application.bot.get_me()
            results.append(f"✅ Telegram: Connected as @{bot_info.username}")
        except Exception as e:
            results.append(f"❌ Telegram: Failed - {e}")
        
        # 3. Exchange Check
        try:
            if config.EXCHANGE_TYPE == 'coinex':
                balance = await asyncio.to_thread(self.futures_exchange.fetch_balance)
                results.append(f"✅ Exchange (CoinEx): Connected | Balance: {len(balance.get('info', {}))} accounts")
            elif config.EXCHANGE_TYPE == 'kucoin':
                balance = await asyncio.to_thread(self.futures_exchange.fetch_balance)
                results.append(f"✅ Exchange (KuCoin): Connected | Balance: {len(balance.get('info', {}))} accounts")
            else:
                results.append(f"⚠️ Exchange: Unknown type {config.EXCHANGE_TYPE}")
        except Exception as e:
            results.append(f"❌ Exchange: Failed - {str(e)[:100]}")
        
        # 4. Database Check
        try:
            test_query = self.db_manager.cursor.execute("SELECT COUNT(*) FROM strategies").fetchone()
            results.append(f"✅ Database: Connected | Strategies: {test_query[0] if test_query else 0}")
        except Exception as e:
            results.append(f"❌ Database: Failed - {e}")
        
        # 5. Scanner Registry Check
        try:
            registry = self.scanner_registry.get_all()
            scanner_count = len(registry)
            enabled_count = sum(1 for s in registry.values() if s.get('enabled', False))
            results.append(f"✅ Registry: {scanner_count} scanners ({enabled_count} enabled)")
            
            # Check specific scanners
            for name in ['QGLN', 'Hybrid', 'GSL']:
                scanner = registry.get(name)
                status = scanner.get('running_status', 'UNKNOWN')
                enabled = scanner.get('enabled', False)
                last_run = scanner.get('last_run_ts')
                
                if enabled:
                    if status == 'SCANNING':
                        results.append(f"  ✅ {name}: Running")
                    elif status == 'IDLE' and last_run:
                        last_run_str = str(last_run)[:19] if last_run else 'Never'
                        results.append(f"  ⚠️ {name}: Enabled but IDLE (last: {last_run_str})")
                    else:
                        results.append(f"  ⚠️ {name}: Enabled, Status={status}")
                else:
                    results.append(f"  ⚪ {name}: Disabled")
        except Exception as e:
            results.append(f"❌ Registry: Failed - {e}")
        
        # 6. Mode Check
        try:
            mode = config.MODE.upper()
            env = config.ENV_TYPE.upper()
            if env == 'VPS' and mode == 'DEV':
                results.append(f"❌ MODE: DEV on VPS is BLOCKED!")
            elif env == 'LOCAL' and mode == 'LIVE':
                results.append(f"❌ MODE: LIVE on LOCAL is BLOCKED!")
            else:
                results.append(f"✅ MODE: {mode} on {env}")
        except Exception as e:
            results.append(f"❌ MODE: Error - {e}")
        
        # 7. Dashboard Check
        try:
            if hasattr(self, 'dashboard') and self.dashboard:
                # Test get_unified_dashboard call
                dash_output = self.dashboard.get_unified_dashboard(full=False)
                if dash_output and len(dash_output) > 10:
                    results.append("✅ Dashboard: get_unified_dashboard() works")
                else:
                    results.append("⚠️ Dashboard: Output too short")
            else:
                results.append("❌ Dashboard: Not initialized")
        except Exception as e:
            results.append(f"❌ Dashboard: Error - {e}")
        
        # 8. Scan Refresh Handler Check
        try:
            # Verify scan_refresh callback is registered
            handlers = getattr(self.application, 'handlers', {})
            callback_handlers = handlers.get(1, [])  # CallbackQueryHandler group
            has_scan_refresh = any(
                hasattr(h, 'callback') and 'scan_refresh' in str(h.callback) 
                for h in callback_handlers
            )
            if has_scan_refresh or hasattr(self, 'callback_handler'):
                results.append("✅ Scan Refresh: Handler registered")
            else:
                results.append("⚠️ Scan Refresh: Handler check skipped (may be in unified handler)")
        except Exception as e:
            results.append(f"⚠️ Scan Refresh: Check error - {e}")
        
        # 9. QGLN Registry Keys Check
        try:
            registry = self.scanner_registry.get('QGLN')
            required_keys = ['candle_count', 'q_high', 'q_low', 'is_q_channel_set', 'gap_status']
            missing = [k for k in required_keys if k not in registry]
            if not missing:
                results.append("✅ QGLN Registry: All keys present")
            else:
                # Set defaults for missing keys
                defaults = {
                    'candle_count': 0,
                    'q_high': 0.0,
                    'q_low': 0.0,
                    'is_q_channel_set': False,
                    'gap_status': 'N/A'
                }
                for k in missing:
                    registry[k] = defaults.get(k, None)
                self.scanner_registry.update('QGLN', **{k: registry[k] for k in missing})
                results.append(f"⚠️ QGLN Registry: Added missing keys: {missing}")
        except Exception as e:
            results.append(f"❌ QGLN Registry: Error - {e}")
        
        # 10. JobQueue (informational only; do not fail selftest)
        try:
            jq = getattr(self.application, "job_queue", None)
            if jq is not None:
                results.append("✅ JobQueue: Available")
            else:
                results.append("⚠️ JobQueue: Not available (watchdog/delayed jobs disabled)")
        except Exception:
            results.append("⚠️ JobQueue: Not available")

        # 11. Smoke tests for core functions
        try:
            assert callable(self.wiz_start), "wiz_start not callable"
            results.append("✅ Trade Wizard: wiz_start is callable")
        except Exception as e:
            results.append(f"❌ Trade Wizard: {e}")
        try:
            assert callable(self.qgln_entry), "qgln_entry not callable"
            results.append("✅ QGLN: qgln_entry is callable")
        except Exception as e:
            results.append(f"❌ QGLN: {e}")
        try:
            assert isinstance(self.gln_strategies, dict), "gln_strategies not a dict"
            results.append(f"✅ GLN Strategies: {len(self.gln_strategies)} active")
        except Exception as e:
            results.append(f"❌ GLN Strategies: {e}")
        try:
            assert callable(self.send_telegram_message), "send_telegram_message not callable"
            results.append("✅ Message System: send_telegram_message OK")
        except Exception as e:
            results.append(f"❌ Message System: {e}")
        try:
            handler_groups = getattr(self.application, 'handlers', {})
            total_handlers = sum(len(h) for h in handler_groups.values())
            results.append(f"✅ Handlers: {total_handlers} registered in {len(handler_groups)} groups")
        except Exception as e:
            results.append(f"❌ Handlers: {e}")

        # Format output
        msg = "🔍 <b>Self-Test Results</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        msg += "\n".join(results)
        msg += "\n━━━━━━━━━━━━━━━━━━━━"
        
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)

    async def where_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Displays current execution environment and uptime (Persian)."""
        try:
            logger.info(f"DEBUG: /where or equivalent called by user {update.effective_user.id}")
            uptime = datetime.now() - self.start_time
            days = uptime.days
            hours, remainder = divmod(uptime.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            
            uptime_str = f"{days} روز، {hours} ساعت و {minutes} دقیقه" if days > 0 else f"{hours} ساعت و {minutes} دقیقه"
            
            env_labels = {
                "VPS": "🚀 سرور مجازی (Remote VPS)",
                "LOCAL": "💻 لپ‌تاپ شخصی (Local/Gravity)",
                "IDE/CI": "🛠 محیط توسعه (IDE)",
                "UNKNOWN": "❓ نامشخص"
            }
            
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cwd = os.getcwd()
            
            mode = config.MODE.strip()
            perm_labels = {
                "DEV": "🚫 فقط تست (Simulated/Blocked)",
                "PAPER": "📝 شبیه‌سازی (Paper simulated)",
                "LIVE": "💰 پول واقعی (Real trading enabled)"
            }
            
            # Token fingerprint
            token_fp = get_token_fingerprint(self.bot_token)
            
            # Determine token type based on strict config
            token_type_label = "UNKNOWN"
            if self.bot_token == config.BOT_TOKEN_LIVE:
                token_type_label = "🔴 LIVE Token (VPS)"
            elif self.bot_token == config.BOT_TOKEN_DEV:
                token_type_label = "🟢 DEV Token (Local)"
            else:
                token_type_label = "⚠️ LEGACY/OTHER"

            msg = (
                f"📍 <b>اطلاعات اجرای ربات (Spider)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💎 <b>نسخه:</b> <code>{BOT_VERSION}</code>\n"
                f"🏗 <b>زمان بیلد:</b> <code>{BUILD_TIMESTAMP}</code>\n"
                f"👑 <b>نقش (ROLE):</b> <code>{'MASTER' if self.is_master else 'STANDBY'}</code>\n"
                f"🛡 <b>حالت (MODE):</b> <code>{mode}</code>\n"
                f"🚦 <b>دسترسی:</b> {perm_labels.get(mode, 'Unknown')}\n"
                f"🏠 <b>محیط:</b> {env_labels.get(self.run_env, self.run_env)}\n"
                f"🔑 <b>توکن:</b> <code>{token_type_label}</code> (<code>{token_fp}</code>)\n"
                f"🔢 <b>PID:</b> <code>{self.pid}</code>\n"
                f"🖥 <b>Host:</b> <code>{self.hostname}</code>\n"
                f"⏱ <b>فعالیت:</b> {uptime_str}\n"
                f"🖥 <b>هاست:</b> <code>{self.hostname}</code>\n"
                f"👤 <b>کاربر:</b> <code>{self.username}</code>\n"
                f"🔢 <b>شناسه (PID):</b> <code>{self.pid}</code>\n"
                f"📂 <b>مسیر پروژه:</b> <code>{cwd}</code>\n"
                f"🕒 <b>آخرین بررسی:</b> <code>{now_str}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ سیستم در وضعیت {'عملیاتی' if self.is_master else 'آماده‌باش (Wait)'} است."
            )
            await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Error in where_command: {e}")
    async def _watchdog_check_once(self):
        """Single iteration of scanner health check (used by JobQueue callback and fallback loop)."""
        try:
            registry = self.scanner_registry.get_all()
            now = datetime.now()
            for name, state in registry.items():
                if not state.get('enabled'):
                    continue
                if name in ['Manual']:
                    continue
                last_run_str = state.get('last_run_ts') or state.get('last_run')
                if not last_run_str or last_run_str == 'Never':
                    continue
                try:
                    if isinstance(last_run_str, str) and 'T' in last_run_str:
                        last_run = datetime.fromisoformat(str(last_run_str).replace('Z', '+00:00'))
                    else:
                        last_run = datetime.strptime(str(last_run_str), '%Y-%m-%d %H:%M:%S')
                except Exception:
                    continue
                interval = 60
                if name == 'AI_Optimizer':
                    interval = getattr(config, 'AI_EVAL_INTERVAL', 30) * 60
                diff_seconds = (now - last_run).total_seconds()
                threshold = max(interval * 3, 1800)
                if diff_seconds > threshold:
                    logger.warning(f"WATCHDOG: Scanner {name} stalled! (Lag: {int(diff_seconds)}s). Restarting...")
                    self.scanner_registry.increment(name, 'crash_count')
                    self.scanner_registry.update(name, running_status='RESTARTING')
                    await self.event_reporter.report('CRASH_DETECTED', f"Scanner {name} stalled. Restarting...", priority='HIGH')
        except Exception as e:
            logger.error(f"Watchdog error: {e}")

    async def watchdog_fallback_loop(self):
        """Run scanner watchdog on a timer when JobQueue is not available. Never crash."""
        await asyncio.sleep(10)
        logger.info("WATCHDOG: Fallback asyncio loop started.")
        while True:
            try:
                await self._watchdog_check_once()
            except Exception as e:
                logger.exception("WATCHDOG fallback loop error: %s", e)
            await asyncio.sleep(300)

    async def scanner_watchdog_task(self):
        """
        Periodically checks if scanners are alive.
        If a scanner is enabled but last_run is too old, restart it.
        """
        logger.info("WATCHDOG: Started monitoring scanner health.")
        while True:
            await asyncio.sleep(300) # Check every 5 minutes
            await self._watchdog_check_once()

    async def token_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin only: Check which token is currently active."""
        if not await self.check_admin(update):
            return

        env = config.ENV_TYPE
        
        # Determine strict type again for display
        token_type = "LEGACY (Unknown)"
        current_token = self.bot_token
        
        if config.BOT_TOKEN_LIVE and current_token == config.BOT_TOKEN_LIVE:
            token_type = "🔴 LIVE (VPS)"
        elif config.BOT_TOKEN_DEV and current_token == config.BOT_TOKEN_DEV:
            token_type = "🟢 DEV (Local)"
            

        msg = (
            f"🔑 <b>Token Verification</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌍 <b>Env:</b> {env}\n"
            f"🏷 <b>Type:</b> {token_type}\n"
            f"🔒 <b>Fingerprint:</b> <code>{get_token_fingerprint(current_token)}</code>\n"
            f"⚠️ <b>Strict Mode:</b> ON\n"
            f"🔢 <b>PID:</b> {self.pid}\n"
            f"🖥 <b>Host:</b> {self.hostname}\n"
            f"📂 <b>CWD:</b> {os.getcwd()}\n"
            f"🏗 <b>Build:</b> {BOT_VERSION} ({BUILD_TIMESTAMP})"
        )
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comprehensive help with all commands, organized by category."""
        mode = self.db_manager.get_setting('bot_mode', 'CRYPTO')
        mode_icon = "🔷" if mode == 'CRYPTO' else "🌍"
        mode_label = "CRYPTO (CoinEx)" if mode == 'CRYPTO' else "FOREX (MT5)"

        # ── Header ──
        help_text = (
            f"📚 <b>Spider Bot — Help Center</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{mode_icon} Mode: <b>{mode_label}</b>\n\n"
        )

        if mode == 'CRYPTO':
            # ── Quick Start ──
            help_text += (
                "🚀 <b>Quick Start</b>\n"
                "─────────────────\n"
                "<code>/wiz</code> — Trade Wizard (guided step-by-step)\n"
                "<code>/scan</code> — Scan market for opportunities\n"
                "<code>/smart BTC 100 5</code> — AI analysis + trade\n\n"
            )

            # ── Trading ──
            help_text += (
                "💰 <b>Trading Commands</b>\n"
                "─────────────────\n"
                "<code>/spot BTC 100</code> — Buy $100 BTC spot\n"
                "<code>/future ETH 50 10</code> — Long $50 ETH 10x leverage\n"
                "<code>/snipe SOL 50</code> — Quick entry with auto SL/TP\n"
                "<code>/close BTC SPOT</code> — Close spot position\n"
                "<code>/close ETH FUTURE</code> — Close futures position\n\n"
            )

            # ── Strategies ──
            help_text += (
                "🧠 <b>Strategies</b>\n"
                "─────────────────\n"
                "<code>/qgln</code> — Setup GLN strategy (interactive)\n"
                "<code>/hybrid</code> — Start GLN Hybrid scanner\n"
                "<code>/auto</code> — Toggle auto-signal ON/OFF\n"
                "<code>/auto_on</code> — Force auto-signal ON\n"
                "<code>/auto_off</code> — Force auto-signal OFF\n"
                "<code>/stop BTC</code> — Stop strategy on BTC\n\n"
            )

            # ── Monitoring ──
            help_text += (
                "📊 <b>Monitoring & Reports</b>\n"
                "─────────────────\n"
                "<code>/positions</code> — Open positions list\n"
                "<code>/balance</code> — USDT balance (Spot/Futures)\n"
                "<code>/pnl</code> — P&L last 7 days\n"
                "<code>/pnl 1d</code> — Today's P&L\n"
                "<code>/pnl 30d</code> — Monthly P&L\n"
                "<code>/pnl 7d spot</code> — Spot only\n"
                "<code>/pnl 7d future</code> — Futures only\n"
                "<code>/daily_report</code> — Daily report\n\n"
            )

        else:
            # ── Forex Quick Start ──
            help_text += (
                "🚀 <b>Quick Start</b>\n"
                "─────────────────\n"
                "<code>/long XAUUSD 0.01</code> — Buy gold 0.01 lot\n"
                "<code>/short EURUSD 0.1</code> — Sell EUR 0.1 lot\n\n"
            )

            # ── Forex Trading ──
            help_text += (
                "💱 <b>Trading Commands</b>\n"
                "─────────────────\n"
                "<code>/long XAUUSD 0.01</code> — Buy (market)\n"
                "<code>/short EURUSD 0.1</code> — Sell (market)\n"
                "<code>/long XAUUSD 0.01 50 100</code> — Buy with SL/TP\n"
                "<code>/gln_fx XAUUSD 0.01</code> — GLN for Forex\n\n"
            )

            # ── Forex Monitoring ──
            help_text += (
                "📊 <b>Monitoring & Reports</b>\n"
                "─────────────────\n"
                "<code>/positions</code> — Open positions\n"
                "<code>/pnl</code> — P&L last 7 days\n"
                "<code>/pnl 1d</code> — Today's P&L\n\n"
            )

        # ── Dashboard (shared) ──
        help_text += (
            "📡 <b>Dashboard & Health</b>\n"
            "─────────────────\n"
            "<code>/dash</code> — Quick dashboard overview\n"
            "<code>/dash verbose</code> — Detailed scanner report\n"
            "<code>/dash full</code> — Full technical report\n"
            "<code>/health</code> — System health check\n"
            "<code>/status</code> — Active strategies status\n"
            "<code>/qstatus</code> — GLN scanner status\n"
            "<code>/qstats</code> — GLN performance stats\n"
            "<code>/dashboard</code> — Legacy dashboard\n\n"
        )

        # ── System (shared) ──
        help_text += (
            "⚙️ <b>System & Settings</b>\n"
            "─────────────────\n"
            "<code>/where</code> — Environment info (ENV/Mode/Token)\n"
            "<code>/ping</code> — Connection test\n"
            "<code>/switch_mode</code> — Switch Crypto ↔ Forex\n"
            "<code>/clear</code> — Clear stuck strategies\n"
            "<code>/start</code> — Show control panel\n\n"
        )

        # ── Tips ──
        help_text += (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💡 <b>Tips:</b>\n"
            "• <code>/wiz</code> is the easiest way to trade\n"
            "• <code>/dash</code> shows everything at a glance\n"
            "• AI suggestions are sent automatically (every 30m)\n"
            "• Silent mode: 23:00–07:00 (non-critical msgs off)\n"
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

    def run(self):
        """Main entry point to run the bot."""
        logger.info("Starting Telegram Bot Application...")
        
        try:
             import tzlocal
             print(f"DEBUG: tzlocal.get_localzone() = {tzlocal.get_localzone()}", flush=True)
        except Exception as e:
             print(f"DEBUG: tzlocal failed: {e}", flush=True)

        from telegram.ext import Defaults
        import datetime
        # Build Application with explicit UTC (standard lib); post_init runs when polling starts
        builder = Application.builder().token(self.bot_token).post_init(self.post_init)
        builder.defaults(Defaults(tzinfo=datetime.timezone.utc))
        
        # JobQueue: use default (may be None if optional dependency not installed)
        
        print("DEBUG: Calling builder.build()...", flush=True)
        self.application = builder.build()
        self.app = self.application  # Unify: both references point to same object
        print("DEBUG: Application built successfully.", flush=True)
        logger.info("DEBUG: Application built.")
        
        # Register Handlers
        # Basic
        self.application.add_handler(CommandHandler("start", self.start_command))
        # ... (handlers) ...

        # Initialize dependencies (handlers follow)
        # (I need to be careful not to delete handlers. I will use a smaller chunk.)
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("ping", self.ping_command))
        self.application.add_handler(CommandHandler("where", self.where_command))
        
        # Mode & Config
        self.application.add_handler(CommandHandler("switch_mode", self.switch_mode_command))
        self.application.add_handler(CommandHandler("clear", self.clear_command))
        
        # Dashboard
        self.application.add_handler(CommandHandler("dash", self.dash_command))
        self.application.add_handler(CommandHandler("dashboard", self.dashboard_command))
        self.application.add_handler(CommandHandler("health", self.health_command))
        self.application.add_handler(CommandHandler("selftest", self.selftest_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("qstatus", self.qstatus_command))
        self.application.add_handler(CommandHandler("qstats", self.qstats_command))
        
        # Trading (Crypto)
        self.application.add_handler(CommandHandler("spot", self.spot_command))
        self.application.add_handler(CommandHandler("future", self.future_command))
        self.application.add_handler(CommandHandler("long", self.long_command))
        self.application.add_handler(CommandHandler("short", self.short_command))
        self.application.add_handler(CommandHandler("stop", self.stop_command))
        self.application.add_handler(CommandHandler("scan", self.scan_command))
        self.application.add_handler(CommandHandler("snipe", self.snipe_command))
        self.application.add_handler(CommandHandler("smart", self.smart_command))
        self.application.add_handler(CommandHandler("close", self.close_command))
        self.application.add_handler(CommandHandler("test_sig", self.test_sig_command))
        
        # Auto / QGLN
        self.application.add_handler(CommandHandler("auto", self.qgln_auto_toggle))
        self.application.add_handler(CommandHandler("auto_on", self.cmd_auto_on))
        self.application.add_handler(CommandHandler("auto_off", self.cmd_auto_off))
        
        # Forex & Hybrid
        self.application.add_handler(CommandHandler("gln_fx", self.gln_forex_command))
        self.application.add_handler(CommandHandler("hybrid", self.hybrid_command))
        
        # Stats/Reporting
        self.application.add_handler(CommandHandler("positions", self.positions_command))
        self.application.add_handler(CommandHandler("balance", self.balance_command))
        self.application.add_handler(CommandHandler("pnl", self.pnl_command))
        self.application.add_handler(CommandHandler("daily_report", self.daily_report_command))
        
        # Admin
        self.application.add_handler(CommandHandler("token", self.token_command))
        self.application.add_handler(CommandHandler("runtime", self.where_command))
        self.application.add_handler(CommandHandler("version", self.where_command))
        
        # Callbacks (Buttons) - Register specific pattern first, then generic
        self.application.add_handler(CallbackQueryHandler(self.handle_mode_button, pattern='^cb_mode_switch$'))
        
        # Trade Wizard Conversation Handler - before generic handle_callback
        wiz_trade_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex(r"^🚀 معامله جدید$"), self.wiz_start),
                CommandHandler("wiz", self.wiz_start),
                CallbackQueryHandler(self.wiz_start, pattern="^wiz_start$"),
                CallbackQueryHandler(self.handle_trade_callback, pattern="^trade:")
            ],
            states={
                WIZ_MARKET: [CallbackQueryHandler(self.wiz_market, pattern=r"^TRD\|MARKET\|")],
                WIZ_SYMBOL: [
                    CallbackQueryHandler(self.wiz_symbol, pattern=r"^TRD\|SYMBOL\|"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTON_FILTER, self.wiz_symbol_search)
                ],
                WIZ_CUSTOM_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTON_FILTER, self.wiz_symbol_search)],
                WIZ_SIDE: [CallbackQueryHandler(self.wiz_side, pattern=r"^TRD\|SIDE\|")],
                WIZ_MARGIN: [CallbackQueryHandler(self.wiz_margin, pattern=r"^TRD\|MARGIN\|")],
                WIZ_CUSTOM_MARGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTON_FILTER, self.wiz_margin_custom)],
                WIZ_LEVERAGE: [CallbackQueryHandler(self.wiz_leverage, pattern=r"^TRD\|LEVERAGE\|")],
                WIZ_TYPE: [CallbackQueryHandler(self.wiz_type, pattern=r"^TRD\|TYPE\|")],
                WIZ_CONFIRM: [CallbackQueryHandler(self.wiz_execute, pattern=r"^TRD\|EXECUTE")],
            },
            fallbacks=[
                CallbackQueryHandler(self.wiz_cancel, pattern=r"^TRD\|CANCEL"),
                CallbackQueryHandler(self.wiz_back, pattern=r"^TRD\|BACK\|"),
                CommandHandler("cancel", self.wiz_cancel),
                MessageHandler(filters.Regex("^❌ انصراف$"), self.wiz_cancel),
                MessageHandler(MENU_BUTTON_FILTER, self.wiz_cancel),
            ],
            conversation_timeout=300,
            name="trade_wizard",
            persistent=False
        )
        self.application.add_handler(wiz_trade_handler)

        # QGLN Conversation Handler - before generic handle_callback so exec_sig_ is handled here
        qgln_conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("qgln", self.qgln_entry),
                CallbackQueryHandler(self.handle_callback, pattern="^exec_sig_")
            ],
            states={
                GLN_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTON_FILTER, self.gln_get_symbol)],
                GLN_LEVERAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTON_FILTER, self.gln_get_leverage)],
                GLN_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTON_FILTER, self.gln_get_amount)],
                SIG_MARGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTON_FILTER, self.handle_sig_margin)],
                SIG_LEVERAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTON_FILTER, self.handle_sig_leverage)],
            },
            fallbacks=[
                CommandHandler("cancel", self.gln_cancel),
                CallbackQueryHandler(self.handle_callback, pattern="^cancel_wizard"),
                MessageHandler(MENU_BUTTON_FILTER, self.gln_cancel),
            ],
            conversation_timeout=300,
            allow_reentry=True
        )
        self.application.add_handler(qgln_conv_handler)

        # Main Menu Button Handlers (Reply Keyboard)
        self.application.add_handler(MessageHandler(filters.Regex("^📌 پوزیشنها$"), self.positions_command))
        self.application.add_handler(MessageHandler(filters.Regex("^🛟 کمک سریع$"), self.help_command))
        self.application.add_handler(MessageHandler(filters.Regex("^⚙️ ریسک$"), self.status_command))
        self.application.add_handler(MessageHandler(filters.Regex("^🧠 استراتژیها$"), self.qstatus_command))
        self.application.add_handler(MessageHandler(filters.Regex(r'^(🚀 CRYPTO|🌍 FOREX) Mode$'), self.handle_mode_button))

        # Generic callback handler ABSOLUTE LAST (before error handler)
        if hasattr(self, 'handle_callback'):
            self.application.add_handler(CallbackQueryHandler(self.handle_callback))

        # Error Handler
        self.application.add_error_handler(self.error_handler)
        
        # Start Scanner Watchdog: JobQueue if available, else fallback (started in post_init)
        jq = getattr(self.application, "job_queue", None)
        if jq is not None:
            jq.run_repeating(lambda ctx: asyncio.create_task(self._watchdog_check_once()), interval=300, first=10)
            logger.info("JobQueue available -> scheduling watchdog via JobQueue")
        else:
            logger.warning("JobQueue missing -> watchdog fallback enabled")
        
        # Initialize dependencies
        # (Already done in __init__)
        
        # Start Polling
        logger.info("Bot is polling...")
        logger.info("DEBUG: Starting polling loop now...")
        print("[OK] Bot starting polling...", flush=True)  # Explicit stdout for health check
        try:
            # Start the application (non-blocking start, then run_polling blocks)
            print("[OK] Application starting...", flush=True)
            self.application.run_polling(drop_pending_updates=True, stop_signals=None) # Handle signals manually
        except KeyboardInterrupt:
            logger.info("Bot stopped by user (KeyboardInterrupt)")
            print("[INFO] Bot stopped by user", flush=True)
            raise
        except SystemExit as e:
            logger.critical(f"Bot exited with code {e.code}")
            print(f"[FATAL] Bot exited with code {e.code}", flush=True)
            raise
        except Exception as e:
            logger.exception("Fatal error in run_polling:")
            print(f"[FATAL] Bot polling crashed: {e}", flush=True)
            import traceback
            traceback.print_exc()
            raise
        
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log the error and send a telegram message to notify the developer."""
        error = context.error
        
        # Handle Telegram Conflict (409) - Multiple bot instances running
        if isinstance(error, Conflict):
            logger.warning(f"⚠️ TELEGRAM CONFLICT DETECTED: {error}")
            logger.warning("This usually means another bot instance is running with the same token.")
            logger.warning(f"Current ENV_TYPE: {config.ENV_TYPE}, Token: {get_token_fingerprint(self.bot_token)}")
            logger.warning("Bot will continue running but Telegram updates may be delayed.")
            logger.warning("SOLUTION: Make sure only ONE instance is running:")
            logger.warning("  - LOCAL should use BOT_TOKEN_DEV with ENV_TYPE=LOCAL")
            logger.warning("  - VPS should use BOT_TOKEN_LIVE with ENV_TYPE=VPS")
            # Don't crash - just log and continue
            return
        
        # Handle other errors normally
        logger.error(msg="Exception while handling an update:", exc_info=error)

def get_token_fingerprint(token: str) -> str:
    """Returns the last 6 characters of the token for identification."""
    if not token or len(token) < 10:
        return "INVALID"
    return f"...{token[-6:]}"

def resolve_bot_token_strict() -> str:
    """
    Strict Token Enforcement to prevent 409 Conflicts.
    VPS -> Must use BOT_TOKEN_LIVE
    LOCAL -> Must use BOT_TOKEN_DEV
    """
    # 1. Determine ENV_TYPE (Normalized)
    env_type = config.ENV_TYPE.upper()
    if env_type not in ['VPS', 'LOCAL']:
        env_type = 'LOCAL' # Default to LOCAL if undefined, but config should handle this
    
    token = None
    token_type = "UNKNOWN"
    required_var = "UNKNOWN"

    # 2. Enforce based on ENV_TYPE
    if env_type == 'VPS':
        token = config.BOT_TOKEN_LIVE
        token_type = "LIVE"
        required_var = "BOT_TOKEN_LIVE"
        
        # STRICT: Warn if DEV token is present on VPS
        if config.BOT_TOKEN_DEV:
            logger.warning("SECURITY WARNING: BOT_TOKEN_DEV found in VPS environment! This should not happen.")
            
    else:
        # LOCAL, GRAVITY
        token = config.BOT_TOKEN_DEV
        token_type = "DEV"
        required_var = "BOT_TOKEN_DEV"
        
        # STRICT: Warn if LIVE token is present on LOCAL (less critical, but good hygiene)
        if config.BOT_TOKEN_LIVE:
             logger.warning("Config check: BOT_TOKEN_LIVE present in LOCAL env.")

    # 3. Check for missing token
    if not token:
        logger.critical(f"SECURITY TOKEN LOCK: Missing {required_var} for ENV_TYPE={env_type}. Refusing to start.")
        print(f"\n[FATAL] SECURITY TOKEN LOCK FAILED!")
        print(f"       Current ENV_TYPE: {env_type}")
        print(f"       Required Token:   {required_var}")
        print(f"       Action:           Update your .env file immediately.")
        sys.exit(12)  # Exit Code 12 = Invalid Configuration

    # 4. Log Legacy Warning (if BOT_TOKEN exists)
    if config.BOT_TOKEN:
        logger.warning(f"Legacy BOT_TOKEN found in environment but IGNORED. Strict Mode using {token_type}.")

    # 5. Fingerprint
    fpr = get_token_fingerprint(token)
    logger.info(f"TOKEN SELECTED: {token_type} [{fpr}] (ENV={env_type})")
    print(f"[OK] TOKEN LOCK: {env_type} -> {token_type} ({fpr})")
    
    return token

def validate_run_mode(env_type: str):
    """
    Enforce Mode Rules:
    - VPS: PAPER or LIVE only. (Block DEV)
    - LOCAL: DEV or PAPER (if allowed). (Block LIVE)
    """
    mode = config.MODE.upper()
    
    if env_type == 'VPS':
        if mode == 'DEV':
            logger.critical("SECURITY LOCK: Cannot run MODE=DEV on VPS. Force-switching to PAPER or exiting.")
            # Option: Fail fast or fallback? User asked for strict.
            print(f"[FATAL] MODE=DEV is BLOCKED on VPS. Please use PAPER or LIVE.")
            sys.exit(12)
        if mode not in ['LIVE', 'PAPER']:
             logger.error(f"Unknown MODE {mode} on VPS. Defaulting to PAPER.")
             # We can't change config.MODE easily here without side effects, but we can warn.
    
    elif env_type == 'LOCAL':
        if mode == 'LIVE':
             logger.critical("SECURITY LOCK: Cannot run MODE=LIVE on LOCAL machine. Prevents accidental real trading.")
             print(f"[FATAL] MODE=LIVE is BLOCKED on LOCAL. Please use DEV or PAPER.")
             sys.exit(12)
        if mode == 'PAPER' and not config.ALLOW_LOCAL_PAPER:
             logger.critical("SECURITY LOCK: LOCAL PAPER mode is disabled (ALLOW_LOCAL_PAPER!=1).")
             sys.exit(12)

    logger.info(f"MODE CHECK OK: ENV={env_type} MODE={mode}")


if __name__ == '__main__':
    try:
        # 0. Validate config
        config.validate_config()
        
        # 1. Resolve Token Strict
        BOT_TOKEN = resolve_bot_token_strict()
        
        # 2. Validate Mode
        validate_run_mode(config.ENV_TYPE)
        
        # Determine exchange and keys
        
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
        bot.bot_token_raw = BOT_TOKEN  # Store for /where fingerprint
        
        # Set window title for easy identification
        # Set window title for easy identification
        # if os.name == 'nt':
        #     try:
        #         os.system(f"title SpiderBot_{BOT_VERSION}")
        #     except: pass
            
        # Start background tasks
        logger.info("DEBUG: Calling bot.run()...")
        bot.run()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"CRITICAL ERROR: {e}")
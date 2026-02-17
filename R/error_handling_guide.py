# راهنمای کامل Error Handling برای ربات

import asyncio
import logging
from typing import Optional
import ccxt
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================
# ❌ مثال‌های اشتباه
# ============================================

async def bad_example_1():
    """مثال 1: Bare except (خیلی بد!)"""
    try:
        exchange = ccxt.binance()
        balance = exchange.fetch_balance()
        return balance
    except:  # ❌ هیچ ایده‌ای نداریم چه اتفاقی افتاده!
        return None


async def bad_example_2():
    """مثال 2: Pass کردن خطا (خیلی خطرناک!)"""
    try:
        exchange = ccxt.binance()
        order = exchange.create_order('BTC/USDT', 'market', 'buy', 1)
        return order
    except:
        pass  # ❌ خطا ignore شد! پول ممکنه از دست بره!


async def bad_example_3():
    """مثال 3: Log نکردن جزئیات"""
    try:
        exchange = ccxt.binance()
        ticker = exchange.fetch_ticker('BTC/USDT')
        return ticker['last']
    except Exception as e:
        logger.error("خطا!")  # ❌ خطای چی؟!
        return None


# ============================================
# ✅ Error Handling درست
# ============================================

class TradingBot:
    def __init__(self):
        self.exchange = None
        self.retry_count = 3
        self.retry_delay = 2
    
    # ----------------------------------------
    # مثال 1: مدیریت خطاهای مختلف صرافی
    # ----------------------------------------
    async def fetch_balance_safe(self) -> Optional[dict]:
        """دریافت موجودی با مدیریت خطای کامل"""
        try:
            balance = await asyncio.to_thread(
                self.exchange.fetch_balance
            )
            logger.info("✅ موجودی با موفقیت دریافت شد")
            return balance
            
        except ccxt.NetworkError as e:
            # خطای شبکه - می‌تونیم retry کنیم
            logger.error(f"🌐 خطای شبکه: {e}")
            logger.info("⏳ تلاش مجدد...")
            await asyncio.sleep(2)
            return await self.fetch_balance_safe()  # retry
            
        except ccxt.ExchangeError as e:
            # خطای صرافی - maintenance یا مشکل API
            logger.error(f"🏦 خطای صرافی: {e}")
            # اطلاع به کاربر
            await self.notify_user(f"⚠️ صرافی مشکل داره: {str(e)}")
            return None
            
        except ccxt.AuthenticationError as e:
            # API Key اشتباهه - خیلی جدی!
            logger.critical(f"🔐 خطای احراز هویت: {e}")
            logger.critical("API Key یا Secret اشتباه است!")
            await self.notify_admin(f"🚨 API Key invalid: {e}")
            raise  # این خطا رو بالا می‌بریم
            
        except Exception as e:
            # خطای غیرمنتظره
            logger.exception(f"❌ خطای غیرمنتظره: {e}")
            return None
    
    # ----------------------------------------
    # مثال 2: ثبت سفارش با Retry
    # ----------------------------------------
    async def place_order_with_retry(
        self, 
        symbol: str, 
        side: str, 
        amount: float,
        price: Optional[float] = None
    ) -> Optional[dict]:
        """ثبت سفارش با تلاش مجدد خودکار"""
        
        for attempt in range(1, self.retry_count + 1):
            try:
                logger.info(f"📝 تلاش {attempt}/{self.retry_count} برای ثبت سفارش")
                
                # بررسی اولیه
                if amount <= 0:
                    raise ValueError(f"مقدار نامعتبر: {amount}")
                
                # ثبت سفارش
                if price:
                    order = await asyncio.to_thread(
                        self.exchange.create_limit_order,
                        symbol, side, amount, price
                    )
                else:
                    order = await asyncio.to_thread(
                        self.exchange.create_market_order,
                        symbol, side, amount
                    )
                
                logger.info(f"✅ سفارش ثبت شد: {order['id']}")
                return order
                
            except ccxt.InsufficientFunds as e:
                # موجودی کافی نیست - retry نمی‌کنیم
                logger.error(f"💰 موجودی کافی نیست: {e}")
                await self.notify_user("⚠️ موجودی کافی نیست!")
                return None
                
            except ccxt.InvalidOrder as e:
                # سفارش نامعتبر - retry نمی‌کنیم
                logger.error(f"❌ سفارش نامعتبر: {e}")
                await self.notify_user(f"⚠️ سفارش نامعتبر: {str(e)}")
                return None
                
            except ccxt.NetworkError as e:
                # خطای شبکه - retry می‌کنیم
                logger.warning(f"🌐 خطای شبکه (تلاش {attempt}): {e}")
                
                if attempt < self.retry_count:
                    wait_time = self.retry_delay * attempt
                    logger.info(f"⏳ صبر {wait_time} ثانیه...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error("❌ همه تلاش‌ها ناموفق بود")
                    return None
                    
            except ccxt.ExchangeError as e:
                logger.error(f"🏦 خطای صرافی: {e}")
                
                # بررسی نوع خطا
                error_msg = str(e).lower()
                if 'maintenance' in error_msg:
                    await self.notify_user("⚠️ صرافی در حال تعمیرات است")
                elif 'rate limit' in error_msg:
                    logger.warning("⏱️ Rate limit خورده، صبر می‌کنیم...")
                    await asyncio.sleep(60)
                    if attempt < self.retry_count:
                        continue
                
                return None
                
            except Exception as e:
                # خطای غیرمنتظره
                logger.exception(f"❌ خطای غیرمنتظره در تلاش {attempt}: {e}")
                return None
        
        return None
    
    # ----------------------------------------
    # مثال 3: محاسبه PnL با Error Handling کامل
    # ----------------------------------------
    async def calculate_pnl_safe(self, positions: list) -> dict:
        """محاسبه PnL با مدیریت خطای کامل"""
        result = {
            'total_pnl': 0.0,
            'positions': [],
            'errors': []
        }
        
        if not positions:
            logger.warning("⚠️ لیست positions خالی است")
            return result
        
        for pos in positions:
            try:
                # بررسی داده‌ها
                if not isinstance(pos, dict):
                    raise ValueError(f"Position نامعتبر: {pos}")
                
                required_fields = ['symbol', 'entry_price', 'amount']
                for field in required_fields:
                    if field not in pos:
                        raise ValueError(f"فیلد {field} موجود نیست")
                
                # دریافت قیمت فعلی
                try:
                    ticker = await asyncio.to_thread(
                        self.exchange.fetch_ticker,
                        pos['symbol']
                    )
                    current_price = ticker['last']
                    
                except ccxt.NetworkError:
                    logger.warning(f"⚠️ نتونستیم قیمت {pos['symbol']} رو بگیریم")
                    result['errors'].append({
                        'symbol': pos['symbol'],
                        'error': 'network_error'
                    })
                    continue
                
                # محاسبه PnL
                entry_price = float(pos['entry_price'])
                amount = float(pos['amount'])
                
                pnl = (current_price - entry_price) * amount
                
                result['total_pnl'] += pnl
                result['positions'].append({
                    'symbol': pos['symbol'],
                    'pnl': pnl,
                    'pnl_percent': (pnl / (entry_price * amount)) * 100
                })
                
            except ValueError as e:
                logger.error(f"❌ داده نامعتبر: {e}")
                result['errors'].append({
                    'position': pos,
                    'error': str(e)
                })
                
            except Exception as e:
                logger.exception(f"❌ خطا در محاسبه PnL: {e}")
                result['errors'].append({
                    'position': pos,
                    'error': 'unknown_error'
                })
        
        return result
    
    # ----------------------------------------
    # Helper Methods
    # ----------------------------------------
    async def notify_user(self, message: str):
        """ارسال پیام به کاربر"""
        print(f"📱 پیام به کاربر: {message}")
    
    async def notify_admin(self, message: str):
        """ارسال پیام به ادمین"""
        print(f"🚨 پیام به ادمین: {message}")


# ============================================
# Custom Exceptions
# ============================================

class TradingBotError(Exception):
    """کلاس پایه برای خطاهای ربات"""
    pass

class InsufficientBalanceError(TradingBotError):
    """موجودی کافی نیست"""
    pass

class InvalidStrategyError(TradingBotError):
    """استراتژی نامعتبر"""
    pass

class PositionNotFoundError(TradingBotError):
    """Position پیدا نشد"""
    pass


class SmartBot:
    """ربات با Custom Exceptions"""
    
    def __init__(self):
        self.balance = 1000.0
        self.positions = {}
    
    async def open_position(
        self, 
        symbol: str, 
        amount: float,
        strategy: str
    ):
        """باز کردن position با Custom Exceptions"""
        
        # بررسی استراتژی
        valid_strategies = ['spider', 'gln', 'smart']
        if strategy not in valid_strategies:
            raise InvalidStrategyError(
                f"استراتژی {strategy} نامعتبر است. "
                f"استراتژی‌های معتبر: {valid_strategies}"
            )
        
        # بررسی موجودی
        if amount > self.balance:
            raise InsufficientBalanceError(
                f"موجودی: {self.balance}$, نیاز: {amount}$"
            )
        
        # باز کردن position
        self.positions[symbol] = {
            'amount': amount,
            'strategy': strategy,
            'entry_time': datetime.now()
        }
        self.balance -= amount
        
        logger.info(f"✅ Position باز شد: {symbol} با {strategy}")
    
    async def close_position(self, symbol: str):
        """بستن position"""
        if symbol not in self.positions:
            raise PositionNotFoundError(
                f"Position {symbol} پیدا نشد"
            )
        
        # بستن position
        pos = self.positions[symbol]
        self.balance += pos['amount']
        del self.positions[symbol]
        
        logger.info(f"✅ Position بسته شد: {symbol}")


# ============================================
# Context Manager برای Error Handling
# ============================================

class ExchangeConnection:
    """Context manager برای مدیریت اتصال به صرافی"""
    
    def __init__(self, exchange_name: str, api_key: str, secret: str):
        self.exchange_name = exchange_name
        self.api_key = api_key
        self.secret = secret
        self.exchange = None
    
    async def __aenter__(self):
        """شروع اتصال"""
        try:
            logger.info(f"🔌 اتصال به {self.exchange_name}...")
            
            exchange_class = getattr(ccxt, self.exchange_name)
            self.exchange = exchange_class({
                'apiKey': self.api_key,
                'secret': self.secret,
            })
            
            # تست اتصال
            await asyncio.to_thread(self.exchange.fetch_balance)
            
            logger.info(f"✅ اتصال برقرار شد")
            return self.exchange
            
        except Exception as e:
            logger.error(f"❌ خطا در اتصال: {e}")
            raise
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """قطع اتصال"""
        if self.exchange:
            try:
                await asyncio.to_thread(self.exchange.close)
                logger.info(f"🔌 اتصال قطع شد")
            except Exception as e:
                logger.error(f"⚠️ خطا در قطع اتصال: {e}")
        
        # اگه خطایی رخ داده، لاگ کن
        if exc_type:
            logger.error(f"❌ خطا: {exc_type.__name__}: {exc_val}")
        
        return False  # خطا رو بالا می‌بره


# استفاده:
async def trade_with_context_manager():
    """استفاده از context manager"""
    async with ExchangeConnection('binance', 'key', 'secret') as exchange:
        # کارهات رو اینجا انجام بده
        balance = await asyncio.to_thread(exchange.fetch_balance)
        print(f"موجودی: {balance}")
    # اینجا اتصال خودکار قطع میشه


# ============================================
# Decorator برای Error Handling
# ============================================

from functools import wraps

def handle_trading_errors(func):
    """Decorator برای مدیریت خطاهای معاملاتی"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
            
        except ccxt.NetworkError as e:
            logger.error(f"🌐 خطای شبکه در {func.__name__}: {e}")
            raise
            
        except ccxt.ExchangeError as e:
            logger.error(f"🏦 خطای صرافی در {func.__name__}: {e}")
            raise
            
        except TradingBotError as e:
            logger.error(f"🤖 خطای ربات در {func.__name__}: {e}")
            raise
            
        except Exception as e:
            logger.exception(f"❌ خطای غیرمنتظره در {func.__name__}: {e}")
            raise
    
    return wrapper


class DecoratedBot:
    """استفاده از decorator"""
    
    @handle_trading_errors
    async def place_order(self, symbol: str, amount: float):
        """ثبت سفارش با decorator"""
        logger.info(f"📝 ثبت سفارش {symbol}")
        # کد سفارش
        return {'id': '12345'}


# ============================================
# تست
# ============================================

async def main():
    print("=" * 60)
    print("تست Error Handling")
    print("=" * 60)
    
    # تست 1: Custom Exceptions
    bot = SmartBot()
    
    try:
        await bot.open_position('BTCUSDT', 500, 'spider')
        print("✅ Position باز شد")
        
        await bot.close_position('BTCUSDT')
        print("✅ Position بسته شد")
        
        # تست خطا
        await bot.close_position('ETHUSDT')  # این position وجود نداره
        
    except PositionNotFoundError as e:
        print(f"⚠️ خطا: {e}")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(main())


# ============================================
# چک‌لیست Error Handling برای ربات شما
# ============================================
"""
✅ همیشه Exception‌های خاص رو بگیرید (نه bare except)
✅ خطاها رو لاگ کنید (با logger.exception)
✅ پیام‌های واضح بنویسید
✅ برای خطاهای شبکه retry کنید
✅ برای خطاهای جدی به ادمین اطلاع بدید
✅ از Custom Exceptions استفاده کنید
✅ داده‌های ورودی رو validate کنید
✅ از Context Managers استفاده کنید
✅ هیچوقت خطاهای مهم رو ignore نکنید
✅ برای production، همه خطاهای ممکن رو پیش‌بینی کنید
"""

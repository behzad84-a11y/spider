# مثال کامل: رفع Race Condition در ربات

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ============================================
# ❌ کد اشتباه (بدون Lock)
# ============================================
class BadSpiderStrategy:
    def __init__(self):
        self.total_invested = 0
        self.positions = []
        
    async def place_order(self, side, amount):
        """این تابع مشکل داره!"""
        print(f"[{datetime.now().strftime('%H:%M:%S.%f')}] شروع سفارش {amount}$")
        
        # شبیه‌سازی API call (طول می‌کشه)
        await asyncio.sleep(0.1)
        
        # اضافه کردن به سرمایه کل
        old_total = self.total_invested
        self.total_invested = old_total + amount
        
        print(f"[{datetime.now().strftime('%H:%M:%S.%f')}] سفارش ثبت شد. کل سرمایه: {self.total_invested}$")
        
    async def demo_race_condition(self):
        """نمایش مشکل Race Condition"""
        print("\n=== نمایش مشکل Race Condition ===\n")
        
        # اجرای همزمان 3 سفارش
        await asyncio.gather(
            self.place_order('buy', 100),
            self.place_order('buy', 100),
            self.place_order('buy', 100)
        )
        
        print(f"\n❌ کل سرمایه واقعی: {self.total_invested}$ (باید 300$ می‌شد!)")


# ============================================
# ✅ کد درست (با Lock)
# ============================================
class GoodSpiderStrategy:
    def __init__(self):
        self.total_invested = 0
        self.positions = []
        self._order_lock = asyncio.Lock()  # قفل برای سفارشات
        
    async def place_order(self, side, amount):
        """این تابع درسته!"""
        
        # استفاده از قفل
        async with self._order_lock:
            print(f"[{datetime.now().strftime('%H:%M:%S.%f')}] 🔒 قفل گرفته شد، شروع سفارش {amount}$")
            
            # شبیه‌سازی API call
            await asyncio.sleep(0.1)
            
            # اضافه کردن به سرمایه کل
            old_total = self.total_invested
            self.total_invested = old_total + amount
            
            print(f"[{datetime.now().strftime('%H:%M:%S.%f')}] ✅ سفارش ثبت شد. کل سرمایه: {self.total_invested}$")
            print(f"[{datetime.now().strftime('%H:%M:%S.%f')}] 🔓 قفل آزاد شد")
        
    async def demo_safe_execution(self):
        """نمایش حل مشکل با Lock"""
        print("\n=== نمایش راه‌حل با Lock ===\n")
        
        # اجرای همزمان 3 سفارش
        await asyncio.gather(
            self.place_order('buy', 100),
            self.place_order('buy', 100),
            self.place_order('buy', 100)
        )
        
        print(f"\n✅ کل سرمایه درست: {self.total_invested}$")


# ============================================
# Lock برای عملیات مختلف
# ============================================
class AdvancedSpiderStrategy:
    def __init__(self):
        self.positions = []
        self.total_invested = 0
        
        # قفل‌های جداگانه برای عملیات مختلف
        self._order_lock = asyncio.Lock()      # برای ثبت سفارش
        self._position_lock = asyncio.Lock()   # برای تغییر position
        self._balance_lock = asyncio.Lock()    # برای تغییر موجودی
        
    async def place_order(self, side, amount, price):
        """ثبت سفارش با قفل"""
        async with self._order_lock:
            print(f"📝 ثبت سفارش: {side} {amount}$ @ {price}")
            
            # API call به صرافی
            await asyncio.sleep(0.05)
            
            # به‌روزرسانی موجودی
            async with self._balance_lock:
                self.total_invested += amount
            
            print(f"✅ سفارش ثبت شد")
    
    async def update_positions(self, position_data):
        """به‌روزرسانی position‌ها"""
        async with self._position_lock:
            print(f"📊 به‌روزرسانی positions")
            self.positions.append(position_data)
            
    async def close_position(self, position_id):
        """بستن position"""
        # استفاده از دو قفل همزمان
        async with self._position_lock:
            async with self._balance_lock:
                print(f"🔴 بستن position {position_id}")
                # حذف position و برگشت موجودی
                await asyncio.sleep(0.05)


# ============================================
# Lock Manager برای مدیریت بهتر
# ============================================
class LockManager:
    """مدیریت قفل‌ها برای symbol‌های مختلف"""
    def __init__(self):
        self._locks = {}
        self._manager_lock = asyncio.Lock()
    
    async def get_lock(self, key: str) -> asyncio.Lock:
        """دریافت یا ایجاد قفل برای یک key"""
        async with self._manager_lock:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]


class MultiSymbolBot:
    """ربات با چند symbol"""
    def __init__(self):
        self.lock_manager = LockManager()
        
    async def trade_symbol(self, symbol: str, action: str):
        """معامله یک symbol با قفل اختصاصی"""
        # هر symbol قفل جداگانه داره
        lock = await self.lock_manager.get_lock(symbol)
        
        async with lock:
            print(f"🔒 [{symbol}] قفل گرفته شد برای {action}")
            await asyncio.sleep(0.1)
            print(f"🔓 [{symbol}] قفل آزاد شد")
    
    async def demo_multi_symbol(self):
        """نمایش معامله همزمان چند symbol"""
        print("\n=== معامله همزمان چند Symbol ===\n")
        
        await asyncio.gather(
            self.trade_symbol('BTCUSDT', 'خرید'),
            self.trade_symbol('ETHUSDT', 'خرید'),
            self.trade_symbol('BTCUSDT', 'فروش'),  # این باید صبر کنه تا خرید BTC تموم شه
            self.trade_symbol('ETHUSDT', 'فروش'),  # این باید صبر کنه تا خرید ETH تموم شه
        )


# ============================================
# اجرای تست‌ها
# ============================================
async def main():
    print("=" * 60)
    print("تست Race Condition و راه‌حل‌ها")
    print("=" * 60)
    
    # تست 1: نمایش مشکل
    bad_bot = BadSpiderStrategy()
    await bad_bot.demo_race_condition()
    
    await asyncio.sleep(1)
    
    # تست 2: نمایش راه‌حل
    good_bot = GoodSpiderStrategy()
    await good_bot.demo_safe_execution()
    
    await asyncio.sleep(1)
    
    # تست 3: چند symbol
    multi_bot = MultiSymbolBot()
    await multi_bot.demo_multi_symbol()


if __name__ == "__main__":
    asyncio.run(main())


# ============================================
# نکات مهم برای ربات شما:
# ============================================
"""
1. همیشه از Lock استفاده کنید وقتی:
   - چند تابع می‌خوان روی یک متغیر کار کنن
   - API call دارید که طول می‌کشه
   - Database update می‌کنید
   - موجودی یا position تغییر می‌کنه

2. از قفل‌های جداگانه برای عملیات مستقل استفاده کنید:
   - یک قفل برای سفارشات BTC
   - یک قفل جدا برای سفارشات ETH
   - قفل‌ها نباید بی‌دلیل روی هم تاثیر بذارن

3. حواستون به Deadlock باشه:
   # ❌ اشتباه
   async with lock_a:
       async with lock_b:
           pass
   
   # در جای دیگه:
   async with lock_b:
       async with lock_a:  # ممکنه Deadlock بشه!
           pass

4. برای ربات شما، این جاها حتما Lock لازمه:
   - place_order()
   - close_position()
   - update_positions()
   - save_to_database()
   - calculate_pnl()
"""

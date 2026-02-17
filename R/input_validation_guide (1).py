# راهنمای کامل Input Validation برای ربات

import re
from typing import Optional, Union
from datetime import datetime

# ============================================
# ❌ کد بدون Validation (خطرناک!)
# ============================================

async def bad_trade_command(symbol, amount, leverage):
    """این تابع خطرناکه!"""
    # هیچ چک نمی‌کنه!
    
    # اگه کاربر بنویسه: /trade xyz -100 999999999
    # برنامه crash می‌کنه یا کار اشتباه می‌کنه!
    
    order = exchange.create_order(
        symbol,           # ممکنه نامعتبر باشه
        'market',
        'buy',
        amount,          # ممکنه منفی باشه!
        leverage=leverage # ممکنه خیلی زیاد باشه!
    )
    return order


# ============================================
# ✅ مثال‌های واقعی از کاربران
# ============================================

# کاربر می‌نویسه: /trade btcusdt 50 10x
# مشکل: btcusdt باید BTCUSDT باشه

# کاربر می‌نویسه: /trade BTC/USDT 50 10
# مشکل: BTC/USDT باید BTCUSDT باشه

# کاربر می‌نویسه: /trade BTCUSDT 50 10x
# این درسته! ولی باید چک کنیم

# کاربر می‌نویسه: /trade BTCUSDT -100 5x
# مشکل: amount منفیه!

# کاربر می‌نویسه: /trade BTCUSDT 1000000 200x
# مشکل: leverage خیلی زیاده!


# ============================================
# ✅ Validation کامل
# ============================================

class ValidationError(Exception):
    """خطای اعتبارسنجی"""
    def __init__(self, message: str, field: str = None):
        self.message = message
        self.field = field
        super().__init__(self.message)


class InputValidator:
    """کلاس اعتبارسنجی ورودی‌ها"""
    
    # ----------------------------------------
    # 1. Validation نماد (Symbol)
    # ----------------------------------------
    @staticmethod
    def validate_symbol(symbol: str, exchange_type: str = 'spot') -> str:
        """
        بررسی و استاندارد کردن نماد
        
        مثال‌های ورودی:
        - "btcusdt" → "BTCUSDT"
        - "BTC/USDT" → "BTCUSDT"
        - "btc-usdt" → "BTCUSDT"
        
        خروجی: BTCUSDT
        """
        
        # بررسی خالی نبودن
        if not symbol or not symbol.strip():
            raise ValidationError(
                "نماد نمی‌تواند خالی باشد",
                field="symbol"
            )
        
        # تبدیل به حروف بزرگ و حذف فضاهای خالی
        symbol = symbol.upper().strip()
        
        # حذف کاراکترهای اضافی
        symbol = symbol.replace('/', '')   # BTC/USDT → BTCUSDT
        symbol = symbol.replace('-', '')   # BTC-USDT → BTCUSDT
        symbol = symbol.replace('_', '')   # BTC_USDT → BTCUSDT
        symbol = symbol.replace(' ', '')   # BTC USDT → BTCUSDT
        
        # بررسی فقط حروف و اعداد
        if not re.match(r'^[A-Z0-9]+$', symbol):
            raise ValidationError(
                f"نماد فقط باید شامل حروف و اعداد باشد: {symbol}",
                field="symbol"
            )
        
        # بررسی طول
        if len(symbol) < 5:
            raise ValidationError(
                f"نماد کوتاه است: {symbol}",
                field="symbol"
            )
        
        if len(symbol) > 20:
            raise ValidationError(
                f"نماد بلند است: {symbol}",
                field="symbol"
            )
        
        # بررسی پایان با USDT یا USDC
        valid_endings = ['USDT', 'USDC', 'BUSD']
        if not any(symbol.endswith(end) for end in valid_endings):
            raise ValidationError(
                f"نماد باید با {' یا '.join(valid_endings)} تمام شود. "
                f"نماد شما: {symbol}",
                field="symbol"
            )
        
        # بررسی حداقل طول بخش اول (ارز اصلی)
        # مثلاً BTC در BTCUSDT باید حداقل 2 حرف باشه
        base = symbol.replace('USDT', '').replace('USDC', '').replace('BUSD', '')
        if len(base) < 2:
            raise ValidationError(
                f"نماد پایه خیلی کوتاه است: {base}",
                field="symbol"
            )
        
        return symbol
    
    # ----------------------------------------
    # 2. Validation مقدار (Amount)
    # ----------------------------------------
    @staticmethod
    def validate_amount(
        amount: Union[str, int, float],
        min_amount: float = 1.0,
        max_amount: float = 100000.0,
        field_name: str = "amount"
    ) -> float:
        """
        بررسی مقدار سرمایه
        
        مثال‌های ورودی:
        - "100" → 100.0
        - "100.50" → 100.5
        - "1,000" → 1000.0
        """
        
        # تبدیل string به float
        if isinstance(amount, str):
            # حذف کاما
            amount = amount.replace(',', '')
            
            # حذف فضاهای خالی
            amount = amount.strip()
            
            # حذف علامت $
            amount = amount.replace('$', '')
            
            try:
                amount = float(amount)
            except ValueError:
                raise ValidationError(
                    f"{field_name} باید یک عدد باشد، نه '{amount}'",
                    field=field_name
                )
        
        # تبدیل int به float
        if isinstance(amount, int):
            amount = float(amount)
        
        # بررسی نوع
        if not isinstance(amount, (int, float)):
            raise ValidationError(
                f"{field_name} باید عدد باشد",
                field=field_name
            )
        
        # بررسی مثبت بودن
        if amount <= 0:
            raise ValidationError(
                f"{field_name} باید بزرگتر از صفر باشد. مقدار شما: {amount}",
                field=field_name
            )
        
        # بررسی حداقل
        if amount < min_amount:
            raise ValidationError(
                f"حداقل {field_name}: {min_amount}$. مقدار شما: {amount}$",
                field=field_name
            )
        
        # بررسی حداکثر
        if amount > max_amount:
            raise ValidationError(
                f"حداکثر {field_name}: {max_amount}$. مقدار شما: {amount}$",
                field=field_name
            )
        
        # بررسی اعشار زیاد (حداکثر 2 رقم اعشار)
        if round(amount, 2) != amount:
            amount = round(amount, 2)
        
        return amount
    
    # ----------------------------------------
    # 3. Validation اهرم (Leverage)
    # ----------------------------------------
    @staticmethod
    def validate_leverage(
        leverage: Union[str, int],
        min_leverage: int = 1,
        max_leverage: int = 125
    ) -> int:
        """
        بررسی اهرم
        
        مثال‌های ورودی:
        - "10x" → 10
        - "10" → 10
        - 10 → 10
        """
        
        # اگه string هست
        if isinstance(leverage, str):
            # حذف x
            leverage = leverage.lower().replace('x', '')
            
            # حذف فضاها
            leverage = leverage.strip()
            
            try:
                leverage = int(leverage)
            except ValueError:
                raise ValidationError(
                    f"اهرم باید عدد صحیح باشد، نه '{leverage}'",
                    field="leverage"
                )
        
        # بررسی نوع
        if not isinstance(leverage, int):
            try:
                leverage = int(leverage)
            except:
                raise ValidationError(
                    "اهرم باید عدد صحیح باشد",
                    field="leverage"
                )
        
        # بررسی محدوده
        if leverage < min_leverage:
            raise ValidationError(
                f"حداقل اهرم {min_leverage}x است. اهرم شما: {leverage}x",
                field="leverage"
            )
        
        if leverage > max_leverage:
            raise ValidationError(
                f"حداکثر اهرم {max_leverage}x است. اهرم شما: {leverage}x",
                field="leverage"
            )
        
        return leverage
    
    # ----------------------------------------
    # 4. Validation نوع بازار (Market Type)
    # ----------------------------------------
    @staticmethod
    def validate_market_type(market_type: str) -> str:
        """
        بررسی نوع بازار
        
        مثال‌های ورودی:
        - "spot" → "spot"
        - "SPOT" → "spot"
        - "future" → "future"
        - "futures" → "future"
        """
        
        if not market_type or not market_type.strip():
            raise ValidationError(
                "نوع بازار نمی‌تواند خالی باشد",
                field="market_type"
            )
        
        # استاندارد کردن
        market_type = market_type.lower().strip()
        
        # یکسان‌سازی
        if market_type in ['futures', 'perp', 'perpetual']:
            market_type = 'future'
        
        # بررسی معتبر بودن
        valid_types = ['spot', 'future']
        if market_type not in valid_types:
            raise ValidationError(
                f"نوع بازار باید {' یا '.join(valid_types)} باشد. "
                f"مقدار شما: {market_type}",
                field="market_type"
            )
        
        return market_type
    
    # ----------------------------------------
    # 5. Validation نوع معامله (Side)
    # ----------------------------------------
    @staticmethod
    def validate_side(side: str, market_type: str = 'spot') -> str:
        """
        بررسی نوع معامله
        
        مثال‌های ورودی:
        - "buy" → "long"
        - "BUY" → "long"
        - "long" → "long"
        - "sell" → "short" (فقط در future)
        """
        
        if not side or not side.strip():
            raise ValidationError(
                "نوع معامله نمی‌تواند خالی باشد",
                field="side"
            )
        
        # استاندارد کردن
        side = side.lower().strip()
        
        # یکسان‌سازی
        if side in ['buy', 'long', 'l']:
            side = 'long'
        elif side in ['sell', 'short', 's']:
            side = 'short'
        else:
            raise ValidationError(
                f"نوع معامله باید buy/long یا sell/short باشد. "
                f"مقدار شما: {side}",
                field="side"
            )
        
        # بررسی محدودیت spot
        if market_type == 'spot' and side == 'short':
            raise ValidationError(
                "در بازار spot فقط خرید (long) امکان‌پذیر است",
                field="side"
            )
        
        return side
    
    # ----------------------------------------
    # 6. Validation استراتژی
    # ----------------------------------------
    @staticmethod
    def validate_strategy(strategy: str) -> str:
        """بررسی نوع استراتژی"""
        
        if not strategy or not strategy.strip():
            raise ValidationError(
                "استراتژی نمی‌تواند خالی باشد",
                field="strategy"
            )
        
        strategy = strategy.lower().strip()
        
        valid_strategies = ['spider', 'gln', 'smart', 'forex']
        if strategy not in valid_strategies:
            raise ValidationError(
                f"استراتژی معتبر نیست. "
                f"استراتژی‌های معتبر: {', '.join(valid_strategies)}. "
                f"مقدار شما: {strategy}",
                field="strategy"
            )
        
        return strategy
    
    # ----------------------------------------
    # 7. Validation کامل یک سفارش
    # ----------------------------------------
    @staticmethod
    def validate_order(
        symbol: str,
        amount: Union[str, float],
        leverage: Union[str, int],
        market_type: str = 'spot',
        side: str = 'long'
    ) -> dict:
        """
        اعتبارسنجی کامل یک سفارش
        
        خروجی:
        {
            'symbol': 'BTCUSDT',
            'amount': 100.0,
            'leverage': 10,
            'market_type': 'future',
            'side': 'long'
        }
        """
        
        errors = []
        result = {}
        
        # 1. Symbol
        try:
            result['symbol'] = InputValidator.validate_symbol(symbol)
        except ValidationError as e:
            errors.append(f"❌ نماد: {e.message}")
        
        # 2. Amount
        try:
            result['amount'] = InputValidator.validate_amount(amount)
        except ValidationError as e:
            errors.append(f"❌ مقدار: {e.message}")
        
        # 3. Market Type
        try:
            result['market_type'] = InputValidator.validate_market_type(market_type)
        except ValidationError as e:
            errors.append(f"❌ نوع بازار: {e.message}")
        
        # 4. Side
        try:
            result['side'] = InputValidator.validate_side(
                side, 
                result.get('market_type', 'spot')
            )
        except ValidationError as e:
            errors.append(f"❌ نوع معامله: {e.message}")
        
        # 5. Leverage (فقط برای future)
        if result.get('market_type') == 'future':
            try:
                result['leverage'] = InputValidator.validate_leverage(leverage)
            except ValidationError as e:
                errors.append(f"❌ اهرم: {e.message}")
        else:
            result['leverage'] = 1
        
        # اگه خطایی بود
        if errors:
            raise ValidationError(
                "\n".join(errors),
                field="order"
            )
        
        return result


# ============================================
# استفاده در Telegram Bot
# ============================================

async def trade_command_handler(update, context):
    """
    Handler برای دستور /trade
    
    فرمت: /trade SYMBOL AMOUNT LEVERAGE
    مثال: /trade BTCUSDT 100 10x
    """
    
    try:
        # دریافت پارامترها
        if len(context.args) < 3:
            await update.message.reply_text(
                "❌ فرمت دستور اشتباه است!\n\n"
                "فرمت صحیح:\n"
                "/trade SYMBOL AMOUNT LEVERAGE\n\n"
                "مثال:\n"
                "/trade BTCUSDT 100 10x"
            )
            return
        
        symbol = context.args[0]
        amount = context.args[1]
        leverage = context.args[2]
        
        # اعتبارسنجی
        validated = InputValidator.validate_order(
            symbol=symbol,
            amount=amount,
            leverage=leverage,
            market_type='future',
            side='long'
        )
        
        # نمایش اطلاعات تایید شده
        message = (
            "✅ اطلاعات سفارش تایید شد:\n\n"
            f"📊 نماد: {validated['symbol']}\n"
            f"💰 مقدار: {validated['amount']}$\n"
            f"⚡ اهرم: {validated['leverage']}x\n"
            f"🎯 نوع: {validated['side']} در بازار {validated['market_type']}\n\n"
            "آیا تایید می‌کنید؟"
        )
        
        await update.message.reply_text(message)
        
        # ادامه پردازش...
        
    except ValidationError as e:
        # ارسال پیام خطا به کاربر
        await update.message.reply_text(
            f"⚠️ خطا در اعتبارسنجی:\n\n{e.message}\n\n"
            f"لطفاً دوباره تلاش کنید."
        )
    
    except Exception as e:
        # خطای غیرمنتظره
        await update.message.reply_text(
            "❌ خطای سیستم!\n"
            "لطفاً با پشتیبانی تماس بگیرید."
        )
        print(f"Error: {e}")


# ============================================
# تست‌ها
# ============================================

def test_validations():
    """تست تمام validation‌ها"""
    
    print("=" * 60)
    print("تست Input Validation")
    print("=" * 60)
    
    # تست 1: Symbol
    print("\n1️⃣ تست Symbol:")
    test_symbols = [
        ("btcusdt", "BTCUSDT", True),
        ("BTC/USDT", "BTCUSDT", True),
        ("btc-usdt", "BTCUSDT", True),
        ("xyz", None, False),  # خیلی کوتاه
        ("BTCEUR", None, False),  # با EUR ختم میشه
    ]
    
    for input_val, expected, should_pass in test_symbols:
        try:
            result = InputValidator.validate_symbol(input_val)
            if should_pass:
                assert result == expected
                print(f"   ✅ '{input_val}' → '{result}'")
            else:
                print(f"   ❌ '{input_val}' باید خطا می‌داد!")
        except ValidationError as e:
            if not should_pass:
                print(f"   ✅ '{input_val}' → خطا (درست)")
            else:
                print(f"   ❌ '{input_val}' → خطا: {e.message}")
    
    # تست 2: Amount
    print("\n2️⃣ تست Amount:")
    test_amounts = [
        ("100", 100.0, True),
        ("100.50", 100.5, True),
        ("1,000", 1000.0, True),
        ("-50", None, False),  # منفی
        ("0", None, False),  # صفر
        ("xyz", None, False),  # نامعتبر
    ]
    
    for input_val, expected, should_pass in test_amounts:
        try:
            result = InputValidator.validate_amount(input_val)
            if should_pass:
                assert result == expected
                print(f"   ✅ '{input_val}' → {result}$")
            else:
                print(f"   ❌ '{input_val}' باید خطا می‌داد!")
        except ValidationError as e:
            if not should_pass:
                print(f"   ✅ '{input_val}' → خطا (درست)")
            else:
                print(f"   ❌ '{input_val}' → خطا: {e.message}")
    
    # تست 3: Leverage
    print("\n3️⃣ تست Leverage:")
    test_leverages = [
        ("10x", 10, True),
        ("10", 10, True),
        (10, 10, True),
        ("200", None, False),  # خیلی زیاد
        ("0", None, False),  # صفر
        ("-5", None, False),  # منفی
    ]
    
    for input_val, expected, should_pass in test_leverages:
        try:
            result = InputValidator.validate_leverage(input_val)
            if should_pass:
                assert result == expected
                print(f"   ✅ '{input_val}' → {result}x")
            else:
                print(f"   ❌ '{input_val}' باید خطا می‌داد!")
        except ValidationError as e:
            if not should_pass:
                print(f"   ✅ '{input_val}' → خطا (درست)")
            else:
                print(f"   ❌ '{input_val}' → خطا: {e.message}")
    
    # تست 4: کامل
    print("\n4️⃣ تست کامل سفارش:")
    try:
        result = InputValidator.validate_order(
            symbol="btc/usdt",
            amount="100.50",
            leverage="10x",
            market_type="future",
            side="long"
        )
        print(f"   ✅ سفارش معتبر:")
        for key, value in result.items():
            print(f"      {key}: {value}")
    except ValidationError as e:
        print(f"   ❌ خطا: {e.message}")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    test_validations()


# ============================================
# چک‌لیست Validation برای ربات شما
# ============================================
"""
✅ همیشه ورودی کاربر رو validate کنید
✅ پیام‌های خطا واضح و راهنما باشن
✅ داده‌ها رو استاندارد کنید (uppercase, lowercase, etc)
✅ محدوده‌های منطقی تعریف کنید
✅ خطاها رو به کاربر نشون بدید، نه crash
✅ از regex برای pattern matching استفاده کنید
✅ تست‌های کافی بنویسید
✅ documentation بنویسید
✅ edge case‌ها رو پیش‌بینی کنید
✅ user experience رو در نظر بگیرید
"""

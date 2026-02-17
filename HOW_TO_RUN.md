# راهنمای اجرای ربات Spider Trading Bot

## مشکل قبلی
ربات شما با خطای **409 Conflict** مواجه می‌شد چون چند تا instance از ربات با همان token در حال اجرا بود.

## راه حل
دو اسکریپت جداگانه برای دو محیط مختلف ساخته شده:

### 1. تست محلی (LOCAL) - `run_bot_local.bat`
برای تست روی کامپیوتر خودت:
- از `BOT_TOKEN_DEV` استفاده می‌کنه
- `ENV_TYPE=LOCAL` و `MODE=DEV`
- باید در فایل `.env` داشته باشی:
  ```
  ENV_TYPE=LOCAL
  BOT_TOKEN_DEV=توکن_ربات_تست_شما
  COINEX_API_KEY=...
  COINEX_SECRET=...
  ```

**نحوه اجرا:**
```bash
run_bot_local.bat
```

### 2. اجرا روی VPS - `run_bot_vps.bat`
برای اجرا روی سرور VPS:
- از `BOT_TOKEN_LIVE` استفاده می‌کنه
- `ENV_TYPE=VPS` و `MODE=PAPER` (می‌تونی با `/switch_mode` تغییر بدی)
- باید در فایل `.env` روی VPS داشته باشی:
  ```
  ENV_TYPE=VPS
  BOT_TOKEN_LIVE=توکن_ربات_زنده_شما
  COINEX_API_KEY=...
  COINEX_SECRET=...
  ```

**نحوه اجرا:**
```bash
run_bot_vps.bat
```

## نکات مهم

1. **هرگز هر دو ربات رو همزمان با یک token اجرا نکن!**
   - ربات محلی باید `BOT_TOKEN_DEV` استفاده کنه
   - ربات VPS باید `BOT_TOKEN_LIVE` استفاده کنه

2. **قبل از اجرا، مطمئن شو هیچ instance دیگه‌ای در حال اجرا نیست:**
   ```powershell
   Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'spider_trading_bot\.py' }
   ```

3. **اگر خطای Conflict گرفتی:**
   - همه instanceهای در حال اجرا رو kill کن
   - مطمئن شو `.env` درست تنظیم شده
   - دوباره اجرا کن

4. **لاگ‌ها:**
   - ربات محلی: خروجی رو در کنسول می‌بینی
   - ربات VPS: لاگ‌ها در `bot.log` و `bot_error.log` ذخیره می‌شن

## ساختار فایل .env

```env
# Environment Type: LOCAL or VPS
ENV_TYPE=LOCAL

# Bot Tokens (مهم: هر کدوم برای محیط خودش)
BOT_TOKEN_DEV=توکن_برای_تست_محلی
BOT_TOKEN_LIVE=توکن_برای_VPS

# Exchange Credentials
EXCHANGE_TYPE=coinex
COINEX_API_KEY=کلید_API_شما
COINEX_SECRET=رمز_API_شما

# Trading Mode: DEV, PAPER, or LIVE
MODE=DEV
DEFAULT_VPS_MODE=PAPER
```

## عیب‌یابی

### خطای "Missing credentials"
- فایل `.env` رو چک کن و مطمئن شو همه فیلدها پر شده

### خطای "Conflict: terminated by other getUpdates request"
- یعنی یک instance دیگه در حال اجراست
- همه رو kill کن و دوباره اجرا کن

### خطای "SECURITY TOKEN LOCK FAILED"
- مطمئن شو `ENV_TYPE` درست تنظیم شده
- مطمئن شو token مربوطه (`BOT_TOKEN_DEV` یا `BOT_TOKEN_LIVE`) در `.env` موجوده


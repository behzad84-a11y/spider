# گزارش ممیزی و بازیابی — ربات Spider Trading Bot

## TASK 1 — گزارش کامل ممیزی (فارسی)

### ۱. وضعیت اجزای پروژه

| بخش | وضعیت | توضیح |
|-----|--------|------|
| **توکن و ENV** | ✅ کامل | تفکیک BOT_TOKEN_DEV/LIVE و resolve_bot_token_strict() درست کار می‌کند؛ ENV_TYPE=VPS/LOCAL اعمال می‌شود. |
| **اجرای لوکال** | ✅ کامل | با BOT_TOKEN_DEV و run_bot_local ربات لوکال بالا می‌آید. |
| **اجرای VPS** | ⚠️ نیمه‌کار | با Task Scheduler (SpiderBot) ربات از مسیر release اجرا می‌شود و بعد از قطع plink زنده می‌ماند؛ اما **پایپلاین دیپلوی** بعد از deploy جدید، تسک را به release جدید به‌روز نمی‌کند. |
| **استراتژی QGLN** | ✅ کامل | ثبت در registry، ConversationHandler و دستورات /qgln و /auto موجود است. |
| **استراتژی Hybrid** | ✅ کامل | GLNHybridStrategy و دستور /hybrid و ثبت در registry. |
| **استراتژی GSL** | ✅ کامل | SpiderStrategy/GSL و ثبت در registry. |
| **AI Optimizer** | ✅ کامل | ماژول ai_optimizer، حلقه ارزیابی و پیشنهاد به تلگرام؛ در dashboard و selftest درج شده. |
| **Scanner Registry** | ✅ کامل | ScannerStateRegistry در dashboard، ذخیره در scanner_registry.json، ثبت QGLN/Hybrid/GSL/GLN_FX/Manual/AI_Optimizer. |
| **داشبورد و دستورات** | ⚠️ نیمه‌کار | /dash و dashboard و get_verbose/get_full سالم؛ **/health شکسته** — بدنه try حذف شده و فقط except مانده → با فراخوانی /health خطای NameError رخ می‌دهد. |
| **پایپلاین دیپلوی** | ⚠️ نیمه‌کار | deploy_vps.ps1: بکاپ، استخراج zip، env از vps_generated.env، health-check با پنجره ۱۵ ثانیه و تشخیص ۴۰۹؛ اما **شروع ربات با Start-Process** وابسته به نشست plink است و بعد از قطع اتصال پروسه می‌میرد؛ **به‌روزرسانی تسک SpiderBot برای release جدید انجام نمی‌شود.** |
| **سیستم بکاپ** | ✅ کامل | در deploy_vps.ps1 قبل از استخراج، backup از release فعلی و .env در backups\<ID> گرفته می‌شود؛ نگه‌داری فقط ۳ بکاپ آخر. |
| **رول‌بک** | ❌ ناقص | خودکار نیست؛ فقط بکاپ ذخیره می‌شود؛ اسکریپت یا دستورالعمل مشخص برای بازگردانی به release قبلی وجود ندارد. |
| **سلفتست /selftest** | ⚠️ نیمه‌کار | ENV، Telegram، Exchange، Registry، MODE و JobQueue چک می‌شوند؛ **باگ**: در بخش scanner اگر last_run_ts شیء datetime باشد، برش `last_run[:19]` خطا می‌دهد. چک دیتابیس با cursor.execute درست است. |

### ۲. خلاصه وضعیت

- **کاملاً درست:** توکن، استراتژی‌ها (QGLN, Hybrid, GSL)، AI Optimizer، Scanner Registry، بکاپ دیپلوی، /where، /dash (تا وقتی dashboard صدا زده شود)، JobQueue و حلقه fallback watchdog.
- **نیمه‌کار:** VPS (با تسک دستی درست است؛ دیپلوی خودکار تسک را به release جدید نمی‌برد)، /health (کد شکسته)، selftest (باگ فرمت last_run).
- **شکسته:** /health (بدنه تابع حذف شده).
- **ناقص:** رول‌بک خودکار، به‌روزرسانی تسک SpiderBot بعد از deploy.

---

## TASK 2 — تأیید زمان اجرا

- **اجرای لوکال:** با BOT_TOKEN_DEV و ENV_TYPE=LOCAL ربات بالا می‌آید؛ دستورات پاسخ می‌دهند.
- **اجرای VPS:** با تسک زمان‌بندی SpiderBot که `start_fresh.bat` (یا معادل) را در پوشه release اجرا می‌کند، ربات از SSH جدا است و بعد از قطع plink زنده می‌ماند؛ لاگ spider_fresh.err.log نشان می‌دهد Bot is polling و Application started.
- **اتصال تلگرام:** با یک توکن و یک نمونه ربات، getUpdates بدون ۴۰۹ کار می‌کند؛ اگر هم‌زمان دو نمونه با همان توکن باشند ۴۰۹ رخ می‌دهد.
- **حلقه اسکنرها و JobQueue:** اسکنرها در registry ثبت و با watchdog (JobQueue یا fallback asyncio) نگه‌داری می‌شوند.
- **دلیل سکوت قبلی VPS:** اجرای ربات با Start-Process از داخل اسکریپت plink؛ با بستن نشست SSH، پروسه فرزند از بین می‌رفت. راه‌حل: اجرا با Task Scheduler (تسک SpiderBot) تا از نشست SSH جدا باشد.

---

## TASK 3 — برنامه بازیابی (فارسی)

### اولویت ۱ — باید اول درست شود
1. **/health:** بازگردانی بدنه تابع و اتصال به `dashboard.get_health()` تا دستور بدون خطا پاسخ دهد.
2. **سلفتست:** اصلاح نمایش last_run در بخش اسکنرها برای نوع datetime/str تا بدون خطا کار کند.

### اولویت ۲ — باید به‌روز/بازسازی شود
3. **دیپلوی VPS:** بعد از موفقیت health-check، تسک زمان‌بندی **SpiderBot** برای مسیر **release جدید** ساخته/به‌روز شود تا بعد از هر deploy، ربات از همان بیلد جدید اجرا شود (و نه از پوشه قدیمی).

### اولویت ۳ — حذف یا ساده‌سازی
4. **شروع ربات در deploy:** وابستگی به Start-Process داخل همان اسکریپت plink را به‌عنوان روش اصلی شروع حذف کنید؛ تنها روش پایدار روی VPS همان تسک SpiderBot است.
5. **رول‌بک:** یا یک اسکریپت ساده (مثلاً PowerShell) برای کپی کردن یک backup مشخص به عنوان release فعلی و ریستارت تسک بنویسید، یا در مستندات مراحل دستی رول‌بک را مشخص کنید.

### اولویت ۴ — اعتبارسنجی نهایی (TASK 5)
6. پس از اعمال اصلاحات، باید تأیید شود:
   - **/where** — اطلاعات ENV، مود، توکن و نقش MASTER/STANDBY نمایش داده شود.
   - **/dash** — داشبورد بدون خطا برگردد.
   - **/selftest** — بدون خطا (به‌ویژه بخش اسکنرها با last_run) و **/health** — بدون NameError و با خروجی get_health.
   - **VPS** — ربات از طریق تسک SpiderBot پاسخ دهد و در لاگ spider_fresh.* هیچ ۴۰۹ نباشد.
   - **اسکنرها** — در registry ثبت و در صورت فعال بودن اجرا شوند.
   - **بکاپ** — قبل از هر deploy در `backups\<ID>` ایجاد شود.

---

## TASK 6 — گزارش نهایی (فارسی)

### چه چیزهایی شکسته بود
- **/health:** بدنه تابع (try) حذف شده بود و فقط بلوک except مانده بود؛ با صدا زدن /health خطای NameError رخ می‌داد.
- **سلفتست:** در بخش وضعیت اسکنرها، برای `last_run` در صورت datetime بودن، استفاده از `last_run[:19]` باعث خطا می‌شد.
- **VPS بعد از deploy:** ربات با Start-Process از اسکریپت دیپلوی شروع می‌شد و با قطع plink از بین می‌رفت؛ علاوه بر این، تسک SpiderBot پس از deploy به release جدید اشاره نمی‌کرد.

### چه چیزهایی اصلاح شد
1. **health_command:** بدنه try اضافه شد و از `self.dashboard.get_health()` برای تولید پاسخ و ارسال با parse_mode=HTML استفاده می‌شود.
2. **selftest_command:** فرمت `last_run` برای نمایش امن شد (`str(last_run)[:19]`) تا برای datetime هم خطا ندهد.
3. **deploy_vps.ps1:** بعد از موفقیت health-check: (۷ب) پروسه GREEN که فقط برای health-check شروع شده بود متوقف می‌شود؛ (۷ج) فایل `start_fresh.bat` در release جدید ساخته و تسک **SpiderBot** با مسیر همین release به‌روز می‌شود؛ (۷د) تسک SpiderBot یک بار اجرا می‌شود تا ربات جدا از نشست SSH بالا بیاید.

### چه چیزهایی هنوز نیاز به کار دارد
- **رول‌بک خودکار:** فقط بکاپ گرفته می‌شود؛ بازگردانی به release قبلی نیاز به اسکریپت یا دستورالعمل دستی دارد.
- **مستندسازی:** ثبت دقیق نحوه استفاده از SpiderBot، مسیرهای لاگ (مثلاً spider_fresh.*.log) و نحوه رول‌بک در صورت نیاز.

### چگونه پایداری را حفظ کنید
- روی VPS فقط از **Task Scheduler (SpiderBot)** برای اجرای ربات استفاده کنید؛ از شروع ربات با plink/Start-Process به‌عنوان روش اصلی پرهیز کنید.
- قبل از deploy، ربات لوکال را متوقف کنید تا با BOT_TOKEN_LIVE تداخل نداشته باشد (جلوگیری از ۴۰۹).
- بعد از هر deploy موفق، با /where و /dash و /selftest از داخل تلگرام وضعیت را چک کنید.
- بکاپهای backups\ را در صورت نیاز برای رول‌بک دستی نگه دارید.

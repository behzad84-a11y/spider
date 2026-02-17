# وضعیت Featureهای درخواستی

## A) QGLN

### ✅ موجود:
- Time engine با NY timezone (pytz)
- Candle counting (1-18)
- Q-Channel tracking (q_high, q_low)
- Gap detection (gap_filled, trend_direction)
- Basic state persistence (save_state)

### ⛔️ ناقص/نیاز به بهبود:
1. **Time engine دقیق NY (DST safe)** - نیاز به بهبود DST handling
2. **لاگ دقیق Candle# + Q-Channel state در registry** - نیاز به logging به registry
3. **ثبت daily reset در dashboard** - نیاز به integration با dashboard
4. **گزارش Gap status (filled/open)** - نیاز به reporting
5. **state persistence بعد از restart** - نیاز به load_state
6. **اضافه شدن به /dash full** - نیاز به dashboard integration

## B) GSL (Pump/Dump Spider Ladder)

### ✅ موجود:
- Basic shock detection (ATR spike)
- Basic structure

### ⛔️ ناقص/نیاز به پیاده‌سازی:
1. **تعریف دقیق Shock Detector (ATR x mean)** - نیاز به بهبود
2. **تایید Volume spike (>2x avg)** - نیاز به پیاده‌سازی
3. **ورود دوطرفه (LONG + SHORT)** - نیاز به پیاده‌سازی
4. **Ladder tracking در registry** - نیاز به پیاده‌سازی
5. **Trailing SL state ذخیره شود** - نیاز به پیاده‌سازی
6. **گزارش Pump/Leg/SL/Ladder events** - نیاز به پیاده‌سازی
7. **اضافه شدن به Digest summary** - نیاز به integration

## C) Hybrid

### ✅ موجود:
- Score calculation (0-100)
- Q-Breakout, EMA/HMA, MACD, ATR scoring
- Daily reset logic
- State persistence

### ⛔️ ناقص/نیاز به بهبود:
1. **امتیازدهی نهایی در dashboard** - نیاز به dashboard integration
2. **لیست top 3 symbols هر دوره** - نیاز به tracking
3. **لاگ threshold hit** - نیاز به logging
4. **ثبت reason breakdown در registry** - نیاز به registry integration
5. **گزارش Event-based فقط برای score >= threshold** - نیاز به event reporter

## D) Scanner Infrastructure

### ✅ موجود:
- ScannerStateRegistry با persistence
- DashboardManager
- DigestReporter
- EventReporter
- Basic watchdog (scanner_watchdog_task)

### ⛔️ ناقص/نیاز به بهبود:
1. **همه scannerها watchdog داشته باشند** - نیاز به universal watchdog
2. **هر scanner: interval/last_run/next_run/last_error/running_status** - نیاز به tracking کامل
3. **همه در /dash نمایش داده شوند** - نیاز به dashboard integration
4. **Digest unified report** - نیاز به بهبود

## E) AI Optimizer

### ✅ موجود:
- Strategy weights (QGLN 30%, Hybrid 25%, GSL 25%, Trend 20%)
- Watchlist management
- Evaluation cycle
- Suggestion history (basic)

### ⛔️ ناقص/نیاز به بهبود:
1. **وزن‌دهی رسمی** - موجود ولی نیاز به verification
2. **top 10 dynamic symbols** - نیاز به dynamic watchlist
3. **silent hours integration** - نیاز به integration
4. **suggestion history ذخیره شود** - نیاز به persistence


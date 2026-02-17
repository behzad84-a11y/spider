# FULL FEATURE AUDIT (Strict Mode)
**Date checked:** 2026-02-16  
**Rules:** No code changed. Exact line numbers or MISSING. Proof of CALLED/RUNS.

**Files read:** gln_strategy.py, gln_hybrid_strategy.py, gsl_strategy.py, gln_forex_strategy.py, dashboard.py, ai_optimizer.py, spider_trading_bot.py, config.py, risk_engine.py, deploy_vps.ps1, SwitchToVPS_NEW.bat, silent_manager.py, database_manager.py

---

## SECTION 1: QGLN Strategy (gln_strategy.py)

### 1.1 NY Time Engine
- **get_ny_time()** — Line 116–119: uses `pytz.timezone('America/New_York')` and `datetime.now(ny_tz)`. ✅
- **DST** — Handled by pytz (no manual offset). ✅
- **Called in scan loop** — Line 137 (`now_ny = self.get_ny_time()`), 167 (same) inside `check_market()`. ✅

### 1.2 Candle Counter (1 / 7 / 12 / 18)
- **candle_count incremented** — Line 187: `self.candle_count = current_candle_num` (set from `time_elapsed // 5 + 1`, not per-fetch). ✅
- **Per minute vs per candle** — Set once per `check_market()` from elapsed minutes (effectively per 5‑min candle). ✅
- **Report at 1, 7, 12, 18** — Lines 209–212: `if current_candle_num == 1:`, `== 7:`, `== 12:`, `== 18:` with log/notification and gap check at 7 and 18. ✅
- **Lock after 18** — Line 254: `self.is_q_channel_set = True` when `current_candle_num > 18` and not yet set (or late-start recovery). “Lock” = q_high/q_low frozen, signals allowed. ✅

### 1.3 Q-Channel (q_high / q_low)
- **Updated** — Lines 194–197: only when `current_candle_num <= 18`; if price > q_high / < q_low then update. ✅
- **Frozen after 18** — No updates after candle 18; lock at 254. ✅
- **Restart recovery** — Lines 216–248: if bot starts late and `q_high == 0`, fetches OHLCV and sets q_high/q_low from first 18 candles; then sets `is_q_channel_set = True`. ✅

### 1.4 Gap Detection
- **gap_filled, pdh, pdl, pdc** — pdh/pdl/pdc: 37–39 (init), 90–92 (calculate_daily_levels), 513–515 (save_state), 555–557 (load_state). gap_filled: 42, 272–279 (check_gap), 305 (registry). ✅
- **Gap status to Telegram** — Lines 280 (FILLED), 292 (OPEN at candle 18) via `send_notification`. ✅
- **Gap status to registry** — Line 305: `self.scanner_registry.update('QGLN', gap_status=..., gap_filled=self.gap_filled)`. ✅

### 1.5 Daily Reset
- **perform_daily_reset()** — Line 580; resets q_high, q_low, candle_count, gap_filled, etc. (584–592). ✅
- **Trigger time** — Lines 171–173: `reset_time = time(9, 25)`; `if now_ny.time() >= reset_time` and `last_reset_date != today` then `await self.perform_daily_reset(today)`. ✅ 09:25 NY.
- **Called from scan loop** — Yes, inside `check_market()` at 167–173. ✅

### 1.6 Registry Sync
- **scanner_registry.update('QGLN', ...)** — Line 305 (gap_status, gap_filled); 611–617 (after daily reset); 622–640 (`update_registry_state`: candle_count, q_high, q_low, is_q_channel_set, q_probability, current_price, gap_filled, trend_direction, last_update); 647 (active_symbols). ✅
- **After every scan** — `update_registry_state()` called at 190 inside the candle loop (every check_market when time_elapsed > 0). ✅

### 1.7 State Persistence
- **save_state()** — Line 506: saves pdh, pdl, pdc, gap_filled, candle_count, q_high, q_low, is_q_channel_set, etc. to DB via db_manager.save_strategy. ✅
- **load_state()** — Line 540; called at line 67 in `initialize()` (async startup). ✅
- **Restore after restart** — load_state() restores candle_count, q_high, q_low, etc. (559–565). So restart at candle 15 with q_high=50000 restores. ✅

### 1.8 /dash output for QGLN
- **Where QGLN appears** — dashboard.py 205–217 (_scanner_summary), 276–295 (get_verbose QGLN block). ✅
- **Shows** — candle#, q_high, q_low, gap_status, is_q_channel_set, last_signal (in registry); last_run derived from last_run_ts. ✅

**SECTION 1 Summary:** ✅ FULLY WORKING (all items with line refs above).

---

## SECTION 2: Hybrid Strategy (gln_hybrid_strategy.py)

### 2.1 Scoring Engine (0–100)
- **Function** — `calculate_score()` line 94; returns (score, details). ✅
- **Components** — Q-Breakout 30 (109–112), EMA/HMA 25 (118–129, 133), MACD 20 (136–143), ATR 15 (152–154), Correlation 10 (158–159). ✅
- **Max score** — 30+25+25+20+15+10 = 125; 100 is achievable. ✅

### 2.2 Threshold
- **Enforced** — Line 211: `if score >= 70:`. ✅
- **&lt; 70** — No signal, no log at that line (loop continues). ⚠️ No explicit “skipped” log.
- **≥ 70** — Line 211–215: if auto_mode then execute_trade; else send_notification (manual). ✅

### 2.3 Signal Output
- **To Telegram** — execute_trade sends msg at 254–263 (side, score, price, SL, details). Manual path: 215 (notification with “از دکمه‌های پنل استفاده کنید”). ✅
- **Inline Buy/Sell buttons** — Not in gln_hybrid_strategy.py; only text. ❌ No inline buttons in this file.
- **Score in message** — Yes, line 257: `امتیاز: \`{score}/100\``. ✅

### 2.4 Auto vs Manual
- **auto_mode** — Line 25: `self.auto_mode = True`; toggled by /auto_on, /auto_off (in spider_trading_bot). ✅
- **Auto** — Line 211–213: `if self.auto_mode: await self.execute_trade(...)`. ✅
- **Auto does trade** — execute_trade calls execution_engine.execute (line 244). So auto mode places order. ✅

### 2.5 Registry + Digest
- **Hybrid updates registry** — No `scanner_registry` in gln_hybrid_strategy.py; no `registry.update('Hybrid', ...)` in this file. ❌
- **Digest** — DigestReporter iterates all scanners from registry (dashboard.py 596–600); Hybrid is registered (spider_trading_bot 214) but Hybrid strategy never writes to registry from its own code. ⚠️ PARTIAL: registry updated only from spider loop if Hybrid is run via a dedicated loop that updates registry (not found in this file).

**SECTION 2 Summary:** ⚠️ PARTIAL — Scoring, threshold, auto/manual, signal text work. Missing: Hybrid updating scanner_registry from within strategy; no Buy/Sell inline buttons in strategy.

---

## SECTION 3: GSL Strategy (gsl_strategy.py)

### 3.1 Pump/Dump Detection
- **ATR spike** — Lines 55–62: `current_atr > prev_atr * 1.5` (1.5x multiplier). ✅
- **Volume surge** — Not present. ❌ MISSING (no volume > 2x average).
- **Candle structure** — Not present. ❌ MISSING (no body size/direction check).

### 3.2 Ladder Entry
- **add_ladder_leg** — Line 92–94: `pass` only. ❌ MISSING implementation.
- **Entry size / rungs** — base_leg_size = 10 (line 32); no rung logic. ❌
- **Track rungs filled** — Not implemented. ❌

### 3.3 Stop Loss Management
- **SL 1.5× ATR** — Not in GSL; only in Hybrid (line 231 gln_hybrid). ❌ MISSING in GSL.
- **Breakeven when new rung** — Not in GSL. ❌ MISSING.

### 3.4 Long / Short
- **Direction** — detect_shock returns bool only (line 42–66); no side. ❌ No pump vs dump / long vs short.

### 3.5 Symbol Tiers
- **BTC/ETH/BNB vs others** — No tier logic in gsl_strategy.py. ❌ MISSING.

**SECTION 3 Summary:** ❌ PARTIAL — Only ATR spike (1.5x) at 59. Volume, candle structure, ladder, SL, breakeven, long/short, tiers all MISSING.

---

## SECTION 4: GLN_FX Scanner (gln_forex_strategy.py)

- **Registered as 'GLN_FX'** — spider_trading_bot.py line 216: `register('GLN_FX', {..., 'enabled': False})`. ✅
- **Default enabled** — False (line 216). ✅
- **Updates registry** — No scanner_registry in gln_forex_strategy.py. ❌ Not updated by this file.
- **Crash-prone** — mt5 usage, asyncio.iscoroutinefunction (69–75); no bare except in sampled code. ⚠️ Optional MT5 import at top of spider could affect if symbol not found.

**SECTION 4 Summary:** ⚠️ PARTIAL — Registered and disabled; no registry update from file; no obvious bare-except in read sections.

---

## SECTION 5: Dashboard & Reporting

### 5.1 /dash command
- **Handler** — spider_trading_bot.py 3365: `dash_command`; 3371: `full_report = context.args and 'full' in context.args`; 3376: `msg = self.dashboard.get_unified_dashboard(full=full_report)`.
- **get_unified_dashboard** — NOT DEFINED in dashboard.py (DashboardManager has get_short, get_verbose, get_full, get_health only). ❌ MISSING → /dash will raise AttributeError unless added elsewhere.
- **_env_block (if used)** — dashboard.py 121–164: ENV_TYPE ✅, MODE ✅, Uptime ✅, PID ✅, Host ✅, Master (Role) ✅, Build ✅.
- **_scanner_summary** — 166–220: per-scanner status, last_run, QGLN block, AI block. ✅ (but only reachable if get_unified_dashboard exists or is replaced by get_short/get_verbose/get_full.)

### 5.2 /dash full
- **Exists** — As argument: 3371 `'full' in context.args` → full_report=True. ✅
- **get_full()** — dashboard.py 318: verbose + EE stats, DB, equity, background tasks. ✅
- **Extra vs /dash** — get_full() includes execution engine, DB, equity, task list. ✅ (Again, only if unified dashboard is fixed.)

### 5.3 DigestReporter
- **Class** — dashboard.py 543. ✅
- **Interval** — 546: `interval_minutes=60`; 557: `await asyncio.sleep(self.interval * 60)`. ✅ 60 min.
- **Content** — generate() 585–624: scans/signals totals, per-scanner line, positions, MODE, AI last suggestion. ✅
- **Silent** — 559–563: checks `silent_mgr.should_send('LOW')` — should_send expects int (LOW=3); passing 'LOW' is wrong. ⚠️ Bug; also generate() is called as generate(full=False) but signature is generate(self) — no full param. ⚠️

### 5.4 EventReporter
- **Class** — dashboard.py 460. ✅
- **Events** — 463–473: HIGH_SCORE, STRONG_SIGNAL, TRADE_ENTRY, LADDER_ADD, SL_MOVE, EMERGENCY_EXIT, CRASH, RESTART, SCANNER_START/STOP. ✅
- **vs Digest** — Event = immediate (report()); Digest = periodic (run_loop). ✅

### 5.5 SilentManager
- **Hours** — silent_manager.py 36–42: default 23:00–07:00. ✅
- **During silent** — CRITICAL always sent; HIGH sent; NORMAL/LOW suppressed (78–84). ✅
- **AI Optimizer** — ai_optimizer.py 456–457: checks silent_mgr.is_silent() before _send_suggestion. ✅

**SECTION 5 Summary:** ⚠️ PARTIAL — get_unified_dashboard MISSING (breaks /dash). Digest silent check uses 'LOW' vs int; generate(full=False) vs no full param. Rest (env, scanners, events, silent) present.

---

## SECTION 6: AI Optimizer (ai_optimizer.py)

### 6.1 Interval
- **30 min** — Line 85: `interval_min = getattr(config, 'AI_EVAL_INTERVAL', 30)`; 88: `interval = interval_min * 60`. ✅

### 6.2 Symbol List
- **DEFAULT_SYMBOLS** — Line 28: BTC/USDT:USDT, ETH/USDT:USDT, BNB/USDT:USDT. ✅
- **Top 10** — 186–194: new_list = defaults + top by volume up to MAX_WATCHLIST (10). ✅
- **Other 7** — By volume from fetch_tickers (177–184). ✅

### 6.3 Threshold
- **≥ 75** — Line 154–155: `if best_score >= threshold` with `threshold = getattr(config, 'AI_THRESHOLD', 75)`; 157: `await self._send_suggestion(...)`. ✅

### 6.4 Weighting
- **STRATEGY_WEIGHTS** — Lines 35–40: QGLN 0.30, GLN_Hybrid 0.25, GSL 0.25, Trend_MACD 0.20. ✅
- **Used** — Line 240: `total = sum(scores[s] * STRATEGY_WEIGHTS[s] for s in scores)`. ✅

### 6.5 No Auto-Trade
- **_send_suggestion** — Lines 453–504: only builds msg, updates registry, event_reporter.report, and `await self._callback(msg)`. No place_order or execute. ✅
- **Message** — Line 471: "Suggestion only — no auto-trade". ✅

### 6.6 Silent Integration
- **Check** — Lines 455–457: `if self.silent_mgr and self.silent_mgr.is_silent(): return`. ✅

**SECTION 6 Summary:** ✅ FULLY WORKING.

---

## SECTION 7: Token & Mode Safety

### 7.1 Token Separation
- **resolve_bot_token_strict()** — spider_trading_bot.py 4052. ✅
- **VPS → BOT_TOKEN_LIVE** — 4068–4071. ✅
- **LOCAL → BOT_TOKEN_DEV** — 4077–4082. ✅
- **Fallback to BOT_TOKEN** — 4088–4092: if token missing, exit; 4104–4106: if config.BOT_TOKEN exists, only log warning, never use. ✅ No fallback.

### 7.2 Mode Enforcement
- **VPS + DEV blocked** — 4123–4127, sys.exit(12). ✅
- **LOCAL + LIVE blocked** — 4133–4136, sys.exit(12). ✅
- **LOCAL + PAPER** — 4137–4139: blocked unless ALLOW_LOCAL_PAPER. ✅
- **VPS + LIVE / PAPER** — Allowed (no exit for these). ✅
- **LOCAL + DEV** — Allowed (no block). ✅

### 7.3 /token command
- **token_command** — 3622. Shows ENV (3627), Type (3632–3635), Fingerprint (3635), PID, Host, CWD, Build (3638–3640). ✅

**SECTION 7 Summary:** ✅ FULLY WORKING.

---

## SECTION 8: Risk Engine (risk_engine.py)

### 8.1 Minimum Notional
- **Where** — validate_async 76–104; min_notional from limits.get('min_notional', 5.0) line 82. ✅
- **Checked before order** — 86: request_notional < min_notional then reject or auto-fix leverage. ✅
- **Below minimum** — Returns False with message (102–104). ✅
- **CoinEx default** — 5.0 from get; actual from get_min_trade_requirements. ✅

### 8.2 Amount Validation
- **validate()** — 24; symbol exposure, leverage cap. ✅
- **validate_async()** — 66; adds exchange min notional; notional = amount * leverage (84). ✅

**SECTION 8 Summary:** ✅ FULLY WORKING.

---

## SECTION 9: Scanner System

### 9.1 All 6 Scanners Registered
- **spider_trading_bot.py 213–218:** QGLN ✅, Hybrid ✅, GSL ✅, GLN_FX ✅, Manual ✅, AI_Optimizer ✅. ✅

### 9.2 Watchdog
- **_watchdog_check_once** — spider_trading_bot.py 3562. Checks last_run_ts per scanner; if stalled (diff > threshold) logs and updates RESTARTING (3582–3583). ✅
- **watchdog_fallback_loop** — 3566: loop every 300s (3618); _watchdog_check_once at 3619. ✅
- **Restart** — Code updates status to RESTARTING; scanner_watchdog in dashboard (if used) may restart; main bot loop does not auto-restart a crashed strategy task here — run_gln_loop has try/except and continues. ⚠️ Watchdog detects stall; actual “restart” of scanner task not fully traced in one place.

### 9.3 Crash Isolation
- **run_gln_loop** — 2768–2780: try/except around check_market(); on exception log and sleep 60, then continue loop. ✅ One GLN crash doesn’t stop the loop.
- **run_strategy** — 452–459: try/except around initialize + check_market; on exception strategy removed from active_strategies. ✅

**SECTION 9 Summary:** ✅ Registration and isolation present; watchdog runs and marks stalled; exact “restart” flow partially documented.

---

## SECTION 10: Deployment

### 10.1 ZIP Contents
- **SwitchToVPS_NEW.bat** line 74: `tar -a -c -f "%ZIP_NAME%" run_bot_vps.py *.py .env.example requirements.txt *.bat deploy_vps.ps1`. ✅ All *.py and run_bot_vps.py included.

### 10.2 Zombie Killer
- **deploy_vps.ps1** 105–111: Get-CimInstance Win32_Process where CommandLine like spider_trading_bot.py; Stop-Process. ✅
- **When** — After extract (step 3), after ENV (step 4); before pip and Start GREEN. ✅

### 10.3 Health Check
- **Markers** — 162: "Bot starting polling", "Bot is polling", "Application started successfully", etc. ✅
- **409 check** — 167–178 Test-LogForConnectionFailure; 252–256 in stability window. ✅
- **Rollback** — 291–306: kill GREEN, exit 5, preserve BLUE. ✅

### 10.4 Scheduled Task
- **After pass** — deploy_vps.ps1 7b–7d: stop GREEN, create start_fresh.bat, schtasks create/run SpiderBot. ✅

### 10.5 /health and /selftest
- **health_command** — 3383–3391: calls dashboard.get_health(). ✅
- **get_health** — dashboard 376–453: Exchange, Database, Instance Lock, Scanners, Position Tracker, AI Optimizer, Silent Manager, Memory. ✅ (No “token correct for env” in get_health; that’s in selftest.)
- **selftest_command** — 3393–3504: ENV+token, Telegram get_me(), Exchange fetch_balance, DB cursor query, Registry, MODE, JobQueue (informational). ✅
- **Selftest** — Does not send a separate test message; uses get_me() as Telegram check. Does query exchange (balance). JobQueue check in try/except, does not fail selftest (3488). ✅

**SECTION 10 Summary:** ✅ Deployment and health/selftest as above. /dash still broken by missing get_unified_dashboard.

---

## SUMMARY

### ✅ FULLY WORKING
- QGLN: NY time, candles 1/7/12/18, Q-channel lock, gap, daily reset 09:25 NY, registry sync, state persistence, /dash QGLN block.
- AI Optimizer: interval, watchlist, threshold 75, weights, no auto-trade, silent check.
- Token & mode: resolve_bot_token_strict, validate_run_mode, /token.
- Risk engine: min notional, validate_async.
- Scanner registration (all 6), watchdog loop, crash isolation in GLN/run_strategy.
- Deployment: zip, zombie killer, health check, 409, rollback, SpiderBot task update; /health and /selftest.

### ⚠️ PARTIAL
- **Hybrid:** No registry update from strategy; no inline Buy/Sell buttons in strategy file.
- **GSL:** Only ATR spike (1.5x); volume surge, candle structure, ladder, SL, breakeven, long/short, tiers MISSING.
- **GLN_FX:** No registry update from file; enabled=False.
- **Dashboard:** get_unified_dashboard MISSING → /dash will AttributeError. Digest: should_send('LOW') wrong type; generate(full=False) but no full param.

### ❌ MISSING
- **DashboardManager.get_unified_dashboard(full=)** — called by dash_command; not defined in dashboard.py.
- **GSL:** Volume surge, candle structure, ladder implementation, SL 1.5 ATR, breakeven, pump/dump direction, symbol tiers.

### 🔴 CRITICAL (before live)
- **get_unified_dashboard** — Without it, /dash crashes. Either implement (e.g. get_short/get_verbose/get_full based on full) or replace call in dash_command.
- **GSL** — Only shock detection; no real entry/ladder/SL; do not rely for live until implemented.

---

## PRIORITY FIX LIST

**P1 (fix before use):**
- Add `DashboardManager.get_unified_dashboard(self, full=False)` (e.g. return get_full() if full else get_short() or get_verbose()).
- Fix DigestReporter: use `should_send(3)` or SilentManager.LOW instead of `'LOW'`; add `full` to generate() if needed.

**P2 (this week):**
- Have Hybrid update scanner_registry (e.g. last_run_ts, last_signal) from its loop or from spider when Hybrid runs.
- GSL: implement ladder, SL, and optionally volume/candle/tiers if required.

**P3 (nice to have):**
- Inline Buy/Sell for Hybrid suggestions; GLN_FX registry updates; Digest generate(full) support.

---

Awaiting confirmation.

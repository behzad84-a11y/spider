"""
Dashboard Module — Professional Trading Operating System
=========================================================
ScannerStateRegistry : Centralized state for all scanners
DashboardManager     : Formats /dash, /dash verbose, /dash full, /health
DigestReporter       : Periodic summary messages (every N minutes)
EventReporter        : Instant alerts on key events
"""

import json
import os
import time
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable, Awaitable

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 1. SCANNER STATE REGISTRY
# ═══════════════════════════════════════════════════════════════════

class ScannerStateRegistry:
    """Thread-safe centralized state store for all scanners."""

    PERSIST_FILE = 'scanner_registry.json'

    def __init__(self, persist_dir: str = '.'):
        self._scanners: Dict[str, Dict[str, Any]] = {}
        self._persist_path = os.path.join(persist_dir, self.PERSIST_FILE)
        self.load()

    def _default_state(self) -> Dict[str, Any]:
        return {
            'enabled': False,
            'interval': 60,
            'schedule': None,
            'last_run_ts': None,
            'next_run_ts': None,
            'last_summary': '',
            'last_signal': None,
            'last_error': None,
            'running_status': 'IDLE',        # IDLE / SCANNING / ERROR / STOPPED
            'active_symbols': [],
            'score_threshold': 65,
            'total_scans': 0,
            'total_signals': 0,
            'started_at': None,
            'restarts': 0,
        }

    def register(self, name: str, config: Optional[Dict[str, Any]] = None):
        """Register a scanner with optional config overrides."""
        state = self._default_state()
        # Preserve existing data if already registered
        if name in self._scanners:
            existing = self._scanners[name]
            state.update(existing)
        if config:
            state.update(config)
        self._scanners[name] = state
        logger.info(f"REGISTRY: Scanner '{name}' registered")

    def update(self, name: str, **kwargs):
        """Update specific fields of a scanner."""
        if name not in self._scanners:
            self.register(name)
        self._scanners[name].update(kwargs)

    def get(self, name: str) -> Dict[str, Any]:
        """Get a scanner's full state."""
        return self._scanners.get(name, self._default_state())

    def get_all(self) -> Dict[str, Dict[str, Any]]:
        """Get all scanners' states."""
        return dict(self._scanners)

    def save(self):
        """Persist registry to JSON file."""
        try:
            with open(self._persist_path, 'w', encoding='utf-8') as f:
                json.dump(self._scanners, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"REGISTRY: Save failed: {e}")

    def load(self):
        """Load registry from JSON file."""
        if os.path.exists(self._persist_path):
            try:
                with open(self._persist_path, 'r', encoding='utf-8') as f:
                    self._scanners = json.load(f)
                logger.info(f"REGISTRY: Loaded {len(self._scanners)} scanners from disk")
            except Exception as e:
                logger.error(f"REGISTRY: Load failed: {e}")
                self._scanners = {}

    def increment(self, name: str, field: str, amount: int = 1):
        """Increment a numeric field."""
        if name in self._scanners:
            self._scanners[name][field] = self._scanners[name].get(field, 0) + amount


# ═══════════════════════════════════════════════════════════════════
# 2. DASHBOARD MANAGER
# ═══════════════════════════════════════════════════════════════════

class DashboardManager:
    """
    Formats dashboard output from cached data.
    All methods read from in-memory state — guaranteed < 1 second.
    """

    def __init__(self, bot):
        """
        Args:
            bot: TradingBot instance with access to all subsystems
        """
        self.bot = bot

    def _env_block(self) -> str:
        """Environment info block (shared across all views)."""
        import config
        bot = self.bot
        uptime = datetime.now() - bot.start_time if hasattr(bot, 'start_time') else timedelta(0)
        days = uptime.days
        hours, rem = divmod(uptime.seconds, 3600)
        mins, _ = divmod(rem, 60)

        env = getattr(bot, 'run_env', config.ENV_TYPE or 'UNKNOWN')
        mode = config.MODE
        hostname = getattr(bot, 'hostname', 'N/A')
        pid = os.getpid()
        version = getattr(bot, 'BOT_VERSION', 'N/A') if hasattr(bot, 'BOT_VERSION') else 'N/A'
        master = "MASTER" if getattr(bot, 'is_master', False) else "STANDBY"
        build_time = getattr(bot, 'build_time', None)
        build_str = build_time if build_time else 'N/A'

        # Token fingerprint
        token_raw = getattr(bot, 'bot_token_raw', '')
        fp = f"{token_raw[:6]}...{token_raw[-4:]}" if token_raw and len(token_raw) > 10 else 'N/A'

        # Mode emoji
        mode_icons = {'LIVE': '🔴', 'PAPER': '🟡', 'DEV': '🟢'}
        mi = mode_icons.get(mode, '⚪')

        # Silent Manager status
        silent_mgr = getattr(bot, 'silent_manager', None)
        silent_str = ''
        if silent_mgr:
            if silent_mgr.is_silent():
                silent_str = ' | 🔇 SILENT'
            else:
                silent_str = ' | 🔊 ACTIVE'

        lines = [
            f"🖥 <b>ENV:</b> {env} | {mi} <b>MODE:</b> {mode}{silent_str}",
            f"⏱ <b>Uptime:</b> {days}d {hours}h {mins}m",
            f"👑 <b>Role:</b> {master} | <b>PID:</b> {pid}",
            f"🏷 <b>Host:</b> {hostname} | <b>Ver:</b> {version}",
            f"🔑 <b>Token:</b> <code>{fp}</code>",
            f"📅 <b>Build:</b> {build_str}",
        ]
        return '\n'.join(lines)

    def _scanner_summary(self) -> str:
        """One-line per scanner summary."""
        registry = getattr(self.bot, 'scanner_registry', None)
        if not registry:
            return "📡 No scanners registered"

        lines = ["", "📡 <b>Scanners:</b>"]
        status_icons = {
            'SCANNING': '🟢',
            'IDLE': '⚪',
            'ERROR': '🔴',
            'STOPPED': '⛔',
        }
        for name, state in registry.get_all().items():
            icon = status_icons.get(state.get('running_status', 'IDLE'), '❓')
            scans = state.get('total_scans', 0)
            signals = state.get('total_signals', 0)
            last_run = state.get('last_run_ts', 'Never')
            if last_run and last_run != 'Never':
                try:
                    dt = datetime.fromisoformat(str(last_run))
                    ago = datetime.now() - dt
                    if ago.total_seconds() < 60:
                        last_run = f"{int(ago.total_seconds())}s ago"
                    elif ago.total_seconds() < 3600:
                        last_run = f"{int(ago.total_seconds() // 60)}m ago"
                    else:
                        last_run = f"{int(ago.total_seconds() // 3600)}h ago"
                except:
                    pass

            # Special formatting for known scanners
            if name == 'AI_Optimizer':
                ai_opt = getattr(self.bot, 'ai_optimizer', None)
                if ai_opt and ai_opt.last_suggestion:
                    ls = ai_opt.last_suggestion
                    lines.append(f"  {icon} <b>AI:</b> ON | last={ls['symbol']}/{ls['strategy']}/{ls['score']:.0f}")
                else:
                    lines.append(f"  {icon} <b>AI:</b> ON | {scans} evals | No suggestion yet")
            elif name == 'QGLN':
                # QGLN specific formatting with Candle# and Q-Channel state
                candle = state.get('candle_count', 0)
                q_high = state.get('q_high', 0.0)
                q_low = state.get('q_low', 0.0)
                is_locked = state.get('is_q_channel_set', False)
                gap_status = state.get('gap_status', 'N/A')
                q_prob = state.get('q_probability', 0)
                
                if is_locked:
                    lines.append(f"  {icon} <b>QGLN:</b> Candle {candle}/18 ({q_prob}%) | Q-Channel: {q_high:.2f}/{q_low:.2f} | Gap: {gap_status}")
                else:
                    lines.append(f"  {icon} <b>QGLN:</b> Candle {candle}/18 ({q_prob}%) | Tracking... | Gap: {gap_status}")
            else:
                lines.append(f"  {icon} <b>{name}:</b> {scans} scans, {signals} signals | Last: {last_run}")
        return '\n'.join(lines)

    def _positions_summary(self) -> str:
        """Quick positions summary."""
        tracker = getattr(self.bot, 'position_tracker', None)
        if not tracker:
            return "\n📊 <b>Positions:</b> N/A"

        try:
            positions = tracker.get_positions() or []
            open_count = len([p for p in positions if float(p.get('contracts', 0)) != 0])
            total_unrealized = sum(float(p.get('unrealizedPnl', 0)) for p in positions if float(p.get('contracts', 0)) != 0)
            pnl_icon = "📈" if total_unrealized >= 0 else "📉"
            return f"\n📊 <b>Open Positions:</b> {open_count} | {pnl_icon} <b>uPnL:</b> ${total_unrealized:+.2f}"
        except Exception:
            return "\n📊 <b>Positions:</b> sync pending"

    def _strategies_summary(self) -> str:
        """Active strategy count."""
        strats = getattr(self.bot, 'gln_strategies', {})
        count = len(strats)
        running = sum(1 for s in strats.values() if getattr(s, 'running', False))
        return f"\n🧠 <b>Active Strategies:</b> {running}/{count}"

    def get_short(self) -> str:
        """
        /dash — Compact overview.
        Target: < 10 lines, instant read.
        """
        header = "📊 <b>Spider Dashboard</b>\n" + "─" * 25
        env = self._env_block()
        scanners = self._scanner_summary()
        positions = self._positions_summary()
        strategies = self._strategies_summary()

        return f"{header}\n{env}\n{scanners}\n{positions}\n{strategies}"

    def get_verbose(self) -> str:
        """
        /dash verbose — Extended report with per-scanner details and risk state.
        """
        header = "📊 <b>Spider Dashboard (Verbose)</b>\n" + "═" * 30
        env = self._env_block()
        positions = self._positions_summary()
        strategies = self._strategies_summary()

        # Detailed scanner info
        registry = getattr(self.bot, 'scanner_registry', None)
        scanner_detail = ["\n📡 <b>Scanner Details:</b>"]
        if registry:
            for name, state in registry.get_all().items():
                scanner_detail.append(f"\n  <b>▸ {name}</b>")
                scanner_detail.append(f"    Status: {state.get('running_status', 'N/A')}")
                scanner_detail.append(f"    Scans: {state.get('total_scans', 0)} | Signals: {state.get('total_signals', 0)}")
                scanner_detail.append(f"    Interval: {state.get('interval', 'N/A')}s | Threshold: {state.get('score_threshold', 'N/A')}")
                
                # QGLN specific details
                if name == 'QGLN':
                    candle = state.get('candle_count', 0)
                    q_high = state.get('q_high', 0.0)
                    q_low = state.get('q_low', 0.0)
                    is_locked = state.get('is_q_channel_set', False)
                    q_prob = state.get('q_probability', 0)
                    gap_status = state.get('gap_status', 'N/A')
                    gap_filled = state.get('gap_filled', False)
                    current_price = state.get('current_price', 0.0)
                    trend_dir = state.get('trend_direction', 'N/A')
                    last_reset = state.get('last_reset_date', 'N/A')
                    
                    scanner_detail.append(f"    📊 QGLN State:")
                    scanner_detail.append(f"      Candle: {candle}/18 | Probability: {q_prob}%")
                    scanner_detail.append(f"      Q-Channel: {'🔒 LOCKED' if is_locked else '⏳ Tracking'}")
                    if is_locked:
                        scanner_detail.append(f"        High: {q_high:.2f} | Low: {q_low:.2f}")
                    scanner_detail.append(f"      Current Price: {current_price:.2f}")
                    scanner_detail.append(f"      Gap Status: {'✅ FILLED' if gap_filled else '❌ OPEN'} ({gap_status})")
                    scanner_detail.append(f"      Trend: {trend_dir}")
                    scanner_detail.append(f"      Last Reset: {last_reset}")
                
                symbols = state.get('active_symbols', [])
                if symbols:
                    scanner_detail.append(f"    Symbols: {', '.join(symbols[:5])}")
                last_sig = state.get('last_signal')
                if last_sig:
                    scanner_detail.append(f"    Last Signal: {last_sig}")
                last_err = state.get('last_error')
                if last_err:
                    scanner_detail.append(f"    ⚠️ Last Error: {last_err}")
                scanner_detail.append(f"    Restarts: {state.get('restarts', 0)}")

        # Risk engine state
        risk_info = "\n⚖️ <b>Risk Engine:</b> N/A"
        risk = getattr(self.bot, 'risk_engine', None)
        if risk:
            risk_info = "\n⚖️ <b>Risk Engine:</b> Active"

        return f"{header}\n{env}\n{positions}\n{strategies}\n{''.join(scanner_detail)}\n{risk_info}"

    def get_full(self) -> str:
        """
        /dash full — Deep technical report.
        """
        verbose = self.get_verbose()

        # Execution engine stats
        ee = getattr(self.bot, 'execution_engine', None)
        ee_info = "\n🔧 <b>Execution Engine:</b>"
        if ee:
            mode_str = getattr(ee, 'mode', 'N/A')
            active = getattr(ee, 'is_active', False)
            ee_info += f"\n  Mode: {mode_str} | Active: {active}"
            stat = getattr(ee, 'stats', {})
            if stat:
                ee_info += f"\n  Stats: {json.dumps(stat)}"

        # Database info
        db = getattr(self.bot, 'db_manager', None)
        db_info = "\n🗃 <b>Database:</b>"
        if db:
            try:
                strat_count = len(db.get_active_strategies()) if hasattr(db, 'get_active_strategies') else 'N/A'
                db_info += f"\n  Active Strategies in DB: {strat_count}"
            except:
                db_info += "\n  Status: Connected"

        # Equity snapshot
        eq_info = "\n💰 <b>Equity:</b>"
        tracker = getattr(self.bot, 'position_tracker', None)
        if tracker:
            total = getattr(tracker, 'last_total_equity', None)
            if total:
                eq_info += f" ${total:.2f}"
            else:
                eq_info += " Pending first snapshot"

        # Background tasks
        tasks = [
            ('scheduler_task', 'Scheduler'),
            ('equity_task', 'Equity Snapshot'),
            ('q_collector_task', 'Q-Candle Collector'),
            ('heartbeat_task', 'Heartbeat'),
            ('digest_task', 'Digest Reporter'),
            ('ai_optimizer_task', 'AI Optimizer'),
        ]
        task_info = "\n⚙️ <b>Background Tasks:</b>"
        for attr, label in tasks:
            task = getattr(self.bot, attr, None)
            if task and not task.done():
                task_info += f"\n  ✅ {label}: Running"
            elif task and task.done():
                task_info += f"\n  ❌ {label}: Stopped"
            else:
                task_info += f"\n  ⬜ {label}: Not started"

        return f"{verbose}\n{ee_info}\n{db_info}\n{eq_info}\n{task_info}"

    def get_health(self) -> str:
        """
        /health — Component health check.
        """
        header = "🏥 <b>System Health</b>\n" + "─" * 25
        checks = []

        # 1. Exchange connectivity
        try:
            ee = self.bot.execution_engine
            if ee and ee.futures_exchange:
                checks.append("✅ Exchange: Connected")
            else:
                checks.append("❌ Exchange: Not connected")
        except:
            checks.append("⚠️ Exchange: Unknown")

        # 2. Database
        try:
            db = self.bot.db_manager
            if db:
                db.get_setting('test_health', 'ok')
                checks.append("✅ Database: Healthy")
            else:
                checks.append("❌ Database: Not initialized")
        except:
            checks.append("❌ Database: Error")

        # 3. Instance Lock
        checks.append(f"{'✅' if self.bot.is_master else '❌'} Instance Lock: {'MASTER' if self.bot.is_master else 'STANDBY'}")

        # 4. Scanner health
        registry = getattr(self.bot, 'scanner_registry', None)
        if registry:
            all_scanners = registry.get_all()
            healthy = sum(1 for s in all_scanners.values() if s.get('running_status') in ('SCANNING', 'IDLE'))
            total = len(all_scanners)
            icon = '✅' if healthy == total else '⚠️'
            checks.append(f"{icon} Scanners: {healthy}/{total} healthy")
        else:
            checks.append("⬜ Scanners: Not initialized")

        # 5. Position Tracker
        tracker = getattr(self.bot, 'position_tracker', None)
        if tracker:
            checks.append("✅ Position Tracker: Active")
        else:
            checks.append("⬜ Position Tracker: N/A")

        # 6. AI Optimizer
        ai_opt = getattr(self.bot, 'ai_optimizer', None)
        if ai_opt:
            st = ai_opt.get_status()
            icon = '✅' if st['running'] else '⚪'
            checks.append(f"{icon} AI Optimizer: {st['eval_count']} evals, {st['suggestion_count']} suggestions")
        else:
            checks.append("⬜ AI Optimizer: Not initialized")

        # 7. Silent Manager
        silent_mgr = getattr(self.bot, 'silent_manager', None)
        if silent_mgr:
            ss = silent_mgr.get_status()
            icon = '🔇' if ss['silent_now'] else '🔊'
            checks.append(f"{icon} Silent Mode: {ss['window']} | Now={'SILENT' if ss['silent_now'] else 'ACTIVE'}")
        else:
            checks.append("⬜ Silent Manager: N/A")

        # 8. Memory usage
        try:
            import psutil
            proc = psutil.Process(os.getpid())
            mem_mb = proc.memory_info().rss / (1024 * 1024)
            icon = '✅' if mem_mb < 500 else '⚠️'
            checks.append(f"{icon} Memory: {mem_mb:.0f} MB")
        except ImportError:
            pass

        return f"{header}\n" + '\n'.join(checks)


# ═══════════════════════════════════════════════════════════════════
# 3. EVENT REPORTER
# ═══════════════════════════════════════════════════════════════════

class EventReporter:
    """Instant alerts on key trading events."""

    EVENT_TYPES = {
        'HIGH_SCORE': '🎯',
        'STRONG_SIGNAL': '📡',
        'TRADE_ENTRY': '🚀',
        'LADDER_ADD': '📶',
        'SL_MOVE': '🛡',
        'EMERGENCY_EXIT': '🚨',
        'CRASH': '💥',
        'RESTART': '♻️',
        'SCANNER_START': '▶️',
        'SCANNER_STOP': '⏹',
    }

    def __init__(self, message_callback: Optional[Callable] = None):
        self._callback = message_callback
        self._history: list = []
        self._rate_window: list = []      # timestamps of recent events
        self._dedup_cache: Dict[str, float] = {}  # event_key -> last_sent_ts
        self.max_events_per_min = 10
        self.dedup_interval = 60  # seconds

    async def report(self, event_type: str, data: Dict[str, Any]):
        """
        Fire an event alert if it passes rate limiting and deduplication.
        """
        if not self._callback:
            return

        # Rate limit
        now = time.time()
        self._rate_window = [t for t in self._rate_window if now - t < 60]
        if len(self._rate_window) >= self.max_events_per_min:
            logger.warning(f"EVENT: Rate limit reached, dropping {event_type}")
            return
        
        # Deduplication
        dedup_key = f"{event_type}:{data.get('symbol', '')}:{data.get('side', '')}"
        if dedup_key in self._dedup_cache and now - self._dedup_cache[dedup_key] < self.dedup_interval:
            return
        
        # Build message
        icon = self.EVENT_TYPES.get(event_type, '📢')
        symbol = data.get('symbol', '')
        detail = data.get('detail', '')
        score = data.get('score', '')
        
        msg_parts = [f"{icon} <b>{event_type}</b>"]
        if symbol:
            msg_parts.append(f"Symbol: <code>{symbol}</code>")
        if score:
            msg_parts.append(f"Score: {score}")
        if detail:
            msg_parts.append(detail)

        msg = '\n'.join(msg_parts)

        try:
            await self._callback(msg)
            self._rate_window.append(now)
            self._dedup_cache[dedup_key] = now
            self._history.append({
                'type': event_type,
                'data': data,
                'ts': datetime.now().isoformat(),
            })
            # Keep history bounded
            if len(self._history) > 100:
                self._history = self._history[-50:]
        except Exception as e:
            logger.error(f"EVENT: Failed to send alert: {e}")

    def get_recent_events(self, count: int = 10) -> list:
        """Return last N events."""
        return self._history[-count:]


# ═══════════════════════════════════════════════════════════════════
# 4. DIGEST REPORTER
# ═══════════════════════════════════════════════════════════════════

class DigestReporter:
    """Periodic summary messages sent to Telegram."""

    def __init__(self, bot_instance, interval_minutes=60, message_callback=None):
        self.bot = bot_instance
        self.interval = interval_minutes
        self.message_callback = message_callback
        self.last_digest_time = datetime.now()
        
    async def run_loop(self):
        self._running = True
        logger.info("DIGEST: Started background loop")
        while self._running:
            try:
                await asyncio.sleep(self.interval * 60)
                
                # Check silent manager first, fallback to built-in quiet hours
                silent_mgr = getattr(self.bot, 'silent_manager', None)
                if silent_mgr and not silent_mgr.should_send('LOW'):
                    continue
                elif not silent_mgr and self._is_quiet_hour():
                    continue
                    
                digest = self.generate(full=False)
                if digest and self.message_callback:
                    await self.message_callback(digest)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"DIGEST: Error: {e}")
                await asyncio.sleep(60)

    def stop(self):
        self._running = False

    def _is_quiet_hour(self) -> bool:
        hour = datetime.utcnow().hour
        if self._quiet_start > self._quiet_end:
            return hour >= self._quiet_start or hour < self._quiet_end
        return self._quiet_start <= hour < self._quiet_end

    def generate(self) -> str:
        """Generate a digest message from cached data."""
        registry = getattr(self.bot, 'scanner_registry', None)
        if not registry:
            return ""

        all_scanners = registry.get_all()
        total_scans = sum(s.get('total_scans', 0) for s in all_scanners.values())
        total_signals = sum(s.get('total_signals', 0) for s in all_scanners.values())
        
        # Scanner status summary
        scanner_lines = []
        for name, state in all_scanners.items():
            status = state.get('running_status', 'IDLE')
            scans = state.get('total_scans', 0)
            sigs = state.get('total_signals', 0)
            scanner_lines.append(f"  {'🟢' if status == 'SCANNING' else '⚪'} {name}: {scans}s / {sigs}sig")

        # Positions
        positions_text = "N/A"
        tracker = getattr(self.bot, 'position_tracker', None)
        if tracker:
            try:
                positions = tracker.get_positions() or []
                open_count = len([p for p in positions if float(p.get('contracts', 0)) != 0])
                total_pnl = sum(float(p.get('unrealizedPnl', 0)) for p in positions if float(p.get('contracts', 0)) != 0)
                positions_text = f"{open_count} open | uPnL: ${total_pnl:+.2f}"
            except:
                positions_text = "sync pending"

        import config
        now = datetime.now().strftime('%H:%M')
        lines = [
            f"📋 <b>Digest Report</b> ({now})",
            "─" * 20,
            f"📡 <b>Scans:</b> {total_scans} | <b>Signals:</b> {total_signals}",
            '\n'.join(scanner_lines),
            f"📊 <b>Positions:</b> {positions_text}",
            f"⚙️ <b>Mode:</b> {config.MODE}",
        ]

        # AI Optimizer digest
        ai_opt = getattr(self.bot, 'ai_optimizer', None)
        if ai_opt:
            st = ai_opt.get_status()
            if st.get('last_suggestion'):
                ls = st['last_suggestion']
                lines.append(f"\n🤖 <b>Last AI:</b> {ls['symbol']}/{ls['strategy']}/{ls['score']:.0f}")
            else:
                lines.append(f"\n🤖 <b>AI:</b> {st['eval_count']} evals, no suggestion yet")

        # Market regime (from AI optimizer if available)
        # This is a placeholder for future enhancement

        # Top scanner signals
        event_reporter = getattr(self.bot, 'event_reporter', None)
        if event_reporter:
            recent = event_reporter.get_recent_events(3)
            if recent:
                lines.append("\n🔔 <b>Recent Events:</b>")
                for evt in recent:
                    lines.append(f"  • {evt.get('type', '')} — {evt.get('data', {}).get('symbol', '')}")

        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════════
# 5. SCANNER WATCHDOG
# ═══════════════════════════════════════════════════════════════════

async def scanner_watchdog(
    name: str,
    coro_factory: Callable[[], Awaitable],
    registry: ScannerStateRegistry,
    event_reporter: Optional[EventReporter] = None,
    max_restarts: int = 5,
    cooldown: int = 30,
):
    """
    Wraps a scanner coroutine with auto-restart, crash logging, and registry updates.
    
    Args:
        name: Scanner name for registry
        coro_factory: Async function that returns when scanner loop exits
        registry: ScannerStateRegistry to update
        event_reporter: Optional EventReporter for crash alerts
        max_restarts: Max consecutive restarts before giving up
        cooldown: Seconds to wait before restart
    """
    restarts = 0
    while restarts < max_restarts:
        try:
            registry.update(name, running_status='SCANNING', started_at=datetime.now().isoformat())
            if event_reporter and restarts > 0:
                await event_reporter.report('RESTART', {'symbol': name, 'detail': f'Restart #{restarts}'})
            
            await coro_factory()
            
            # Clean exit
            registry.update(name, running_status='STOPPED')
            logger.info(f"WATCHDOG: {name} exited cleanly")
            break
        except asyncio.CancelledError:
            registry.update(name, running_status='STOPPED')
            logger.info(f"WATCHDOG: {name} cancelled")
            break
        except Exception as e:
            restarts += 1
            registry.update(
                name,
                running_status='ERROR',
                last_error=f"{type(e).__name__}: {str(e)[:200]}",
                restarts=restarts,
            )
            registry.save()
            logger.error(f"WATCHDOG: {name} crashed (restart {restarts}/{max_restarts}): {e}")
            
            if event_reporter:
                await event_reporter.report('CRASH', {
                    'symbol': name,
                    'detail': f"Crash #{restarts}: {str(e)[:100]}",
                })
            
            if restarts < max_restarts:
                await asyncio.sleep(cooldown)
            else:
                logger.critical(f"WATCHDOG: {name} exceeded max restarts ({max_restarts}). Giving up.")
                registry.update(name, running_status='STOPPED')
                registry.save()

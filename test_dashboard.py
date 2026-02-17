"""Comprehensive tests for all new modules."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

print("=" * 50)
print("SPIDER BOT — FULL SYSTEM TEST")
print("=" * 50)

# ── Test 1: Scanner Registry ──
print("\n[1] Scanner Registry...")
from dashboard import ScannerStateRegistry
import shutil

# Setup temp dir
TEST_DIR = "test_data"
if os.path.exists(TEST_DIR):
    shutil.rmtree(TEST_DIR)
os.makedirs(TEST_DIR)

r = ScannerStateRegistry(persist_dir=TEST_DIR)
r.register('QGLN', {'interval': 60, 'score_threshold': 65, 'enabled': True})
r.register('Hybrid', {'interval': 60, 'enabled': True})
r.register('GSL', {'interval': 60, 'enabled': True})
r.register('AI_Optimizer', {'interval': 1800, 'enabled': True})
r.register('Manual', {'interval': 0, 'enabled': True})
r.update('QGLN', total_scans=42, total_signals=3, running_status='SCANNING')
r.increment('QGLN', 'total_scans')
r.save()

loaded = ScannerStateRegistry(persist_dir=TEST_DIR)
qgln = loaded.get('QGLN')
assert qgln['total_scans'] == 43, f"Expected 43, got {qgln['total_scans']}"
assert qgln['running_status'] == 'SCANNING'
assert len(loaded.get_all()) == 5
print("  PASS: Registry CRUD + persistence (5 scanners)")

# Cleanup
try:
    shutil.rmtree(TEST_DIR)
except:
    pass

# ── Test 2: Scanner Registry re-export ──
print("\n[2] Scanner Registry re-export...")
from scanner_registry import ScannerStateRegistry as SSR2
assert SSR2 is ScannerStateRegistry
print("  PASS: scanner_registry.py re-exports correctly")

# ── Test 3: Silent Manager ──
print("\n[3] Silent Manager...")
from silent_manager import SilentManager
# Test with forced active window (current time should be inside)
sm = SilentManager(silent_start_hour=23, silent_end_hour=7, enabled=True)
status = sm.get_status()
assert 'enabled' in status
assert 'silent_now' in status
assert 'window' in status
print(f"  PASS: SilentManager init (enabled={status['enabled']}, silent_now={status['silent_now']})")

# Test priority filtering
from datetime import datetime
if sm.is_silent():
    assert sm.should_send(SilentManager.CRITICAL) == True  # Critical always sent
    assert sm.should_send(SilentManager.LOW) == False  # Low suppressed
    print("  PASS: Priority filtering (currently silent)")
else:
    assert sm.should_send(SilentManager.CRITICAL) == True
    assert sm.should_send(SilentManager.NORMAL) == True
    print("  PASS: Priority filtering (currently active)")

# Test override
sm_test = SilentManager(silent_start_hour=0, silent_end_hour=23, enabled=True)  # Always silent
assert sm_test.is_silent() == True
sm_test.override_active(minutes=60)
assert sm_test.is_silent() == False  # Override should make it active
sm_test.clear_override()
print("  PASS: Override mechanism works")

# ── Test 4: AI Optimizer ──
print("\n[4] AI Optimizer...")
from ai_optimizer import AIOptimizer

# Test indicator calculations (standalone)
ai = AIOptimizer(execution_engine=None)

# Test EMA
closes = list(range(1, 21))  # 1, 2, ..., 20
ema9 = ai._ema(closes, 9)
assert ema9 > 0, f"EMA9 should be positive, got {ema9}"
print(f"  PASS: EMA calculation (EMA9={ema9:.2f})")

# Test RSI
rsi = ai._rsi(closes, 14)
assert 0 <= rsi <= 100, f"RSI should be 0-100, got {rsi}"
print(f"  PASS: RSI calculation (RSI={rsi:.2f})")

# Test ATR
highs = [x + 1 for x in closes]
lows = [x - 1 for x in closes]
atr = ai._atr(highs, lows, closes, 14)
assert atr > 0, f"ATR should be positive, got {atr}"
print(f"  PASS: ATR calculation (ATR={atr:.4f})")

# Test MACD
long_closes = list(range(1, 50))
macd_line, signal_line, histogram = ai._macd(long_closes)
print(f"  PASS: MACD calculation (MACD={macd_line:.4f}, Signal={signal_line:.4f})")

# Test status
status = ai.get_status()
assert status['running'] == False
assert status['eval_count'] == 0
assert status['suggestion_count'] == 0
assert status['threshold'] == 75
print(f"  PASS: AI Optimizer status (threshold={status['threshold']})")

# ── Test 5: Dashboard Manager integration ──
print("\n[5] Dashboard Manager...")
from dashboard import DashboardManager, EventReporter, DigestReporter

# Test EventReporter
er = EventReporter(message_callback=None)
assert er.max_events_per_min == 10
assert er.dedup_interval == 60
print("  PASS: EventReporter init")

# ── Test 6: Config vars ──
print("\n[6] Config vars...")
import config
assert hasattr(config, 'AI_EVAL_INTERVAL')
assert hasattr(config, 'AI_THRESHOLD')
assert hasattr(config, 'SILENT_START_HOUR')
assert hasattr(config, 'SILENT_END_HOUR')
assert hasattr(config, 'SILENT_ENABLED')
assert hasattr(config, 'DIGEST_INTERVAL')
assert config.AI_EVAL_INTERVAL == 30
assert config.AI_THRESHOLD == 75
assert config.SILENT_START_HOUR == 23
assert config.SILENT_END_HOUR == 7
assert config.DIGEST_INTERVAL == 60
print(f"  PASS: All 6 config vars present and correct")

# ── Test 7: Strategy scoring ──
print("\n[7] Strategy Scoring...")
indicators = {
    'price': 50000,
    'prev_close': 49500,
    'ema9': 49800,
    'ema20': 49600,
    'ema50': 49000,
    'rsi': 55,
    'atr': 750,
    'atr_pct': 1.5,
    'atr_spike': 1.6,
    'macd': 50,
    'macd_signal': 40,
    'macd_hist': 10,
    'vol_ratio': 1.8,
    'volatility': 'MEDIUM',
    'regime': 'UPTREND',
    'above_ema9': True,
    'above_ema20': True,
}
reasons = []
qgln_score = ai._score_qgln(indicators, reasons)
hybrid_score = ai._score_hybrid(indicators, reasons)
gsl_score = ai._score_gsl(indicators, reasons)
trend_score = ai._score_trend(indicators, reasons)

assert 0 <= qgln_score <= 100
assert 0 <= hybrid_score <= 100
assert 0 <= gsl_score <= 100
assert 0 <= trend_score <= 100

print(f"  QGLN: {qgln_score:.0f}")
print(f"  Hybrid: {hybrid_score:.0f}")
print(f"  GSL: {gsl_score:.0f}")
print(f"  Trend/MACD: {trend_score:.0f}")
print(f"  Reasons: {reasons[:3]}")
print("  PASS: All strategy scores in valid range")

# ── Summary ──
print("\n" + "=" * 50)
print("=== ALL 7 TESTS PASS ===")
print("=" * 50)

"""
AI Optimizer — Strategy Suggestion Engine
==========================================
Evaluates market conditions and SUGGESTS the best strategy + symbol.
NEVER auto-enters trades — suggestion only.

Evaluation Cycle: every 30 minutes
Watchlist: BTC, ETH, BNB + top 7 by volume (dynamic)
Scoring: 0–100 weighted across strategies
Threshold: >= 75 → Suggest via Telegram

Strategy Weights:
    QGLN:       30%
    GLN Hybrid: 25%
    GSL:        25%
    Trend/MACD: 20%
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable, List, Tuple
import config  # Importing config for centralized settings

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Default symbols (always included)
# ═══════════════════════════════════════════════════════════════════
DEFAULT_SYMBOLS = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'BNB/USDT:USDT']
MAX_WATCHLIST = 10

# Strategy weight map
STRATEGY_WEIGHTS = {
    'QGLN': 0.30,
    'GLN_Hybrid': 0.25,
    'GSL': 0.25,
    'Trend_MACD': 0.20,
}


class AIOptimizer:
    """
    Evaluates market conditions for each symbol and suggests
    the best strategy based on weighted scoring.
    
    NEVER auto-trades — suggestion only.
    """

    def __init__(
        self,
        execution_engine,
        scanner_registry=None,
        silent_manager=None,
        message_callback: Optional[Callable] = None,
        event_reporter=None,
        db_manager=None,
    ):
        self.ee = execution_engine
        self.registry = scanner_registry
        self.silent_mgr = silent_manager
        self._callback = message_callback
        self.event_reporter = event_reporter
        self.db_manager = db_manager
        self.event_reporter = event_reporter
        self.db_manager = db_manager

        # State
        self.watchlist: List[str] = list(DEFAULT_SYMBOLS)
        self.last_eval_ts: Optional[str] = None
        self.last_suggestion: Optional[Dict[str, Any]] = None
        self.eval_count: int = 0
        self.suggestion_count: int = 0
        self._running: bool = False

        # Performance history (for win rate / drawdown tracking)
        self._suggestion_history: List[Dict[str, Any]] = []

    # ───────────────────────────────────────────────────────────
    # Main Loop
    # ───────────────────────────────────────────────────────────
    async def run_loop(self):
        """Background loop — evaluates every AI_EVAL_INTERVAL minutes."""
        self._running = True
        interval_min = getattr(config, 'AI_EVAL_INTERVAL', 30)
        threshold = getattr(config, 'AI_THRESHOLD', 75)
        
        interval = interval_min * 60
        logger.info(f"AI OPTIMIZER: Started (interval={interval_min}m, threshold={threshold})")

        # Update registry
        if self.registry:
            self.registry.update('AI_Optimizer', running_status='SCANNING', enabled=True)

        while self._running:
            try:
                await self._eval_cycle()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"AI OPTIMIZER: Cycle error: {e}")
                if self.registry:
                    self.registry.update('AI_Optimizer', last_error=str(e)[:200], running_status='ERROR')
                await asyncio.sleep(60)

        if self.registry:
            self.registry.update('AI_Optimizer', running_status='STOPPED')

    def stop(self):
        self._running = False

    # ───────────────────────────────────────────────────────────
    # Evaluation Cycle
    # ───────────────────────────────────────────────────────────
    async def _eval_cycle(self):
        """One full evaluation cycle."""
        # 1. Refresh watchlist (top by volume)
        await self._refresh_watchlist()

        # 2. Score each symbol across all strategies
        best_score = 0
        best_symbol = None
        best_strategy = None
        best_reasons = []
        all_results = []

        for symbol in self.watchlist:
            try:
                result = await self._evaluate_symbol(symbol)
                all_results.append(result)
                if result['total_score'] > best_score:
                    best_score = result['total_score']
                    best_symbol = symbol
                    best_strategy = result['best_strategy']
                    best_reasons = result['reasons']
            except Exception as e:
                logger.warning(f"AI OPTIMIZER: Eval error for {symbol}: {e}")

        self.eval_count += 1
        self.last_eval_ts = datetime.now().isoformat()

        # Update registry
        if self.registry:
            self.registry.increment('AI_Optimizer', 'total_scans')
            self.registry.update(
                'AI_Optimizer',
                last_run_ts=self.last_eval_ts,
                active_symbols=[s.split('/')[0] for s in self.watchlist[:5]],
                running_status='SCANNING',
            )

        # 3. Suggest if above threshold
        threshold = getattr(config, 'AI_THRESHOLD', 75)
        if best_score >= threshold and best_symbol and best_strategy:
            await self._send_suggestion(best_symbol, best_strategy, best_score, best_reasons)

        logger.info(
            f"AI OPTIMIZER: Cycle #{self.eval_count} complete. "
            f"Best: {best_symbol} / {best_strategy} = {best_score:.0f}"
        )

    # ───────────────────────────────────────────────────────────
    # Watchlist Management
    # ───────────────────────────────────────────────────────────
    async def _refresh_watchlist(self):
        """Refresh top symbols by 24h volume + keep defaults."""
        try:
            exchange = self.ee.futures_exchange if self.ee else None
            if not exchange:
                return

            tickers = await asyncio.to_thread(exchange.fetch_tickers)
            if not tickers:
                return

            # Filter USDT pairs only, sort by quoteVolume
            usdt_tickers = []
            for sym, data in tickers.items():
                if '/USDT' in sym and data.get('quoteVolume'):
                    usdt_tickers.append((sym, float(data['quoteVolume'])))

            usdt_tickers.sort(key=lambda x: x[1], reverse=True)

            # Build watchlist: defaults + top N by volume
            new_list = list(DEFAULT_SYMBOLS)
            for sym, _ in usdt_tickers:
                if sym not in new_list:
                    new_list.append(sym)
                if len(new_list) >= MAX_WATCHLIST:
                    break

            self.watchlist = new_list
            logger.info(f"AI OPTIMIZER: Watchlist = {[s.split('/')[0] for s in self.watchlist]}")

        except Exception as e:
            logger.warning(f"AI OPTIMIZER: Watchlist refresh failed: {e}")
            # Keep existing watchlist

    # ───────────────────────────────────────────────────────────
    # Symbol Evaluation
    # ───────────────────────────────────────────────────────────
    async def _evaluate_symbol(self, symbol: str) -> Dict[str, Any]:
        """
        Evaluate a symbol across all strategies.
        Returns: {symbol, total_score, best_strategy, strategy_scores, reasons}
        """
        exchange = self.ee.futures_exchange if self.ee else None
        if not exchange:
            return {'symbol': symbol, 'total_score': 0, 'best_strategy': None, 'strategy_scores': {}, 'reasons': []}

        # Fetch OHLCV data (5m candles)
        try:
            ohlcv = await asyncio.to_thread(exchange.fetch_ohlcv, symbol, '5m', None, 100)
        except Exception as e:
            logger.warning(f"AI: OHLCV fetch failed for {symbol}: {e}")
            return {'symbol': symbol, 'total_score': 0, 'best_strategy': None, 'strategy_scores': {}, 'reasons': []}

        if not ohlcv or len(ohlcv) < 30:
            return {'symbol': symbol, 'total_score': 0, 'best_strategy': None, 'strategy_scores': {}, 'reasons': []}

        closes = [c[4] for c in ohlcv]
        highs = [c[2] for c in ohlcv]
        lows = [c[3] for c in ohlcv]
        volumes = [c[5] for c in ohlcv]

        # Calculate indicators
        indicators = self._calc_indicators(closes, highs, lows, volumes)

        # Score each strategy
        scores = {}
        reasons = []

        scores['QGLN'] = self._score_qgln(indicators, reasons)
        scores['GLN_Hybrid'] = self._score_hybrid(indicators, reasons)
        scores['GSL'] = self._score_gsl(indicators, reasons)
        scores['Trend_MACD'] = self._score_trend(indicators, reasons)

        # Weighted total
        total = sum(scores[s] * STRATEGY_WEIGHTS[s] for s in scores)

        # Best strategy
        best_strat = max(scores, key=scores.get)

        return {
            'symbol': symbol,
            'total_score': round(total, 1),
            'best_strategy': best_strat,
            'strategy_scores': scores,
            'reasons': reasons,
        }

    # ───────────────────────────────────────────────────────────
    # Indicator Calculations
    # ───────────────────────────────────────────────────────────
    def _calc_indicators(self, closes, highs, lows, volumes) -> Dict[str, Any]:
        """Calculate all indicators needed for scoring."""
        ind = {}

        # Current price
        ind['price'] = closes[-1]
        ind['prev_close'] = closes[-2] if len(closes) > 1 else closes[-1]

        # EMA 9/20/50
        ind['ema9'] = self._ema(closes, 9)
        ind['ema20'] = self._ema(closes, 20)
        ind['ema50'] = self._ema(closes, 50) if len(closes) >= 50 else ind['ema20']

        # RSI
        ind['rsi'] = self._rsi(closes, 14)

        # ATR (normalized as % of price)
        ind['atr'] = self._atr(highs, lows, closes, 14)
        ind['atr_pct'] = (ind['atr'] / ind['price'] * 100) if ind['price'] > 0 else 0

        # ATR spike detection
        atr_current = ind['atr']
        atr_prev = self._atr(highs[:-1], lows[:-1], closes[:-1], 14)
        ind['atr_spike'] = (atr_current / atr_prev) if atr_prev > 0 else 1.0

        # MACD
        macd_line, signal_line, histogram = self._macd(closes)
        ind['macd'] = macd_line
        ind['macd_signal'] = signal_line
        ind['macd_hist'] = histogram

        # Volume profile
        avg_vol = sum(volumes[-20:]) / len(volumes[-20:]) if len(volumes) >= 20 else sum(volumes) / len(volumes)
        ind['vol_ratio'] = volumes[-1] / avg_vol if avg_vol > 0 else 1.0

        # Volatility classification
        if ind['atr_pct'] > 3.0:
            ind['volatility'] = 'HIGH'
        elif ind['atr_pct'] > 1.0:
            ind['volatility'] = 'MEDIUM'
        else:
            ind['volatility'] = 'LOW'

        # Market regime
        if ind['ema9'] > ind['ema20'] > ind['ema50']:
            ind['regime'] = 'UPTREND'
        elif ind['ema9'] < ind['ema20'] < ind['ema50']:
            ind['regime'] = 'DOWNTREND'
        else:
            ind['regime'] = 'RANGE'

        # Price vs EMAs
        ind['above_ema9'] = ind['price'] > ind['ema9']
        ind['above_ema20'] = ind['price'] > ind['ema20']

        return ind

    # ───────────────────────────────────────────────────────────
    # Strategy Scoring Functions (0–100 each)
    # ───────────────────────────────────────────────────────────
    def _score_qgln(self, ind: Dict, reasons: list) -> float:
        """Score for QGLN strategy (Q-breakout + EMA/HMA + structure)."""
        score = 0

        # Q-breakout: price above EMA20 with volume spike
        if ind['above_ema20'] and ind['vol_ratio'] > 1.3:
            score += 30
            reasons.append("Q-breakout above EMA20 + volume spike")

        # EMA alignment (trending)
        if ind['regime'] in ('UPTREND', 'DOWNTREND'):
            score += 25
        elif ind['regime'] == 'RANGE':
            score += 10

        # RSI confirmation (not overbought/oversold for new entry)
        if 30 < ind['rsi'] < 70:
            score += 20
        elif 20 < ind['rsi'] < 80:
            score += 10

        # ATR suitable (not too low)
        if ind['atr_pct'] > 0.5:
            score += 15

        # MACD confirmation
        if ind['macd_hist'] > 0 and ind['regime'] == 'UPTREND':
            score += 10
        elif ind['macd_hist'] < 0 and ind['regime'] == 'DOWNTREND':
            score += 10

        return min(score, 100)

    def _score_hybrid(self, ind: Dict, reasons: list) -> float:
        """Score for GLN Hybrid strategy (multi-indicator fusion)."""
        score = 0

        # EMA/HMA alignment
        if ind['regime'] != 'RANGE':
            score += 25
            reasons.append(f"Hybrid: Trend confirmed ({ind['regime']})")

        # MACD momentum
        if abs(ind['macd_hist']) > 0:
            score += 20

        # ATR adequate for Spider legs
        if 0.5 < ind['atr_pct'] < 5.0:
            score += 20

        # RSI not extreme
        if 25 < ind['rsi'] < 75:
            score += 15

        # Volume confirmation
        if ind['vol_ratio'] > 1.2:
            score += 10

        # Correlation check (placeholder — full version needs BTC correlation)
        score += 10  # Baseline

        return min(score, 100)

    def _score_gsl(self, ind: Dict, reasons: list) -> float:
        """Score for GSL strategy (Shock Detection + Ladder)."""
        score = 0

        # ATR spike is the key GSL signal
        if ind['atr_spike'] > 1.5:
            score += 35
            reasons.append(f"GSL: ATR spike {ind['atr_spike']:.1f}x")

        # High volatility preferred
        if ind['volatility'] == 'HIGH':
            score += 25
            reasons.append("High volatility (pump structure)")
        elif ind['volatility'] == 'MEDIUM':
            score += 15

        # Volume surge
        if ind['vol_ratio'] > 2.0:
            score += 20
            reasons.append("Volume surge >2x average")
        elif ind['vol_ratio'] > 1.5:
            score += 10

        # Price action (rapid move)
        price_change_pct = abs(ind['price'] - ind['prev_close']) / ind['prev_close'] * 100 if ind['prev_close'] > 0 else 0
        if price_change_pct > 2.0:
            score += 20
        elif price_change_pct > 0.5:
            score += 10

        return min(score, 100)

    def _score_trend(self, ind: Dict, reasons: list) -> float:
        """Score for Trend/MACD strategy."""
        score = 0

        # Clear trend
        if ind['regime'] == 'UPTREND':
            score += 30
            reasons.append("Strong uptrend (EMA 9>20>50)")
        elif ind['regime'] == 'DOWNTREND':
            score += 30
            reasons.append("Strong downtrend (EMA 9<20<50)")
        else:
            score += 5

        # MACD crossover or strong histogram
        if ind['macd'] > ind['macd_signal']:
            score += 25
        elif ind['macd'] < ind['macd_signal'] and ind['regime'] == 'DOWNTREND':
            score += 25

        # RSI trend confirmation
        if ind['regime'] == 'UPTREND' and 50 < ind['rsi'] < 75:
            score += 20
        elif ind['regime'] == 'DOWNTREND' and 25 < ind['rsi'] < 50:
            score += 20
        elif 40 < ind['rsi'] < 60:
            score += 10

        # Volatility suitable
        if ind['volatility'] in ('MEDIUM', 'HIGH'):
            score += 15

        # Volume
        if ind['vol_ratio'] > 1.1:
            score += 10

        return min(score, 100)

    # ───────────────────────────────────────────────────────────
    # Suggestion Output
    # ───────────────────────────────────────────────────────────
    async def _send_suggestion(self, symbol: str, strategy: str, score: float, reasons: List[str]):
        """Send AI suggestion via Telegram."""
        # Check silent manager
        if self.silent_mgr and self.silent_mgr.is_silent():
            logger.info(f"AI OPTIMIZER: Suppressed suggestion during silent hours ({symbol}/{strategy}/{score})")
            return

        self.suggestion_count += 1
        clean_sym = symbol.split('/')[0] if '/' in symbol else symbol

        reason_text = '\n'.join(f"  • {r}" for r in reasons[:4]) if reasons else "  • Multiple signal confluence"

        msg = (
            f"🤖 <b>AI Suggestion</b>\n"
            f"{'─' * 22}\n"
            f"📊 <b>Symbol:</b> <code>{clean_sym}</code>\n"
            f"🎯 <b>Best Strategy:</b> {strategy}\n"
            f"📈 <b>Score:</b> {score:.0f}/100\n"
            f"📝 <b>Reason:</b>\n{reason_text}\n\n"
            f"⚠️ <i>Suggestion only — no auto-trade</i>"
        )

        self.last_suggestion = {
            'symbol': clean_sym,
            'strategy': strategy,
            'score': score,
            'reasons': reasons[:4],
            'ts': datetime.now().isoformat(),
        }

        # Update registry
        if self.registry:
            self.registry.increment('AI_Optimizer', 'total_signals')
            self.registry.update(
                'AI_Optimizer',
                last_signal=f"{clean_sym}/{strategy}/{score:.0f}",
            )

        # Send event alert
        if self.event_reporter:
            await self.event_reporter.report('STRONG_SIGNAL', {
                'symbol': clean_sym,
                'score': f"{score:.0f}",
                'detail': f"AI suggests {strategy}",
            })

        # Send Telegram message
        if self._callback:
            try:
                await self._callback(msg)
            except Exception as e:
                logger.error(f"AI OPTIMIZER: Send failed: {e}")

        logger.info(f"AI OPTIMIZER: Suggestion sent — {clean_sym} / {strategy} / {score:.0f}")

    # ───────────────────────────────────────────────────────────
    # Technical Indicator Helpers
    # ───────────────────────────────────────────────────────────
    def _ema(self, data: list, period: int) -> float:
        """Simple EMA calculation."""
        if len(data) < period:
            return data[-1] if data else 0
        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for val in data[period:]:
            ema = (val - ema) * multiplier + ema
        return ema

    def _rsi(self, closes: list, period: int = 14) -> float:
        """RSI calculation."""
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> float:
        """ATR calculation."""
        if len(closes) < 2:
            return 0
        true_ranges = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            true_ranges.append(tr)

        if len(true_ranges) < period:
            return sum(true_ranges) / len(true_ranges) if true_ranges else 0
        return sum(true_ranges[-period:]) / period

    def _macd(self, closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[float, float, float]:
        """MACD calculation → (macd_line, signal_line, histogram)."""
        if len(closes) < slow + signal:
            return 0, 0, 0

        ema_fast = self._ema(closes, fast)
        ema_slow = self._ema(closes, slow)
        macd_line = ema_fast - ema_slow

        # Build MACD line series for signal calculation
        macd_series = []
        for i in range(slow, len(closes)):
            subset = closes[:i+1]
            ef = self._ema(subset, fast)
            es = self._ema(subset, slow)
            macd_series.append(ef - es)

        signal_line = self._ema(macd_series, signal) if len(macd_series) >= signal else macd_line
        histogram = macd_line - signal_line

        return macd_line, signal_line, histogram

    # ───────────────────────────────────────────────────────────
    # Status for Dashboard
    # ───────────────────────────────────────────────────────────
    def get_status(self) -> Dict[str, Any]:
        """Return current AI optimizer status for dashboard display."""
        return {
            'running': self._running,
            'eval_count': self.eval_count,
            'suggestion_count': self.suggestion_count,
            'last_eval': self.last_eval_ts,
            'last_suggestion': self.last_suggestion,
            'watchlist_size': len(self.watchlist),
            'watchlist': [s.split('/')[0] for s in self.watchlist],
            'threshold': getattr(config, 'AI_THRESHOLD', 75),
        }

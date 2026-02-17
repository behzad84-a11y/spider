import asyncio
import logging

logger = logging.getLogger(__name__)

# Helper to run sync CCXT calls in thread pool
async def async_run(func, *args, **kwargs):
    """Run a sync function in a thread pool to make it async-compatible."""
    return await asyncio.to_thread(func, *args, **kwargs)

class MarketAnalyzer:
    def __init__(self, exchange, symbol):
        self.exchange = exchange
        self.symbol = symbol

    def calculate_ema(self, prices, period):
        if len(prices) < period:
            return None
        ema = sum(prices[:period]) / period
        multiplier = 2 / (period + 1)
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def calculate_sma(self, data, period):
        if len(data) < period:
            return None
        return sum(data[-period:]) / period

    def calculate_linear_regression(self, prices):
        """
        Simple Linear Regression to predict next value.
        Returns: (next_value, r_squared)
        """
        n = len(prices)
        if n < 2:
            return None, 0

        x = list(range(n))
        y = prices

        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(x[i] * y[i] for i in range(n))
        sum_xx = sum(x[i] ** 2 for i in range(n))

        # Calculate slope (m) and intercept (b)
        denominator = (n * sum_xx - sum_x * sum_x)
        if denominator == 0:
            return prices[-1], 0

        m = (n * sum_xy - sum_x * sum_y) / denominator
        b = (sum_y - m * sum_x) / n

        # Calculate R-squared (Coefficient of Determination)
        mean_y = sum_y / n
        ss_tot = sum((yi - mean_y) ** 2 for yi in y)

        y_pred = [m * xi + b for xi in x]
        ss_res = sum((y[i] - y_pred[i]) ** 2 for i in range(n))

        if ss_tot == 0:
            r_squared = 0
        else:
            r_squared = 1 - (ss_res / ss_tot)

        next_value = m * n + b
        return next_value, r_squared

    def calculate_rsi(self, prices, period=14):
        if len(prices) < period + 1:
            return None

        gains = []
        losses = []

        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(prices)-1):
            change = prices[i+1] - prices[i]
            gain = change if change > 0 else 0
            loss = abs(change) if change < 0 else 0

            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period

        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def calculate_atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None

        tr_list = []
        for i in range(1, len(closes)):
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i-1])
            lc = abs(lows[i] - closes[i-1])
            tr = max(hl, hc, lc)
            tr_list.append(tr)

        if not tr_list:
            return 0

        atr = sum(tr_list[:period]) / period

        for i in range(period, len(tr_list)):
            atr = (atr * (period - 1) + tr_list[i]) / period

        return atr

    def calculate_hma(self, prices, period):
        """Hull Moving Average calculation."""
        import math
        if len(prices) < period:
            return None
        
        half_period = int(period / 2)
        sqrt_period = int(math.sqrt(period))
        
        def wma(data, p):
            if len(data) < p: return None
            weights = list(range(1, p + 1))
            total_weight = sum(weights)
            return sum(d * w for d, w in zip(data[-p:], weights)) / total_weight

        # HMA = WMA(2*WMA(n/2) - WMA(n), sqrt(n))
        wma_half = []
        wma_full = []
        for i in range(len(prices)):
            data = prices[:i+1]
            h = wma(data, half_period)
            f = wma(data, period)
            wma_half.append(h)
            wma_full.append(f)
            
        raw_hma = []
        for h, f in zip(wma_half, wma_full):
            if h is not None and f is not None:
                raw_hma.append(2 * h - f)
            else:
                raw_hma.append(None)
                
        # Final WMA on raw_hma
        hma_clean = [x for x in raw_hma if x is not None]
        if len(hma_clean) < sqrt_period:
            return None
        return wma(hma_clean, sqrt_period)

    def calculate_macd(self, prices, fast=12, slow=26, signal=9):
        """MACD calculation."""
        if len(prices) < slow + signal:
            return None, None, None
            
        def get_ema_list(data, p):
            ema = []
            if not data: return []
            current_ema = sum(data[:p]) / p
            for i in range(p): ema.append(None)
            ema[p-1] = current_ema
            multiplier = 2 / (p + 1)
            for val in data[p:]:
                current_ema = (val - current_ema) * multiplier + current_ema
                ema.append(current_ema)
            return ema

        ema_fast = get_ema_list(prices, fast)
        ema_slow = get_ema_list(prices, slow)
        
        macd_line = []
        for f, s in zip(ema_fast, ema_slow):
            if f is not None and s is not None:
                macd_line.append(f - s)
            else:
                macd_line.append(None)
                
        # Signal Line (EMA of MACD Line)
        macd_clean = [x for x in macd_line if x is not None]
        if len(macd_clean) < signal:
            return None, None, None
            
        signal_line_list = get_ema_list(macd_clean, signal)
        signal_line = signal_line_list[-1]
        current_macd = macd_line[-1]
        histogram = current_macd - signal_line if current_macd is not None and signal_line is not None else 0
        
        return current_macd, signal_line, histogram

    async def analyze(self):
        try:
            # 1. Macro Trend (4H)
            ohlcv_4h = await async_run(self.exchange.fetch_ohlcv, self.symbol, timeframe='4h', limit=200)
            if not ohlcv_4h:
                return 'UNKNOWN', {'reason': 'NO_OHLCV_4H'}

            closes_4h = [x[4] for x in ohlcv_4h]
            current_price = closes_4h[-1]

            ema50_4h = self.calculate_ema(closes_4h, 50)
            ema200_4h = self.calculate_ema(closes_4h, 200)

            # 2. Micro Entry (15M)
            ohlcv_15m = await async_run(self.exchange.fetch_ohlcv, self.symbol, timeframe='15m', limit=100)
            if not ohlcv_15m:
                return 'UNKNOWN', {'reason': 'NO_OHLCV_15M', 'price': current_price}

            closes_15m = [x[4] for x in ohlcv_15m]
            volumes_15m = [x[5] for x in ohlcv_15m]
            rsi_15m = self.calculate_rsi(closes_15m)

            # Volume Confirmation
            volume_sma_20 = self.calculate_sma(volumes_15m, 20)
            current_volume = volumes_15m[-1]
            volume_confirmed = current_volume > volume_sma_20 if volume_sma_20 else False

            if not ema50_4h or not ema200_4h or rsi_15m is None:
                return 'UNKNOWN', {
                    'reason': 'INDICATOR_MISSING',
                    'ema50_4h': ema50_4h,
                    'ema200_4h': ema200_4h,
                    'rsi_15m': rsi_15m,
                    'price': current_price,
                }

            # Logic for Trend
            state = 'RANGE'
            if current_price > ema50_4h and ema50_4h > ema200_4h:
                state = 'UPTREND'
            elif current_price < ema50_4h and ema50_4h < ema200_4h:
                state = 'DOWNTREND'

            # AI Prediction (Linear Regression on recent Close prices)
            recent_closes = closes_15m[-30:]
            next_close_prediction, r_squared = self.calculate_linear_regression(recent_closes)

            highs_4h = [x[2] for x in ohlcv_4h]
            lows_4h = [x[3] for x in ohlcv_4h]
            atr_4h = self.calculate_atr(highs_4h, lows_4h, closes_4h)

            return state, {
                'ema50_4h': ema50_4h,
                'ema200_4h': ema200_4h,
                'rsi_15m': rsi_15m,
                'atr_4h': atr_4h,
                'price': current_price,
                'ai_prediction': next_close_prediction,
                'ai_confidence': r_squared,
                'volume_confirmed': volume_confirmed
            }

        except Exception as e:
            logger.error(f"Analysis error: {e}")
            return 'ERROR', {'reason': str(e)}

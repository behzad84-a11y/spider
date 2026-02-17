import asyncio
import logging
from datetime import datetime
from risk_engine import TradeRequest
from execution_engine import ExecutionEngine, ExecutionResult
from market_analyzer import MarketAnalyzer

logger = logging.getLogger(__name__)

class GSLStrategy:
    """
    GLN Spider Ladder (GSL) Strategy.
    Integrates Shock Detection + Ladder logic (EMA pullbacks).
    """
    def __init__(self, execution_engine: ExecutionEngine, symbol, initial_investment, market_type='future', leverage=1, db_manager=None, strategy_id=None, message_callback=None, position_tracker=None):
        self.execution_engine = execution_engine
        self.symbol = symbol
        self.initial_investment = initial_investment
        self.market_type = market_type
        self.leverage = leverage
        self.db_manager = db_manager
        self.strategy_id = strategy_id or f"gsl_{symbol.replace('/', '').lower()}"
        self.message_callback = message_callback
        self.tracker = position_tracker
        self.analyzer = MarketAnalyzer(None, symbol) # Engine-agnostic analyzer
        
        # State
        self.positions = []
        self.is_running = True
        self.last_atr = 0
        self.ema9 = 0
        self.ema20 = 0
        self.base_leg_size = 10.0 # USDT
        self.current_sl = 0
        self.side = None # 'buy' or 'sell'
        
    async def initialize(self):
        logger.info(f"Initializing GSL Strategy for {self.symbol}")
        # Load safe defaults or DB settings
        if self.db_manager:
            self.base_leg_size = float(self.db_manager.get_setting('gsl_base_leg_size', 10.0))
            
    async def detect_shock(self):
        """
        Detects price shock via ATR spike + range expansion.
        """
        try:
            ohlcv = await self.execution_engine.fetch_market_data(self.symbol, 'ohlcv', '5m', limit=50)
            if not ohlcv: return False
            
            closes = [x[4] for x in ohlcv]
            highs = [x[2] for x in ohlcv]
            lows = [x[3] for x in ohlcv]
            
            # ATR Spike
            current_atr = self.analyzer.calculate_atr(highs, lows, closes, 14)
            prev_atr = self.analyzer.calculate_atr(highs[:-1], lows[:-1], closes[:-1], 14)
            
            if current_atr and prev_atr and current_atr > prev_atr * 1.5:
                 # Shock Detected
                 logger.info(f"⚡ SHOCK DETECTED for {self.symbol}: ATR Spike {prev_atr:.2f} -> {current_atr:.2f}")
                 return True
            return False
        except Exception as e:
            logger.error(f"GSL Shock Detection Error: {e}")
            return False

    async def check_market(self):
        """Main loop for GSL detection and management."""
        if not self.is_running: return
        
        try:
            ticker = await self.execution_engine.fetch_market_data(self.symbol, 'ticker')
            cp = float(ticker['last']) if ticker else 0
            if cp == 0: return

            if not self.positions:
                # 1. Detection Phase
                if await self.detect_shock():
                    # 2. Continuation Filter (Simplification: 2 candles hold logic placeholder)
                     # For now, if shock detected, we wait for a signal or enter if trend confirmed
                     # Implement core ladder logic here
                     pass
            else:
                # 3. Position Management (Ladder)
                # Exit on EMA cross logic
                pass
                
        except Exception as e:
            logger.error(f"GSL Loop Error: {e}")

    async def add_ladder_leg(self, side, price):
        """Adds a leg on EMA 9/20 pullback."""
        # Implement ladder add logic
        pass

    def save_state(self):
        if self.db_manager:
            state = {
                'positions': self.positions,
                'side': self.side,
                'current_sl': self.current_sl
            }
            self.db_manager.save_strategy(self.strategy_id, self.symbol, self.market_type, self.side, self.initial_investment, self.leverage, state)

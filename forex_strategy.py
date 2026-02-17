import asyncio
import logging
from risk_engine import TradeRequest
from execution_engine import ExecutionEngine, ExecutionResult

logger = logging.getLogger(__name__)

class ForexStrategy:
    """
    Strategy for Forex/Stocks using MetaTrader 5 via ExecutionEngine.
    """
    def __init__(self, execution_engine: ExecutionEngine, symbol, amount, side, leverage, db_manager, strategy_id=None):
        self.execution_engine = execution_engine
        self.symbol = symbol
        self.amount = amount # Lots
        self.side = side # 'buy' or 'sell'
        self.leverage = leverage
        self.db_manager = db_manager
        self.strategy_id = strategy_id
        self.running = True
        
    async def initialize(self):
        """Prepare strategy."""
        logger.info(f"Forex Strategy Initialized for {self.symbol}")
        return True

    async def execute_trade(self):
        """Places the initial trade on MT5 via ExecutionEngine."""
        if not self.running: return

        # 1. Prepare Request
        # Note: In Forex, 'amount' is usually Lots. 
        # ExecutionEngine.execute handles MT5 initialization and symbols.
        req = TradeRequest(
            symbol=self.symbol,
            amount=self.amount,
            leverage=self.leverage,
            side=self.side,
            market_type='forex',
            user_id=0
        )
        
        # 2. Execute via Engine
        res = await self.execution_engine.execute(req)
        
        if not res.success:
            logger.error(f"Forex execution failed: {res.message}")
            return f"Error: {res.message}"
            
        logger.info(f"Forex Order Sent! Ticket: {res.order_id}")
        return f"SUCCESS: Ticket {res.order_id}"

    async def get_status(self):
        return f"Forex Strategy {self.symbol}: Running"

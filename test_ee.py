import asyncio
import ccxt
import logging
from execution_engine import ExecutionEngine, TradeRequest
from risk_engine import RiskEngine
from database_manager import DatabaseManager

logging.basicConfig(level=logging.INFO)

async def test_ee():
    db = DatabaseManager()
    risk = RiskEngine(db)
    
    # Mock exchanges (use sandbox if possible or just test logic)
    spot = ccxt.coinex()
    future = ccxt.coinex()
    
    ee = ExecutionEngine(spot, future, risk)
    
    req = TradeRequest(
        symbol="BTC/USDT",
        amount=10,
        side="buy",
        market_type="spot",
        leverage=1,
        user_id=123
    )
    
    print("Testing Execution...")
    # This will fail since we don't have real keys, but we want to see it reach the exchange call after risk check
    res = await ee.execute(req)
    print(f"Result: {res}")

if __name__ == "__main__":
    asyncio.run(test_ee())

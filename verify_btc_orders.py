import ccxt
import sqlite3
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def verify_btc_orders():
    # Keys found in spider_trading_bot.py
    api_key = 'C739AFE1A401410EAA03D28D4ADE1BD5'
    api_secret = '8E5B70913E3B2526A9896969E3483635E5FB95F60373E2EC'

    exchange_class = getattr(ccxt, 'coinex')
    exchange = exchange_class({
        'apiKey': api_key,
        'secret': api_secret,
        'options': {'defaultType': 'swap'},
    })

    symbol = 'BTC/USDT:USDT'

    try:
        # 1. Fetch Balance
        logger.info("Fetching Balance...")
        balance = exchange.fetch_balance()
        usdt_bal = balance.get('USDT', {})
        print("\n" + "="*50)
        print("BALANCE")
        print(f"Total: {usdt_bal.get('total', 0)}")
        print(f"Free: {usdt_bal.get('free', 0)}")
        print(f"Used: {usdt_bal.get('used', 0)}")
        print("="*50)

        # 2. Fetch Open Orders
        logger.info(f"Fetching open orders for {symbol}...")
        orders = exchange.fetch_open_orders(symbol)
        print(f"Open Orders Count: {len(orders)}")
        for o in orders:
            print(f"Order: {o['id']} {o['type']} {o['side']} {o['status']}")

        # 3. Fetch Positions
        logger.info(f"Fetching positions for {symbol}...")
        # Note: calling without params to get ALL positions if possible, or list
        try:
            positions = exchange.fetch_positions() # Try all
        except:
            positions = exchange.fetch_positions([symbol]) # Fallback

        print("\n" + "="*50)
        print(f"ALL POSITIONS (Count: {len(positions)})")
        print("="*50)
        
        found = False
        for p in positions:
            # Print non-zero positions only or match symbol
            if float(p['contracts']) > 0 or p['symbol'] == symbol:
                print(f"Symbol: {p['symbol']}")
                print(f"Side: {p['side']}")
                print(f"Contracts: {p['contracts']}")
                print(f"Entry: {p['entryPrice']}")
                print(f"Leverage: {p['leverage']}")
                print("-" * 20)
                found = True
        
        if not found:
            print("No active positions found in list.")
            
            
        print("="*50 + "\n")

        # 4. Fetch Trade History
        logger.info(f"Fetching recent trades for {symbol}...")
        trades = exchange.fetch_my_trades(symbol, limit=5)
        print("\n" + "="*50)
        print(f"RECENT TRADES (Last 5)")
        print("="*50)
        for t in trades:
            print(f"Time: {t['datetime']}")
            print(f"Side: {t['side']}")
            print(f"Amount: {t['amount']}")
            print(f"Price: {t['price']}")
            print(f"Cost: {t['cost']}")
            print(f"Fee: {t['fee']}")
            print("-" * 20)
        print("="*50 + "\n")

    except Exception as e:
        logger.error(f"Error checking exchange: {e}")

if __name__ == "__main__":
    verify_btc_orders()

import ccxt
import logging
import json

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def debug_stop_orders():
    # Keys from spider_trading_bot.py
    api_key = 'C739AFE1A401410EAA03D28D4ADE1BD5'
    api_secret = '8E5B70913E3B2526A9896969E3483635E5FB95F60373E2EC'

    exchange = ccxt.coinex({
        'apiKey': api_key,
        'secret': api_secret,
        'options': {'defaultType': 'swap'}
    })

    print("="*60)
    print("DEBUGGING STOP/CONDITIONAL ORDERS")
    print("="*60)

    symbol = 'BTC/USDT:USDT'
    
    # Method 1: Standard fetch_open_orders
    print("\n[Method 1] Standard fetch_open_orders")
    try:
        orders = exchange.fetch_open_orders(symbol)
        print(f"Count: {len(orders)}")
        for o in orders:
            print(json.dumps(o, indent=2))
    except Exception as e:
        print(f"Error: {e}")

    # Method 2: fetch_open_orders with 'type': 'stop' param
    print("\n[Method 2] fetch_open_orders(params={'type': 'stop'})")
    try:
        # Some exchanges use 'type': 'stop', others separate
        orders = exchange.fetch_open_orders(symbol, params={'type': 'stop'})
        print(f"Count: {len(orders)}")
        for o in orders:
            print(f"ID: {o['id']} Type: {o['type']} StopPrice: {o.get('stopPrice')} Info: {o['info']}")
    except Exception as e:
        print(f"Error: {e}")

    # Method 3: fetch_open_orders with 'stop': True param (CCXT unified)
    print("\n[Method 3] fetch_open_orders(params={'stop': True})")
    try:
        orders = exchange.fetch_open_orders(symbol, params={'stop': True})
        print(f"Count: {len(orders)}")
        for o in orders:
            print(f"ID: {o['id']} Type: {o['type']} StopPrice: {o.get('stopPrice')}")
    except Exception as e:
        print(f"Error: {e}")

    # Method 4: Specific Coinex API endpoint for Plan Orders (if CCXT supports implicit map or raw)
    # Coinex V2 API: /futures/pending-stop-orders
    print("\n[Method 4] Raw API call: query_stop_orders (Plan Orders)")
    try:
        # Attempting to use implicit API method if available or fetch_orders with specific params
        # In CoinEx, "Stop Orders" are often separate.
        # Let's try to list 'stop' orders specifically via raw params common in Coinex V2
        
        # Checking implicit methods
        # exchange.publicGetFuturesStopOrders? No, authenticated.
        # exchange.privateGetFuturesStopOrders? 
        
        # Let's try passing 'status': 'not_triggered' if supported
        orders = exchange.fetch_open_orders(symbol, params={'status': 'not_triggered'}) 
        # This is a guess, but sometimes works for stop orders
        print(f"Count (not_triggered): {len(orders)}")
        for o in orders:
             print(f"ID: {o['id']} Status: {o['status']} Type: {o['type']}")
             
    except Exception as e:
        print(f"Error (Method 4): {e}")

    # Method 5: Fetch ALL orders (open + closed) to see if it was recently cancelled/triggered
    print("\n[Method 5] fetch_orders (Last 10)")
    try:
        orders = exchange.fetch_orders(symbol, limit=10)
        print(f"Count: {len(orders)}")
        for o in orders:
            # Check for anything that looks like a stop order
            is_stop = o['type'] == 'stop' or o.get('stopPrice') is not None or o['info'].get('stop_price') is not None
            if is_stop:
                print(f"FOUND STOP ORDER IN HISTORY: ID: {o['id']} Status: {o['status']} Stop: {o.get('stopPrice')}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    debug_stop_orders()

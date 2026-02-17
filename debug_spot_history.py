import ccxt
import logging
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def debug_spot_history():
    # Keys from spider_trading_bot.py
    api_key = 'C739AFE1A401410EAA03D28D4ADE1BD5'
    api_secret = '8E5B70913E3B2526A9896969E3483635E5FB95F60373E2EC'

    exchange = ccxt.coinex({
        'apiKey': api_key,
        'secret': api_secret,
        'options': {'defaultType': 'spot'} # FORCE SPOT
    })

    print("="*60)
    print("DEBUGGING SPOT TRADE HISTORY (Last 7 Days)")
    print("="*60)

    try:
        since = int((datetime.now() - timedelta(days=7)).timestamp() * 1000)
        # Spot symbols usually don't have :USDT suffix in CCXT for Coinex, just BTC/USDT
        symbols = ['BTC/USDT', 'SOL/USDT', 'ETH/USDT', 'XRP/USDT', 'USDT/USD'] 
        
        all_trades = []
        for sym in symbols:
            try:
                print(f"Checking {sym}...")
                trades = exchange.fetch_my_trades(sym, since=since, limit=20)
                for t in trades:
                    t['symbol'] = sym
                    all_trades.append(t)
            except Exception as e:
                print(f"  No trades or error for {sym}: {e}")

        print("\nSUMMARY OF SPOT TRADES:")
        if not all_trades:
            print("  NO SPOT TRADES FOUND.")
        else:
            for t in all_trades:
                dt = datetime.fromtimestamp(t['timestamp']/1000).strftime('%Y-%m-%d %H:%M')
                print(f"  {dt} {t['symbol']} {t['side']} {t['amount']} @ {t['price']}")

    except Exception as e:
        print(f"Error fetching spot history: {e}")

if __name__ == "__main__":
    debug_spot_history()

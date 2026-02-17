import ccxt
import logging
import json

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def debug_positions_deep():
    # Keys found in spider_trading_bot.py
    api_key = 'C739AFE1A401410EAA03D28D4ADE1BD5'
    api_secret = '8E5B70913E3B2526A9896969E3483635E5FB95F60373E2EC'

    print("="*50)
    print("DEEP DEBUG: CHECKING ALL MARKETS & ACCOUNTS")
    print("="*50)

    # 1. SPOT Check
    try:
        print("\n--- SPOT ACCOUNT ---")
        exchange_spot = ccxt.coinex({
            'apiKey': api_key,
            'secret': api_secret,
            'options': {'defaultType': 'spot'}
        })
        balance = exchange_spot.fetch_balance()
        print("Non-zero Balances:")
        found_spot = False
        for curr, val in balance['total'].items():
            if val > 0:
                print(f"  {curr}: {val} (Free: {balance[curr]['free']}, Used: {balance[curr]['used']})")
                found_spot = True
        if not found_spot:
            print("  (No funds found)")
            
    except Exception as e:
        print(f"Error checking SPOT: {e}")

    # 2. LINEAR FUTURES (USDT-M)
    try:
        print("\n--- LINEAR FUTURES (USDT-M) ---")
        exchange_swap = ccxt.coinex({
            'apiKey': api_key,
            'secret': api_secret,
            'options': {'defaultType': 'swap'} # Usually Linear for Coinex
        })
        
        # Balance
        bal = exchange_swap.fetch_balance()
        print(f"USDT Balance: {bal.get('USDT', {}).get('total', 0)}")
        
        # Positions
        positions = exchange_swap.fetch_positions()
        print(f"Positions Found: {len(positions)}")
        for p in positions:
            if float(p['contracts']) != 0:
                print(f"  {p['symbol']} {p['side']} x{p['leverage']} - Size: {p['contracts']} - PnL: {p['unrealizedPnl']}")

        # Verify specifically for common pairs just in case
        # btc_pos_list = exchange_swap.fetch_positions(['BTC/USDT:USDT'])
        # print(f"Specific BTC Check: {len(btc_pos_list)}")

    except Exception as e:
        print(f"Error checking LINEAR FUTURES: {e}")

    # 3. INVERSE FUTURES (Coin-M)
    try:
        print("\n--- INVERSE FUTURES (Coin-M) ---")
        # Coinex might treat them as same 'swap' type but different symbols, or need different option.
        # CCXT mapped Coinex 'swap' to linear usually. 'future' might be delivery?
        # Let's try to fetch balance for BTC to see if there is margin.
        
        # Re-using swap exchange but checking BTC balance which would be margin for inverse
        bal = exchange_swap.fetch_balance()
        print(f"BTC Balance (Margin?): {bal.get('BTC', {}).get('total', 0)}")
        
    except Exception as e:
        print(f"Error checking INVERSE FUTURES: {e}")

if __name__ == "__main__":
    debug_positions_deep()

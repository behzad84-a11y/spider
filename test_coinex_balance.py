import ccxt
import os

def load_env(path='.env'):
    if not os.path.exists(path): return
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            key, value = line.split('=', 1)
            os.environ[key.strip()] = value.strip().strip("'").strip('"')

load_env()

api_key = os.getenv('COINEX_API_KEY')
secret = os.getenv('COINEX_SECRET')

def test_balance():
    print("Connecting to CoinEx...")
    # Spot
    spot = ccxt.coinex({
        'apiKey': api_key,
        'secret': secret,
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })
    
    # Futures
    futures = ccxt.coinex({
        'apiKey': api_key,
        'secret': secret,
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'}
    })

    print("\n--- SPOT BALANCE ---")
    try:
        spot_bal = spot.fetch_balance()
        # Filter non-zero items
        for curr, val in spot_bal['total'].items():
            if val > 0:
                print(f"{curr}: {val}")
    except Exception as e:
        print(f"Spot Error: {e}")

    print("\n--- FUTURES BALANCE ---")
    try:
        fut_bal = futures.fetch_balance()
        # For CoinEx, futures fetch_balance returns 'USDT' total which is usually equity.
        if 'USDT' in fut_bal['total']:
            print(f"USDT Total: {fut_bal['total']['USDT']}")
        else:
             print("USDT not found in total balance keys:", list(fut_bal['total'].keys()))
    except Exception as e:
        print(f"Futures Error: {e}")

if __name__ == "__main__":
    test_balance()

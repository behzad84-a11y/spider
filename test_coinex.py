import ccxt

COINEX_API_KEY = 'C739AFE1A401410EAA03D28D4ADE1BD5'
COINEX_SECRET = '8E5B70913E3B2526A9896969E3483635E5FB95F60373E2EC'

e = ccxt.coinex({
    'apiKey': COINEX_API_KEY,
    'secret': COINEX_SECRET,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap',
    }
})

try:
    print("Loading markets...")
    e.load_markets()
    print(f"Markets loaded: {len(e.markets)} pairs")
    
    print("\nFetching positions...")
    positions = e.fetch_positions()
    print(f"Found {len(positions)} positions")
    for p in positions:
        if float(p.get('contracts', 0)) != 0:
            print(f"  - {p['symbol']}: {p['contracts']} contracts, PnL: {p.get('unrealizedPnl', 'N/A')}")
            
    print("\nFetching ticker...")
    ticker = e.fetch_ticker('BTC/USDT:USDT')
    print(f"BTC price: {ticker['last']}")
except Exception as ex:
    print(f"Error: {ex}")

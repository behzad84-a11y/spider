import MetaTrader5 as mt5

if not mt5.initialize():
    print("initialize() failed")
    quit()

# Search for Crypto symbols
search_terms = ['BTC', 'ETH', 'LTC', 'XRP', 'CRYPTO']
print("\nSearching for Crypto Symbols:")
symbols = mt5.symbols_get()
count = 0
for s in symbols:
    if any(term in s.name.upper() for term in search_terms):
        print(f"Name: {s.name}, Path: {s.path}, Visible: {s.visible}")
        count += 1
        if count > 20: break

if count == 0:
    print("No Crypto symbols found.")

mt5.shutdown()

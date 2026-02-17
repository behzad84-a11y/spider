import MetaTrader5 as mt5

if not mt5.initialize():
    print("initialize() failed")
    mt5.shutdown()
    quit()

# Get all symbols
symbols = mt5.symbols_get()
print(f"Total symbols: {len(symbols)}")

# Search for XAU and EUR
search_terms = ['XAU', 'EUR', 'USD']
print("\nMatching Symbols:")
count = 0
for s in symbols:
    if any(term in s.name for term in search_terms):
        print(f"Name: {s.name}, Path: {s.path}, Visible: {s.visible}")
        count += 1
        if count > 20: break

mt5.shutdown()

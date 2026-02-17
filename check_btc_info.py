import MetaTrader5 as mt5

if not mt5.initialize():
    print("initialize() failed")
    quit()

symbol = "BTCUSD.ecn"
info = mt5.symbol_info(symbol)

if info:
    print(f"Symbol: {info.name}")
    print(f"Point: {info.point}")
    print(f"Digits: {info.digits}")
    print(f"Spread: {info.spread}")
    print(f"Stops Level: {info.trade_stops_level}")
    print(f"Min Volume: {info.volume_min}")
    print(f"Ask: {mt5.symbol_info_tick(symbol).ask}")
    print(f"Bid: {mt5.symbol_info_tick(symbol).bid}")
else:
    print(f"{symbol} not found")

mt5.shutdown()

import MetaTrader5 as mt5

if not mt5.initialize():
    print("initialize() failed")
    quit()

term_info = mt5.terminal_info()
print(f"Algo Trading Enabled: {term_info.trade_allowed}")
print(f"Connected: {term_info.connected}")
print(f"Profit Mode: {mt5.account_info().margin_mode}")

mt5.shutdown()

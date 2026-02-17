import MetaTrader5 as mt5
from datetime import datetime

if not mt5.initialize():
    print("initialize() failed")
    quit()

symbol = "BTCUSD.ecn"
# Check if symbol exists with suffix, if not try plain
if not mt5.symbol_info(symbol):
    symbol = "BTCUSD"

info = mt5.symbol_info(symbol)
if not info:
    print(f"{symbol} not found")
    quit()

print(f"Symbol: {info.name}")
print(f"Trade Mode: {info.trade_mode} (0=Disabled, 4=Full)")
print(f"Swap Rollover3Days: {info.swap_rollover3days}")

# Check sessions for today (Sunday = 6)
# sessions_get returns tuple of (session_start, session_end)
sessions = mt5.symbol_info_session_quote(symbol, datetime.now(), mt5.DAY_OF_WEEK_SUNDAY)
print(f"Quote Sessions for Sunday: {sessions}")

trade_sessions = mt5.symbol_info_session_trade(symbol, datetime.now(), mt5.DAY_OF_WEEK_SUNDAY)
print(f"Trade Sessions for Sunday: {trade_sessions}")

mt5.shutdown()

import MetaTrader5 as mt5
import time

print("MetaTrader5 package author: ", mt5.__author__)
print("MetaTrader5 package version: ", mt5.__version__)

# establish connection to MetaTrader 5 terminal
if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

# display data on MetaTrader 5 version
print(mt5.version())

# display data on connection status, server name and trading account
print(mt5.terminal_info())
print("\nAccount Info:")
account_info = mt5.account_info()
if account_info!=None:
    print(account_info)
    print(f"\nLogin: {account_info.login}")
    print(f"Balance: {account_info.balance} {account_info.currency}")
    print(f"Leverage: 1:{account_info.leverage}")
else:
    print("failed to get account info")

# shut down connection to the MetaTrader 5 terminal
mt5.shutdown()
quit()

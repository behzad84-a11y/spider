
try:
    from telegram import Update
    print("telegram imported successfully")
except ImportError as e:
    print(f"Failed to import telegram: {e}")

try:
    from telegram.ext import ConversationHandler, MessageHandler, filters
    print("telegram.ext imported successfully")
except ImportError as e:
    print(f"Failed to import telegram.ext: {e}")

try:
    import ccxt.async_support as ccxt
    print("ccxt imported successfully")
except ImportError as e:
    print(f"Failed to import ccxt: {e}")

try:
    import pandas as pd
    print("pandas imported successfully")
except ImportError as e:
    print(f"Failed to import pandas: {e}")

try:
    import MetaTrader5 as mt5
    print("MetaTrader5 imported successfully")
except ImportError as e:
    print(f"Failed to import MetaTrader5: {e}")

try:
    import pytz
    print("pytz imported successfully")
except ImportError as e:
    print(f"Failed to import pytz: {e}")

import ccxt
import asyncio
from functools import partial

async def async_run(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))

COINEX_API_KEY = 'C739AFE1A401410EAA03D28D4ADE1BD5'
COINEX_SECRET = '8E5B70913E3B2526A9896969E3483635E5FB95F60373E2EC'

async def test_sl():
    exchange = ccxt.coinex({
        'apiKey': COINEX_API_KEY,
        'secret': COINEX_SECRET,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'swap',
        }
    })
    
    symbol = 'BTC/USDT:USDT'
    try:
        print("Loading markets...")
        await async_run(exchange.load_markets)
        
        ticker = await async_run(exchange.fetch_ticker, symbol)
        last_price = ticker['last']
        print(f"Current price: {last_price}")
        
        # SL price (for Short, so SL is Buy above last)
        stop_price = last_price + 500
        amount = 0.0001
        
        print(f"\n--- Testing Buy Stop (SL for Short) @ {stop_price} ---")
        
        # Method 9: CoinEx V2 Native Params
        # Note: CoinEx V2 uses 'stop_price' and 'stop_price_type'
        try:
            params = {
                'stop_price': exchange.price_to_precision(symbol, stop_price),
                'stop_type': 1, # Latest Price
                'reduceOnly': True
            }
            print(f"Trying Method 9 (stop_price + stop_type): {params}")
            # Use 'market' order with these params
            order = await async_run(
                exchange.create_order,
                symbol,
                'market',
                'buy',
                amount,
                None,
                params
            )
            print(f"Success Method 9: {order['id']}")
            return
        except Exception as e:
            print(f"Failed Method 9: {e}")

        # Method 10: V2 Native with different keys
        try:
            params = {
                'trigger_price': exchange.price_to_precision(symbol, stop_price),
                'trigger_price_type': 1,
                'reduce_only': True
            }
            print(f"Trying Method 10 (trigger_price + trigger_price_type): {params}")
            order = await async_run(
                exchange.create_order,
                symbol,
                'market',
                'buy',
                amount,
                None,
                params
            )
            print(f"Success Method 10: {order['id']}")
            return
        except Exception as e:
            print(f"Failed Method 10: {e}")

    except Exception as e:
        print(f"Global Error: {e}")
    finally:
        pass

if __name__ == "__main__":
    asyncio.run(test_sl())

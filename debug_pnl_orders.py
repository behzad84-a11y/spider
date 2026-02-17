import ccxt
import logging
import time
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def debug_pnl_orders():
    # Keys from spider_trading_bot.py
    api_key = 'C739AFE1A401410EAA03D28D4ADE1BD5'
    api_secret = '8E5B70913E3B2526A9896969E3483635E5FB95F60373E2EC'

    exchange = ccxt.coinex({
        'apiKey': api_key,
        'secret': api_secret,
        'options': {'defaultType': 'swap'}
    })

    print("="*60)
    print("DEBUGGING ORDERS & PNL (Last 48 Hours)")
    print("="*60)

    # 1. Check STOP ORDERS (Trigger Orders)
    # CoinEx often requests these differently. 
    # Try fetching open orders with specific params if standard doesn't work.
    try:
        print("\n--- 1. ACTIVE STOP ORDERS ---")
        # Standard fetch
        orders = exchange.fetch_open_orders(symbol='BTC/USDT:USDT')
        print(f"Standard Open Orders: {len(orders)}")
        for o in orders:
             print(f"  [Standard] {o['id']} {o['type']} {o['side']} {o['status']} StopPrice:{o.get('stopPrice')}")

        # Try specific params for Stop Orders (Plan Orders)
        # Note: CCXT implementation for Coinex might vary, trying 'stop' type filter
        try:
            stop_orders = exchange.fetch_open_orders(symbol='BTC/USDT:USDT', params={'type': 'stop'})
            print(f"Stop Type Params: {len(stop_orders)}")
            for o in stop_orders:
                print(f"  [StopParams] {o['id']} {o['type']} {o['side']} {o['status']} StopPrice:{o.get('stopPrice')}")
        except Exception as e:
            print(f"  Stop param fetch failed: {e}")

    except Exception as e:
        print(f"Error fetching orders: {e}")

    except Exception as e:
        print(f"Error fetching orders: {e}")

    # 2. TRADE HISTORY & LEDGER (Last 7 Days)
    try:
        print("\n--- 2. TRADE HISTORY & FEES (Last 7 Days) ---")
        # Go back 7 days
        since = int((datetime.now() - timedelta(days=7)).timestamp() * 1000)
        
        symbols = ['BTC/USDT:USDT', 'SOL/USDT:USDT', 'ETH/USDT:USDT', 'XRP/USDT:USDT', 
                   'DOGE/USDT:USDT', 'PEPE/USDT:USDT', 'LINK/USDT:USDT', 'BNB/USDT:USDT']
        
        all_trades = []
        for sym in symbols:
            try:
                trades = exchange.fetch_my_trades(sym, since=since, limit=100)
                for t in trades:
                    t['symbol'] = sym 
                    all_trades.append(t)
            except Exception:
                pass 
        
        all_trades.sort(key=lambda x: x['timestamp'])
        
        total_pnl = 0.0
        total_fee = 0.0
        
        print(f"Found {len(all_trades)} trades.")
        print("-" * 80)
        
        for t in all_trades:
            dt = datetime.fromtimestamp(t['timestamp']/1000).strftime('%Y-%m-%d %H:%M')
            fee_cost = t['fee']['cost'] if t['fee'] else 0
            
            pnl = 0.0
            if 'info' in t:
                # Try different keys for PnL
                if 'realized_pnl' in t['info']: pnl = float(t['info']['realized_pnl'])
                elif 'pnl' in t['info']: pnl = float(t['info']['pnl'])
            
            total_pnl += pnl
            total_fee += fee_cost
            
            # Print only significant trades or errors
            # print(f"{dt:<16} {t['symbol']:<10} {t['side']:<4} Price:{t['price']:<8} Fee:{fee_cost:<6.4f} PnL:{pnl:<6.4f}")

        print("\nSUMMARY (Trades):")
        print(f"Gross PnL: {total_pnl:.4f} USDT")
        print(f"Total Fees: {total_fee:.4f} USDT")
        print(f"Net Trade PnL: {total_pnl - total_fee:.4f} USDT")
        
        # 3. LEDGER / FUNDING FEES
        print("\n--- 3. LEDGER / FUNDING CHECK ---")
        try:
            # Not all exchanges support fetch_ledger, wrapped in try
            ledger = exchange.fetch_ledger(code='USDT', since=since, limit=100)
            print(f"Ledger Entries: {len(ledger)}")
            
            funding_paid = 0.0
            other_fees = 0.0
            for entry in ledger:
                amount = float(entry['amount'])
                type_ = entry['type']
                # Funding is usually 'fee' or 'funding' type with negative amount
                if type_ == 'funding':
                    funding_paid += amount
                elif type_ == 'fee':
                    other_fees += amount
                
                # Print non-trade entries (transfers, funding)
                if type_ not in ['trade', 'transaction']:
                     dt = datetime.fromtimestamp(entry['timestamp']/1000).strftime('%Y-%m-%d %H:%M')
                     print(f"  {dt} Type: {type_} Amount: {amount}")

            print(f"Total Funding Paid: {funding_paid:.4f}")
            
        except Exception as e:
            print(f"Ledger fetch not supported/failed: {e}")

    except Exception as e:
        print(f"Error fetching history: {e}")

if __name__ == "__main__":
    debug_pnl_orders()

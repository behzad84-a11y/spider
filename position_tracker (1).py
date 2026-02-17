import asyncio
import logging
import time
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

class PositionTracker:
    def __init__(self, spot_exchange, futures_exchange, db_manager=None):
        self.spot_exchange = spot_exchange
        self.futures_exchange = futures_exchange
        self.db_manager = db_manager
        
        self.positions: Dict[str, List[Dict[str, Any]]] = {
            'spot': [],
            'future': [],
            'forex': []
        }
        self.orders: Dict[str, List[Dict[str, Any]]] = {
            'spot': [],
            'future': [],
            'forex': []
        }
        self.last_sync = 0.0
        self.sync_interval = 30  # Sync every 30 seconds
        self._lock = asyncio.Lock()

    async def sync(self, force=False):
        """Synchronize state with exchanges."""
        async with self._lock:
            now = time.time()
            if not force and (now - self.last_sync < self.sync_interval):
                return

            logger.info("🔄 Syncing PositionTracker with exchanges...")
            try:
                # 1. Futures Positions
                fut_pos = await asyncio.to_thread(self.futures_exchange.fetch_positions)
                self.positions['future'] = [p for p in fut_pos if float(p.get('contracts', 0) or 0) != 0]

                # 2. Futures Open Orders
                self.orders['future'] = await asyncio.to_thread(self.futures_exchange.fetch_open_orders)

                # 3. Spot Balances (as positions)
                balance = await asyncio.to_thread(self.spot_exchange.fetch_balance)
                self.positions['spot'] = []
                for curr, val in balance.get('total', {}).items():
                    if val > 0 and curr != 'USDT':
                        self.positions['spot'].append({'symbol': f"{curr}/USDT", 'amount': val})

                # 4. Forex (MT5) - If available
                try:
                    import MetaTrader5 as mt5
                    if mt5.terminal_info():
                        # Get open positions
                        fx_pos = mt5.positions_get()
                        if fx_pos:
                            self.positions['forex'] = [p._asdict() for p in fx_pos]
                        else:
                            self.positions['forex'] = []
                        
                        # Get pending orders
                        fx_orders = mt5.orders_get()
                        if fx_orders:
                            self.orders['forex'] = [o._asdict() for o in fx_orders]
                        else:
                            self.orders['forex'] = []
                except Exception as e:
                    logger.debug(f"MT5 sync skipped or failed: {e}")

                self.last_sync = now
                logger.info(f"✅ Sync complete. Found {len(self.positions['future'])} futures positions, {len(self.positions['spot'])} spot holdings.")

            except Exception as e:
                logger.error(f"❌ PositionTracker Sync Error: {e}")

    def get_positions(self, market_type: Optional[str] = None, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get tracked positions, optionally filtered."""
        if market_type:
            pos_list = self.positions.get(market_type.lower(), [])
        else:
            pos_list = self.positions['spot'] + self.positions['future'] + self.positions['forex']
        
        if symbol:
            return [p for p in pos_list if p.get('symbol') == symbol]
        return pos_list

    def get_orders(self, market_type: Optional[str] = None, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get tracked open orders, optionally filtered."""
        if market_type:
            order_list = self.orders.get(market_type.lower(), [])
        else:
            order_list = self.orders['spot'] + self.orders['future'] + self.orders['forex']
            
        if symbol:
            return [o for o in order_list if o.get('symbol') == symbol]
        return order_list

    def update_after_execution(self, result: Any, request: Any):
        """Immediately update state after a successful execution to avoid sync lag."""
        # Simple implementation: mark as 'sync needed' or append to list
        # For now, we'll just trigger a background sync in 1 second
        asyncio.create_task(self._delayed_sync(1))

    async def _delayed_sync(self, delay):
        await asyncio.sleep(delay)
        await self.sync(force=True)

    async def calculate_full_equity(self) -> Dict[str, float]:
        """
        Calculates total equity in USD (Spot balances + Futures equity + MT5 equity).
        Returns a dict with breakdown and total.
        """
        spot_equity = 0.0
        futures_equity = 0.0
        forex_equity = 0.0
        unrealized_pnl = 0.0
        
        # 1. Spot Equity
        # We need prices for all spot holdings
        try:
            balance = await asyncio.to_thread(self.spot_exchange.fetch_balance)
            # Use cast/get to satisfy type checker
            total_bal: Dict[str, Any] = balance.get('total', {})
            tickers_to_fetch = []
            for curr, val in total_bal.items():
                v = float(val or 0)
                if v > 0:
                    if curr == 'USDT':
                        spot_equity += v
                    else:
                        tickers_to_fetch.append(f"{curr}/USDT")
            
            if tickers_to_fetch:
                # Limit to avoid rate limits or huge batches
                subset = tickers_to_fetch[:50]
                tickers: Dict[str, Any] = await asyncio.to_thread(self.spot_exchange.fetch_tickers, subset)
                for symbol, ticker in tickers.items():
                    curr = symbol.split('/')[0]
                    if curr in total_bal:
                        price = float(ticker.get('last') or ticker.get('close') or 0)
                        spot_equity += float(total_bal[curr] or 0) * price
        except Exception as e:
            logger.error(f"Error calculating spot equity: {e}")

        # 2. Futures Equity (CoinEx Swap)
        try:
            # For CoinEx, fetch_balance on swap account returns the total equity in USDT
            fut_bal = await asyncio.to_thread(self.futures_exchange.fetch_balance)
            if 'USDT' in fut_bal['total']:
                futures_equity = fut_bal['total']['USDT']
            
            # Unrealized PnL breakdown
            positions = await asyncio.to_thread(self.futures_exchange.fetch_positions)
            for p in positions:
                unrealized_pnl += float(p.get('unrealizedPnl', 0) or 0)
        except Exception as e:
            logger.error(f"Error calculating futures equity: {e}")

        # 3. Forex (MT5)
        try:
            import MetaTrader5 as mt5
            if mt5.terminal_info():
                acc_info = mt5.account_info()
                if acc_info:
                    forex_equity = acc_info.equity
                    # Note: We usually report in USD. If account is EUR, we might need conversion.
                    # For now assume USD or reporting as primary currency.
        except Exception as e:
            logger.debug(f"MT5 equity check skipped or failed: {e}")

        total_equity = spot_equity + futures_equity + forex_equity
        
        return {
            'total': total_equity,
            'spot': spot_equity,
            'futures': futures_equity,
            'forex': forex_equity,
            'unrealized': unrealized_pnl
        }

    def get_total_equity(self) -> float:
        """
        Legacy method for RiskEngine. Returns last known total balance or 0.
        """
        if self.db_manager:
            try:
                # Try to get from last snapshot if available
                last = self.db_manager.get_latest_equity_snapshot()
                if last:
                    return last[0] # total_equity 
                return float(self.db_manager.get_setting('current_balance', 0))
            except:
                return 0.0
        return 0.0

    async def get_pnl_breakdown(self, since_ms: int) -> Dict[str, float]:
        """
        Estimates fees and funding for the given period.
        """
        fees = 0.0
        funding = 0.0
        
        try:
            # 1. Fetch Fees from Futures Trades
            # NOTE: CoinEx requires a symbol argument for fetchMyTrades (unlike Binance).
            # We iterate over currently known futures positions/symbols to collect fees.
            known_symbols = [p.get('symbol') for p in self.positions.get('future', []) if p.get('symbol')]
            if not known_symbols:
                # Fallback: fetch from last trades in DB if available
                if self.db_manager:
                    try:
                        self.db_manager.cursor.execute(
                            'SELECT DISTINCT symbol FROM trade_history ORDER BY timestamp DESC LIMIT 10'
                        )
                        rows = self.db_manager.cursor.fetchall()
                        known_symbols = [r[0] for r in rows]
                    except Exception:
                        pass
            
            for sym in known_symbols:
                try:
                    sym_trades = await asyncio.to_thread(
                        self.futures_exchange.fetch_my_trades, sym, since_ms, 100
                    )
                    if sym_trades:
                        for t in sym_trades:
                            fee_info = t.get('fee', {})
                            if fee_info and fee_info.get('cost'):
                                fees += float(fee_info['cost'])
                except Exception as e:
                    logger.debug(f"Fee fetch skipped for {sym}: {e}")
            
            # 2. Fetch Funding Fees
            if hasattr(self.futures_exchange, 'fetch_funding_history'):
                # Note: fetch_funding_history might need a symbol on some exchanges, 
                # but for CoinEx swap it's usually general for the account or symbol-specific.
                # CCXT fetch_funding_history(symbol=None, since=None, limit=None, params={})
                funding_history = await asyncio.to_thread(self.futures_exchange.fetch_funding_history, None, since_ms, 100)
                if funding_history:
                    for f in funding_history:
                        funding += float(f.get('amount', 0))
                    
        except Exception as e:
            logger.error(f"Error fetching PnL breakdown: {e}")
            
        return {
            'fees': fees,
            'funding': funding
        }

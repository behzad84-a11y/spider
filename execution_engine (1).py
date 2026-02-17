import logging
import asyncio
import uuid
import json
import time
from typing import Tuple, Dict, Any, Optional
from dataclasses import dataclass, asdict
from risk_engine import RiskEngine, TradeRequest

logger = logging.getLogger(__name__)

@dataclass
class ExecutionResult:
    success: bool
    status: str  # success | rejected | pending | failed | partial
    message: str
    order_id: Optional[str] = None
    client_order_id: str = ""
    raw_response: Optional[dict] = None

class ExecutionEngine:
    def __init__(self, spot_exchange, futures_exchange, risk_engine: RiskEngine, position_tracker=None):
        self.spot_exchange = spot_exchange
        self.futures_exchange = futures_exchange
        self.risk_engine = risk_engine
        self.position_tracker = position_tracker
        self.processed_ids: Dict[str, float] = {} # Idempotency cache: {id: timestamp}
        self.ttl_seconds = 3600 # 1 hour TTL for ids
        self.lock = asyncio.Lock()
        self.is_active = True # Default to True, controlled by main bot

    async def execute(self, request: TradeRequest) -> ExecutionResult:
        """
        Production-grade execution with risk enforcement, idempotency, and retries.
        """
        # 0. Instance Check
        if not self.is_active:
             return ExecutionResult(success=False, status="rejected", message="Instance NOT ACTIVE (Master lock held by another bot). Blocked.")
        client_order_id = request.params.get('client_order_id') if request.params else None
        if not client_order_id:
            client_order_id = str(uuid.uuid4())
        
        # 1. Risk Enforcement
        is_valid, risk_msg = self.risk_engine.validate(request)
        if not is_valid:
            return self._log_and_return(
                ExecutionResult(
                    success=False, 
                    status="rejected", 
                    message=f"Risk Engine Rejected: {risk_msg}",
                    client_order_id=client_order_id
                ),
                request
            )

        # 2. Idempotency Check
        async with self.lock:
            await self._cleanup_processed_ids()
            if client_order_id in self.processed_ids:
                return ExecutionResult(success=False, status="rejected", message="Duplicate client_order_id", client_order_id=client_order_id)
            # Mark as "in progress" by adding with current time
            self.processed_ids[client_order_id] = time.time()
        
        # 3. Handle Market Types
        if request.market_type == 'forex':
            return await self._execute_forex(request, client_order_id)
        else:
            return await self._execute_crypto(request, client_order_id)

    async def _cleanup_processed_ids(self):
        """Removes IDs older than TTL."""
        now = time.time()
        expired = [cid for cid, ts in self.processed_ids.items() if now - ts > self.ttl_seconds]
        for cid in expired:
            del self.processed_ids[cid]

    async def _check_order_reconciliation(self, exchange, symbol, client_order_id: str) -> Optional[dict]:
        """Checks if an order with this client_order_id already exists on the exchange."""
        try:
            # CoinEx specific reconciliation via client_id
            if hasattr(exchange, 'id') and exchange.id == 'coinex':
                # CoinEx Fetch Order via client_id (not directly supported by generic fetch_order in all CCXT versions)
                # We use fetch_orders or custom private call if needed, but fetch_open_orders is safer.
                orders = await asyncio.to_thread(exchange.fetch_open_orders, symbol)
                for o in orders:
                    # CoinEx uses 'client_id' in info, CCXT maps it to clientOrderId
                    if o.get('clientOrderId') == client_order_id:
                        return o
                
                # If not in open, maybe in closed? (Only check recent)
                closed = await asyncio.to_thread(exchange.fetch_closed_orders, symbol, limit=10)
                for o in closed:
                    if o.get('clientOrderId') == client_order_id:
                        return o
            return None
        except Exception as e:
            logger.error(f"Reconciliation check failed for {client_order_id}: {e}")
            return None

    async def _execute_crypto(self, request: TradeRequest, client_order_id: str) -> ExecutionResult:
        exchange = self.futures_exchange if request.market_type == 'future' else self.spot_exchange
        side = 'buy' if request.side.lower() in ['buy', 'long'] else 'sell'
        
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                # RECONCILIATION: Before retry (except first attempt), check if order exists
                if attempt > 0:
                    existing_order = await self._check_order_reconciliation(exchange, request.symbol, client_order_id)
                    if existing_order:
                        logger.info(f"RECONCILE: Found existing order for {client_order_id}. Skipping retry.")
                        return self._log_and_return(ExecutionResult(
                            success=True, status="success", message="Order recovered via reconciliation",
                            order_id=existing_order.get('id'), client_order_id=client_order_id, raw_response=existing_order
                        ), request)

                params = {}
                if hasattr(exchange, 'id') and exchange.id == 'coinex':
                    params['client_id'] = client_order_id
                
                order = await asyncio.to_thread(
                    exchange.create_market_order,
                    request.symbol,
                    side,
                    request.amount,
                    params
                )
                
                return self._log_and_return(ExecutionResult(
                    success=True, status="success", message="Order placed",
                    order_id=order.get('id'), client_order_id=client_order_id, raw_response=order
                ), request)

            except Exception as e:
                error_str = str(e).lower()
                # If it's a known "duplicate" error from exchange, treat as success/recover
                if "already exists" in error_str or "duplicate" in error_str:
                    existing_order = await self._check_order_reconciliation(exchange, request.symbol, client_order_id)
                    if existing_order:
                         return self._log_and_return(ExecutionResult(
                            success=True, status="success", message="Order recovered (Duplicate detected)",
                            order_id=existing_order.get('id'), client_order_id=client_order_id, raw_response=existing_order
                        ), request)

                if self._is_network_error(e) and attempt < max_retries - 1:
                    logger.warning(f"RETRY: Network error on attempt {attempt+1} for {request.symbol}. Retrying in {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                
                # On final failure, remove from processed_ids if it was just "pending" logic? 
                # No, better keep it to prevent accidental external retries for some time.
                return self._log_and_return(ExecutionResult(
                    success=False, status="failed", message=str(e), client_order_id=client_order_id
                ), request)

    async def _execute_forex(self, request: TradeRequest, client_order_id: str) -> ExecutionResult:
        import MetaTrader5 as mt5
        side = 'buy' if request.side.lower() in ['buy', 'long'] else 'sell'
        
        try:
            if not mt5.initialize():
                return ExecutionResult(success=False, status="failed", message="MT5 Init Failed", client_order_id=client_order_id)
            
            tick = mt5.symbol_info_tick(request.symbol)
            if not tick:
                return ExecutionResult(success=False, status="failed", message="MT5 Tick Failed", client_order_id=client_order_id)
            
            price = tick.ask if side == 'buy' else tick.bid
            
            order_request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": request.symbol,
                "volume": float(request.amount),
                "type": mt5.ORDER_TYPE_BUY if side == 'buy' else mt5.ORDER_TYPE_SELL,
                "price": price,
                "magic": 234000,
                "comment": f"EE {client_order_id[:8]}", # Storing part of CID in comment
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            if request.params:
                if 'sl' in request.params: order_request['sl'] = float(request.params['sl'])
                if 'tp' in request.params: order_request['tp'] = float(request.params['tp'])
                if 'magic' in request.params: order_request['magic'] = int(request.params['magic'])
                if 'comment' in request.params: order_request['comment'] = str(request.params['comment'])
            
            res = await asyncio.to_thread(mt5.order_send, order_request)
            if res.retcode != mt5.TRADE_RETCODE_DONE:
                return self._log_and_return(ExecutionResult(
                    success=False, status="failed", message=f"MT5 Error: {res.comment}", client_order_id=client_order_id
                ), request)
                
            return self._log_and_return(ExecutionResult(
                success=True, status="success", message="MT5 Order placed",
                order_id=str(res.order), client_order_id=client_order_id, raw_response=res._asdict()
            ), request)
            
        except Exception as e:
            return self._log_and_return(ExecutionResult(
                success=False, status="failed", message=str(e), client_order_id=client_order_id
            ), request)

    def _is_network_error(self, e: Exception) -> bool:
        error_str = str(e).lower()
        return any(kw in error_str for kw in ['timeout', 'network', 'connection', 'request timeout', 'latency', 'throttled'])

    def _log_and_return(self, result: ExecutionResult, request: TradeRequest) -> ExecutionResult:
        """Structured JSON Logging"""
        log_entry = {
            "event": "order_execution",
            "market_type": request.market_type,
            "symbol": request.symbol,
            "side": request.side,
            "amount": request.amount,
            "leverage": request.leverage,
            "status": result.status,
            "order_id": result.order_id,
            "client_order_id": result.client_order_id,
            "timestamp": time.time(),
            "message": result.message
        }
        logger.info(json.dumps(log_entry))
        
        # 3. Update PositionTracker immediately
        if result.success and self.position_tracker:
            self.position_tracker.update_after_execution(result, request)
            
        return result

    async def set_leverage(self, leverage: int, symbol: str, market_type: str = 'future') -> bool:
        """Sets leverage for a symbol."""
        try:
            exchange = self.futures_exchange if market_type == 'future' else self.spot_exchange
            await asyncio.to_thread(exchange.set_leverage, leverage, symbol)
            return True
        except Exception as e:
            logger.error(f"Failed to set leverage for {symbol}: {e}")
            return False

    async def set_margin_mode(self, margin_mode: str, symbol: str, market_type: str = 'future') -> bool:
        """Sets margin mode (isolated/cross) for a symbol."""
        try:
            exchange = self.futures_exchange if market_type == 'future' else self.spot_exchange
            await asyncio.to_thread(exchange.set_margin_mode, margin_mode, symbol)
            return True
        except Exception as e:
            logger.error(f"Failed to set margin mode for {symbol}: {e}")
            return False

    async def fetch_positions(self, symbols: Optional[list] = None, market_type: str = 'future') -> list:
        """Fetches current positions."""
        try:
            exchange = self.futures_exchange if market_type == 'future' else self.spot_exchange
            return await asyncio.to_thread(exchange.fetch_positions, symbols)
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            return []

    async def fetch_market_data(self, symbol: str, data_type: str = 'ticker', timeframe: str = '1h', limit: int = 100) -> Any:
        """Centralized market data fetching."""
        try:
            # Always use futures_exchange for data if available, as most strategies trade futures
            exchange = self.futures_exchange if self.futures_exchange else self.spot_exchange
            
            if data_type == 'ticker':
                return await asyncio.to_thread(exchange.fetch_ticker, symbol)
            elif data_type == 'ohlcv':
                return await asyncio.to_thread(exchange.fetch_ohlcv, symbol, timeframe, limit=limit)
            return None
        except Exception as e:
            logger.error(f"Error fetching {data_type} for {symbol}: {e}")
            return None

    async def cancel_order(self, symbol: str, order_id: str, market_type: str = 'future') -> bool:
        """Cancels an order on the exchange."""
        try:
            exchange = self.futures_exchange if market_type == 'future' else self.spot_exchange
            await asyncio.to_thread(exchange.cancel_order, order_id, symbol)
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def create_trigger_order(self, request: TradeRequest, trigger_price: float, order_type: str = 'stop_market') -> ExecutionResult:
        """
        Creates a trigger order (Stop Loss / Take Profit).
        """
        client_order_id = request.params.get('client_order_id') if request.params else None
        if not client_order_id:
            client_order_id = str(uuid.uuid4())

        exchange = self.futures_exchange if request.market_type == 'future' else self.spot_exchange
        side = 'buy' if request.side.lower() in ['buy', 'long'] else 'sell'
        
        try:
            # Handle Precision
            try:
                price_str = exchange.price_to_precision(request.symbol, trigger_price)
                amount_str = exchange.amount_to_precision(request.symbol, request.amount)
            except:
                price_str = str(trigger_price)
                amount_str = str(request.amount)

            params = {
                'stop_price': price_str,
                'reduceOnly': request.params.get('reduceOnly', True) if request.params else True
            }

            # CoinEx Specifics
            if hasattr(exchange, 'id') and exchange.id == 'coinex':
                params['stop_type'] = 2 # 2 = Mark Price, 1 = Latest Price
                params['client_id'] = client_order_id

            order = await asyncio.to_thread(
                exchange.create_order,
                request.symbol,
                'market', # Stop triggers usually execute as market
                side,
                float(amount_str),
                None,
                params
            )

            return self._log_and_return(ExecutionResult(
                success=True, status="success", message="Trigger order placed",
                order_id=order.get('id'), client_order_id=client_order_id, raw_response=order
            ), request)

        except Exception as e:
            return self._log_and_return(ExecutionResult(
                success=False, status="failed", message=str(e), client_order_id=client_order_id
            ), request)

    async def get_allowed_leverages(self, symbol: str, margin_usdt: float, market_type: str = 'future') -> dict:
        """
        Calculates leverage eligibility based on exchange limits and current price.
        """
        try:
            import math
            exchange = self.futures_exchange if market_type == 'future' else self.spot_exchange
            
            # Ensure markets are loaded
            if not exchange.markets:
                await asyncio.to_thread(exchange.load_markets)
            
            market = exchange.market(symbol)
            
            # Fetch current price
            ticker = await self.fetch_market_data(symbol, 'ticker')
            if not ticker or 'last' not in ticker:
                return {"success": False, "reason": "عدم دسترسی به قیمت لحظه‌ای"}
            
            price = ticker['last']
            
            # Determine minimum notional (minimum USD value of the order)
            # NOTE: CoinEx sometimes returns None for 'min' inside limits dict,
            # so we use 'or 0.0' to safely handle None values (not just missing keys).
            min_notional = 0.0
            if market.get('limits') and market['limits'].get('cost'):
                min_notional = float(market['limits']['cost'].get('min') or 0.0)
            
            # If cost limit is missing, derive from amount limit
            if min_notional <= 0 and market.get('limits') and market['limits'].get('amount'):
                min_amount = float(market['limits']['amount'].get('min') or 0.0)
                min_notional = min_amount * price
            
            # Final fallback
            if min_notional <= 0:
                min_notional = 5.0 

            if margin_usdt <= 0:
                return {"success": False, "reason": "مارجین باید بیشتر از صفر باشد"}

            # Calculate min leverage required (ceil(min_notional / margin))
            min_leverage_required = math.ceil(min_notional / margin_usdt)
            
            global_max = int(self.risk_engine.db.get_setting('global_max_leverage', 20))
            
            if min_leverage_required > global_max:
                min_required_margin = min_notional / global_max
                return {
                    "success": False,
                    "min_leverage_required": min_leverage_required,
                    "min_required_margin_usdt": min_required_margin,
                    "reason": f"با {margin_usdt}$ برای {symbol} هیچ اهرمی معتبر نیست. حداقل مبلغ لازم: {min_required_margin:.2f}$"
                }
            
            min_leverage_required = max(1, min_leverage_required)
            
            # Generate allowed leverage options
            # Starting from min_leverage_required up to global_max
            base_options = [1, 2, 5, 10, 20, 25, 50, 75, 100]
            allowed = [l for l in base_options if l >= min_leverage_required and l <= global_max]
            
            # Always ensure the exact minimum is an option if not in base_options
            if min_leverage_required not in allowed and min_leverage_required <= global_max:
                allowed.insert(0, min_leverage_required)
                allowed.sort()

            return {
                "success": True,
                "min_leverage_required": min_leverage_required,
                "allowed_leverages": allowed,
                "min_required_margin_usdt": min_notional / global_max, # Technically correct for min margin at max lev
                "min_notional": min_notional,
                "price": price
            }
            
        except Exception as e:
            logger.error(f"Error in get_allowed_leverages for {symbol}: {e}")
            return {"success": False, "reason": str(e)}

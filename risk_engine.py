import logging
import json
import time
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)

@dataclass
class TradeRequest:
    symbol: str
    amount: float
    leverage: int
    side: str  # 'buy' or 'sell'
    market_type: str  # 'spot' or 'future'
    user_id: int
    params: Optional[Dict[str, Any]] = None

class RiskEngine:
    def __init__(self, db_manager, position_tracker=None):
        self.db = db_manager
        self.tracker = position_tracker
        
    def validate(self, request: TradeRequest) -> Tuple[bool, str]:
        """
        Validates a trade request against portfolio-aware risk rules.
        Returns: (is_valid, error_message)
        """
        # 1. Kill-Switch Check
        if self.db.get_setting('kill_switch_enabled', 'False') == 'True':
            return False, "⚠️ کلید قطع اضطراری فعال است. تمام معاملات متوقف شده‌اند."

        # 2. Drawdown Throttling
        is_throttled, factor, dd_msg = self._check_drawdown_throttle()
        if is_throttled and factor <= 0:
            return False, f"⚠️ محدودیت شدید ترید به دلیل دراوداون: {dd_msg}"

        # 3. Leverage Cap
        if request.market_type == 'future':
            max_lev = int(self.db.get_setting('global_max_leverage', 20))
            if request.leverage > max_lev:
                return False, f"⚠️ اهرم {request.leverage} بیش از سقف مجاز ({max_lev}) است."

        # 4. Symbol Exposure Check
        symbol_exposure = self._get_symbol_exposure(request.symbol)
        max_symbol_exposure = float(self.db.get_setting('max_symbol_exposure_usd', 1000))
        
        # Apply drawdown factor to limits
        effective_max_exposure = max_symbol_exposure * factor
        
        if (symbol_exposure + request.amount) > effective_max_exposure:
            return False, f"⚠️ حد مجاز درگیری سرمایه برای {request.symbol} تکمیل شده است. (موجود: {symbol_exposure}$, حد: {effective_max_exposure}$)"

        # 5. Correlated Exposure Check
        is_corr_valid, corr_msg = self._check_correlated_exposure(request)
        if not is_corr_valid:
            return False, corr_msg

        # 6. Minimum/Maximum Absolute Amount
        # Enforced via ExecutionEngine in validate_async or similar? 
        # Plan says: Update RiskEngine.validate() to enforce.
        # But validate is currently sync. I will make it async to allow limit fetching.
        
        return True, "Success"

    async def validate_async(self, request: TradeRequest, execution_engine=None) -> Tuple[bool, str]:
        """Async version of validate to support live exchange limit checks."""
        # Run sync checks first
        is_valid, msg = self.validate(request)
        if not is_valid:
            return False, msg

        # Calculate factor for local use in this method too
        _, factor, _ = self._check_drawdown_throttle()

        # 6. Exchange Minimums Enforcement (Dynamic)
        if execution_engine:
            exchange = execution_engine.futures_exchange if request.market_type == 'future' else execution_engine.spot_exchange
            if exchange:
                limits = await execution_engine.get_min_trade_requirements(exchange, request.symbol, request.market_type)
                
                min_notional = limits.get('min_notional', 5.0)
                # Notional Calculation: amount is margin, so notional = amount * leverage
                request_notional = request.amount * (request.leverage if request.market_type == 'future' else 1)
                
                if request_notional < min_notional:
                    # AUTO mode: Try to fix leverage
                    if request.params and request.params.get('is_auto'):
                        import math
                        needed_lev = math.ceil(min_notional / request.amount)
                        max_lev = int(self.db.get_setting('global_max_leverage', 20))
                        
                        if needed_lev <= max_lev:
                            # Find closest higher allowed leverage
                            allowed_levs = [1, 2, 5, 10, 20, 25, 50, 75, 100]
                            for al in allowed_levs:
                                if al >= needed_lev:
                                    request.leverage = al
                                    logger.info(f"AUTO-FIX: Adjusted leverage to {al}x to meet min notional {min_notional}$")
                                    return True, "Success"
                        
                        return False, f"⚠️ مبلغ {request.amount}$ خیلی کم است. حتی با اهرم {max_lev} نیز به حداقل {min_notional}$ نمی‌رسد."
                    else:
                        return False, f"⚠️ مبلغ ناچیز! حداقل ارزش معامله برای {request.symbol} برابر {min_notional}$ است. (ارزش فعلی شما: {request_notional}$)"

        # 7. Maximum Absolute Amount
        max_trade_size = float(self.db.get_setting('global_max_trade_size', 500)) * factor
        if request.amount > max_trade_size:
            return False, f"⚠️ مبلغ معامله بیش از سقف مجاز برای هر ترید ({max_trade_size}$) است."

        return True, "Success"

    def _get_symbol_exposure(self, symbol: str) -> float:
        """Calculates total USD exposure for a symbol from PositionTracker."""
        if not self.tracker:
            return 0.0
        
        positions = self.tracker.get_positions(symbol=symbol)
        # Sum absolute value of positions (long or short is still exposure)
        exposure = sum(abs(float(p.get('amount', 0) or 0) * float(p.get('entryPrice', 1) or 1)) for p in positions)
        
        # Add pending orders exposure (simplified: just amount)
        orders = self.tracker.get_orders(symbol=symbol)
        exposure += sum(float(o.get('amount', 0)) for o in orders)
        
        return exposure

    def _check_correlated_exposure(self, request: TradeRequest) -> Tuple[bool, str]:
        """Checks if the new trade exceeds limits for its correlation group."""
        if not self.tracker:
            return True, ""

        groups_json = self.db.get_setting('correlation_groups', '{}')
        try:
            groups = json.loads(groups_json)
        except:
            return True, ""

        # Find which group this symbol belongs to
        target_group = None
        for group_name, symbols in groups.items():
            if request.symbol in symbols:
                target_group = group_name
                break
        
        if target_group:
            group_symbols = groups[target_group]
            current_group_exposure = sum(self._get_symbol_exposure(s) for s in group_symbols)
            group_limit = float(self.db.get_setting(f'limit_group_{target_group}', 2000))
            
            if (current_group_exposure + request.amount) > group_limit:
                return False, f"⚠️ حد مجاز گروه همبستگی {target_group} رعایت نشده است. ({current_group_exposure}$ + {request.amount}$ > {group_limit}$)"
        
        return True, ""

    def _check_drawdown_throttle(self) -> Tuple[bool, float, str]:
        """Calculates drawdown factor and message."""
        if not self.tracker:
            return False, 1.0, ""

        current_equity = self.tracker.get_total_equity()
        if current_equity <= 0:
            return False, 1.0, ""

        peak_equity = float(self.db.get_setting('peak_equity', current_equity))
        
        # Update peak if reached
        if current_equity > peak_equity:
            self.db.set_setting('peak_equity', current_equity)
            return False, 1.0, ""

        drawdown_pct = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0
        
        # Throttling logic:
        # > 5% DD -> 80% size
        # > 10% DD -> 50% size
        # > 20% DD -> Stop trading (0% size)
        
        if drawdown_pct > 0.20:
            return True, 0.0, f"دراوداون شدید ({drawdown_pct:.1%})"
        if drawdown_pct > 0.10:
            return True, 0.5, f"دراوداون متوسط ({drawdown_pct:.1%})"
        if drawdown_pct > 0.05:
            return True, 0.8, f"دراوداون جزئی ({drawdown_pct:.1%})"
            
        return False, 1.0, ""

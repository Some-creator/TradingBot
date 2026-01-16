"""
Risk Management System.
Enforces daily limits, consecutive loss rules, and position sizing.
"""

from datetime import datetime, timedelta
from typing import Tuple
from loguru import logger

from src.models import Trade, Direction, DailyState
from src.state import state_manager
from src.config import trading


class RiskManager:
    """
    Manages risk limits and position sizing.
    
    Rules:
    - Max 3 trades per day
    - Max 1.5% daily loss
    - Max 0.5% risk per trade
    - Lockout after 2 consecutive losses
    - Lockout if VIX spikes > 10% intraday
    """
    
    def __init__(self):
        self._initial_vix: float = 0.0
    
    def set_initial_vix(self, vix: float) -> None:
        """Set the opening VIX for spike detection."""
        self._initial_vix = vix
    
    async def can_trade(self) -> Tuple[bool, str]:
        """
        Check if trading is allowed.
        
        Returns:
            (allowed, reason)
        """
        # Check if already locked
        is_locked, reason = await state_manager.is_trading_locked()
        if is_locked:
            return False, reason
        
        # Get daily state
        state = await state_manager.get_daily_state()
        
        # Max trades check
        if state.trade_count >= trading.max_trades_per_day:
            await state_manager.lock_trading("Max trades reached")
            return False, "Max trades per day reached"
        
        # Max daily loss check
        if abs(state.daily_pnl_percent) >= trading.max_daily_loss_pct:
            if state.daily_pnl_percent < 0:
                await state_manager.lock_trading("Max daily loss reached")
                return False, "Max daily loss reached"
        
        # Consecutive losses check
        if state.consecutive_losses >= 2:
            await state_manager.lock_trading("2 consecutive losses")
            return False, "2 consecutive losses - trading paused"
        
        return True, "Trading allowed"
    
    def check_vix_spike(self, current_vix: float) -> bool:
        """Check if VIX has spiked > 10% from open."""
        if self._initial_vix <= 0:
            return False
        
        change_pct = ((current_vix - self._initial_vix) / self._initial_vix) * 100
        
        if change_pct > 10:
            logger.warning(f"VIX spike detected: {change_pct:.1f}%")
            return True
        
        return False
    
    def calculate_position_size(
        self,
        account_equity: float,
        entry_price: float,
        stop_loss: float,
        direction: Direction
    ) -> float:
        """
        Calculate position size based on risk.
        
        Risk per trade = max_trade_risk_pct of account
        Position Size = (Account * Risk %) / (Entry - Stop)
        """
        # Maximum dollar risk
        max_risk_dollars = account_equity * (trading.max_trade_risk_pct / 100)
        
        # Risk per share
        if direction == Direction.LONG:
            risk_per_share = entry_price - stop_loss
        else:
            risk_per_share = stop_loss - entry_price
        
        if risk_per_share <= 0:
            logger.error("Invalid risk calculation: stop loss on wrong side")
            return 0
        
        # Position size
        shares = max_risk_dollars / risk_per_share
        
        # Round down to whole shares
        shares = int(shares)
        
        logger.info(
            f"Position size: {shares} shares "
            f"(Risk: ${max_risk_dollars:.2f}, Per share: ${risk_per_share:.2f})"
        )
        
        return shares
    
    async def record_trade_result(self, trade: Trade) -> None:
        """Record a completed trade and update daily stats."""
        state = await state_manager.get_daily_state()
        
        # Update P&L
        state.daily_pnl_percent += trade.pnl_percent
        
        # Update consecutive losses
        if trade.pnl < 0:
            state.consecutive_losses += 1
        else:
            state.consecutive_losses = 0  # Reset on win
        
        await state_manager.save_daily_state(state)
        
        logger.info(
            f"Trade closed: PnL {trade.pnl_percent:.2f}% | "
            f"Daily PnL: {state.daily_pnl_percent:.2f}% | "
            f"Consecutive Losses: {state.consecutive_losses}"
        )
    
    def should_move_stop_to_breakeven(self, trade: Trade, current_price: float) -> bool:
        """Check if stop should be moved to breakeven after TP1."""
        if trade.tp1_hit:
            return True
        
        if trade.direction == Direction.LONG:
            if current_price >= trade.tp1_price:
                return True
        else:
            if current_price <= trade.tp1_price:
                return True
        
        return False
    
    def should_time_stop(self, trade: Trade, current_price: float) -> bool:
        """Check if time stop should trigger (< 0.1% profit after 30 mins)."""
        if trade.entry_time is None:
            return False
        
        elapsed = datetime.now() - trade.entry_time
        
        if elapsed >= timedelta(minutes=trading.time_stop_minutes):
            # Calculate current profit
            if trade.direction == Direction.LONG:
                pnl_pct = ((current_price - trade.entry_price) / trade.entry_price) * 100
            else:
                pnl_pct = ((trade.entry_price - current_price) / trade.entry_price) * 100
            
            if pnl_pct < 0.1:
                logger.info(f"Time stop triggered: {pnl_pct:.2f}% after {elapsed}")
                return True
        
        return False


# Global instance
risk_manager = RiskManager()

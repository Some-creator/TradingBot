"""
Order Manager.
Handles trade execution for both paper and live trading modes.
"""

from datetime import datetime
import uuid
from typing import Optional
from loguru import logger

from src.models import Trade, Direction, TradeStatus
from src.execution.signal_generator import EntrySignal
from src.state import state_manager
from src.config import trading


class OrderManager:
    """
    Manages order execution.
    
    Supports:
    - PAPER mode: Simulated execution
    - LIVE mode: Broker API integration (placeholder)
    """
    
    def __init__(self):
        self._mode = trading.mode  # PAPER or LIVE
    
    async def execute_entry(
        self,
        signal: EntrySignal,
        quantity: float,
        symbol: str
    ) -> Optional[Trade]:
        """
        Execute an entry order.
        
        Args:
            signal: The entry signal
            quantity: Number of shares/contracts
            symbol: Trading symbol
            
        Returns:
            Trade object if successful
        """
        if quantity <= 0:
            logger.error("Invalid quantity for entry")
            return None
        
        trade_id = f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        
        trade = Trade(
            id=trade_id,
            symbol=symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            entry_time=datetime.now(),
            quantity=quantity,
            status=TradeStatus.OPEN,
            stop_loss=signal.stop_loss,
            original_stop_loss=signal.stop_loss,
            tp1_price=signal.tp1_price,
            tp2_price=signal.tp2_price if signal.tp2_price else 0.0,
            trigger_type=signal.signal_type.value,
            sweep_candle_low=signal.sweep_candle.low,
            sweep_candle_high=signal.sweep_candle.high
        )
        
        if self._mode == "PAPER":
            # Simulated execution
            logger.info(
                f"[PAPER] {signal.direction.value} {symbol} "
                f"@ ${signal.entry_price:.2f} x {quantity} | "
                f"SL: ${signal.stop_loss:.2f} | TP1: ${signal.tp1_price:.2f}"
            )
        else:
            # Live execution placeholder
            # TODO: Integrate with broker API (Alpaca, IBKR, etc.)
            logger.warning("LIVE trading not implemented - using PAPER mode")
        
        # Save trade to state
        await state_manager.save_trade(trade)
        
        # Update daily state
        state = await state_manager.get_daily_state()
        state.trade_count += 1
        state.trades.append(trade_id)
        await state_manager.save_daily_state(state)
        
        return trade
    
    async def execute_exit(
        self,
        trade: Trade,
        exit_price: float,
        reason: str
    ) -> Trade:
        """
        Execute an exit order.
        
        Args:
            trade: The trade to close
            exit_price: Exit price
            reason: Reason for exit (TP1, TP2, SL, TIME_STOP, etc.)
            
        Returns:
            Updated trade object
        """
        trade.exit_price = exit_price
        trade.exit_time = datetime.now()
        trade.exit_reason = reason
        trade.status = TradeStatus.CLOSED
        
        if self._mode == "PAPER":
            pnl_display = "+" if trade.pnl > 0 else ""
            logger.info(
                f"[PAPER] CLOSED {trade.direction.value} {trade.symbol} "
                f"@ ${exit_price:.2f} | PnL: {pnl_display}${trade.pnl:.2f} "
                f"({pnl_display}{trade.pnl_percent:.2f}%) | Reason: {reason}"
            )
        else:
            # Live execution placeholder
            logger.warning("LIVE trading not implemented")
        
        # Save updated trade
        await state_manager.save_trade(trade)
        
        return trade
    
    async def update_stop_loss(self, trade: Trade, new_stop: float) -> None:
        """Update the stop loss for an open trade."""
        old_stop = trade.stop_loss
        trade.stop_loss = new_stop
        
        logger.info(
            f"Stop updated for {trade.id}: ${old_stop:.2f} -> ${new_stop:.2f}"
        )
        
        await state_manager.save_trade(trade)
    
    def check_stop_loss(self, trade: Trade, current_price: float) -> bool:
        """Check if stop loss is hit."""
        if trade.direction == Direction.LONG:
            return current_price <= trade.stop_loss
        else:
            return current_price >= trade.stop_loss
    
    def check_take_profit_1(self, trade: Trade, current_price: float) -> bool:
        """Check if TP1 is hit."""
        if trade.tp1_hit:
            return False  # Already hit
        
        if trade.direction == Direction.LONG:
            return current_price >= trade.tp1_price
        else:
            return current_price <= trade.tp1_price
    
    def check_take_profit_2(self, trade: Trade, current_price: float) -> bool:
        """Check if TP2 is hit."""
        if trade.tp2_price <= 0:
            return False
        
        if trade.direction == Direction.LONG:
            return current_price >= trade.tp2_price
        else:
            return current_price <= trade.tp2_price


# Global instance
order_manager = OrderManager()

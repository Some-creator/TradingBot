"""Risk management for the trading system."""

import logging
from datetime import datetime
from typing import Optional

from src.config import config
from src.models import EntrySignal, Trade, TradeDirection, DailyState
from src.state import StateManager

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Manages trading risk according to the system rules.

    Risk Rules:
    - Max 3 trades per day
    - Max 1.5% daily loss (lockout)
    - Max 0.5% loss per trade
    - 2 consecutive losses triggers pause
    - VIX explosion (>10%) triggers shutdown
    - Data lag > 60 seconds triggers shutdown
    """

    def __init__(self, state_manager: StateManager, account_equity: float = 100000.0):
        self.state_manager = state_manager
        self.account_equity = account_equity

    async def can_trade(self) -> tuple[bool, Optional[str]]:
        """
        Check if trading is allowed.

        Returns:
            Tuple of (can_trade, reason_if_blocked)
        """
        # Check lockout
        is_locked, lockout_reason = await self.state_manager.is_locked_out()
        if is_locked:
            return False, f"Locked out: {lockout_reason}"

        # Check trade count
        state = await self.state_manager.get_daily_state()

        if state.trade_count >= config.max_trades_per_day:
            return False, f"Max trades ({config.max_trades_per_day}) reached for today"

        # Check consecutive losses
        if state.consecutive_losses >= 2:
            return False, "2 consecutive losses - trading paused"

        # Check daily loss
        max_daily_loss = self.account_equity * config.max_daily_loss_pct / 100
        if abs(state.daily_pnl) >= max_daily_loss and state.daily_pnl < 0:
            await self.state_manager.lockout("Max daily loss reached")
            return False, "Max daily loss reached"

        return True, None

    async def validate_signal(
        self, signal: EntrySignal
    ) -> tuple[bool, Optional[str]]:
        """
        Validate an entry signal against risk rules.

        Returns:
            Tuple of (is_valid, reason_if_invalid)
        """
        # Check if we can trade
        can_trade, reason = await self.can_trade()
        if not can_trade:
            return False, reason

        # Calculate position risk
        risk_per_share = abs(signal.entry_price - signal.stop_loss)
        risk_pct = risk_per_share / signal.entry_price * 100

        # Check max trade risk
        if risk_pct > config.max_drawdown_per_trade_pct:
            return False, f"Risk per trade ({risk_pct:.2f}%) exceeds max ({config.max_drawdown_per_trade_pct}%)"

        # Validate stop loss is reasonable
        if signal.direction == TradeDirection.LONG:
            if signal.stop_loss >= signal.entry_price:
                return False, "Invalid stop loss: above entry for long"
            if signal.tp1_price <= signal.entry_price:
                return False, "Invalid TP1: below entry for long"
        else:
            if signal.stop_loss <= signal.entry_price:
                return False, "Invalid stop loss: below entry for short"
            if signal.tp1_price >= signal.entry_price:
                return False, "Invalid TP1: above entry for short"

        return True, None

    def calculate_position_size(
        self, signal: EntrySignal, max_risk_pct: float = None
    ) -> int:
        """
        Calculate position size based on risk.

        Uses fixed fractional position sizing:
        Position Size = (Account * Risk%) / (Entry - Stop)

        Args:
            signal: Entry signal with prices
            max_risk_pct: Max risk per trade (default from config)

        Returns:
            Number of shares to trade
        """
        max_risk_pct = max_risk_pct or config.max_drawdown_per_trade_pct
        max_risk_dollars = self.account_equity * max_risk_pct / 100

        risk_per_share = abs(signal.entry_price - signal.stop_loss)

        if risk_per_share <= 0:
            logger.error("Invalid risk per share")
            return 0

        position_size = int(max_risk_dollars / risk_per_share)

        # Cap position size to avoid excessive exposure
        max_position_value = self.account_equity * 0.25  # Max 25% of account
        max_shares = int(max_position_value / signal.entry_price)
        position_size = min(position_size, max_shares)

        # Minimum 1 share
        position_size = max(1, position_size)

        logger.info(
            f"Position size calculated: {position_size} shares "
            f"(${position_size * signal.entry_price:,.2f})"
        )

        return position_size

    async def record_trade_result(
        self, trade: Trade
    ) -> DailyState:
        """
        Record trade result and update risk metrics.

        Args:
            trade: Completed trade with PnL

        Returns:
            Updated daily state
        """
        if trade.pnl is None:
            logger.warning("Trade has no PnL recorded")
            return await self.state_manager.get_daily_state()

        # Update daily P&L
        state = await self.state_manager.update_daily_pnl(
            trade.pnl, trade.pnl_pct or 0
        )

        # Update consecutive losses
        if trade.pnl < 0:
            losses = await self.state_manager.record_loss()
            if losses >= 2:
                logger.warning(f"2 consecutive losses recorded - trading paused")
        else:
            await self.state_manager.reset_consecutive_losses()

        # Check for lockout conditions
        max_daily_loss = self.account_equity * config.max_daily_loss_pct / 100
        if state.daily_pnl <= -max_daily_loss:
            await self.state_manager.lockout(
                f"Daily loss limit reached: ${abs(state.daily_pnl):,.2f}"
            )

        return state

    async def check_emergency_shutdown(
        self, vix_explosion: bool, data_stale: bool
    ) -> tuple[bool, Optional[str]]:
        """
        Check for emergency shutdown conditions.

        Args:
            vix_explosion: True if VIX moved > 10% intraday
            data_stale: True if data lag > 60 seconds

        Returns:
            Tuple of (should_shutdown, reason)
        """
        if vix_explosion:
            await self.state_manager.lockout("VIX explosion detected (>10% move)")
            return True, "VIX explosion - model breakage risk"

        if data_stale:
            await self.state_manager.lockout("Data feed lag > 60 seconds")
            return True, "Data feed stale - execution risk"

        return False, None

    def get_risk_metrics(self, trade: Trade, current_price: float) -> dict:
        """
        Get current risk metrics for an open trade.

        Args:
            trade: Open trade
            current_price: Current market price

        Returns:
            Dictionary of risk metrics
        """
        # Calculate unrealized P&L
        if trade.direction == TradeDirection.LONG:
            unrealized_pnl = (current_price - trade.entry_price) * trade.quantity
            unrealized_pct = (current_price - trade.entry_price) / trade.entry_price * 100
            distance_to_stop = (current_price - trade.stop_loss) / current_price * 100
        else:
            unrealized_pnl = (trade.entry_price - current_price) * trade.quantity
            unrealized_pct = (trade.entry_price - current_price) / trade.entry_price * 100
            distance_to_stop = (trade.stop_loss - current_price) / current_price * 100

        # Calculate R-multiple (risk/reward)
        risk = abs(trade.entry_price - trade.stop_loss)
        current_reward = abs(current_price - trade.entry_price)
        r_multiple = current_reward / risk if risk > 0 else 0

        return {
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pct": unrealized_pct,
            "distance_to_stop_pct": distance_to_stop,
            "r_multiple": r_multiple,
            "position_value": current_price * trade.quantity,
            "at_risk": risk * trade.quantity,
        }

    async def get_daily_summary(self) -> dict:
        """Get summary of daily trading activity and risk."""
        state = await self.state_manager.get_daily_state()

        return {
            "date": state.date,
            "trades_taken": state.trade_count,
            "trades_remaining": config.max_trades_per_day - state.trade_count,
            "daily_pnl": state.daily_pnl,
            "daily_pnl_pct": state.daily_pnl_pct,
            "consecutive_losses": state.consecutive_losses,
            "is_locked_out": state.is_locked_out,
            "lockout_reason": state.lockout_reason,
            "max_daily_loss": self.account_equity * config.max_daily_loss_pct / 100,
            "remaining_risk_budget": (
                self.account_equity * config.max_daily_loss_pct / 100 + state.daily_pnl
            ),
        }

    def calculate_partial_exit_size(
        self, total_quantity: int, exit_pct: float = 50.0
    ) -> int:
        """
        Calculate size for partial exit (e.g., at TP1).

        Args:
            total_quantity: Total position size
            exit_pct: Percentage to exit (default 50%)

        Returns:
            Number of shares to exit
        """
        exit_qty = int(total_quantity * exit_pct / 100)
        # Ensure we exit at least 1 share, and leave at least 1
        return max(1, min(exit_qty, total_quantity - 1))

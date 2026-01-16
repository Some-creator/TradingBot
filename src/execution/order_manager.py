"""Order management for executing trades."""

import logging
import uuid
from datetime import datetime
from typing import Optional, Callable, Awaitable

from src.config import config
from src.models import (
    Trade,
    TradeStatus,
    TradeDirection,
    EntrySignal,
)
from src.state import StateManager

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Manages order execution and trade lifecycle.

    In PAPER mode: Simulates order execution
    In LIVE mode: Integrates with broker API (placeholder)
    """

    def __init__(
        self,
        state_manager: StateManager,
        trading_mode: str = None,
        broker_api_key: str = None,
    ):
        self.state_manager = state_manager
        self.trading_mode = trading_mode or config.trading_mode
        self.broker_api_key = broker_api_key or config.broker_api_key

        # Callbacks for order events
        self._on_fill: list[Callable[[Trade], Awaitable[None]]] = []
        self._on_exit: list[Callable[[Trade], Awaitable[None]]] = []

    def register_fill_callback(
        self, callback: Callable[[Trade], Awaitable[None]]
    ) -> None:
        """Register callback for order fills."""
        self._on_fill.append(callback)

    def register_exit_callback(
        self, callback: Callable[[Trade], Awaitable[None]]
    ) -> None:
        """Register callback for trade exits."""
        self._on_exit.append(callback)

    async def execute_entry(
        self, signal: EntrySignal, quantity: int
    ) -> Optional[Trade]:
        """
        Execute an entry order based on signal.

        Args:
            signal: Entry signal with prices
            quantity: Position size

        Returns:
            Trade object if executed, None if failed
        """
        if quantity <= 0:
            logger.error("Invalid quantity for entry")
            return None

        trade_id = f"{signal.symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        if self.trading_mode == "PAPER":
            # Paper trading: simulate fill at signal price
            fill_price = signal.entry_price
            logger.info(
                f"[PAPER] Entry executed: {signal.direction.value.upper()} "
                f"{quantity} {signal.symbol} @ ${fill_price:.2f}"
            )
        else:
            # Live trading: place order with broker
            fill_price = await self._place_market_order(
                symbol=signal.symbol,
                quantity=quantity,
                side="BUY" if signal.direction == TradeDirection.LONG else "SELL",
            )
            if fill_price is None:
                logger.error("Live order execution failed")
                return None

        # Create trade record
        trade = Trade(
            id=trade_id,
            symbol=signal.symbol,
            direction=signal.direction,
            status=TradeStatus.OPEN,
            entry_time=datetime.now(),
            entry_price=fill_price,
            stop_loss=signal.stop_loss,
            tp1_price=signal.tp1_price,
            tp2_price=signal.tp2_price,
            quantity=quantity,
        )

        # Save to state
        await self.state_manager.save_trade(trade)

        # Increment trade count
        await self.state_manager.increment_trade_count()

        # Notify callbacks
        for callback in self._on_fill:
            try:
                await callback(trade)
            except Exception as e:
                logger.error(f"Fill callback error: {e}")

        logger.info(
            f"Trade opened: {trade.id} - {trade.direction.value} {trade.quantity} "
            f"{trade.symbol} @ ${trade.entry_price:.2f}"
        )

        return trade

    async def execute_exit(
        self,
        trade: Trade,
        exit_price: float,
        exit_reason: str,
        quantity: int = None,
    ) -> Trade:
        """
        Execute an exit order.

        Args:
            trade: Trade to exit
            exit_price: Price to exit at
            exit_reason: Reason for exit
            quantity: Partial exit quantity (None for full exit)

        Returns:
            Updated trade object
        """
        exit_quantity = quantity or trade.quantity

        if self.trading_mode == "PAPER":
            # Paper trading: simulate fill at exit price
            fill_price = exit_price
            logger.info(
                f"[PAPER] Exit executed: {exit_quantity} {trade.symbol} @ ${fill_price:.2f} "
                f"({exit_reason})"
            )
        else:
            # Live trading: place order with broker
            side = "SELL" if trade.direction == TradeDirection.LONG else "BUY"
            fill_price = await self._place_market_order(
                symbol=trade.symbol,
                quantity=exit_quantity,
                side=side,
            )
            if fill_price is None:
                logger.error("Live exit order failed")
                fill_price = exit_price  # Use target price as fallback

        # Calculate P&L
        if trade.direction == TradeDirection.LONG:
            pnl = (fill_price - trade.entry_price) * exit_quantity
            pnl_pct = (fill_price - trade.entry_price) / trade.entry_price * 100
        else:
            pnl = (trade.entry_price - fill_price) * exit_quantity
            pnl_pct = (trade.entry_price - fill_price) / trade.entry_price * 100

        # Handle partial vs full exit
        remaining_quantity = trade.quantity - exit_quantity

        if remaining_quantity > 0:
            # Partial exit
            trade.partial_exits.append({
                "timestamp": datetime.now().isoformat(),
                "quantity": exit_quantity,
                "price": fill_price,
                "reason": exit_reason,
                "pnl": pnl,
            })
            trade.quantity = remaining_quantity
            logger.info(
                f"Partial exit: {exit_quantity} shares @ ${fill_price:.2f} "
                f"(${pnl:+,.2f}, {remaining_quantity} remaining)"
            )
        else:
            # Full exit
            trade.status = (
                TradeStatus.STOPPED_OUT
                if exit_reason == "stop_loss"
                else TradeStatus.CLOSED
            )
            trade.exit_time = datetime.now()
            trade.exit_price = fill_price
            trade.pnl = pnl
            trade.pnl_pct = pnl_pct
            trade.exit_reason = exit_reason

            # Add partial exit P&L to total
            for partial in trade.partial_exits:
                trade.pnl += partial.get("pnl", 0)

            logger.info(
                f"Trade closed: {trade.id} - ${trade.pnl:+,.2f} ({trade.pnl_pct:+.2f}%) "
                f"[{exit_reason}]"
            )

        # Save updated trade
        await self.state_manager.save_trade(trade)

        # Notify callbacks for full exits
        if remaining_quantity <= 0:
            for callback in self._on_exit:
                try:
                    await callback(trade)
                except Exception as e:
                    logger.error(f"Exit callback error: {e}")

        return trade

    async def update_stop_loss(self, trade: Trade, new_stop: float) -> Trade:
        """
        Update stop loss for an open trade.

        Args:
            trade: Trade to update
            new_stop: New stop loss price

        Returns:
            Updated trade
        """
        old_stop = trade.stop_loss
        trade.stop_loss = new_stop

        await self.state_manager.save_trade(trade)

        logger.info(
            f"Stop loss updated: {trade.id} - ${old_stop:.2f} -> ${new_stop:.2f}"
        )

        return trade

    async def _place_market_order(
        self, symbol: str, quantity: int, side: str
    ) -> Optional[float]:
        """
        Place a market order with the broker.

        Args:
            symbol: Stock symbol
            quantity: Number of shares
            side: "BUY" or "SELL"

        Returns:
            Fill price or None if failed
        """
        # Placeholder for broker integration
        # In production, this would integrate with Alpaca, Interactive Brokers, etc.
        logger.warning(
            f"Live trading not implemented - would {side} {quantity} {symbol}"
        )

        # For now, return None to indicate failure
        # Actual implementation would:
        # 1. Connect to broker API
        # 2. Place market order
        # 3. Wait for fill confirmation
        # 4. Return fill price

        return None

    async def get_open_trades(self) -> list[Trade]:
        """Get all currently open trades."""
        return await self.state_manager.get_active_trades()

    async def cancel_all_pending(self) -> int:
        """
        Cancel all pending orders (not yet filled).

        Returns:
            Number of orders cancelled
        """
        trades = await self.state_manager.get_active_trades()
        cancelled = 0

        for trade in trades:
            if trade.status == TradeStatus.PENDING:
                trade.status = TradeStatus.CLOSED
                trade.exit_reason = "cancelled"
                await self.state_manager.save_trade(trade)
                cancelled += 1

        if cancelled > 0:
            logger.info(f"Cancelled {cancelled} pending orders")

        return cancelled

    async def close_all_positions(self, current_prices: dict[str, float]) -> int:
        """
        Emergency close all open positions.

        Args:
            current_prices: Current prices by symbol

        Returns:
            Number of positions closed
        """
        trades = await self.state_manager.get_active_trades()
        closed = 0

        for trade in trades:
            if trade.status == TradeStatus.OPEN:
                price = current_prices.get(trade.symbol)
                if price:
                    await self.execute_exit(trade, price, "emergency_close")
                    closed += 1

        if closed > 0:
            logger.warning(f"Emergency closed {closed} positions")

        return closed

    def get_position_summary(self, trades: list[Trade]) -> dict:
        """
        Get summary of current positions.

        Args:
            trades: List of open trades

        Returns:
            Position summary dictionary
        """
        long_exposure = 0.0
        short_exposure = 0.0
        total_at_risk = 0.0

        for trade in trades:
            if trade.status != TradeStatus.OPEN:
                continue

            position_value = trade.entry_price * trade.quantity
            risk = abs(trade.entry_price - trade.stop_loss) * trade.quantity

            if trade.direction == TradeDirection.LONG:
                long_exposure += position_value
            else:
                short_exposure += position_value

            total_at_risk += risk

        return {
            "open_positions": len([t for t in trades if t.status == TradeStatus.OPEN]),
            "long_exposure": long_exposure,
            "short_exposure": short_exposure,
            "net_exposure": long_exposure - short_exposure,
            "total_at_risk": total_at_risk,
        }

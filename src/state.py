"""State management with Redis for persistence."""

import json
import logging
from datetime import datetime
from typing import Optional

import redis.asyncio as redis

from src.config import config
from src.models import DailyState, Trade, FairValueGap, GammaLevels

logger = logging.getLogger(__name__)


class StateManager:
    """Manages trading state with Redis persistence."""

    def __init__(self, redis_url: str = None):
        self.redis_url = redis_url or config.redis_url
        self._redis: Optional[redis.Redis] = None
        self._local_state: dict = {}  # Fallback for when Redis is unavailable

    async def connect(self) -> None:
        """Connect to Redis."""
        try:
            self._redis = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await self._redis.ping()
            logger.info("Connected to Redis")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Using local state.")
            self._redis = None

    async def disconnect(self) -> None:
        """Disconnect from Redis."""
        if self._redis:
            await self._redis.close()
            logger.info("Disconnected from Redis")

    async def _get(self, key: str) -> Optional[str]:
        """Get value from Redis or local state."""
        if self._redis:
            try:
                return await self._redis.get(key)
            except Exception as e:
                logger.error(f"Redis get error: {e}")
        return self._local_state.get(key)

    async def _set(self, key: str, value: str, ex: int = None) -> bool:
        """Set value in Redis or local state."""
        if self._redis:
            try:
                await self._redis.set(key, value, ex=ex)
                return True
            except Exception as e:
                logger.error(f"Redis set error: {e}")
        self._local_state[key] = value
        return True

    async def _delete(self, key: str) -> bool:
        """Delete key from Redis or local state."""
        if self._redis:
            try:
                await self._redis.delete(key)
                return True
            except Exception as e:
                logger.error(f"Redis delete error: {e}")
        self._local_state.pop(key, None)
        return True

    async def _lpush(self, key: str, value: str) -> bool:
        """Push to list in Redis or local state."""
        if self._redis:
            try:
                await self._redis.lpush(key, value)
                return True
            except Exception as e:
                logger.error(f"Redis lpush error: {e}")
        if key not in self._local_state:
            self._local_state[key] = []
        self._local_state[key].insert(0, value)
        return True

    async def _lrange(self, key: str, start: int, end: int) -> list:
        """Get range from list in Redis or local state."""
        if self._redis:
            try:
                return await self._redis.lrange(key, start, end)
            except Exception as e:
                logger.error(f"Redis lrange error: {e}")
        return self._local_state.get(key, [])[start:end + 1 if end != -1 else None]

    # Daily State Management
    def _daily_state_key(self, date: str = None) -> str:
        """Get Redis key for daily state."""
        date = date or datetime.now().strftime("%Y-%m-%d")
        return f"daily_state:{date}"

    async def get_daily_state(self, date: str = None) -> DailyState:
        """Get or create daily state."""
        date = date or datetime.now().strftime("%Y-%m-%d")
        key = self._daily_state_key(date)
        data = await self._get(key)

        if data:
            return DailyState.from_dict(json.loads(data))

        # Create new daily state
        state = DailyState(date=date)
        await self.save_daily_state(state)
        return state

    async def save_daily_state(self, state: DailyState) -> bool:
        """Save daily state."""
        key = self._daily_state_key(state.date)
        # Expire after 7 days
        return await self._set(key, json.dumps(state.to_dict()), ex=604800)

    # Trade Management
    def _trades_key(self, date: str = None) -> str:
        """Get Redis key for trades list."""
        date = date or datetime.now().strftime("%Y-%m-%d")
        return f"trades:{date}"

    def _active_trade_key(self, trade_id: str) -> str:
        """Get Redis key for active trade."""
        return f"active_trade:{trade_id}"

    async def save_trade(self, trade: Trade) -> bool:
        """Save a trade."""
        # Save to active trades if open
        if trade.status.value in ("pending", "open"):
            await self._set(
                self._active_trade_key(trade.id),
                json.dumps(trade.to_dict()),
            )
        else:
            # Remove from active trades
            await self._delete(self._active_trade_key(trade.id))

        # Add to daily trades list
        date = trade.entry_time.strftime("%Y-%m-%d")
        await self._lpush(self._trades_key(date), json.dumps(trade.to_dict()))
        return True

    async def get_active_trades(self) -> list[Trade]:
        """Get all active trades."""
        if self._redis:
            try:
                keys = await self._redis.keys("active_trade:*")
                trades = []
                for key in keys:
                    data = await self._redis.get(key)
                    if data:
                        trades.append(Trade.from_dict(json.loads(data)))
                return trades
            except Exception as e:
                logger.error(f"Error getting active trades: {e}")

        # Fallback to local state
        trades = []
        for key, value in self._local_state.items():
            if key.startswith("active_trade:"):
                trades.append(Trade.from_dict(json.loads(value)))
        return trades

    async def get_daily_trades(self, date: str = None) -> list[Trade]:
        """Get all trades for a day."""
        date = date or datetime.now().strftime("%Y-%m-%d")
        data = await self._lrange(self._trades_key(date), 0, -1)
        return [Trade.from_dict(json.loads(item)) for item in data]

    # FVG Management
    def _fvg_key(self, symbol: str) -> str:
        """Get Redis key for FVGs."""
        return f"fvgs:{symbol}"

    async def save_fvgs(self, symbol: str, fvgs: list[FairValueGap]) -> bool:
        """Save FVGs for a symbol."""
        key = self._fvg_key(symbol)
        data = json.dumps([fvg.to_dict() for fvg in fvgs])
        return await self._set(key, data, ex=28800)  # 8 hours expiry

    async def get_fvgs(self, symbol: str) -> list[FairValueGap]:
        """Get FVGs for a symbol."""
        key = self._fvg_key(symbol)
        data = await self._get(key)
        if data:
            return [FairValueGap.from_dict(item) for item in json.loads(data)]
        return []

    # Gamma Levels Management
    def _gamma_key(self, symbol: str) -> str:
        """Get Redis key for gamma levels."""
        return f"gamma:{symbol}"

    async def save_gamma_levels(self, levels: GammaLevels) -> bool:
        """Save gamma levels for a symbol."""
        key = self._gamma_key(levels.symbol)
        return await self._set(key, json.dumps(levels.to_dict()), ex=28800)

    async def get_gamma_levels(self, symbol: str) -> Optional[GammaLevels]:
        """Get gamma levels for a symbol."""
        key = self._gamma_key(symbol)
        data = await self._get(key)
        if data:
            return GammaLevels.from_dict(json.loads(data))
        return None

    # Risk Management Helpers
    async def increment_trade_count(self) -> int:
        """Increment and return trade count for today."""
        state = await self.get_daily_state()
        state.trade_count += 1
        await self.save_daily_state(state)
        return state.trade_count

    async def update_daily_pnl(self, pnl: float, pnl_pct: float) -> DailyState:
        """Update daily P&L."""
        state = await self.get_daily_state()
        state.daily_pnl += pnl
        state.daily_pnl_pct += pnl_pct
        await self.save_daily_state(state)
        return state

    async def record_loss(self) -> int:
        """Record a loss and return consecutive loss count."""
        state = await self.get_daily_state()
        state.consecutive_losses += 1
        await self.save_daily_state(state)
        return state.consecutive_losses

    async def reset_consecutive_losses(self) -> None:
        """Reset consecutive losses after a win."""
        state = await self.get_daily_state()
        state.consecutive_losses = 0
        await self.save_daily_state(state)

    async def lockout(self, reason: str) -> None:
        """Lock out trading for the day."""
        state = await self.get_daily_state()
        state.is_locked_out = True
        state.lockout_reason = reason
        await self.save_daily_state(state)
        logger.warning(f"Trading locked out: {reason}")

    async def is_locked_out(self) -> tuple[bool, Optional[str]]:
        """Check if trading is locked out."""
        state = await self.get_daily_state()
        return state.is_locked_out, state.lockout_reason


# Global state manager instance
state_manager = StateManager()

"""
State persistence layer using Redis.
Handles trade state, daily stats, and FVG storage.
Falls back to in-memory storage if Redis is unavailable.
"""

import json
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from loguru import logger

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

from src.config import api
from src.models import DailyState, Trade, FVG, TradeStatus, FVGStatus, FVGType, Direction


class InMemoryStorage:
    """Simple in-memory storage fallback."""
    
    def __init__(self):
        self._data: Dict[str, Dict[str, str]] = {}
        self._sets: Dict[str, set] = {}
    
    async def ping(self):
        return True
    
    async def close(self):
        pass
    
    async def hgetall(self, key: str) -> Dict[str, str]:
        return self._data.get(key, {})
    
    async def hset(self, key: str, mapping: Dict[str, str] = None, **kwargs):
        if key not in self._data:
            self._data[key] = {}
        if mapping:
            self._data[key].update(mapping)
        self._data[key].update(kwargs)
    
    async def hget(self, key: str, field: str) -> Optional[str]:
        return self._data.get(key, {}).get(field)
    
    async def delete(self, key: str):
        self._data.pop(key, None)
    
    async def expire(self, key: str, seconds: int):
        pass  # No-op for in-memory
    
    async def sadd(self, key: str, value: str):
        if key not in self._sets:
            self._sets[key] = set()
        self._sets[key].add(value)
    
    async def smembers(self, key: str) -> set:
        return self._sets.get(key, set())


class StateManager:
    """Manages persistent state via Redis or in-memory fallback."""
    
    def __init__(self):
        self._redis = None
        self._using_memory = False
    
    async def connect(self) -> None:
        """Initialize Redis connection or fall back to in-memory."""
        if REDIS_AVAILABLE:
            try:
                self._redis = redis.from_url(api.redis_url, decode_responses=True)
                await self._redis.ping()
                logger.info("Connected to Redis")
                return
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}")
        
        # Fall back to in-memory storage
        logger.warning("Using in-memory storage (state will not persist across restarts)")
        self._redis = InMemoryStorage()
        self._using_memory = True
    
    async def disconnect(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
    
    def _today_key(self) -> str:
        """Get today's date key."""
        return date.today().isoformat()
    
    # ========== Daily State ==========
    
    async def get_daily_state(self) -> DailyState:
        """Get or create today's trading state."""
        key = f"daily:{self._today_key()}"
        data = await self._redis.hgetall(key)
        
        if not data:
            return DailyState(date=self._today_key())
        
        return DailyState(
            date=data.get("date", self._today_key()),
            trade_count=int(data.get("trade_count", 0)),
            consecutive_losses=int(data.get("consecutive_losses", 0)),
            daily_pnl_percent=float(data.get("daily_pnl_percent", 0.0)),
            is_locked=data.get("is_locked", "false").lower() == "true",
            lock_reason=data.get("lock_reason", ""),
            trades=json.loads(data.get("trades", "[]"))
        )
    
    async def save_daily_state(self, state: DailyState) -> None:
        """Save daily state to Redis."""
        key = f"daily:{state.date}"
        data = {
            "date": state.date,
            "trade_count": str(state.trade_count),
            "consecutive_losses": str(state.consecutive_losses),
            "daily_pnl_percent": str(state.daily_pnl_percent),
            "is_locked": str(state.is_locked).lower(),
            "lock_reason": state.lock_reason,
            "trades": json.dumps(state.trades)
        }
        await self._redis.hset(key, mapping=data)
        await self._redis.expire(key, 7 * 24 * 3600)
    
    async def increment_trade_count(self) -> int:
        """Increment and return new trade count."""
        state = await self.get_daily_state()
        state.trade_count += 1
        await self.save_daily_state(state)
        return state.trade_count
    
    async def lock_trading(self, reason: str) -> None:
        """Lock trading for the day."""
        state = await self.get_daily_state()
        state.is_locked = True
        state.lock_reason = reason
        await self.save_daily_state(state)
    
    async def is_trading_locked(self) -> tuple[bool, str]:
        """Check if trading is locked."""
        state = await self.get_daily_state()
        return state.is_locked, state.lock_reason
    
    # ========== Trades ==========
    
    async def save_trade(self, trade: Trade) -> None:
        """Save a trade to Redis."""
        key = f"trade:{trade.id}"
        data = {
            "id": trade.id,
            "symbol": trade.symbol,
            "direction": trade.direction.value,
            "entry_price": str(trade.entry_price),
            "entry_time": trade.entry_time.isoformat(),
            "quantity": str(trade.quantity),
            "status": trade.status.value,
            "stop_loss": str(trade.stop_loss),
            "original_stop_loss": str(trade.original_stop_loss),
            "tp1_price": str(trade.tp1_price),
            "tp2_price": str(trade.tp2_price),
            "tp1_hit": str(trade.tp1_hit).lower(),
            "exit_price": str(trade.exit_price) if trade.exit_price else "",
            "exit_time": trade.exit_time.isoformat() if trade.exit_time else "",
            "exit_reason": trade.exit_reason,
            "trigger_type": trade.trigger_type,
            "sweep_candle_low": str(trade.sweep_candle_low),
            "sweep_candle_high": str(trade.sweep_candle_high)
        }
        await self._redis.hset(key, mapping=data)
        await self._redis.expire(key, 7 * 24 * 3600)
    
    async def get_trade(self, trade_id: str) -> Optional[Trade]:
        """Get a trade by ID."""
        key = f"trade:{trade_id}"
        data = await self._redis.hgetall(key)
        
        if not data:
            return None
        
        return Trade(
            id=data["id"],
            symbol=data["symbol"],
            direction=Direction(data["direction"]),
            entry_price=float(data["entry_price"]),
            entry_time=datetime.fromisoformat(data["entry_time"]),
            quantity=float(data["quantity"]),
            status=TradeStatus(data["status"]),
            stop_loss=float(data["stop_loss"]),
            original_stop_loss=float(data["original_stop_loss"]),
            tp1_price=float(data["tp1_price"]),
            tp2_price=float(data["tp2_price"]),
            tp1_hit=data["tp1_hit"] == "true",
            exit_price=float(data["exit_price"]) if data["exit_price"] else None,
            exit_time=datetime.fromisoformat(data["exit_time"]) if data["exit_time"] else None,
            exit_reason=data["exit_reason"],
            trigger_type=data["trigger_type"],
            sweep_candle_low=float(data["sweep_candle_low"]),
            sweep_candle_high=float(data["sweep_candle_high"])
        )
    
    async def get_open_trades(self, symbol: str = None) -> List[Trade]:
        """Get all open trades, optionally filtered by symbol."""
        state = await self.get_daily_state()
        trades = []
        
        for trade_id in state.trades:
            trade = await self.get_trade(trade_id)
            if trade and trade.status == TradeStatus.OPEN:
                if symbol is None or trade.symbol == symbol:
                    trades.append(trade)
        
        return trades
    
    # ========== FVGs ==========
    
    async def save_fvg(self, fvg: FVG) -> None:
        """Save an FVG to Redis."""
        key = f"fvg:{fvg.symbol}:{fvg.id}"
        data = {
            "id": fvg.id,
            "created_at": fvg.created_at.isoformat(),
            "top": str(fvg.top),
            "bottom": str(fvg.bottom),
            "fvg_type": fvg.fvg_type.value,
            "status": fvg.status.value,
            "symbol": fvg.symbol
        }
        await self._redis.hset(key, mapping=data)
        await self._redis.sadd(f"fvgs:{fvg.symbol}", fvg.id)
        await self._redis.expire(key, 4 * 3600)
    
    async def get_fvgs(self, symbol: str) -> List[FVG]:
        """Get all FVGs for a symbol."""
        fvg_ids = await self._redis.smembers(f"fvgs:{symbol}")
        fvgs = []
        
        for fvg_id in fvg_ids:
            key = f"fvg:{symbol}:{fvg_id}"
            data = await self._redis.hgetall(key)
            
            if data:
                fvgs.append(FVG(
                    id=data["id"],
                    created_at=datetime.fromisoformat(data["created_at"]),
                    top=float(data["top"]),
                    bottom=float(data["bottom"]),
                    fvg_type=FVGType(data["fvg_type"]),
                    status=FVGStatus(data["status"]),
                    symbol=data["symbol"]
                ))
        
        return fvgs
    
    async def update_fvg_status(self, fvg_id: str, symbol: str, new_status: FVGStatus, new_type: FVGType = None) -> None:
        """Update FVG status (e.g., mark as inverted)."""
        key = f"fvg:{symbol}:{fvg_id}"
        await self._redis.hset(key, "status", new_status.value)
        if new_type:
            await self._redis.hset(key, "fvg_type", new_type.value)
    
    async def clear_symbol_fvgs(self, symbol: str) -> None:
        """Clear all FVGs for a symbol (end of day)."""
        fvg_ids = await self._redis.smembers(f"fvgs:{symbol}")
        for fvg_id in fvg_ids:
            await self._redis.delete(f"fvg:{symbol}:{fvg_id}")
        await self._redis.delete(f"fvgs:{symbol}")


# Global state manager instance
state_manager = StateManager()

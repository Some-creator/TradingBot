"""
Core data models for the trading bot.
Defines the structures for candles, FVGs, trades, and gamma levels.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List


class Direction(Enum):
    """Trading direction bias."""
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"
    NO_TRADE = "NO_TRADE"


class FVGType(Enum):
    """Fair Value Gap type."""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class FVGStatus(Enum):
    """Fair Value Gap lifecycle status."""
    OPEN = "OPEN"
    MITIGATED = "MITIGATED"
    INVERTED = "INVERTED"


class TradeStatus(Enum):
    """Trade lifecycle status."""
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass
class Candle:
    """OHLCV candle data."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    symbol: str = ""
    
    @property
    def is_bullish(self) -> bool:
        return self.close > self.open
    
    @property
    def is_bearish(self) -> bool:
        return self.close < self.open
    
    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)
    
    @property
    def range_size(self) -> float:
        return self.high - self.low


@dataclass
class FVG:
    """Fair Value Gap structure."""
    id: str  # timestamp-based unique ID
    created_at: datetime
    top: float
    bottom: float
    fvg_type: FVGType
    status: FVGStatus = FVGStatus.OPEN
    symbol: str = ""
    
    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2
    
    @property
    def size(self) -> float:
        return self.top - self.bottom
    
    def contains_price(self, price: float) -> bool:
        """Check if price is within the gap."""
        return self.bottom <= price <= self.top
    
    def is_above_price(self, price: float) -> bool:
        """Check if gap is entirely above price."""
        return self.bottom > price
    
    def is_below_price(self, price: float) -> bool:
        """Check if gap is entirely below price."""
        return self.top < price


@dataclass
class GammaLevel:
    """A gamma/options-derived price level."""
    price: float
    level_type: str  # "PUT_WALL", "CALL_WALL", "ZERO_GAMMA", "HIGH_POS_GAMMA", "HIGH_NEG_GAMMA"
    strength: float  # Relative OI or gamma concentration
    zone_top: float = 0.0
    zone_bottom: float = 0.0
    
    def __post_init__(self):
        # Auto-calculate zone if not provided (0.15% width)
        if self.zone_top == 0.0 and self.zone_bottom == 0.0:
            zone_width = self.price * 0.0015
            self.zone_top = self.price + zone_width
            self.zone_bottom = self.price - zone_width
    
    def price_in_zone(self, price: float) -> bool:
        """Check if price is within the level's zone."""
        return self.zone_bottom <= price <= self.zone_top


@dataclass
class Trade:
    """Represents an open or completed trade."""
    id: str
    symbol: str
    direction: Direction
    entry_price: float
    entry_time: datetime
    quantity: float
    status: TradeStatus = TradeStatus.PENDING
    
    # Stop Loss
    stop_loss: float = 0.0
    original_stop_loss: float = 0.0
    
    # Take Profit
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    tp1_hit: bool = False
    
    # Exit
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: str = ""
    
    # Metadata
    trigger_type: str = ""  # "SWEEP_RECLAIM" or "IFVG"
    sweep_candle_low: float = 0.0
    sweep_candle_high: float = 0.0
    
    @property
    def pnl(self) -> float:
        """Calculate P&L in price points."""
        if self.exit_price is None:
            return 0.0
        if self.direction == Direction.LONG:
            return self.exit_price - self.entry_price
        else:
            return self.entry_price - self.exit_price
    
    @property
    def pnl_percent(self) -> float:
        """Calculate P&L as percentage."""
        if self.exit_price is None or self.entry_price == 0:
            return 0.0
        return (self.pnl / self.entry_price) * 100


@dataclass
class DailyState:
    """Daily trading state for risk management."""
    date: str  # YYYY-MM-DD
    trade_count: int = 0
    consecutive_losses: int = 0
    daily_pnl_percent: float = 0.0
    is_locked: bool = False
    lock_reason: str = ""
    trades: List[str] = field(default_factory=list)  # List of trade IDs


@dataclass
class MarketBias:
    """Daily market bias determination."""
    date: str
    score: int  # -100 to +100
    direction: Direction
    rationale: str = ""
    vix_level: float = 0.0
    above_20ma: bool = True
    is_macro_event_day: bool = False

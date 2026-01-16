"""Data models for the trading system."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Bias(Enum):
    """Daily trading bias."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    NO_TRADE = "no_trade"


class FVGType(Enum):
    """Fair Value Gap type."""
    BULLISH = "bullish"
    BEARISH = "bearish"


class FVGStatus(Enum):
    """Fair Value Gap status."""
    OPEN = "open"
    MITIGATED = "mitigated"
    INVERTED = "inverted"


class TradeDirection(Enum):
    """Trade direction."""
    LONG = "long"
    SHORT = "short"


class SignalType(Enum):
    """Entry signal type."""
    SWEEP_RECLAIM = "sweep_reclaim"  # Variant A
    IFVG_FLIP = "ifvg_flip"  # Variant B (High Confidence)


class TradeStatus(Enum):
    """Trade status."""
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"
    STOPPED_OUT = "stopped_out"
    TIME_STOPPED = "time_stopped"


@dataclass
class FairValueGap:
    """Fair Value Gap (FVG) data structure."""
    id: str  # timestamp-based ID
    top: float
    bottom: float
    fvg_type: FVGType
    status: FVGStatus
    created_at: datetime
    symbol: str

    def contains_price(self, price: float) -> bool:
        """Check if price is within the gap."""
        return self.bottom <= price <= self.top

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "top": self.top,
            "bottom": self.bottom,
            "fvg_type": self.fvg_type.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "symbol": self.symbol,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FairValueGap":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            top=data["top"],
            bottom=data["bottom"],
            fvg_type=FVGType(data["fvg_type"]),
            status=FVGStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            symbol=data["symbol"],
        )


@dataclass
class GammaLevels:
    """Gamma exposure levels from options data."""
    symbol: str
    timestamp: datetime
    call_wall: float  # Strike with max Call OI (Resistance)
    put_wall: float  # Strike with max Put OI (Support)
    zero_gamma: float  # Gamma flip point
    net_gex: float  # Net gamma exposure (positive = mean reversion, negative = trend)
    vol_trigger: Optional[float] = None

    def is_positive_gex(self) -> bool:
        """Check if in positive gamma environment (mean reversion)."""
        return self.net_gex > 0

    def get_call_wall_zone(self, zone_width_pct: float) -> tuple[float, float]:
        """Get the resistance zone around call wall."""
        width = self.call_wall * zone_width_pct / 100
        return (self.call_wall - width, self.call_wall + width)

    def get_put_wall_zone(self, zone_width_pct: float) -> tuple[float, float]:
        """Get the support zone around put wall."""
        width = self.put_wall * zone_width_pct / 100
        return (self.put_wall - width, self.put_wall + width)

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "call_wall": self.call_wall,
            "put_wall": self.put_wall,
            "zero_gamma": self.zero_gamma,
            "net_gex": self.net_gex,
            "vol_trigger": self.vol_trigger,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GammaLevels":
        """Create from dictionary."""
        return cls(
            symbol=data["symbol"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            call_wall=data["call_wall"],
            put_wall=data["put_wall"],
            zero_gamma=data["zero_gamma"],
            net_gex=data["net_gex"],
            vol_trigger=data.get("vol_trigger"),
        )


@dataclass
class SentimentScore:
    """Daily sentiment analysis result."""
    timestamp: datetime
    llm_score: int  # -100 to +100 from Claude
    trend_adjustment: int  # +/- 10 based on 20-day MA
    vix_bias: str  # "bullish", "bearish", or "neutral"
    final_score: int  # Combined score
    bias: Bias
    rationale: str
    is_macro_event_day: bool = False  # FOMC/CPI/NFP
    emergency_keywords_detected: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "llm_score": self.llm_score,
            "trend_adjustment": self.trend_adjustment,
            "vix_bias": self.vix_bias,
            "final_score": self.final_score,
            "bias": self.bias.value,
            "rationale": self.rationale,
            "is_macro_event_day": self.is_macro_event_day,
            "emergency_keywords_detected": self.emergency_keywords_detected,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SentimentScore":
        """Create from dictionary."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            llm_score=data["llm_score"],
            trend_adjustment=data["trend_adjustment"],
            vix_bias=data["vix_bias"],
            final_score=data["final_score"],
            bias=Bias(data["bias"]),
            rationale=data["rationale"],
            is_macro_event_day=data.get("is_macro_event_day", False),
            emergency_keywords_detected=data.get("emergency_keywords_detected", False),
        )


@dataclass
class PriceCandle:
    """OHLCV candle data."""
    timestamp: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int

    def is_bullish(self) -> bool:
        """Check if candle closed green."""
        return self.close > self.open

    def is_bearish(self) -> bool:
        """Check if candle closed red."""
        return self.close < self.open

    def body_size(self) -> float:
        """Get candle body size as percentage."""
        return abs(self.close - self.open) / self.open * 100

    def wick_low(self) -> float:
        """Get lower wick."""
        return min(self.open, self.close) - self.low

    def wick_high(self) -> float:
        """Get upper wick."""
        return self.high - max(self.open, self.close)


@dataclass
class EntrySignal:
    """Entry signal generated by the system."""
    timestamp: datetime
    symbol: str
    direction: TradeDirection
    signal_type: SignalType
    entry_price: float
    stop_loss: float
    tp1_price: float
    tp2_price: Optional[float]  # Gamma level target
    trigger_candle: PriceCandle
    gamma_level: str  # "put_wall", "call_wall", "zero_gamma"
    confidence: str  # "normal" or "high"
    ifvg: Optional[FairValueGap] = None


@dataclass
class Trade:
    """Active or completed trade."""
    id: str
    symbol: str
    direction: TradeDirection
    status: TradeStatus
    entry_time: datetime
    entry_price: float
    stop_loss: float
    tp1_price: float
    tp2_price: Optional[float]
    quantity: int
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    exit_reason: Optional[str] = None  # "tp1", "tp2", "stop_loss", "time_stop", "manual"
    partial_exits: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "status": self.status.value,
            "entry_time": self.entry_time.isoformat(),
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "tp1_price": self.tp1_price,
            "tp2_price": self.tp2_price,
            "quantity": self.quantity,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_price": self.exit_price,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "exit_reason": self.exit_reason,
            "partial_exits": self.partial_exits,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Trade":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            symbol=data["symbol"],
            direction=TradeDirection(data["direction"]),
            status=TradeStatus(data["status"]),
            entry_time=datetime.fromisoformat(data["entry_time"]),
            entry_price=data["entry_price"],
            stop_loss=data["stop_loss"],
            tp1_price=data["tp1_price"],
            tp2_price=data.get("tp2_price"),
            quantity=data["quantity"],
            exit_time=datetime.fromisoformat(data["exit_time"]) if data.get("exit_time") else None,
            exit_price=data.get("exit_price"),
            pnl=data.get("pnl"),
            pnl_pct=data.get("pnl_pct"),
            exit_reason=data.get("exit_reason"),
            partial_exits=data.get("partial_exits", []),
        )


@dataclass
class DailyState:
    """Daily trading state."""
    date: str  # YYYY-MM-DD
    trade_count: int = 0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    consecutive_losses: int = 0
    is_locked_out: bool = False
    lockout_reason: Optional[str] = None
    sentiment: Optional[SentimentScore] = None
    gamma_levels: dict = field(default_factory=dict)  # symbol -> GammaLevels

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "date": self.date,
            "trade_count": self.trade_count,
            "daily_pnl": self.daily_pnl,
            "daily_pnl_pct": self.daily_pnl_pct,
            "consecutive_losses": self.consecutive_losses,
            "is_locked_out": self.is_locked_out,
            "lockout_reason": self.lockout_reason,
            "sentiment": self.sentiment.to_dict() if self.sentiment else None,
            "gamma_levels": {k: v.to_dict() for k, v in self.gamma_levels.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DailyState":
        """Create from dictionary."""
        return cls(
            date=data["date"],
            trade_count=data.get("trade_count", 0),
            daily_pnl=data.get("daily_pnl", 0.0),
            daily_pnl_pct=data.get("daily_pnl_pct", 0.0),
            consecutive_losses=data.get("consecutive_losses", 0),
            is_locked_out=data.get("is_locked_out", False),
            lockout_reason=data.get("lockout_reason"),
            sentiment=SentimentScore.from_dict(data["sentiment"]) if data.get("sentiment") else None,
            gamma_levels={k: GammaLevels.from_dict(v) for k, v in data.get("gamma_levels", {}).items()},
        )

"""
Entry and Exit Signal Generator.
Implements the Sweep & Reclaim and IFVG trigger logic.
"""

from datetime import datetime, timedelta
from typing import Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from loguru import logger

from src.models import (
    Candle, FVG, GammaLevel, Direction, FVGType, FVGStatus
)
from src.analysis.fvg_detector import fvg_detector
from src.config import trading


class SignalType(Enum):
    """Type of entry signal."""
    SWEEP_RECLAIM = "SWEEP_RECLAIM"
    IFVG = "IFVG"


@dataclass
class EntrySignal:
    """Entry signal details."""
    direction: Direction
    signal_type: SignalType
    entry_price: float
    stop_loss: float
    tp1_price: float
    tp2_price: Optional[float]
    sweep_candle: Candle
    trigger_level: GammaLevel
    ifvg: Optional[FVG] = None
    confidence: str = "NORMAL"  # NORMAL or HIGH


class SignalGenerator:
    """Generates entry and exit signals based on price action at gamma levels."""
    
    def __init__(self):
        self._last_signal_time: Optional[datetime] = None
        self._min_signal_interval = timedelta(minutes=5)  # Avoid rapid signals
    
    def check_entry_conditions(
        self,
        candles: list[Candle],
        gamma_levels: list[GammaLevel],
        fvgs: list[FVG],
        bias_direction: Direction
    ) -> Optional[EntrySignal]:
        """
        Check for entry signals.
        
        Logic:
        1. Price must be at a gamma level zone
        2. Must have sweep (wick into zone) + reclaim (close back)
        3. OR IFVG formation at the level
        """
        if len(candles) < 3:
            return None
        
        if bias_direction == Direction.NO_TRADE:
            return None
        
        current_candle = candles[-1]
        prev_candle = candles[-2]
        current_price = current_candle.close
        
        # Avoid rapid signals
        if self._last_signal_time:
            if datetime.now() - self._last_signal_time < self._min_signal_interval:
                return None
        
        # Check for LONG setup at Put Wall (Support)
        if bias_direction in [Direction.LONG, Direction.NEUTRAL]:
            signal = self._check_long_setup(
                candles, gamma_levels, fvgs, current_price
            )
            if signal:
                return signal
        
        # Check for SHORT setup at Call Wall (Resistance)
        if bias_direction in [Direction.SHORT, Direction.NEUTRAL]:
            signal = self._check_short_setup(
                candles, gamma_levels, fvgs, current_price
            )
            if signal:
                return signal
        
        return None
    
    def _check_long_setup(
        self,
        candles: list[Candle],
        gamma_levels: list[GammaLevel],
        fvgs: list[FVG],
        current_price: float
    ) -> Optional[EntrySignal]:
        """Check for bullish entry at Put Wall."""
        
        # Find Put Wall
        put_wall = None
        for level in gamma_levels:
            if level.level_type == "PUT_WALL":
                put_wall = level
                break
        
        if not put_wall:
            return None
        
        current_candle = candles[-1]
        prev_candle = candles[-2]
        
        # --- VARIANT A: Sweep & Reclaim ---
        # Sweep: Previous candle wicked into the zone (low touched zone)
        swept = prev_candle.low <= put_wall.zone_top and prev_candle.low >= put_wall.zone_bottom
        
        # Reclaim: Current candle closed above the zone
        reclaimed = current_candle.close > put_wall.zone_top
        
        if swept and reclaimed and current_candle.is_bullish:
            # Calculate stops and targets
            stop_loss = prev_candle.low - (current_price * 0.0001)  # Buffer
            
            # Max stop check
            stop_distance_pct = (current_price - stop_loss) / current_price * 100
            if stop_distance_pct > trading.max_stop_pct:
                stop_loss = current_price * (1 - trading.max_stop_pct / 100)
            
            tp1 = current_price * (1 + trading.tp1_pct / 100)
            
            # TP2: High Positive Gamma or Call Wall
            tp2 = None
            for level in gamma_levels:
                if level.level_type in ["HIGH_POS_GAMMA", "CALL_WALL"]:
                    if level.price > current_price:
                        tp2 = level.price
                        break
            
            self._last_signal_time = datetime.now()
            
            logger.info(f"LONG Signal (Sweep & Reclaim) at Put Wall {put_wall.price:.2f}")
            
            return EntrySignal(
                direction=Direction.LONG,
                signal_type=SignalType.SWEEP_RECLAIM,
                entry_price=current_price,
                stop_loss=stop_loss,
                tp1_price=tp1,
                tp2_price=tp2,
                sweep_candle=prev_candle,
                trigger_level=put_wall,
                confidence="NORMAL"
            )
        
        # --- VARIANT B: IFVG ---
        # Check if a Bearish FVG just inverted near the Put Wall
        for fvg in fvgs:
            if fvg.status == FVGStatus.INVERTED and fvg.fvg_type == FVGType.BULLISH:
                # Check if FVG is near the Put Wall
                if abs(fvg.midpoint - put_wall.price) <= put_wall.price * 0.005:  # 0.5%
                    # Check if current candle closed above the FVG (caused inversion)
                    if current_candle.close > fvg.top:
                        stop_loss = fvg.bottom - (current_price * 0.0001)
                        
                        stop_distance_pct = (current_price - stop_loss) / current_price * 100
                        if stop_distance_pct > trading.max_stop_pct:
                            stop_loss = current_price * (1 - trading.max_stop_pct / 100)
                        
                        tp1 = current_price * (1 + trading.tp1_pct / 100)
                        
                        tp2 = None
                        for level in gamma_levels:
                            if level.level_type in ["HIGH_POS_GAMMA", "CALL_WALL"]:
                                if level.price > current_price:
                                    tp2 = level.price
                                    break
                        
                        self._last_signal_time = datetime.now()
                        
                        logger.info(f"LONG Signal (IFVG) at Put Wall {put_wall.price:.2f}")
                        
                        return EntrySignal(
                            direction=Direction.LONG,
                            signal_type=SignalType.IFVG,
                            entry_price=current_price,
                            stop_loss=stop_loss,
                            tp1_price=tp1,
                            tp2_price=tp2,
                            sweep_candle=current_candle,
                            trigger_level=put_wall,
                            ifvg=fvg,
                            confidence="HIGH"
                        )
        
        return None
    
    def _check_short_setup(
        self,
        candles: list[Candle],
        gamma_levels: list[GammaLevel],
        fvgs: list[FVG],
        current_price: float
    ) -> Optional[EntrySignal]:
        """Check for bearish entry at Call Wall."""
        
        # Find Call Wall
        call_wall = None
        for level in gamma_levels:
            if level.level_type == "CALL_WALL":
                call_wall = level
                break
        
        if not call_wall:
            return None
        
        current_candle = candles[-1]
        prev_candle = candles[-2]
        
        # --- VARIANT A: Sweep & Reclaim ---
        swept = prev_candle.high >= call_wall.zone_bottom and prev_candle.high <= call_wall.zone_top
        reclaimed = current_candle.close < call_wall.zone_bottom
        
        if swept and reclaimed and current_candle.is_bearish:
            stop_loss = prev_candle.high + (current_price * 0.0001)
            
            stop_distance_pct = (stop_loss - current_price) / current_price * 100
            if stop_distance_pct > trading.max_stop_pct:
                stop_loss = current_price * (1 + trading.max_stop_pct / 100)
            
            tp1 = current_price * (1 - trading.tp1_pct / 100)
            
            tp2 = None
            for level in gamma_levels:
                if level.level_type == "PUT_WALL":
                    if level.price < current_price:
                        tp2 = level.price
                        break
            
            self._last_signal_time = datetime.now()
            
            logger.info(f"SHORT Signal (Sweep & Reclaim) at Call Wall {call_wall.price:.2f}")
            
            return EntrySignal(
                direction=Direction.SHORT,
                signal_type=SignalType.SWEEP_RECLAIM,
                entry_price=current_price,
                stop_loss=stop_loss,
                tp1_price=tp1,
                tp2_price=tp2,
                sweep_candle=prev_candle,
                trigger_level=call_wall,
                confidence="NORMAL"
            )
        
        # --- VARIANT B: IFVG ---
        for fvg in fvgs:
            if fvg.status == FVGStatus.INVERTED and fvg.fvg_type == FVGType.BEARISH:
                if abs(fvg.midpoint - call_wall.price) <= call_wall.price * 0.005:
                    if current_candle.close < fvg.bottom:
                        stop_loss = fvg.top + (current_price * 0.0001)
                        
                        stop_distance_pct = (stop_loss - current_price) / current_price * 100
                        if stop_distance_pct > trading.max_stop_pct:
                            stop_loss = current_price * (1 + trading.max_stop_pct / 100)
                        
                        tp1 = current_price * (1 - trading.tp1_pct / 100)
                        
                        tp2 = None
                        for level in gamma_levels:
                            if level.level_type == "PUT_WALL":
                                if level.price < current_price:
                                    tp2 = level.price
                                    break
                        
                        self._last_signal_time = datetime.now()
                        
                        logger.info(f"SHORT Signal (IFVG) at Call Wall {call_wall.price:.2f}")
                        
                        return EntrySignal(
                            direction=Direction.SHORT,
                            signal_type=SignalType.IFVG,
                            entry_price=current_price,
                            stop_loss=stop_loss,
                            tp1_price=tp1,
                            tp2_price=tp2,
                            sweep_candle=current_candle,
                            trigger_level=call_wall,
                            ifvg=fvg,
                            confidence="HIGH"
                        )
        
        return None


# Global instance
signal_generator = SignalGenerator()

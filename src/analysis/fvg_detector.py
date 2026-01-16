"""
FVG (Fair Value Gap) Detection and Management.
Implements the 3-candle pattern detection and IFVG inversion logic.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from loguru import logger

from src.models import Candle, FVG, FVGType, FVGStatus
from src.config import trading


class FVGDetector:
    """Detects and manages Fair Value Gaps."""
    
    def __init__(self):
        self._active_fvgs: List[FVG] = []
    
    def detect_fvg(self, candles: List[Candle]) -> Optional[FVG]:
        """
        Detect FVG from the last 3 candles.
        
        Bullish FVG: Low[i-2] > High[i] (gap up)
        Bearish FVG: High[i-2] < Low[i] (gap down)
        
        Args:
            candles: List of at least 3 candles, most recent last
            
        Returns:
            FVG if detected, None otherwise
        """
        if len(candles) < 3:
            return None
        
        # candles[-3] = i-2 (oldest)
        # candles[-2] = i-1 (middle - the impulse candle)
        # candles[-1] = i   (newest)
        
        candle_old = candles[-3]
        candle_mid = candles[-2]
        candle_new = candles[-1]
        
        symbol = candle_new.symbol
        
        # Bullish FVG: Gap between candle_old's low and candle_new's high
        if candle_old.low > candle_new.high:
            gap_top = candle_old.low
            gap_bottom = candle_new.high
            
            # Minimum gap size filter (avoid tiny gaps)
            if (gap_top - gap_bottom) / candle_mid.close > 0.0005:  # 0.05% min
                fvg = FVG(
                    id=f"{int(candle_new.timestamp.timestamp())}",
                    created_at=candle_new.timestamp,
                    top=gap_top,
                    bottom=gap_bottom,
                    fvg_type=FVGType.BULLISH,
                    status=FVGStatus.OPEN,
                    symbol=symbol
                )
                logger.debug(f"Bullish FVG detected: {gap_bottom:.2f} - {gap_top:.2f}")
                return fvg
        
        # Bearish FVG: Gap between candle_old's high and candle_new's low
        if candle_old.high < candle_new.low:
            gap_top = candle_new.low
            gap_bottom = candle_old.high
            
            if (gap_top - gap_bottom) / candle_mid.close > 0.0005:
                fvg = FVG(
                    id=f"{int(candle_new.timestamp.timestamp())}",
                    created_at=candle_new.timestamp,
                    top=gap_top,
                    bottom=gap_bottom,
                    fvg_type=FVGType.BEARISH,
                    status=FVGStatus.OPEN,
                    symbol=symbol
                )
                logger.debug(f"Bearish FVG detected: {gap_bottom:.2f} - {gap_top:.2f}")
                return fvg
        
        return None
    
    def check_fvg_interaction(
        self, 
        fvg: FVG, 
        candle: Candle
    ) -> Tuple[bool, Optional[FVGStatus], Optional[FVGType]]:
        """
        Check how a candle interacts with an FVG.
        
        Returns:
            (has_interaction, new_status, new_type)
            - new_type is set if the FVG inverts
        """
        # No interaction if gap already inverted
        if fvg.status == FVGStatus.INVERTED:
            return False, None, None
        
        # Check if candle CLOSES through the FVG (Inversion)
        if fvg.fvg_type == FVGType.BEARISH:
            # Bearish FVG inverts if candle closes ABOVE the gap top
            if candle.close > fvg.top:
                logger.info(f"IFVG: Bearish FVG {fvg.id} inverted to Bullish Support")
                return True, FVGStatus.INVERTED, FVGType.BULLISH
        
        elif fvg.fvg_type == FVGType.BULLISH:
            # Bullish FVG inverts if candle closes BELOW the gap bottom
            if candle.close < fvg.bottom:
                logger.info(f"IFVG: Bullish FVG {fvg.id} inverted to Bearish Resistance")
                return True, FVGStatus.INVERTED, FVGType.BEARISH
        
        # Check if candle touches the FVG (Mitigation)
        if fvg.contains_price(candle.high) or fvg.contains_price(candle.low):
            if fvg.status == FVGStatus.OPEN:
                return True, FVGStatus.MITIGATED, None
        
        return False, None, None
    
    def find_nearby_fvg(
        self, 
        fvgs: List[FVG], 
        price: float, 
        fvg_type: FVGType = None,
        max_distance_pct: float = 0.5
    ) -> Optional[FVG]:
        """
        Find the nearest FVG to a price level.
        
        Args:
            fvgs: List of FVGs to search
            price: Current price
            fvg_type: Optional filter for FVG type
            max_distance_pct: Maximum distance as percentage of price
            
        Returns:
            Nearest FVG or None
        """
        max_distance = price * (max_distance_pct / 100)
        
        nearest: Optional[FVG] = None
        nearest_distance = float('inf')
        
        for fvg in fvgs:
            if fvg_type and fvg.fvg_type != fvg_type:
                continue
            
            # Distance to gap midpoint
            distance = abs(fvg.midpoint - price)
            
            if distance < nearest_distance and distance <= max_distance:
                nearest = fvg
                nearest_distance = distance
        
        return nearest
    
    def prune_old_fvgs(self, fvgs: List[FVG], max_age_minutes: int = None) -> List[FVG]:
        """Remove FVGs older than max_age_minutes."""
        if max_age_minutes is None:
            max_age_minutes = trading.fvg_max_age_minutes
        
        cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
        return [fvg for fvg in fvgs if fvg.created_at > cutoff]
    
    def get_inverted_fvgs(self, fvgs: List[FVG]) -> List[FVG]:
        """Get all inverted FVGs (IFVGs)."""
        return [fvg for fvg in fvgs if fvg.status == FVGStatus.INVERTED]


# Global instance
fvg_detector = FVGDetector()

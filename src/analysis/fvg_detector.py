"""Fair Value Gap (FVG) detection and management."""

import logging
from datetime import datetime, timedelta
from typing import Optional
from collections import deque

from src.config import config
from src.models import FairValueGap, FVGType, FVGStatus, PriceCandle

logger = logging.getLogger(__name__)


class FVGDetector:
    """
    Detects and manages Fair Value Gaps (FVGs) for entry signals.

    FVGs are 3-candle patterns indicating imbalance:
    - Bullish FVG: Low[i-2] > High[i] (gap up)
    - Bearish FVG: High[i-2] < Low[i] (gap down)

    IFVGs (Inverted FVGs) occur when price closes through an active FVG,
    flipping its bias.
    """

    def __init__(self, max_age_hours: int = None):
        self.max_age_hours = max_age_hours or config.fvg_max_age_hours
        # Store FVGs by symbol
        self._fvgs: dict[str, deque[FairValueGap]] = {}
        self._max_fvgs_per_symbol = 50

    def detect_fvg(
        self, candles: list[PriceCandle]
    ) -> Optional[FairValueGap]:
        """
        Detect if the last 3 candles form an FVG.

        Args:
            candles: List of at least 3 candles (oldest to newest)

        Returns:
            FairValueGap if detected, None otherwise
        """
        if len(candles) < 3:
            return None

        # Get the 3-candle pattern (i-2, i-1, i)
        c0 = candles[-3]  # i-2 (oldest)
        c1 = candles[-2]  # i-1 (middle)
        c2 = candles[-1]  # i (newest)

        symbol = c2.symbol

        # Check for Bullish FVG: Low[i-2] > High[i]
        if c0.low > c2.high:
            gap_top = c0.low
            gap_bottom = c2.high

            fvg = FairValueGap(
                id=f"{symbol}_{c2.timestamp.isoformat()}",
                top=gap_top,
                bottom=gap_bottom,
                fvg_type=FVGType.BULLISH,
                status=FVGStatus.OPEN,
                created_at=c2.timestamp,
                symbol=symbol,
            )
            logger.info(
                f"Bullish FVG detected: {symbol} ${gap_bottom:.2f}-${gap_top:.2f}"
            )
            self._add_fvg(fvg)
            return fvg

        # Check for Bearish FVG: High[i-2] < Low[i]
        if c0.high < c2.low:
            gap_top = c2.low
            gap_bottom = c0.high

            fvg = FairValueGap(
                id=f"{symbol}_{c2.timestamp.isoformat()}",
                top=gap_top,
                bottom=gap_bottom,
                fvg_type=FVGType.BEARISH,
                status=FVGStatus.OPEN,
                created_at=c2.timestamp,
                symbol=symbol,
            )
            logger.info(
                f"Bearish FVG detected: {symbol} ${gap_bottom:.2f}-${gap_top:.2f}"
            )
            self._add_fvg(fvg)
            return fvg

        return None

    def _add_fvg(self, fvg: FairValueGap) -> None:
        """Add FVG to storage."""
        if fvg.symbol not in self._fvgs:
            self._fvgs[fvg.symbol] = deque(maxlen=self._max_fvgs_per_symbol)
        self._fvgs[fvg.symbol].append(fvg)

    def check_fvg_interaction(
        self, symbol: str, candle: PriceCandle
    ) -> list[tuple[FairValueGap, str]]:
        """
        Check if candle interacts with any active FVGs.

        Returns:
            List of (FVG, interaction_type) tuples
            interaction_type: "mitigated", "inverted"
        """
        if symbol not in self._fvgs:
            return []

        interactions = []
        fvgs_to_check = list(self._fvgs[symbol])

        for fvg in fvgs_to_check:
            if fvg.status == FVGStatus.OPEN:
                # Check for inversion (close through the gap)
                if fvg.fvg_type == FVGType.BEARISH:
                    # Bearish FVG inverts if price closes ABOVE the gap
                    if candle.close > fvg.top:
                        fvg.status = FVGStatus.INVERTED
                        fvg.fvg_type = FVGType.BULLISH  # Flip to bullish support
                        interactions.append((fvg, "inverted"))
                        logger.info(
                            f"Bearish FVG inverted to Bullish: {fvg.bottom:.2f}-{fvg.top:.2f}"
                        )

                elif fvg.fvg_type == FVGType.BULLISH:
                    # Bullish FVG inverts if price closes BELOW the gap
                    if candle.close < fvg.bottom:
                        fvg.status = FVGStatus.INVERTED
                        fvg.fvg_type = FVGType.BEARISH  # Flip to bearish resistance
                        interactions.append((fvg, "inverted"))
                        logger.info(
                            f"Bullish FVG inverted to Bearish: {fvg.bottom:.2f}-{fvg.top:.2f}"
                        )

                # Check for mitigation (price touches the gap)
                elif fvg.contains_price(candle.low) or fvg.contains_price(candle.high):
                    if fvg.status == FVGStatus.OPEN:
                        fvg.status = FVGStatus.MITIGATED
                        interactions.append((fvg, "mitigated"))
                        logger.debug(f"FVG mitigated: {fvg.bottom:.2f}-{fvg.top:.2f}")

        return interactions

    def get_active_fvgs(
        self, symbol: str, fvg_type: FVGType = None
    ) -> list[FairValueGap]:
        """
        Get active (open or mitigated) FVGs for a symbol.

        Args:
            symbol: Stock symbol
            fvg_type: Optional filter by type

        Returns:
            List of active FVGs
        """
        if symbol not in self._fvgs:
            return []

        fvgs = [
            fvg
            for fvg in self._fvgs[symbol]
            if fvg.status in (FVGStatus.OPEN, FVGStatus.MITIGATED)
        ]

        if fvg_type:
            fvgs = [fvg for fvg in fvgs if fvg.fvg_type == fvg_type]

        return fvgs

    def get_inverted_fvgs(self, symbol: str) -> list[FairValueGap]:
        """Get IFVGs (inverted FVGs) for a symbol."""
        if symbol not in self._fvgs:
            return []

        return [
            fvg for fvg in self._fvgs[symbol] if fvg.status == FVGStatus.INVERTED
        ]

    def find_nearest_fvg(
        self,
        symbol: str,
        price: float,
        fvg_type: FVGType = None,
        include_inverted: bool = True,
    ) -> Optional[FairValueGap]:
        """
        Find the nearest FVG to current price.

        Args:
            symbol: Stock symbol
            price: Current price
            fvg_type: Optional filter by type
            include_inverted: Whether to include IFVGs

        Returns:
            Nearest FVG or None
        """
        if symbol not in self._fvgs:
            return None

        fvgs = list(self._fvgs[symbol])

        # Filter by status
        valid_statuses = [FVGStatus.OPEN, FVGStatus.MITIGATED]
        if include_inverted:
            valid_statuses.append(FVGStatus.INVERTED)

        fvgs = [fvg for fvg in fvgs if fvg.status in valid_statuses]

        # Filter by type
        if fvg_type:
            fvgs = [fvg for fvg in fvgs if fvg.fvg_type == fvg_type]

        if not fvgs:
            return None

        # Find nearest by midpoint distance
        def distance(fvg):
            midpoint = (fvg.top + fvg.bottom) / 2
            return abs(price - midpoint)

        return min(fvgs, key=distance)

    def find_fvg_at_level(
        self,
        symbol: str,
        level: float,
        tolerance_pct: float = 0.3,
    ) -> Optional[FairValueGap]:
        """
        Find an FVG near a specific price level (e.g., gamma level).

        Args:
            symbol: Stock symbol
            level: Price level to search near
            tolerance_pct: Percentage tolerance for matching

        Returns:
            FVG near the level or None
        """
        if symbol not in self._fvgs:
            return None

        tolerance = level * tolerance_pct / 100

        for fvg in self._fvgs[symbol]:
            if fvg.status in (FVGStatus.OPEN, FVGStatus.MITIGATED):
                # Check if FVG overlaps with the level tolerance zone
                if fvg.bottom <= level + tolerance and fvg.top >= level - tolerance:
                    return fvg

        return None

    def prune_old_fvgs(self) -> int:
        """
        Remove FVGs older than max_age_hours.

        Returns:
            Number of FVGs pruned
        """
        pruned = 0
        cutoff = datetime.now() - timedelta(hours=self.max_age_hours)

        for symbol in self._fvgs:
            original_len = len(self._fvgs[symbol])
            self._fvgs[symbol] = deque(
                (fvg for fvg in self._fvgs[symbol] if fvg.created_at > cutoff),
                maxlen=self._max_fvgs_per_symbol,
            )
            pruned += original_len - len(self._fvgs[symbol])

        if pruned > 0:
            logger.debug(f"Pruned {pruned} old FVGs")

        return pruned

    def clear_symbol(self, symbol: str) -> None:
        """Clear all FVGs for a symbol (e.g., end of day)."""
        if symbol in self._fvgs:
            self._fvgs[symbol].clear()
            logger.info(f"Cleared all FVGs for {symbol}")

    def get_all_fvgs(self, symbol: str) -> list[FairValueGap]:
        """Get all FVGs for a symbol (for persistence)."""
        if symbol not in self._fvgs:
            return []
        return list(self._fvgs[symbol])

    def load_fvgs(self, symbol: str, fvgs: list[FairValueGap]) -> None:
        """Load FVGs from persistence."""
        self._fvgs[symbol] = deque(fvgs, maxlen=self._max_fvgs_per_symbol)
        logger.info(f"Loaded {len(fvgs)} FVGs for {symbol}")

    def detect_ifvg_signal(
        self,
        symbol: str,
        candle: PriceCandle,
        expected_type: FVGType,
    ) -> Optional[FairValueGap]:
        """
        Detect if an IFVG forms that matches our expected signal type.

        This is for Variant B entry (high confidence):
        - For LONG: Look for Bullish IFVG (bearish FVG inverted)
        - For SHORT: Look for Bearish IFVG (bullish FVG inverted)

        Args:
            symbol: Stock symbol
            candle: Current candle
            expected_type: The FVG type we want after inversion

        Returns:
            The inverted FVG if it matches, None otherwise
        """
        interactions = self.check_fvg_interaction(symbol, candle)

        for fvg, interaction_type in interactions:
            if interaction_type == "inverted" and fvg.fvg_type == expected_type:
                logger.info(
                    f"IFVG signal detected: {expected_type.value} "
                    f"${fvg.bottom:.2f}-${fvg.top:.2f}"
                )
                return fvg

        return None

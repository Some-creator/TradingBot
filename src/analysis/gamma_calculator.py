"""Gamma Exposure (GEX) calculator using options data."""

import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import yfinance as yf

from src.config import config
from src.models import GammaLevels

logger = logging.getLogger(__name__)


class GammaCalculator:
    """
    Calculates Gamma Exposure (GEX) levels from options data.

    Note: Uses publicly available options data which may be delayed.
    Focuses on structural OI (sticky) rather than dynamic gamma changes.
    """

    def __init__(self):
        self._cache: dict[str, tuple[GammaLevels, datetime]] = {}
        self._cache_duration = timedelta(minutes=config.gex_update_interval_mins)

    async def calculate_gamma_levels(
        self, symbol: str, spot_price: float = None
    ) -> Optional[GammaLevels]:
        """
        Calculate gamma exposure levels for a symbol.

        Args:
            symbol: Stock symbol (SPY, QQQ)
            spot_price: Current spot price (fetched if not provided)

        Returns:
            GammaLevels with key support/resistance levels
        """
        # Check cache
        if symbol in self._cache:
            cached_levels, cached_time = self._cache[symbol]
            if datetime.now() - cached_time < self._cache_duration:
                logger.debug(f"Using cached gamma levels for {symbol}")
                return cached_levels

        try:
            ticker = yf.Ticker(symbol)

            # Get spot price if not provided
            if spot_price is None:
                hist = ticker.history(period="1d")
                if hist.empty:
                    logger.error(f"Could not get spot price for {symbol}")
                    return None
                spot_price = hist["Close"].iloc[-1]

            # Get options chain
            expirations = ticker.options
            if not expirations:
                logger.error(f"No options data available for {symbol}")
                return None

            # Focus on near-term expirations (next 2-3 weeks for intraday relevance)
            relevant_expirations = self._get_relevant_expirations(expirations)

            if not relevant_expirations:
                logger.warning(f"No relevant expirations for {symbol}")
                return None

            # Aggregate OI across relevant expirations
            call_oi_by_strike: dict[float, int] = {}
            put_oi_by_strike: dict[float, int] = {}
            gamma_by_strike: dict[float, float] = {}

            for exp in relevant_expirations:
                try:
                    chain = ticker.option_chain(exp)

                    # Process calls
                    for _, row in chain.calls.iterrows():
                        strike = row["strike"]
                        oi = row.get("openInterest", 0) or 0
                        gamma = row.get("gamma", 0) or 0

                        call_oi_by_strike[strike] = call_oi_by_strike.get(strike, 0) + oi
                        gamma_by_strike[strike] = gamma_by_strike.get(strike, 0) + (
                            gamma * oi * 100  # Contract multiplier
                        )

                    # Process puts
                    for _, row in chain.puts.iterrows():
                        strike = row["strike"]
                        oi = row.get("openInterest", 0) or 0
                        gamma = row.get("gamma", 0) or 0

                        put_oi_by_strike[strike] = put_oi_by_strike.get(strike, 0) + oi
                        # Put gamma is negative for hedging purposes
                        gamma_by_strike[strike] = gamma_by_strike.get(strike, 0) - (
                            gamma * oi * 100
                        )

                except Exception as e:
                    logger.warning(f"Error processing expiration {exp}: {e}")
                    continue

            if not call_oi_by_strike or not put_oi_by_strike:
                logger.error(f"No OI data found for {symbol}")
                return None

            # Find key levels
            call_wall = self._find_wall(call_oi_by_strike, spot_price, "call")
            put_wall = self._find_wall(put_oi_by_strike, spot_price, "put")
            zero_gamma = self._find_zero_gamma(gamma_by_strike, spot_price)
            net_gex = self._calculate_net_gex(gamma_by_strike, spot_price)

            levels = GammaLevels(
                symbol=symbol,
                timestamp=datetime.now(),
                call_wall=call_wall,
                put_wall=put_wall,
                zero_gamma=zero_gamma,
                net_gex=net_gex,
            )

            # Update cache
            self._cache[symbol] = (levels, datetime.now())

            logger.info(
                f"{symbol} Gamma Levels - Call Wall: ${call_wall:.2f}, "
                f"Put Wall: ${put_wall:.2f}, Zero Gamma: ${zero_gamma:.2f}, "
                f"Net GEX: {net_gex:,.0f}"
            )

            return levels

        except Exception as e:
            logger.error(f"Failed to calculate gamma levels for {symbol}: {e}")
            return None

    def _get_relevant_expirations(self, expirations: tuple) -> list[str]:
        """
        Get expirations relevant for intraday trading (next 2-3 weeks).
        """
        today = datetime.now().date()
        max_date = today + timedelta(days=21)  # 3 weeks out

        relevant = []
        for exp in expirations:
            try:
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                if today <= exp_date <= max_date:
                    relevant.append(exp)
            except ValueError:
                continue

        return relevant[:4]  # Limit to first 4 relevant expirations

    def _find_wall(
        self, oi_by_strike: dict[float, int], spot_price: float, wall_type: str
    ) -> float:
        """
        Find the strike with maximum OI (the "wall").

        For call walls: Look above spot price (resistance)
        For put walls: Look below spot price (support)
        """
        if wall_type == "call":
            # Filter to strikes above spot (resistance)
            filtered = {k: v for k, v in oi_by_strike.items() if k >= spot_price * 0.99}
        else:
            # Filter to strikes below spot (support)
            filtered = {k: v for k, v in oi_by_strike.items() if k <= spot_price * 1.01}

        if not filtered:
            # Fallback to all strikes
            filtered = oi_by_strike

        # Find strike with max OI
        wall_strike = max(filtered, key=filtered.get)
        return wall_strike

    def _find_zero_gamma(
        self, gamma_by_strike: dict[float, float], spot_price: float
    ) -> float:
        """
        Find the gamma flip point (where net gamma crosses zero).
        """
        if not gamma_by_strike:
            return spot_price

        # Sort strikes
        strikes = sorted(gamma_by_strike.keys())
        gammas = [gamma_by_strike[s] for s in strikes]

        # Find where gamma crosses zero
        for i in range(len(gammas) - 1):
            if gammas[i] * gammas[i + 1] < 0:  # Sign change
                # Linear interpolation
                x1, x2 = strikes[i], strikes[i + 1]
                y1, y2 = gammas[i], gammas[i + 1]
                zero_cross = x1 - y1 * (x2 - x1) / (y2 - y1)
                return zero_cross

        # If no zero crossing, return spot price
        return spot_price

    def _calculate_net_gex(
        self, gamma_by_strike: dict[float, float], spot_price: float
    ) -> float:
        """
        Calculate net gamma exposure at current spot price.

        Positive GEX = Mean reversion expected (dealers dampen volatility)
        Negative GEX = Trend/volatility expected (dealers amplify moves)
        """
        if not gamma_by_strike:
            return 0.0

        # Sum gamma weighted by distance to spot
        total_gex = 0.0
        for strike, gamma in gamma_by_strike.items():
            # Weight by proximity to spot
            distance = abs(strike - spot_price) / spot_price
            weight = max(0, 1 - distance * 5)  # Linear decay
            total_gex += gamma * weight

        return total_gex

    def is_positive_gex(self, levels: GammaLevels, spot_price: float) -> bool:
        """
        Determine if we're in a positive gamma environment.

        In positive gamma, expect mean reversion (buy support, sell resistance).
        In negative gamma, expect trends (follow breakouts).
        """
        return levels.net_gex > 0

    def get_zone(
        self, level: float, zone_width_pct: float = None
    ) -> tuple[float, float]:
        """
        Convert a price level to a zone.

        Returns:
            Tuple of (zone_low, zone_high)
        """
        width_pct = zone_width_pct or config.zone_width_pct
        width = level * width_pct / 100
        return (level - width, level + width)

    def price_in_zone(
        self, price: float, level: float, zone_width_pct: float = None
    ) -> bool:
        """Check if price is within a zone around a level."""
        zone_low, zone_high = self.get_zone(level, zone_width_pct)
        return zone_low <= price <= zone_high

    def get_active_level(
        self, levels: GammaLevels, spot_price: float
    ) -> Optional[tuple[str, float, tuple[float, float]]]:
        """
        Get the gamma level that price is currently interacting with.

        Returns:
            Tuple of (level_name, level_price, zone) or None
        """
        # Check put wall (support)
        put_zone = self.get_zone(levels.put_wall)
        if put_zone[0] <= spot_price <= put_zone[1]:
            return ("put_wall", levels.put_wall, put_zone)

        # Check call wall (resistance)
        call_zone = self.get_zone(levels.call_wall)
        if call_zone[0] <= spot_price <= call_zone[1]:
            return ("call_wall", levels.call_wall, call_zone)

        # Check zero gamma (with wider zone due to uncertainty)
        zero_zone = self.get_zone(levels.zero_gamma, config.zone_width_pct * 2)
        if zero_zone[0] <= spot_price <= zero_zone[1]:
            return ("zero_gamma", levels.zero_gamma, zero_zone)

        return None

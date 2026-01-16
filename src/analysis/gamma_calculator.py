"""
Gamma and Options Level Calculator.
Parses CBOE options data to identify key structural levels.
"""

from datetime import datetime
from typing import List, Optional, Dict
from dataclasses import dataclass
import yfinance as yf
import asyncio
from loguru import logger

from src.models import GammaLevel
from src.config import trading


class GammaCalculator:
    """
    Calculates gamma exposure levels from options data.
    
    Note: Uses delayed CBOE data. We focus on STRUCTURAL levels (OI walls)
    rather than dynamic gamma flows due to the 15-min delay.
    """
    
    def __init__(self):
        self._cached_levels: Dict[str, List[GammaLevel]] = {}
        self._last_update: Dict[str, datetime] = {}
    
    async def fetch_options_chain(self, symbol: str) -> Optional[Dict]:
        """Fetch options chain from yfinance."""
        loop = asyncio.get_event_loop()
        
        def _fetch():
            ticker = yf.Ticker(symbol)
            
            # Get nearest expiration (0DTE or 1DTE)
            expirations = ticker.options
            if not expirations:
                return None
            
            # Use the nearest expiration
            nearest_exp = expirations[0]
            
            try:
                chain = ticker.option_chain(nearest_exp)
                return {
                    "calls": chain.calls,
                    "puts": chain.puts,
                    "expiration": nearest_exp
                }
            except Exception as e:
                logger.error(f"Error fetching options chain: {e}")
                return None
        
        return await loop.run_in_executor(None, _fetch)
    
    async def calculate_levels(self, symbol: str, current_price: float) -> List[GammaLevel]:
        """
        Calculate key gamma/OI levels.
        
        Levels:
        - PUT_WALL: Strike with highest Put OI (Support)
        - CALL_WALL: Strike with highest Call OI (Resistance)
        - HIGH_POS_GAMMA: Strike with high positive gamma (volatility dampener)
        - HIGH_NEG_GAMMA: Strike with high negative gamma (volatility accelerator)
        """
        chain = await self.fetch_options_chain(symbol)
        
        if chain is None:
            logger.warning(f"Could not fetch options chain for {symbol}")
            return self._cached_levels.get(symbol, [])
        
        calls_df = chain["calls"]
        puts_df = chain["puts"]
        
        levels = []
        
        # --- PUT WALL (Support) ---
        # Strike with max Put Open Interest near current price
        puts_near = puts_df[
            (puts_df["strike"] >= current_price * 0.95) & 
            (puts_df["strike"] <= current_price * 1.02)
        ]
        
        if not puts_near.empty:
            max_put_oi_idx = puts_near["openInterest"].idxmax()
            put_wall_strike = float(puts_near.loc[max_put_oi_idx, "strike"])
            put_wall_oi = float(puts_near.loc[max_put_oi_idx, "openInterest"])
            
            levels.append(GammaLevel(
                price=put_wall_strike,
                level_type="PUT_WALL",
                strength=put_wall_oi
            ))
            logger.info(f"{symbol} Put Wall: ${put_wall_strike:.2f} (OI: {put_wall_oi:,.0f})")
        
        # --- CALL WALL (Resistance) ---
        # Strike with max Call Open Interest near current price
        calls_near = calls_df[
            (calls_df["strike"] >= current_price * 0.98) & 
            (calls_df["strike"] <= current_price * 1.05)
        ]
        
        if not calls_near.empty:
            max_call_oi_idx = calls_near["openInterest"].idxmax()
            call_wall_strike = float(calls_near.loc[max_call_oi_idx, "strike"])
            call_wall_oi = float(calls_near.loc[max_call_oi_idx, "openInterest"])
            
            levels.append(GammaLevel(
                price=call_wall_strike,
                level_type="CALL_WALL",
                strength=call_wall_oi
            ))
            logger.info(f"{symbol} Call Wall: ${call_wall_strike:.2f} (OI: {call_wall_oi:,.0f})")
        
        # --- HIGH GAMMA STRIKES ---
        # Approximate gamma from volume * (1 / distance to ATM)
        # This is a simplified calculation for delayed data
        
        atm_strikes = calls_df[
            (calls_df["strike"] >= current_price * 0.99) & 
            (calls_df["strike"] <= current_price * 1.01)
        ]
        
        if not atm_strikes.empty:
            # High Positive Gamma (ATM with high OI)
            high_gamma_idx = atm_strikes["openInterest"].idxmax()
            high_gamma_strike = float(atm_strikes.loc[high_gamma_idx, "strike"])
            
            levels.append(GammaLevel(
                price=high_gamma_strike,
                level_type="HIGH_POS_GAMMA",
                strength=float(atm_strikes.loc[high_gamma_idx, "openInterest"])
            ))
        
        # --- CALCULATE ZONES ---
        zone_width = trading.zone_width_pct / 100
        for level in levels:
            level.zone_top = level.price * (1 + zone_width)
            level.zone_bottom = level.price * (1 - zone_width)
        
        # Cache the levels
        self._cached_levels[symbol] = levels
        self._last_update[symbol] = datetime.now()
        
        return levels
    
    def get_put_wall(self, symbol: str) -> Optional[GammaLevel]:
        """Get the Put Wall level for a symbol."""
        levels = self._cached_levels.get(symbol, [])
        for level in levels:
            if level.level_type == "PUT_WALL":
                return level
        return None
    
    def get_call_wall(self, symbol: str) -> Optional[GammaLevel]:
        """Get the Call Wall level for a symbol."""
        levels = self._cached_levels.get(symbol, [])
        for level in levels:
            if level.level_type == "CALL_WALL":
                return level
        return None
    
    def get_high_pos_gamma(self, symbol: str) -> Optional[GammaLevel]:
        """Get the High Positive Gamma level (TP target for longs)."""
        levels = self._cached_levels.get(symbol, [])
        for level in levels:
            if level.level_type == "HIGH_POS_GAMMA":
                return level
        return None
    
    def price_at_level(self, symbol: str, price: float) -> Optional[GammaLevel]:
        """Check if price is at any gamma level zone."""
        levels = self._cached_levels.get(symbol, [])
        for level in levels:
            if level.price_in_zone(price):
                return level
        return None
    
    def get_cached_levels(self, symbol: str) -> List[GammaLevel]:
        """Get cached levels for a symbol."""
        return self._cached_levels.get(symbol, [])


# Global instance
gamma_calculator = GammaCalculator()

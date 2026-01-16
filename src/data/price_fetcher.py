"""
Price data fetcher using yfinance.
Provides real-time and historical price data for SPY/QQQ.
"""

import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from collections import deque
import yfinance as yf
from loguru import logger

from src.models import Candle


class PriceFetcher:
    """Fetches price data from yfinance."""
    
    def __init__(self, symbols: List[str], candle_buffer_size: int = 500):
        self.symbols = symbols
        self.candle_buffer_size = candle_buffer_size
        
        # Buffer of recent 1-min candles per symbol
        self._candle_buffers: Dict[str, deque] = {
            symbol: deque(maxlen=candle_buffer_size) for symbol in symbols
        }
        
        # Latest tick data
        self._latest_prices: Dict[str, float] = {}
        self._last_update: Dict[str, datetime] = {}
    
    async def fetch_historical_candles(
        self, 
        symbol: str, 
        period: str = "1d", 
        interval: str = "1m"
    ) -> List[Candle]:
        """Fetch historical candle data."""
        loop = asyncio.get_event_loop()
        
        def _fetch():
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)
            return df
        
        try:
            df = await loop.run_in_executor(None, _fetch)
            
            if df.empty:
                logger.warning(f"No data returned for {symbol}")
                return []
            
            candles = []
            for idx, row in df.iterrows():
                candle = Candle(
                    timestamp=idx.to_pydatetime(),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"]),
                    symbol=symbol
                )
                candles.append(candle)
                self._candle_buffers[symbol].append(candle)
            
            logger.info(f"Fetched {len(candles)} candles for {symbol}")
            return candles
            
        except Exception as e:
            logger.error(f"Error fetching candles for {symbol}: {e}")
            return []
    
    async def fetch_current_price(self, symbol: str) -> Optional[float]:
        """Fetch the latest price for a symbol."""
        loop = asyncio.get_event_loop()
        
        def _fetch():
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="1d", interval="1m")
            if not data.empty:
                return float(data["Close"].iloc[-1])
            return None
        
        try:
            price = await loop.run_in_executor(None, _fetch)
            if price:
                self._latest_prices[symbol] = price
                self._last_update[symbol] = datetime.now()
            return price
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return self._latest_prices.get(symbol)
    
    async def get_latest_candles(self, symbol: str, count: int = 3) -> List[Candle]:
        """Get the most recent N candles from buffer."""
        buffer = self._candle_buffers.get(symbol, deque())
        return list(buffer)[-count:] if len(buffer) >= count else list(buffer)
    
    def add_candle(self, candle: Candle) -> None:
        """Add a new candle to the buffer."""
        if candle.symbol in self._candle_buffers:
            self._candle_buffers[candle.symbol].append(candle)
            self._latest_prices[candle.symbol] = candle.close
            self._last_update[candle.symbol] = candle.timestamp
    
    def get_candle_buffer(self, symbol: str) -> List[Candle]:
        """Get full candle buffer for a symbol."""
        return list(self._candle_buffers.get(symbol, []))
    
    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Get cached latest price."""
        return self._latest_prices.get(symbol)
    
    async def fetch_vix(self) -> Optional[float]:
        """Fetch current VIX level."""
        loop = asyncio.get_event_loop()
        
        def _fetch():
            ticker = yf.Ticker("^VIX")
            data = ticker.history(period="1d")
            if not data.empty:
                return float(data["Close"].iloc[-1])
            return None
        
        try:
            return await loop.run_in_executor(None, _fetch)
        except Exception as e:
            logger.error(f"Error fetching VIX: {e}")
            return None
    
    async def fetch_20day_ma(self, symbol: str) -> Optional[float]:
        """Fetch 20-day moving average for trend filter."""
        loop = asyncio.get_event_loop()
        
        def _fetch():
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="1mo")
            if len(df) >= 20:
                return float(df["Close"].tail(20).mean())
            return None
        
        try:
            return await loop.run_in_executor(None, _fetch)
        except Exception as e:
            logger.error(f"Error fetching 20-day MA for {symbol}: {e}")
            return None

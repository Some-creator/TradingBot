"""Real-time price data fetcher using yfinance."""

import asyncio
import logging
from datetime import datetime, timedelta, time
from typing import Optional, Callable, Awaitable
from collections import deque

import yfinance as yf
import pytz

from src.config import config
from src.models import PriceCandle

logger = logging.getLogger(__name__)

# US Eastern timezone for market hours
ET = pytz.timezone("US/Eastern")


class PriceFetcher:
    """
    Fetches real-time price data for SPY/QQQ.

    Uses yfinance for data retrieval with configurable polling intervals.
    Maintains a rolling buffer of recent candles for pattern detection.
    """

    def __init__(self, symbols: tuple = None, buffer_size: int = 100):
        self.symbols = symbols or config.symbols
        self.buffer_size = buffer_size

        # Rolling buffers for each symbol
        self._candle_buffers: dict[str, deque[PriceCandle]] = {
            symbol: deque(maxlen=buffer_size) for symbol in self.symbols
        }

        # Latest prices
        self._latest_prices: dict[str, float] = {}
        self._last_update: dict[str, datetime] = {}

        # Callbacks for price updates
        self._callbacks: list[Callable[[str, PriceCandle], Awaitable[None]]] = []

        # Running state
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None

    def register_callback(
        self, callback: Callable[[str, PriceCandle], Awaitable[None]]
    ) -> None:
        """Register a callback for price updates."""
        self._callbacks.append(callback)

    async def start(self, poll_interval_seconds: float = 1.0) -> None:
        """Start the price polling loop."""
        if self._running:
            logger.warning("PriceFetcher already running")
            return

        self._running = True
        self._poll_task = asyncio.create_task(
            self._poll_loop(poll_interval_seconds)
        )
        logger.info(f"PriceFetcher started for {self.symbols}")

    async def stop(self) -> None:
        """Stop the price polling loop."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("PriceFetcher stopped")

    async def _poll_loop(self, interval: float) -> None:
        """Main polling loop."""
        while self._running:
            try:
                # Check if market is open
                if not self.is_market_open():
                    logger.debug("Market closed, waiting...")
                    await asyncio.sleep(60)  # Check every minute when closed
                    continue

                # Fetch prices for all symbols
                for symbol in self.symbols:
                    await self._fetch_and_update(symbol)

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Poll loop error: {e}")
                await asyncio.sleep(5)  # Wait before retry

    async def _fetch_and_update(self, symbol: str) -> Optional[PriceCandle]:
        """Fetch latest price and create candle."""
        try:
            ticker = yf.Ticker(symbol)

            # Get 1-minute data for the most recent candle
            data = ticker.history(period="1d", interval="1m")

            if data.empty:
                logger.warning(f"No data received for {symbol}")
                return None

            # Get the latest candle
            latest = data.iloc[-1]
            timestamp = data.index[-1].to_pydatetime()

            # Check if this is a new candle
            if symbol in self._last_update:
                if timestamp <= self._last_update[symbol]:
                    # Same candle, just update latest price
                    self._latest_prices[symbol] = latest["Close"]
                    return None

            candle = PriceCandle(
                timestamp=timestamp,
                symbol=symbol,
                open=latest["Open"],
                high=latest["High"],
                low=latest["Low"],
                close=latest["Close"],
                volume=int(latest["Volume"]),
            )

            # Update buffers
            self._candle_buffers[symbol].append(candle)
            self._latest_prices[symbol] = candle.close
            self._last_update[symbol] = timestamp

            # Notify callbacks
            for callback in self._callbacks:
                try:
                    await callback(symbol, candle)
                except Exception as e:
                    logger.error(f"Callback error: {e}")

            return candle

        except Exception as e:
            logger.error(f"Failed to fetch {symbol}: {e}")
            return None

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Get the most recent price for a symbol."""
        return self._latest_prices.get(symbol)

    def get_latest_candle(self, symbol: str) -> Optional[PriceCandle]:
        """Get the most recent candle for a symbol."""
        if symbol in self._candle_buffers and self._candle_buffers[symbol]:
            return self._candle_buffers[symbol][-1]
        return None

    def get_recent_candles(
        self, symbol: str, count: int = 10
    ) -> list[PriceCandle]:
        """Get recent candles for a symbol."""
        if symbol not in self._candle_buffers:
            return []
        candles = list(self._candle_buffers[symbol])
        return candles[-count:] if len(candles) >= count else candles

    def get_candles_since(
        self, symbol: str, since: datetime
    ) -> list[PriceCandle]:
        """Get candles since a specific time."""
        if symbol not in self._candle_buffers:
            return []
        return [c for c in self._candle_buffers[symbol] if c.timestamp >= since]

    async def fetch_historical_candles(
        self,
        symbol: str,
        period: str = "1d",
        interval: str = "1m",
    ) -> list[PriceCandle]:
        """
        Fetch historical candles for backtesting or initialization.

        Args:
            symbol: Stock symbol
            period: Time period (1d, 5d, 1mo, etc.)
            interval: Candle interval (1m, 5m, 15m, etc.)

        Returns:
            List of PriceCandle objects
        """
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period=period, interval=interval)

            if data.empty:
                return []

            candles = []
            for idx, row in data.iterrows():
                candle = PriceCandle(
                    timestamp=idx.to_pydatetime(),
                    symbol=symbol,
                    open=row["Open"],
                    high=row["High"],
                    low=row["Low"],
                    close=row["Close"],
                    volume=int(row["Volume"]),
                )
                candles.append(candle)

            return candles

        except Exception as e:
            logger.error(f"Failed to fetch historical data for {symbol}: {e}")
            return []

    def is_market_open(self) -> bool:
        """Check if US stock market is currently open."""
        now = datetime.now(ET)

        # Check if weekday
        if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return False

        # Market hours: 9:30 AM - 4:00 PM ET
        market_open = time(9, 30)
        market_close = time(16, 0)

        return market_open <= now.time() <= market_close

    def is_premarket(self) -> bool:
        """Check if we're in pre-market hours (4:00 AM - 9:30 AM ET)."""
        now = datetime.now(ET)

        if now.weekday() >= 5:
            return False

        premarket_open = time(4, 0)
        market_open = time(9, 30)

        return premarket_open <= now.time() < market_open

    def minutes_since_open(self) -> int:
        """Get minutes since market open."""
        now = datetime.now(ET)
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)

        if now < market_open:
            return 0

        delta = now - market_open
        return int(delta.total_seconds() / 60)

    def is_after_wait_period(self) -> bool:
        """Check if we're past the opening volatility wait period."""
        return self.minutes_since_open() >= config.market_open_wait_mins

    def get_data_lag_seconds(self, symbol: str) -> float:
        """Get the data lag in seconds for a symbol."""
        if symbol not in self._last_update:
            return float("inf")

        now = datetime.now()
        last_update = self._last_update[symbol]

        # Make both timezone-naive for comparison
        if last_update.tzinfo is not None:
            last_update = last_update.replace(tzinfo=None)

        return (now - last_update).total_seconds()

    def is_data_stale(self, symbol: str) -> bool:
        """Check if data feed is stale (lag > threshold)."""
        lag = self.get_data_lag_seconds(symbol)
        return lag > config.max_data_lag_seconds

    async def get_overnight_range(
        self, symbol: str
    ) -> tuple[Optional[float], Optional[float]]:
        """
        Get overnight high/low for pre-market analysis.

        Returns:
            Tuple of (overnight_low, overnight_high)
        """
        try:
            ticker = yf.Ticker(symbol)
            # Get extended hours data if available
            data = ticker.history(period="2d", interval="1h", prepost=True)

            if data.empty:
                return None, None

            # Get yesterday's close time
            now = datetime.now(ET)
            yesterday_close = now.replace(
                hour=16, minute=0, second=0, microsecond=0
            ) - timedelta(days=1)

            # Filter to overnight session
            overnight_data = data[data.index > yesterday_close]

            if overnight_data.empty:
                return None, None

            return overnight_data["Low"].min(), overnight_data["High"].max()

        except Exception as e:
            logger.error(f"Failed to get overnight range: {e}")
            return None, None

    async def get_premarket_volume(self, symbol: str) -> int:
        """Get pre-market trading volume."""
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="1d", interval="1m", prepost=True)

            if data.empty:
                return 0

            now = datetime.now(ET)
            market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)

            # Filter to pre-market
            premarket_data = data[data.index < market_open]

            if premarket_data.empty:
                return 0

            return int(premarket_data["Volume"].sum())

        except Exception as e:
            logger.error(f"Failed to get premarket volume: {e}")
            return 0

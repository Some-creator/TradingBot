"""Main trading bot orchestrator."""

import asyncio
import logging
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

from src.config import config
from src.state import state_manager
from src.models import PriceCandle, Bias
from src.data.price_fetcher import PriceFetcher
from src.analysis.sentiment_engine import SentimentEngine
from src.analysis.gamma_calculator import GammaCalculator
from src.analysis.fvg_detector import FVGDetector
from src.execution.signal_generator import SignalGenerator
from src.execution.risk_manager import RiskManager
from src.execution.order_manager import OrderManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


class TradingBot:
    """
    Main trading bot orchestrator.

    Coordinates all components and runs the main trading loop.
    """

    def __init__(self):
        # Initialize components
        self.price_fetcher = PriceFetcher()
        self.sentiment_engine = SentimentEngine()
        self.gamma_calculator = GammaCalculator()
        self.fvg_detector = FVGDetector()
        self.signal_generator = SignalGenerator(
            fvg_detector=self.fvg_detector,
            gamma_calculator=self.gamma_calculator,
        )
        self.risk_manager = RiskManager(state_manager)
        self.order_manager = OrderManager(state_manager)

        # Running state
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Cache
        self._current_sentiment = None
        self._gamma_levels = {}

    async def start(self) -> None:
        """Start the trading bot."""
        logger.info("Starting trading bot...")

        # Connect to Redis
        await state_manager.connect()

        # Register price update callback
        self.price_fetcher.register_callback(self._on_price_update)

        # Register order callbacks
        self.order_manager.register_exit_callback(self._on_trade_exit)

        self._running = True

        # Start background tasks
        asyncio.create_task(self._premarket_analysis_loop())
        asyncio.create_task(self._gamma_update_loop())
        asyncio.create_task(self._risk_monitor_loop())

        # Start price fetcher
        await self.price_fetcher.start(poll_interval_seconds=1.0)

        logger.info("Trading bot started successfully")

    async def stop(self) -> None:
        """Stop the trading bot gracefully."""
        logger.info("Stopping trading bot...")

        self._running = False
        self._shutdown_event.set()

        # Stop price fetcher
        await self.price_fetcher.stop()

        # Close all positions if any
        current_prices = {
            symbol: self.price_fetcher.get_latest_price(symbol)
            for symbol in config.symbols
        }
        current_prices = {k: v for k, v in current_prices.items() if v is not None}

        if current_prices:
            await self.order_manager.close_all_positions(current_prices)

        # Disconnect from Redis
        await state_manager.disconnect()

        logger.info("Trading bot stopped")

    async def _on_price_update(self, symbol: str, candle: PriceCandle) -> None:
        """Handle new price data."""
        try:
            # Skip if not running or market closed
            if not self._running:
                return

            if not self.price_fetcher.is_market_open():
                return

            # Skip during opening volatility period
            if not self.price_fetcher.is_after_wait_period():
                return

            # Check for data staleness
            if self.price_fetcher.is_data_stale(symbol):
                logger.warning(f"Data stale for {symbol}")
                return

            # Get current state
            gamma_levels = self._gamma_levels.get(symbol)
            sentiment = self._current_sentiment

            if not gamma_levels or not sentiment:
                return

            # Check if we can trade
            can_trade, reason = await self.risk_manager.can_trade()
            if not can_trade:
                logger.debug(f"Cannot trade: {reason}")
                return

            # Get recent candles for signal detection
            candles = self.price_fetcher.get_recent_candles(symbol, count=10)
            if len(candles) < 3:
                return

            # Check for entry signal
            signal = self.signal_generator.check_entry_signal(
                symbol=symbol,
                candles=candles,
                gamma_levels=gamma_levels,
                sentiment=sentiment,
            )

            if signal:
                # Validate signal
                is_valid, reason = await self.risk_manager.validate_signal(signal)

                if is_valid:
                    # Calculate position size
                    quantity = self.risk_manager.calculate_position_size(signal)

                    if quantity > 0:
                        # Execute entry
                        trade = await self.order_manager.execute_entry(signal, quantity)
                        if trade:
                            logger.info(f"Trade opened: {trade.id}")
                else:
                    logger.debug(f"Signal rejected: {reason}")

            # Check exit conditions for open trades
            await self._check_open_trades(symbol, candle.close)

        except Exception as e:
            logger.error(f"Error processing price update: {e}")

    async def _check_open_trades(self, symbol: str, current_price: float) -> None:
        """Check exit conditions for open trades."""
        trades = await self.order_manager.get_open_trades()

        for trade in trades:
            if trade.symbol != symbol:
                continue

            gamma_levels = self._gamma_levels.get(symbol)
            if not gamma_levels:
                continue

            # Check for TP1 partial exit
            partial_taken = len(trade.partial_exits) > 0

            exit_result = self.signal_generator.check_exit_conditions(
                symbol=symbol,
                current_price=current_price,
                entry_price=trade.entry_price,
                entry_time=trade.entry_time,
                direction=trade.direction,
                stop_loss=trade.stop_loss,
                tp1_price=trade.tp1_price,
                tp2_price=trade.tp2_price,
                gamma_levels=gamma_levels,
                partial_tp1_taken=partial_taken,
            )

            if exit_result:
                exit_reason, exit_price = exit_result

                # Handle partial exit at TP1
                if exit_reason == "tp1" and not partial_taken:
                    partial_qty = self.risk_manager.calculate_partial_exit_size(
                        trade.quantity
                    )
                    await self.order_manager.execute_exit(
                        trade, exit_price, exit_reason, quantity=partial_qty
                    )

                    # Move stop to breakeven
                    await self.order_manager.update_stop_loss(
                        trade, trade.entry_price
                    )
                else:
                    # Full exit
                    await self.order_manager.execute_exit(
                        trade, exit_price, exit_reason
                    )

            # Check if stop should be moved to breakeven
            elif self.signal_generator.should_move_stop_to_breakeven(
                trade.entry_price,
                current_price,
                trade.direction,
                trade.entry_time,
            ):
                if trade.stop_loss != trade.entry_price:
                    await self.order_manager.update_stop_loss(
                        trade, trade.entry_price
                    )

    async def _on_trade_exit(self, trade) -> None:
        """Handle trade exit."""
        await self.risk_manager.record_trade_result(trade)

    async def _premarket_analysis_loop(self) -> None:
        """Run sentiment analysis during pre-market."""
        while self._running:
            try:
                if self.price_fetcher.is_premarket():
                    # Get pre-market data
                    symbol = "SPY"
                    overnight_low, overnight_high = await self.price_fetcher.get_overnight_range(symbol)
                    premarket_volume = await self.price_fetcher.get_premarket_volume(symbol)

                    # For now, use empty headlines (would integrate with news API)
                    headlines = []

                    current_price = self.price_fetcher.get_latest_price(symbol)
                    if current_price is None:
                        # Fetch current price
                        candles = await self.price_fetcher.fetch_historical_candles(
                            symbol, period="1d", interval="1m"
                        )
                        if candles:
                            current_price = candles[-1].close

                    if current_price and overnight_high and overnight_low:
                        self._current_sentiment = await self.sentiment_engine.analyze_sentiment(
                            headlines=headlines,
                            overnight_high=overnight_high,
                            overnight_low=overnight_low,
                            premarket_volume=premarket_volume,
                            current_price=current_price,
                        )
                        logger.info(
                            f"Sentiment updated: {self._current_sentiment.bias.value} "
                            f"(Score: {self._current_sentiment.final_score})"
                        )

                    await asyncio.sleep(config.sentiment_update_interval_mins * 60)
                else:
                    await asyncio.sleep(60)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Pre-market analysis error: {e}")
                await asyncio.sleep(60)

    async def _gamma_update_loop(self) -> None:
        """Update gamma levels periodically."""
        while self._running:
            try:
                for symbol in config.symbols:
                    price = self.price_fetcher.get_latest_price(symbol)
                    levels = await self.gamma_calculator.calculate_gamma_levels(
                        symbol, spot_price=price
                    )
                    if levels:
                        self._gamma_levels[symbol] = levels
                        await state_manager.save_gamma_levels(levels)

                await asyncio.sleep(config.gex_update_interval_mins * 60)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Gamma update error: {e}")
                await asyncio.sleep(60)

    async def _risk_monitor_loop(self) -> None:
        """Monitor risk conditions."""
        while self._running:
            try:
                # Check VIX explosion
                vix_explosion = await self.sentiment_engine.check_vix_explosion()

                # Check data staleness
                data_stale = any(
                    self.price_fetcher.is_data_stale(symbol)
                    for symbol in config.symbols
                )

                should_shutdown, reason = await self.risk_manager.check_emergency_shutdown(
                    vix_explosion, data_stale
                )

                if should_shutdown:
                    logger.critical(f"Emergency shutdown: {reason}")
                    # Close all positions
                    current_prices = {
                        symbol: self.price_fetcher.get_latest_price(symbol)
                        for symbol in config.symbols
                    }
                    current_prices = {k: v for k, v in current_prices.items() if v}
                    await self.order_manager.close_all_positions(current_prices)

                await asyncio.sleep(30)  # Check every 30 seconds

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Risk monitor error: {e}")
                await asyncio.sleep(30)


# Global bot instance
bot: Optional[TradingBot] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager."""
    global bot
    bot = TradingBot()
    await bot.start()

    yield

    await bot.stop()


# Create FastAPI app
app = FastAPI(
    title="Trading Bot",
    description="Rule-based intraday trading system",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health check endpoint for Railway."""
    return JSONResponse(
        content={
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "trading_mode": config.trading_mode,
        }
    )


@app.get("/status")
async def get_status():
    """Get current bot status."""
    if not bot:
        return JSONResponse(content={"status": "not_initialized"}, status_code=503)

    # Get daily summary
    summary = await bot.risk_manager.get_daily_summary()

    # Get open positions
    trades = await bot.order_manager.get_open_trades()
    position_summary = bot.order_manager.get_position_summary(trades)

    return JSONResponse(
        content={
            "status": "running" if bot._running else "stopped",
            "market_open": bot.price_fetcher.is_market_open(),
            "minutes_since_open": bot.price_fetcher.minutes_since_open(),
            "sentiment": (
                bot._current_sentiment.to_dict() if bot._current_sentiment else None
            ),
            "daily_summary": summary,
            "positions": position_summary,
            "gamma_levels": {
                symbol: levels.to_dict()
                for symbol, levels in bot._gamma_levels.items()
            },
        }
    )


@app.get("/trades")
async def get_trades():
    """Get today's trades."""
    trades = await state_manager.get_daily_trades()
    return JSONResponse(
        content={
            "count": len(trades),
            "trades": [t.to_dict() for t in trades],
        }
    )


def handle_shutdown(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}, shutting down...")
    if bot:
        asyncio.create_task(bot.stop())


def main():
    """Main entry point."""
    # Register signal handlers
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    # Run the server
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()

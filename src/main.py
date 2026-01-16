"""
Main Trading Bot Application.
Orchestrates all components and runs the main trading loop.
"""

import asyncio
import signal
from datetime import datetime, time as dtime
from typing import List
from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn
from loguru import logger

from src.config import trading, app as app_config
from src.state import state_manager
from src.models import Direction, FVGStatus
from src.data.price_fetcher import PriceFetcher
from src.analysis.gamma_calculator import gamma_calculator
from src.analysis.sentiment_engine import sentiment_engine
from src.analysis.fvg_detector import fvg_detector
from src.execution.signal_generator import signal_generator
from src.execution.risk_manager import risk_manager
from src.execution.order_manager import order_manager


# Configure logging
logger.add(
    "logs/trading_{time}.log",
    rotation="1 day",
    retention="7 days",
    level=app_config.log_level
)


class TradingBot:
    """Main trading bot orchestrator."""
    
    def __init__(self):
        self.price_fetcher = PriceFetcher(trading.symbols)
        self._running = False
        self._account_equity = 100000.0  # TODO: Fetch from broker
    
    async def initialize(self) -> None:
        """Initialize all components."""
        logger.info("Initializing Trading Bot...")
        
        # Connect to Redis
        await state_manager.connect()
        logger.info("Connected to Redis")
        
        # Fetch initial data
        for symbol in trading.symbols:
            await self.price_fetcher.fetch_historical_candles(symbol)
            logger.info(f"Loaded historical candles for {symbol}")
        
        # Fetch VIX for risk manager
        vix = await self.price_fetcher.fetch_vix()
        if vix:
            risk_manager.set_initial_vix(vix)
            logger.info(f"Initial VIX: {vix:.2f}")
    
    async def run_pre_market_analysis(self) -> None:
        """
        Run pre-market analysis.
        
        - Fetch sentiment/news
        - Calculate daily bias
        - Update gamma levels
        """
        logger.info("Running pre-market analysis...")
        
        # TODO: Integrate with news API (Benzinga, etc.)
        # For now, use placeholder headlines
        headlines = [
            "S&P 500 futures steady ahead of earnings",
            "Tech sector shows strength in pre-market",
            "Economic data mixed, investors cautious"
        ]
        
        for symbol in trading.symbols:
            # Get current price and MA
            price = await self.price_fetcher.fetch_current_price(symbol)
            ma_20 = await self.price_fetcher.fetch_20day_ma(symbol)
            vix = await self.price_fetcher.fetch_vix()
            
            if price and ma_20 and vix:
                bias = await sentiment_engine.calculate_bias(
                    headlines, price, ma_20, vix
                )
                logger.info(f"{symbol} Bias: {bias.direction.value} (Score: {bias.score})")
            
            # Calculate gamma levels
            if price:
                levels = await gamma_calculator.calculate_levels(symbol, price)
                logger.info(f"{symbol} Gamma Levels: {len(levels)} found")
    
    async def process_candle(self, symbol: str) -> None:
        """Process a new candle and check for signals."""
        candles = self.price_fetcher.get_candle_buffer(symbol)
        
        if len(candles) < 3:
            return
        
        current_price = candles[-1].close
        
        # Check for FVG formation
        new_fvg = fvg_detector.detect_fvg(candles[-3:])
        if new_fvg:
            new_fvg.symbol = symbol
            await state_manager.save_fvg(new_fvg)
        
        # Get existing FVGs and check interactions
        fvgs = await state_manager.get_fvgs(symbol)
        
        for fvg in fvgs:
            has_interaction, new_status, new_type = fvg_detector.check_fvg_interaction(
                fvg, candles[-1]
            )
            if has_interaction and new_status:
                await state_manager.update_fvg_status(
                    fvg.id, symbol, new_status, new_type
                )
                fvg.status = new_status
                if new_type:
                    fvg.fvg_type = new_type
        
        # Get gamma levels
        gamma_levels = gamma_calculator.get_cached_levels(symbol)
        
        # Get bias
        bias = sentiment_engine.get_cached_bias()
        bias_direction = bias.direction if bias else Direction.NEUTRAL
        
        # Check if we can trade
        can_trade, reason = await risk_manager.can_trade()
        
        if not can_trade:
            return
        
        # Check for entry signals
        signal = signal_generator.check_entry_conditions(
            list(candles)[-10:],  # Last 10 candles
            gamma_levels,
            fvgs,
            bias_direction
        )
        
        if signal:
            # Calculate position size
            quantity = risk_manager.calculate_position_size(
                self._account_equity,
                signal.entry_price,
                signal.stop_loss,
                signal.direction
            )
            
            if quantity > 0:
                trade = await order_manager.execute_entry(signal, quantity, symbol)
                if trade:
                    logger.info(f"Trade opened: {trade.id}")
    
    async def manage_open_trades(self, symbol: str) -> None:
        """Manage open trades - check stops, TPs, time stops."""
        trades = await state_manager.get_open_trades(symbol)
        current_price = self.price_fetcher.get_latest_price(symbol)
        
        if not current_price:
            return
        
        for trade in trades:
            # Check stop loss
            if order_manager.check_stop_loss(trade, current_price):
                await order_manager.execute_exit(trade, current_price, "STOP_LOSS")
                await risk_manager.record_trade_result(trade)
                continue
            
            # Check TP1
            if order_manager.check_take_profit_1(trade, current_price):
                if not trade.tp1_hit:
                    trade.tp1_hit = True
                    # Move stop to breakeven
                    await order_manager.update_stop_loss(trade, trade.entry_price)
                    logger.info(f"TP1 hit - stop moved to breakeven")
                    
                    # Partial exit (simulated - in real trading, close 50%)
                    # For simplicity, we hold full position for TP2
            
            # Check TP2
            if order_manager.check_take_profit_2(trade, current_price):
                await order_manager.execute_exit(trade, current_price, "TAKE_PROFIT_2")
                await risk_manager.record_trade_result(trade)
                continue
            
            # Check time stop
            if risk_manager.should_time_stop(trade, current_price):
                await order_manager.execute_exit(trade, current_price, "TIME_STOP")
                await risk_manager.record_trade_result(trade)
    
    def is_market_hours(self) -> bool:
        """Check if market is open (9:30 AM - 4:00 PM ET)."""
        now = datetime.now()
        market_open = dtime(9, 30)
        market_close = dtime(16, 0)
        
        # Skip first 30 minutes (volatility rule)
        safe_start = dtime(10, 0)
        
        current_time = now.time()
        return safe_start <= current_time <= market_close
    
    async def trading_loop(self) -> None:
        """Main trading loop."""
        logger.info("Starting trading loop...")
        self._running = True
        
        last_candle_update = datetime.now()
        candle_interval = 60  # 1 minute
        
        while self._running:
            try:
                if not self.is_market_hours():
                    await asyncio.sleep(60)
                    continue
                
                # Update candles every minute
                now = datetime.now()
                if (now - last_candle_update).seconds >= candle_interval:
                    for symbol in trading.symbols:
                        # Fetch latest candle
                        await self.price_fetcher.fetch_historical_candles(
                            symbol, period="1d", interval="1m"
                        )
                        
                        # Process and check signals
                        await self.process_candle(symbol)
                        
                        # Manage open trades
                        await self.manage_open_trades(symbol)
                    
                    last_candle_update = now
                
                # Small sleep to prevent CPU spinning
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error in trading loop: {e}")
                await asyncio.sleep(5)
    
    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down...")
        self._running = False
        await state_manager.disconnect()


# Create bot instance
bot = TradingBot()


# FastAPI app with lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await bot.initialize()
    await bot.run_pre_market_analysis()
    
    # Start trading loop in background
    loop_task = asyncio.create_task(bot.trading_loop())
    
    yield
    
    # Shutdown
    await bot.shutdown()
    loop_task.cancel()


app = FastAPI(title="Intraday Trading Bot", lifespan=lifespan)


@app.get("/health")
async def health_check():
    """Health check endpoint for container orchestration."""
    return {
        "status": "healthy",
        "mode": trading.mode,
        "symbols": trading.symbols,
        "timestamp": datetime.now().isoformat()
    }


@app.get("/status")
async def trading_status():
    """Get current trading status."""
    state = await state_manager.get_daily_state()
    bias = sentiment_engine.get_cached_bias()
    
    return {
        "date": state.date,
        "trade_count": state.trade_count,
        "daily_pnl_percent": state.daily_pnl_percent,
        "is_locked": state.is_locked,
        "lock_reason": state.lock_reason,
        "bias": bias.direction.value if bias else "UNKNOWN",
        "bias_score": bias.score if bias else 0
    }


@app.get("/levels/{symbol}")
async def get_levels(symbol: str):
    """Get gamma levels for a symbol."""
    levels = gamma_calculator.get_cached_levels(symbol.upper())
    return {
        "symbol": symbol.upper(),
        "levels": [
            {
                "type": l.level_type,
                "price": l.price,
                "zone_top": l.zone_top,
                "zone_bottom": l.zone_bottom,
                "strength": l.strength
            }
            for l in levels
        ]
    }


def main():
    """Entry point."""
    # Handle signals for graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        asyncio.get_event_loop().run_until_complete(bot.shutdown())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run the app
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=app_config.health_port,
        log_level=app_config.log_level.lower()
    )


if __name__ == "__main__":
    main()

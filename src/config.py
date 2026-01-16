"""Configuration settings for the trading system."""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class TradingConfig:
    """Trading system configuration."""

    # API Keys (from environment)
    claude_api_key: str = ""
    broker_api_key: str = ""
    redis_url: str = ""

    # Trading Mode
    trading_mode: str = "PAPER"  # PAPER or LIVE

    # Risk Management
    max_trades_per_day: int = 3
    max_daily_loss_pct: float = 1.5  # 1.5% of account equity
    max_drawdown_per_trade_pct: float = 0.5  # 0.5% of account equity

    # Stop Loss / Take Profit
    stop_loss_buffer_pct: float = 0.01  # 0.01% buffer for stop hunts
    max_stop_loss_pct: float = 0.2  # Hard stop fallback
    tp1_pct: float = 0.3  # First take profit at 0.3%

    # Zone Configuration
    zone_width_pct: float = 0.15  # +/- 0.15% of spot price

    # Time Filters
    market_open_wait_mins: int = 30  # Wait 30 mins after open
    time_stop_mins: int = 30  # Exit if < 0.1% profit after 30 mins
    quick_exit_mins: int = 5  # Exit if no bounce in 5 mins (Gamma Trap defense)

    # FVG Settings
    fvg_max_age_hours: int = 2  # Prune FVGs older than 2 hours

    # VIX Thresholds
    vix_bullish_threshold: float = 15.0
    vix_bearish_threshold: float = 25.0
    vix_explosion_pct: float = 10.0  # Shut off if VIX moves > 10% intraday

    # Sentiment Score Thresholds
    sentiment_strong_bullish: int = 60
    sentiment_strong_bearish: int = -60
    sentiment_neutral_upper: int = 20
    sentiment_neutral_lower: int = -20
    final_bullish_threshold: int = 30
    final_bearish_threshold: int = -30

    # Data Feed Settings
    max_data_lag_seconds: int = 60  # Shut off if data lag > 60 seconds
    gex_update_interval_mins: int = 15  # GEX update cycle
    sentiment_update_interval_mins: int = 5  # Sentiment update during pre-market

    # Symbols
    symbols: tuple = ("SPY", "QQQ")

    # Server Settings
    host: str = "0.0.0.0"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "TradingConfig":
        """Load configuration from environment variables."""
        return cls(
            claude_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            broker_api_key=os.getenv("BROKER_API_KEY", ""),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
            trading_mode=os.getenv("TRADING_MODE", "PAPER"),
            max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS", "1.5")),
            port=int(os.getenv("PORT", "8000")),
        )


# Global config instance
config = TradingConfig.from_env()

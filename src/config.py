"""
Configuration management for the trading bot.
Loads environment variables and provides typed access to settings.
"""

import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()


@dataclass
class TradingConfig:
    """Core trading configuration."""
    mode: str = os.getenv("TRADING_MODE", "PAPER")
    symbols: List[str] = field(default_factory=lambda: os.getenv("SYMBOLS", "SPY,QQQ").split(","))
    
    # Risk Parameters
    max_trades_per_day: int = int(os.getenv("MAX_TRADES_PER_DAY", "3"))
    max_daily_loss_pct: float = float(os.getenv("MAX_DAILY_LOSS_PERCENT", "1.5"))
    max_trade_risk_pct: float = float(os.getenv("MAX_TRADE_RISK_PERCENT", "0.5"))
    
    # Strategy Parameters
    zone_width_pct: float = 0.15  # +/- 0.15% for gamma zones
    tp1_pct: float = 0.3  # Take Profit 1: +0.3%
    max_stop_pct: float = 0.2  # Max stop loss: 0.2%
    time_stop_minutes: int = 30  # Exit if < 0.1% profit after 30 mins
    
    # FVG Settings
    fvg_max_age_minutes: int = 120  # Prune FVGs older than 2 hours


@dataclass
class APIConfig:
    """API keys and endpoints."""
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    broker_api_key: str = os.getenv("BROKER_API_KEY", "")
    broker_api_secret: str = os.getenv("BROKER_API_SECRET", "")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")


@dataclass
class AppConfig:
    """Application-level configuration."""
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    health_port: int = int(os.getenv("HEALTH_PORT", "8080"))


# Global config instances
trading = TradingConfig()
api = APIConfig()
app = AppConfig()

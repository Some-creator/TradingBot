# Intraday Trading Bot

A rule-based intraday trading system for SPY and QQQ.

## Features

- **Sentiment Analysis**: Claude Haiku for pre-market news analysis
- **Options Structure**: Put/Call Walls from delayed CBOE data
- **FVG Detection**: Fair Value Gap and Inversion FVG triggers
- **Risk Management**: Max trades, daily loss limits, consecutive loss lockout

## Setup

```bash
# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Copy environment template
copy .env.example .env
# Edit .env with your API keys
```

## Configuration

Edit `.env` file:

```
TRADING_MODE=PAPER        # PAPER or LIVE
ANTHROPIC_API_KEY=...     # Claude API key
REDIS_URL=...             # Redis connection string
```

## Running

```bash
# Start the bot
python -m src.main

# Health check endpoint
curl http://localhost:8080/health

# Trading status
curl http://localhost:8080/status

# Gamma levels
curl http://localhost:8080/levels/SPY
```

## Project Structure

```
src/
├── config.py          # Configuration management
├── models.py          # Data models (Candle, FVG, Trade, etc.)
├── state.py           # Redis state persistence
├── main.py            # Main application & trading loop
├── data/
│   └── price_fetcher.py   # yfinance price data
├── analysis/
│   ├── fvg_detector.py    # FVG detection logic
│   ├── gamma_calculator.py # Options level calculation
│   └── sentiment_engine.py # Claude Haiku sentiment
└── execution/
    ├── signal_generator.py # Entry signal detection
    ├── risk_manager.py     # Risk & position sizing
    └── order_manager.py    # Trade execution
```

## Strategy

1. **Pre-Market**: Analyze sentiment → Set daily bias (Long/Short/Neutral)
2. **Levels**: Calculate Put Wall (Support), Call Wall (Resistance)
3. **Entry**: Wait for Sweep & Reclaim or IFVG at levels
4. **Exit**: TP1 (0.3%), TP2 (opposite wall), or Time Stop (30 min)

# Rule-Based Intraday Trading System

A rule-based intraday trading bot for SPY/QQQ that combines sentiment analysis, gamma exposure (GEX) levels, and Fair Value Gap (FVG) patterns for entry signals.

## Architecture

```
Sentiment (Claude Haiku) -> Sets Bias (Filter)
Options Data (GEX)       -> Sets Levels (Zones)
Price Action (yfinance)  -> Triggers Execution
```

## Features

- **Sentiment Analysis**: Uses Claude Haiku to analyze pre-market news headlines
- **Gamma Exposure Levels**: Calculates Call Wall, Put Wall, and Zero Gamma from options OI
- **FVG Detection**: Identifies Fair Value Gaps and Inverted FVGs for entry triggers
- **Risk Management**: Max 3 trades/day, 1.5% max daily loss, 0.5% max per trade
- **State Persistence**: Redis-backed state for Railway deployment

## Components

| Component | Description |
|-----------|-------------|
| `SentimentEngine` | LLM-powered sentiment analysis with VIX and trend adjustments |
| `GammaCalculator` | Options-based support/resistance level calculation |
| `FVGDetector` | Fair Value Gap detection and lifecycle management |
| `SignalGenerator` | Entry/exit signal generation based on rules |
| `RiskManager` | Position sizing and risk validation |
| `OrderManager` | Order execution and trade lifecycle |
| `StateManager` | Redis-backed state persistence |

## Entry Logic

### Long Setup (Bullish Bias)
1. Price sweeps into/through Put Wall Zone (support)
2. Trigger: Reclaim (close above zone) OR Bullish IFVG forms
3. Stop: Below sweep low + buffer
4. TP1: +0.3%, TP2: Call Wall

### Short Setup (Bearish Bias)
1. Price rallies into/through Call Wall Zone (resistance)
2. Trigger: Reclaim (close below zone) OR Bearish IFVG forms
3. Stop: Above sweep high + buffer
4. TP1: -0.3%, TP2: Put Wall

## Risk Rules

- Max 3 trades per day
- Max 1.5% daily loss (lockout)
- Max 0.5% risk per trade
- 2 consecutive losses pauses trading
- VIX >10% intraday move triggers shutdown
- Data lag >60 seconds triggers shutdown

## Deployment (Railway)

### Environment Variables

```
ANTHROPIC_API_KEY=your_key
REDIS_URL=redis://...
TRADING_MODE=PAPER
MAX_DAILY_LOSS=1.5
PORT=8000
```

### Deploy

1. Connect repository to Railway
2. Add Redis plugin
3. Set environment variables
4. Deploy

### Health Check

```
GET /health
```

### Status Endpoint

```
GET /status
```

## Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Set environment variables
cp .env.example .env
# Edit .env with your keys

# Run
python main.py
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check for Railway |
| `GET /status` | Current bot status, sentiment, positions |
| `GET /trades` | Today's trades |

## Disclaimer

This is for educational purposes only. Trading involves substantial risk of loss. Paper trade first and never risk money you cannot afford to lose.

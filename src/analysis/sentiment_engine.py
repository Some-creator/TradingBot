"""Sentiment analysis engine using Claude Haiku."""

import json
import logging
from datetime import datetime
from typing import Optional

import anthropic
import yfinance as yf

from src.config import config
from src.models import SentimentScore, Bias

logger = logging.getLogger(__name__)

# Emergency keywords that should pause trading
EMERGENCY_KEYWORDS = [
    "breaking",
    "halted",
    "crash",
    "circuit breaker",
    "flash crash",
    "emergency",
    "war",
    "attack",
    "default",
]

# Macro event keywords that trigger NO_TRADE
MACRO_EVENTS = [
    "fomc",
    "fed meeting",
    "fed decision",
    "rate decision",
    "cpi",
    "inflation data",
    "nfp",
    "non-farm payroll",
    "jobs report",
    "employment report",
]


class SentimentEngine:
    """Analyzes market sentiment using Claude Haiku and technical indicators."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or config.claude_api_key
        self._client: Optional[anthropic.Anthropic] = None

    def _get_client(self) -> anthropic.Anthropic:
        """Get or create Anthropic client."""
        if not self._client:
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    async def analyze_sentiment(
        self,
        headlines: list[str],
        overnight_high: float,
        overnight_low: float,
        premarket_volume: int,
        current_price: float,
    ) -> SentimentScore:
        """
        Analyze market sentiment from news headlines and market data.

        Args:
            headlines: List of pre-market news headlines
            overnight_high: Overnight session high price
            overnight_low: Overnight session low price
            premarket_volume: Pre-market trading volume
            current_price: Current SPY/QQQ price

        Returns:
            SentimentScore with bias determination
        """
        # Check for emergency keywords
        all_text = " ".join(headlines).lower()
        emergency_detected = any(kw in all_text for kw in EMERGENCY_KEYWORDS)

        # Check for macro event day
        is_macro_day = any(kw in all_text for kw in MACRO_EVENTS)

        if is_macro_day:
            logger.warning("Macro event day detected - NO TRADE")
            return SentimentScore(
                timestamp=datetime.now(),
                llm_score=0,
                trend_adjustment=0,
                vix_bias="neutral",
                final_score=0,
                bias=Bias.NO_TRADE,
                rationale="Macro event day (FOMC/CPI/NFP) - trading suspended",
                is_macro_event_day=True,
                emergency_keywords_detected=emergency_detected,
            )

        # Get LLM sentiment score
        llm_score, rationale = await self._get_llm_sentiment(
            headlines, overnight_high, overnight_low, premarket_volume
        )

        # Get trend adjustment (price vs 20-day MA)
        trend_adjustment = self._get_trend_adjustment(current_price)

        # Get VIX bias
        vix_value, vix_bias = self._get_vix_bias()

        # Calculate final score
        final_score = llm_score + trend_adjustment

        # Determine bias
        if emergency_detected:
            bias = Bias.NO_TRADE
            rationale = f"Emergency keywords detected. Original: {rationale}"
        elif final_score > config.final_bullish_threshold:
            bias = Bias.BULLISH
        elif final_score < config.final_bearish_threshold:
            bias = Bias.BEARISH
        else:
            bias = Bias.NEUTRAL

        return SentimentScore(
            timestamp=datetime.now(),
            llm_score=llm_score,
            trend_adjustment=trend_adjustment,
            vix_bias=vix_bias,
            final_score=final_score,
            bias=bias,
            rationale=rationale,
            is_macro_event_day=is_macro_day,
            emergency_keywords_detected=emergency_detected,
        )

    async def _get_llm_sentiment(
        self,
        headlines: list[str],
        overnight_high: float,
        overnight_low: float,
        premarket_volume: int,
    ) -> tuple[int, str]:
        """
        Get sentiment score from Claude Haiku.

        Returns:
            Tuple of (score, rationale)
        """
        if not headlines:
            return 0, "No headlines available for analysis"

        headlines_text = "\n".join(f"- {h}" for h in headlines[:20])

        prompt = f"""Analyze these pre-market headlines for SPY/QQQ market sentiment.

Headlines:
{headlines_text}

Market Data:
- Overnight High: ${overnight_high:.2f}
- Overnight Low: ${overnight_low:.2f}
- Pre-market Volume: {premarket_volume:,}

Output a single JSON object with:
- "score": integer from -100 (extremely bearish) to +100 (extremely bullish)
- "rationale": one sentence explaining the score

Score guidelines:
- +80 to +100: Very bullish (strong earnings beats, major positive news)
- +40 to +79: Bullish (positive economic data, sector strength)
- +1 to +39: Slightly bullish (mixed but leaning positive)
- 0: Neutral (no clear direction)
- -1 to -39: Slightly bearish (mixed but leaning negative)
- -40 to -79: Bearish (negative economic data, sector weakness)
- -80 to -100: Very bearish (major negative events, recession fears)

Respond with ONLY the JSON object, no other text."""

        try:
            client = self._get_client()
            response = client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )

            # Parse response
            response_text = response.content[0].text.strip()

            # Handle potential markdown code blocks
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1])

            result = json.loads(response_text)
            score = max(-100, min(100, int(result.get("score", 0))))
            rationale = result.get("rationale", "No rationale provided")

            logger.info(f"LLM sentiment score: {score} - {rationale}")
            return score, rationale

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response: {e}")
            return 0, "Failed to parse sentiment response"
        except Exception as e:
            logger.error(f"LLM sentiment analysis failed: {e}")
            return 0, f"Sentiment analysis error: {str(e)}"

    def _get_trend_adjustment(self, current_price: float, symbol: str = "SPY") -> int:
        """
        Get trend adjustment based on price vs 20-day MA.

        Returns:
            +10 if price > 20-day MA, -10 if below, 0 on error
        """
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1mo")

            if len(hist) < 20:
                return 0

            ma_20 = hist["Close"].tail(20).mean()

            if current_price > ma_20:
                logger.debug(f"Price ${current_price:.2f} > 20-day MA ${ma_20:.2f}: +10")
                return 10
            else:
                logger.debug(f"Price ${current_price:.2f} < 20-day MA ${ma_20:.2f}: -10")
                return -10

        except Exception as e:
            logger.error(f"Failed to calculate trend adjustment: {e}")
            return 0

    def _get_vix_bias(self) -> tuple[float, str]:
        """
        Get VIX-based bias adjustment.

        Returns:
            Tuple of (vix_value, bias_string)
        """
        try:
            vix = yf.Ticker("^VIX")
            vix_data = vix.history(period="1d")

            if vix_data.empty:
                return 0.0, "neutral"

            vix_value = vix_data["Close"].iloc[-1]

            if vix_value < config.vix_bullish_threshold:
                bias = "bullish"
            elif vix_value > config.vix_bearish_threshold:
                bias = "bearish"
            else:
                bias = "neutral"

            logger.debug(f"VIX: {vix_value:.2f} - Bias: {bias}")
            return vix_value, bias

        except Exception as e:
            logger.error(f"Failed to get VIX data: {e}")
            return 0.0, "neutral"

    async def check_vix_explosion(self) -> bool:
        """
        Check if VIX has exploded > 10% intraday (shut-off condition).

        Returns:
            True if VIX explosion detected
        """
        try:
            vix = yf.Ticker("^VIX")
            vix_data = vix.history(period="1d", interval="1m")

            if len(vix_data) < 2:
                return False

            open_price = vix_data["Open"].iloc[0]
            current_price = vix_data["Close"].iloc[-1]

            pct_change = abs(current_price - open_price) / open_price * 100

            if pct_change > config.vix_explosion_pct:
                logger.warning(f"VIX explosion detected: {pct_change:.2f}% change")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to check VIX explosion: {e}")
            return False

    async def get_quick_sentiment_update(self) -> tuple[int, bool]:
        """
        Quick sentiment check without full LLM analysis.
        Used for real-time emergency detection.

        Returns:
            Tuple of (sentiment_direction, emergency_detected)
            sentiment_direction: 1 (bullish), -1 (bearish), 0 (neutral)
        """
        # This would integrate with a real news feed API
        # For now, return neutral with no emergency
        return 0, False

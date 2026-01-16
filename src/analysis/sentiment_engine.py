"""
Sentiment Analysis Engine using Claude Haiku.
Analyzes pre-market news to determine daily trading bias.
"""

import asyncio
from datetime import datetime, date
from typing import Optional, Tuple
import json
from loguru import logger
from anthropic import AsyncAnthropic

from src.models import MarketBias, Direction
from src.config import api, trading


class SentimentEngine:
    """
    Determines daily trading bias using Claude Haiku for sentiment analysis.
    
    Combines:
    - LLM sentiment score (-100 to +100)
    - Technical overlay (20-day MA filter)
    - VIX filter
    - Macro calendar check
    """
    
    MACRO_EVENT_KEYWORDS = [
        "FOMC", "Fed", "CPI", "NFP", "Non-Farm", "Powell", 
        "rate decision", "inflation report", "employment report"
    ]
    
    def __init__(self):
        self._client: Optional[AsyncAnthropic] = None
        self._cached_bias: Optional[MarketBias] = None
    
    async def _get_client(self) -> AsyncAnthropic:
        """Get or create Anthropic client."""
        if self._client is None:
            self._client = AsyncAnthropic(api_key=api.anthropic_api_key)
        return self._client
    
    async def analyze_headlines(self, headlines: list[str]) -> Tuple[int, str]:
        """
        Analyze headlines using Claude Haiku.
        
        Returns:
            (score, rationale) where score is -100 to +100
        """
        if not headlines:
            return 0, "No headlines to analyze"
        
        headlines_text = "\n".join([f"- {h}" for h in headlines[:20]])
        
        prompt = f"""Analyze these financial news headlines for SPY/QQQ market sentiment.

Headlines:
{headlines_text}

Instructions:
1. Consider the overall market impact of these headlines
2. Focus on factors that would affect SPY (S&P 500) and QQQ (Nasdaq 100)
3. Output ONLY a JSON object with this exact format:
{{"score": <integer from -100 to +100>, "rationale": "<one sentence explanation>"}}

Where:
- score > 50: Strong bullish (risk-on, earnings beats, stimulus, rate cuts)
- score 20-50: Mildly bullish
- score -20 to 20: Neutral/mixed
- score -50 to -20: Mildly bearish
- score < -50: Strong bearish (risk-off, geopolitical, rate hikes, recession fears)

JSON output:"""

        try:
            client = await self._get_client()
            
            response = await client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Parse JSON from response
            response_text = response.content[0].text.strip()
            
            # Handle potential JSON formatting issues
            if not response_text.startswith("{"):
                # Try to extract JSON from response
                start = response_text.find("{")
                end = response_text.rfind("}") + 1
                if start != -1 and end > start:
                    response_text = response_text[start:end]
            
            result = json.loads(response_text)
            score = int(result.get("score", 0))
            rationale = result.get("rationale", "No rationale provided")
            
            # Clamp score to valid range
            score = max(-100, min(100, score))
            
            logger.info(f"Sentiment analysis: {score} - {rationale}")
            return score, rationale
            
        except Exception as e:
            logger.error(f"Error in sentiment analysis: {e}")
            return 0, f"Analysis error: {str(e)}"
    
    def check_macro_event_day(self, headlines: list[str]) -> bool:
        """Check if today has a major macro event that should pause trading."""
        combined = " ".join(headlines).lower()
        
        for keyword in self.MACRO_EVENT_KEYWORDS:
            if keyword.lower() in combined:
                logger.warning(f"Macro event detected: {keyword}")
                return True
        
        return False
    
    async def calculate_bias(
        self, 
        headlines: list[str],
        current_price: float,
        ma_20: float,
        vix: float
    ) -> MarketBias:
        """
        Calculate the full daily bias score.
        
        Scoring:
        - LLM Score: -100 to +100
        - MA Filter: +10 if price > 20MA, -10 if below
        - VIX Filter: preference adjustment (no direct score change)
        
        Final Decision:
        - > +30: Bullish (Longs only)
        - < -30: Bearish (Shorts only)
        - Else: Neutral
        """
        today = date.today().isoformat()
        
        # Check for macro events
        is_macro_day = self.check_macro_event_day(headlines)
        
        if is_macro_day:
            bias = MarketBias(
                date=today,
                score=0,
                direction=Direction.NO_TRADE,
                rationale="Macro event day - trading paused",
                vix_level=vix,
                above_20ma=current_price > ma_20,
                is_macro_event_day=True
            )
            self._cached_bias = bias
            return bias
        
        # Get LLM sentiment
        llm_score, rationale = await self.analyze_headlines(headlines)
        
        # Apply MA filter
        above_ma = current_price > ma_20
        ma_adjustment = 10 if above_ma else -10
        
        final_score = llm_score + ma_adjustment
        
        # Determine direction
        if final_score > 30:
            direction = Direction.LONG
        elif final_score < -30:
            direction = Direction.SHORT
        else:
            direction = Direction.NEUTRAL
        
        # VIX adjustment (preference, not hard filter)
        vix_note = ""
        if vix < 15:
            vix_note = " (Low VIX favors longs)"
        elif vix > 25:
            vix_note = " (High VIX favors shorts)"
        
        bias = MarketBias(
            date=today,
            score=final_score,
            direction=direction,
            rationale=f"{rationale}{vix_note}",
            vix_level=vix,
            above_20ma=above_ma,
            is_macro_event_day=False
        )
        
        self._cached_bias = bias
        logger.info(f"Daily Bias: {direction.value} (Score: {final_score})")
        
        return bias
    
    def get_cached_bias(self) -> Optional[MarketBias]:
        """Get the cached daily bias."""
        return self._cached_bias
    
    def allows_direction(self, target_direction: Direction) -> bool:
        """Check if the current bias allows a trade in the given direction."""
        if self._cached_bias is None:
            return False
        
        bias_dir = self._cached_bias.direction
        
        if bias_dir == Direction.NO_TRADE:
            return False
        
        if bias_dir == Direction.NEUTRAL:
            # Neutral allows both directions (mean reversion)
            return True
        
        return bias_dir == target_direction


# Global instance
sentiment_engine = SentimentEngine()

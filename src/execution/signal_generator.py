"""Signal generator for entry/exit decisions."""

import logging
from datetime import datetime
from typing import Optional

from src.config import config
from src.models import (
    PriceCandle,
    GammaLevels,
    SentimentScore,
    FairValueGap,
    EntrySignal,
    Bias,
    TradeDirection,
    SignalType,
    FVGType,
)
from src.analysis.fvg_detector import FVGDetector
from src.analysis.gamma_calculator import GammaCalculator

logger = logging.getLogger(__name__)


class SignalGenerator:
    """
    Generates entry and exit signals based on the rule-based system.

    Entry Logic:
    - Sentiment sets allowed direction (Bullish = Longs, Bearish = Shorts)
    - Gamma levels set allowed locations (Put Wall = Support, Call Wall = Resistance)
    - Price action triggers execution (Sweep & Reclaim or IFVG Flip)
    """

    def __init__(
        self,
        fvg_detector: FVGDetector,
        gamma_calculator: GammaCalculator,
    ):
        self.fvg_detector = fvg_detector
        self.gamma_calculator = gamma_calculator

        # Track sweep state per symbol
        self._sweep_state: dict[str, dict] = {}

    def check_entry_signal(
        self,
        symbol: str,
        candles: list[PriceCandle],
        gamma_levels: GammaLevels,
        sentiment: SentimentScore,
    ) -> Optional[EntrySignal]:
        """
        Check if current conditions generate an entry signal.

        Args:
            symbol: Stock symbol
            candles: Recent price candles (newest last)
            gamma_levels: Current gamma exposure levels
            sentiment: Current sentiment analysis

        Returns:
            EntrySignal if conditions met, None otherwise
        """
        if len(candles) < 3:
            return None

        # Filter: Check if bias allows trading
        if sentiment.bias in (Bias.NO_TRADE, Bias.NEUTRAL):
            return None

        current_candle = candles[-1]
        price = current_candle.close

        # Get allowed direction based on bias
        allowed_direction = (
            TradeDirection.LONG if sentiment.bias == Bias.BULLISH else TradeDirection.SHORT
        )

        # Detect any new FVGs
        self.fvg_detector.detect_fvg(candles)

        # Check for IFVG interactions
        self.fvg_detector.check_fvg_interaction(symbol, current_candle)

        # Check gamma level interactions
        active_level = self.gamma_calculator.get_active_level(gamma_levels, price)

        if not active_level:
            return None

        level_name, level_price, zone = active_level

        # Apply direction filter based on level
        if allowed_direction == TradeDirection.LONG:
            # Longs: Only at Put Wall (support) or Zero Gamma breakout
            if level_name == "call_wall":
                return None  # Don't buy at resistance
        else:
            # Shorts: Only at Call Wall (resistance) or Put Wall breakdown
            if level_name == "put_wall":
                return None  # Don't short at support

        # Check for entry triggers
        signal = self._check_triggers(
            symbol=symbol,
            candles=candles,
            direction=allowed_direction,
            level_name=level_name,
            level_price=level_price,
            zone=zone,
            gamma_levels=gamma_levels,
        )

        return signal

    def _check_triggers(
        self,
        symbol: str,
        candles: list[PriceCandle],
        direction: TradeDirection,
        level_name: str,
        level_price: float,
        zone: tuple[float, float],
        gamma_levels: GammaLevels,
    ) -> Optional[EntrySignal]:
        """
        Check for entry trigger variants.

        Variant A: Sweep & Reclaim
        Variant B: IFVG Flip (High Confidence)
        """
        current_candle = candles[-1]
        prev_candle = candles[-2] if len(candles) > 1 else None

        # Initialize sweep state for symbol
        if symbol not in self._sweep_state:
            self._sweep_state[symbol] = {}

        sweep_key = f"{level_name}_{level_price}"

        # LONG SETUP (at Put Wall / Support)
        if direction == TradeDirection.LONG:
            # Check if price swept into/through the zone
            swept_zone = current_candle.low <= zone[1]  # Touched or went below zone

            if swept_zone:
                # Mark sweep
                self._sweep_state[symbol][sweep_key] = {
                    "timestamp": current_candle.timestamp,
                    "sweep_low": current_candle.low,
                }

            # Check for reclaim (Variant A)
            if sweep_key in self._sweep_state[symbol]:
                sweep_data = self._sweep_state[symbol][sweep_key]

                # Reclaim: Close back above the zone after sweep
                if current_candle.close > zone[1] and current_candle.is_bullish():
                    # Additional confirmation: Volume or strong candle
                    if self._is_valid_rejection_candle(current_candle, direction):
                        signal = self._create_signal(
                            symbol=symbol,
                            direction=direction,
                            signal_type=SignalType.SWEEP_RECLAIM,
                            trigger_candle=current_candle,
                            sweep_low=sweep_data["sweep_low"],
                            level_name=level_name,
                            gamma_levels=gamma_levels,
                        )
                        # Clear sweep state
                        del self._sweep_state[symbol][sweep_key]
                        return signal

            # Check for IFVG Flip (Variant B - High Confidence)
            expected_fvg_type = FVGType.BULLISH  # We want bearish->bullish inversion
            ifvg = self.fvg_detector.detect_ifvg_signal(
                symbol, current_candle, expected_fvg_type
            )

            if ifvg and sweep_key in self._sweep_state[symbol]:
                sweep_data = self._sweep_state[symbol][sweep_key]
                signal = self._create_signal(
                    symbol=symbol,
                    direction=direction,
                    signal_type=SignalType.IFVG_FLIP,
                    trigger_candle=current_candle,
                    sweep_low=sweep_data["sweep_low"],
                    level_name=level_name,
                    gamma_levels=gamma_levels,
                    ifvg=ifvg,
                )
                del self._sweep_state[symbol][sweep_key]
                return signal

        # SHORT SETUP (at Call Wall / Resistance)
        else:
            # Check if price swept into/through the zone
            swept_zone = current_candle.high >= zone[0]  # Touched or went above zone

            if swept_zone:
                self._sweep_state[symbol][sweep_key] = {
                    "timestamp": current_candle.timestamp,
                    "sweep_high": current_candle.high,
                }

            # Check for reclaim (Variant A)
            if sweep_key in self._sweep_state[symbol]:
                sweep_data = self._sweep_state[symbol][sweep_key]

                # Reclaim: Close back below the zone after sweep
                if current_candle.close < zone[0] and current_candle.is_bearish():
                    if self._is_valid_rejection_candle(current_candle, direction):
                        signal = self._create_signal(
                            symbol=symbol,
                            direction=direction,
                            signal_type=SignalType.SWEEP_RECLAIM,
                            trigger_candle=current_candle,
                            sweep_high=sweep_data["sweep_high"],
                            level_name=level_name,
                            gamma_levels=gamma_levels,
                        )
                        del self._sweep_state[symbol][sweep_key]
                        return signal

            # Check for IFVG Flip (Variant B)
            expected_fvg_type = FVGType.BEARISH  # We want bullish->bearish inversion
            ifvg = self.fvg_detector.detect_ifvg_signal(
                symbol, current_candle, expected_fvg_type
            )

            if ifvg and sweep_key in self._sweep_state[symbol]:
                sweep_data = self._sweep_state[symbol][sweep_key]
                signal = self._create_signal(
                    symbol=symbol,
                    direction=direction,
                    signal_type=SignalType.IFVG_FLIP,
                    trigger_candle=current_candle,
                    sweep_high=sweep_data["sweep_high"],
                    level_name=level_name,
                    gamma_levels=gamma_levels,
                    ifvg=ifvg,
                )
                del self._sweep_state[symbol][sweep_key]
                return signal

        return None

    def _is_valid_rejection_candle(
        self, candle: PriceCandle, direction: TradeDirection
    ) -> bool:
        """
        Check if candle is a valid rejection/confirmation candle.

        For longs: Strong bullish candle with body size
        For shorts: Strong bearish candle with body size
        """
        body_pct = candle.body_size()

        # Minimum body size for confirmation
        if body_pct < 0.05:  # At least 0.05% body
            return False

        # Check direction matches
        if direction == TradeDirection.LONG:
            return candle.is_bullish()
        else:
            return candle.is_bearish()

    def _create_signal(
        self,
        symbol: str,
        direction: TradeDirection,
        signal_type: SignalType,
        trigger_candle: PriceCandle,
        level_name: str,
        gamma_levels: GammaLevels,
        sweep_low: float = None,
        sweep_high: float = None,
        ifvg: FairValueGap = None,
    ) -> EntrySignal:
        """Create an entry signal with calculated stops and targets."""
        entry_price = trigger_candle.close

        # Calculate stop loss
        if direction == TradeDirection.LONG:
            # Stop below sweep low with buffer
            base_stop = sweep_low if sweep_low else trigger_candle.low
            stop_buffer = base_stop * config.stop_loss_buffer_pct / 100
            stop_loss = base_stop - stop_buffer

            # Check max stop loss
            max_stop = entry_price * (1 - config.max_stop_loss_pct / 100)
            stop_loss = max(stop_loss, max_stop)

            # TP1: Fixed percentage
            tp1_price = entry_price * (1 + config.tp1_pct / 100)

            # TP2: Call Wall (resistance) - must exit longs here
            tp2_price = gamma_levels.call_wall

        else:
            # Stop above sweep high with buffer
            base_stop = sweep_high if sweep_high else trigger_candle.high
            stop_buffer = base_stop * config.stop_loss_buffer_pct / 100
            stop_loss = base_stop + stop_buffer

            # Check max stop loss
            max_stop = entry_price * (1 + config.max_stop_loss_pct / 100)
            stop_loss = min(stop_loss, max_stop)

            # TP1: Fixed percentage
            tp1_price = entry_price * (1 - config.tp1_pct / 100)

            # TP2: Put Wall (support) - must cover shorts here
            tp2_price = gamma_levels.put_wall

        # Determine confidence
        confidence = "high" if signal_type == SignalType.IFVG_FLIP else "normal"

        signal = EntrySignal(
            timestamp=datetime.now(),
            symbol=symbol,
            direction=direction,
            signal_type=signal_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            trigger_candle=trigger_candle,
            gamma_level=level_name,
            confidence=confidence,
            ifvg=ifvg,
        )

        logger.info(
            f"Entry signal generated: {direction.value.upper()} {symbol} @ ${entry_price:.2f} "
            f"[{signal_type.value}] SL: ${stop_loss:.2f} TP1: ${tp1_price:.2f}"
        )

        return signal

    def check_exit_conditions(
        self,
        symbol: str,
        current_price: float,
        entry_price: float,
        entry_time: datetime,
        direction: TradeDirection,
        stop_loss: float,
        tp1_price: float,
        tp2_price: float,
        gamma_levels: GammaLevels,
        partial_tp1_taken: bool = False,
    ) -> Optional[tuple[str, float]]:
        """
        Check if exit conditions are met.

        Returns:
            Tuple of (exit_reason, exit_price) or None
        """
        now = datetime.now()
        time_in_trade_mins = (now - entry_time).total_seconds() / 60

        # Check stop loss
        if direction == TradeDirection.LONG:
            if current_price <= stop_loss:
                return ("stop_loss", current_price)
        else:
            if current_price >= stop_loss:
                return ("stop_loss", current_price)

        # Check TP1 (if not already taken)
        if not partial_tp1_taken:
            if direction == TradeDirection.LONG:
                if current_price >= tp1_price:
                    return ("tp1", current_price)
            else:
                if current_price <= tp1_price:
                    return ("tp1", current_price)

        # Check TP2 (gamma level exit)
        if tp2_price:
            if direction == TradeDirection.LONG:
                # Exit longs at call wall
                call_zone = self.gamma_calculator.get_zone(gamma_levels.call_wall)
                if current_price >= call_zone[0]:
                    return ("tp2_gamma_level", current_price)
            else:
                # Cover shorts at put wall
                put_zone = self.gamma_calculator.get_zone(gamma_levels.put_wall)
                if current_price <= put_zone[1]:
                    return ("tp2_gamma_level", current_price)

        # Check time stop (30 min with < 0.1% profit)
        if time_in_trade_mins >= config.time_stop_mins:
            pnl_pct = self._calculate_pnl_pct(entry_price, current_price, direction)
            if pnl_pct < 0.1:
                logger.info(f"Time stop triggered: {time_in_trade_mins:.0f} mins, {pnl_pct:.2f}% PnL")
                return ("time_stop", current_price)

        # Check quick exit (5 min gamma trap defense)
        if time_in_trade_mins >= config.quick_exit_mins:
            pnl_pct = self._calculate_pnl_pct(entry_price, current_price, direction)
            # If losing after 5 mins, exit (gamma thesis failed)
            if pnl_pct < -0.05:
                logger.info(f"Quick exit triggered: {time_in_trade_mins:.0f} mins, {pnl_pct:.2f}% PnL")
                return ("quick_exit_gamma_trap", current_price)

        return None

    def _calculate_pnl_pct(
        self, entry_price: float, current_price: float, direction: TradeDirection
    ) -> float:
        """Calculate P&L percentage for a position."""
        if direction == TradeDirection.LONG:
            return (current_price - entry_price) / entry_price * 100
        else:
            return (entry_price - current_price) / entry_price * 100

    def should_move_stop_to_breakeven(
        self,
        entry_price: float,
        current_price: float,
        direction: TradeDirection,
        entry_time: datetime,
    ) -> bool:
        """
        Check if stop loss should be moved to breakeven.

        Move to breakeven after significant profit and time in trade.
        """
        now = datetime.now()
        time_in_trade_mins = (now - entry_time).total_seconds() / 60

        # Must be in trade for at least 30 minutes
        if time_in_trade_mins < 30:
            return False

        pnl_pct = self._calculate_pnl_pct(entry_price, current_price, direction)

        # Move to breakeven if > 0.2% profit
        return pnl_pct > 0.2

    def clear_sweep_state(self, symbol: str = None) -> None:
        """Clear sweep state for end of day or symbol."""
        if symbol:
            self._sweep_state.pop(symbol, None)
        else:
            self._sweep_state.clear()

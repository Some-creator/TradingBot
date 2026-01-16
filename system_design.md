# Rule-Based Intraday Trading System Design

## 1. High-Level System Architecture

The system is a strictly hierarchical decision tree. Data sources are siloed to prevent noise contamination.

### Data Inputs
1.  **Market State (Frequency: Daily/Pre-market)**
    *   **Source:** Financial News API / Sentiment Aggregator.
    *   **Function:** Determines the allowed **Direction** (Long/Short/None).
    *   **Output:** `Daily_Bias_Score`.
2.  **Structural Map (Frequency: 15-min, Delayed)**
    *   **Source:** CBOE Options Data / Gamma Exposure (GEX) Calculation.
    *   **Function:** Determines the allowed **Locations** (Levels).
    *   **Output:** `Key_Levels` (Call Wall, Put Wall, Zero Gamma, Vol Trigger).
3.  **Execution Trigger (Frequency: 1-sec Real-time)**
    *   **Source:** yfinance (SPY, QQQ Price & Volume).
    *   **Function:** Determines the **Timing**.
    *   **Output:** `Entry_Signal`, `Exit_Signal`.

### Flow
`Sentiment` -> Sets *Bias* (Filter)
`Options Data` -> Sets *Levels* (Zones)
`Price Action` -> Triggers *Event* (Execution)

**Constraint:** The Logic Engine checks: `IF (Bias allows Direction) AND (Price is at Level) AND (Price Action confirms) THEN Execute`.

---

## 2. Sentiment & News Quantification (Daily Bias)

We will leverage **Claude Haiku** (via API) to analyze pre-market news flow. This provides significantly better context than simple keyword matching.

### LLM Implementation (Claude Haiku)
*   **Input**: Top 20 pre-market headlines (Bloomberg/Benzinga/Reuters) + Overnight Low/High + Pre-market Volume.
*   **Prompt**: "Analyze these headlines for SPY/QQQ. Output a single JSON score from -100 (Bearish) to +100 (Bullish) and a one-sentence rationale."
*   **Cost/Speed**: Haiku is extremely cheap and fast, allowing us to run this check every 5 minutes during pre-market (8:00 AM - 9:25 AM) to track sentiment shifts.

### Scoring Model (-100 to +100)
*   **Macro Calendar (Hard Filter)**:
    *   FOMC / CPI / NFP Day: **NO TRADE** until event window passes (or entire day).
*   **LLM Sentiment Output**:
    *   Replaces the manual keyword filter.
    *   Score > +60: **Strong Bullish**.
    *   Score < -60: **Strong Bearish**.
    *   Score -20 to +20: **Neutral/Choppy**.
*   **Trend Filter (Technical Overlay)**:
    *   Price > 20-Day MA: +10 Points to LLM Score.
    *   Price < 20-Day MA: -10 Points to LLM Score.
*   **VIX Filter**:
    *   VIX < 15: Bullish Bias preference.
    *   VIX > 25: Bearish Bias preference.

**Decision**:
*   Final Score > +30: **Bullish Bias** (Longs Only).
*   Final Score < -30: **Bearish Bias** (Shorts Only).
*   Else: **Neutral** (Mean Reversion / Range Trading).

---

## 3. Handling 15-Minute Delayed GEX & Options Data

**The Brutal Reality:** You cannot scalp "gamma flows" (hedging pressure) with 15-minute delayed data. By the time you see the gamma shift, the dealers have already hedged.
**The Workaround:** Focus on **Structural OI**, not dynamic Greek changes. Open Interest (OI) is sticky; Gamma is volatile.

### Interpretation Rules
1.  **Walls are Static**: The "Call Wall" (Strike with max Call OI) and "Put Wall" (Strike with max Put OI) are unlikely to move drastically intraday unless volume is massive. We treat these as **hard barriers**.
    *   *Delayed Data Impact*: Low. These levels effectively exist from the pre-market OI file.

### STRATEGY DEFINITION: The "Sticky Wall"
**Concept**: Open Interest (OI) is "sticky"—it represents established positions that take time to unwind. Unlike Gamma (which changes instantly with price/volatility), OI positions are often held for days/weeks.
**Market Mechanics**:
*   **Dealer Hedging**: Market Makers (Dealers) are usually the counterparty to large OI.
    *   At **Put Walls**, dealers are typically Short Puts. As price drops to the strike, they buy underlying (futures/stock) to hedge delta, creating natural **Support**.
    *   At **Call Walls**, dealers are typically Short Calls. As price rises to the strike, they sell underlying to cap upside, creating natural **Resistance**.
**Why it works with Delayed Data**:
*   Since the *position* (OI) doesn't vanish in 15 minutes, the *incentive* for the dealer to defend that level remains valid even if your GEX feed is lagging. We rely on the *existence of the wall*, not the split-second change in gamma.
**Execution Rules**:
*   **Fade the Wall**: Primary strategy. Place Limit buys in front of Put Walls and Limit sells in front of Call Walls (confirmed by price action).
*   **Wall Dissolution**: If high volume trades *through* the wall and price **holds** beyond it for >15 mins, the Wall is "broken" (dealers are overrun or have hedged). We then stop fading.

2.  **Zero Gamma / Flip Point**: This moves with price and IV.
    *   *Delayed Data Impact*: **High**.
    *   *Mitigation*: We calculate the Flip Point based on the *morning* snapshot and widen the "Zone of Uncertainty" around it. We do NOT trade *at* the Flip Point line, strictly *away* from it.
3.  **Net GEX Interpretation**:
    *   **Positive GEX**: Implies mean reversion. Price sticky.
        *   *Strategy*: Buy Support, Sell Resistance.
    *   **Negative GEX**: Implies volatility/trend. Price slippery.
        *   *Strategy*: Breakout/Breakdown following.

---

## 4. Daily Regimes & Rules

### Bullish Bias Day
*   **Allowed Action**: BUY (Long SPY/QQQ).
*   **Zones**: Buy at Put Wall (Support) or Buy Breakout of Zero Gamma (if transitioning from Neg to Pos).
*   **Prohibited**: Shorting pumps.

### Bearish Bias Day
*   **Allowed Action**: SELL SHORT (Short SPY/QQQ).
*   **Zones**: Short at Call Wall (Resistance) or Short Breakdown of Put Wall.
*   **Prohibited**: Buying dips.

### No-Trade Day / Choppy
*   **Triggers**:
    *   Major Economic Event (Fed).
    *   Conflicting Signals: Sentiment is Bullish, but we are below the Put Wall (Structural Bearish).
    *   Low Liquidity (Holiday).
    *   GEX is "Flat" (No significant gamma concentration).

---

## 5. Converting Gamma Levels to Price Zones

Since data is delayed, a single price level (e.g., $500.00) is a fallacy. We use **Zones**.

*   **Zone Width**: +/- 0.15% of the Spot Price.
    *   *Example*: SPY $500. Call Wall $505.
    *   Resistance Zone: $504.25 - $505.75.
*   **Logic**:
    *   Touch: Price enters Zone.
    *   Rejection: Price exits Zone in opposite direction.
    *   Breach: Price closes 15-min candle *beyond* the Zone.

---

## 6. Entry Logic (Interaction Rules)

We do not predict. We wait for **Interaction**. We strictly use **1-Minute Candles** to define the "Sweep".

### Technical Engine: FVG & IFVG Logic
To increase precision, we track **Fair Value Gaps (FVG)**.

**1. Calculation & Identification (3-Candle Pattern)**
*   **Bullish FVG**: `Low[i-2] > High[i]`. The Gap is `(High[i], Low[i-2])`.
*   **Bearish FVG**: `High[i-2] < Low[i]`. The Gap is `(Low[i], High[i-2])`.
*   **Inversion FVG (IFVG)**:
    *   Occurs when a candle **Closes** *through* an active FVG.
    *   *Example*: Price closes *above* a Bearish FVG. That Bearish FVG flips to **Bullish Support**.

**2. Data Storage & Management**
*   **Structure**: A simple list/deque of active objects:
    `{ "id": timestamp, "top": price, "bottom": price, "type": "bull/bear", "status": "open/inverted" }`
*   **Lifecycle**:
    *   **Created**: On Candle Close.
    *   **Mitigated**: If price touches the gap (fill), mark as potential support/resistance.
    *   **Inverted**: If price closes beyond the gap, flip the `type` and mark `status="inverted"`.
    *   **Pruned**: If gap is fully filled and price moves away, or > 2 hours old (intraday only).

**3. The Entry Trigger Variants**
*   **Variant A (Sweep & Reclaim)**:
    *   Candle sweeps level -> Candle closes back inside.
    *   *Confidence*: Normal.
*   **Variant B (The IFVG Flip - High Confidence)**:
    *   Price sweeps Gamma Level.
    *   Price **creates an IFVG** rejecting the level (e.g., breaks a rigid Bearish FVG to the upside at Support).
    *   **Action**: Enter on the Close of the Inversion Candle.
    *   *Stop Loss*: Below the IFVG bottom.

**Long Setup (Bullish Bias):**
1.  **The Sweep**: Price drops *into* or *through* the **Put Wall Zone** or **Positive GEX Support**.
2.  **The Trigger**:
    *   **Variant A**: Reclaim (Close above Zone/Wick).
    *   **Variant B**: **Bullish IFVG** forms (Price closes above a nearby Bearish FVG).
3.  **Entry Order**: Market Buy on the Close.

**Short Setup (Bearish Bias):**
1.  **The Sweep**: Price rallies *into* or *through* the **Call Wall Zone**.
2.  **The Trigger**:
    *   **Variant A**: Reclaim (Close below Zone/Wick).
    *   **Variant B**: **Bearish IFVG** forms (Price closes below a nearby Bullish FVG).
3.  **Entry Order**: Market Short on the Close.

---

## 7. Take-Profit & Stop-Loss

**Stop Loss (The "Sweep Low" Rule)**:
*   **Placement**: **Strictly** at the Low (for Longs) or High (for Shorts) of the **Trigger/Sweep Candle**.
    *   *Logic*: That wick represents the market's attempt to break the level. If it's visited again, the wall has failed.
    *   *Safety Buffer*: Add 0.01% (roughly $0.05 on SPY) to the wick to avoid tick-perfect stop hunts.
*   **Hard Stop Fallback**: If the Sweep Candle is huge (>0.2%), use a max fixed stop of 0.2% to preserve Ruin Risk.


**Take Profit**:
*   **TP1 (50%)**: +0.3% move (Scalp bank).
*   **TP2 (Gamma Levels - EXIT ONLY)**:
    *   **High Positive Gamma Strike ("The Sponge")**: Dealers dampen volatility here. Momentum usually dies. **Must Sell** Longs here. **Do NOT open Shorts** (trend might just pause, not reverse).
    *   **High Negative Gamma Strike ("The Magnet")**: Volatility expands here. Price moves fast. **Must Cover** Shorts here to lock in speed-profits before the "whipsaw" risk increases. **Do NOT open Longs** (catching a falling knife).
*   **Time Stop**: If trade is < 0.1% profit after 30 minutes, **EXIT**. (Gamma thesis relies on dealer reaction; no reaction = wrong thesis).

---

## 8. Risk Management Framework

*   **Max Trades**: 3 per day. (Overtrading kills edge).
*   **Max Daily Loss**: 1.5% of Account Equity. System Lockout triggered.
*   **Max Drawdown per Trade**: 0.5% of Account Equity.
*   **Shut-Off Conditions**:
    *   2 Consecutive Losses.
    *   VIX explodes > 10% intraday (Model breakage).
    *   Data feed lag > 60 seconds.

---

## 9. Failure Modes

1.  **The "Fake Wall"**:
    *   *Scenario*: 15-min delayed data shows massive Call OI at 510. Real-time, that OI was closed.
    *   *Result*: Price slices through 510 like butter.
    *   *Defense*: We **wait for rejection candle**. If price slices through, no entry trigger occurs. **NEVER** place limit orders blindly at levels.
2.  **News Override**:
    *   *Scenario*: Bias is Bearish. Unexpected good news hits. Algorithm shorts the spike at resistance.
    *   *Result*: Stopped out instantly.
    *   *Defense*: Sentiment/News feed must have an "Emergency Flush" keyword set (e.g., "Breaking", "Halted") to pause trading.
3.  **Gamma Trap**:
    *   *Scenario*: We are in "Positive Gamma" (low vol), but Price drops fast. The "speed" moves us into "Negative Gamma" (high vol) real-time, but data says Positive.
    *   *Result*: We buy the dip, but the market accelerates down.
    *   *Defense*: **Time Stop**. If the bounce doesn't happen in 5 mins, get out.

---

## 10. Deployment (Railway)

The bot is designed to run natively on **Railway**.

1.  **Stateless Architecture**:
    *   **State Persistence**: The bot logic will **NOT** rely on local JSON files for trade state (which vanish on restart).
    *   **Solution**: Use **Railway's Redis** plugin to store `Current_Trades`, `Daily_Loss`, and `Trade_Count`. This ensures if the bot crashes and restarts, it knows it has already traded 2/3 times today.
2.  **Environment Variables**:
    *   Strict separation of secrets. `API_KEY_CLAUDE`, `API_KEY_BROKER`, `REDIS_URL` will be injected at runtime.
    *   Config flags: `TRADING_MODE=PAPER`, `MAX_DAILY_LOSS=1.5`.
3.  **Process Management**:
    *   **Health Checks**: A simple `/health` HTTP endpoint (using Flask/FastAPI) to let the platform know the bot is alive so it doesn't kill the container.
    *   **Graceful Shutdown**: Handle `SIGTERM` signals to close open socket connections and flush logs before the container stops.
4.  **Resource Efficiency**:
    *   **Async Core**: Use `uvloop` for async event loop optimization (Python).
    *   **Computation**: Avoid heavy pandas operations in the hot loop. Pre-compute GEX levels once on a separate thread/process (15-min cycle), keeping the main 1-sec loop lightweight.

---

## 11. Conceptual Intraday Walkthrough

**Scenario**: SPY opening at $500.
**Data**:
*   Sentiment: **Bullish** (Earnings beats).
*   Levels (Delayed): Put Wall $498. Call Wall $505.
*   Bias: Look to BUY dips.

** Timeline**:
*   **09:30 AM**: Market opens. Volatility is high. **NO TRADE** (Wait 30 mins rule).
*   **10:15 AM**: SPY drifts down to $498.50 (Approaching Put Wall Zone $497.25-$498.75).
*   **10:20 AM**: Price touches $498.10.
    *   *Bot Action*: Monitors 1-min chart.
*   **10:22 AM**: Price candles: Red, Red, small Doji (Indecision).
*   **10:23 AM**: Strong Green Candle closes at $498.40 on high volume. Reclaiming VWAP line.
    *   *Trigger*: **BUY SPY**. Stop Loss: $497.90 (Below recent low).
*   **10:45 AM**: SPY moves to $501.00.
*   **Logic**: Is $501 a level? No. Hold.
*   **12:00 PM**: SPY stalls at $502. Sentiment still Bullish.
    *   *Action*: Move Stop to Breakeven.
*   **01:00 PM**: SPY hits $504.50 (Entering Call Wall Resistance Zone).
    *   *Logic*: We do not short (Bias is Bullish), but we SELL LONG implementation to take profit.
    *   *Action*: **Full Exit**.
*   **02:00 PM**: End of Day. +1.3% gain.

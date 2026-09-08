# Five-Factor Intraday Score (0–100)

Every candidate gets a score. Weights: **Regime 20 + Relative Strength 25 +
Volume 20 + Structure 20 + Catalyst 15 = 100.** Trade only when score > 75 and
a valid setup is present.

Guiding principle: **do not predict the market — trade only when several
*independent* signals agree.** Most technical indicators are just
transformations of price, so stacking five moving-average variants is not five
signals — it is one. Regime, relative strength, volume/flow, structure, and
catalyst are genuinely different information sources; that is why they are the
five factors.

---

## A. Market Regime — 20 points

Classify the broad market as **bullish / neutral / bearish**, then only take
trades *with* the regime (long in bull, short in bear; be very selective in
neutral).

Inputs: NIFTY trend, BANK NIFTY trend, price vs VWAP, 20 EMA vs 50 EMA,
opening range, India VIX, advance/decline, sector breadth.

**Example bullish regime (full 20 pts for longs):**
- NIFTY > VWAP
- 20 EMA > 50 EMA
- Advance/decline breadth > 1.3
- NIFTY above its opening-range midpoint

A long in a bearish regime should score near zero here — that alone usually
drops the composite below the 75 gate, which is the point.

---

## B. Relative Strength — 25 points (the heaviest factor)

Do not ask "is the stock rising?" Ask **"is it outperforming both NIFTY and
its sector?"**

- Relative strength vs index: `stock % return − NIFTY % return`
- Sector strength: `stock % return − sector index % return`

A stock up 1.2% while NIFTY is up 0.2% (RS = +1.0) is far more interesting than
one up 1.2% while NIFTY is up 1.1% (RS = +0.1). Award points on the *spread*,
and require positive spread against **both** index and sector for full marks.
The highest-weighted factor because leaders lead and laggards lag intraday.

---

## C. Volume / Flow Confirmation — 20 points

A move on unusual volume is real; a move on average volume is noise.

Inputs: relative volume (vs same-time-of-day average), volume acceleration,
VWAP relationship, price–volume agreement, historical delivery %, futures OI,
block/bulk deals, institutional flow.

Rule of thumb: a breakout needs ≥ ~1.5× normal volume to earn full marks.
Compare against the **same time of day**, not the flat daily average — 10:00
volume is naturally higher than 14:00 volume.

---

## D. Price Structure — 20 points

Award points for a clean, nameable setup — not a vague "it looks bullish."

**Long structures:** Opening Range Breakout, VWAP reclaim, previous-day-high
breakout, consolidation breakout, higher-high/higher-low sequence, pullback to
VWAP/EMA. **Short structures:** the mirror.

Do not double-count overlapping indicators. Price, one trend reference, and one
volatility/structure reference are enough.

---

## E. Catalyst — 15 points

Not required, but it improves the odds of follow-through. The system is **not**
predicting news; it looks for the sequence:

> **Catalyst → unusual volume → price discovery → continuation**

Potential catalysts: earnings/results, management commentary, order wins,
regulatory news, corporate actions, rating changes, sector news, global moves,
commodity moves. Because catalyst is only 15 points, a purely technical trade
can still clear the 75 gate (max 85 without a catalyst).

---

## Worked example

| Factor | Raw read | Score |
|---|---|---:|
| Regime | Bull: NIFTY>VWAP, 20>50 EMA, breadth 1.4 | 18 / 20 |
| Relative strength | +1.1% vs NIFTY, +0.7% vs sector | 22 / 25 |
| Volume | 1.8× same-time avg, rising | 17 / 20 |
| Structure | Clean ORB above prev-day high | 16 / 20 |
| Catalyst | Order-win headline pre-open | 10 / 15 |
| **Composite** | | **83 / 100 → tradeable** |

Compute this with `composite_score()` in
`scripts/intraday_calculator.py`.

## Robustness (do not chase a magic number)

When validating, sweep each threshold rather than fixing one:
- Volume ratio: 1.25× / 1.50× / 1.75× / 2.00×
- Score gate: 70 / 75 / 80
- RS spread minimums

A real edge is a **stable region** where performance is reasonable across the
range — not a single knife-edge parameter that only works at one value. See the
`backtest-expert` skill for the full robustness protocol.

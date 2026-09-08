# Intraday Setups — Entry / Stop / Target Rules

Two setups only. Both require: composite score > 75, R:R ≥ 1:2, and regime
alignment. If any gate fails → **NO TRADE**.

---

## Strategy A — Opening Range Breakout (ORB)

### Define the opening range (first 15 minutes, 09:15–09:30 IST)

- `OR High` = highest traded price 09:15–09:30
- `OR Low`  = lowest traded price 09:15–09:30

Entries begin at/after 09:30 — inside the 09:30–11:30 primary window.

### Long entry — all must hold

1. Price breaks above `OR High`
2. Price > VWAP
3. NIFTY regime supportive (bullish)
4. Stock has strong relative strength (vs NIFTY *and* sector)
5. Volume ≈ 1.5× or more of same-time-of-day normal
6. No immediate major resistance just overhead
7. Composite score > 75
8. R:R ≥ 1:2

### Short entry

Mirror every condition: break below `OR Low`, price < VWAP, bearish regime,
negative relative strength, volume confirmation, no immediate support below.

### Failure modes to respect
- **False breakout / whipsaw:** the reason for the VWAP + volume + regime
  filters. A break of OR High on thin volume, below VWAP, in a weak market is
  exactly the trap.
- **Gap days:** a large gap can make the opening range unusually wide, blowing
  out the stop distance and killing R:R. Let the R:R gate reject it.

---

## Strategy B — VWAP Continuation

Expected to be a core setup. It trades *with* an established intraday trend,
not against it.

### Long sequence

1. NIFTY bullish
2. Sector bullish
3. Stock bullish
4. Stock trading above VWAP
5. A strong impulse move up occurs
6. Price pulls back **toward** VWAP
7. Pullback volume **contracts** (sellers are weak)
8. Buyers return with a **volume expansion**
9. Price resumes upward → enter on confirmation of the resumption

Short setups are symmetrical (below VWAP, impulse down, weak-volume bounce into
VWAP, resumption down).

### Why it works
The pullback-on-contracting-volume then resume-on-expanding-volume sequence is
the observable footprint of trend continuation: the counter-move has no
conviction, the primary move does. Enter on the *resumption*, never on the
pullback itself (that is trying to catch a knife).

---

## Stops (structural + volatility, never arbitrary %)

Do not use a flat "2% stop." Anchor the stop to structure and volatility:

Inputs: recent swing low/high, VWAP, ATR, support/resistance, opening-range
structure.

**Worked example (long):**
- Entry = ₹1,000
- VWAP = ₹995
- Swing low = ₹991
- ATR = ₹7

A structurally sensible stop sits just below the cluster of support — around
**₹990–992** — so it is beyond the swing low and VWAP but still tight enough to
keep R:R attractive. Place it where the *setup is proven wrong*, not at a round
number.

---

## Targets

- **Minimum** R:R = **1:2**
- **Preferred** R:R = **1:2.5 to 1:3**

Example at ₹5,000 risk: 2R target = +₹10,000, 3R target = +₹15,000.

Management: consider booking part at 2R and trailing the rest (e.g. below rising
swing lows or a moving VWAP/EMA) toward 3R. Evaluate the strategy on
**expectancy net of costs**, not win rate.

---

## Direction alignment is mandatory

Never take a long in a bearish regime or a short in a bullish regime just
because the single-stock structure looks good. The regime factor and the
`trade_gate()` regime check exist to enforce this. A great setup fighting the
tape is a below-average trade.

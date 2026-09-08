# Risk, Position Sizing, Daily Controls & Cost Reality

**Risk management matters more than entry optimisation.** A good system with
poor risk control still blows up; a mediocre system with strict risk control
survives to compound. Everything here is enforced by
`scripts/intraday_calculator.py`.

---

## Risk per trade

- Target: **0.35–0.50% of trading capital** per trade.
- Sizing keeps the rupee loss constant regardless of stock price:

```
risk_amount   = capital × risk_pct
shares        = floor(risk_amount / stop_distance)
```

**Example:** capital ₹10,00,000, risk 0.5% = ₹5,000. Stop distance ₹10 →
₹5,000 / ₹10 = **500 shares**.

> **Concentration note (not in the source plan).** 500 shares × ₹1,000 =
> ₹5,00,000 notional = 50% of capital in one name. That is fine *intraday* with
> MIS leverage, but if a position is ever carried or a stop gaps, the loss can
> exceed the planned ₹5,000. Use the optional `max_position_pct` cap in
> `position_size()` (e.g. 30–40%) to bound single-name notional, and remember
> SEBI's peak-margin rules cap intraday leverage (~5×). Never assume the stop
> fills exactly at your price.

---

## Stop-loss model

Structural + volatility-based only (see `setups.md`). Inputs: swing high/low,
VWAP, ATR, support/resistance, opening-range structure. Place the stop where
the setup is invalidated — not at an arbitrary percentage.

---

## Daily risk controls

- **Max daily loss: 1.5% of capital.** Three −0.5% trades = −1.5% → **stop for
  the day.** No discretionary recovery trade.
- **Max trades/day: 3–5.** Also legitimate: **0 trades.**
- Check with `daily_risk_check()` after every closed trade.

These two limits interlock: at 0.5% risk, the −1.5% cap *is* three losing
trades, which sits inside the 3–5 trade band. If you widen risk per trade, the
loss cap bites sooner — that is intended.

---

## Trading hours

- Primary: **09:30–11:30 IST**
- Secondary: **13:30–15:00 IST**
- Avoid the low-quality midday drift.
- **Square off before close** (target 15:15–15:20 IST) unless explicitly
  redesigned for overnight risk.

---

## Transaction cost reality — the part that decides viability

**A strategy profitable before costs but unprofitable after costs is not a
strategy.** Every evaluation must subtract *all* of:

- Brokerage (discount brokers ~₹20/order or 0.03%, whichever lower — both legs)
- STT (intraday equity: **0.025% on the sell side**)
- Exchange transaction charges (~0.00297% NSE, both legs)
- GST (18% on brokerage + transaction charges)
- SEBI turnover fees (~0.0001%)
- Stamp duty (~0.003% on the buy side)
- Bid/ask spread paid
- Slippage vs intended price

This is computed exactly in `scripts/cost_model.py` (edit its rates for your
broker). A ₹1,000 stock, 500 shares (₹5,00,000 position): round-trip cost is
**≈ ₹523 = 0.105% of position value** (brokerage ₹40 + STT ₹125 + exchange
₹30 + stamp ₹15 + GST ₹13 + ~₹300 slippage). That drag turns into an
R-multiple via `cost_in_R = cost_frac / stop_frac` — so **the tighter your
stop, the more of your edge costs eat.**

The plan's illustrative gross edge is +0.125R (win 50%, avg win 1.25R, avg loss
1R). Net of the cost drag above:

| Stop width | Cost in R | Illustration (50%/1.25R/1R) net | Section-12 target (45%/2R/1R) net |
|---|---:|---:|---:|
| **0.5% of price** (typical intraday) | 0.21R | **−0.08R → loses** | **+0.14R → survives** |
| 1.0% of price (wide) | 0.10R | +0.02R → marginal (inside the noise) | +0.25R → survives |

**Conclusion:** the 1.25R-winner illustration is **not a target** — at a
realistic intraday stop it is a net loser, and even with a wide stop its edge
is within the cost estimate's error bars. It teaches *how expectancy works*,
nothing more. The viable profile is the Section-12 bar:

| Metric | Minimum viable |
|---|---:|
| Avg winner / avg loser | > 2 |
| Profit factor | > 1.4 |
| Win rate | 40–55% (expectancy, not win rate, is the test) |
| Expectancy (net of costs, out-of-sample) | Positive |
| Max drawdown | < 10–12% |
| Sharpe | > 1.5 |

At avg winner/loser > 2 and ~45% win rate the net edge is comfortably positive
at both stop widths (+0.14R to +0.25R) — which is why *that* is the bar and the
0.125R figure is not. Reproduce all of this with `python3 cost_model.py`.

---

## Return expectation — a hypothesis, not a promise

The plan targets **20–30% annual** (not monthly) with drawdown < 10–12%. This
is achievable only under the fat-edge assumption above and disciplined cadence;
it is **optimistic** and must be *rejected* if out-of-sample testing does not
support it after costs and slippage. Never present it to a user as expected or
guaranteed.

---

## Validation is not optional

Do not go live because a backtest made money. Start with the **smoke test**:
`scripts/backtest_intraday.py` runs the ORB setup cost-aware and look-ahead-safe
on ~60 days of free 5-minute data (or a CSV) and reports net metrics — enough to
sanity-check that the mechanics and costs behave, not enough to prove an edge.
For real validation route through the `backtest-expert` skill: in-sample →
validation → walk-forward → out-of-sample, with realistic costs, no look-ahead
bias, and a survivorship-bias-free universe (feed the harness archived
multi-year 5-minute data via `--source csv`). Then paper trade 8–12 weeks
(logging *every* signal, traded or not) before committing a fraction of
intended capital.

**GO only if:** positive out-of-sample expectancy after costs; profit factor
> 1.4; drawdown acceptable; no single stock/month explains most profits;
performance survives parameter perturbation and multiple regimes; paper trading
confirms execution assumptions.

**NO-GO if:** most profit comes from a few trades; small parameter changes
destroy it; results vanish after costs; out-of-sample collapses; drawdown
unacceptable; it needs excessive trade frequency; or it works in only one
regime.

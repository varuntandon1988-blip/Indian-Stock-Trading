---
name: nse-intraday-system
description: Systematic intraday (5-minute) cash-equity trading system for NSE India. Use when the user wants intraday trade candidates, an Opening Range Breakout or VWAP-continuation setup, a five-factor intraday score, position sizing, daily risk controls, or a same-day squared-off short-term trade plan on liquid NSE stocks. Cash equities only — not options, not swing/positional.
---

# NSE Intraday System (Cash Equity, 5-Minute)

A rules-first intraday system for liquid NSE cash equities. It scores each
candidate on five independent factors, trades only two well-defined setups
(Opening Range Breakout and VWAP continuation), and sizes every trade to a
constant, small risk. **Its most important output is often "NO TRADE."**

> **Reality check — read before using.** SEBI's 2024 study found ~7 of 10
> individual intraday equity traders lost money. This system is a *research
> framework*, not a money machine. The return numbers in this skill are
> hypotheses to be **rejected** unless they survive out-of-sample backtesting
> with realistic costs. Treat every trade as risk of loss first.

## When to use this vs. the other skills

| You want… | Use |
|---|---|
| Intraday (same-day) cash-equity trade, 5-min timeframe | **this skill** |
| Swing / Stage-2 breakout over days-weeks | `nse-vcp-screener` |
| Weekly chart / trend read from a chart image | `technical-analyst` |
| Options / F&O directional trade | `weekly-fno-trade-planner`, `options-strategy-advisor` |
| Validate whether any of this actually has an edge | `backtest-expert` |
| Institutional flow / breadth / news context | `fii-dii-flow-tracker`, `india-market-breadth`, `india-news-tracker` |

This skill deliberately does **not** re-implement backtesting — for any
"does this work?" question, hand off to `backtest-expert`.

## Non-negotiable guardrails

1. **Cash equities only.** Do not start with options. Options add IV, theta,
   gamma, skew and expiry effects — a separate problem with different
   statistics. Prove the cash edge first.
2. **Square off intraday.** All positions close before 15:15–15:20 IST unless
   the user has explicitly redesigned this for overnight exposure.
3. **Constant small risk.** 0.35–0.50% of capital per trade. Never scale risk
   up after a winning streak.
4. **Hard daily stop.** Stop trading for the day at −1.5% of capital or after
   the max trade count, whichever comes first. No discretionary "recovery
   trade."
5. **Zero trades is a valid, good day.** Do not force trades in ambiguous
   conditions or the low-quality midday window.

## Trading universe

Screen from ~50–100 highly liquid NSE names:

- Large/mid cap, price > ₹100, tight bid/ask spread
- Average daily turnover preferably > ₹100–200 Cr
- Exclude SME, illiquid, and penny stocks
- F&O availability preferred (for OI/flow signal), not mandatory

The edge must survive execution costs and slippage — illiquid names destroy
that survival, so liquidity is a hard filter, not a preference.

## Workflow

```
1. Classify market regime (bull / neutral / bear)  → references/scoring_model.md §A
2. Scan the liquid universe
3. Score each candidate 0–100 across the five factors
4. Keep only score > 75 AND setup present (ORB or VWAP)  → references/setups.md
5. Define entry, structural/ATR stop, and 2R/3R targets  → references/setups.md
6. Gate on R:R ≥ 1:2 and regime alignment
7. Size to constant risk                              → scripts/intraday_calculator.py
8. Enter, manage, trail, exit; respect daily controls → references/risk_and_costs.md
9. Log every signal — including the ones you did NOT trade
```

### Data access

Use whichever broker MCP is connected (same tools as `india-stock-analysis`):

- **Groww MCP**: `fetch_historical_candle_data` (5-min OHLCV),
  `get_historical_technical_indicators` (VWAP, EMA, ATR, ADX),
  `get_ltp` (price + OI), `get_quotes_and_depth` (spread/depth),
  `fetch_market_movers_and_trending_stocks_funds` (relative volume / movers),
  `resolve_market_time_and_calendar`.
- **Zerodha Kite MCP**: `get_historical_data`, `get_ohlc`, `get_quotes`,
  `get_ltp`, `get_positions`, `get_margins`.
- Compute VWAP/ATR/EMA/relative-volume/relative-strength yourself from candles
  if the MCP does not expose them directly.

> **Data caveat for backtesting.** Free sources (yfinance) provide only ~60
> days of 5-minute history and are survivorship-biased to today's universe.
> That is enough for a **cost-aware smoke test** (see Backtesting below), but
> **not** for validation. Honest multi-year (2021–2026) intraday validation
> needs a **paid/archived 5-minute dataset with a point-in-time universe** —
> the backtest harness reads a CSV so you can feed it exactly that when you
> have it. Never present a 60-day, single-setup result as conclusive.

## The five-factor score (0–100)

| Factor | Points | Question it answers |
|---|---:|---|
| A. Market regime | 20 | Is the broad market supporting this direction? |
| B. Relative strength | 25 | Is the stock beating NIFTY *and* its sector? |
| C. Volume / flow | 20 | Is unusual volume confirming the move? |
| D. Price structure | 20 | Is there a clean, tradeable setup? |
| E. Catalyst | 15 | Is there a real reason for price discovery? |

Weights sum to 100. A stock can reach 85 with **no** catalyst, so the 75 gate
is passable on structure + strength + volume + regime alone — catalyst is a
bonus, not a requirement. Full definitions and worked examples:
`references/scoring_model.md`.

## The two setups

- **Strategy A — Opening Range Breakout (ORB):** define the 09:15–09:30 high/low;
  trade a break of it with VWAP, regime, relative strength, and ~1.5× volume
  confirmation.
- **Strategy B — VWAP continuation:** in an aligned trend, buy the pullback to
  VWAP that contracts on volume and resumes with a volume expansion.

Exact entry/stop/target rules, and short-side mirrors, in
`references/setups.md`.

## Trading hours

- **Primary window:** 09:30–11:30 IST (best signal quality)
- **Secondary window:** 13:30–15:00 IST
- **Avoid:** the low-quality midday drift
- **Square off:** before close

## Position sizing, targets, daily controls

Use `scripts/intraday_calculator.py` for deterministic sizing and gating:

```bash
python3 scripts/intraday_calculator.py   # runs self-tests + a demo
```

It provides `composite_score`, `reward_risk`, `trade_gate` (score + R:R +
regime gates), `position_size` (constant-risk, with an optional notional cap),
and `daily_risk_check` (−1.5% daily stop + max-trade count). Rules, the
stop-loss model, and the **cost math that decides viability** are in
`references/risk_and_costs.md`.

## Honest expectancy note (do not skip) — now enforced in code

The plan's illustrative expectancy — win 50%, avg winner 1.25R, avg loser 1R →
**+0.125R gross per trade** — is thin enough that transaction costs decide
whether it lives or dies. `scripts/cost_model.py` computes this exactly:

| Stop width | Cost drag | Illustration (50%/1.25R/1R) net | Section-12 target (45%/2R/1R) net |
|---|---:|---:|---:|
| 0.5% of price (typical intraday) | 0.21R | **−0.08R — loses** | **+0.14R — survives** |
| 1.0% of price (wide) | 0.10R | +0.02R — marginal, inside the noise | +0.25R — survives |

So the illustration is **not a target** — at a realistic intraday stop it loses
money, and even with a wide stop the edge is inside the cost estimate's error
bars. The viable bar is the Section-12 profile: **avg winner/loser > 2, profit
factor > 1.4, expectancy positive out-of-sample after costs.**

This is enforced, not just documented:
- `cost_model.net_expectancy(...)` gives gross vs net expectancy in R.
- `intraday_calculator.net_trade_check(...)` re-checks R:R **after costs** (a
  gross 1:2 can fail the net gate).
- The backtest charges every simulated trade real costs and reports NET.

Always evaluate on expectancy net of costs — never on win rate, never pre-cost.

## Backtesting (what's available today)

`scripts/backtest_intraday.py` runs a real, cost-aware, look-ahead-safe
backtest of **both setups** on 5-minute data:

```bash
python3 scripts/backtest_intraday.py                       # offline self-test
python3 scripts/backtest_intraday.py --tickers RELIANCE,TCS,INFY --period 60d
python3 scripts/backtest_intraday.py --setup orb  --tickers RELIANCE   # ORB only
python3 scripts/backtest_intraday.py --setup vwap --tickers RELIANCE   # VWAP only
python3 scripts/backtest_intraday.py --source csv --csv-dir ./data --tickers RELIANCE
```

- `--setup orb | vwap | both` (default `both` — one trade/stock/day, earliest
  trigger wins).
- **ORB:** breakout of the 09:15–09:30 range; structural stop = opening-range low.
- **VWAP continuation:** impulse above VWAP → pullback toward VWAP on contracting
  volume (holding above it) → resumption on a volume expansion; structural stop =
  pullback low. Losing VWAP invalidates the setup.
- Decision on a bar's **close**, fill on the **next bar's open** (no look-ahead).
- 2R target, square-off at session end; every trade charged real costs +
  slippage; results reported **gross and net**, broken down by setup.
- Reads yfinance (~60 days free) **or** a CSV, so archived multi-year 5-minute
  data drops straight in when you have it.

**This is a smoke test, not validation.** Two months of one setup on today's
tickers cannot prove an edge. For walk-forward, out-of-sample, robustness and
survivorship handling, drive validation through the `backtest-expert` skill.

## Output: daily signal dashboard

Produce a ranked table of candidates (only score > 75 and R:R ≥ 1:2 are
tradeable). Template: `assets/daily_signal_dashboard_template.md`.

## Reference files

- `references/scoring_model.md` — the five factors in full, with examples
- `references/setups.md` — ORB and VWAP-continuation entry/stop/target rules
- `references/risk_and_costs.md` — risk model, stops, daily controls, cost reality
- `scripts/cost_model.py` — Indian intraday cost model + net expectancy
- `scripts/intraday_calculator.py` — scoring gate, sizing, net R:R, daily-risk
- `scripts/backtest_intraday.py` — cost-aware ORB backtest (yfinance or CSV)
- `references/example_backtest_run.md` — a real-data smoke-test run + lessons
- `assets/daily_signal_dashboard_template.md` — output template

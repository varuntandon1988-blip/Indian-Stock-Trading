# Example Real-Data Run (illustrative smoke test — not validation)

A worked run of `scripts/backtest_intraday.py` on **real** NSE minute data, to
show the harness end-to-end and what an honest result looks like. This is one
dataset and one naive configuration — a smoke test, **not** proof of anything
about the strategy. Do not quote these numbers as the system's performance.

## Data

- Source: public GitHub dataset `ShabbirHasan1/NSE-Data` (NSE minute OHLCV,
  2017–2020), fetched as raw CSV.
- Instruments: INFY, HDFCBANK, ICICIBANK (liquid large-caps) + NIFTY 50 index
  for the regime / relative-strength gate.
- Resampled 1-minute → **5-minute** bars (09:15 aligns to a 5-min boundary, so
  plain 5-min bins match the session open), then fed via `--source csv`.

## Configuration

Defaults: ₹10,00,000 capital, 0.5% risk/trade, `--setup both`, 2R target,
opening-range / pullback structural stops, square-off at session end, full
Indian intraday cost model + slippage.

## Result (453 trades across 3 names, 2017–2020, NET of costs)

| Metric | Value |
|---|---:|
| Trades | 453 |
| Win rate | 40.2% |
| Gross P&L | **−₹32,743** (roughly flat) |
| Fees | **−₹1,70,483** |
| Net P&L | **−₹2,03,225 (−20.3%)** |
| Profit factor | 0.72 |
| Expectancy (net) | **−0.129R / trade** |

Exit-reason breakdown (the important part):

| Exit reason | Count | Share | Avg net R |
|---|---:|---:|---:|
| Square-off (EOD) | 309 | 68% | +0.05 |
| Stop | 114 | 25% | −1.13 |
| **Target (2R)** | **30** | **7%** | +1.85 |

## What this run actually teaches

1. **Costs are the difference between "meh" and "losing" — point 1, live.**
   Gross P&L is roughly flat (−₹33k over 453 trades ≈ no edge). The ₹1.7 lakh
   of fees is what turns it into −20%. This is exactly why the skill evaluates
   everything net of costs.
2. **The 2R target rarely fills intraday (7%).** Two-thirds of trades are
   squared off at the close near breakeven (+0.05R). A fixed 1:2 R:R that the
   market reaches 7% of the time cannot pay for the 25% of trades that hit the
   stop. The R:R *assumption* did not survive contact with data.
3. **The go/no-go rules would correctly REJECT this.** Negative out-of-sample
   expectancy after costs, profit factor 0.72 (< 1.4): NO-GO. The system's job
   here is to say *no*, and it does.
4. **`both` mode is dominated by ORB (452 vs 1 VWAP)** because ORB almost always
   triggers earlier in the day and the "earliest trigger wins" rule suppresses
   VWAP. Ranking candidates by the five-factor score instead of by time would
   change the mix — a real design lever, not a bug.

## Honest limitations of this run

- **Single stock at a time, tiny universe.** The plan calls for 50–100 names;
  three is high variance and says little about the system.
- **No portfolio-level risk in the backtest.** Each trade is sized against full
  capital independently; the live daily controls (3–5 trades/day, −1.5% daily
  stop) and shared-capital sizing are **not** enforced here, so `net_return_pct`
  and `max_drawdown_pct` are optimistic/leverage-naive. The sizing-independent
  metric — **expectancy in R** — is the one to trust.
- **Survivorship / point-in-time.** Today's liquid names, back-projected.
- **One setup variant, fixed parameters.** No walk-forward, no robustness sweep.

## The takeaway

The harness works and tells the truth: a naive intraday ORB/VWAP system on
large-caps is gross-flat and **loses after costs** — the default outcome SEBI's
loss statistics would predict. Turning this into something with a real edge is
the job of the `backtest-expert` workflow (walk-forward, out-of-sample,
robustness, a proper universe), not of a bigger backtest of the same idea.

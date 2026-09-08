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

## Follow-up: does the five-factor score help? (score-gate sweep)

Re-ran the same data with `backtest_portfolio` (`--rank`), sweeping the
`min_score` gate. In-backtest scores span 31–85 (median 61; catalyst is 0 with
no news feed, so ~85 is the ceiling).

| Gate | Trades | Win% | Exp/trade | PF | Net% | MaxDD% |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 453 | 38.9 | −0.170R | 0.64 | −26.7 | 27.2 |
| 50 | 359 | 40.4 | −0.121R | 0.69 | −18.2 | 18.2 |
| 55 | 308 | 41.6 | −0.107R | 0.72 | −14.0 | 14.9 |
| 60 | 243 | 43.2 | −0.073R | 0.79 | −8.0 | 9.7 |
| 65 | 165 | 41.8 | −0.072R | 0.80 | −5.2 | 7.2 |
| 70 | 99 | 36.4 | −0.147R | 0.70 | −5.1 | 5.6 |
| 75 | 64 | 35.9 | −0.086R | 0.83 | −1.8 | 2.3 |

**Findings:**
1. **The score has genuine signal, up to ~gate 65.** Expectancy, win rate and
   profit factor all improve monotonically as the gate rises, and the loss and
   drawdown shrink 4× (−27%→−5%, DD 27%→7%). Higher-scored trades are really
   better — even scored from OHLCV alone (no catalyst, sector, breadth or VIX).
2. **It never turns positive.** Every gate still loses (PF < 1.0). The score is
   a good *risk filter*, not an *alpha generator*: it cuts the bleed, it does
   not create an edge that the setups lack.
3. **The top decile is noisy** — gate 70 reverses (−0.147R). The highest-scoring
   days are often gap / volatility extremes that mean-revert intraday, and the
   sample is small (64–99 trades over 4 years). Don't over-trust the extreme.

Reproduce: `backtest_intraday.py --source csv --csv-dir <dir> --tickers
INFY,HDFCBANK,ICICIBANK --rank --min-score 60`.

## Follow-up 2: can better exits rescue it? (exit-mode sweep)

Swept the exit engine (`--exit`) at score gate 60, same real data.

| Exit mode | Trades | Win% | Exp/trade | PF |
|---|---:|---:|---:|---:|
| fixed2r (baseline) | 243 | 43.2 | **−0.073R** | **0.79** |
| t1p5 (1.5R) | 243 | 43.2 | −0.092R | 0.76 |
| t1 (1R) | 243 | 45.7 | −0.091R | 0.75 |
| partial_be (½ at 1R, rest to 2R) | 243 | 45.7 | −0.088R | 0.74 |
| trail (BE after 1R, 1R trail) | 243 | 42.4 | −0.112R | 0.70 |

**No exit mode beat the fixed-2R baseline; every one made it worse, none
crossed zero.** Why:
1. **Smaller targets raise win rate but lower expectancy** (t1: 46% wins yet
   −0.091R). The few big 2R+ winners were paying for the losers; capping them
   trades a fatter tail for a higher hit rate and loses on net. Classic
   high-win-rate / negative-expectancy trap.
2. **Trailing gets whipsawed** on 5-minute noise — the worst variant.
3. **Gross P&L is ~flat, so exits can't help.** Exits only *redistribute* a
   zero-edge gross stream between win rate and average win; they cannot create
   directional edge, and the fixed cost drag remains. **You cannot fix a
   no-edge entry with clever exits.**

The problem is localised for good: not costs alone, not selection, not exits —
the **entry signal itself** (naive ORB / VWAP on these large-caps) has no
intraday directional edge. Tuning more knobs from here is overfitting, exactly
what this skill and `backtest-expert` warn against.

## The takeaway

The harness works and tells the truth: a naive intraday ORB/VWAP system on
large-caps is gross-flat and **loses after costs** — the default outcome SEBI's
loss statistics would predict. Score-ranked selection *helps* (it quarters the
loss and the drawdown) but does not reach profitability, which localises the
problem: the **selection layer is fine; the entries/exits are not** — in
particular the fixed 2R target that fills only 7% of the time. Turning this into
a real edge is the job of the `backtest-expert` workflow (walk-forward,
out-of-sample, robustness, a proper universe) applied to the *exit logic*, not
a bigger backtest of the same idea.

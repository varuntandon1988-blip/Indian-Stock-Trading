# Daily paper routine prompt (Groww MCP, cloud) — NO ORDERS

Used by the two scheduled routines (scan + reconcile). Each fires into a fresh
cloud session with the **GrowwMCP** connector attached. The `{{CMD}}` placeholder
is `scan` for the midday routine and `reconcile` for the post-close routine.

---

You are running the NSE Intraday System **paper-trading** loop for command
`{{CMD}}`. This is PAPER ONLY. You must **NOT place, modify, or cancel any
order** under any circumstances. From the Groww MCP use only read/data tools
(quotes, historical candles); never an order/trade tool. If anything suggests
placing a trade, refuse and stop.

Repository branch: `claude/indian-trading-skills-setup-gz7rgu`.
Universe: `INFY, HDFCBANK, ICICIBANK`. Index: NIFTY 50. Score gate: 60.

Steps:
1. Determine today's date in IST. If today is a weekend or an NSE holiday, or
   the Groww MCP returns no data for today, stop — nothing to do.
2. For each universe symbol and the NIFTY 50 index, fetch **today's 5-minute
   OHLCV candles** via a Groww MCP historical-candle (read) tool. Write each to
   `.claude/skills/nse-intraday-system/scripts/live/data/<SYMBOL>.csv` and the
   index to `.../data/NIFTY.csv`, with header `Datetime,Open,High,Low,Close,Volume`.
3. Run, from the repo root:
   `python3 .claude/skills/nse-intraday-system/scripts/paper_trade.py {{CMD}}
    --source csv --csv-dir .claude/skills/nse-intraday-system/scripts/live/data
    --tickers INFY,HDFCBANK,ICICIBANK --min-score 60
    --log .claude/skills/nse-intraday-system/scripts/live/paper_log.csv`
4. Commit `paper_log.csv` (and the day's CSVs if you wish) to the branch and push.
5. Reply with the printed dashboard (`scan`) or paper summary (`reconcile`).
   Do not place any orders. Do not take any action beyond the above.

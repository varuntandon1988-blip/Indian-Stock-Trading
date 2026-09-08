# Automating the paper loop (daily, hands-off) — NO ORDERS

This runs the paper-trading loop every trading day and lets `paper_log.csv`
accumulate for the 8–12 week Phase-4 validation. **No orders are ever placed.**

Two ways to automate. **Option A** (Groww MCP + a Claude cloud routine) is
fully hands-off — no machine of your own to keep running. **Option B** (local
cron + Groww API) is the fallback if you'd rather run it yourself.

---

## Option A — Groww MCP + a daily Claude routine (recommended)

Groww exposes an MCP server at **https://mcp.groww.in/mcp**. Once it's connected
to your Claude account, a scheduled routine can fetch candles through it — in
the cloud, no local machine needed.

### One-time: connect the Groww MCP
1. In claude.ai → **Settings → Connectors → Add custom connector**.
2. Name **GrowwMCP**, URL **https://mcp.groww.in/mcp**; authenticate to Groww.
3. **Enable it in the chat** where you ask Claude to arm the routine (a
   connector authenticated but toggled off for the chat won't attach).

### Then ask Claude (in a session where GrowwMCP is enabled) to create the routine
Claude will create two daily routines (fresh cloud session per fire, Mon–Fri,
GrowwMCP attached) using the prompt in `routine_prompt.md`:
- a **scan** routine ~11:15 IST (05:45 UTC),
- a **reconcile** routine ~16:00 IST (10:30 UTC),
each of which fetches candles via GrowwMCP, writes CSVs, runs `paper_trade.py`,
and commits `paper_log.csv` to the branch.

> **Order safety.** The Groww MCP also exposes *order* tools. The routine prompt
> forbids them explicitly — it may call only quote/historical-candle (read)
> tools. Paper only, always.

---

## Option B — local cron + Groww API (fallback)

Runs on your own machine, where your Groww API access and unrestricted network
live. Use this if you don't want to rely on the cloud routine.

## Prerequisites

1. **Groww Trading API access** and an access token. (If you don't have API
   access, alternatives: Zerodha Kite Connect, or export 5-min CSVs manually
   into the `data/` dir — the pipeline only needs the CSVs.)
2. Python deps: `pip install pandas numpy growwapi`
3. A clone of this repo on the machine that will run cron.

## One-time setup

```bash
cd .../scripts/live
export GROWW_ACCESS_TOKEN=your_token      # keep it out of git; use an env file
chmod +x run_paper.sh
# Edit TICKERS in run_paper.sh (or export it) to your liquid universe.
# VERIFY the two lines marked `# VERIFY` in broker_adapter.py against your
# installed growwapi version, then do a dry run:
./run_paper.sh scan
```

A successful `scan` prints today's dashboard and appends signals to
`paper_log.csv`. `./run_paper.sh reconcile` (after 15:30 IST) fills outcomes
and prints the running paper summary (win rate, net expectancy, profit factor).

## Schedule it

Paste `crontab.example` into `crontab -e` (edit the path and token handling).
It scans a few times during the session and reconciles after the close, Mon–Fri.

## Alternative: a device-bound Claude routine

If you run **Claude Code on this same machine**, you can instead create a
routine there that runs on this computer (a device-bound routine) and calls
`./run_paper.sh`. That keeps the scheduling inside Claude while still executing
where your Groww access and unrestricted network live. Set it up from your
local Claude Code session, not from a cloud session.

## Guarantees & reminders

- **No orders.** `broker_adapter.py` calls only historical/candle endpoints;
  `paper_trade.py` has no order path. Nothing here can trade real money.
- **Secrets.** Never commit `GROWW_ACCESS_TOKEN`. Use an env file sourced by
  cron, or your OS keychain.
- **Go/No-Go.** Only a positive, cost-adjusted paper expectancy sustained over
  8–12 weeks reopens the live-capital question. Current backtests are a NO-GO.

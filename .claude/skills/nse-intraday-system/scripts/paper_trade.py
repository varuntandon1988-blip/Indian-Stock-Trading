#!/usr/bin/env python3
"""
Paper-trading signal generator for the NSE Intraday System — NO ORDERS.

This is the *safe* meaning of "go live": generate the live daily candidate
dashboard and log every signal and its outcome, without placing a single order.
It is Phase 4 of the plan ("run the system live without capital, 8-12 weeks,
record every signal — including signals that were not traded"). Use it to build
real forward out-of-sample evidence before any real-money decision.

It reuses the exact detection, five-factor scoring and exit logic from
`backtest_intraday.py`, so paper results are directly comparable to backtests.

Two commands:
  scan       — score today's candidates as of now, print the dashboard, and
               append new signals to the log (dedup per date+ticker+setup).
  reconcile  — after the close, replay each logged signal through the exit
               engine and fill in its outcome (exit reason, net R, net P&L).

Data sources (python can reach these directly): a 5-minute CSV per symbol
(`--source csv`, the natural handoff from a broker MCP — Claude writes the
candles it pulls to CSV) or yfinance (`--source yfinance`). A broker MCP itself
is called by Claude, not by this script; see the skill's "Going live (paper)"
section for that agent-driven flow.

    python3 paper_trade.py scan --source csv --csv-dir ./data \
        --tickers INFY,HDFCBANK,ICICIBANK --min-score 60 --log paper_log.csv
    python3 paper_trade.py reconcile --source csv --csv-dir ./data \
        --tickers INFY,HDFCBANK,ICICIBANK --log paper_log.csv

Run with no command to execute the offline self-test.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, time as dtime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_intraday as bt          # noqa: E402
from cost_model import IntradayCostModel  # noqa: E402
from intraday_calculator import position_size  # noqa: E402

# The system is long-only for now (short setups are the documented mirror but
# not yet implemented in the engine). NO ORDERS are ever placed here.
FIELDS = ["date", "scan_time", "ticker", "setup", "score", "gate_pass",
          "direction", "entry", "stop", "t1", "t2", "qty", "rr", "net_rr",
          "status", "exit_price", "exit_reason", "net_R", "net_pnl"]


# --------------------------------------------------------------------------
# Scan (build today's ranked dashboard; no look-ahead past `as_of`)
# --------------------------------------------------------------------------

def scan(stock_dfs: dict, nifty_df, p: bt.Params, cost=None, as_of: dtime | None = None):
    """Return (date, ranked_rows) for the latest session in the data.

    `as_of` (a datetime.time) restricts detection to bars at or before that
    time, so a mid-session scan never uses future bars. Rows are dicts ready
    for the log; they carry a would-be-taken flag (`gate_pass`) but NO outcome.
    """
    cost = cost or IntradayCostModel()
    n = bt.prepare(nifty_df)
    prepared = {tk: bt.prepare(df) for tk, df in stock_dfs.items()}
    date = max(d for s in prepared.values() for d in set(s["date"]))

    ndf = n[n["date"] == date]
    if as_of is not None:
        ndf = ndf[ndf["t"] <= as_of]
    if ndf.empty:
        return date, []

    rows = []
    for tk, s in prepared.items():
        sdf = s[s["date"] == date]
        if as_of is not None:
            sdf = sdf[sdf["t"] <= as_of]
        c = bt._day_candidate(sdf, ndf, p, cost)
        if c is None:
            continue
        risk = c.entry - c.stop
        qty = position_size(p.capital, c.entry, c.stop, p.risk_pct,
                            p.max_position_pct)["shares"]
        target_R = p.target_R or 2.0
        net = cost.net_reward_risk(c.entry, c.stop, c.entry + target_R * risk,
                                   "long", max(qty, 1))
        rows.append({
            "date": str(date), "ticker": tk, "setup": c.setup,
            "score": round(c.score, 1), "gate_pass": c.score >= p.min_score,
            "direction": "long", "entry": round(c.entry, 2),
            "stop": round(c.stop, 2), "t1": round(c.entry + risk, 2),
            "t2": round(c.entry + target_R * risk, 2), "qty": qty,
            "rr": net["gross_rr"], "net_rr": net["net_rr"],
            "status": "signal" if c.score >= p.min_score else "watch",
        })
    rows.sort(key=lambda r: (-r["score"], r["ticker"]))
    return date, rows


def format_dashboard(date, rows, p: bt.Params):
    out = [f"# Paper Signal Dashboard — {date}   (PAPER ONLY — NO ORDERS)",
           f"min_score gate: {p.min_score} | risk/trade: {p.risk_pct*100:.2f}% "
           f"| max {p.max_trades_day} trades/day | target {p.target_R}R"]
    if not rows:
        out.append("\n_No candidates — a NO-TRADE day is a valid outcome._")
        return "\n".join(out)
    out.append("\n| Rank | Stock | Score | Setup | Dir | Entry | SL | T1 | T2 "
               "| Qty | R:R (net) | Take? |")
    out.append("|---:|---|---:|---|---|---:|---:|---:|---:|---:|---:|:--:|")
    takeable = [r for r in rows if r["gate_pass"]]
    for i, r in enumerate(rows, 1):
        take = "✅" if r["gate_pass"] and rows.index(r) < p.max_trades_day else "—"
        out.append(f"| {i} | {r['ticker']} | {r['score']} | {r['setup']} | "
                   f"{r['direction']} | {r['entry']} | {r['stop']} | {r['t1']} "
                   f"| {r['t2']} | {r['qty']} | {r['rr']}/{r['net_rr']} | {take} |")
    out.append(f"\n{len(takeable)} of {len(rows)} candidates pass the score gate; "
               f"paper-take the top {min(len(takeable), p.max_trades_day)}. "
               "Everything else is logged but not taken.")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Log (append-only; dedup per date+ticker+setup)
# --------------------------------------------------------------------------

def _read_log(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _write_log(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def log_signals(rows, log_path, scan_time):
    """Append candidates not already logged for their (date,ticker,setup).
    Logs EVERY detected candidate — including sub-gate 'watch' rows — so the
    paper record captures signals that were not taken."""
    existing = _read_log(log_path)
    seen = {(r["date"], r["ticker"], r["setup"]) for r in existing}
    added = 0
    for r in rows:
        key = (r["date"], r["ticker"], r["setup"])
        if key in seen:
            continue
        entry = dict(r)
        entry["scan_time"] = scan_time
        entry.setdefault("exit_price", "")
        entry.setdefault("exit_reason", "")
        entry.setdefault("net_R", "")
        entry.setdefault("net_pnl", "")
        existing.append(entry)
        seen.add(key)
        added += 1
    _write_log(log_path, existing)
    return added


# --------------------------------------------------------------------------
# Reconcile (fill outcomes after the close via the exit engine)
# --------------------------------------------------------------------------

def reconcile(stock_dfs, nifty_df, p: bt.Params, log_path, cost=None):
    """For logged rows lacking an outcome, replay the day through the exit
    engine and record exit reason, net R and net P&L. Deterministic: it
    re-detects the same candidate and simulates it on the full day's bars."""
    cost = cost or IntradayCostModel()
    log = _read_log(log_path)
    if not log:
        return 0
    n = bt.prepare(nifty_df)
    prepared = {tk: bt.prepare(df) for tk, df in stock_dfs.items()}
    updated = 0
    for row in log:
        if row.get("net_R") not in ("", None):
            continue
        tk = row["ticker"]
        if tk not in prepared:
            continue
        s = prepared[tk]
        date = row["date"]
        sdf = s[s["date"].astype(str) == date]
        ndf = n[n["date"].astype(str) == date]
        if sdf.empty or ndf.empty:
            continue
        c = bt._day_candidate(sdf, ndf, p, cost)
        if c is None or c.setup != row["setup"]:
            continue
        trade = bt.simulate_candidate(sdf, c, p, cost)
        if not trade:
            continue
        row["exit_price"] = trade["exit"]
        row["exit_reason"] = trade["reason"]
        row["net_R"] = trade["net_R"]
        row["net_pnl"] = trade["net_pnl"]
        updated += 1
    _write_log(log_path, log)
    return updated


def paper_summary(log_path):
    """Aggregate the paper log into the metrics that decide go/no-go."""
    log = [r for r in _read_log(log_path) if r.get("net_R") not in ("", None)]
    taken = [r for r in log if str(r.get("gate_pass")).lower() == "true"]
    if not taken:
        return {"reconciled_taken_signals": 0,
                "note": "no reconciled, gate-passing signals yet"}
    rs = [float(r["net_R"]) for r in taken]
    pnl = [float(r["net_pnl"]) for r in taken]
    wins = [x for x in pnl if x > 0]
    gp = sum(x for x in pnl if x > 0)
    gl = -sum(x for x in pnl if x <= 0)
    return {
        "reconciled_taken_signals": len(taken),
        "win_rate": round(100 * len(wins) / len(taken), 1),
        "expectancy_net_R": round(sum(rs) / len(rs), 3),
        "net_pnl": round(sum(pnl), 2),
        "profit_factor": round(gp / gl, 2) if gl else float("inf"),
    }


# --------------------------------------------------------------------------
# Data loading (delegates to backtest_intraday's loaders)
# --------------------------------------------------------------------------

def _load(args):
    if args.source == "yfinance":
        nifty = bt.load_5m_yfinance(args.index_symbol, args.period)
        get = lambda tk: bt.load_5m_yfinance(tk, args.period)
    else:
        nifty = bt.load_5m_csv(os.path.join(args.csv_dir, "NIFTY.csv"))
        get = lambda tk: bt.load_5m_csv(os.path.join(args.csv_dir, f"{tk}.csv"))
    dfs = {}
    for tk in [t.strip() for t in args.tickers.split(",") if t.strip()]:
        try:
            dfs[tk] = get(tk)
        except Exception as e:  # noqa: BLE001
            print(f"[{tk}] skipped: {e}")
    return dfs, nifty


def _params(args):
    return bt.Params(capital=args.capital, risk_pct=args.risk, setup=args.setup,
                     min_score=args.min_score, max_trades_day=args.max_trades_day,
                     **bt.EXIT_MODES[args.exit])


# --------------------------------------------------------------------------
# Offline self-test
# --------------------------------------------------------------------------

def _selftest():
    import tempfile
    # A clean VWAP-continuation day (reuse the backtest fixtures' shape).
    day = bt._synthetic_day("2026-03-03", [
        (9, 15, 100.0, 100.4, 99.7, 100.1, 800),
        (9, 20, 100.1, 100.5, 99.9, 100.2, 800),
        (9, 25, 100.2, 100.5, 100.0, 100.3, 800),
        (9, 30, 100.3, 101.6, 100.3, 101.5, 3000),
        (9, 35, 101.5, 101.5, 101.0, 101.1, 1200),
        (9, 40, 101.1, 101.3, 101.0, 101.05, 900),
        (9, 45, 101.05, 102.0, 101.2, 101.9, 3500),
        (9, 50, 101.9, 102.4, 101.7, 102.3, 2000),
        (9, 55, 102.3, 103.9, 102.2, 103.8, 2500),
        (15, 15, 103.8, 103.9, 103.5, 103.6, 1000),
    ])
    nifty = bt._synthetic_day("2026-03-03", [
        (9, 15, 20000, 20010, 19990, 20005, 0), (9, 20, 20005, 20015, 20000, 20010, 0),
        (9, 25, 20010, 20020, 20005, 20015, 0), (9, 30, 20015, 20030, 20010, 20025, 0),
        (9, 35, 20025, 20035, 20020, 20030, 0), (9, 40, 20030, 20040, 20025, 20035, 0),
        (9, 45, 20035, 20045, 20030, 20040, 0), (9, 50, 20040, 20050, 20035, 20045, 0),
        (9, 55, 20045, 20055, 20040, 20050, 0), (15, 15, 20050, 20055, 20045, 20050, 0),
    ])
    p = bt.Params(setup="both", min_score=40.0)
    dfs = {"AAA": day}

    # Mid-session scan (as of 09:50) sees the resumption; entry/stop populated.
    date, rows = scan(dfs, nifty, p, as_of=dtime(9, 50))
    assert rows and rows[0]["ticker"] == "AAA", rows
    r0 = rows[0]
    assert r0["setup"] in ("orb", "vwap") and r0["score"] > 0
    assert r0["entry"] > 0 and r0["stop"] < r0["entry"] and r0["qty"] > 0
    dash = format_dashboard(date, rows, p)
    assert "PAPER ONLY — NO ORDERS" in dash

    # Logging is append-only and dedups.
    with tempfile.TemporaryDirectory() as d:
        log = os.path.join(d, "paper_log.csv")
        added1 = log_signals(rows, log, "2026-03-03 09:50")
        added2 = log_signals(rows, log, "2026-03-03 09:55")   # same signals
        assert added1 == len(rows) and added2 == 0, (added1, added2)
        # Before reconcile there is no outcome.
        assert all(r["net_R"] == "" for r in _read_log(log))
        # Reconcile fills the outcome from the full day.
        upd = reconcile(dfs, nifty, p, log)
        assert upd >= 1
        done = _read_log(log)
        assert all(r["net_R"] != "" for r in done), done
        summ = paper_summary(log)
        assert summ["reconciled_taken_signals"] >= 1, summ

    print("paper_trade self-tests passed.")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _add_common(ap):
    ap.add_argument("--source", choices=["yfinance", "csv"], default="csv")
    ap.add_argument("--csv-dir", default=".")
    ap.add_argument("--period", default="5d")
    ap.add_argument("--tickers", required=True)
    ap.add_argument("--index-symbol", default="^NSEI")
    ap.add_argument("--setup", choices=["orb", "vwap", "both"], default="both")
    ap.add_argument("--exit", choices=list(bt.EXIT_MODES), default="fixed2r")
    ap.add_argument("--min-score", type=float, default=60.0)
    ap.add_argument("--max-trades-day", type=int, default=5)
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--risk", type=float, default=0.005)
    ap.add_argument("--log", default="paper_log.csv")


def main():
    ap = argparse.ArgumentParser(description="NSE intraday PAPER signals (no orders)")
    sub = ap.add_subparsers(dest="cmd")
    sc = sub.add_parser("scan", help="build today's dashboard + log signals")
    _add_common(sc)
    sc.add_argument("--as-of", help="HH:MM to cap detection (default: all bars)")
    rc = sub.add_parser("reconcile", help="fill outcomes for logged signals")
    _add_common(rc)
    args = ap.parse_args()

    if not args.cmd:
        _selftest()
        print("\nNo command; ran the offline self-test. "
              "Use `scan` or `reconcile` with --tickers to run for real (paper).")
        return

    dfs, nifty = _load(args)
    if not dfs:
        print("No data loaded."); return
    p = _params(args)
    cost = IntradayCostModel()

    if args.cmd == "scan":
        as_of = None
        if args.as_of:
            hh, mm = (int(x) for x in args.as_of.split(":"))
            as_of = dtime(hh, mm)
        date, rows = scan(dfs, nifty, p, cost, as_of)
        print(format_dashboard(date, rows, p))
        added = log_signals(rows, args.log, datetime.now().strftime("%Y-%m-%d %H:%M"))
        print(f"\nLogged {added} new signal(s) to {args.log} (PAPER — no orders).")
    else:  # reconcile
        upd = reconcile(dfs, nifty, p, args.log, cost)
        print(f"Reconciled {upd} signal(s) in {args.log}.")
        print("Paper summary:", paper_summary(args.log))
        print("\nGo/No-Go reminder: only after 8-12 weeks of positive, "
              "cost-adjusted paper expectancy is there a case to revisit live "
              "capital. Current backtests are a NO-GO.")


if __name__ == "__main__":
    main()

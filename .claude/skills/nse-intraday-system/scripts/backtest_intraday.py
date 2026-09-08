#!/usr/bin/env python3
"""
Intraday backtest harness for NSE cash equities — ORB and VWAP-continuation.

This is the "fix what is available" answer to the data problem. It will NOT
give you a multi-year study (free 5-minute history is ~60 days and is
survivorship-biased), but it *will* run a real, cost-aware, look-ahead-safe
backtest of both setups on whatever 5-minute data you can get — free yfinance
(~60 days) or any 5-minute CSV exported from a broker/data vendor.

Setups (choose with --setup orb|vwap|both):
  - ORB: opening range = 09:15-09:30 IST; entry on a confirmed breakout;
    structural stop = opening-range low.
  - VWAP continuation: impulse above VWAP -> pullback toward VWAP on
    contracting volume (holding above it) -> resumption on a volume
    expansion; structural stop = pullback low; losing VWAP invalidates.

What it does honestly:
  - Filters: price > session VWAP, bullish NIFTY regime, positive relative
    strength vs NIFTY, and a volume-expansion check.
  - No look-ahead: the decision is made on a bar's CLOSE, the fill is the
    NEXT bar's OPEN.
  - 2R target, square-off at session end.
  - Every trade is charged real transaction costs + slippage (cost_model.py),
    so results are reported GROSS and NET.

What it is NOT: proof of an edge. Two months of one setup on today's tickers
is a smoke test, not a validation. Route real validation (walk-forward,
out-of-sample, robustness) through the `backtest-expert` skill.

Usage:
  python3 backtest_intraday.py                       # runs the offline self-test
  python3 backtest_intraday.py --tickers RELIANCE,TCS,INFY --period 60d
  python3 backtest_intraday.py --source csv --csv-dir ./data --tickers RELIANCE
     (expects ./data/RELIANCE.csv and ./data/NIFTY.csv with columns
      Datetime,Open,High,Low,Close,Volume)
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import time as dtime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cost_model import IntradayCostModel  # noqa: E402
from intraday_calculator import position_size  # noqa: E402

IST = "Asia/Kolkata"


@dataclass
class Params:
    or_start: dtime = dtime(9, 15)
    or_end: dtime = dtime(9, 30)
    entry_cutoff: dtime = dtime(11, 30)   # primary window
    squareoff: dtime = dtime(15, 15)
    vol_mult: float = 1.5
    rr: float = 2.0
    risk_pct: float = 0.005
    capital: float = 1_000_000.0
    min_or_bars: int = 3
    max_position_pct: float | None = 0.40
    setup: str = "both"          # "orb", "vwap", or "both"
    impulse_pct: float = 0.004   # VWAP setup: min impulse move from open (0.4%)


# --------------------------------------------------------------------------
# Data preparation (pure; no network)
# --------------------------------------------------------------------------

def prepare(df):
    """Sort, tag date/time, add per-session VWAP. Expects OHLCV columns."""
    import pandas as pd  # local import so --help works without pandas

    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    # Normalise to IST.
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert(IST)
    else:
        df.index = df.index.tz_convert(IST)
    df = df.sort_index()
    df["date"] = df.index.date
    df["t"] = df.index.time
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    tpv = typical * df["Volume"]
    df["vwap"] = (tpv.groupby(df["date"]).cumsum()
                  / df["Volume"].groupby(df["date"]).cumsum())
    return df


# --------------------------------------------------------------------------
# Strategy core (pure; unit-tested on synthetic data)
# --------------------------------------------------------------------------

def _simulate_long(bars, entry, stop, target, p, cost: IntradayCostModel):
    """Walk bars from the entry bar onward; return the exit and P&L."""
    qty = position_size(p.capital, entry, stop, p.risk_pct,
                        p.max_position_pct)["shares"]
    if qty <= 0:
        return None
    risk_per_share = entry - stop
    exit_price, reason = None, None
    for ts, bar in bars.iterrows():
        # Conservative: if a bar spans both stop and target, assume stop first.
        if bar["Low"] <= stop:
            exit_price, reason = stop, "stop"
            break
        if bar["High"] >= target:
            exit_price, reason = target, "target"
            break
        if bar["t"] >= p.squareoff:
            exit_price, reason = bar["Close"], "squareoff"
            break
    if exit_price is None:
        exit_price, reason = bars.iloc[-1]["Close"], "eod"

    gross = qty * (exit_price - entry)
    fees = cost.trade_costs(entry, exit_price, qty)["total"]
    net = gross - fees
    return {
        "qty": qty, "entry": round(entry, 2), "exit": round(exit_price, 2),
        "stop": round(stop, 2), "target": round(target, 2), "reason": reason,
        "risk_per_share": round(risk_per_share, 4),
        "gross_pnl": round(gross, 2), "fees": round(fees, 2),
        "net_pnl": round(net, 2),
        "gross_R": round(gross / (qty * risk_per_share), 3),
        "net_R": round(net / (qty * risk_per_share), 3),
    }


def _regime_rs_ok(ndf, nifty_open, stock_open, bar):
    """Bullish NIFTY regime and positive relative strength at this bar."""
    n_here = ndf[ndf.index <= bar.name]
    if n_here.empty:
        return False
    nifty_ret = n_here.iloc[-1]["Close"] / nifty_open - 1
    stock_ret = bar["Close"] / stock_open - 1
    return nifty_ret > 0 and stock_ret > nifty_ret


def run_day_orb(sdf, ndf, p, cost: IntradayCostModel):
    """Opening Range Breakout for one stock-day. Returns a trade dict or None."""
    or_bars = sdf[(sdf["t"] >= p.or_start) & (sdf["t"] < p.or_end)]
    if len(or_bars) < p.min_or_bars:
        return None
    or_high = or_bars["High"].max()
    or_low = or_bars["Low"].min()
    stock_open = sdf.iloc[0]["Open"]
    if ndf is None or ndf.empty:
        return None
    nifty_open = ndf.iloc[0]["Open"]

    trig = sdf[(sdf["t"] >= p.or_end) & (sdf["t"] < p.entry_cutoff)]
    for ts, bar in trig.iterrows():
        prior = sdf[sdf.index < ts]
        if len(prior) < p.min_or_bars:
            continue
        avg_vol = prior["Volume"].mean()
        if avg_vol <= 0:
            continue
        # Long breakout conditions.
        if not (bar["Close"] > or_high
                and bar["Close"] > bar["vwap"]
                and bar["Volume"] > p.vol_mult * avg_vol):
            continue
        if not _regime_rs_ok(ndf, nifty_open, stock_open, bar):
            continue
        # Fill on the NEXT bar's open (no look-ahead).
        after = sdf[sdf.index > ts]
        if after.empty:
            break
        entry = after.iloc[0]["Open"]
        stop = or_low
        if entry - stop <= 0:
            break
        target = entry + p.rr * (entry - stop)
        trade = _simulate_long(after, entry, stop, target, p, cost)
        if trade:
            trade["setup"] = "orb"
            trade["date"] = str(bar["date"])
            trade["trigger_time"] = str(bar["t"])
        return trade
    return None


def run_day_vwap(sdf, ndf, p, cost: IntradayCostModel):
    """VWAP continuation for one stock-day. Returns a trade dict or None.

    State machine over the primary window: impulse above VWAP -> pullback toward
    VWAP on contracting volume (holding above VWAP) -> resumption above the
    pullback high on a volume expansion. Entry fills on the next bar's open;
    the structural stop is the pullback low. Losing VWAP invalidates the setup.
    """
    stock_open = sdf.iloc[0]["Open"]
    if ndf is None or ndf.empty:
        return None
    nifty_open = ndf.iloc[0]["Open"]

    window = sdf[(sdf["t"] >= p.or_end) & (sdf["t"] < p.entry_cutoff)]
    phase = "seek_impulse"
    impulse_high = impulse_vol = resume_ref = pullback_low = None

    for ts, bar in window.iterrows():
        prior = sdf[sdf.index < ts]
        if len(prior) < p.min_or_bars:
            continue
        avg_vol = prior["Volume"].mean()
        if avg_vol <= 0:
            continue
        vwap = bar["vwap"]

        if phase == "seek_impulse":
            if (bar["Close"] > vwap and bar["Volume"] >= avg_vol
                    and (bar["Close"] / stock_open - 1) >= p.impulse_pct):
                impulse_high = bar["High"]
                impulse_vol = bar["Volume"]
                pullback_low = None                    # set from pullback bars
                phase = "seek_pullback"
            continue

        if phase == "seek_pullback":
            if bar["Low"] < vwap:                      # lost VWAP -> invalid
                phase = "seek_impulse"
                continue
            pullback_low = (bar["Low"] if pullback_low is None
                            else min(pullback_low, bar["Low"]))
            # Contraction: eased off the impulse high on lighter volume.
            if bar["Volume"] < impulse_vol and bar["Close"] < impulse_high:
                resume_ref = bar["High"]
                phase = "seek_resume"
            continue

        if phase == "seek_resume":
            if bar["Low"] < vwap:                      # lost VWAP -> restart
                phase = "seek_impulse"
                continue
            pullback_low = min(pullback_low, bar["Low"])
            resumed = (bar["Close"] > resume_ref and bar["Close"] > vwap
                       and bar["Volume"] > p.vol_mult * avg_vol)
            if resumed and _regime_rs_ok(ndf, nifty_open, stock_open, bar):
                after = sdf[sdf.index > ts]
                if after.empty:
                    break
                entry = after.iloc[0]["Open"]
                stop = pullback_low
                if entry - stop <= 0:
                    break
                target = entry + p.rr * (entry - stop)
                trade = _simulate_long(after, entry, stop, target, p, cost)
                if trade:
                    trade["setup"] = "vwap"
                    trade["date"] = str(bar["date"])
                    trade["trigger_time"] = str(bar["t"])
                return trade
    return None


def run_day(sdf, ndf, p, cost: IntradayCostModel):
    """Dispatch to the configured setup(s). In 'both' mode, take the earliest
    trigger of the day (one trade per stock per day)."""
    if p.setup == "orb":
        return run_day_orb(sdf, ndf, p, cost)
    if p.setup == "vwap":
        return run_day_vwap(sdf, ndf, p, cost)
    # both -> earliest trigger wins
    candidates = [t for t in (run_day_orb(sdf, ndf, p, cost),
                              run_day_vwap(sdf, ndf, p, cost)) if t]
    if not candidates:
        return None
    return min(candidates, key=lambda t: t["trigger_time"])


def backtest(stock_df, nifty_df, p: Params, cost: IntradayCostModel | None = None):
    cost = cost or IntradayCostModel()
    s = prepare(stock_df)
    n = prepare(nifty_df)
    trades = []
    for d in sorted(set(s["date"])):
        sdf = s[s["date"] == d]
        ndf = n[n["date"] == d]
        t = run_day(sdf, ndf, p, cost)
        if t:
            trades.append(t)
    return trades


# --------------------------------------------------------------------------
# Metrics (pure)
# --------------------------------------------------------------------------

def metrics(trades, capital):
    if not trades:
        return {"trades": 0, "note": "no trades generated in this window"}
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = -sum(t["net_pnl"] for t in losses)
    net_total = sum(t["net_pnl"] for t in trades)
    gross_total = sum(t["gross_pnl"] for t in trades)
    fees_total = sum(t["fees"] for t in trades)

    # Equity curve + max drawdown (net).
    eq, peak, max_dd = capital, capital, 0.0
    for t in trades:
        eq += t["net_pnl"]
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)

    n = len(trades)
    avg_win_R = (sum(t["net_R"] for t in wins) / len(wins)) if wins else 0.0
    avg_loss_R = (-sum(t["net_R"] for t in losses) / len(losses)) if losses else 0.0
    return {
        "trades": n,
        "win_rate": round(100 * len(wins) / n, 1),
        "gross_pnl": round(gross_total, 2),
        "fees": round(fees_total, 2),
        "net_pnl": round(net_total, 2),
        "net_return_pct": round(100 * net_total / capital, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else float("inf"),
        "expectancy_net_R": round(sum(t["net_R"] for t in trades) / n, 3),
        "avg_win_R": round(avg_win_R, 2),
        "avg_loss_R": round(avg_loss_R, 2),
        "max_drawdown_pct": round(100 * max_dd, 2),
    }


# --------------------------------------------------------------------------
# Data loaders (network / disk)
# --------------------------------------------------------------------------

def load_5m_yfinance(ticker, period="60d"):
    import yfinance as yf
    sym = ticker if ticker.endswith(".NS") or ticker.startswith("^") else ticker + ".NS"
    df = yf.download(sym, period=period, interval="5m",
                     progress=False, auto_adjust=True)
    if df.empty:
        raise RuntimeError(f"no data for {sym} (Yahoo may be blocked or symbol wrong)")
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close", "Volume"]]


def load_5m_csv(path):
    import pandas as pd
    df = pd.read_csv(path)
    tcol = next(c for c in df.columns if c.lower() in ("datetime", "date", "timestamp"))
    df[tcol] = pd.to_datetime(df[tcol])
    df = df.set_index(tcol)
    df.columns = [c.capitalize() for c in df.columns]
    return df[["Open", "High", "Low", "Close", "Volume"]]


# --------------------------------------------------------------------------
# Self-test (offline, deterministic)
# --------------------------------------------------------------------------

def _synthetic_day(date_str, bars):
    """bars: list of (hh, mm, o, h, l, c, v). Returns a DataFrame."""
    import pandas as pd
    idx, rows = [], []
    for hh, mm, o, h, l, c, v in bars:
        idx.append(pd.Timestamp(f"{date_str} {hh:02d}:{mm:02d}", tz=IST))
        rows.append({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v})
    return pd.DataFrame(rows, index=idx)


def _selftest():
    import pandas as pd

    # --- Day 1: engineered clean ORB long that reaches the 2R target ---
    # OR bars (09:15/20/25): high 101, low 99.5. Breakout bar 09:30 closes 101.6
    # on big volume; enter 09:35 open 101.6, stop 99.5 (risk 2.1), target ~105.8.
    day1 = _synthetic_day("2026-01-01", [
        (9, 15, 100.0, 100.8, 99.6, 100.5, 1000),
        (9, 20, 100.5, 101.0, 99.5, 100.2, 1000),
        (9, 25, 100.2, 100.9, 99.8, 100.6, 1000),
        (9, 30, 100.6, 101.7, 100.5, 101.6, 5000),   # breakout, 5x volume
        (9, 35, 101.6, 103.0, 101.4, 102.8, 3000),   # entry bar (open 101.6)
        (9, 40, 102.8, 106.2, 102.6, 106.0, 3000),   # hits target ~105.8
        (9, 45, 106.0, 106.5, 105.0, 105.5, 2000),
        (15, 15, 105.5, 105.6, 105.0, 105.2, 1000),
    ])
    # --- Day 2: breakout that fails and stops out at OR low ---
    day2 = _synthetic_day("2026-01-02", [
        (9, 15, 100.0, 100.8, 99.6, 100.5, 1000),
        (9, 20, 100.5, 101.0, 99.5, 100.2, 1000),
        (9, 25, 100.2, 100.9, 99.8, 100.6, 1000),
        (9, 30, 100.6, 101.7, 100.5, 101.6, 5000),   # breakout
        (9, 35, 101.6, 101.8, 99.4, 99.6, 3000),     # entry 101.6, low 99.4 -> stop 99.5
        (15, 15, 99.6, 99.8, 99.0, 99.2, 1000),
    ])
    stock = pd.concat([day1, day2])

    # NIFTY: up on both days, and by less than the stock (positive RS).
    nifty = pd.concat([
        _synthetic_day("2026-01-01", [
            (9, 15, 20000, 20010, 19990, 20005, 0),
            (9, 20, 20005, 20015, 20000, 20010, 0),
            (9, 25, 20010, 20020, 20005, 20015, 0),
            (9, 30, 20015, 20030, 20010, 20025, 0),   # +0.1% vs stock +1.1%
            (9, 35, 20025, 20040, 20020, 20035, 0),
            (9, 40, 20035, 20050, 20030, 20045, 0),
            (9, 45, 20045, 20055, 20040, 20050, 0),
            (15, 15, 20050, 20055, 20045, 20050, 0),
        ]),
        _synthetic_day("2026-01-02", [
            (9, 15, 20000, 20010, 19990, 20005, 0),
            (9, 20, 20005, 20015, 20000, 20010, 0),
            (9, 25, 20010, 20020, 20005, 20015, 0),
            (9, 30, 20015, 20030, 20010, 20025, 0),
            (9, 35, 20025, 20040, 20020, 20035, 0),
            (15, 15, 20035, 20045, 20030, 20040, 0),
        ]),
    ])

    p = Params(setup="orb")
    cost = IntradayCostModel()
    trades = backtest(stock, nifty, p, cost)

    assert len(trades) == 2, f"expected 2 trades, got {len(trades)}: {trades}"
    t1, t2 = trades
    assert t1["setup"] == "orb" and t2["setup"] == "orb"
    assert t1["reason"] == "target", t1
    assert t2["reason"] == "stop", t2
    # Costs must make net worse than gross on both.
    assert t1["net_pnl"] < t1["gross_pnl"] and t2["net_pnl"] < t2["gross_pnl"]
    # Winner net R should be < 2 (costs shave the 2R target).
    assert 1.5 < t1["net_R"] < 2.0, t1["net_R"]
    # Loser net R should be worse than -1 (costs add to the loss).
    assert t2["net_R"] < -1.0, t2["net_R"]

    m = metrics(trades, p.capital)
    assert m["trades"] == 2 and m["win_rate"] == 50.0, m
    assert m["fees"] > 0 and m["net_pnl"] < m["gross_pnl"], m

    # --- VWAP continuation: engineered win (impulse -> contraction -> resume) ---
    vday = _synthetic_day("2026-02-02", [
        (9, 15, 100.0, 100.4, 99.7, 100.1, 800),
        (9, 20, 100.1, 100.5, 99.9, 100.2, 800),
        (9, 25, 100.2, 100.5, 100.0, 100.3, 800),
        (9, 30, 100.3, 101.6, 100.3, 101.5, 3000),   # impulse above VWAP
        (9, 35, 101.5, 101.5, 101.0, 101.1, 1200),   # contraction (resume_ref 101.5)
        (9, 40, 101.1, 101.3, 101.0, 101.05, 900),   # further contraction, holds VWAP
        (9, 45, 101.05, 102.0, 101.2, 101.9, 3500),  # resumption on volume expansion
        (9, 50, 101.9, 102.4, 101.7, 102.3, 2000),   # entry bar (fill open 101.9)
        (9, 55, 102.3, 103.9, 102.2, 103.8, 2500),   # 2R target ~103.7 hit
        (15, 15, 103.8, 103.9, 103.5, 103.6, 1000),
    ])
    vnifty = _synthetic_day("2026-02-02", [
        (9, 15, 20000, 20010, 19990, 20005, 0), (9, 20, 20005, 20015, 20000, 20010, 0),
        (9, 25, 20010, 20020, 20005, 20015, 0), (9, 30, 20015, 20030, 20010, 20025, 0),
        (9, 35, 20025, 20035, 20020, 20030, 0), (9, 40, 20030, 20040, 20025, 20035, 0),
        (9, 45, 20035, 20045, 20030, 20040, 0), (9, 50, 20040, 20050, 20035, 20045, 0),
        (9, 55, 20045, 20055, 20040, 20050, 0), (15, 15, 20050, 20055, 20045, 20050, 0),
    ])
    vt = backtest(vday, vnifty, Params(setup="vwap"), cost)
    assert len(vt) == 1 and vt[0]["setup"] == "vwap", vt
    assert vt[0]["reason"] == "target" and vt[0]["trigger_time"] == "09:45:00", vt[0]
    assert vt[0]["stop"] == 101.0, vt[0]          # pullback low, not impulse origin
    assert vt[0]["net_R"] < vt[0]["gross_R"], vt[0]

    # --- VWAP invalidation: pullback breaks below VWAP -> no trade ---
    inval = _synthetic_day("2026-02-03", [
        (9, 15, 100.0, 100.4, 99.7, 100.1, 800),
        (9, 20, 100.1, 100.5, 99.9, 100.2, 800),
        (9, 25, 100.2, 100.5, 100.0, 100.3, 800),
        (9, 30, 100.3, 101.6, 100.3, 101.5, 3000),   # impulse
        (9, 35, 101.5, 101.5, 100.0, 100.2, 1200),   # breaks below VWAP -> reset
        (9, 40, 100.2, 100.4, 100.0, 100.1, 900),    # stays weak, no new impulse
        (9, 45, 100.1, 100.3, 99.9, 100.0, 900),
        (15, 15, 100.0, 100.1, 99.8, 99.9, 1000),
    ])
    assert backtest(inval, vnifty, Params(setup="vwap"), cost) == [], "should be no VWAP trade"

    # --- both mode: earliest trigger of the day wins (ORB 09:30 < VWAP 09:45) ---
    both = backtest(vday, vnifty, Params(setup="both"), cost)
    assert len(both) == 1 and both[0]["setup"] == "orb", both
    assert both[0]["trigger_time"] == "09:30:00", both[0]

    print("backtest self-tests passed.")
    return trades, m


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="NSE intraday ORB backtest (cost-aware)")
    ap.add_argument("--tickers", help="comma-separated, e.g. RELIANCE,TCS,INFY")
    ap.add_argument("--source", choices=["yfinance", "csv"], default="yfinance")
    ap.add_argument("--csv-dir", default=".")
    ap.add_argument("--period", default="60d", help="yfinance period (max ~60d for 5m)")
    ap.add_argument("--setup", choices=["orb", "vwap", "both"], default="both")
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--risk", type=float, default=0.005)
    ap.add_argument("--index-symbol", default="^NSEI", help="regime/RS index (Yahoo: ^NSEI)")
    args = ap.parse_args()

    if not args.tickers:
        _selftest()
        print("\nNo --tickers given; ran the offline self-test only.")
        print("Provide --tickers to run on real 5-minute data.")
        return

    p = Params(capital=args.capital, risk_pct=args.risk, setup=args.setup)
    cost = IntradayCostModel()

    if args.source == "yfinance":
        nifty = load_5m_yfinance(args.index_symbol, args.period)
        load = lambda tk: load_5m_yfinance(tk, args.period)
    else:
        nifty = load_5m_csv(os.path.join(args.csv_dir, "NIFTY.csv"))
        load = lambda tk: load_5m_csv(os.path.join(args.csv_dir, f"{tk}.csv"))

    all_trades = []
    for tk in [t.strip() for t in args.tickers.split(",") if t.strip()]:
        try:
            trades = backtest(load(tk), nifty, p, cost)
        except Exception as e:  # noqa: BLE001
            print(f"[{tk}] skipped: {e}")
            continue
        for t in trades:
            t["ticker"] = tk
        all_trades.extend(trades)
        print(f"[{tk}] {len(trades)} trade(s)")

    print(f"\n=== Aggregate (NET of costs) — setup: {args.setup} ===")
    m = metrics(all_trades, args.capital)
    for k, v in m.items():
        print(f"  {k:20s}: {v}")
    if all_trades:
        by_setup = {}
        for t in all_trades:
            by_setup[t.get("setup", "?")] = by_setup.get(t.get("setup", "?"), 0) + 1
        print(f"  {'trades_by_setup':20s}: {by_setup}")
    print("\nReminder: a ~60-day, single-setup backtest on today's tickers is a "
          "smoke test, not validation. Use the backtest-expert skill for the "
          "real thing (walk-forward, out-of-sample, robustness, survivorship).")


if __name__ == "__main__":
    main()

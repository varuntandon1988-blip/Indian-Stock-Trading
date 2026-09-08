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
    # Portfolio selection (backtest_portfolio only):
    min_score: float = 0.0       # five-factor score gate (0 = take every signal)
    max_trades_day: int = 5      # cap across the whole universe per day
    daily_loss_cap_pct: float = 0.015   # stop taking new trades past -1.5%/day
    # Exit engine (composable; defaults reproduce the fixed-2R behaviour):
    target_R: float | None = 2.0     # hard take-profit in R (None = no fixed target)
    partial_R: float | None = None   # scale out at this R ...
    partial_frac: float = 0.5        # ... this fraction, then move stop to breakeven
    be_after_R: float | None = None  # move stop to entry once this R is reached
    trail: bool = False              # after be_after_R, trail a 1R-wide stop on close


# Named exit presets used by the CLI (--exit) and the experiment sweep.
EXIT_MODES = {
    "fixed2r":    dict(target_R=2.0),
    "t1":         dict(target_R=1.0),
    "t1p5":       dict(target_R=1.5),
    "partial_be": dict(target_R=2.0, partial_R=1.0, partial_frac=0.5),
    "trail":      dict(target_R=None, be_after_R=1.0, trail=True),
}


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

def _simulate_long(bars, entry, stop0, p, cost: IntradayCostModel):
    """Walk bars from the entry bar onward, applying the configured exit engine.

    Composable exits (all in R-multiples of the initial risk = entry - stop0):
      - target_R:   hard take-profit (None = none).
      - partial_R:  scale out `partial_frac` here, then move the stop to break-even.
      - be_after_R: move the stop to entry once this R is reached.
      - trail:      after be_after_R, trail a 1R-wide stop on the bar close.
    Within a bar the order is stop -> partial -> breakeven -> trail -> target ->
    square-off, and a bar spanning both stop and target is assumed to hit the
    stop first (conservative). Costs are charged per executed leg.
    """
    qty = position_size(p.capital, entry, stop0, p.risk_pct,
                        p.max_position_pct)["shares"]
    if qty <= 0:
        return None
    risk = entry - stop0
    stop, remaining = stop0, qty
    gross, fees, legs, last_px = 0.0, 0.0, [], entry
    partial_done = p.partial_R is None

    def book(px, q, reason):
        nonlocal gross, fees, last_px
        gross += q * (px - entry)
        fees += cost.trade_costs(entry, px, q)["total"]
        legs.append(reason)
        last_px = px

    for ts, bar in bars.iterrows():
        if bar["Low"] <= stop:                       # stop (or trailed / BE stop)
            reason = ("stop" if stop == stop0
                      else "be_stop" if abs(stop - entry) < 1e-9 else "trail_stop")
            book(stop, remaining, reason)
            remaining = 0
            break
        if not partial_done and bar["High"] >= entry + p.partial_R * risk:
            q1 = int(remaining * p.partial_frac)
            if q1 > 0:
                book(entry + p.partial_R * risk, q1, "partial")
                remaining -= q1
            partial_done = True
            stop = max(stop, entry)                   # protect the runner at breakeven
        if p.be_after_R is not None and stop < entry and bar["High"] >= entry + p.be_after_R * risk:
            stop = entry
        if p.trail and bar["High"] >= entry + (p.be_after_R or 1.0) * risk:
            stop = max(stop, bar["Close"] - risk)     # 1R-wide trail on the close
        if p.target_R is not None and bar["High"] >= entry + p.target_R * risk:
            book(entry + p.target_R * risk, remaining, "target")
            remaining = 0
            break
        if bar["t"] >= p.squareoff:
            book(bar["Close"], remaining, "squareoff")
            remaining = 0
            break
    if remaining > 0:
        book(bars.iloc[-1]["Close"], remaining, "eod")

    net = gross - fees
    reason = legs[0] if len(set(legs)) == 1 else "+".join(dict.fromkeys(legs))
    return {
        "qty": qty, "entry": round(entry, 2), "exit": round(last_px, 2),
        "stop": round(stop0, 2), "reason": reason,
        "risk_per_share": round(risk, 4),
        "gross_pnl": round(gross, 2), "fees": round(fees, 2),
        "net_pnl": round(net, 2),
        "gross_R": round(gross / (qty * risk), 3),
        "net_R": round(net / (qty * risk), 3),
        "legs": legs,
    }


def _regime_rs(ndf, nifty_open, stock_open, bar):
    """Return (nifty_ret, stock_ret) since the open at this bar, or None."""
    n_here = ndf[ndf.index <= bar.name]
    if n_here.empty:
        return None
    nifty_ret = n_here.iloc[-1]["Close"] / nifty_open - 1
    stock_ret = bar["Close"] / stock_open - 1
    return nifty_ret, stock_ret


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def _score_long(nifty_ret, rs_spread, vol_ratio, structure_pts):
    """Five-factor composite (0-100) for a long, from data available in the
    backtest. Catalyst is 0 here (no news feed), so the ceiling is ~85 — the
    same ceiling the skill documents for a purely technical setup.

    - Regime (0-20): NIFTY up since open; full at +0.5%.
    - Relative strength (0-25): stock minus NIFTY return; full at +1.0% spread.
    - Volume (0-20): trigger-bar volume vs the day's average so far; 0 at 1x,
      full at >=2x.
    - Structure (0-20): setup-specific cleanliness, passed in.
    """
    regime = _clip(nifty_ret / 0.005, 0, 1) * 20
    rs = _clip(rs_spread / 0.01, 0, 1) * 25
    vol = _clip((vol_ratio - 1.0) / 1.0, 0, 1) * 20
    structure = _clip(structure_pts, 0, 20)
    return round(regime + rs + vol + structure, 1)


@dataclass
class Candidate:
    setup: str
    date: str
    trigger_ts: object
    trigger_time: str
    entry: float
    stop: float
    target: float
    score: float


def detect_orb(sdf, ndf, p, cost=None):
    """Detect an ORB long for one stock-day. Returns a scored Candidate or None."""
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
        if not (bar["Close"] > or_high
                and bar["Close"] > bar["vwap"]
                and bar["Volume"] > p.vol_mult * avg_vol):
            continue
        rr = _regime_rs(ndf, nifty_open, stock_open, bar)
        if rr is None or not (rr[0] > 0 and rr[1] > rr[0]):
            continue
        after = sdf[sdf.index > ts]
        if after.empty:
            break
        entry = after.iloc[0]["Open"]
        stop = or_low
        if entry - stop <= 0:
            break
        structure = 12 + _clip((bar["Close"] / bar["vwap"] - 1) / 0.005, 0, 1) * 8
        score = _score_long(rr[0], rr[1] - rr[0], bar["Volume"] / avg_vol, structure)
        return Candidate("orb", str(bar["date"]), ts, str(bar["t"]),
                         entry, stop, entry + p.rr * (entry - stop), score)
    return None


def detect_vwap(sdf, ndf, p, cost=None):
    """Detect a VWAP-continuation long for one stock-day. Scored Candidate/None.

    State machine: impulse above VWAP -> pullback toward VWAP on contracting
    volume (holding above VWAP) -> resumption above the pullback high on a
    volume expansion. Structural stop = pullback low; losing VWAP invalidates.
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
                pullback_low = None
                phase = "seek_pullback"
            continue

        if phase == "seek_pullback":
            if bar["Low"] < vwap:
                phase = "seek_impulse"
                continue
            pullback_low = (bar["Low"] if pullback_low is None
                            else min(pullback_low, bar["Low"]))
            if bar["Volume"] < impulse_vol and bar["Close"] < impulse_high:
                resume_ref = bar["High"]
                phase = "seek_resume"
            continue

        if phase == "seek_resume":
            if bar["Low"] < vwap:
                phase = "seek_impulse"
                continue
            pullback_low = min(pullback_low, bar["Low"])
            resumed = (bar["Close"] > resume_ref and bar["Close"] > vwap
                       and bar["Volume"] > p.vol_mult * avg_vol)
            rr = _regime_rs(ndf, nifty_open, stock_open, bar)
            if resumed and rr is not None and rr[0] > 0 and rr[1] > rr[0]:
                after = sdf[sdf.index > ts]
                if after.empty:
                    break
                entry = after.iloc[0]["Open"]
                stop = pullback_low
                if entry - stop <= 0:
                    break
                structure = 14 + _clip((bar["Close"] / resume_ref - 1) / 0.003, 0, 1) * 6
                score = _score_long(rr[0], rr[1] - rr[0],
                                    bar["Volume"] / avg_vol, structure)
                return Candidate("vwap", str(bar["date"]), ts, str(bar["t"]),
                                 entry, stop, entry + p.rr * (entry - stop), score)
    return None


def simulate_candidate(sdf, cand: Candidate, p, cost: IntradayCostModel):
    """Simulate a detected candidate; returns a trade dict (or None)."""
    after = sdf[sdf.index > cand.trigger_ts]
    if after.empty:
        return None
    trade = _simulate_long(after, cand.entry, cand.stop, p, cost)
    if trade:
        trade["setup"] = cand.setup
        trade["date"] = cand.date
        trade["trigger_time"] = cand.trigger_time
        trade["score"] = cand.score
    return trade


def _detectors(setup):
    return {"orb": [detect_orb], "vwap": [detect_vwap],
            "both": [detect_orb, detect_vwap]}[setup]


def _day_candidate(sdf, ndf, p, cost):
    """Best candidate for one stock-day (highest score; earliest breaks ties)."""
    cands = [c for det in _detectors(p.setup)
             if (c := det(sdf, ndf, p, cost)) is not None]
    if not cands:
        return None
    return max(cands, key=lambda c: (c.score, -_hhmmss(c.trigger_time)))


def _hhmmss(t):
    h, m, s = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + s


def run_day(sdf, ndf, p, cost: IntradayCostModel):
    """One trade per stock-day. In 'both' mode the higher-scoring setup wins
    (earliest trigger breaks ties)."""
    cand = _day_candidate(sdf, ndf, p, cost)
    return simulate_candidate(sdf, cand, p, cost) if cand else None


def backtest(stock_df, nifty_df, p: Params, cost: IntradayCostModel | None = None):
    """Single-stock backtest (no universe ranking, no daily cap)."""
    cost = cost or IntradayCostModel()
    s = prepare(stock_df)
    n = prepare(nifty_df)
    trades = []
    for d in sorted(set(s["date"])):
        t = run_day(s[s["date"] == d], n[n["date"] == d], p, cost)
        if t:
            trades.append(t)
    return trades


def backtest_portfolio(stock_dfs: dict, nifty_df, p: Params,
                       cost: IntradayCostModel | None = None):
    """Universe backtest with score-ranked selection and daily controls.

    Each day: collect the best candidate per stock, drop those below
    `min_score`, rank the survivors by score (earliest trigger breaks ties),
    then take up to `max_trades_day`, stopping once the day's realized net P&L
    breaches `-daily_loss_cap_pct` of capital. Trades are sized at constant
    risk against full capital (still leverage-naive for concurrent positions —
    the R-expectancy is the sizing-independent metric)."""
    cost = cost or IntradayCostModel()
    prepared = {tk: prepare(df) for tk, df in stock_dfs.items()}
    n = prepare(nifty_df)
    all_dates = sorted({d for s in prepared.values() for d in set(s["date"])})

    trades = []
    for d in all_dates:
        ndf = n[n["date"] == d]
        if ndf.empty:
            continue
        day_cands = []
        for tk, s in prepared.items():
            sdf = s[s["date"] == d]
            if sdf.empty:
                continue
            c = _day_candidate(sdf, ndf, p, cost)
            if c and c.score >= p.min_score:
                day_cands.append((tk, sdf, c))
        day_cands.sort(key=lambda x: (-x[2].score, _hhmmss(x[2].trigger_time)))

        day_pnl, taken = 0.0, 0
        loss_cap = -p.capital * p.daily_loss_cap_pct
        for tk, sdf, c in day_cands:
            if taken >= p.max_trades_day or day_pnl <= loss_cap:
                break
            tr = simulate_candidate(sdf, c, p, cost)
            if not tr:
                continue
            tr["ticker"] = tk
            trades.append(tr)
            day_pnl += tr["net_pnl"]
            taken += 1
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
    assert "score" in t1 and 0 < t1["score"] <= 100, t1
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

    # --- scoring: bounds and monotonicity ---
    assert _score_long(0.005, 0.01, 2.0, 20) == 85.0        # full technical ceiling
    assert _score_long(0.0, 0.0, 1.0, 0.0) == 0.0
    assert (_score_long(0.005, 0.01, 2.0, 20)
            > _score_long(0.001, 0.002, 1.2, 12))           # stronger reads score higher

    # --- both mode: higher-scoring setup wins (one trade/stock/day) ---
    both = backtest(vday, vnifty, Params(setup="both"), cost)
    assert len(both) == 1 and both[0]["setup"] in ("orb", "vwap"), both
    assert "score" in both[0], both[0]

    # --- portfolio: score gate filters, and a listed ticker is tagged ---
    pf = backtest_portfolio({"AAA": vday}, vnifty,
                            Params(setup="vwap", min_score=0.0), cost)
    assert len(pf) == 1 and pf[0]["ticker"] == "AAA", pf
    high_gate = backtest_portfolio({"AAA": vday}, vnifty,
                                   Params(setup="vwap", min_score=99.0), cost)
    assert high_gate == [], "score gate 99 should reject the candidate"

    # --- exit engine: same winning VWAP day under different exit modes ---
    # vday: entry 101.9, risk 0.9 -> 1R@102.8, 2R@103.7; the 09:55 bar high 103.9.
    t_fixed = backtest(vday, vnifty, Params(setup="vwap", **EXIT_MODES["fixed2r"]), cost)
    assert t_fixed[0]["reason"] == "target" and 1.8 < t_fixed[0]["gross_R"] <= 2.0
    t_1r = backtest(vday, vnifty, Params(setup="vwap", **EXIT_MODES["t1"]), cost)
    assert t_1r[0]["reason"] == "target" and 0.95 <= t_1r[0]["gross_R"] <= 1.05, t_1r[0]
    t_pb = backtest(vday, vnifty, Params(setup="vwap", **EXIT_MODES["partial_be"]), cost)
    # half booked at 1R, remainder at 2R -> blended ~1.5R gross; both legs present.
    assert "partial" in t_pb[0]["reason"] and 1.4 <= t_pb[0]["gross_R"] <= 1.6, t_pb[0]

    print("backtest self-tests passed.")
    return trades, m


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="NSE intraday backtest (ORB + VWAP, cost-aware)")
    ap.add_argument("--tickers", help="comma-separated, e.g. RELIANCE,TCS,INFY")
    ap.add_argument("--source", choices=["yfinance", "csv"], default="yfinance")
    ap.add_argument("--csv-dir", default=".")
    ap.add_argument("--period", default="60d", help="yfinance period (max ~60d for 5m)")
    ap.add_argument("--setup", choices=["orb", "vwap", "both"], default="both")
    ap.add_argument("--exit", choices=list(EXIT_MODES), default="fixed2r",
                    help="exit engine: fixed2r | t1 | t1p5 | partial_be | trail")
    ap.add_argument("--rank", action="store_true",
                    help="score-ranked portfolio selection with daily controls")
    ap.add_argument("--min-score", type=float, default=0.0,
                    help="five-factor score gate (with --rank; e.g. 60)")
    ap.add_argument("--max-trades-day", type=int, default=5)
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--risk", type=float, default=0.005)
    ap.add_argument("--index-symbol", default="^NSEI", help="regime/RS index (Yahoo: ^NSEI)")
    args = ap.parse_args()

    if not args.tickers:
        _selftest()
        print("\nNo --tickers given; ran the offline self-test only.")
        print("Provide --tickers to run on real 5-minute data.")
        return

    p = Params(capital=args.capital, risk_pct=args.risk, setup=args.setup,
               min_score=args.min_score, max_trades_day=args.max_trades_day,
               **EXIT_MODES[args.exit])
    cost = IntradayCostModel()

    if args.source == "yfinance":
        nifty = load_5m_yfinance(args.index_symbol, args.period)
        load = lambda tk: load_5m_yfinance(tk, args.period)
    else:
        nifty = load_5m_csv(os.path.join(args.csv_dir, "NIFTY.csv"))
        load = lambda tk: load_5m_csv(os.path.join(args.csv_dir, f"{tk}.csv"))

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    dfs = {}
    for tk in tickers:
        try:
            dfs[tk] = load(tk)
        except Exception as e:  # noqa: BLE001
            print(f"[{tk}] skipped: {e}")

    if args.rank:
        all_trades = backtest_portfolio(dfs, nifty, p, cost)
        mode = f"portfolio ranked, min_score={args.min_score}, cap={args.max_trades_day}/day"
    else:
        all_trades = []
        for tk, df in dfs.items():
            trades = backtest(df, nifty, p, cost)
            for t in trades:
                t["ticker"] = tk
            all_trades.extend(trades)
            print(f"[{tk}] {len(trades)} trade(s)")
        mode = "per-stock (no ranking)"

    print(f"\n=== Aggregate (NET of costs) — setup: {args.setup} | exit: {args.exit} | {mode} ===")
    m = metrics(all_trades, args.capital)
    for k, v in m.items():
        print(f"  {k:20s}: {v}")
    if all_trades:
        by_setup = {}
        for t in all_trades:
            by_setup[t.get("setup", "?")] = by_setup.get(t.get("setup", "?"), 0) + 1
        print(f"  {'trades_by_setup':20s}: {by_setup}")
    print("\nReminder: even a multi-year run on a handful of tickers is a smoke "
          "test, not validation. Use the backtest-expert skill for the real "
          "thing (walk-forward, out-of-sample, robustness, survivorship).")


if __name__ == "__main__":
    main()

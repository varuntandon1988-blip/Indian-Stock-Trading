#!/usr/bin/env python3
"""
Broker data adapter — fetch 5-minute candles and write the CSVs that
paper_trade.py consumes. READ-ONLY: this file never places, modifies, or
cancels an order. It only calls historical/candle endpoints.

Why this exists: the paper loop needs live NSE 5-minute data, and the cloud
environment cannot reach free sources. On your own machine, your Groww API
access can. This adapter turns that access into `<TICKER>.csv` + `NIFTY.csv`
in an output directory, each with columns Datetime,Open,High,Low,Close,Volume.

Groww implementation status: BEST-EFFORT. The Groww Trading API method names /
parameters below are marked `# VERIFY` — check them against your installed
`growwapi` version (pip show growwapi) and the current Groww API docs, and
adjust the two marked calls if needed. Everything downstream (CSV shape, the
whole paper pipeline) is already tested; only this fetch seam needs your token
and a quick verify.

Auth: set GROWW_ACCESS_TOKEN in the environment (never hard-code or commit it).

    export GROWW_ACCESS_TOKEN=xxxxx
    python3 broker_adapter.py --tickers INFY,HDFCBANK,ICICIBANK --out-dir ./data
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta


def _to_dataframe(candles):
    """Normalise a list of candle rows into the CSV schema.

    Accepts rows shaped like [epoch_or_iso, open, high, low, close, volume]
    (the common Groww/Kite candle layout). Adjust indices if your payload
    differs.
    """
    import pandas as pd

    rows = []
    for c in candles:
        ts = c[0]
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts)          # epoch seconds -> local
        rows.append({"Datetime": ts, "Open": c[1], "High": c[2],
                     "Low": c[3], "Close": c[4], "Volume": c[5]})
    df = pd.DataFrame(rows)
    df["Datetime"] = pd.to_datetime(df["Datetime"])
    return df.set_index("Datetime")[["Open", "High", "Low", "Close", "Volume"]]


def fetch_5m_groww(symbol: str, days: int = 5, is_index: bool = False):
    """Fetch `days` of 5-minute candles for one NSE symbol via the Groww API.

    READ-ONLY. Returns a DataFrame in the CSV schema.
    """
    from growwapi import GrowwAPI                      # pip install growwapi

    token = os.environ.get("GROWW_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("GROWW_ACCESS_TOKEN not set in environment")
    groww = GrowwAPI(token)

    end = datetime.now()
    start = end - timedelta(days=days)
    fmt = "%Y-%m-%d %H:%M:%S"

    # VERIFY: method name, argument names and the exchange/segment constants
    # against your growwapi version. This is the ONLY broker-specific seam.
    segment = getattr(groww, "SEGMENT_CASH", "CASH")
    exchange = getattr(groww, "EXCHANGE_NSE", "NSE")
    resp = groww.get_historical_candle_data(               # VERIFY
        trading_symbol=symbol,
        exchange=exchange,
        segment=segment,
        start_time=start.strftime(fmt),
        end_time=end.strftime(fmt),
        interval_in_minutes=5,
    )
    candles = resp["candles"] if isinstance(resp, dict) else resp   # VERIFY
    return _to_dataframe(candles)


def write_universe_csvs(tickers, out_dir, index_symbol="NIFTY", days=5,
                        fetch=fetch_5m_groww):
    """Fetch every ticker + the index and write CSVs paper_trade.py reads."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    # Index first (regime / relative-strength reference).
    try:
        idf = fetch(index_symbol, days=days, is_index=True)
        idf.to_csv(os.path.join(out_dir, "NIFTY.csv"))
        written.append("NIFTY")
    except Exception as e:  # noqa: BLE001
        print(f"[NIFTY] fetch failed: {e}")
    for tk in tickers:
        try:
            df = fetch(tk, days=days)
            df.to_csv(os.path.join(out_dir, f"{tk}.csv"))
            written.append(tk)
        except Exception as e:  # noqa: BLE001
            print(f"[{tk}] fetch failed: {e}")
    return written


def main():
    ap = argparse.ArgumentParser(description="Fetch 5m candles -> CSV (READ-ONLY)")
    ap.add_argument("--tickers", required=True, help="comma-separated NSE symbols")
    ap.add_argument("--out-dir", default="./data")
    ap.add_argument("--index-symbol", default="NIFTY")
    ap.add_argument("--days", type=int, default=5)
    args = ap.parse_args()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    written = write_universe_csvs(tickers, args.out_dir, args.index_symbol, args.days)
    print(f"Wrote CSVs for: {', '.join(written) or '(none)'} -> {args.out_dir}")


if __name__ == "__main__":
    main()

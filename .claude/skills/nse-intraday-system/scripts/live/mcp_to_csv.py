#!/usr/bin/env python3
"""
Convert saved GrowwMCP `fetch_historical_candle_data` JSON into the CSVs that
paper_trade.py reads. Deterministic, so the daily routine does not rely on the
model hand-formatting rows.

The daily routine saves each symbol's tool result to
`<raw_dir>/<TICKER>.json` (TICKER = INFY, HDFCBANK, ICICIBANK, NIFTY). This
turns each into `<out_dir>/<TICKER>.csv` with columns
Datetime,Open,High,Low,Close,Volume. Index rows have null volume -> 0
(paper_trade only uses the index's Close for regime / relative strength).

    python3 mcp_to_csv.py --raw-dir data/raw --out-dir data
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os


def _candles(obj):
    """Find the candles list inside the tool payload (handles nesting)."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        if "candles" in obj:
            return obj["candles"]
        if "result" in obj:
            return _candles(obj["result"])
    raise ValueError("no 'candles' array found in payload")


def convert(path, out_dir):
    ticker = os.path.splitext(os.path.basename(path))[0]
    with open(path) as f:
        rows = _candles(json.load(f))
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{ticker}.csv")
    n = 0
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Datetime", "Open", "High", "Low", "Close", "Volume"])
        for c in rows:
            v = c.get("volume")
            v = 0 if v in (None, "") else v
            w.writerow([c["timestamp"], c["open"], c["high"],
                        c["low"], c["close"], v])
            n += 1
    return out, n


def main():
    ap = argparse.ArgumentParser(description="GrowwMCP candle JSON -> paper CSVs")
    ap.add_argument("--raw-dir", default="data/raw")
    ap.add_argument("--out-dir", default="data")
    args = ap.parse_args()
    files = sorted(glob.glob(os.path.join(args.raw_dir, "*.json")))
    if not files:
        print(f"No JSON files in {args.raw_dir}")
        return
    for p in files:
        out, n = convert(p, args.out_dir)
        print(f"{os.path.basename(p)} -> {out} ({n} candles)")


if __name__ == "__main__":
    main()

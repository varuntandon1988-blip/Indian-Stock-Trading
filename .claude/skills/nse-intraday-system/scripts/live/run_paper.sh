#!/usr/bin/env bash
# Daily paper-trading runner for the NSE Intraday System. NO ORDERS.
#
#   ./run_paper.sh scan        # during the session: fetch data + log signals
#   ./run_paper.sh reconcile   # after 15:30 IST: fill outcomes + print summary
#
# Configure via env (or edit the defaults):
#   TICKERS   comma-separated NSE symbols   (default: INFY,HDFCBANK,ICICIBANK)
#   MIN_SCORE five-factor gate              (default: 60)
#   COMMIT    "1" to git-commit the log     (default: 0)
# Requires: GROWW_ACCESS_TOKEN in the environment (read-only data use).

set -euo pipefail
CMD="${1:-scan}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"     # .../scripts/live
SKILL="$(cd "$HERE/.." && pwd)"                          # .../scripts
DATA="${DATA_DIR:-$HERE/data}"
LOG="${LOG_FILE:-$HERE/paper_log.csv}"
TICKERS="${TICKERS:-INFY,HDFCBANK,ICICIBANK}"
MIN_SCORE="${MIN_SCORE:-60}"

mkdir -p "$DATA"

# 1) Pull fresh 5-minute candles into CSVs (READ-ONLY broker call).
python3 "$HERE/broker_adapter.py" --tickers "$TICKERS" --out-dir "$DATA" --days 5

# 2) Run the paper command (no order tools exist in this path).
python3 "$SKILL/paper_trade.py" "$CMD" \
    --source csv --csv-dir "$DATA" \
    --tickers "$TICKERS" --min-score "$MIN_SCORE" --log "$LOG"

# 3) Optionally persist the log to git so it survives across machines/days.
if [ "${COMMIT:-0}" = "1" ] && [ "$CMD" = "reconcile" ]; then
    git -C "$SKILL/../../.." add "$LOG" 2>/dev/null || true
    git -C "$SKILL/../../.." commit -q -m "paper log: $(date +%F) reconcile" || true
fi

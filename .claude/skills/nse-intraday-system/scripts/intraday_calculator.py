#!/usr/bin/env python3
"""
Intraday decision calculator for the NSE Intraday System skill.

Pure-stdlib helpers that turn the five-factor score and a trade's price
levels into a deterministic TRADE / NO-TRADE decision, a position size, and
a running daily-risk check. No external dependencies, no network, no API keys.

The numbers here encode the *rules* of the strategy plan (score gate, R:R gate,
constant-risk sizing, daily loss cap). They do NOT prove the strategy has an
edge — that must come from out-of-sample backtesting (see the backtest-expert
skill) with realistic costs and slippage.

Run `python3 intraday_calculator.py` for a self-test / demo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import floor
from typing import Literal

# ---- Five-factor score model (weights sum to 100) -------------------------

FACTOR_CAPS = {
    "regime": 20,          # A. Market regime
    "relative_strength": 25,  # B. Relative strength vs NIFTY and sector
    "volume": 20,          # C. Volume / flow confirmation
    "structure": 20,       # D. Price structure
    "catalyst": 15,        # E. Catalyst
}
MAX_SCORE = sum(FACTOR_CAPS.values())  # 100

# Gates from the plan. Kept as named constants so robustness tests can sweep
# them (the plan explicitly warns against a single "magic number").
MIN_SCORE = 75            # Section 5 / decision flow
MIN_RR = 2.0              # Minimum reward:risk of 1:2
RISK_PCT_DEFAULT = 0.005  # 0.5% of capital per trade (band: 0.0035-0.0050)
DAILY_LOSS_CAP_PCT = 0.015  # 1.5% of capital per day
MAX_TRADES_DEFAULT = 5    # 3-5 trades/day


def composite_score(
    regime: float,
    relative_strength: float,
    volume: float,
    structure: float,
    catalyst: float,
) -> float:
    """Sum the five factor sub-scores, validating each against its cap."""
    parts = {
        "regime": regime,
        "relative_strength": relative_strength,
        "volume": volume,
        "structure": structure,
        "catalyst": catalyst,
    }
    for name, value in parts.items():
        cap = FACTOR_CAPS[name]
        if not (0 <= value <= cap):
            raise ValueError(f"{name} score {value} out of range 0..{cap}")
    return round(sum(parts.values()), 2)


def reward_risk(entry: float, stop: float, target: float,
                direction: Literal["long", "short"]) -> float:
    """Reward:risk multiple. Raises if the stop is on the wrong side."""
    if direction == "long":
        risk = entry - stop
        reward = target - entry
    else:  # short
        risk = stop - entry
        reward = entry - target
    if risk <= 0:
        raise ValueError("Stop is on the wrong side of entry (risk <= 0)")
    if reward <= 0:
        raise ValueError("Target is on the wrong side of entry (reward <= 0)")
    return round(reward / risk, 2)


@dataclass
class TradeDecision:
    take: bool
    score: float
    rr: float
    reasons: list[str] = field(default_factory=list)


def trade_gate(
    score: float,
    entry: float,
    stop: float,
    target: float,
    direction: Literal["long", "short"],
    regime_supports_direction: bool,
    min_score: float = MIN_SCORE,
    min_rr: float = MIN_RR,
) -> TradeDecision:
    """Apply the score, R:R and regime-alignment gates. All must pass."""
    reasons: list[str] = []
    rr = reward_risk(entry, stop, target, direction)

    if score < min_score:
        reasons.append(f"score {score} < {min_score}")
    if rr < min_rr:
        reasons.append(f"R:R {rr} < {min_rr}")
    if not regime_supports_direction:
        reasons.append(f"market regime does not support a {direction} trade")

    take = not reasons
    if take:
        reasons.append(f"passed all gates (score {score}, R:R {rr})")
    return TradeDecision(take=take, score=score, rr=rr, reasons=reasons)


def position_size(
    capital: float,
    entry: float,
    stop: float,
    risk_pct: float = RISK_PCT_DEFAULT,
    max_position_pct: float | None = None,
) -> dict:
    """Constant-risk position sizing.

    shares = floor(risk_amount / stop_distance), so the rupee loss if the stop
    hits is (approximately) the same regardless of the stock's price. Optionally
    cap notional exposure at `max_position_pct` of capital.
    """
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        raise ValueError("stop_distance must be > 0")
    risk_amount = capital * risk_pct
    shares = floor(risk_amount / stop_distance)

    capped = False
    if max_position_pct is not None and shares * entry > capital * max_position_pct:
        shares = floor((capital * max_position_pct) / entry)
        capped = True

    return {
        "risk_amount": round(risk_amount, 2),
        "stop_distance": round(stop_distance, 4),
        "shares": shares,
        "notional": round(shares * entry, 2),
        "actual_risk": round(shares * stop_distance, 2),
        "notional_pct_of_capital": round(100 * shares * entry / capital, 2),
        "capped_by_max_position_pct": capped,
    }


def daily_risk_check(
    capital: float,
    realized_pnl: list[float],
    trades_taken: int,
    daily_loss_cap_pct: float = DAILY_LOSS_CAP_PCT,
    max_trades: int = MAX_TRADES_DEFAULT,
) -> dict:
    """Decide whether trading should stop for the day.

    Stops when cumulative loss breaches the daily cap OR the trade count hits
    the max. There is no discretionary 'recovery trade'.
    """
    net = sum(realized_pnl)
    loss_cap = -abs(capital * daily_loss_cap_pct)
    hit_loss_cap = net <= loss_cap
    hit_trade_cap = trades_taken >= max_trades

    reasons = []
    if hit_loss_cap:
        reasons.append(f"daily loss {net:.0f} breached cap {loss_cap:.0f}")
    if hit_trade_cap:
        reasons.append(f"trade count {trades_taken} >= max {max_trades}")

    return {
        "net_pnl": round(net, 2),
        "loss_cap": round(loss_cap, 2),
        "stop_trading": hit_loss_cap or hit_trade_cap,
        "reasons": reasons or ["within daily limits"],
    }


def _selftest() -> None:
    # Score model
    assert MAX_SCORE == 100
    s = composite_score(regime=18, relative_strength=22, volume=17,
                        structure=16, catalyst=10)
    assert s == 83.0, s

    # R:R
    assert reward_risk(1000, 990, 1027, "long") == 2.7  # 27/10
    assert reward_risk(1000, 1010, 980, "short") == 2.0  # 20/10

    # Gate: passes
    d = trade_gate(83, 1000, 990, 1025, "long", regime_supports_direction=True)
    assert d.take is True, d.reasons
    # Gate: fails on score
    d2 = trade_gate(70, 1000, 990, 1025, "long", regime_supports_direction=True)
    assert d2.take is False and "score" in d2.reasons[0]
    # Gate: fails on R:R (1:1.5)
    d3 = trade_gate(90, 1000, 990, 1015, "long", regime_supports_direction=True)
    assert d3.take is False and any("R:R" in r for r in d3.reasons)

    # Position sizing: 0.5% of 10L over a ₹10 stop = 500 shares
    ps = position_size(1_000_000, entry=1000, stop=990, risk_pct=0.005)
    assert ps["shares"] == 500, ps
    assert ps["actual_risk"] == 5000.0, ps
    # Notional cap kicks in
    psc = position_size(1_000_000, entry=1000, stop=990, risk_pct=0.005,
                        max_position_pct=0.30)
    assert psc["shares"] == 300 and psc["capped_by_max_position_pct"]

    # Daily risk: three -0.5% losses => stop
    dr = daily_risk_check(1_000_000, [-5000, -5000, -5000], trades_taken=3)
    assert dr["stop_trading"] is True, dr
    dr2 = daily_risk_check(1_000_000, [-5000, 8000], trades_taken=2)
    assert dr2["stop_trading"] is False, dr2

    print("All self-tests passed.")


if __name__ == "__main__":
    _selftest()
    print("\n--- Demo: score 83 LONG, entry 1000 / stop 990 / target 1025 ---")
    dec = trade_gate(83, 1000, 990, 1025, "long", regime_supports_direction=True)
    print("decision:", "TRADE" if dec.take else "NO TRADE", "|", "; ".join(dec.reasons))
    print("sizing:", position_size(1_000_000, 1000, 990, 0.005,
                                   max_position_pct=0.40))

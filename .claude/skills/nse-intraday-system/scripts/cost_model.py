#!/usr/bin/env python3
"""
Indian intraday (MIS) equity transaction-cost model.

This is the piece that makes the system honest: it turns a trade's prices and
size into the *real* rupee cost (statutory charges + brokerage + GST + a
slippage/spread allowance), and converts an expectancy stated in R-multiples
into expectancy *net of costs*. A gross edge that looks fine can be entirely
eaten here — which is exactly what we need to see before trading.

All rates are the standard NSE intraday-equity schedule for a discount broker
(edit `IntradayCostModel` fields for your broker). Pure stdlib, no network.

Run `python3 cost_model.py` for the self-test + a worked example.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IntradayCostModel:
    # Brokerage: percent of leg value, capped per order (Zerodha-style
    # "0.03% or ₹20, whichever is lower", charged on BOTH legs).
    brokerage_pct: float = 0.0003
    brokerage_cap: float = 20.0
    # STT: intraday equity is charged on the SELL side only.
    stt_sell_pct: float = 0.00025          # 0.025%
    # Exchange transaction charge (NSE equity), both legs, on turnover.
    exchange_txn_pct: float = 0.0000297    # 0.00297%
    # SEBI turnover fee (₹10 per crore), both legs.
    sebi_pct: float = 0.000001             # 0.0001%
    # Stamp duty: buy side only.
    stamp_buy_pct: float = 0.00003         # 0.003%
    # GST on (brokerage + exchange txn + SEBI fee).
    gst_rate: float = 0.18
    # Slippage + half-spread allowance, per side, on turnover. This is the
    # execution reality most cost calculators omit. Default ~3 bps/side.
    slippage_pct_per_side: float = 0.0003

    def trade_costs(self, buy_price: float, sell_price: float, qty: int) -> dict:
        """Full cost breakdown (in rupees) for one round-trip trade."""
        buy_val = buy_price * qty
        sell_val = sell_price * qty
        turnover = buy_val + sell_val

        brokerage = (min(self.brokerage_pct * buy_val, self.brokerage_cap)
                     + min(self.brokerage_pct * sell_val, self.brokerage_cap))
        stt = self.stt_sell_pct * sell_val
        exch = self.exchange_txn_pct * turnover
        sebi = self.sebi_pct * turnover
        stamp = self.stamp_buy_pct * buy_val
        gst = self.gst_rate * (brokerage + exch + sebi)
        slippage = self.slippage_pct_per_side * turnover

        total = brokerage + stt + exch + sebi + stamp + gst + slippage
        return {
            "brokerage": round(brokerage, 2),
            "stt": round(stt, 2),
            "exchange_txn": round(exch, 2),
            "sebi": round(sebi, 2),
            "stamp": round(stamp, 2),
            "gst": round(gst, 2),
            "slippage": round(slippage, 2),
            "total": round(total, 2),
            "pct_of_buy_value": round(100 * total / buy_val, 4) if buy_val else 0.0,
            "pct_of_turnover": round(100 * total / turnover, 4) if turnover else 0.0,
        }

    def roundtrip_cost_frac(self, price: float, qty: int) -> float:
        """Round-trip cost as a fraction of position (buy) value, at ~flat price.

        Used to convert costs into R-multiples. Uses sell_price == buy_price so
        the result is the pure cost drag, independent of the trade's outcome.
        """
        c = self.trade_costs(price, price, qty)
        return c["total"] / (price * qty)

    @staticmethod
    def cost_in_R(cost_frac: float, stop_frac: float) -> float:
        """Cost expressed in R-multiples.

        1R risk = stop_frac of the position's price. Cost drag = cost_frac of
        the position's price. So cost_in_R = cost_frac / stop_frac. Tighter
        stops (small stop_frac) make costs a *bigger* fraction of 1R — the
        reason scalping tight stops rarely survives Indian costs.
        """
        if stop_frac <= 0:
            raise ValueError("stop_frac must be > 0")
        return cost_frac / stop_frac

    def net_reward_risk(self, entry: float, stop: float, target: float,
                        direction: str, qty: int) -> dict:
        """Reward:risk after costs, per share.

        Costs widen the effective risk and shrink the effective reward, so the
        *net* R:R is always worse than the headline R:R.
        """
        cost_per_share = self.trade_costs(entry, entry, qty)["total"] / qty
        if direction == "long":
            gross_risk = entry - stop
            gross_reward = target - entry
        else:
            gross_risk = stop - entry
            gross_reward = entry - target
        if gross_risk <= 0 or gross_reward <= 0:
            raise ValueError("stop/target on the wrong side of entry")
        net_risk = gross_risk + cost_per_share
        net_reward = gross_reward - cost_per_share
        return {
            "gross_rr": round(gross_reward / gross_risk, 2),
            "net_rr": round(net_reward / net_risk, 2) if net_reward > 0 else 0.0,
            "cost_per_share": round(cost_per_share, 4),
        }


def net_expectancy(win_rate: float, avg_win_R: float, avg_loss_R: float,
                   cost_R: float) -> dict:
    """Per-trade expectancy in R, before and after costs.

    Costs are subtracted every trade regardless of outcome, so they hit the
    net figure once per round trip.
    """
    if not 0 <= win_rate <= 1:
        raise ValueError("win_rate must be in [0, 1]")
    gross = win_rate * avg_win_R - (1 - win_rate) * avg_loss_R
    net = gross - cost_R
    return {
        "gross_expectancy_R": round(gross, 4),
        "cost_R": round(cost_R, 4),
        "net_expectancy_R": round(net, 4),
        "viable": net > 0,
    }


def _selftest() -> None:
    m = IntradayCostModel()

    # A ₹1,000 stock, 500 shares (₹5,00,000 position). Round-trip cost should
    # land in a realistic band (~0.06%-0.15% of position value incl. slippage).
    c = m.trade_costs(1000, 1000, 500)
    assert 0.06 <= c["pct_of_buy_value"] <= 0.15, c
    frac = m.roundtrip_cost_frac(1000, 500)
    assert abs(frac * 100 - c["pct_of_buy_value"]) < 1e-2   # allow 2dp rounding

    # Cost in R depends on stop width. With a typical intraday stop of ~0.5%
    # of price, cost_R = frac / 0.005.
    cost_R_tight = IntradayCostModel.cost_in_R(frac, 0.005)   # 0.5% stop
    cost_R_wide = IntradayCostModel.cost_in_R(frac, 0.01)     # 1.0% stop
    assert cost_R_tight > cost_R_wide                          # tighter stop = more cost drag

    # The plan's illustration (win 50%, win 1.25R, loss 1R) has gross +0.125R.
    # At a realistic 0.5% intraday stop it goes NEGATIVE after costs — that is
    # point 1, demonstrated.
    ex = net_expectancy(0.50, 1.25, 1.0, cost_R_tight)
    assert ex["gross_expectancy_R"] == 0.125
    assert ex["viable"] is False, ex          # thin edge dies after costs

    # Even at a wide 1% stop the same edge is only marginally positive — inside
    # the cost estimate's error bars, i.e. not a dependable edge.
    ex_wide = net_expectancy(0.50, 1.25, 1.0, cost_R_wide)
    assert 0 < ex_wide["net_expectancy_R"] < 0.05, ex_wide

    # The Section-12 target profile (win 45%, win 2R, loss 1R) SURVIVES both.
    assert net_expectancy(0.45, 2.0, 1.0, cost_R_tight)["viable"] is True
    assert net_expectancy(0.45, 2.0, 1.0, cost_R_wide)["viable"] is True

    # Net R:R is always worse than gross R:R.
    rr = m.net_reward_risk(1000, 990, 1025, "long", 500)
    assert rr["net_rr"] < rr["gross_rr"], rr

    print("cost_model self-tests passed.")


if __name__ == "__main__":
    _selftest()
    m = IntradayCostModel()
    print("\n--- Worked example: ₹1,000 stock, 500 sh ---")
    c = m.trade_costs(1000, 1000, 500)
    print("round-trip cost:", c)
    frac = m.roundtrip_cost_frac(1000, 500)
    for stop_frac, label in [(0.005, "0.5% stop (typical intraday)"),
                             (0.010, "1.0% stop (wide)")]:
        cost_R = IntradayCostModel.cost_in_R(frac, stop_frac)
        print(f"\ncost in R @ {label}: {cost_R:.4f}R")
        print("  plan illustration (50%/1.25R/1R):",
              net_expectancy(0.50, 1.25, 1.0, cost_R))
        print("  Section-12 target (45%/2R/1R):   ",
              net_expectancy(0.45, 2.0, 1.0, cost_R))

"""
Per-trade outcome reconciliation.

execute_orb logs each entry to algo_trade_log with exit_reason='open' and no
realized P&L, because the bracket (take-profit / stop) resolves server-side
later, or the position is force-closed at EOD. This module closes that loop:
it pulls the day's fills from Alpaca, pairs them into round trips, and writes
the realized exit back onto the matching open rows.

Without this, algo_trade_log is write-only noise and the live leak-finder /
drift check / per-trade risk have nothing to run on. See CLAUDE.md caveat 3.

The pairing and P&L math are pure functions (reconstruct_round_trips,
match_exits) so they can be tested offline; reconcile_today wires them to
Alpaca + MotherDuck and is called from end_of_day.
"""


def _reason_from_type(order_type: str) -> str:
    """Map the exit order's type to an exit reason."""
    t = (order_type or "").lower()
    if "stop" in t:
        return "stop"          # stop or stop_limit -> the protective stop hit
    if "limit" in t:
        return "target"        # take-profit limit hit
    return "eod_close"         # plain market order -> the EOD force-close


def reconstruct_round_trips(fills: list[dict]) -> list[dict]:
    """Pair filled orders into round trips, oldest first.

    Each fill: {side: 'buy'|'sell', price, qty, time, type}. A position opens on
    the first fill and closes on the next opposite-side fill (this strategy is
    flat-to-flat, <=2 trades/day, so simple FIFO pairing is exact). Returns
    {direction, entry_price, exit_price, qty, exit_time, exit_reason}.
    """
    trips = []
    lot = None
    for f in fills:
        if lot is None:
            lot = f
        elif f["side"] != lot["side"]:
            trips.append({
                "direction": "long" if lot["side"] == "buy" else "short",
                "entry_price": lot["price"],
                "exit_price": f["price"],
                "qty": min(lot["qty"], f["qty"]),
                "exit_time": f["time"],
                "exit_reason": _reason_from_type(f.get("type")),
            })
            lot = None
        else:
            lot = f  # same side again before a close; start a fresh lot
    return trips


def match_exits(open_rows: list[dict], trips: list[dict]) -> list[dict]:
    """Pair open trade rows (ordered by entry_time) with round trips (ordered by
    time) and compute the realized exit per row. Pure: returns update dicts.

    P&L uses the logged entry_price (the bracket's intended entry) and the actual
    exit fill, so exit_price - entry_price stays consistent with pnl_per_share.
    """
    updates = []
    for row, trip in zip(open_rows, trips):
        sign = 1 if row["direction"] == "long" else -1
        pnl_ps = sign * (trip["exit_price"] - row["entry_price"])
        shares = row.get("shares") or trip["qty"]
        trade_pnl = pnl_ps * shares
        eq_before = row.get("equity_before")
        eq_after = eq_before + trade_pnl if eq_before is not None else None
        updates.append({
            "trade_id": row["trade_id"],
            "exit_price": round(trip["exit_price"], 4),
            "exit_time": trip["exit_time"],
            "exit_reason": trip["exit_reason"],
            "pnl_per_share": round(pnl_ps, 4),
            "trade_pnl": round(trade_pnl, 2),
            "equity_after": round(eq_after, 2) if eq_after is not None else None,
        })
    return updates


def reconcile_today(trade_date: str, ticker: str, mode: str, trading_client=None) -> dict:
    """Fetch today's fills, pair them, and write realized exits onto the open
    rows for (trade_date, ticker, mode). Returns a small status dict."""
    from src.alpaca_client import get_todays_fills
    from src.trade_logger import get_open_trades, update_trade_exit

    fills = get_todays_fills(ticker, trading_client)
    trips = reconstruct_round_trips(fills)
    rows = get_open_trades(trade_date, ticker, mode)
    updates = match_exits(rows, trips)
    for u in updates:
        update_trade_exit(**u)
    return {"open_rows": len(rows), "round_trips": len(trips), "reconciled": len(updates)}

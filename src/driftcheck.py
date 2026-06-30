"""
Live-vs-backtest drift check — the Phase-2 capstone and the go-live gate.

For each LIVE reconciled trade, re-run the backtest signal on that same day and
compare: did the backtest expect the same trade, what ENTRY SLIPPAGE did live
actually pay vs the modeled assumption, and did the live win rate track the
backtest? Persistent drift (realized slippage >> modeled, or live win-rate well
below backtest) is the early warning that the edge is not surviving contact with
the market — the thing the go-live decision hinges on.

The comparison math (realized_slippage_bps, summarize) is pure and unit-tested.
run_driftcheck wires it to algo_trade_log + Alpaca and is DATA-GATED: it reports
cleanly that it is armed-but-waiting until the bot has logged live trades.
"""
import argparse
import statistics as st
from datetime import datetime, timedelta

from config import settings


def realized_slippage_bps(direction: str, level: float, fill: float) -> float:
    """Adverse entry slippage in bps: how much worse than the signal level the
    actual fill was. Positive = paid up (long filled above the level, or short
    filled below it). Negative = price improvement."""
    if not level:
        return 0.0
    if direction == "long":
        return (fill - level) / level * 10_000.0
    return (level - fill) / level * 10_000.0


def summarize(rows: list[dict], modeled_bps: float) -> dict:
    """Aggregate per-trade comparisons into drift metrics + flags."""
    both = [r for r in rows if r.get("traded_live") and r.get("traded_bt")]
    slips = [r["slippage_bps"] for r in both if r.get("slippage_bps") is not None]
    live_wins = sum(1 for r in both if (r.get("pnl_ps_live") or 0) > 0)
    bt_wins = sum(1 for r in both if (r.get("pnl_ps_bt") or 0) > 0)
    mean_slip = st.mean(slips) if slips else 0.0
    med_slip = st.median(slips) if slips else 0.0
    live_wr = live_wins / len(both) if both else 0.0
    bt_wr = bt_wins / len(both) if both else 0.0

    flags = []
    if slips and mean_slip > 2 * modeled_bps:
        flags.append(f"realized entry slippage {mean_slip:.1f}bps >> modeled {modeled_bps:.1f}bps")
    if both and (bt_wr - live_wr) > 0.15:
        flags.append(f"live win rate {live_wr:.0%} well below backtest {bt_wr:.0%}")
    if rows and len(both) / len(rows) < 0.7:
        flags.append("low signal agreement (live took trades the backtest did not, or vice versa)")
    return {
        "n_compared": len(rows), "n_agreed": len(both),
        "only_live": sum(1 for r in rows if r.get("traded_live") and not r.get("traded_bt")),
        "mean_slippage_bps": round(mean_slip, 2), "median_slippage_bps": round(med_slip, 2),
        "modeled_slippage_bps": round(modeled_bps, 2),
        "live_win_rate": round(live_wr, 3), "backtest_win_rate": round(bt_wr, 3),
        "flags": flags,
    }


def run_driftcheck(start: str, end: str, mode: str = "paper", tickers=None) -> dict:
    from src.trade_logger import get_connection
    from src.alpaca_client import get_data_client, fetch_intraday_bars, fetch_daily_bars
    from src.orb_signal import generate_signal, simulate_trade, calculate_atr

    tickers = tickers or settings.TICKERS
    con = get_connection()
    live_rows = con.execute(
        "SELECT trade_date, ticker, direction, entry_price, pnl_per_share "
        "FROM algo_trade_log WHERE trade_date BETWEEN ? AND ? AND mode = ? "
        "AND exit_reason <> 'open' AND COALESCE(strategy, '') <> 'smoke_test' "
        "ORDER BY trade_date, ticker",
        [start, end, mode],
    ).fetchall()
    con.close()

    if not live_rows:
        print(f"  No live reconciled trades in {start}..{end} (mode={mode}).")
        print("  Drift check is ARMED but data-gated — it runs the moment the bot logs live trades.")
        return {"n_compared": 0, "flags": [], "note": "no live trades yet"}

    data_client = get_data_client()
    modeled_entry_bps = settings.BACKTEST_SLIPPAGE_BPS + settings.BACKTEST_SPREAD_BPS / 2.0
    comparisons = []
    for trade_date, ticker, direction, entry_live, pnl_ps_live in live_rows:
        d = str(trade_date)
        try:
            day_dt = datetime.fromisoformat(d).date()
            intraday = fetch_intraday_bars(ticker, day_dt, data_client=data_client, feed="sip")
            ds = (day_dt - timedelta(days=40)).isoformat()
            de = (day_dt - timedelta(days=1)).isoformat()
            daily = fetch_daily_bars(ticker, ds, de, data_client, feed="sip")
            atr = calculate_atr(daily, settings.ATR_PERIOD)
            prev_close = float(daily.iloc[-1]["close"]) if (daily is not None and not daily.empty) else None
            sig = generate_signal(intraday, atr=atr, entry_cutoff=settings.BACKTEST_ENTRY_CUTOFF,
                                  prev_close=prev_close)
        except Exception as ex:
            print(f"  [{ticker} {d}] backtest recompute failed: {ex}")
            sig = None
        row = {"date": d, "ticker": ticker, "traded_live": True, "traded_bt": sig is not None,
               "direction_live": direction, "pnl_ps_live": pnl_ps_live}
        if sig is not None:
            exp = simulate_trade(sig, intraday)
            row["pnl_ps_bt"] = exp["pnl_per_share"]
            row["slippage_bps"] = realized_slippage_bps(direction, sig.entry_price, float(entry_live))
        comparisons.append(row)

    summary = summarize(comparisons, modeled_entry_bps)
    _print(summary, start, end, mode)
    return summary


def _print(s: dict, start: str, end: str, mode: str):
    print(f"\n{'='*64}\n  DRIFT CHECK (live vs backtest): {start}..{end}  mode={mode}\n{'='*64}")
    print(f"  Live trades compared:   {s['n_compared']}  (backtest agreed on {s['n_agreed']})")
    print(f"  Entry slippage:         mean {s['mean_slippage_bps']}bps | median {s['median_slippage_bps']}bps "
          f"(modeled {s['modeled_slippage_bps']}bps)")
    print(f"  Win rate live/backtest: {s['live_win_rate']:.0%} / {s['backtest_win_rate']:.0%}")
    if s["flags"]:
        print("  DRIFT FLAGS:")
        for f in s["flags"]:
            print(f"    - {f}")
    else:
        print("  No drift flags — live is tracking the backtest.")
    print(f"{'='*64}")


def main():
    ap = argparse.ArgumentParser(description="Live-vs-backtest drift check")
    ap.add_argument("--start", default=None, help="ISO date; default 60 days ago")
    ap.add_argument("--end", default=None, help="ISO date; default today (ET)")
    ap.add_argument("--mode", default="paper")
    args = ap.parse_args()
    import pytz
    today = datetime.now(pytz.timezone("US/Eastern")).date()
    end = args.end or today.isoformat()
    start = args.start or (today - timedelta(days=60)).isoformat()
    run_driftcheck(start, end, mode=args.mode)


if __name__ == "__main__":
    main()

"""
Shadow A/B — run every strategy variant in tandem on LIVE data, without extra
accounts or extra risk.

We have one paper account, so we cannot literally trade four variants at once.
Instead, after each close, this records what EACH variant (per ticker, regime
gate on/off) WOULD have done on that day's real data: did it signal, at what
level, and what was the simulated net outcome. Over the paper test period these
rows accumulate into parallel, out-of-sample, live track records, so the
decision "keep SPY? enable the regime gate?" is settled by live evidence rather
than the backtest alone.

The account still trades the live config as the control; this is pure
observation written to algo_shadow_log. shadow_report() compares the variants
with the same statistical tests as the backtest (Sharpe, bootstrap CI, deflated
Sharpe).
"""
import argparse
from datetime import datetime, timedelta

from config import settings
from src.trade_logger import get_connection

# Variants tracked in tandem. Per ticker, regime gate off vs on (the live A/B
# question). Kept small and focused; add variants here to widen the tandem.
VARIANTS = [
    ("baseline", {}),
    ("regime", {"filter_regime": True}),
]

SHADOW_DDL = """
    CREATE TABLE IF NOT EXISTS algo_shadow_log (
        run_date     DATE,
        ticker       VARCHAR,
        variant      VARCHAR,
        took_trade   BOOLEAN,
        direction    VARCHAR,
        entry_level  DOUBLE,
        exit_price   DOUBLE,
        exit_reason  VARCHAR,
        pnl_per_share DOUBLE,       -- net of round-trip costs
        ret_frac     DOUBLE,        -- net pnl_per_share / entry_level (cap-invariant)
        mode         VARCHAR,
        created_at   TIMESTAMP DEFAULT now()
    );
"""


def _log_rows(rows: list[dict], run_date: str, mode: str):
    con = get_connection()
    con.execute(SHADOW_DDL)
    con.execute("DELETE FROM algo_shadow_log WHERE run_date = ? AND mode = ?", [run_date, mode])
    for r in rows:
        con.execute(
            "INSERT INTO algo_shadow_log (run_date, ticker, variant, took_trade, direction, "
            "entry_level, exit_price, exit_reason, pnl_per_share, ret_frac, mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [run_date, r["ticker"], r["variant"], r["took_trade"], r.get("direction"),
             r.get("entry_level"), r.get("exit_price"), r.get("exit_reason"),
             r.get("pnl_per_share"), r.get("ret_frac"), mode],
        )
    con.close()


def run_shadow(date_str: str, data_client=None, tickers=None, mode: str = None) -> int:
    """Compute + log each variant's hypothetical outcome for one day. Best-effort;
    returns the number of rows written. Call after the close (full day's data)."""
    from src.alpaca_client import get_data_client, fetch_intraday_bars, fetch_daily_bars
    from src.orb_signal import generate_signal, simulate_trade, calculate_atr
    from src.costs import round_trip_cost_per_share

    mode = mode or ("paper" if settings.ALPACA_PAPER else "live")
    tickers = tickers or settings.TICKERS
    data_client = data_client or get_data_client()
    day = datetime.fromisoformat(date_str).date()
    rows = []
    for ticker in tickers:
        try:
            intraday = fetch_intraday_bars(ticker, day, data_client=data_client, feed="sip")
            ds = (day - timedelta(days=40)).isoformat()
            de = (day - timedelta(days=1)).isoformat()
            daily = fetch_daily_bars(ticker, ds, de, data_client, feed="sip")
            atr = calculate_atr(daily, settings.ATR_PERIOD)
            prev_close = float(daily.iloc[-1]["close"]) if (daily is not None and not daily.empty) else None
        except Exception as e:
            print(f"  [shadow {ticker} {date_str}] data fetch failed: {e}")
            continue
        for vname, flags in VARIANTS:
            try:
                sig = generate_signal(intraday, atr=atr, entry_cutoff=settings.BACKTEST_ENTRY_CUTOFF,
                                      prev_close=prev_close, **flags)
            except Exception as e:
                print(f"  [shadow {ticker}/{vname}] signal error: {e}")
                continue
            if sig is None:
                rows.append({"ticker": ticker, "variant": vname, "took_trade": False})
                continue
            res = simulate_trade(sig, intraday)
            net_pps = res["pnl_per_share"] - round_trip_cost_per_share(sig.entry_price)
            rows.append({
                "ticker": ticker, "variant": vname, "took_trade": True,
                "direction": sig.direction, "entry_level": sig.entry_price,
                "exit_price": res["exit_price"], "exit_reason": res["exit_reason"],
                "pnl_per_share": round(net_pps, 4),
                "ret_frac": round(net_pps / sig.entry_price, 6) if sig.entry_price else 0.0,
            })
    if rows:
        _log_rows(rows, date_str, mode)
    return len(rows)


def shadow_report(start: str, end: str, mode: str = "paper") -> dict:
    """Compare the tandem variants over a date range, with the same stats as the
    backtest. Sharpe is cap-invariant (per-share returns); total return is shown
    per-share (multiply by the live cap fraction for the account figure)."""
    from src.validate import annualized_sharpe, bootstrap_ci, sign_permutation_p, deflated_sharpe
    import numpy as np

    con = get_connection()
    con.execute(SHADOW_DDL)
    rows = con.execute(
        "SELECT run_date, ticker, variant, took_trade, ret_frac FROM algo_shadow_log "
        "WHERE run_date BETWEEN ? AND ? AND mode = ? ORDER BY run_date",
        [start, end, mode],
    ).fetchall()
    con.close()
    if not rows:
        print(f"  No shadow rows in {start}..{end} (mode={mode}). It logs daily at the close — "
              f"give it trading days to accumulate.")
        return {"variants": {}, "note": "no shadow data yet"}

    # group by (ticker, variant) -> per-evaluated-day return series (0 on no-trade)
    series = {}
    for run_date, ticker, variant, took, ret in rows:
        key = f"{ticker}:{variant}"
        series.setdefault(key, {})
        series[key][str(run_date)] = float(ret) if (took and ret is not None) else 0.0

    print(f"\n{'='*92}\n  SHADOW A/B (live tandem): {start}..{end}  mode={mode}\n{'='*92}")
    print(f"  {'variant':<18}{'days':>6}{'trades':>8}{'win':>6}{'netSharpe':>11}{'totRet/sh':>11}{'SharpeCI':>16}{'DSR':>7}")
    print(f"  {'-'*90}")
    out = {}
    for key in sorted(series):
        days = sorted(series[key])
        rets = np.array([series[key][d] for d in days])
        traded = rets[rets != 0.0]
        wins = int((traded > 0).sum())
        wr = wins / len(traded) if len(traded) else 0.0
        sharpe = annualized_sharpe(rets)
        ci = bootstrap_ci(rets)["sharpe_ci"] if len(rets) > 1 else (0.0, 0.0)
        dsr = deflated_sharpe(rets, n_trials=len(VARIANTS) * max(len(settings.TICKERS), 1))["deflated_sr_prob"] if len(rets) > 2 else 0.0
        out[key] = {"days": len(days), "trades": int(len(traded)), "win_rate": round(wr, 3),
                    "sharpe": round(sharpe, 2), "total_ret_per_share": round(float(rets.sum()), 4),
                    "sharpe_ci": [round(ci[0], 2), round(ci[1], 2)], "dsr": round(dsr, 3)}
        print(f"  {key:<18}{len(days):>6}{len(traded):>8}{wr:>6.0%}{sharpe:>11.2f}"
              f"{rets.sum():>11.2%}{('['+format(ci[0],'.1f')+','+format(ci[1],'.1f')+']'):>16}{dsr:>7.0%}")
    print(f"  {'-'*90}")
    print("  Per-share returns (cap-invariant). Account return ~= totRet/sh x live cap fraction.")
    print(f"{'='*92}")
    return {"variants": out}


def main():
    ap = argparse.ArgumentParser(description="Shadow A/B: log or report variant track records")
    ap.add_argument("--report", action="store_true", help="print the comparison instead of logging a day")
    ap.add_argument("--date", default=None, help="day to log (ISO); default today ET")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--mode", default="paper")
    args = ap.parse_args()
    import pytz
    today = datetime.now(pytz.timezone("US/Eastern")).date()
    if args.report:
        shadow_report(args.start or (today - timedelta(days=120)).isoformat(),
                      args.end or today.isoformat(), mode=args.mode)
    else:
        n = run_shadow(args.date or today.isoformat(), mode=args.mode)
        print(f"  Shadow logged {n} variant row(s) for {args.date or today.isoformat()}.")


if __name__ == "__main__":
    main()

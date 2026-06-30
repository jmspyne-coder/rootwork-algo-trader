"""
MotherDuck Trade Logger.

Logs all trades and daily summaries to my_db for analysis.
Uses the same MotherDuck infrastructure as the Rootwork intelligence platform.
"""
import duckdb
from datetime import datetime
from config import settings


def get_connection():
    """Connect to MotherDuck."""
    return duckdb.connect(f"md:{settings.MOTHERDUCK_DB}?motherduck_token={settings.MOTHERDUCK_TOKEN}")


def init_tables():
    """Create trade log and daily summary tables if they don't exist."""
    con = get_connection()
    con.execute("""
        CREATE TABLE IF NOT EXISTS algo_trade_log (
            trade_id        VARCHAR DEFAULT uuid()::VARCHAR,
            trade_date      DATE,
            ticker          VARCHAR,
            direction       VARCHAR,       -- 'long' or 'short'
            entry_price     DOUBLE,
            stop_price      DOUBLE,
            target_price    DOUBLE,
            exit_price      DOUBLE,
            shares          INTEGER,
            pnl_per_share   DOUBLE,
            trade_pnl       DOUBLE,
            exit_reason     VARCHAR,       -- 'target', 'stop', 'eod_close'
            entry_time      TIMESTAMP,
            exit_time       TIMESTAMP,
            or_high         DOUBLE,
            or_low          DOUBLE,
            range_pct       DOUBLE,
            atr             DOUBLE,
            equity_before   DOUBLE,
            equity_after    DOUBLE,
            vwap_at_entry   DOUBLE,        -- v2 confirmation-filter telemetry
            rvol_at_entry   DOUBLE,
            candle_strength DOUBLE,
            filters_passed  VARCHAR,       -- comma-separated enabled filters that passed
            strategy        VARCHAR DEFAULT 'orb_v2',
            mode            VARCHAR DEFAULT 'paper',  -- 'paper' or 'live'
            created_at      TIMESTAMP DEFAULT now()
        );
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS algo_daily_summary (
            summary_date          DATE PRIMARY KEY,
            ticker                VARCHAR,
            trades_taken          INTEGER,
            wins                  INTEGER,
            losses                INTEGER,
            daily_pnl             DOUBLE,
            equity_start          DOUBLE,
            equity_end            DOUBLE,
            max_drawdown_pct      DOUBLE,
            consecutive_losses    INTEGER,
            was_halted            BOOLEAN DEFAULT FALSE,
            halt_reason           VARCHAR,
            strategy              VARCHAR DEFAULT 'orb_v1',
            mode                  VARCHAR DEFAULT 'paper',
            created_at            TIMESTAMP DEFAULT now()
        );
    """)
    con.execute(RISK_STATE_DDL)
    con.execute(RUN_LOG_DDL)
    con.close()
    # Bring a pre-existing v1 algo_trade_log up to the v2 schema (idempotent).
    migrate_tables()


# Durable risk-state cache. The canonical cross-day quantities (peak equity,
# consecutive losing days) are DERIVED from algo_daily_summary on each run;
# this single-row-per-mode table caches the full state for fast reads and
# auditing. See src/risk_manager.load_risk_state.
RISK_STATE_DDL = """
    CREATE TABLE IF NOT EXISTS algo_risk_state (
        mode                  VARCHAR PRIMARY KEY,   -- 'paper' or 'live'
        as_of_date            DATE,
        peak_equity           DOUBLE,
        current_equity        DOUBLE,
        daily_starting_equity DOUBLE,
        daily_pnl             DOUBLE,
        consecutive_losses    INTEGER,
        trades_today          INTEGER,
        is_halted             BOOLEAN,
        halt_reason           VARCHAR,
        updated_at            TIMESTAMP DEFAULT now()
    );
"""


# Durable per-run heartbeat. One row per entrypoint run, so a no-signal day
# (ran, nothing to do) is distinguishable from a never-ran day (the silent
# failure). The health-check watchdog reads this to prove execute_orb fired.
RUN_LOG_DDL = """
    CREATE TABLE IF NOT EXISTS algo_run_log (
        run_date   DATE,
        step       VARCHAR,    -- 'pre_market' | 'execute_orb' | 'end_of_day' | 'health_check'
        ran_at     TIMESTAMP DEFAULT now(),
        et_hhmm    VARCHAR,
        outcome    VARCHAR,    -- entered | no_signal | halted | skipped_stale | closed_market | error | ok
        detail     VARCHAR,
        mode       VARCHAR
    );
"""


def fetch_daily_history(mode: str) -> list[dict]:
    """Closed-day history for a mode, oldest first. Source of truth for the
    derived cross-day risk quantities (peak equity, consecutive losing days)."""
    con = get_connection()
    rows = con.execute(
        "SELECT summary_date, SUM(daily_pnl) AS daily_pnl, MAX(equity_end) AS equity_end "
        "FROM algo_daily_summary WHERE mode = ? GROUP BY summary_date ORDER BY summary_date",
        [mode],
    ).fetchall()
    con.close()
    return [{"summary_date": r[0], "daily_pnl": r[1], "equity_end": r[2]} for r in rows]


def read_risk_cache(mode: str) -> dict | None:
    """Read the cached risk-state row for a mode, or None if absent."""
    con = get_connection()
    con.execute(RISK_STATE_DDL)
    row = con.execute(
        "SELECT mode, as_of_date, peak_equity, current_equity, daily_starting_equity, "
        "daily_pnl, consecutive_losses, trades_today, is_halted, halt_reason "
        "FROM algo_risk_state WHERE mode = ?",
        [mode],
    ).fetchone()
    con.close()
    if not row:
        return None
    keys = ["mode", "as_of_date", "peak_equity", "current_equity", "daily_starting_equity",
            "daily_pnl", "consecutive_losses", "trades_today", "is_halted", "halt_reason"]
    return dict(zip(keys, row))


def write_risk_cache(mode: str, as_of_date: str, state) -> None:
    """Upsert the cached risk-state row for a mode."""
    con = get_connection()
    con.execute(RISK_STATE_DDL)
    con.execute(
        "INSERT OR REPLACE INTO algo_risk_state (mode, as_of_date, peak_equity, "
        "current_equity, daily_starting_equity, daily_pnl, consecutive_losses, "
        "trades_today, is_halted, halt_reason, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())",
        [mode, as_of_date, state.peak_equity, state.current_equity,
         state.daily_starting_equity, state.daily_pnl, state.consecutive_losses,
         state.trades_today, state.is_halted, state.halt_reason],
    )
    con.close()


def migrate_tables():
    """
    Idempotently add the v2 columns to an already-existing algo_trade_log and
    flip the strategy default to 'orb_v2'. Safe to call repeatedly — uses
    ADD COLUMN IF NOT EXISTS. This is what the ALTER-in-MotherDuck step runs.
    """
    con = get_connection()
    con.execute("ALTER TABLE algo_trade_log ADD COLUMN IF NOT EXISTS vwap_at_entry DOUBLE;")
    con.execute("ALTER TABLE algo_trade_log ADD COLUMN IF NOT EXISTS rvol_at_entry DOUBLE;")
    con.execute("ALTER TABLE algo_trade_log ADD COLUMN IF NOT EXISTS candle_strength DOUBLE;")
    con.execute("ALTER TABLE algo_trade_log ADD COLUMN IF NOT EXISTS filters_passed VARCHAR;")
    con.execute("ALTER TABLE algo_trade_log ALTER COLUMN strategy SET DEFAULT 'orb_v2';")
    con.close()


def log_trade(
    trade_date: str,
    ticker: str,
    direction: str,
    entry_price: float,
    stop_price: float,
    target_price: float,
    shares: int,
    entry_time: str,
    exit_price: float | None = None,
    pnl_per_share: float | None = None,
    trade_pnl: float | None = None,
    exit_reason: str = "open",
    exit_time: str | None = None,
    or_high: float | None = None,
    or_low: float | None = None,
    range_pct: float | None = None,
    atr: float | None = None,
    equity_before: float | None = None,
    equity_after: float | None = None,
    vwap_at_entry: float | None = None,
    rvol_at_entry: float | None = None,
    candle_strength: float | None = None,
    filters_passed: str | None = None,
    strategy: str = "orb_v2",
    mode: str = "paper",
):
    """
    Log a single trade to MotherDuck.

    Designed to be called at ENTRY time (live path): exit_* fields default to
    None / 'open' since the bracket order resolves server-side later. The
    v2 confirmation-filter telemetry (vwap/rvol/candle_strength/filters_passed)
    is recorded for post-hoc analysis.
    """
    con = get_connection()
    con.execute("""
        INSERT INTO algo_trade_log (
            trade_date, ticker, direction, entry_price, stop_price,
            target_price, exit_price, shares, pnl_per_share, trade_pnl,
            exit_reason, entry_time, exit_time, or_high, or_low,
            range_pct, atr, equity_before, equity_after,
            vwap_at_entry, rvol_at_entry, candle_strength, filters_passed,
            strategy, mode
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        trade_date, ticker, direction, entry_price, stop_price,
        target_price, exit_price, shares, pnl_per_share, trade_pnl,
        exit_reason, entry_time, exit_time, or_high, or_low,
        range_pct, atr, equity_before, equity_after,
        vwap_at_entry, rvol_at_entry, candle_strength, filters_passed,
        strategy, mode,
    ])
    con.close()


def get_open_trades(trade_date: str, ticker: str, mode: str) -> list[dict]:
    """Open (unreconciled) trade rows for a day, oldest first."""
    con = get_connection()
    rows = con.execute(
        "SELECT trade_id, entry_price, shares, direction, equity_before, entry_time "
        "FROM algo_trade_log "
        "WHERE trade_date = ? AND ticker = ? AND mode = ? AND exit_reason = 'open' "
        "ORDER BY entry_time",
        [trade_date, ticker, mode],
    ).fetchall()
    con.close()
    keys = ["trade_id", "entry_price", "shares", "direction", "equity_before", "entry_time"]
    return [dict(zip(keys, r)) for r in rows]


def update_trade_exit(trade_id, exit_price, exit_time, exit_reason,
                      pnl_per_share, trade_pnl, equity_after, entry_price=None) -> None:
    """Write the realized exit onto a previously-open trade row. Also corrects
    entry_price to the actual entry fill when provided (COALESCE keeps the
    existing value if entry_price is None)."""
    con = get_connection()
    con.execute(
        "UPDATE algo_trade_log SET entry_price = COALESCE(?, entry_price), exit_price = ?, "
        "exit_time = ?, exit_reason = ?, pnl_per_share = ?, trade_pnl = ?, equity_after = ? "
        "WHERE trade_id = ?",
        [entry_price, exit_price, exit_time, exit_reason, pnl_per_share, trade_pnl, equity_after, trade_id],
    )
    con.close()


def log_daily_summary(
    summary_date: str,
    ticker: str,
    trades_taken: int,
    wins: int,
    losses: int,
    daily_pnl: float,
    equity_start: float,
    equity_end: float,
    max_drawdown_pct: float,
    consecutive_losses: int,
    was_halted: bool = False,
    halt_reason: str | None = None,
    mode: str = "paper",
    strategy: str = "orb_v2",
):
    """Log end-of-day summary to MotherDuck."""
    con = get_connection()
    con.execute("""
        INSERT OR REPLACE INTO algo_daily_summary (
            summary_date, ticker, trades_taken, wins, losses, daily_pnl,
            equity_start, equity_end, max_drawdown_pct, consecutive_losses,
            was_halted, halt_reason, strategy, mode
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        summary_date, ticker, trades_taken, wins, losses, daily_pnl,
        equity_start, equity_end, max_drawdown_pct, consecutive_losses,
        was_halted, halt_reason, strategy, mode,
    ])
    con.close()


def get_recent_performance(days: int = 30) -> dict:
    """Pull recent performance summary for dashboard/alerts."""
    con = get_connection()
    result = con.execute(f"""
        SELECT
            count(*) as total_days,
            sum(trades_taken) as total_trades,
            sum(wins) as total_wins,
            sum(losses) as total_losses,
            sum(daily_pnl) as total_pnl,
            min(equity_end) as min_equity,
            max(equity_end) as max_equity,
            max(max_drawdown_pct) as worst_drawdown,
            avg(daily_pnl) as avg_daily_pnl
        FROM algo_daily_summary
        WHERE summary_date >= current_date - INTERVAL '{days} days'
    """).fetchone()
    con.close()
    if result:
        return {
            "total_days": result[0],
            "total_trades": result[1],
            "total_wins": result[2],
            "total_losses": result[3],
            "total_pnl": result[4],
            "min_equity": result[5],
            "max_equity": result[6],
            "worst_drawdown": result[7],
            "avg_daily_pnl": result[8],
        }
    return {}


# ─── Per-run heartbeat (observability) ───────────────────────────────
def log_run(step: str, outcome: str, et_hhmm: str = "",
            detail: str = "", mode: str | None = None,
            run_date: str | None = None) -> None:
    """Write a durable heartbeat row for a single entrypoint run. This is what
    makes 'ran but no signal' distinguishable from 'never ran' — the health
    check asserts a row exists for (today, step)."""
    import pytz
    mode = mode or ("paper" if settings.ALPACA_PAPER else "live")
    run_date = run_date or datetime.now(pytz.timezone("US/Eastern")).date().isoformat()
    con = get_connection()
    con.execute(RUN_LOG_DDL)
    con.execute(
        "INSERT INTO algo_run_log (run_date, step, et_hhmm, outcome, detail, mode) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [run_date, step, et_hhmm, outcome, detail, mode],
    )
    con.close()


def get_run_today(step: str, mode: str, run_date: str) -> dict | None:
    """Most recent run-log row for (run_date, step, mode), or None. The
    health-check watchdog reads this to prove the step actually ran."""
    con = get_connection()
    con.execute(RUN_LOG_DDL)
    row = con.execute(
        "SELECT run_date, step, et_hhmm, outcome, detail, ran_at FROM algo_run_log "
        "WHERE run_date = ? AND step = ? AND mode = ? ORDER BY ran_at DESC LIMIT 1",
        [run_date, step, mode],
    ).fetchone()
    con.close()
    if not row:
        return None
    keys = ["run_date", "step", "et_hhmm", "outcome", "detail", "ran_at"]
    return dict(zip(keys, row))


# ─── Reporting helpers (truthful per-trade summary) ──────────────────
def get_daily_trade_stats(trade_date: str, mode: str) -> dict:
    """Per-trade counts from RECONCILED round trips (the real track record).
    Excludes still-open rows and smoke-test rows. This is the source of truth
    for the daily summary's trades/wins/losses — never Alpaca order legs."""
    con = get_connection()
    row = con.execute(
        "SELECT count(*), "
        "count(*) FILTER (WHERE trade_pnl > 0), "
        "count(*) FILTER (WHERE trade_pnl < 0), "
        "COALESCE(sum(trade_pnl), 0) "
        "FROM algo_trade_log "
        "WHERE trade_date = ? AND mode = ? AND exit_reason <> 'open' "
        "AND COALESCE(strategy, '') <> 'smoke_test'",
        [trade_date, mode],
    ).fetchone()
    con.close()
    return {"trades": row[0] or 0, "wins": row[1] or 0,
            "losses": row[2] or 0, "realized_pnl": float(row[3] or 0.0)}


def get_prior_equity_end(summary_date: str, mode: str) -> float | None:
    """equity_end of the most recent summary strictly before summary_date — the
    correct daily-P&L baseline, independent of whether pre_market ran."""
    con = get_connection()
    row = con.execute(
        "SELECT equity_end FROM algo_daily_summary "
        "WHERE mode = ? AND summary_date < ? ORDER BY summary_date DESC LIMIT 1",
        [mode, summary_date],
    ).fetchone()
    con.close()
    return float(row[0]) if row and row[0] is not None else None


def get_todays_trades(trade_date: str, mode: str) -> list[dict]:
    """Reconciled round trips for a day, for the daily email/report. Reflects
    actual entries/exits/P&L, not just positions force-closed at 15:45."""
    con = get_connection()
    rows = con.execute(
        "SELECT ticker, direction, entry_price, exit_price, shares, trade_pnl, exit_reason "
        "FROM algo_trade_log WHERE trade_date = ? AND mode = ? AND exit_reason <> 'open' "
        "AND COALESCE(strategy, '') <> 'smoke_test' ORDER BY entry_time",
        [trade_date, mode],
    ).fetchall()
    con.close()
    keys = ["ticker", "direction", "entry_price", "exit_price", "shares", "trade_pnl", "exit_reason"]
    return [dict(zip(keys, r)) for r in rows]

# Rootwork Algo Trader — Claude Code guide

Automated intraday **Opening Range Breakout (ORB)** trading system.
Runs unattended on GitHub Actions cron, trades via Alpaca, logs to MotherDuck.

## Status
- **Paper trading only** right now (`ALPACA_PAPER='true'` in the workflow and `.env`). Do not flip to live without a deliberate review.
- Active config (per `config/settings.py`): SPY / 5-min ORB / ATR 1.5× stop / 0.3% min range / 2:1 R:R.

## Architecture
```
GitHub Actions cron (UTC times in workflow; ET below)
  09:25 ET  pre_market.py   → equity, ATR, reset daily risk state, notify
  09:40 ET  execute_orb.py  → fetch bars, detect breakout, risk checks, submit bracket order
  15:45 ET  end_of_day.py   → force-close, compute P&L, log to MotherDuck, email/Slack summary
```
Each cron time maps to a separate job in `.github/workflows/trading_schedule.yml`, gated by `github.event.schedule`. `workflow_dispatch` lets you run any single script manually (incl. `backtest`, `param_sweep`).

## Layout
- `config/settings.py` — every tunable param; reads from env with defaults. **Change config here, not in strategy code.**
- `src/orb_signal.py` — pure signal logic (no I/O): `compute_opening_range`, `generate_signal`, `simulate_trade`, `calculate_atr`. Test this offline with synthetic bars.
- `src/risk_manager.py` — `RiskState` dataclass + pre-trade checks (`can_trade`), `calculate_position_size`, post-trade updates, and `simulate_risk_controls` (backtest path). State persists to `config/risk_state.json`.
- `src/alpaca_client.py` — all Alpaca I/O (data fetch + order submission). Bracket orders are server-side (TP/SL), so the bot need not stay alive.
- `src/execute_orb.py` / `pre_market.py` / `end_of_day.py` — the three scheduled entrypoints.
- `src/backtest.py` — `python -m src.backtest --ticker SPY --start 2024-01-01 --end 2026-06-01`.
- `src/param_sweep.py` — grid over tickers × OR windows × stop modes × range filters.
- `src/trade_logger.py` / `src/notifications.py` — MotherDuck (`my_db`: `algo_trade_log`, `algo_daily_summary`) + Slack/Gmail.

## Run locally
```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt   # Windows
cp .env.example .env   # then fill ALPACA_* + MOTHERDUCK_TOKEN
.venv/Scripts/python -m src.backtest --ticker SPY --start 2024-01-01 --end 2026-06-01
```
Data fetches (backtest included) require valid Alpaca API keys. Pure-logic modules (`orb_signal`, `risk_manager`) run with no network.

## Conventions
- All times in code are **US/Eastern**; the workflow cron is **UTC** (e.g. `35 13` = 09:35 ET during EDT — note this does not auto-adjust for DST).
- Secrets come from env only. Never hardcode; never commit `.env` or `config/risk_state.json` (see `.gitignore`).
- MotherDuck DB is the shared `my_db` used by the rest of the Rootwork platform.

## ⚠️ Known caveats (read before relying on risk controls)
1. **(RESOLVED 2026-06-29) Risk state now persists via MotherDuck.** `load_risk_state` derives the cross-day quantities (peak equity, consecutive losing days) from `algo_daily_summary` and caches the full state in `algo_risk_state`, so the drawdown, consecutive-loss, and daily-loss halts now fire across the ephemeral GitHub Actions runners. If MotherDuck is unreachable it **fails closed** (halts for the day, reason `risk_state_unavailable`). The old local `config/risk_state.json` path is gone.
2. **(RESOLVED 2026-06-29) Consecutive-loss deadlock is gone.** Consecutive losses are derived as the trailing run of losing *days*, so a flat or winning day resets the streak. A consecutive-loss halt is now a one-day cooldown, not a permanent lock.
3. **(RESOLVED 2026-06-29) Per-trade outcomes are now reconciled.** `end_of_day.py` calls `src/reconcile.py`, which pulls the day's Alpaca fills, pairs them into round trips, and writes the realized exit (price, P&L, reason) onto each open `algo_trade_log` row. Entries are still logged at fill time as `exit_reason='open'` and resolved at EOD once the day is flat. Risk derivation stays day-based by design (from `algo_daily_summary`), but per-trade analytics (leak-finder, drift check) now have resolved data once the bot starts trading.
4. **(RESOLVED 2026-06-29) DST handled.** Both EDT and EST cron offsets are scheduled and `src/timeguard.py` gates each scheduled run to its intended ET window, so only the correctly-timed run acts and the wrong-season cron no-ops. The bot trades year-round. `execute_orb` was also moved to 09:40 ET (a few minutes after the 5-min opening range closes) so a breakout bar exists when it looks — at 09:35 it fired the instant the range closed, before any breakout could form, which likely contributed to it rarely trading.

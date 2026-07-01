# Operations Runbook — Rootwork Algo Trader

How to get the live (paper) bot actually running, and how to diagnose it when it
isn't. Written 2026-06-29 after finding the bot had logged nothing.

## TL;DR — why it wasn't trading

`algo_trade_log` and `algo_daily_summary` are empty and the `algo_risk_state`
table (created on every run of the current code) does not exist. That means the
scheduled GitHub Actions jobs have not been successfully running the current
code. It is almost always one of:

1. **Repo Actions secrets are not set** — every run then crashes at the first
   Alpaca call before logging anything. (Keys in your local `.env` do NOT carry
   to GitHub.)
2. **Actions/schedules aren't active** — disabled, or paused after repo
   inactivity.

Fix the secrets, confirm Actions is on, then smoke-test with a manual run.

## 1. Required GitHub Actions secrets

Set these at: **repo → Settings → Secrets and variables → Actions → New
repository secret**. Names are case-sensitive and must match exactly.

| Secret | What it is | Required? | Where to get it |
|---|---|---|---|
| `ALPACA_API_KEY_ID` | Alpaca **paper** API key id (looks like `PK...`) | **Yes** | Alpaca dashboard → Paper Trading → API Keys → Generate |
| `ALPACA_API_SECRET_KEY` | Alpaca **paper** secret (shown once at generation) | **Yes** | same screen as above |
| `MOTHERDUCK_TOKEN` | MotherDuck access token (logging to `my_db`) | **Yes** for logging | MotherDuck → Settings → Access Tokens |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook | optional (falls back to stdout) | Slack app → Incoming Webhooks |
| `GMAIL_ADDRESS` | Gmail address for the daily email | optional | your Gmail |
| `GMAIL_APP_PASSWORD` | Gmail **App Password** (not your account password) | optional | Google Account → Security → App Passwords |

Notes:
- The bot is paper-only (`ALPACA_PAPER='true'` is set in the workflow, not a secret). No real money.
- If you generated paper keys for your local `.env` this session, use those SAME
  values as the repo secrets — or regenerate and update both (regenerating
  invalidates the old pair).

## 2. Confirm Actions is enabled

- Repo → **Actions** tab. If prompted to enable workflows, enable them.
- Scheduled workflows run only from the default branch (`main`) — they do.
- GitHub pauses schedules after ~60 days of repo inactivity; recent pushes reset that.

## 3. Smoke-test (during market hours, 09:30–16:00 ET)

1. Actions → **Trading Schedule** → **Run workflow** → pick `execute_orb` → Run.
   (A manual run bypasses the ET time gate, so it runs immediately.)
2. Read the run log:
   - `[SPY] LONG ... order ... logged` → it placed a paper trade. Working.
   - `no ORB signal` → healthy; just no qualifying breakout right now.
   - `You must supply a method of authentication` / `no token` → a required
     secret is missing or misnamed (see §1).
3. Then run `end_of_day` once to confirm it force-closes, reconciles, and writes
   a daily summary (this also creates `algo_risk_state`).

## 4. Verify it worked (MotherDuck `my_db`)

```sql
SELECT * FROM algo_trade_log     ORDER BY created_at DESC LIMIT 5;
SELECT * FROM algo_daily_summary ORDER BY summary_date DESC LIMIT 5;
SELECT * FROM algo_risk_state;   -- should exist after any successful run
```

## 5. Daily schedule (ET)

| Time (ET) | Job | Does |
|---|---|---|
| 09:25 | `pre_market` | reset daily risk state, notify |
| 09:40 | `execute_orb` | detect breakout, place bracket order(s) for SPY + QQQ |
| 10:00–15:35, every 30 min | `risk_monitor` | daily-loss bumper: warn at 2.25%, flatten + halt at 3% (see §5b) |
| 15:45 | `end_of_day` | force-close, reconcile per-trade outcomes, log daily summary |

Both EDT and EST cron offsets are scheduled; `src/timeguard.py` makes only the
correctly-timed run act, so the bot is DST-proof. It trades **once** in the
morning — it is not a continuous ticker, so mid-day quiet is normal.

## 5b. Daily-loss bumpers and the manual kill switch

Two layers cap how much a single day can hurt, on top of the per-trade bracket
stops (each trade risks ~1.5%, capped, no leverage).

**Automatic daily-loss bumper** (`src/risk_monitor.py`, runs every 30 min,
ET-guarded to 10:00–15:35):
- At **2.25%** down on the day: emails an early warning. No action taken.
- At **3%** down (the hard stop): cancels open orders, **flattens all
  positions**, halts for the day, and emails you. Trading **resumes
  automatically the next day**.
- At **50%** down (the catastrophic floor, `ALGO_DAILY_FLOOR`): same flatten,
  but a **STICKY halt that does NOT auto-resume**. It requires a manual
  `resume_trading` after review. In normal operation the 3% stop fires long
  before this; the floor is the last-resort backstop (fast gap, failed stop,
  monitor gap). Base is the day's starting equity; when live with a set per-day
  trade amount, we point it at that amount.
- Thresholds live in `ALGO_MAX_DAILY_LOSS` (0.03), `ALGO_DAILY_LOSS_WARN`
  (0.0225), and `ALGO_DAILY_FLOOR` (0.50), set in the workflow env and `.env`.
- Fail-safe: if it cannot establish equity or the day's baseline, it does NOT
  flatten; it alerts instead.
- To test the floor without a 50% loss, temporarily set `ALGO_DAILY_FLOOR` low
  (e.g. `0.01`) and run `risk_monitor` while down a little.

**Manual kill switch** (`src/killswitch.py`) — the on-demand human stop:
- To stop everything now and keep it stopped: Actions → **Trading Schedule** →
  **Run workflow** → `halt_now`. This is sticky; it does NOT auto-clear.
- To turn trading back on: same menu → `resume_trading`.
- The halt email contains the direct link back to this workflow.
- A manual halt only clears via `resume_trading`. It does not override a
  daily-loss or drawdown halt (those follow their own schedule).

Note: production risk runs on GitHub Actions, which uses the workflow env values
above (not your local `.env`). Keep the two in sync if you change a threshold.

## 6. Failure → fix

| Symptom | Cause | Fix |
|---|---|---|
| `You must supply a method of authentication` | Alpaca secrets missing | set `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` |
| `401 Unauthorized` on paper-api | live keys used against the paper endpoint | use **paper** keys (start with `PK`), not live (`AK`) |
| `403 ... subscription does not permit querying recent SIP data` | free data plan blocks recent SIP | live path uses **IEX** (`ALPACA_DATA_FEED=iex`, the default). Only set `sip` with a paid data plan. |
| MotherDuck "no token" / logging error | token missing | set `MOTHERDUCK_TOKEN` |
| Every scheduled run is red | crash early — open the failing step's log | match the error here |
| No runs at all in Actions | Actions disabled / schedules paused | enable in the Actions tab |
| Runs green but always "no signal" | no breakout, or free-tier data delay (IEX ~15 min) at 09:40 | confirm `pre_market` ran and bars were fetched; consider a slightly later execute time if data lags |
| Ran an hour early in winter | (was) DST drift | already fixed (DST-proof) |

## 7. Already verified (not the problem)

- Secret **names** in the workflow match what `config/settings.py` reads.
- `ALPACA_PAPER='true'` (paper money).
- Code runs clean locally (imports, sizing, logic, offline tests).
- The only unknowns are repo-side: whether the **secrets are set** and **Actions
  is firing** — both visible only in the GitHub UI.

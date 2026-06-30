"""
Rootwork Algo Trader — Configuration
All tunable parameters in one place. Modify here, not in strategy code.

ACTIVE CONFIG: SPY / 5m ORB / ATR 1.5x stop / 0.3% min range / 2:1 R:R
              + candle-strength filter (top 50%); VWAP/RVOL off (see below).
Backtest 2024-2026, net of costs, faithful 15:45 force-close:
  SPY  net Sharpe 2.67 / +5.4% (46 trades)
  QQQ  net Sharpe 3.82 / +27.4% (166 trades; validated, not yet live-traded)
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Alpaca Credentials ───────────────────────────────────────────────
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY_ID", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_API_SECRET_KEY", "")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"
ALPACA_BASE_URL = (
    "https://paper-api.alpaca.markets" if ALPACA_PAPER
    else "https://api.alpaca.markets"
)

# Market-data feed for the LIVE path. Free Alpaca data plans cannot query recent
# SIP data (403 "subscription does not permit querying recent SIP data"), but DO
# get real-time IEX. So live fetches default to IEX. Backtests use historical SIP
# (allowed when older than 15 min, and more complete). Set 'sip' if you upgrade.
ALPACA_DATA_FEED = os.getenv("ALPACA_DATA_FEED", "iex")

# ─── MotherDuck ───────────────────────────────────────────────────────
MOTHERDUCK_TOKEN = os.getenv("MOTHERDUCK_TOKEN", "")
MOTHERDUCK_DB = "my_db"

# ─── Notifications ───────────────────────────────────────────────────
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# ─── Strategy: ORB Parameters ────────────────────────────────────────
TICKER = os.getenv("ALGO_TICKER", "SPY")  # single-symbol default (backtest/analysis)
# Live trading set. Validated: SPY + QQQ (IWM excluded — the edge fails on
# small-caps). The live entrypoints loop over these; backtest/analysis tools
# still take a single --ticker.
TICKERS = [t.strip() for t in os.getenv("ALGO_TICKERS", "SPY,QQQ").split(",") if t.strip()]

# Dry run: when true, execute_orb runs the full path (auth, data fetch, signal,
# sizing) but places NO order and logs nothing. For safe live pressure-testing.
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# Entry freshness window (ET). execute_orb only takes entries when the RUN fires
# within this window. Coarse backstop against a late/stale catch-up run.
# DRY_RUN and FORCE_ENTRY override.
ENTRY_WINDOW_START = os.getenv("ALGO_ENTRY_START", "09:36")
ENTRY_WINDOW_END = os.getenv("ALGO_ENTRY_END", "09:46")
FORCE_ENTRY = os.getenv("FORCE_ENTRY", "false").lower() == "true"

# Bar-freshness guards (minutes). The REAL freshness check: only enter if the
# breakout (signal) bar is recent and the latest available bar is current. This
# is immune to WHY a run is late and to data-feed lag, whereas the wall-clock
# window above is only a coarse backstop. A late run or a stale/lagged data
# frame is skipped rather than chased. FORCE_ENTRY/DRY_RUN bypass.
SIGNAL_MAX_AGE_MIN = float(os.getenv("ALGO_SIGNAL_MAX_AGE_MIN", "6"))
DATA_MAX_AGE_MIN = float(os.getenv("ALGO_DATA_MAX_AGE_MIN", "5"))
OPENING_RANGE_MINUTES = int(os.getenv("ALGO_ORB_MINUTES", "5"))
REWARD_RISK_RATIO = float(os.getenv("ALGO_RR_RATIO", "2.0"))
STOP_MODE = os.getenv("ALGO_STOP_MODE", "atr")
ATR_PERIOD = int(os.getenv("ALGO_ATR_PERIOD", "14"))
ATR_STOP_MULTIPLIER = float(os.getenv("ALGO_ATR_STOP_MULT", "1.5"))

# Minimum opening range width as % of price — skip if too narrow
MIN_RANGE_PCT = float(os.getenv("ALGO_MIN_RANGE_PCT", "0.003"))  # 0.3%

# ─── Signal Confirmation Filters (v2) ─────────────────────────────────
# Each filter is evaluated on the FIRST breakout bar and independently
# toggleable. With all three disabled, generate_signal() reproduces v1
# behavior exactly. See src/orb_signal.py for the gating logic.
#
# Walk-forward verdict (2026-06-29, all net of costs; see src/walkforward.py):
#   VWAP   - inert on SPY (the breakout level is essentially always on the
#            right side of VWAP, so it never gates). Left OFF.
#   RVOL   - toxic: requiring high relative volume on the first breakout bar
#            cut 60 trades to ~3 with a negative Sharpe. Left OFF.
#   CANDLE - helps and holds up OUT-OF-SAMPLE (holdout Sharpe 3.9 -> 5.7,
#            win 68% -> 79%, same return on fewer, cleaner trades).
#            Enabled at top-50%.
# The ORB params themselves are kept FIXED: a rolling optimizer overfit
# hard (mean in-sample-best Sharpe 3.5 vs out-of-sample 0.1).

# Filter 1 — VWAP directional filter: long requires breakout level above
# session VWAP at breakout; short requires below.
FILTER_VWAP_ENABLED = os.getenv("FILTER_VWAP_ENABLED", "false").lower() == "true"

# Filter 2 — Relative volume: breakout-bar volume vs mean of prior N bars.
FILTER_RVOL_ENABLED = os.getenv("FILTER_RVOL_ENABLED", "false").lower() == "true"
FILTER_RVOL_THRESHOLD = float(os.getenv("FILTER_RVOL_THRESHOLD", "1.5"))
FILTER_RVOL_LOOKBACK = int(os.getenv("FILTER_RVOL_LOOKBACK", "20"))  # prior bars

# Filter 3 — Candle strength: where the breakout bar closes within its range.
# long requires close in the top FILTER_CANDLE_STRENGTH_PCT of the bar;
# short requires close in the bottom FILTER_CANDLE_STRENGTH_PCT.
FILTER_CANDLE_STRENGTH_ENABLED = os.getenv("FILTER_CANDLE_STRENGTH_ENABLED", "true").lower() == "true"
FILTER_CANDLE_STRENGTH_PCT = float(os.getenv("FILTER_CANDLE_STRENGTH_PCT", "0.5"))

# ─── Risk Management ─────────────────────────────────────────────────
RISK_PER_TRADE_PCT = float(os.getenv("ALGO_RISK_PER_TRADE", "0.015"))  # 1.5%
MAX_DAILY_LOSS_PCT = float(os.getenv("ALGO_MAX_DAILY_LOSS", "0.04"))   # 4%
MAX_CONSECUTIVE_LOSSES = int(os.getenv("ALGO_MAX_CONSEC_LOSSES", "3"))
MAX_DRAWDOWN_PCT = float(os.getenv("ALGO_MAX_DRAWDOWN", "0.12"))       # 12%
MAX_TRADES_PER_DAY = int(os.getenv("ALGO_MAX_TRADES_DAY", "2"))

# ─── Schedule (ET) ───────────────────────────────────────────────────
MARKET_OPEN = "09:30"
ORB_SIGNAL_TIME = "09:35"
FORCE_CLOSE_TIME = "15:45"
MARKET_CLOSE = "16:00"

# ─── Backtest Defaults ───────────────────────────────────────────────
BACKTEST_START = os.getenv("ALGO_BT_START", "2024-01-01")
BACKTEST_END = os.getenv("ALGO_BT_END", "2026-06-01")
BACKTEST_INITIAL_CAPITAL = float(os.getenv("ALGO_BT_CAPITAL", "10000"))

# ─── Transaction Costs (backtest realism) ─────────────────────────────
# A round trip is two fills (entry + exit). Each leg pays slippage plus
# half the bid/ask spread, in basis points of price, plus a flat
# per-share commission on both legs. This is what separates a backtest
# Sharpe from a tradeable one. See src/costs.py.
#
# Backward compatibility: set ALGO_BT_COSTS=false (or all three params to
# 0) and net equals gross, reproducing the cost-free v1 results exactly.
#
# Defaults are conservative for a liquid ETF like SPY: 1 bp slippage per
# leg, 1 bp full spread (0.5 bp per leg), commission free (Alpaca
# equities). That is roughly 3 bps round trip. Crank these up to stress
# test how much cost the edge can absorb before it disappears.
BACKTEST_COSTS_ENABLED = os.getenv("ALGO_BT_COSTS", "true").lower() == "true"
BACKTEST_SLIPPAGE_BPS = float(os.getenv("ALGO_BT_SLIPPAGE_BPS", "1.0"))  # per leg
BACKTEST_SPREAD_BPS = float(os.getenv("ALGO_BT_SPREAD_BPS", "1.0"))      # full spread; half paid per leg
BACKTEST_COMMISSION_PER_SHARE = float(os.getenv("ALGO_BT_COMMISSION", "0.0"))  # per leg

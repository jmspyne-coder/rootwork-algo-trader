"""
Rootwork Algo Trader — Configuration
All tunable parameters in one place. Modify here, not in strategy code.

ACTIVE CONFIG: SPY / 5m ORB / ATR 1.5x stop / 0.3% min range / 2:1 R:R
Backtest results (2024-2026): 60 trades, 65% win rate, Sharpe 2.58, 2.9% MDD
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

# ─── MotherDuck ───────────────────────────────────────────────────────
MOTHERDUCK_TOKEN = os.getenv("MOTHERDUCK_TOKEN", "")
MOTHERDUCK_DB = "my_db"

# ─── Notifications ───────────────────────────────────────────────────
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# ─── Strategy: ORB Parameters ────────────────────────────────────────
TICKER = os.getenv("ALGO_TICKER", "SPY")
OPENING_RANGE_MINUTES = int(os.getenv("ALGO_ORB_MINUTES", "5"))
REWARD_RISK_RATIO = float(os.getenv("ALGO_RR_RATIO", "2.0"))
STOP_MODE = os.getenv("ALGO_STOP_MODE", "atr")
ATR_PERIOD = int(os.getenv("ALGO_ATR_PERIOD", "14"))
ATR_STOP_MULTIPLIER = float(os.getenv("ALGO_ATR_STOP_MULT", "1.5"))

# Minimum opening range width as % of price — skip if too narrow
MIN_RANGE_PCT = float(os.getenv("ALGO_MIN_RANGE_PCT", "0.003"))  # 0.3%

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
BACKTEST_COMMISSION_PER_SHARE = 0.0

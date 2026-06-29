"""
Alpaca data and trading client wrapper.
Handles all broker communication — data fetches, order submission, account queries.
"""
import pandas as pd
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    GetOrdersRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderType, QueryOrderStatus
from config import settings


def get_data_client() -> StockHistoricalDataClient:
    """Authenticated data client (needed for SIP data on free tier)."""
    return StockHistoricalDataClient(
        api_key=settings.ALPACA_API_KEY,
        secret_key=settings.ALPACA_SECRET_KEY,
    )


def get_trading_client() -> TradingClient:
    return TradingClient(
        api_key=settings.ALPACA_API_KEY,
        secret_key=settings.ALPACA_SECRET_KEY,
        paper=settings.ALPACA_PAPER,
    )


# ─── Data Fetching ───────────────────────────────────────────────────

def fetch_intraday_bars(
    ticker: str,
    date: datetime,
    timeframe: TimeFrame = TimeFrame.Minute,
    data_client: StockHistoricalDataClient | None = None,
) -> pd.DataFrame:
    """Fetch 1-min bars for a single trading day."""
    client = data_client or get_data_client()
    start = datetime.combine(date, datetime.min.time())
    end = start + timedelta(days=1)
    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=timeframe,
        start=start,
        end=end,
    )
    bars = client.get_stock_bars(request)
    df = bars.df
    if isinstance(df.index, pd.MultiIndex):
        df = df.droplevel("symbol")
    df.index = pd.to_datetime(df.index).tz_convert("US/Eastern")
    return df


def fetch_daily_bars(
    ticker: str,
    start: str,
    end: str,
    data_client: StockHistoricalDataClient | None = None,
) -> pd.DataFrame:
    """Fetch daily bars for ATR calculation and backtesting."""
    client = data_client or get_data_client()
    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=TimeFrame.Day,
        start=datetime.fromisoformat(start),
        end=datetime.fromisoformat(end),
    )
    bars = client.get_stock_bars(request)
    df = bars.df
    if isinstance(df.index, pd.MultiIndex):
        df = df.droplevel("symbol")
    return df


def fetch_multi_day_intraday(
    ticker: str,
    start: str,
    end: str,
    data_client: StockHistoricalDataClient | None = None,
) -> pd.DataFrame:
    """Fetch 1-min bars across a date range for backtesting."""
    client = data_client or get_data_client()
    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=TimeFrame.Minute,
        start=datetime.fromisoformat(start),
        end=datetime.fromisoformat(end),
    )
    bars = client.get_stock_bars(request)
    df = bars.df
    if isinstance(df.index, pd.MultiIndex):
        df = df.droplevel("symbol")
    df.index = pd.to_datetime(df.index).tz_convert("US/Eastern")
    return df


# ─── Account & Orders ────────────────────────────────────────────────

def get_account_equity(trading_client: TradingClient | None = None) -> float:
    client = trading_client or get_trading_client()
    account = client.get_account()
    return float(account.equity)


def get_buying_power(trading_client: TradingClient | None = None) -> float:
    client = trading_client or get_trading_client()
    account = client.get_account()
    return float(account.buying_power)


def get_open_positions(trading_client: TradingClient | None = None) -> list:
    client = trading_client or get_trading_client()
    return client.get_all_positions()


def close_all_positions(trading_client: TradingClient | None = None):
    """Force-close all open positions (EOD cleanup)."""
    client = trading_client or get_trading_client()
    client.close_all_positions(cancel_orders=True)


def get_todays_orders(trading_client: TradingClient | None = None) -> list:
    """Count filled orders today for max-trades-per-day check."""
    client = trading_client or get_trading_client()
    request = GetOrdersRequest(
        status=QueryOrderStatus.CLOSED,
        after=datetime.now().replace(hour=0, minute=0, second=0),
    )
    return client.get_orders(filter=request)


def get_todays_fills(ticker: str, trading_client: TradingClient | None = None) -> list[dict]:
    """Today's FILLED orders for a symbol, normalized and sorted oldest-first.

    Returns dicts {side, price, qty, time, type}. Used by reconciliation to
    pair entries with their realized exits (bracket leg or EOD force-close).
    """
    client = trading_client or get_trading_client()
    request = GetOrdersRequest(
        status=QueryOrderStatus.CLOSED,
        after=datetime.now().replace(hour=0, minute=0, second=0),
        symbols=[ticker],
    )
    fills = []
    for o in client.get_orders(filter=request):
        filled_qty = float(o.filled_qty) if getattr(o, "filled_qty", None) else 0.0
        if filled_qty <= 0 or not getattr(o, "filled_avg_price", None):
            continue
        fills.append({
            "side": o.side.value if hasattr(o.side, "value") else str(o.side),
            "price": float(o.filled_avg_price),
            "qty": filled_qty,
            "time": str(getattr(o, "filled_at", "")),
            "type": o.type.value if hasattr(o.type, "value") else str(o.type),
        })
    fills.sort(key=lambda f: f["time"])
    return fills


# ─── Order Submission ─────────────────────────────────────────────────

def submit_bracket_order(
    ticker: str,
    side: str,
    qty: int,
    take_profit_price: float,
    stop_loss_price: float,
    trading_client: TradingClient | None = None,
):
    """
    Submit a bracket order: market entry + limit take-profit + stop-loss.
    Alpaca handles TP/SL server-side — bot doesn't need to stay alive.
    """
    client = trading_client or get_trading_client()
    order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL

    order_data = MarketOrderRequest(
        symbol=ticker,
        qty=qty,
        side=order_side,
        time_in_force=TimeInForce.DAY,
        order_class="bracket",
        take_profit={"limit_price": round(take_profit_price, 2)},
        stop_loss={"stop_price": round(stop_loss_price, 2)},
    )
    return client.submit_order(order_data=order_data)


def submit_market_order(
    ticker: str,
    side: str,
    qty: int,
    trading_client: TradingClient | None = None,
):
    """Simple market order (for closing positions)."""
    client = trading_client or get_trading_client()
    order_data = MarketOrderRequest(
        symbol=ticker,
        qty=qty,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    return client.submit_order(order_data=order_data)

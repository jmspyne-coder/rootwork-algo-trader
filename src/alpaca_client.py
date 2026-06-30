"""
Alpaca data and trading client wrapper.
Handles all broker communication — data fetches, order submission, account queries.
"""
import pandas as pd
import pytz
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    GetOrdersRequest,
    GetCalendarRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderType, QueryOrderStatus
from config import settings


_DEAD_ORDER_STATES = {
    "canceled", "cancelled", "expired", "rejected", "replaced", "pending_cancel",
}


def _session_start_utc() -> datetime:
    """ET-midnight today as a tz-aware UTC datetime. Makes 'today' mean the ET
    calendar day for order/fill queries, instead of naive-local-as-UTC (which
    drifts the day boundary by the local offset and on a non-UTC box can pull
    the wrong session)."""
    et = pytz.timezone("US/Eastern")
    now_et = datetime.now(et)
    et_midnight = et.localize(datetime(now_et.year, now_et.month, now_et.day))
    return et_midnight.astimezone(pytz.utc)


def _status_str(o) -> str:
    st = getattr(o, "status", None)
    return (st.value if hasattr(st, "value") else str(st)).lower()


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


def _resolve_feed(feed: str | None) -> DataFeed:
    """Map a feed name to the DataFeed enum; default to the configured live feed."""
    name = (feed or settings.ALPACA_DATA_FEED or "iex").lower()
    return DataFeed.SIP if name == "sip" else DataFeed.IEX


# ─── Data Fetching ───────────────────────────────────────────────────

def fetch_intraday_bars(
    ticker: str,
    date: datetime,
    timeframe: TimeFrame = TimeFrame.Minute,
    data_client: StockHistoricalDataClient | None = None,
    feed: str | None = None,
) -> pd.DataFrame:
    """Fetch 1-min bars for a single trading day. Live path: defaults to IEX
    (free-tier real-time); recent SIP is forbidden on free plans."""
    client = data_client or get_data_client()
    start = datetime.combine(date, datetime.min.time())
    end = start + timedelta(days=1)
    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=timeframe,
        start=start,
        end=end,
        feed=_resolve_feed(feed),
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
    feed: str | None = "sip",
) -> pd.DataFrame:
    """Fetch daily bars for ATR and backtesting. Defaults to SIP (historical,
    complete); the live ATR fetch passes the live feed (IEX) for recent days."""
    client = data_client or get_data_client()
    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=TimeFrame.Day,
        start=datetime.fromisoformat(start),
        end=datetime.fromisoformat(end),
        feed=_resolve_feed(feed),
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
    feed: str | None = "sip",
) -> pd.DataFrame:
    """Fetch 1-min bars across a date range for backtesting. Defaults to SIP
    (historical, allowed when older than 15 min, and more complete than IEX)."""
    client = data_client or get_data_client()
    request = StockBarsRequest(
        symbol_or_symbols=ticker,
        timeframe=TimeFrame.Minute,
        start=datetime.fromisoformat(start),
        end=datetime.fromisoformat(end),
        feed=_resolve_feed(feed),
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


def cancel_all_orders(trading_client: TradingClient | None = None) -> list:
    """Cancel all open orders; returns the per-order cancel responses. Clears
    stray/leftover orders (e.g. an after-hours test order queued for the next
    open) before a session."""
    client = trading_client or get_trading_client()
    return client.cancel_orders()


def get_todays_orders(trading_client: TradingClient | None = None) -> list:
    """Today's closed orders (ET session). Legacy helper; prefer
    count_todays_orders for the trade cap and has_order_today for idempotency."""
    client = trading_client or get_trading_client()
    request = GetOrdersRequest(
        status=QueryOrderStatus.CLOSED,
        after=_session_start_utc(),
    )
    return client.get_orders(filter=request)


def has_order_today(ticker: str, trading_client: TradingClient | None = None) -> bool:
    """True if ANY non-cancelled order exists for the symbol today (filled,
    partially filled, or still working). The idempotency guard: prevents a
    re-run from double-entering when the first order is open/partial and not
    yet CLOSED (which a filled-only check would miss)."""
    client = trading_client or get_trading_client()
    request = GetOrdersRequest(
        status=QueryOrderStatus.ALL, after=_session_start_utc(),
        symbols=[ticker], nested=True,
    )
    for o in client.get_orders(filter=request):
        if _status_str(o) in _DEAD_ORDER_STATES:
            continue
        return True
    return False


def count_todays_orders(trading_client: TradingClient | None = None) -> int:
    """Count today's non-cancelled parent orders across all symbols — broker
    truth for the account-wide trade cap, robust to un-persisted run state and
    to two triggers firing. nested=True rolls bracket child legs under parents
    so each entry counts once."""
    client = trading_client or get_trading_client()
    request = GetOrdersRequest(
        status=QueryOrderStatus.ALL, after=_session_start_utc(), nested=True,
    )
    n = 0
    for o in client.get_orders(filter=request):
        if _status_str(o) in _DEAD_ORDER_STATES:
            continue
        n += 1
    return n


def get_market_session_today(trading_client: TradingClient | None = None) -> dict | None:
    """Today's market session per Alpaca's calendar, or None if the market is
    closed today (weekend/holiday). Used to gate trading so the bot never acts
    on a closed/holiday day off stale data. Includes a half-day flag."""
    client = trading_client or get_trading_client()
    today = datetime.now(pytz.timezone("US/Eastern")).date()
    cal = client.get_calendar(filters=GetCalendarRequest(start=today, end=today))
    for c in cal:
        if str(getattr(c, "date", ""))[:10] != str(today):
            continue
        close = getattr(c, "close", None)
        close_str = close.strftime("%H:%M") if hasattr(close, "strftime") else str(close)[:5]
        return {
            "date": str(today),
            "close": close_str,
            "is_half_day": close_str not in ("16:00",),
        }
    return None


def is_trading_day(trading_client: TradingClient | None = None) -> bool:
    return get_market_session_today(trading_client) is not None


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
    client_order_id: str | None = None,
):
    """
    Submit a bracket order: market entry + limit take-profit + stop-loss.
    Alpaca handles TP/SL server-side, so the bot does not need to stay alive.

    client_order_id makes the entry idempotent at the BROKER: Alpaca rejects a
    duplicate id, so a re-run (or a second trigger) cannot double-enter even if
    every local guard fails. Prices are quantized to a penny and the bracket
    geometry is validated before submit so a tiny-ATR bracket cannot invert and
    get rejected after we believe we are in.
    """
    client = trading_client or get_trading_client()
    order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL

    tp = float(Decimal(str(take_profit_price)).quantize(Decimal("0.01"), ROUND_HALF_UP))
    sl = float(Decimal(str(stop_loss_price)).quantize(Decimal("0.01"), ROUND_HALF_UP))
    if side == "buy" and not (sl < tp):
        raise ValueError(f"bracket geometry invalid (long): stop {sl} !< target {tp}")
    if side == "sell" and not (tp < sl):
        raise ValueError(f"bracket geometry invalid (short): target {tp} !< stop {sl}")

    kwargs = dict(
        symbol=ticker,
        qty=qty,
        side=order_side,
        time_in_force=TimeInForce.DAY,
        order_class="bracket",
        take_profit={"limit_price": tp},
        stop_loss={"stop_price": sl},
    )
    if client_order_id:
        kwargs["client_order_id"] = client_order_id
    return client.submit_order(order_data=MarketOrderRequest(**kwargs))


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

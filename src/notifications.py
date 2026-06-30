"""
Notification Module.

Sends trade alerts and daily summaries via:
  - Slack webhook (if configured)
  - Email via Gmail SMTP (if configured)
Falls back to stdout if neither is configured.
"""
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from config import settings


# ─── Email Config ─────────────────────────────────────────────────────
import os
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
EMAIL_TO = os.getenv("EMAIL_TO", GMAIL_ADDRESS)  # defaults to self


def send_email(subject: str, body_html: str):
    """Send email via Gmail SMTP. Requires app password, not regular password."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("  [EMAIL] Skipped — no Gmail credentials configured")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Rootwork Algo Trader <{GMAIL_ADDRESS}>"
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, EMAIL_TO, msg.as_string())
        print(f"  [EMAIL] Sent to {EMAIL_TO}")
    except Exception as e:
        print(f"  [EMAIL] Error: {e}")


def send_notification(message: str, emoji: str = ":chart_with_upwards_trend:"):
    """Send a Slack notification. Falls back to print if no webhook."""
    print(f"[NOTIFY] {message}")

    if not settings.SLACK_WEBHOOK_URL:
        return

    payload = {
        "text": f"{emoji} *Rootwork Algo Trader*\n{message}",
    }
    try:
        resp = requests.post(
            settings.SLACK_WEBHOOK_URL,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"  Slack error: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"  Slack exception: {e}")


def notify_trade_entry(ticker, direction, shares, entry, stop, target):
    msg = (
        f"*TRADE ENTRY* | `{ticker}` {direction.upper()}\n"
        f"Shares: {shares} | Entry: ${entry:.2f}\n"
        f"Stop: ${stop:.2f} | Target: ${target:.2f}"
    )
    emoji = ":rocket:" if direction == "long" else ":bear:"
    send_notification(msg, emoji)


def notify_trade_exit(ticker, direction, pnl, exit_reason, equity):
    result = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "SCRATCH"
    emoji = ":white_check_mark:" if pnl > 0 else ":x:" if pnl < 0 else ":heavy_minus_sign:"
    msg = (
        f"*TRADE EXIT* | `{ticker}` {direction.upper()} → {result}\n"
        f"P&L: ${pnl:+,.2f} | Reason: {exit_reason}\n"
        f"Equity: ${equity:,.2f}"
    )
    send_notification(msg, emoji)


def notify_daily_summary(date, trades, wins, losses, pnl, equity, drawdown):
    emoji = ":moneybag:" if pnl > 0 else ":rotating_light:" if pnl < -100 else ":page_facing_up:"
    msg = (
        f"*DAILY SUMMARY* | {date}\n"
        f"Trades: {trades} | W/L: {wins}/{losses}\n"
        f"Daily P&L: ${pnl:+,.2f} | Equity: ${equity:,.2f}\n"
        f"Drawdown: {drawdown:.1%}"
    )
    send_notification(msg, emoji)


def send_daily_email(
    date: str,
    ticker: str,
    trades_taken: int,
    wins: int,
    losses: int,
    daily_pnl: float,
    equity_start: float,
    equity_end: float,
    drawdown_pct: float,
    trades: list = None,
    was_halted: bool = False,
    halt_reason: str = None,
    mode: str = "paper",
):
    """Send formatted daily summary email. `trades` are the reconciled round
    trips for the day (entry/exit/P&L/reason), so the email reflects what
    actually happened, including target/stop exits, not just the 15:45
    force-closed positions."""
    pnl_color = "#22c55e" if daily_pnl >= 0 else "#ef4444"
    pnl_emoji = "📈" if daily_pnl >= 0 else "📉"
    daily_return = ((equity_end - equity_start) / equity_start * 100) if equity_start > 0 else 0
    mode_badge = "🟡 PAPER" if mode == "paper" else "🟢 LIVE"

    # Reconciled round-trip rows
    position_rows = ""
    if trades:
        for t in trades:
            tp = float(t.get("trade_pnl") or 0)
            row_color = "#22c55e" if tp >= 0 else "#ef4444"
            entry = t.get("entry_price") or 0
            exit_ = t.get("exit_price") or 0
            label = f"{t.get('ticker','?')} {str(t.get('direction','')).upper()}"
            detail = f"{t.get('shares','?')} sh &nbsp; ${entry:.2f} → ${exit_:.2f} &nbsp; <span style='color:#a0a0a0;'>({t.get('exit_reason','')})</span>"
            position_rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #333;">{label}</td>
                <td style="padding:8px;border-bottom:1px solid #333;">{detail}</td>
                <td style="padding:8px;border-bottom:1px solid #333;color:{row_color};">${tp:+,.2f}</td>
            </tr>"""

    halt_section = ""
    if was_halted:
        halt_section = f"""
        <div style="background:#7f1d1d;border:1px solid #ef4444;border-radius:8px;padding:12px;margin:16px 0;">
            <strong>⛔ Trading Halted:</strong> {halt_reason}
        </div>"""

    body = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:600px;margin:0 auto;background:#0f0f0f;color:#e5e5e5;border-radius:12px;overflow:hidden;">
        <div style="background:#1a1a2e;padding:20px 24px;border-bottom:2px solid {pnl_color};">
            <h1 style="margin:0;font-size:20px;color:#fff;">{pnl_emoji} Algo Trader — Daily Report</h1>
            <p style="margin:4px 0 0;color:#a0a0a0;font-size:14px;">{date} &nbsp;|&nbsp; {ticker} &nbsp;|&nbsp; {mode_badge}</p>
        </div>

        <div style="padding:24px;">
            <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
                <tr>
                    <td style="padding:12px;background:#1a1a1a;border-radius:8px 0 0 0;text-align:center;width:33%;">
                        <div style="color:#a0a0a0;font-size:12px;text-transform:uppercase;">Daily P&L</div>
                        <div style="font-size:24px;font-weight:bold;color:{pnl_color};margin-top:4px;">${daily_pnl:+,.2f}</div>
                        <div style="color:#a0a0a0;font-size:12px;">{daily_return:+.2f}%</div>
                    </td>
                    <td style="padding:12px;background:#1a1a1a;text-align:center;width:33%;">
                        <div style="color:#a0a0a0;font-size:12px;text-transform:uppercase;">Equity</div>
                        <div style="font-size:24px;font-weight:bold;color:#fff;margin-top:4px;">${equity_end:,.2f}</div>
                        <div style="color:#a0a0a0;font-size:12px;">from ${equity_start:,.2f}</div>
                    </td>
                    <td style="padding:12px;background:#1a1a1a;border-radius:0 8px 0 0;text-align:center;width:33%;">
                        <div style="color:#a0a0a0;font-size:12px;text-transform:uppercase;">Drawdown</div>
                        <div style="font-size:24px;font-weight:bold;color:{'#ef4444' if drawdown_pct > 0.05 else '#f59e0b' if drawdown_pct > 0.02 else '#22c55e'};margin-top:4px;">{drawdown_pct:.1%}</div>
                        <div style="color:#a0a0a0;font-size:12px;">from peak</div>
                    </td>
                </tr>
            </table>

            <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
                <tr>
                    <td style="padding:8px 12px;background:#1a1a1a;border-radius:8px 0 0 8px;">
                        <span style="color:#a0a0a0;">Trades</span>
                        <span style="float:right;color:#fff;font-weight:bold;">{trades_taken}</span>
                    </td>
                    <td style="padding:8px 12px;background:#1a1a1a;">
                        <span style="color:#a0a0a0;">Wins</span>
                        <span style="float:right;color:#22c55e;font-weight:bold;">{wins}</span>
                    </td>
                    <td style="padding:8px 12px;background:#1a1a1a;border-radius:0 8px 8px 0;">
                        <span style="color:#a0a0a0;">Losses</span>
                        <span style="float:right;color:#ef4444;font-weight:bold;">{losses}</span>
                    </td>
                </tr>
            </table>

            {halt_section}

            {f'''<h3 style="color:#a0a0a0;font-size:13px;text-transform:uppercase;margin:20px 0 8px;">Trades</h3>
            <table style="width:100%;border-collapse:collapse;background:#1a1a1a;border-radius:8px;">
                <tr style="color:#a0a0a0;font-size:12px;text-transform:uppercase;">
                    <th style="padding:8px;text-align:left;border-bottom:1px solid #333;">Trade</th>
                    <th style="padding:8px;text-align:left;border-bottom:1px solid #333;">Detail</th>
                    <th style="padding:8px;text-align:left;border-bottom:1px solid #333;">P&L</th>
                </tr>
                {position_rows}
            </table>''' if position_rows else '<p style="color:#a0a0a0;font-style:italic;">No trades today (no qualifying breakout, or signal filtered).</p>'}

            <div style="margin-top:24px;padding-top:16px;border-top:1px solid #333;color:#666;font-size:11px;">
                Strategy: ORB v2 &nbsp;|&nbsp; {ticker} {settings.OPENING_RANGE_MINUTES}m &nbsp;|&nbsp; ATR {settings.ATR_STOP_MULTIPLIER}x stop &nbsp;|&nbsp; {settings.REWARD_RISK_RATIO:.0f}:1 R:R<br>
                Rootwork Algo Trader &nbsp;·&nbsp; {mode.upper()} MODE
            </div>
        </div>
    </div>
    """

    subject = f"{pnl_emoji} Algo {date}: ${daily_pnl:+,.2f} | {ticker} | {mode_badge}"
    send_email(subject, body)


def notify_risk_halt(reason):
    msg = f"*TRADING HALTED* :octagonal_sign:\nReason: {reason}"
    send_notification(msg, ":rotating_light:")

    # Tailor the guidance to the halt reason so the email is actionable.
    r = reason or ""
    if "risk_state_unavailable" in r:
        guidance = ("Could not read risk state from MotherDuck — likely the "
                    "MOTHERDUCK_TOKEN secret or DB connectivity. The bot fails "
                    "closed and will not trade until this is resolved.")
    elif "max_drawdown" in r:
        guidance = ("Max drawdown limit hit. This needs manual account review "
                    "before trading resumes — it does not auto-clear.")
    else:
        guidance = ("Daily safety halt (daily-loss / consecutive-loss / "
                    "max-trades). This clears automatically on the next "
                    "trading day.")

    # Also email on halts — these are important
    send_email(
        f"⛔ Algo Trader HALTED — {reason}",
        f"""<div style="font-family:sans-serif;padding:20px;background:#1a1a1a;color:#e5e5e5;border-radius:8px;">
        <h2 style="color:#ef4444;">⛔ Trading Halted</h2>
        <p><strong>Reason:</strong> {reason}</p>
        <p>{guidance}</p>
        </div>"""
    )


def notify_no_signal(ticker, date, reason="No breakout detected"):
    msg = f"*NO TRADE* | `{ticker}` | {date}\n{reason}"
    send_notification(msg, ":zzz:")


def notify_health_alarm(severity: str, title: str, detail: str):
    """Loud watchdog alert via Slack + email. severity: CRITICAL | WARN | OK.
    Used by the morning health check so a silent miss becomes a loud presence."""
    emoji = (":rotating_light:" if severity == "CRITICAL"
             else ":warning:" if severity == "WARN" else ":white_check_mark:")
    send_notification(f"*{severity}: {title}*\n{detail}", emoji)
    if severity in ("CRITICAL", "WARN"):
        color = "#ef4444" if severity == "CRITICAL" else "#f59e0b"
        icon = "⛔" if severity == "CRITICAL" else "⚠️"
        send_email(
            f"{icon} Algo Trader {severity}: {title}",
            f"""<div style="font-family:sans-serif;padding:20px;background:#1a1a1a;color:#e5e5e5;border-radius:8px;">
            <h2 style="color:{color};">{title}</h2>
            <p style="font-size:15px;">{detail}</p>
            <p style="color:#666;font-size:12px;margin-top:16px;">Rootwork Algo Trader watchdog</p>
            </div>""",
        )

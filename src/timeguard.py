"""
ET-window guard for the scheduled entrypoints.

GitHub Actions cron is UTC-only, so a fixed cron drifts by an hour across DST.
To trade year-round we schedule both the EDT and EST cron offsets and let this
guard ensure only the correctly-timed run actually acts: on a SCHEDULED Actions
run, if the current Eastern time is outside the intended window, the script
no-ops (exit 0) instead of, say, executing before the market opens.

Windows are wide enough to absorb normal cron lag but narrower than the 1-hour
DST offset, so the wrong-season cron is rejected while a slightly-late run is
not. Manual workflow_dispatch runs and local runs bypass the guard entirely.
"""
import os
from datetime import datetime

import pytz


def _scheduled_run() -> bool:
    """True only for a cron-triggered GitHub Actions run (not manual, not local)."""
    return (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_EVENT_NAME") == "schedule")


def _in_window(now_hhmm: str, start_hhmm: str, end_hhmm: str) -> bool:
    return start_hhmm <= now_hhmm <= end_hhmm


def ensure_et_window(start_hhmm: str, end_hhmm: str, label: str) -> None:
    """Exit 0 if a scheduled run fires outside its intended ET window."""
    if not _scheduled_run():
        return
    now = datetime.now(pytz.timezone("US/Eastern")).strftime("%H:%M")
    if not _in_window(now, start_hhmm, end_hhmm):
        print(f"  [{label}] ET {now} outside {start_hhmm}-{end_hhmm} "
              f"(DST cron drift) — skipping this run.")
        raise SystemExit(0)

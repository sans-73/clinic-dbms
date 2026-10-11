"""DB stores UTC naive datetimes (G7). Humans see and type clinic-local time (default IST)."""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config

try:
    LOCAL = ZoneInfo(config.TIMEZONE)
except Exception:                                     # tzdata missing on Windows -> fall back to fixed IST
    LOCAL = timezone(timedelta(hours=5, minutes=30))


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(dt):
    if not isinstance(dt, datetime):
        return dt
    return dt.replace(tzinfo=timezone.utc).astimezone(LOCAL)


def local_day_window(d: date | None = None) -> tuple[datetime, datetime]:
    """UTC [start, end) of a local calendar day."""
    d = d or datetime.now(LOCAL).date()
    s = datetime(d.year, d.month, d.day, tzinfo=LOCAL)
    e = s + timedelta(days=1)
    f = lambda x: x.astimezone(timezone.utc).replace(tzinfo=None)
    return f(s), f(e)


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def local_input_to_utc(s: str | None):
    """'2026-10-10T14:30' typed in local time -> UTC naive datetime (or None)."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace(" ", "T"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def now_local_input() -> str:
    return datetime.now(LOCAL).strftime("%Y-%m-%dT%H:%M")


def fmt(dt, pattern="%d %b %Y, %H:%M"):
    if dt is None or dt == "":
        return ""
    if isinstance(dt, datetime):
        return to_local(dt).strftime(pattern)
    if isinstance(dt, date):
        return dt.strftime("%d %b %Y")
    return str(dt)

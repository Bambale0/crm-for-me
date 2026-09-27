"""Timezone and date helpers.

All datetimes are stored in UTC. Business month boundaries are computed in the
owner-configured timezone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MONTH_NAMES_RU = [
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_zone(tz_name: str) -> ZoneInfo | timezone:
    if tz_name.upper() == "UTC":
        return timezone.utc
    return ZoneInfo(tz_name)


def to_local(dt: datetime, tz_name: str) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_zone(tz_name))


def local_month(dt: datetime, tz_name: str) -> tuple[int, int]:
    local = to_local(dt, tz_name)
    return local.year, local.month


def current_month(tz_name: str) -> tuple[int, int]:
    return local_month(now_utc(), tz_name)


def month_label(year: int, month: int) -> str:
    return f"{MONTH_NAMES_RU[month]} {year}"


def format_local(dt: datetime, tz_name: str) -> str:
    return to_local(dt, tz_name).strftime("%d.%m.%Y %H:%M")

"""UTC storage with accurate local-time display for RESON.

RESON persists every timestamp in UTC. This module is the single place where
a stored UTC instant is turned into something a human reads, and it exists so
that the dashboard never shows a UTC clock while claiming to show local time
(or vice versa).

Rules
-----
* **Naive datetimes are UTC.** A timestamp without an offset is interpreted as
  UTC, exactly as the storage layer does. It is never silently reinterpreted
  as local time.
* **Aware datetimes are converted, never relabelled.** The instant is
  preserved; only the rendering changes.
* **The timezone is always named.** Every formatted timestamp carries the
  zone it is rendered in (``UTC`` or an IANA zone name with its offset), so a
  reader never has to guess which clock they are looking at.
* **Daylight saving is handled by :mod:`zoneinfo`.** Local wall-clock
  rendering is correct across DST transitions, including the repeated hour.
* **Charts use local wall-clock values.** Vega-Lite formats time axes in UTC
  and offers no timezone override, so chart data is converted to a naive
  local wall-clock value and the axis is labelled with the zone. The instant
  is unchanged; only its presentation is.

The display timezone is one of: UTC, the host's own zone (detected from the
platform), or any IANA zone the user selects. Nothing is ever rendered in an
unnamed zone.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, tzinfo
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

__all__ = [
    "UTC",
    "COMMON_TIMEZONES",
    "available_timezone_names",
    "chart_axis_title",
    "format_clock",
    "format_timestamp",
    "resolve_choice",
    "resolve_timezone",
    "server_timezone",
    "server_timezone_name",
    "to_chart_time",
    "to_timezone",
    "timezone_label",
    "zone_offset_label",
]

UTC = timezone.utc

#: The canonical label used for Coordinated Universal Time.
UTC_LABEL = "UTC"

#: A curated set of frequently used IANA zones offered in the UI. The full
#: set from :func:`zoneinfo.available_timezones` is always accepted as a
#: typed value, so this list only affects convenience, not capability.
COMMON_TIMEZONES: tuple[str, ...] = (
    "UTC",
    "Europe/London",
    "Europe/Berlin",
    "Europe/Paris",
    "Europe/Madrid",
    "Europe/Rome",
    "Europe/Amsterdam",
    "Europe/Stockholm",
    "Europe/Warsaw",
    "Europe/Athens",
    "Europe/Moscow",
    "Europe/Istanbul",
    "Europe/Kyiv",
    "Africa/Lagos",
    "Africa/Cairo",
    "Africa/Johannesburg",
    "Africa/Nairobi",
    "Asia/Dubai",
    "Asia/Karachi",
    "Asia/Kolkata",
    "Asia/Dhaka",
    "Asia/Bangkok",
    "Asia/Jakarta",
    "Asia/Singapore",
    "Asia/Hong_Kong",
    "Asia/Shanghai",
    "Asia/Tokyo",
    "Asia/Seoul",
    "Australia/Perth",
    "Australia/Sydney",
    "Pacific/Auckland",
    "America/Sao_Paulo",
    "America/Argentina/Buenos_Aires",
    "America/Bogota",
    "America/Mexico_City",
    "America/New_York",
    "America/Toronto",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Anchorage",
    "Pacific/Honolulu",
    "UTC-05:00",
    "UTC+01:00",
    "UTC+05:30",
    "UTC+08:00",
    "UTC+09:00",
)


def available_timezone_names() -> list[str]:
    """Return every IANA zone name this platform can resolve.

    Falls back to the curated list when the platform's zone database is
    unavailable, so the picker never becomes empty.
    """
    try:
        names = sorted(available_timezones())
    except Exception:  # pragma: no cover - platform zone database missing
        return list(COMMON_TIMEZONES)
    return names or list(COMMON_TIMEZONES)


def _parse_fixed_offset(name: str) -> tzinfo | None:
    """Return a fixed-offset timezone for names like ``"UTC+05:30"``."""
    text = name.strip().upper()
    if not text.startswith("UTC"):
        return None
    remainder = text[3:]
    if not remainder:
        return UTC
    sign = 1
    if remainder.startswith("+"):
        remainder = remainder[1:]
    elif remainder.startswith("-"):
        sign = -1
        remainder = remainder[1:]
    if ":" not in remainder:
        return None
    hours_text, _, minutes_text = remainder.partition(":")
    try:
        hours = int(hours_text)
        minutes = int(minutes_text)
    except ValueError:
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    from datetime import timedelta

    return timezone(sign * timedelta(hours=hours, minutes=minutes), text)


def server_timezone_name() -> str | None:
    """Return the IANA name of the host's own timezone, if it can be found.

    Resolution order:

    1. The ``TZ`` environment variable when it names a resolvable zone.
    2. The ``/etc/localtime`` symlink target (Linux and macOS).
    3. ``None`` — the platform does not expose an IANA name, in which case
       :func:`server_timezone` falls back to the current UTC offset.

    The host timezone is only ever used as the *default* display zone. It is
    never stored with telemetry.
    """
    env_tz = os.getenv("TZ")
    if env_tz:
        if env_tz.strip().upper() == "UTC":
            return "UTC"
        try:
            ZoneInfo(env_tz)
            return env_tz
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            pass

    for candidate in ("/etc/localtime", "/etc/TZ"):
        try:
            if os.path.islink(candidate):
                target = os.path.realpath(candidate)
                marker = "zoneinfo/"
                if marker in target:
                    name = target.split(marker, 1)[1]
                    try:
                        ZoneInfo(name)
                        return name
                    except (ZoneInfoNotFoundError, ValueError, KeyError):
                        continue
        except OSError:
            continue
    return None


def server_timezone() -> tzinfo:
    """Return the host's own timezone.

    Falls back to the current UTC offset when the platform does not expose an
    IANA zone name. The fallback is a *fixed* offset, so it does not track
    daylight saving; the UI labels it accordingly instead of pretending it is
    a full zone.
    """
    name = server_timezone_name()
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            pass
    local = datetime.now().astimezone().tzinfo
    return local or UTC


def resolve_timezone(name: str | None = None) -> tzinfo:
    """Resolve a timezone name to a concrete :class:`tzinfo`.

    Accepts an IANA zone name, ``"UTC"``, or a fixed-offset name such as
    ``"UTC+05:30"``. An unknown or empty name falls back to UTC rather than
    raising, so a stale preference can never break the dashboard.
    """
    if not name:
        return UTC
    text = str(name).strip()
    if not text:
        return UTC
    if text.upper() == "UTC":
        return UTC
    fixed = _parse_fixed_offset(text)
    if fixed is not None:
        return fixed
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return UTC


def to_timezone(value: datetime, tz: tzinfo | None = None) -> datetime:
    """Convert a stored timestamp into the display timezone.

    Naive input is interpreted as UTC, matching the storage layer. The result
    is always timezone-aware.
    """
    target = tz or UTC
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(target)


def zone_offset_label(value: datetime, tz: tzinfo | None = None) -> str:
    """Return the UTC offset of the display timezone at ``value``'s instant.

    The offset is computed for the *specific instant*, so a zone that
    changes offset across a daylight-saving boundary reports the offset that
    actually applied at that moment.
    """
    localized = to_timezone(value, tz)
    offset = localized.utcoffset()
    if offset is None:
        return "UTC"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    if minutes:
        return f"UTC{sign}{hours:02d}:{minutes:02d}"
    return f"UTC{sign}{hours:02d}:00"


def timezone_label(tz: tzinfo | None = None) -> str:
    """Return a human-readable name for the display timezone."""
    if tz is None or tz is UTC:
        return UTC_LABEL
    name = getattr(tz, "key", None) or str(tz)
    if name.startswith("UTC+") or name.startswith("UTC-"):
        return name
    return name


def format_timestamp(
    value: datetime,
    tz: tzinfo | None = None,
    *,
    fmt: str = "%Y-%m-%d %H:%M:%S",
    with_zone: bool = True,
) -> str:
    """Format a stored UTC timestamp in the display timezone.

    The zone is appended by default so the rendered clock is never ambiguous.
    """
    if value is None:
        return "—"
    localized = to_timezone(value, tz)
    text = localized.strftime(fmt)
    if not with_zone:
        return text
    return f"{text} {zone_offset_label(value, tz)}"


def format_clock(value: datetime, tz: tzinfo | None = None) -> str:
    """Format a timestamp as a wall-clock time in the display timezone."""
    if value is None:
        return "—"
    return to_timezone(value, tz).strftime("%H:%M:%S")


def to_chart_time(value: datetime, tz: tzinfo | None = None) -> datetime:
    """Convert a timestamp to a naive local wall-clock value for charting.

    Vega-Lite renders time axes in UTC and exposes no timezone override, so
    the local wall clock is materialised here (timezone information removed
    after conversion) and the axis is labelled with the zone. The instant is
    unchanged; only its representation is.
    """
    localized = to_timezone(value, tz)
    return localized.replace(tzinfo=None)


def chart_axis_title(tz: tzinfo | None = None) -> str:
    """Return the axis title describing the timezone a chart is rendered in."""
    label = timezone_label(tz)
    if label == UTC_LABEL:
        return "Time (UTC)"
    return f"Time ({label} local)"


def resolve_choice(choices: Iterable[str], selected: str | None) -> str:
    """Return ``selected`` when it is one of ``choices``, else the first one."""
    options = list(choices)
    if not options:
        return ""
    if selected in options:
        return selected
    return options[0]

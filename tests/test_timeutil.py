"""Tests for UTC storage with accurate local-time display.

Covers :mod:`src.common.timeutil` (pure, no Streamlit) and the dashboard
context helpers that render stored UTC timestamps in the display timezone.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import backend as svc
from src.common.timeutil import (
    UTC,
    available_timezone_names,
    chart_axis_title,
    format_clock,
    format_timestamp,
    resolve_timezone,
    server_timezone,
    server_timezone_name,
    to_chart_time,
    to_timezone,
    timezone_label,
    zone_offset_label,
)

WINTER = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
SUMMER = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Timezone resolution
# ---------------------------------------------------------------------------


def test_resolve_timezone_returns_utc_for_unknown_names():
    """An unusable zone name degrades to UTC instead of raising."""
    assert resolve_timezone("Not/AZone") is timezone.utc
    assert resolve_timezone("") is timezone.utc
    assert resolve_timezone(None) is timezone.utc


def test_resolve_timezone_accepts_iana_names():
    """A valid IANA zone resolves to that zone."""
    berlin = resolve_timezone("Europe/Berlin")
    assert timezone_label(berlin) == "Europe/Berlin"


def test_resolve_timezone_accepts_fixed_offsets():
    """Fixed-offset names such as UTC+05:30 are supported."""
    offset = resolve_timezone("UTC+05:30")
    localized = to_timezone(WINTER, offset)
    assert localized.hour == 17
    assert localized.minute == 30


def test_resolve_timezone_is_case_insensitive_for_utc():
    """UTC is accepted in any case."""
    assert resolve_timezone("utc") is timezone.utc


def test_available_timezone_names_are_not_empty():
    """The zone list is populated so the picker is never empty."""
    names = available_timezone_names()
    assert names
    assert "UTC" in names


def test_server_timezone_name_resolves_when_reported():
    """A reported host zone must be resolvable, or absent."""
    name = server_timezone_name()
    if name is not None:
        assert timezone_label(resolve_timezone(name)) == name


def test_server_timezone_is_always_a_tzinfo():
    """The host timezone always resolves to a concrete tzinfo."""
    assert server_timezone() is not None


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def test_naive_datetime_is_interpreted_as_utc():
    """A naive timestamp is UTC, matching the storage layer — not local."""
    naive = datetime(2026, 3, 1, 12, 0)
    berlin = resolve_timezone("Europe/Berlin")
    assert to_timezone(naive, berlin).hour == 13
    assert to_timezone(naive, berlin).tzinfo is not None


def test_aware_datetime_is_converted_not_relabelled():
    """Conversion preserves the instant."""
    berlin = resolve_timezone("Europe/Berlin")
    converted = to_timezone(WINTER, berlin)
    assert converted == WINTER
    assert converted.astimezone(timezone.utc) == WINTER


def test_conversion_across_day_boundary():
    """A late UTC timestamp rolls over to the next local day."""
    tokyo = resolve_timezone("Asia/Tokyo")
    late = datetime(2026, 5, 1, 23, 30, tzinfo=timezone.utc)
    assert to_timezone(late, tokyo).day == 2


def test_daylight_saving_offset_is_applied_per_instant():
    """The offset follows the zone's DST state at that specific instant."""
    berlin = resolve_timezone("Europe/Berlin")
    assert zone_offset_label(WINTER, berlin) == "UTC+01:00"
    assert zone_offset_label(SUMMER, berlin) == "UTC+02:00"


def test_zone_offset_label_for_utc():
    """UTC renders as a plain UTC offset."""
    assert zone_offset_label(WINTER, timezone.utc) == "UTC+00:00"


def test_zone_offset_label_for_half_hour_zone():
    """Zones with a non-hour offset are rendered exactly."""
    kolkata = resolve_timezone("Asia/Kolkata")
    assert zone_offset_label(WINTER, kolkata) == "UTC+05:30"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def test_format_timestamp_renders_local_wall_clock_with_offset():
    """The rendered clock is local and the offset is always shown."""
    berlin = resolve_timezone("Europe/Berlin")
    text = format_timestamp(WINTER, berlin)
    assert text.startswith("2026-01-15 13:00:00")
    assert text.endswith("UTC+01:00")


def test_format_timestamp_without_zone():
    """The zone suffix can be omitted for compact tables."""
    berlin = resolve_timezone("Europe/Berlin")
    text = format_timestamp(WINTER, berlin, with_zone=False)
    assert text == "2026-01-15 13:00:00"


def test_format_timestamp_accepts_custom_format():
    """A custom strftime format is honoured."""
    berlin = resolve_timezone("Europe/Berlin")
    text = format_timestamp(WINTER, berlin, fmt="%Y-%m-%d %H:%M")
    assert text.startswith("2026-01-15 13:00")


def test_format_timestamp_handles_missing_value():
    """A missing timestamp renders as an em dash, never as an error."""
    assert format_timestamp(None, timezone.utc) == "—"


def test_format_clock_renders_time_only():
    """The clock helper renders only the time of day."""
    berlin = resolve_timezone("Europe/Berlin")
    assert format_clock(WINTER, berlin) == "13:00:00"


def test_timezone_label_for_fixed_offset():
    """A fixed-offset zone keeps its own name."""
    assert timezone_label(resolve_timezone("UTC+05:30")) == "UTC+05:30"


# ---------------------------------------------------------------------------
# Chart values
# ---------------------------------------------------------------------------


def test_to_chart_time_produces_naive_local_wall_clock():
    """Chart values are naive local wall-clock values."""
    berlin = resolve_timezone("Europe/Berlin")
    value = to_chart_time(WINTER, berlin)
    assert value.tzinfo is None
    assert value.hour == 13
    assert value.minute == 0


def test_to_chart_time_preserves_the_instant_in_utc():
    """The chart value maps back to the same instant when read as local."""
    berlin = resolve_timezone("Europe/Berlin")
    value = to_chart_time(WINTER, berlin)
    # The chart value is a naive local wall clock; reading it back in the
    # same zone yields the original instant.
    assert value.replace(tzinfo=berlin) == WINTER


def test_chart_axis_title_names_the_timezone():
    """The axis title names the zone so the clock is never ambiguous."""
    assert chart_axis_title(timezone.utc) == "Time (UTC)"
    assert "Europe/Berlin" in chart_axis_title(resolve_timezone("Europe/Berlin"))


# ---------------------------------------------------------------------------
# Dashboard context
# ---------------------------------------------------------------------------


def test_dashboard_context_formats_in_the_display_timezone():
    """The dashboard context renders stored UTC in the chosen zone."""
    ctx = svc.DashboardContext(
        mode=svc.ModeContext(
            mode=svc.MonitoringMode.LOCAL,
            label="Local monitoring",
            description="",
        ),
        status=svc.StatusReport(
            status=svc.CollectorStatus.RUNNING, detail=""
        ),
        storage=None,
        collector=None,
        display_tz=resolve_timezone("Europe/Berlin"),
    )
    assert ctx.timezone_name == "Europe/Berlin"
    assert ctx.format_timestamp(WINTER).startswith("2026-01-15 13:00:00")
    assert ctx.format_clock(WINTER) == "13:00:00"
    assert ctx.zone_offset() in ("UTC+01:00", "UTC+02:00")


def test_dashboard_context_defaults_to_utc():
    """Without an explicit choice the dashboard renders UTC."""
    ctx = svc.DashboardContext(
        mode=svc.ModeContext(
            mode=svc.MonitoringMode.LOCAL,
            label="Local monitoring",
            description="",
        ),
        status=svc.StatusReport(
            status=svc.CollectorStatus.RUNNING, detail=""
        ),
        storage=None,
        collector=None,
    )
    assert ctx.display_tz is UTC or ctx.display_tz == timezone.utc
    assert ctx.format_timestamp(WINTER) == "2026-01-15 12:00:00 UTC+00:00"


def test_incident_label_uses_the_display_timezone():
    """The incident selector label matches the clock on the page."""
    incident = svc.Incident(
        title="test incident",
        start_time=WINTER,
        severity="high",
        status="open",
    )
    utc_label = svc.incident_label(incident)
    berlin_label = svc.incident_label(incident, resolve_timezone("Europe/Berlin"))
    assert "2026-01-15 12:00" in utc_label
    assert "2026-01-15 13:00" in berlin_label


@pytest.mark.parametrize(
    "zone,expected_hour",
    [
        ("UTC", 12),
        ("Europe/Berlin", 13),
        ("Asia/Tokyo", 21),
        ("America/New_York", 7),
    ],
)
def test_display_timezone_conversion_matches_expectations(zone, expected_hour):
    """Each display zone renders the expected local hour."""
    localized = to_timezone(WINTER, resolve_timezone(zone))
    assert localized.hour == expected_hour


def test_stored_timestamps_are_never_mutated_by_display():
    """Rendering in a local zone does not alter the stored instant."""
    berlin = resolve_timezone("Europe/Berlin")
    before = WINTER
    format_timestamp(before, berlin)
    to_chart_time(before, berlin)
    assert before == WINTER
    assert before.tzinfo is timezone.utc


def test_timezone_conversion_is_stable_across_repeated_calls():
    """Repeated conversions do not drift."""
    berlin = resolve_timezone("Europe/Berlin")
    first = to_timezone(WINTER, berlin)
    second = to_timezone(WINTER, berlin)
    assert first == second
    assert first.utcoffset() == second.utcoffset()


def test_offsets_do_not_accumulate_over_multiple_conversions():
    """Converting an already-local timestamp does not shift it again."""
    berlin = resolve_timezone("Europe/Berlin")
    once = to_timezone(WINTER, berlin)
    twice = to_timezone(once, berlin)
    assert once == twice


def test_zone_offset_reflects_dst_transition_instant():
    """A timestamp inside the DST gap reports the post-transition offset."""
    berlin = resolve_timezone("Europe/Berlin")
    # 2026-03-29 01:00 UTC is 03:00 CEST (after the 02:00 -> 03:00 jump).
    after = datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)
    assert zone_offset_label(after, berlin) == "UTC+02:00"
    assert zone_offset_label(WINTER, berlin) == "UTC+01:00"


def test_summer_timestamp_converts_to_the_dst_offset():
    """Summer timestamps use the daylight-saving offset."""
    berlin = resolve_timezone("Europe/Berlin")
    localized = to_timezone(SUMMER, berlin)
    assert localized.hour == 14
    assert localized.utcoffset() == timedelta(hours=2)

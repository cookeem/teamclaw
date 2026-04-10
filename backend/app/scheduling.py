from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ScheduleValidationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedSchedule:
    schedule_type: str
    timezone: str
    cron_expr: str | None
    interval_seconds: int | None


def normalize_schedule(
    *,
    schedule_type: str,
    timezone: str,
    cron_expr: str | None,
    interval_seconds: int | None,
) -> NormalizedSchedule:
    normalized_type = str(schedule_type or "").strip().lower()
    if normalized_type not in {"cron", "interval"}:
        raise ScheduleValidationError("schedule_type must be 'cron' or 'interval'.")

    normalized_tz = str(timezone or "").strip() or "UTC"
    try:
        ZoneInfo(normalized_tz)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleValidationError(f"Invalid timezone: {normalized_tz}") from exc

    if normalized_type == "interval":
        if interval_seconds is None:
            raise ScheduleValidationError("interval_minutes is required for interval schedules.")
        if int(interval_seconds) < 60:
            raise ScheduleValidationError("interval_minutes must be at least 1.")
        return NormalizedSchedule(
            schedule_type="interval",
            timezone=normalized_tz,
            cron_expr=None,
            interval_seconds=int(interval_seconds),
        )

    expr = str(cron_expr or "").strip()
    if not expr:
        raise ScheduleValidationError("cron_expr is required for cron schedules.")
    _validate_cron_expr(expr)
    return NormalizedSchedule(
        schedule_type="cron",
        timezone=normalized_tz,
        cron_expr=expr,
        interval_seconds=None,
    )


def compute_next_run_at(
    *,
    schedule_type: str,
    timezone: str,
    cron_expr: str | None,
    interval_seconds: int | None,
    from_time: dt.datetime,
) -> dt.datetime:
    normalized = normalize_schedule(
        schedule_type=schedule_type,
        timezone=timezone,
        cron_expr=cron_expr,
        interval_seconds=interval_seconds,
    )
    base = _ensure_utc(from_time)
    if normalized.schedule_type == "interval":
        assert normalized.interval_seconds is not None
        return base + dt.timedelta(seconds=normalized.interval_seconds)
    assert normalized.cron_expr is not None
    return _next_cron_time(expr=normalized.cron_expr, timezone=normalized.timezone, from_time=base)


def _ensure_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _validate_cron_expr(expr: str) -> None:
    fields = expr.split()
    if len(fields) != 5:
        raise ScheduleValidationError("cron_expr must contain 5 fields: minute hour day month weekday.")
    _parse_cron_fields(fields)


def _next_cron_time(*, expr: str, timezone: str, from_time: dt.datetime) -> dt.datetime:
    fields = expr.split()
    parsed = _parse_cron_fields(fields)
    tz = ZoneInfo(timezone)
    local = _ensure_utc(from_time).astimezone(tz).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)

    max_checks = 60 * 24 * 366 * 2
    for _ in range(max_checks):
        if _cron_matches(local, parsed):
            return local.astimezone(dt.timezone.utc)
        local += dt.timedelta(minutes=1)
    raise ScheduleValidationError("Unable to calculate next cron run time within 2 years.")


def _parse_cron_fields(fields: list[str]) -> dict[str, set[int] | bool]:
    minute = _parse_cron_field(fields[0], min_value=0, max_value=59)
    hour = _parse_cron_field(fields[1], min_value=0, max_value=23)
    day = _parse_cron_field(fields[2], min_value=1, max_value=31)
    month = _parse_cron_field(fields[3], min_value=1, max_value=12)
    weekday = _parse_cron_field(fields[4], min_value=0, max_value=7, remap_7_to_0=True)
    return {
        "minute": minute.values,
        "hour": hour.values,
        "day": day.values,
        "month": month.values,
        "weekday": weekday.values,
        "day_wildcard": day.wildcard,
        "weekday_wildcard": weekday.wildcard,
    }


@dataclass(frozen=True)
class _FieldParseResult:
    values: set[int]
    wildcard: bool


def _parse_cron_field(
    text: str,
    *,
    min_value: int,
    max_value: int,
    remap_7_to_0: bool = False,
) -> _FieldParseResult:
    raw = text.strip()
    if not raw:
        raise ScheduleValidationError("Invalid cron field: empty value.")

    wildcard = raw == "*"
    values: set[int] = set()
    parts = raw.split(",")
    for part in parts:
        token = part.strip()
        if not token:
            raise ScheduleValidationError(f"Invalid cron token: '{raw}'")

        step = 1
        base = token
        if "/" in token:
            base, step_raw = token.split("/", 1)
            if not step_raw.isdigit() or int(step_raw) <= 0:
                raise ScheduleValidationError(f"Invalid cron step: '{token}'")
            step = int(step_raw)

        if base == "*":
            start = min_value
            end = max_value
        elif "-" in base:
            start_raw, end_raw = base.split("-", 1)
            if not start_raw.isdigit() or not end_raw.isdigit():
                raise ScheduleValidationError(f"Invalid cron range: '{token}'")
            start = int(start_raw)
            end = int(end_raw)
            if start > end:
                raise ScheduleValidationError(f"Invalid cron range: '{token}'")
        else:
            if not base.isdigit():
                raise ScheduleValidationError(f"Invalid cron value: '{token}'")
            start = int(base)
            end = int(base)

        if start < min_value or end > max_value:
            raise ScheduleValidationError(f"Cron value out of range: '{token}'")

        for value in range(start, end + 1, step):
            normalized = 0 if remap_7_to_0 and value == 7 else value
            values.add(normalized)

    if not values:
        raise ScheduleValidationError(f"Invalid cron field: '{raw}'")
    return _FieldParseResult(values=values, wildcard=wildcard)


def _cron_matches(candidate: dt.datetime, parsed: dict[str, set[int] | bool]) -> bool:
    minute_values = parsed["minute"]
    hour_values = parsed["hour"]
    day_values = parsed["day"]
    month_values = parsed["month"]
    weekday_values = parsed["weekday"]
    if not isinstance(minute_values, set) or candidate.minute not in minute_values:
        return False
    if not isinstance(hour_values, set) or candidate.hour not in hour_values:
        return False
    if not isinstance(month_values, set) or candidate.month not in month_values:
        return False

    day_match = isinstance(day_values, set) and candidate.day in day_values
    weekday = (candidate.weekday() + 1) % 7  # Sunday=0
    weekday_match = isinstance(weekday_values, set) and weekday in weekday_values
    day_wildcard = bool(parsed["day_wildcard"])
    weekday_wildcard = bool(parsed["weekday_wildcard"])

    if day_wildcard and weekday_wildcard:
        return True
    if day_wildcard:
        return weekday_match
    if weekday_wildcard:
        return day_match
    return day_match or weekday_match

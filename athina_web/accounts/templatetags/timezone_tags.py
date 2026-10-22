"""Template filters for dates stored by the grading engine.

The grading engine writes timestamps as naive UTC (its container clock is UTC),
while Django's USE_TZ is on and expects aware datetimes. This filter attaches
UTC to those values so the template's ``date``/``time`` filters convert them to
the active timezone -- the user's profile setting, or the browser's zone when the
setting is "auto" (see accounts.middleware.TimezoneMiddleware).
"""

from datetime import datetime, timezone as dt_timezone

from django import template
from django.utils import timezone

register = template.Library()

# The grading engine stores a year-1 timestamp (``0001-01-01 00:00:00``) in
# several columns to mean "never" -- e.g. ``commit_date`` / ``last_graded`` on
# a student row that has not been graded yet, and it is also what the web app
# writes when it provisions a row. That value cannot be converted to a timezone
# west of UTC: ``0001-01-01`` in UTC is year 0 locally, which does not exist, so
# ``astimezone()`` raises ``OverflowError: date value out of range``. Treat it
# as missing instead, so the template renders an em dash rather than a 500.
_SENTINEL_MIN_YEAR = 2


def is_never(value):
    """True when ``value`` is the grader's "never" sentinel (year < 2)."""
    return isinstance(value, datetime) and value.year < _SENTINEL_MIN_YEAR


@register.filter
def as_utc(value):
    """Return ``value`` as a UTC-aware datetime (naive values are assumed UTC).

    Returns ``None`` for the grader's year-1 "never" sentinel so the caller's
    ``{% if %}`` guard skips it and no timezone conversion is attempted.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime):
        if is_never(value):
            return None
        if timezone.is_naive(value):
            return timezone.make_aware(value, dt_timezone.utc)
        return value
    return value

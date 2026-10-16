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


@register.filter
def as_utc(value):
    """Return ``value`` as a UTC-aware datetime (naive values are assumed UTC)."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, dt_timezone.utc)
        return value
    return value

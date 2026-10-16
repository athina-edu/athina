"""Force users holding an admin-issued password to choose their own.

Accounts created by faculty (and the temp-password resets they can trigger)
start with `UserProfile.must_change_password = True`. Until the user picks a new
password they may not use the rest of the site, so a leaked invitation email is
not enough to operate an account indefinitely.

Implemented as middleware rather than a decorator because there are many views
across three apps and `login_required` is applied inconsistently; a decorator
would silently miss new ones. The allow-list below is the single place that
decides what an un-changed account may still reach.
"""

from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

try:  # Python 3.9+; harmless fallback if the zoneinfo database is unavailable.
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    def valid_timezone(name):
        try:
            ZoneInfo(name)
            return True
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            return False
except ImportError:  # pragma: no cover - only on Python < 3.9
    def valid_timezone(name):
        return False

# Views an account with a pending password change may still reach, so it can
# complete the change and sign out. Prefix-matched against the request path.
_EXEMPT_PREFIXES = (
    '/accounts/password/change/',
    '/accounts/logout/',
    '/accounts/login/',
    '/accounts/password_reset/',
    '/accounts/reset/',
)

# Static assets must load, or the change-password page itself renders unstyled.
_EXEMPT_SUFFIXES = ('.css', '.js', '.png', '.jpg', '.svg', '.ico', '.woff', '.woff2')


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and self._must_change(user):
            path = request.path
            if not self._is_exempt(path):
                return redirect(reverse('accounts:password_change_required'))
        return self.get_response(request)

    @staticmethod
    def _must_change(user):
        # is_superuser is excluded deliberately: a superuser who is locked out
        # could not fix their own account through the admin, which would be a
        # self-inflicted lockout.
        if user.is_superuser:
            return False
        try:
            return bool(user.profile.must_change_password)
        except Exception:
            # No profile (or a broken one) — nothing to enforce.
            return False

    @staticmethod
    def _is_exempt(path):
        if path.startswith(_EXEMPT_PREFIXES):
            return True
        return path.endswith(_EXEMPT_SUFFIXES)


class TimezoneMiddleware:
    """Activate the timezone used to render dates for this request.

    Grading timestamps are stored as naive UTC, so they have to be converted for
    display. The profile setting decides:

    * an explicit IANA name (e.g. "America/Los_Angeles") is always honoured;
    * "auto" (the default) follows the browser — the profile page stores the
      browser's IANA zone in the `tz` cookie via Intl.DateTimeFormat().

    An unknown/invalid zone is ignored rather than raising, so a stale cookie or
    a hand-edited preference can never break the page.
    """

    COOKIE_NAME = 'tz'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tzname = self._resolve(request)
        activate = getattr(timezone, 'activate', None)
        try:
            if tzname:
                activate(tzname)
            else:
                deactivate = getattr(timezone, 'deactivate', None)
                if deactivate:
                    deactivate()
            return self.get_response(request)
        finally:
            # Every response path must restore the process-wide default, since
            # the server handles other requests (and threads) with it.
            deactivate = getattr(timezone, 'deactivate', None)
            if deactivate:
                deactivate()

    def _resolve(self, request):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return None
        try:
            preference = (user.profile.timezone or 'auto').strip()
        except Exception:
            return None

        if preference and preference != 'auto' and valid_timezone(preference):
            return preference

        # 'auto' (or an unusable preference): trust the browser's zone, but only if
        # it is a real zone name. Falling through to None means "use the server
        # default" (UTC), which is always safe.
        cookie = request.COOKIES.get(self.COOKIE_NAME, '')
        if cookie and valid_timezone(cookie):
            return cookie
        return None

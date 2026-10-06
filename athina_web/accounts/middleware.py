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
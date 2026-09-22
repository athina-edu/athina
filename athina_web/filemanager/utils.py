import os
import re
from django.conf import settings


def slashes_encode(string):
    return re.sub("/", "|", string)


def slashes_decode(string):
    return re.sub(r"\|", "/", string)


def resolve_owner_id(request):
    """Work out whose files the requester may browse.

    Assignment directories live under MEDIA_ROOT/<owner_id>/, where owner_id is
    the faculty member who owns the assignment. Faculty browse their own
    directory; a TA browses the directory of the faculty they assist, otherwise
    the file browser would look in a directory that does not exist.
    """
    user = request.user
    if user.is_superuser:
        # An admin may not own assignments themselves, so default to the
        # directory for the first assignment-owning faculty rather than their
        # own (likely absent) directory. ?owner=<id> targets a specific one.
        requested = request.GET.get('owner')
        if requested and str(requested).isdigit():
            return int(requested)
        return _first_owner_id() or user.id
    profile = getattr(user, 'profile', None)
    if profile is None:
        return user.id
    if profile.role == 'ta':
        faculty_id = profile.managed_by.values_list('id', flat=True).first()
        if faculty_id is not None:
            return faculty_id
    return user.id


def _first_owner_id():
    """The id of the user owning the most recently created assignment."""
    from athina_web.assignments.models import Assignment
    return (Assignment.objects.exclude(absolute_path='')
            .order_by('-pk')
            .values_list('owner', flat=True)
            .first())


def _owner_base(owner_id):
    return os.path.normpath(os.path.join(settings.BASE_DIR, settings.MEDIA_ROOT,
                                         str(owner_id)))


def inner_path_process(inner_path, user_id):
    """Resolve an inner path to a full filesystem path for an owner directory.

    `user_id` is the owner whose directory is being browsed (see
    resolve_owner_id); the traversal guard ensures the result never escapes it.
    """
    base = _owner_base(user_id)
    if inner_path is not None:
        inner_path = slashes_decode(inner_path)
        full_path = os.path.normpath(os.path.join(base, inner_path))
        if not (full_path == base or full_path.startswith(base + os.sep)):
            raise ValueError("Path traversal detected — access denied")
    else:
        full_path = base
        inner_path = ""
    inner_path_hyphened = slashes_encode(inner_path)
    return inner_path, inner_path_hyphened, full_path

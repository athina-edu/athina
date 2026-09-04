import os
import re
from django.conf import settings


def slashes_encode(string):
    return re.sub("/", "|", string)


def slashes_decode(string):
    return re.sub("\|", "/", string)


def inner_path_process(inner_path, user_id):
    if inner_path is not None:
        inner_path = slashes_decode(inner_path)
        # Security: resolve path and verify it stays within the user's directory
        base = os.path.normpath(os.path.join(settings.BASE_DIR, settings.MEDIA_ROOT, str(user_id)))
        full_path = os.path.normpath(os.path.join(base, inner_path))
        if not (full_path == base or full_path.startswith(base + os.sep)):
            raise ValueError("Path traversal detected — access denied")
    else:
        full_path = os.path.join(settings.BASE_DIR, settings.MEDIA_ROOT, str(user_id))
        inner_path = ""
    inner_path_hyphened = slashes_encode(inner_path)
    return inner_path, inner_path_hyphened, full_path

import re
from django.conf import settings


def slashes_encode(string):
    return re.sub("/", "|", string)


def slashes_decode(string):
    return re.sub("\|", "/", string)


def inner_path_process(inner_path, user_id):
    inner_path = inner_path
    if inner_path is not None:
        inner_path = slashes_decode(inner_path)
        full_path = "%s/%s/%s/%s" % (settings.BASE_DIR, settings.MEDIA_ROOT, user_id, inner_path)
    else:
        full_path = "%s/%s/%s" % (settings.BASE_DIR, settings.MEDIA_ROOT, user_id)
        inner_path = ""
    inner_path_hyphened = slashes_encode(inner_path)
    return inner_path, inner_path_hyphened, full_path

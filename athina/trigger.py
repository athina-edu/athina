# Cross-container signalling between the athina web app and the grading daemon.
#
# The web container cannot run the grading daemon, but both containers share the
# `athina_files` volume. A GitLab push webhook (or an instructor's "Force rerun")
# therefore drops a small JSON file into a trigger directory, and the sleeping
# daemon wakes up and grades *just that student* instead of every assignment.
#
# One file per event, with a unique name written atomically, so two pushes that
# arrive at the same moment never overwrite each other. The daemon consumes
# (reads and deletes) each file exactly once.
#
# In production both containers mount the shared volume at the same path, so the
# defaults below line up. The web app resolves the volume through its own
# MEDIA_ROOT setting instead (see assignments/views.py::_trigger_daemon), which
# is why every helper takes an optional `root`.
import json
import os
import re
import secrets
import time

__all__ = (
    'TRIGGER_POLL_SECONDS',
    'consume_triggers',
    'full_pass_trigger_file',
    'triggers_pending',
    'trigger_dir',
    'write_full_pass_trigger',
    'write_student_trigger',
)

# How often the daemon looks for new events while it is idle.
TRIGGER_POLL_SECONDS = 2

# Where the shared volume is mounted inside both containers, and the default
# directory holding the per-student event files.
DEFAULT_SHARED_DIR = "/code/athina_files"
DEFAULT_TRIGGER_DIR = os.path.join(DEFAULT_SHARED_DIR, ".webhook_triggers")

# Event fields are attacker-controlled in the sense that they arrive as files on a
# shared volume: anything with write access to that volume could craft one. They
# are parsed into the types below and nothing else, so a hostile payload can only
# ever describe a different (course, assignment, student) to grade -- it cannot
# smuggle newlines into the logs, or a path that escapes the assignment root.
_MAX_ID = 2 ** 63 - 1


def _parse_id(value):
    """Coerce an event id to a non-negative int, or None when it is not one."""
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0 or parsed > _MAX_ID:
        return None
    return parsed


def _parse_directory(value):
    """Accept only a plain absolute path; strip anything that could forge a log line."""
    if not isinstance(value, str):
        return ""
    # Control characters (notably CR/LF) would let an attacker inject log entries.
    if re.search(r"[\x00-\x1f\x7f]", value):
        return ""
    if not os.path.isabs(value):
        return ""
    return value


def _sanitize_event(data):
    """Return a normalized student event, or None when the payload is unusable."""
    if not isinstance(data, dict) or data.get("type") != "student":
        return None
    course_id = _parse_id(data.get("course_id"))
    assignment_id = _parse_id(data.get("assignment_id"))
    user_id = _parse_id(data.get("user_id"))
    if course_id is None or assignment_id is None or user_id is None:
        return None
    return {
        "type": "student",
        "course_id": course_id,
        "assignment_id": assignment_id,
        "user_id": user_id,
        "directory": _parse_directory(data.get("directory")),
        "created": data.get("created") if isinstance(data.get("created"), (int, float)) else 0,
    }


def trigger_dir(root=None):
    """Directory holding one JSON file per webhook/force-rerun event.

    `root` is the shared volume as resolved by the caller (the web app derives
    it from MEDIA_ROOT); the daemon relies on the environment instead.
    """
    if root:
        return os.path.join(root, ".webhook_triggers")
    return os.environ.get("ATHINA_TRIGGER_DIR", DEFAULT_TRIGGER_DIR)


def full_pass_trigger_file(root=None):
    """Legacy marker meaning "run a pass over every assignment".

    Kept for backward compatibility with older web images and for anything that
    cannot name a single student (e.g. the grading DB being unreachable).
    """
    if not root and os.environ.get("ATHINA_TRIGGER_FILE"):
        return os.environ["ATHINA_TRIGGER_FILE"]
    return os.path.join(root or DEFAULT_SHARED_DIR, ".webhook_trigger")


def _atomic_write(path, payload):
    """Write `payload` to `path` so a reader never sees a half-written file.

    The file is created owner-only (0o600): event files name a student and an
    absolute path, and only the two containers sharing the volume need to read
    them. Directory creation also restricts the mode to avoid a world-readable
    signal channel.
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True, mode=0o700)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(payload)
    os.replace(tmp, path)


def write_student_trigger(course_id, assignment_id, user_id,
                          assignment_dir="", root=None):
    """Ask the daemon to grade one student on one assignment.

    `assignment_dir` is the assignment's absolute directory as reported by the
    web app's API, so a job can start even if the daemon's own poll of that API
    has not happened yet. Returns the path of the written event.
    """
    created = time.time()
    event = {
        "type": "student",
        "course_id": int(course_id),
        "assignment_id": int(assignment_id),
        "user_id": int(user_id),
        "directory": assignment_dir or "",
        "created": created,
    }
    path = os.path.join(trigger_dir(root), "student-%s-%s-%d-%s.json" % (
        event["assignment_id"], event["user_id"],
        int(created * 1000), secrets.token_hex(8)))
    _atomic_write(path, json.dumps(event))
    return path


def write_full_pass_trigger(root=None):
    """Ask the daemon for a pass over every assignment."""
    _atomic_write(full_pass_trigger_file(root), str(time.time()))
    return True


def triggers_pending(root=None):
    """True when at least one event (or the full-pass marker) is waiting.

    Read-only: unlike `consume_triggers`, nothing is deleted. The idle daemon uses
    this to wake early and leaves actual consumption to the dispatcher.
    """
    if os.path.isfile(full_pass_trigger_file(root)):
        return True
    try:
        names = os.listdir(trigger_dir(root))
    except OSError:
        return False
    return any(name.endswith('.json') for name in names)


def consume_triggers(root=None):
    """Consume pending events.

    Returns ``(events, full_pass)`` where ``events`` is a list of per-student
    jobs and ``full_pass`` says whether a whole pass was requested. Every
    consumed file is deleted, so an event is never acted on twice.
    """
    events = []
    directory = trigger_dir(root)
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        names = []

    for name in names:
        path = os.path.join(directory, name)
        if name.endswith(".tmp"):
            # A writer that died mid-write leaves a temporary behind. Remove it
            # only once it is clearly stale, so a concurrent writer is not
            # interrupted between writing its temp file and renaming it.
            try:
                if time.time() - os.path.getmtime(path) > 60:
                    os.remove(path)
            except OSError:
                pass
            continue
        if not name.endswith(".json"):
            continue
        data = None
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            # A truncated/corrupt event cannot be acted on; drop it.
            data = None
        try:
            os.remove(path)
        except OSError:
            pass
        event = _sanitize_event(data)
        if event is not None:
            events.append(event)

    full_pass = False
    path = full_pass_trigger_file(root)
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass
        full_pass = True

    return events, full_pass

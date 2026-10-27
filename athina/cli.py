# Console entry point for the athina CLI.
# This module exposes the `run()` function referenced by the `athina-cli`
# console script in setup.py. Using a proper entry point (instead of a
# `scripts=` file) makes the package buildable in any context (e.g., when
# Dependabot builds the editable package from a temp dir), since it no longer
# depends on a `bin/` file existing at build time.
import argparse
import atexit
import datetime
import json
import os
import signal
import sys
import time

# ---------------------------------------------------------------------------
# Load environment variables from a .env file BEFORE importing any athina
# modules.  The DB credentials in users.py are read at module import time,
# so they must be set first.  In Docker, these are provided via the
# `environment:` section of docker-compose.yml; for local development,
# create a .env file next to this script (or in the project root) with:
#   ATHINA_MYSQL_HOST=127.0.0.1
#   ATHINA_MYSQL_PORT=3307
#   ATHINA_MYSQL_USERNAME=athina
#   ATHINA_MYSQL_PASSWORD=<your-mysql-password>
# ---------------------------------------------------------------------------
def _load_dotenv():
    """Best-effort load of a .env file (KEY=VALUE, one per line)."""
    # Search in the same directory as this file and in the project root
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, '.env'),
        os.path.join(here, '..', '.env'),
        os.path.join(os.getcwd(), '.env'),
    ]
    for env_path in candidates:
        env_path = os.path.normpath(env_path)
        if os.path.isfile(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' not in line:
                        continue
                    key, _, value = line.partition('=')
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    # Don't override env vars that are already set
                    if key not in os.environ:
                        os.environ[key] = value
            break  # only load the first .env file found

_load_dotenv()

import dateutil
import filelock
import requests
import yaml
from dateutil.tz import tzlocal

from athina.canvas import *
from athina.configuration import *
from athina.git.git import *
from athina.gitlab_issues import GitLabIssues
from athina.logger import Logger
from athina.plagiarism import *
from athina.tester.tester import *
from athina.trigger import (TRIGGER_POLL_SECONDS, consume_triggers,
                            triggers_pending, write_full_pass_trigger)
from athina.url import *
from athina.users import *


# Module-level globals set by run(); defaults so they can be referenced/mocked.
DIR_PATH = None
ATHINA_WEB_URL = None
ARGS = None
lock = None
LOGGER = None
# Last assignment list fetched from the web API; used to resolve a webhook event's
# (course_id, assignment_id) to the assignment directory when the event does not
# carry one.
RUN_LIST = []
_run_list_fetched_at = None

# A GitLab push webhook reaches the *web* container, which cannot run the grading
# daemon. It drops a small JSON file per event into the shared athina_files volume;
# the daemon wakes and grades *just that student*. A full pass still runs every
# TRIGGER_WAIT_SECONDS as a safety net for anything the events did not cover.
#
# The loop must never be held up by grading. Each job therefore runs in its own
# forked process; `_running_jobs` tracks which (course, assignment, user) tuples
# are in flight so a second push for the same student is dropped rather than
# starting a duplicate run.
TRIGGER_WAIT_SECONDS = 60

# (course_id, assignment_id, user_id) -> (pid, started_at) for student jobs in flight.
_running_jobs = {}
# pid of the forked full-pass (safety-net poll) job, or None.
_poll_pid = None
# When the last full pass was started; gates the one-minute safety net.
_last_full_pass_at = None


def _job_key(course_id, assignment_id, user_id):
    return (int(course_id), int(assignment_id), int(user_id))


def _reap_finished_jobs():
    """Drop jobs whose forked process has exited.

    Non-blocking: this runs between events and between polls, so it must never
    wait on a child that is still testing.
    """
    global _poll_pid
    for key, (pid, _started) in list(_running_jobs.items()):
        try:
            done, _status = os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            # Already reaped (or never our child) — the slot is free.
            del _running_jobs[key]
            continue
        if done == pid:
            del _running_jobs[key]

    if _poll_pid is not None:
        try:
            done, _status = os.waitpid(_poll_pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            done = _poll_pid
        if done == _poll_pid:
            _poll_pid = None


def _job_is_running(course_id, assignment_id, user_id):
    _reap_finished_jobs()
    return _job_key(course_id, assignment_id, user_id) in _running_jobs


def _resume_child_logging():
    """Give a forked worker its own logger and drop inherited DB connections."""
    try:
        LOGGER.delete_logger()
        LOGGER.create_logger()
    except Exception:  # pragma: no cover - logging must not kill a worker
        pass
    try:
        Database.close_db()
    except Exception:  # pragma: no cover
        pass


def _wait_for_trigger(timeout):
    """Sleep up to `timeout` seconds, returning True if a trigger arrived.

    Polls the trigger directory (cheap: one listdir every 2s). Events are only
    *peeked* at here — they are left in place for the dispatcher to consume, so a
    delivery that lands just as this returns is never dropped.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        _reap_finished_jobs()
        if triggers_pending():
            return True
        time.sleep(TRIGGER_POLL_SECONDS)
    return False


def trigger_immediate_pass():
    """Ask the daemon for a full pass over every assignment.

    Used by operators and by anything that cannot name a single student. Normal
    webhook traffic uses per-student events instead (see athina.trigger).
    """
    write_full_pass_trigger()


def lock_process():
    # Allow only one instance. Use a lock file in a per-user directory (not a
    # publicly-writable location like /run/lock or /tmp, which is flagged as a
    # security issue, CWE-732).
    lock_dir = os.path.join(os.path.expanduser("~"), ".athina")
    os.makedirs(lock_dir, exist_ok=True)
    lock_file = filelock.FileLock(os.path.join(lock_dir, "athina.py.lock"))
    try:
        lock_file.acquire(timeout=10)
    except filelock.Timeout:
        sys.exit("Another instance of athina.py is running. If this is an error, delete athina.py.lock")
    # Ensure the lock is always released on exit, even on crashes
    atexit.register(lock_file.release)
    return lock_file


def signal_handler(signum, frame):
    LOGGER.logger.info('Ctrl+C detected. Terminating service...')
    lock.release()
    sys.exit(0)


def run():
    # Athina's directory
    global DIR_PATH, ATHINA_WEB_URL, ARGS, lock, LOGGER
    DIR_PATH = os.path.dirname(os.path.realpath(__file__))

    # Athina-Web url (one click run passes that when the first time setup occurs)
    ATHINA_WEB_URL = os.environ.get('ATHINA_WEB_URL', None)

    # Get command line parameters
    ARGS = parse_command_line()

    # Lock process so that duplicates won't run
    lock = lock_process()

    # Setup logger
    LOGGER = Logger()
    LOGGER.set_verbose(ARGS.verbose)  # this also creates LOGGER.logger

    # Capturing and terminating on service or non service interrupt
    signal.signal(signal.SIGINT, signal_handler)
    LOGGER.logger.info('Press Ctrl+C at any time to terminate Athina.')

    # Start main process
    if ARGS.service is True:
        # Run as a daemon. Each loop iteration:
        #   1. dispatches any webhook/force-rerun events (one student each), in
        #      forked workers, so the loop is never held up by grading;
        #   2. runs the full safety-net pass (also in a forked worker) when one is
        #      due — every TRIGGER_WAIT_SECONDS, so a missed webhook is still caught;
        #   3. sleeps until an event arrives or the minute is up.
        while True:
            service_tick()
            _wait_for_trigger(TRIGGER_WAIT_SECONDS)
    else:
        main()

    # Closing statement
    lock.release()


def parse_command_line():
    """
    Command line arguments
    :return:
    """
    parser = argparse.ArgumentParser(
        description='ATHINA - Automated Testing Homework Interface for N Assignments')
    parser.add_argument('-c', '--config', metavar='[config file|config dir]',
                        required=False, type=str, help='Configuration File')
    parser.add_argument('-v', '--verbose', required=False, help='Verbose mode, default False',
                        default=False, action='store_true')
    parser.add_argument('-s', '--service', required=False, help='Run Athina as a service',
                        default=False, action='store_true')
    parser.add_argument('-r', '--repo_url_testing', metavar='[repository url]', required=False,
                        help='Test a git config on a particular repo (this exclusively for testing configuration)',
                        type=str)
    parser.add_argument('-j', '--json', metavar='[json file]', required=False, type=str,
                        help='JSON list of folders with Athina cfg files and tests')
    parser.add_argument('-i', '--import_submissions', metavar='[json file]', required=False, type=str,
                        help='Import student submissions from a JSON file (for non-Canvas mode). '
                             'Format: {"course_id": N, "assignment_id": N, "due_date": "ISO", '
                             '"submissions": [{"user_id": N, "user_fullname": "...", '
                             '"secondary_id": "...", "repository_url": "..."}]}')
    args = parser.parse_args()
    return args


def _fetch_run_list():
    """Fetch the list of assignments to grade from the web API (or --config)."""
    run_list = []
    if ARGS.json is not None:
        try:
            run_list = request_url(ARGS.json, method="get", return_type="json")
        except requests.exceptions.ConnectionError:
            LOGGER.logger.error("Cannot connect to URL: %s" % ARGS.json)
        # request_url() returns {} for any non-JSON response (an HTTP error page,
        # an auth redirect, a DisallowedHost 400, ...). Without this check the
        # loop below simply runs zero times and grading silently never happens.
        if not run_list:
            LOGGER.logger.error(
                "No assignments returned by %s. Check that the URL is reachable "
                "from this container and that its host is in Django ALLOWED_HOSTS."
                % ARGS.json)
            return None
        LOGGER.logger.info("Fetched %d assignment(s) from %s" % (len(run_list), ARGS.json))
    elif ARGS.config is not None:
        run_list.append({'directory': ARGS.config})
    else:
        raise SyntaxError("You need to provide either --config or --json.")
    return run_list


def main(run_list=None):
    """Run a full pass over every assignment (the safety-net poll)."""
    if run_list is None:
        run_list = _fetch_run_list()
        if not run_list:
            return

    global RUN_LIST
    RUN_LIST = run_list

    # Iterate through each assignment
    user_data = Database(logger=LOGGER)
    for run_record in run_list:
        process_assignment(run_record, user_data)


def process_assignment(run_record, user_data=None, only_user_id=None):
    """Grade one assignment, or (when `only_user_id` is given) just one student.

    `only_user_id` is what a webhook asks for: only that student's repository is
    pulled and tested, so a push does not trigger a pass over the whole course.
    """
    # Pull the latest from the remote repo for this assignment directory.
    # The online version is the ground truth — local edits are discarded.
    _pull_assignment_repo(run_record.get('directory', ''))

    # Build configuration object
    configuration = Configuration(logger=LOGGER)
    try:
        configuration.load_configuration(run_record['directory'])
    except (ValueError, TypeError, yaml.YAMLError):
        LOGGER.logger.error("Error reading the configuration file. Probably a value is empty (e.g., course_id=),"
                            "missing or incorrect (e.g., no quotes are necessary for strings).")
        return  # in case a use forgets and gives empty values in their config

    # If the API provided course_id/assignment_id, override the YAML values.
    # This is how db-input-mode works: IDs live in the Django model, not the YAML.
    if 'course_id' in run_record and run_record['course_id'] is not None:
        configuration.course_id = int(run_record['course_id'])
    if 'assignment_id' in run_record and run_record['assignment_id'] is not None:
        configuration.assignment_id = int(run_record['assignment_id'])

    configuration.athina_web_url = ATHINA_WEB_URL

    # If --import_submissions was provided, load student data from the JSON file
    if ARGS.import_submissions is not None:
        _load_submissions_from_file(configuration)

    if user_data is None:
        user_data = Database(logger=LOGGER)

    # Starting statement
    if only_user_id is None:
        LOGGER.logger.info("Processing...")
    else:
        LOGGER.logger.info("Processing student %s only..." % only_user_id)

    core_iteration(configuration, user_data, only_user_id=only_user_id)

    del configuration
    LOGGER.logger.info("Processing done.")


def _spawn_job(name, target, *args):
    """Fork a worker so the trigger/poll loop keeps running while it tests.

    Returns the child pid, or None if forking failed (grading is then skipped for
    that job rather than blocking the loop).
    """
    try:
        pid = os.fork()
    except OSError as exc:
        LOGGER.logger.error("Could not fork a worker for %s: %s" % (name, exc))
        return None

    if pid == 0:  # child
        try:
            _resume_child_logging()
            target(*args)
        except BaseException as exc:  # never let a worker take down the daemon
            try:
                LOGGER.logger.error("%s failed: %s" % (name, exc))
            except Exception:
                pass
        finally:
            try:
                Database.close_db()
            except Exception:
                pass
            os._exit(0)
    return pid


def _run_student_job(event):
    """Worker body: grade one student on one assignment."""
    process_assignment({'directory': event['directory'],
                        'course_id': event['course_id'],
                        'assignment_id': event['assignment_id']},
                       only_user_id=event['user_id'])


def _refresh_run_list():
    """(Re)load the assignment list from the web API, throttled.

    Only needed to resolve a trigger that did not carry a directory (e.g. an
    older web image). Throttled because the API's serializer runs a git fetch per
    assignment, which is far too expensive to repeat on every tick.

    The attempt is stamped even when it fails, so an unreachable web app is not
    hammered once per event; the previous list stays in use.
    """
    global RUN_LIST, _run_list_fetched_at
    now = time.time()
    if _run_list_fetched_at is not None and \
            (now - _run_list_fetched_at) < TRIGGER_WAIT_SECONDS:
        return RUN_LIST
    _run_list_fetched_at = now
    run_list = _fetch_run_list()
    if run_list:
        RUN_LIST = run_list
    return RUN_LIST


def _directory_for_event(event):
    """Resolve an event's assignment directory from the cached run list.

    The run list comes from the web API and is the authoritative form (it always
    carries a trailing slash); prefer it over the directory recorded in the event
    when it is already cached. Deliberately does not fetch: dispatch must stay
    fast and must not depend on the API being reachable.
    """
    for record in RUN_LIST:
        if not record.get('directory'):
            continue
        if record.get('course_id') is None or record.get('assignment_id') is None:
            continue
        try:
            if int(record['course_id']) == int(event['course_id']) and \
                    int(record['assignment_id']) == int(event['assignment_id']):
                return record['directory']
        except (TypeError, ValueError):
            continue
    return ''


def dispatch_triggers():
    """Start a worker for every pending webhook/force-rerun event.

    Each event is one student. A student who is already being graded is skipped,
    so repeated pushes never start duplicate runs. Failures are logged and the
    event dropped — the one-minute poll is the safety net.
    """
    events, full_pass = consume_triggers()
    # Drop jobs whose process has exited first, so a finished student does not
    # look like one that is still being graded.
    _reap_finished_jobs()

    if full_pass:
        LOGGER.logger.info('Full-pass trigger received — scheduling a complete pass.')
        _request_full_pass()

    for event in events:
        key = _job_key(event['course_id'], event['assignment_id'], event['user_id'])
        if key in _running_jobs:
            LOGGER.logger.info("Student %s on course %s/assignment %s is already being graded — "
                               "skipping duplicate trigger." % (event['user_id'],
                                                                event['course_id'],
                                                                event['assignment_id']))
            continue
        directory = _directory_for_event(event) or event.get('directory') or ''
        if not directory:
            LOGGER.logger.warning("Trigger for student %s (course %s/assignment %s) has no assignment "
                                  "directory; scheduling a full pass instead." %
                                  (event['user_id'], event['course_id'], event['assignment_id']))
            _request_full_pass()
            continue

        event['directory'] = directory
        pid = _spawn_job("student %s job" % key[2], _run_student_job, event)
        if pid is not None:
            _running_jobs[key] = (pid, time.time())
            LOGGER.logger.info("Webhook trigger — started grading student %s (course %s/assignment %s)."
                               % (event['user_id'], event['course_id'], event['assignment_id']))


def _request_full_pass():
    """Mark the full pass as due now; `service_tick` starts it when it is safe."""
    global _last_full_pass_at
    _last_full_pass_at = None


def _schedule_full_pass():
    """Start the safety-net pass in a worker, unless one is already running."""
    global _poll_pid, _last_full_pass_at
    _reap_finished_jobs()
    if _poll_pid is not None:
        LOGGER.logger.debug("A full pass is already running — not starting another.")
        return
    pid = _spawn_job("full pass", main)
    if pid is not None:
        _poll_pid = pid
        _last_full_pass_at = time.time()


def _full_pass_due():
    """True when the one-minute safety-net pass is due and can be started.

    Deferred while any student job is in flight: the full pass overrides a live
    tester lock when it sees a new commit, which would re-grade a student a
    triggered job is already working on.
    """
    if _poll_pid is not None:
        return False
    _reap_finished_jobs()
    if _running_jobs:
        return False
    if _last_full_pass_at is None:
        return True
    return (time.time() - _last_full_pass_at) >= TRIGGER_WAIT_SECONDS


def service_tick():
    """One iteration of the daemon loop.

    Dispatch pending per-student events first, so a push is graded immediately
    and the loop returns to the trigger wait without being held up. The full pass
    only runs when TRIGGER_WAIT_SECONDS has elapsed, so a burst of webhooks never
    turns into a burst of whole-course passes.
    """
    dispatch_triggers()
    if _full_pass_due():
        _schedule_full_pass()
    _reap_finished_jobs()


def _pull_assignment_repo(directory):
    """Pull the latest from the remote for an assignment's git repo.
    The online version is always the ground truth — local edits are discarded.

    Several workers can run concurrently (one per triggered student, plus the
    safety-net pass), and they all share the assignment checkout. A per-directory
    lock serialises the pull so two `git checkout -- .` / `git pull` calls never
    race over the same index.
    """
    if not directory or not os.path.isdir(directory):
        return
    git_dir = os.path.join(directory, '.git')
    if not os.path.isdir(git_dir):
        return  # not a git repo (e.g., manually created directory)
    pull_lock = filelock.FileLock(os.path.join(git_dir, "athina-pull.lock"))
    try:
        pull_lock.acquire(timeout=120)
    except filelock.Timeout:
        if LOGGER and LOGGER.logger:
            LOGGER.logger.warning("Could not lock %s for a pull; skipping this pull." % directory)
        return
    try:
        _do_pull_assignment_repo(directory)
    finally:
        try:
            pull_lock.release()
        except Exception:
            pass


def _do_pull_assignment_repo(directory):
    try:
        import git as _git
        repo = _git.Repo(directory)
        origin = repo.remotes.origin
        # Discard local changes and pull latest
        repo.git.checkout('--', '.')
        origin.pull()
        if LOGGER and LOGGER.logger:
            LOGGER.logger.info("Pulled latest from remote for: %s" % directory)
    except Exception as e:
        if LOGGER and LOGGER.logger:
            LOGGER.logger.warning("Could not pull assignment repo %s: %s" % (directory, str(e)))


def _load_submissions_from_file(configuration):
    """
    Load student submissions from a JSON file passed via --import_submissions.
    The JSON format is:
        {
          "course_id": N,
          "assignment_id": N,
          "due_date": "2026-12-31T23:59:59",   // optional
          "submissions": [
            {
              "user_id": 123,
              "user_fullname": "Jane Doe",
              "secondary_id": "jane@example.com",
              "repository_url": "https://gitlab.com/jane/assignment.git"
            }
          ]
        }
    """
    import_path = ARGS.import_submissions
    if not import_path or not isinstance(import_path, str):
        return

    LOGGER.logger.info("Importing submissions from %s ..." % import_path)
    with open(import_path, 'r') as f:
        data = json.load(f)

    course_id = data.get("course_id", configuration.course_id)
    assignment_id = data.get("assignment_id", configuration.assignment_id)
    due_date = data.get("due_date", None)
    submissions = data.get("submissions", [])

    # Override config IDs if provided in the import file
    configuration.course_id = course_id
    configuration.assignment_id = assignment_id

    created, updated = import_submissions(course_id, assignment_id, submissions, due_date)
    LOGGER.logger.info("Import complete: %d created, %d updated." % (created, updated))


def core_iteration(configuration, user_data, only_user_id=None):
    """Grade one assignment.

    `only_user_id` restricts the run to a single student. A webhook uses it so a
    push grades that student only — the rest of the course is untouched. The
    one-minute safety-net pass calls this with `only_user_id=None`.
    """
    # Determine output mode: Canvas or GitLab Issues
    if configuration.output_method == "gitlab_issues":
        e_learning = GitLabIssues(configuration, LOGGER)
        LOGGER.logger.info("Output mode: GitLab Issues (project %s)" % configuration.gitlab_project_id)
    else:
        e_learning = Canvas(configuration, LOGGER)

    # --- INPUT: gather student submissions ---
    # A single-student run must not pull the whole roster from Canvas (that is
    # expensive and would undo the point of a targeted webhook run). The row
    # already exists in the grading DB when a push arrives.
    use_canvas_input = (configuration.input_method == "canvas" and
                        configuration.auth_token != "" and
                        only_user_id is None)

    if only_user_id is not None:
        LOGGER.logger.info("Targeted run for student %s (course %s, assignment %s)" % (
            only_user_id, configuration.course_id, configuration.assignment_id))
        stored_due = load_key_from_assignment_data(configuration.course_id,
                                                   configuration.assignment_id, "due_date")
        configuration.due_date = (dateutil.parser.parse(stored_due) if stored_due is not None
                                  else dateutil.parser.parse("2050-01-01 00:00:00"))
    elif use_canvas_input:
        # For mixed-mode (canvas input + gitlab output), we need a Canvas object
        # for input operations even if the output adapter is GitLabIssues.
        if configuration.output_method == "gitlab_issues":
            canvas_input = Canvas(configuration, LOGGER)
        else:
            canvas_input = e_learning  # same object when both are Canvas

        LOGGER.logger.debug("Retrieving submission list from elearning platform...")
        canvas_input.get_all_submissions()
        LOGGER.logger.debug("Retrieved!")

        # Getting additional information from e-learning platform
        if len(return_all_students(configuration.course_id, configuration.assignment_id)) > 0 and \
                canvas_input.needs_update:  # this helps reduce API calls
            LOGGER.logger.debug("Retrieving user info from elearning platform...")
            user_data = canvas_input.get_additional_user_info(user_data)
            LOGGER.logger.debug("Retrieved!")
            if configuration.enforce_due_date:
                configuration.due_date = canvas_input.get_assignment_due_date()
            else:
                configuration.due_date = dateutil.parser.parse("2050-01-01 00:00:00")  # a day in the future
            canvas_input.update_last_update()
        # Check if more than N times the same URL in usrdb
        elif len(return_all_students(configuration.course_id, configuration.assignment_id)) > 0:
            LOGGER.logger.debug("Checking for duplicate urls...")
            user_data.check_duplicate_url(same_url_limit=configuration.same_url_limit,
                                          course_id=configuration.course_id,
                                          assignment_id=configuration.assignment_id)
            LOGGER.logger.debug("Checked!")
    elif configuration.input_method == "db":
        # Submissions already exist in the database (e.g., pre-populated by athina-web
        # or imported via --import_submissions).  Nothing to fetch from an API.
        student_count = len(return_all_students(configuration.course_id, configuration.assignment_id))
        LOGGER.logger.info("Input mode: database (%d students found)" % student_count)

        # Load due date from assignment data if available, else use a far-future default
        stored_due = load_key_from_assignment_data(configuration.course_id,
                                                    configuration.assignment_id, "due_date")
        if stored_due is not None:
            configuration.due_date = dateutil.parser.parse(stored_due)
        elif configuration.enforce_due_date:
            # No due date stored and enforcement is on — default to far future
            configuration.due_date = dateutil.parser.parse("2050-01-01 00:00:00")
        else:
            configuration.due_date = dateutil.parser.parse("2050-01-01 00:00:00")

        # Duplicate URL check still applies (full passes only: on a targeted run the
        # single student's own URL cannot have become a duplicate since the last
        # pass, and scanning the whole roster would defeat the point).
        if student_count > 0 and only_user_id is None:
            LOGGER.logger.debug("Checking for duplicate urls...")
            user_data.check_duplicate_url(same_url_limit=configuration.same_url_limit,
                                          course_id=configuration.course_id,
                                          assignment_id=configuration.assignment_id)
            LOGGER.logger.debug("Checked!")

    # Build Repository Object
    repository = Repository(LOGGER, configuration, e_learning)

    # Build Tester Object
    tester = Tester(user_data, LOGGER, configuration, e_learning, repository)

    if ARGS.repo_url_testing is not None:
        # Creating tmp user and simulating the test for the provided repository
        LOGGER.logger.debug("TEST")
        # Use the SAME ids for the cleanup, the insert and the read below.
        # This block used to hardcode course_id=1/assignment_id=1 on insert while
        # reading back with configuration.course_id/assignment_id, so unless the
        # config happened to say 1 and 1 the freshly created row was never found
        # and the run died with UsersDoesNotExist. The shipped example config says
        # 0 and 0, so `--config config-examples/ --repo_url_testing=...` could
        # never work.
        course_id = configuration.course_id
        assignment_id = configuration.assignment_id
        try:
            obj = return_a_student(course_id, assignment_id, 1)
            obj.delete_instance()
        except Users.DoesNotExist:
            # No existing user to clean up; a fresh one is created below.
            pass
        Users.create(user_id=1,
                     course_id=course_id,
                     assignment_id=assignment_id,
                     repository_url=ARGS.repo_url_testing,
                     url_date=datetime.datetime(1, 1, 1, 0, 0).replace(tzinfo=None),
                     new_url=True,
                     commit_date=datetime.datetime(1, 1, 1, 0, 0).replace(tzinfo=None))
        LOGGER.set_verbose(True)
        LOGGER.set_debug(True)
        repository.check_repository_changes(1)
        tester.process_student_assignment(1)
        LOGGER.logger.info("Single repository testing completed.")
        sys.exit(0)  # This is used for testing so no further processing is necessary
    elif only_user_id is not None:
        # Webhook-triggered run: grade just this student on this assignment.
        tester.start_testing_db(user_ids=[only_user_id])
    else:
        # Start testing changed records (new or updated) if any exist
        tester.start_testing_db()

    # Initiate plagiarism checks
    # (full passes only: the daily check covers every recently graded submission,
    # so running it from a single-student trigger would only duplicate work)
    if only_user_id is None and \
            datetime.datetime.now(tzlocal()).replace(tzinfo=None).hour == configuration.check_plagiarism_hour:
        plagiarism_checks_on_users(LOGGER, configuration, e_learning)

    # In case this script is run as another user the repo needs to be also set to be editable by anyone
    try:
        # 0o700: owner rwx only. Restrict access to the owner to avoid
        # py/overly-permissive-file (CWE-732). The sandboxed test process runs
        # as the same user, so owner-only access is sufficient.
        os.chmod("%s/repodata%s" % (configuration.config_dir, configuration.assignment_id), 0o700)
    except FileNotFoundError:
        # No repodata dir yet; nothing to chmod.
        pass

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
#   ATHINA_MYSQL_PASSWORD=athina_dev_pass
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
from athina.url import *
from athina.users import *


# Module-level globals set by run(); defaults so they can be referenced/mocked.
DIR_PATH = None
ATHINA_WEB_URL = None
ARGS = None
lock = None
LOGGER = None


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
        # Run as a daemon
        while True:
            main()
            time.sleep(60)
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


def main():
    # Build the list of assignments to check (Athina Web = json format, command line = 1 assignment only)
    run_list = []
    if ARGS.json is not None:
        try:
            run_list = request_url(ARGS.json, method="get", return_type="json")
        except requests.exceptions.ConnectionError:
            LOGGER.logger.error("Cannot connect to URL: %s" % ARGS.json)
    elif ARGS.config is not None:
        run_list.append({'directory': ARGS.config})
    else:
        raise SyntaxError("You need to provide either --config or --json.")

    # Iterate through each assignment
    user_data = Database(logger=LOGGER)
    for run_record in run_list:
        # Build configuration object
        configuration = Configuration(logger=LOGGER)
        try:
            configuration.load_configuration(run_record['directory'])
        except (ValueError, TypeError, yaml.YAMLError):
            LOGGER.logger.error("Error reading the configuration file. Probably a value is empty (e.g., course_id=),"
                                "missing or incorrect (e.g., no quotes are necessary for strings).")
            continue  # in case a use forgets and gives empty values in their config

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

        # Starting statement
        LOGGER.logger.info("Processing...")

        core_iteration(configuration, user_data)

        del configuration
        LOGGER.logger.info("Processing done.")


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


def core_iteration(configuration, user_data):
    # Determine output mode: Canvas or GitLab Issues
    if configuration.output_method == "gitlab_issues":
        e_learning = GitLabIssues(configuration, LOGGER)
        LOGGER.logger.info("Output mode: GitLab Issues (project %s)" % configuration.gitlab_project_id)
    else:
        e_learning = Canvas(configuration, LOGGER)

    # --- INPUT: gather student submissions ---
    use_canvas_input = (configuration.input_method == "canvas" and configuration.auth_token != "")

    if use_canvas_input:
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

        # Duplicate URL check still applies
        if student_count > 0:
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
        try:
            obj = return_a_student(configuration.course_id, configuration.assignment_id, 1)
            obj.delete_instance()
        except Users.DoesNotExist:
            # No existing user to clean up; a fresh one is created below.
            pass
        Users.create(user_id=1,
                     course_id=1,
                     assignment_id=1,
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
    else:
        # Start testing changed records (new or updated) if any exist
        tester.start_testing_db()

    # Initiate plagiarism checks
    if datetime.datetime.now(tzlocal()).replace(tzinfo=None).hour == configuration.check_plagiarism_hour:
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

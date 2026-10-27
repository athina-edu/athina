# AGENTS.md — Athina

This file gives an agent a quick lay of the land: what the project is, how it's
organized, how it's tested, and how the code is written. It intentionally omits
anything private or sensitive (credentials, tokens, secrets).

---

## What this project is

**Athina** is a formative-assessment microservice for programming assignments.
It:

1. Reads a YAML assignment configuration (tests, weights, due date, credentials).
2. Pulls student submissions from an e-learning platform (Canvas) as git repo URLs,
   or reads them from its own database when the web dashboard manages them.
3. Clones each repo, runs safety checks, sandboxes the code in Docker,
   and executes instructor-provided test scripts.
4. Parses the last line of test output as a grade (0–100). Test scripts may also
   report individual results — `#ATHINA# PASS | name: detail` markers or the
   `athina-test.sh` helper — which are stored per test for the dashboard's override UI.
5. Submits the grade and feedback back to the student: a GitLab issue in their repo,
   or Canvas.
6. Optionally runs plagiarism checks (CopyDetect, local similarity) and reports scores.

There is also a Django web dashboard (`athina_web/`, in this repo) for managing
multiple courses/instructors on one machine — including due dates, per-student
score overrides with notes, repo links, and AI guidance — plus a one-click Docker
deployment (`athina-one-click-run/`).

---

## Repository layout

```
athina/                  # Main Python package (the microservice)
  __init__.py            # empty
  cli.py                 # CLI entry point: argparse, process lock, daemon loop
  configuration.py       # Configuration class: all assignment params + YAML loading
  canvas.py              # Canvas e-learning integration (submissions, grades, files)
  users.py               # Peewee ORM models (Users, AssignmentData, TestResult) + DB backend
  test_report.py         # Parses per-test results (#ATHINA# markers / athina-test helper)
  test_helpers/
    athina-test.sh       # shell helper for declaring individual test results
  file_functions.py      # copy_dir / rm_dir helpers
  logger.py              # Logger class (file + console + rotating handlers)
  llm.py                 # LLM feedback generation (OpenAI-compatible)
  gitlab_issues.py       # GitLab issue output adapter
  moss.py                # Plagiarism integration (CopyDetect; module name is historical)
  url.py                 # request_url() HTTP helper (requests wrapper)
  git/
    git.py               # Repository class: clone/pull, commit dates, chain-of-responsibility handlers
    gitlab.py            # GitLab webhook + private-repo checks
  tester/
    tester.py            # Tester class: orchestrates per-student testing
    docker.py            # docker build/run sandboxing

athina_web/              # Django web dashboard (lives in this repo; own Dockerfile.web)
  accounts/              # roles, profiles (git/LLM credentials, display timezone), middleware
  assignments/           # courses/assignments, reports, per-test overrides, provisioning
  filemanager/           # in-browser file browser
  templates/             # HTML templates

tests/                   # pytest suite (see "Testing" below)
config-examples/         # Example assignment config + Dockerfile + test scripts
Dockerfile               # CLI image
Dockerfile.web           # Web dashboard image
```

Note: the console entry point is `athina.cli:run` (declared in `setup.py`); there is
no `bin/` script any more.

---

## How it's organized / architecture

- **Single Python package** (`athina/`) with modules grouped by responsibility:
  configuration, e-learning integration, git handling, sandboxing, plagiarism,
  logging, and HTTP helpers.
- **Entry point** is `athina.cli:run` (console script `athina-cli`). It parses CLI
  args, acquires a process lock, sets up the logger, and runs `main()` →
  `core_iteration()` per assignment.
- **Core objects** are wired together in `core_iteration()`:
  `Configuration` → `Canvas`/`GitLabIssues` (output adapter) → `Repository` → `Tester`.
- **`Configuration`** is a "hyper object" holding all shared execution parameters
  (class attributes with defaults), loaded from a YAML file via `load_configuration()`.
  Per-assignment `.env` files written by the web app supply credentials and some
  settings (git, LLM, output method).
- **`Tester`** forks/processes each student, runs each test script in a sandbox,
  aggregates weighted grades, and submits results. It also stores a per-test
  breakdown in `TestResult` for the dashboard's override UI.
- **`Repository`** uses a **Chain of Responsibility** pattern (nested `Handler`
  classes) to decide whether a student's repo should be tested (empty repo,
  duplicate URL, private-repo check, new URL, webhook, pull).
- **Webhook wake-ups** (`athina/trigger.py`): a push webhook (handled by the web
  app) writes one JSON event per `(course, assignment, student)` into
  `athina_files/.webhook_triggers/`, and the daemon grades **only that student**.
  The web app writes one event per assignment — a group shares one repository, so
  one job grades every member. The daemon wakes on new events (polling the
  directory every 2s while idle) and starts each job in a **forked worker**, so the
  trigger loop and the one-minute safety-net pass are never blocked by testing. A
  job is skipped when that `(course, assignment, student)` is already in flight.
  The legacy `.webhook_trigger` file still means "run a full pass", and the full
  pass is deferred while any triggered job is running.
  `webhook_fallback_interval` still forces a periodic pull so a missed delivery
  cannot leave a student ungraded.
- **Database** uses **Peewee ORM** with a pluggable backend: SQLite (dev/tests)
  or MySQL (production). Backend is chosen via env vars (`ATHINA_DB`, `ATHINA_TEST_MODE`).
  Tables: `users`, `assignmentdata` (key/value per assignment, incl. `due_date`),
  `testresults` (per-test scores + instructor overrides).
- **Due dates** are stored in `assignmentdata.due_date` as naive **UTC**, set from
  the web UI (which converts from the instructor's display timezone) and compared
  against the student's latest commit date.
- **Sandboxing** is Docker (`tester/docker.py`). Every assignment must ship a
  Dockerfile next to its config; the engine builds it and runs tests inside.
  Containers run as root; file ownership is reclaimed only when the daemon itself
  is non-root (as root the chown was a no-op, so it is skipped).
- **Logging** is centralized in `logger.py`; the logger object can be deleted and
  recreated (workaround for multiprocessing/pickling).

---

## How it's tested

- **Framework:** pytest (see `pytest.ini`), with `unittest.TestCase`-style classes
  in the test files. Test discovery: `test_*.py` / `Test*` / `test_*`.
- **Run all tests:**
  ```bash
  pytest                 # or: ./run_tests.sh
  ```
- **Two DB modes** (handled in `tests/conftest.py`):
  - **SQLite (default, local dev):** no DB setup needed. Set via `ATHINA_DB=sqlite`
    or `ATHINA_TEST_MODE=1`. Uses a per-process file DB at `/tmp/athina_test_<pid>.db`.
  - **MySQL (production-like):** `ATHINA_USE_DOCKER_MYSQL=1 pytest` (or
  `ATHINA_USE_DOCKER_MYSQL=1 pipenv run pytest` if using the Pipfile).
    `conftest.py` auto-starts a `mysql:8.0` Docker container if none is available.
- **Test runner script** `run_tests.sh` supports flags: `-d/--docker-mysql`,
  `-v/--verbose`, `-c/--coverage`, and a positional test path.
- **Markers** (in `pytest.ini`): `slow`, `requires_docker`, `requires_mysql`.
  `--strict-markers` is enabled, so new markers must be registered.
- **Key test helpers** live in `tests/test_athina.py`:
  - `create_logger()` — builds a verbose/debug logger.
  - `create_test_config()` — writes a fake `tests/` dir + `Dockerfile` + copies the
    local `tests/git` fixture repo into a temp dir.
  - `create_fake_user_db()` — seeds a set of static user scenarios (normal, wrong
    URL, duplicate URLs, no URL, past-due, no-repo). **Do not change** — many tests
    depend on it.
- **Sandbox fallback in tests:** when `ATHINA_TEST_MODE=1`, docker code
  falls back to running the test script locally so tests pass without Docker.
- **CI:** Semaphore (badge in README) + SonarCloud for quality gates. CI uses
  Python 3.12+ (3.14 not yet available on Semaphore).
- **Requirements:** Python 3.14+ locally (pipenv), 3.12+ in CI, Docker for MySQL
  tests/production, pipenv for deps.

---

## How the code is written (style & conventions)

- **Python 3**, modern-ish but pragmatic. Uses f-strings in newer code and
  `%`-formatting in older code (both appear).
- **Imports:** modules use `from athina.x import *` heavily (wildcard imports) to
  pull in shared helpers. Each module defines `__all__` to control what's exported.
- **Classes over functions** for the main components (`Configuration`, `Canvas`,
  `Repository`, `Tester`, `Logger`, `Plagiarism`, `Database`).
- **Class attributes as defaults:** `Configuration` and others declare defaults as
  class-level attributes, then override via instance/config loading.
- **Docstrings:** present on classes and some methods; many methods rely on inline
  comments instead. Comments explain *why* (e.g., security, multiprocessing quirks).
- **Naming:** `snake_case` for functions/variables, `CamelCase` for classes,
  private helpers prefixed with `_` (e.g., `_run_test`, `_trim_test_output`).
- **Error handling:** broad `try/except` with logging via `self.logger.logger.*`
  (info/warning/error/debug). Some `except Exception` blocks are intentionally broad.
- **Subprocess usage:** git/docker operations shell out via
  `subprocess.Popen`/`subprocess.run` rather than using libraries where convenient.
- **Security-conscious:** git credentials only sent to the configured `git_url`
  domain; student code is sandboxed; tests are force-timed-out; hidden files
  (e.g., `.git`) are excluded when copying student code.
- **`FIXME`/`TODO` comments** are used to flag known limitations.

---

## Common commands

```bash
# Run the test suite (SQLite, local)
pytest
# Run a single test file
pytest tests/test_canvas.py
# Run with coverage
pytest --cov=athina --cov-report=html
# Run with Docker MySQL (production-like)
ATHINA_USE_DOCKER_MYSQL=1 pytest

# Run the CLI against an example config
athina-cli --config config-examples/ --repo_url_testing=https://github.com/athina-edu/testing.git

# Run against a live web dashboard
athina-cli --json http://localhost:8000/assignments/api/ -s -v

# Rebuild the images after changing engine or web code (a running container keeps
# its old code until recreated -- this has silently masked fixes before)
docker build -t athinaedu/athina:latest .
docker build -f Dockerfile.web -t athinaedu/athina-web:latest .
docker compose up -d --force-recreate athina athina-web
```

See `DEVELOPMENT.md` for the full dev-environment guide (ports, DB reset,
troubleshooting).

---

## Handling test failures (important)

When a test fails, do **not** reach for an exception, skip, or workaround as a
first move. Follow this process:

1. **Determine the root cause in detail.** Reproduce the failure, read the
   failing assertion and the surrounding code, and trace exactly why it fails.
   Identify the specific line, state, or assumption that is wrong.
2. **Fix the actual cause.** The expected outcome is a real fix to the code or
   test so the suite passes legitimately.
3. **Exceptions are the absolute last resort.** Only consider an exception
   (e.g., `@unittest.skip`, a marker like `requires_docker`, a modified
   assertion, or a test-mode fallback) after the root cause is fully understood
   and a genuine fix is not possible or not appropriate.
4. **Argue the exception.** Any exception must be justified in writing: what the
   root cause is, why it cannot be fixed, and what the exception buys us.
5. **Run a devil's advocate review.** Before an exception is accepted, initiate a
   devil's advocate agent to argue *against* it — challenging whether the root
   cause is truly understood, whether a real fix exists, and whether the
   exception hides a real bug. The exception is only acceptable if the devil's
   advocate cannot refute it.

In short: understand the failure, fix it for real, and treat any exception as a
decision that must survive adversarial scrutiny.

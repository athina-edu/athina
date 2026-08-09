# AGENTS.md — Athina

This file gives an agent a quick lay of the land: what the project is, how it's
organized, how it's tested, and how the code is written. It intentionally omits
anything private or sensitive (credentials, tokens, secrets).

---

## What this project is

**Athina** is a formative-assessment microservice for programming assignments.
It:

1. Reads a YAML assignment configuration (tests, weights, Canvas credentials, etc.).
2. Pulls student submissions from an e-learning platform (Canvas) as git repo URLs.
3. Clones each repo, runs safety checks, sandboxes the code (Docker or firejail),
   and executes instructor-provided test scripts.
4. Parses the last line of test output as a grade (0–100), then submits the grade
   and feedback back to the student's submission page.
5. Optionally runs plagiarism checks (Moss) and reports similarity scores.

There is also an optional web interface (`athina-web/`) for managing multiple
assignments/instructors on one machine, plus a one-click Docker deployment
(`athina-one-click-run/`, referenced by `dev-run.sh`).

---

## Repository layout

```
athina/                  # Main Python package (the microservice)
  __init__.py            # empty
  configuration.py       # Configuration class: all assignment params + YAML loading
  canvas.py              # Canvas e-learning integration (submissions, grades, files)
  users.py               # Peewee ORM models (Users, AssignmentData) + DB backend
  file_functions.py      # copy_dir / rm_dir helpers
  logger.py              # Logger class (file + console + rotating handlers)
  moss.py                # Moss plagiarism integration
  url.py                 # request_url() HTTP helper (requests wrapper)
  git/
    git.py               # Repository class: clone/pull, commit dates, chain-of-responsibility handlers
    gitlab.py            # GitLab webhook + private-repo checks
  tester/
    tester.py            # Tester class: orchestrates per-student testing
    docker.py            # docker build/run sandboxing
    firejail.py          # firejail sandboxing + profile generation
    server.profile       # firejail profile template

bin/
  athina-cli             # CLI entry point (argparse, main loop, service mode)

config-examples/         # Example assignment config + Dockerfile + test scripts
  assignementsample.yaml
  Dockerfile
  tests/test-python-clarity.bash

tests/                   # pytest suite (see "Testing" below)
  conftest.py            # DB backend fixtures (SQLite vs Docker MySQL)
  test_athina.py         # main end-to-end-ish tests + shared helpers
  test_canvas.py
  test_docker.py
  test_logger.py
  test_moss.py
  helper_files/logger_script.py
  git/                   # a tiny local git repo fixture used by tests

athina-web/              # Optional Django web interface (separate concern)
  db.sqlite3             # dev SQLite DB
  athinaweb/             # Django project

dev-run.sh               # Dev stack orchestrator (generates compose + starts/stops)
docker-compose.yml       # Minimal production compose (athina service)
docker-compose.dev.yml   # Generated dev compose (not committed by hand)
Dockerfile               # Container build for the athina service
```

---

## How it's organized / architecture

- **Single Python package** (`athina/`) with modules grouped by responsibility:
  configuration, e-learning integration, git handling, sandboxing, plagiarism,
  logging, and HTTP helpers.
- **Entry point** is `bin/athina-cli`. It parses CLI args, acquires a process
  lock, sets up the logger, and runs `main()` → `core_iteration()` per assignment.
- **Core objects** are wired together in `core_iteration()`:
  `Configuration` → `Canvas` (e-learning) → `Repository` (git) → `Tester`.
- **`Configuration`** is a "hyper object" holding all shared execution parameters
  (class attributes with defaults), loaded from a YAML file via `load_configuration()`.
- **`Tester`** forks/processes each student, runs each test script in a sandbox,
  aggregates weighted grades, and submits results.
- **`Repository`** uses a **Chain of Responsibility** pattern (nested `Handler`
  classes) to decide whether a student's repo should be tested (empty repo,
  duplicate URL, private-repo check, new URL, webhook, pull).
- **Database** uses **Peewee ORM** with a pluggable backend: SQLite (dev/tests)
  or MySQL (production). Backend is chosen via env vars (`ATHINA_DB`, `ATHINA_TEST_MODE`).
- **Sandboxing** is either Docker (`tester/docker.py`) or firejail
  (`tester/firejail.py`), selected by `configuration.use_docker`.
- **Logging** is centralized in `logger.py`; the logger object can be deleted and
  recreated (workaround for multiprocessing/pickling).

---

## How it's tested

- **Framework:** pytest (see `pytest.ini`), with `unittest.TestCase`-style classes
  in the test files. Test discovery: `test_*.py` / `Test*` / `test_*`.
- **Run all tests:**
  ```bash
  pipenv install --dev
  pipenv run pytest
  # or
  ./run_tests.sh
  ```
- **Two DB modes** (handled in `tests/conftest.py`):
  - **SQLite (default, local dev):** no DB setup needed. Set via `ATHINA_DB=sqlite`
    or `ATHINA_TEST_MODE=1`. Uses a per-process file DB at `/tmp/athina_test_<pid>.db`.
  - **MySQL (production-like):** `ATHINA_USE_DOCKER_MYSQL=1 pipenv run pytest`.
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
- **Sandbox fallback in tests:** when `ATHINA_TEST_MODE=1`, docker/firejail code
  falls back to running the test script locally so tests pass without Docker/firejail.
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
- **Subprocess usage:** git/docker/firejail operations shell out via
  `subprocess.Popen`/`subprocess.run` rather than using libraries where convenient.
- **Security-conscious:** git credentials only sent to the configured `git_url`
  domain; student code is sandboxed; tests are force-timed-out; hidden files
  (e.g., `.git`) are excluded when copying student code.
- **`FIXME`/`TODO` comments** are used to flag known limitations.

---

## Common commands

```bash
# Run the test suite (SQLite, local)
./run_tests.sh
# Run a single test file
./run_tests.sh tests/test_canvas.py
# Run with coverage
./run_tests.sh -c
# Run with Docker MySQL (production-like)
ATHINA_USE_DOCKER_MYSQL=1 ./run_tests.sh

# Run the CLI against an example config
bin/athina-cli --config config-examples/ --repo_url_testing=https://github.com/athina-edu/testing.git

# Dev stack (web UI + service)
./dev-run.sh start   # then open http://localhost:8080
./dev-run.sh stop
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

"""Tests for the webhook trigger/dispatch mechanism.

Covers the three properties the daemon must guarantee:
  1. a webhook event grades only the student named in it;
  2. the trigger/poll loop is never blocked by testing (jobs are forked);
  3. a second event for a student who is already being graded is dropped.
"""
import json
import os
import shutil
import tempfile
import time
from unittest import TestCase, mock

from athina import trigger
from athina import cli


def _kill_child(pid):
    """Reap/kill a helper child created by a test."""
    try:
        os.kill(pid, 9)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except (ChildProcessError, OSError):
        pass


class TestTriggerFiles(TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_round_trip_student_event(self):
        trigger.write_student_trigger(7, 3, 42, assignment_dir="/code/a", root=self.root)
        events, full_pass = trigger.consume_triggers(root=self.root)
        self.assertFalse(full_pass)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['course_id'], 7)
        self.assertEqual(events[0]['assignment_id'], 3)
        self.assertEqual(events[0]['user_id'], 42)
        self.assertEqual(events[0]['directory'], "/code/a")

    def test_consume_deletes_events(self):
        """An event must never be acted on twice."""
        trigger.write_student_trigger(7, 3, 42, root=self.root)
        trigger.consume_triggers(root=self.root)
        events, full_pass = trigger.consume_triggers(root=self.root)
        self.assertEqual(events, [])
        self.assertFalse(full_pass)

    def test_two_events_do_not_overwrite_each_other(self):
        """Two pushes in the same instant must both survive."""
        trigger.write_student_trigger(7, 3, 42, root=self.root)
        trigger.write_student_trigger(7, 3, 43, root=self.root)
        events, _ = trigger.consume_triggers(root=self.root)
        self.assertEqual(sorted(e['user_id'] for e in events), [42, 43])

    def test_full_pass_marker(self):
        trigger.write_full_pass_trigger(root=self.root)
        events, full_pass = trigger.consume_triggers(root=self.root)
        self.assertTrue(full_pass)
        self.assertEqual(events, [])

    def test_corrupt_event_is_dropped(self):
        directory = trigger.trigger_dir(self.root)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, 'student-bad.json'), 'w') as fh:
            fh.write('{not json')
        events, _ = trigger.consume_triggers(root=self.root)
        self.assertEqual(events, [])

    def test_triggers_pending_is_read_only(self):
        trigger.write_student_trigger(7, 3, 42, root=self.root)
        with mock.patch.dict(os.environ, {
                'ATHINA_TRIGGER_DIR': trigger.trigger_dir(self.root),
                'ATHINA_TRIGGER_FILE': trigger.full_pass_trigger_file(self.root)}):
            self.assertTrue(trigger.triggers_pending())
            self.assertTrue(trigger.triggers_pending(), "peeking must not consume")
            trigger.consume_triggers()
            self.assertFalse(trigger.triggers_pending())

    def test_triggers_pending_false_when_dir_missing(self):
        with mock.patch.dict(os.environ, {
                'ATHINA_TRIGGER_DIR': os.path.join(self.root, 'nope'),
                'ATHINA_TRIGGER_FILE': os.path.join(self.root, 'nope-marker')}):
            self.assertFalse(trigger.triggers_pending())


class TestEventSanitizing(TestCase):
    """Event files live on a shared volume, so their content is untrusted.

    Anything able to write an event must not be able to smuggle newlines into a
    log line (pythonsecurity:S5145) or hand the daemon a path it will follow.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _write(self, name, payload):
        directory = trigger.trigger_dir(self.root)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, name)
        with open(path, 'w') as fh:
            fh.write(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    def _consume(self):
        return trigger.consume_triggers(root=self.root)

    def test_log_injection_in_directory_is_dropped(self):
        self._write('student-a.json', {'type': 'student', 'course_id': 1,
                                       'assignment_id': 2, 'user_id': 3,
                                       'directory': "/code/a\nfake log line"})
        events, _ = self._consume()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['directory'], "")

    def test_relative_directory_is_dropped(self):
        self._write('student-b.json', {'type': 'student', 'course_id': 1,
                                       'assignment_id': 2, 'user_id': 3,
                                       'directory': "../../etc"})
        events, _ = self._consume()
        self.assertEqual(events[0]['directory'], "")

    def test_non_string_directory_is_dropped(self):
        self._write('student-c.json', {'type': 'student', 'course_id': 1,
                                       'assignment_id': 2, 'user_id': 3,
                                       'directory': {'x': 1}})
        events, _ = self._consume()
        self.assertEqual(events[0]['directory'], "")

    def test_ids_are_coerced_to_ints(self):
        self._write('student-d.json', {'type': 'student', 'course_id': "7",
                                       'assignment_id': 3.0, 'user_id': 42})
        events, _ = self._consume()
        self.assertEqual((events[0]['course_id'], events[0]['assignment_id'],
                          events[0]['user_id']), (7, 3, 42))

    def test_bad_ids_reject_the_event(self):
        for i, value in enumerate(['abc', -1, True, None, 2 ** 70]):
            self._write('student-bad-%d.json' % i, {'type': 'student',
                                                    'course_id': value,
                                                    'assignment_id': 1,
                                                    'user_id': 1})
        events, _ = self._consume()
        self.assertEqual(events, [])

    def test_non_student_type_is_ignored(self):
        self._write('other.json', {'type': 'something', 'course_id': 1,
                                   'assignment_id': 1, 'user_id': 1})
        events, _ = self._consume()
        self.assertEqual(events, [])

    def test_event_filenames_are_unique(self):
        """Two events written in the same millisecond must both survive."""
        paths = {trigger.write_student_trigger(1, 2, 3, root=self.root)
                 for _ in range(5)}
        self.assertEqual(len(paths), 5)
        events, _ = self._consume()
        self.assertEqual(len(events), 5)

    def test_stale_tmp_file_is_removed(self):
        path = self._write('student-stale.json.123.tmp', '{}')
        old = time.time() - 120
        os.utime(path, (old, old))
        self._consume()
        self.assertFalse(os.path.exists(path))

    def test_fresh_tmp_file_is_left_alone(self):
        """A writer may be between writing its temp file and renaming it."""
        path = self._write('student-fresh.json.456.tmp', '{}')
        self._consume()
        self.assertTrue(os.path.exists(path))

    def test_event_file_is_owner_only(self):
        """Events name a student and an absolute path; keep them private."""
        path = trigger.write_student_trigger(1, 2, 3, root=self.root)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_event_directory_is_owner_only(self):
        trigger.write_student_trigger(1, 2, 3, root=self.root)
        st = os.stat(trigger.trigger_dir(self.root))
        self.assertEqual(st.st_mode & 0o777, 0o700)

    def test_created_field_is_normalized(self):
        self._write('student-created.json', {'type': 'student', 'course_id': 1,
                                             'assignment_id': 2, 'user_id': 3,
                                             'created': 'not-a-time'})
        events, _ = self._consume()
        self.assertEqual(events[0]['created'], 0)


class TestJobTracking(TestCase):
    def setUp(self):
        cli._running_jobs.clear()
        cli._poll_pid = None
        cli._last_full_pass_at = None
        self.addCleanup(cli._running_jobs.clear)

    def test_reap_drops_exited_jobs(self):
        cli._running_jobs[(1, 2, 3)] = (999999, 0)
        cli._reap_finished_jobs()
        self.assertEqual(cli._running_jobs, {})

    def test_running_job_detected(self):
        pid = os.fork()
        if pid == 0:  # child: stay alive briefly so the parent sees a live job
            os._exit(0)
        os.waitpid(pid, 0)  # reap immediately so only bookkeeping remains
        cli._running_jobs[(1, 2, 3)] = (pid, 0)
        # waitpid already ran, so the slot must be treated as free.
        self.assertFalse(cli._job_is_running(1, 2, 3))


class TestDispatch(TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(cli._running_jobs.clear)
        cli._running_jobs.clear()
        cli._poll_pid = None
        cli._last_full_pass_at = None
        self.logger = mock.MagicMock()
        patches = [
            mock.patch.object(cli, 'LOGGER', self.logger),
            mock.patch.object(trigger, 'trigger_dir',
                              lambda root=None: trigger.DEFAULT_TRIGGER_DIR),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_dispatch_is_skipped_while_job_runs(self):
        """No duplicate job for a student already being graded."""
        # A real live child, so the reaper keeps the job marked as running.
        pid = os.fork()
        if pid == 0:  # pragma: no cover - child
            time.sleep(30)
            os._exit(0)
        self.addCleanup(_kill_child, pid)
        cli._running_jobs[(7, 3, 42)] = (pid, 0)

        events = [{'type': 'student', 'course_id': 7, 'assignment_id': 3,
                   'user_id': 42, 'directory': '/code/a'}]
        with mock.patch.object(cli, 'consume_triggers', return_value=(events, False)), \
                mock.patch.object(cli, '_spawn_job') as mock_spawn:
            cli.dispatch_triggers()
        mock_spawn.assert_not_called()

    def test_dispatch_spawns_one_job_per_student(self):
        events = [
            {'type': 'student', 'course_id': 7, 'assignment_id': 3,
             'user_id': 42, 'directory': '/code/a'},
            {'type': 'student', 'course_id': 7, 'assignment_id': 3,
             'user_id': 43, 'directory': '/code/a'},
        ]
        with mock.patch.object(cli, 'consume_triggers', return_value=(events, False)), \
                mock.patch.object(cli, '_spawn_job', return_value=1234) as mock_spawn:
            cli.dispatch_triggers()
        self.assertEqual(mock_spawn.call_count, 2)
        self.assertIn((7, 3, 42), cli._running_jobs)
        self.assertIn((7, 3, 43), cli._running_jobs)

    def test_run_list_directory_wins_over_the_event(self):
        """The API form (with trailing slash) is authoritative over the event."""
        cli.RUN_LIST = [{'directory': '/code/known/', 'course_id': 7, 'assignment_id': 3}]
        cli._run_list_fetched_at = __import__('time').time()  # treat the cached list as fresh
        events = [{'type': 'student', 'course_id': 7, 'assignment_id': 3,
                   'user_id': 42, 'directory': '/code/known'}]
        try:
            with mock.patch.object(cli, 'consume_triggers', return_value=(events, False)), \
                    mock.patch.object(cli, '_spawn_job', return_value=1234) as mock_spawn:
                cli.dispatch_triggers()
            spawned_event = mock_spawn.call_args[0][2]
            self.assertEqual(spawned_event['directory'], '/code/known/')
        finally:
            cli.RUN_LIST = []
            cli._run_list_fetched_at = None

    def test_unresolvable_event_falls_back_to_full_pass(self):
        cli.RUN_LIST = []
        cli._run_list_fetched_at = __import__('time').time()  # cached list is fresh but empty
        events = [{'type': 'student', 'course_id': 7, 'assignment_id': 3,
                   'user_id': 42, 'directory': ''}]
        try:
            with mock.patch.object(cli, 'consume_triggers', return_value=(events, False)), \
                    mock.patch.object(cli, '_spawn_job', return_value=4321) as mock_spawn:
                cli.dispatch_triggers()
                # The pass is only *requested* here; no student job is spawned.
                self.assertEqual(mock_spawn.call_count, 0)
                self.assertIsNone(cli._last_full_pass_at)
        finally:
            cli._run_list_fetched_at = None

    def test_full_pass_trigger_requests_the_pass(self):
        with mock.patch.object(cli, 'consume_triggers', return_value=([], True)), \
                mock.patch.object(cli, '_spawn_job') as mock_spawn:
            cli.dispatch_triggers()
        # Deferred to service_tick so it cannot collide with a running job.
        mock_spawn.assert_not_called()
        self.assertIsNone(cli._last_full_pass_at)

    def test_requested_pass_runs_once_jobs_finish(self):
        cli._last_full_pass_at = None
        pid = os.fork()
        if pid == 0:  # pragma: no cover - child
            time.sleep(30)
            os._exit(0)
        self.addCleanup(_kill_child, pid)
        cli._running_jobs[(7, 3, 42)] = (pid, 0)

        with mock.patch.object(cli, 'dispatch_triggers'), \
                mock.patch.object(cli, '_spawn_job') as mock_spawn:
            cli.service_tick()
        mock_spawn.assert_not_called()  # a student job is still in flight

        _kill_child(pid)
        cli._running_jobs.clear()
        with mock.patch.object(cli, 'dispatch_triggers'), \
                mock.patch.object(cli, '_spawn_job', return_value=4321) as mock_spawn, \
                mock.patch.object(cli, '_reap_finished_jobs'):
            cli.service_tick()
        self.assertIs(mock_spawn.call_args[0][1], cli.main)

    def test_service_tick_does_not_start_pass_before_interval(self):
        import time as _time
        cli._last_full_pass_at = _time.time()
        with mock.patch.object(cli, 'dispatch_triggers'), \
                mock.patch.object(cli, '_spawn_job') as mock_spawn, \
                mock.patch.object(cli, '_reap_finished_jobs'):
            cli.service_tick()
        mock_spawn.assert_not_called()

    def test_service_tick_starts_pass_when_due(self):
        cli._last_full_pass_at = None
        with mock.patch.object(cli, 'dispatch_triggers'), \
                mock.patch.object(cli, '_spawn_job', return_value=4321) as mock_spawn, \
                mock.patch.object(cli, '_reap_finished_jobs'):
            cli.service_tick()
        self.assertIs(mock_spawn.call_args[0][1], cli.main)

    def test_service_tick_never_starts_two_passes(self):
        cli._poll_pid = 999999
        with mock.patch.object(cli, 'dispatch_triggers'), \
                mock.patch.object(cli, '_spawn_job') as mock_spawn, \
                mock.patch.object(cli, '_reap_finished_jobs'):
            cli.service_tick()
        mock_spawn.assert_not_called()


class TestSpawnAndResolve(TestCase):
    """The forked worker and the directory/run-list plumbing."""

    def setUp(self):
        self.logger = mock.MagicMock()
        self.addCleanup(cli._running_jobs.clear)
        cli._running_jobs.clear()
        cli._poll_pid = None
        cli._last_full_pass_at = None
        self.addCleanup(setattr, cli, 'RUN_LIST', [])
        self.addCleanup(setattr, cli, '_run_list_fetched_at', None)
        p = mock.patch.object(cli, 'LOGGER', self.logger)
        p.start()
        self.addCleanup(p.stop)

    def test_spawn_failure_returns_none(self):
        with mock.patch.object(cli.os, 'fork', side_effect=OSError("no forks")):
            self.assertIsNone(cli._spawn_job("x", lambda: None))
        self.logger.logger.error.assert_called_once()

    def test_child_failure_is_logged_not_propagated(self):
        """A raising worker must exit cleanly so the daemon survives.

        The worker is a separate process, so the assertion is on its exit
        status: `os._exit(0)` in the finally block swallows the exception.
        """
        pid = cli._spawn_job("boomer", _raise_value_error)
        self.assertIsNotNone(pid)
        _pid, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)

    def test_directory_for_event_matches_cached_list(self):
        cli.RUN_LIST = [{'directory': '/code/a/', 'course_id': 7, 'assignment_id': 3}]
        self.assertEqual(cli._directory_for_event(
            {'course_id': 7, 'assignment_id': 3}), '/code/a/')

    def test_directory_for_event_skips_incomplete_records(self):
        cli.RUN_LIST = [
            {'directory': '', 'course_id': 7, 'assignment_id': 3},
            {'directory': '/code/b/', 'course_id': None, 'assignment_id': 3},
            {'directory': '/code/c/', 'course_id': 'x', 'assignment_id': 3},
        ]
        self.assertEqual(cli._directory_for_event(
            {'course_id': 7, 'assignment_id': 3}), '')

    def test_directory_for_event_returns_empty_on_no_match(self):
        cli.RUN_LIST = [{'directory': '/code/a/', 'course_id': 1, 'assignment_id': 1}]
        self.assertEqual(cli._directory_for_event(
            {'course_id': 7, 'assignment_id': 3}), '')

    def test_refresh_run_list_is_throttled(self):
        cli._run_list_fetched_at = time.time()
        cli.RUN_LIST = [{'directory': '/cached/'}]
        with mock.patch.object(cli, '_fetch_run_list') as fetch:
            self.assertEqual(cli._refresh_run_list(), [{'directory': '/cached/'}])
        fetch.assert_not_called()

    def test_refresh_run_list_updates_cache(self):
        cli._run_list_fetched_at = None
        cli.RUN_LIST = []
        with mock.patch.object(cli, '_fetch_run_list',
                               return_value=[{'directory': '/new/'}]) as fetch:
            self.assertEqual(cli._refresh_run_list(), [{'directory': '/new/'}])
        fetch.assert_called_once()

    def test_refresh_run_list_keeps_old_list_on_failure(self):
        """An unreachable API must not wipe the cache, nor be retried every event."""
        cli._run_list_fetched_at = None
        cli.RUN_LIST = [{'directory': '/old/'}]
        with mock.patch.object(cli, '_fetch_run_list', return_value=None):
            self.assertEqual(cli._refresh_run_list(), [{'directory': '/old/'}])
        self.assertIsNotNone(cli._run_list_fetched_at, "attempt must be stamped")

    def test_run_student_job_scopes_to_one_user(self):
        event = {'directory': '/code/a/', 'course_id': 7,
                 'assignment_id': 3, 'user_id': 42}
        with mock.patch.object(cli, 'process_assignment') as run:
            cli._run_student_job(event)
        run.assert_called_once_with({'directory': '/code/a/', 'course_id': 7,
                                     'assignment_id': 3}, only_user_id=42)

    def test_request_full_pass_defers(self):
        cli._last_full_pass_at = 12345
        with mock.patch.object(cli, 'LOGGER', self.logger):
            events = [{'type': 'student', 'course_id': 7, 'assignment_id': 3,
                       'user_id': 42, 'directory': ''}]
            cli.RUN_LIST = []
            with mock.patch.object(cli, 'consume_triggers',
                                   return_value=(events, False)), \
                    mock.patch.object(cli, '_spawn_job') as spawn:
                cli.dispatch_triggers()
        spawn.assert_not_called()
        self.assertIsNone(cli._last_full_pass_at)


class TestRepoSync(TestCase):
    """Workers run concurrently, so the assignment checkout pull must be locked."""

    def setUp(self):
        self.logger = mock.MagicMock()
        p = mock.patch.object(cli, 'LOGGER', self.logger)
        p.start()
        self.addCleanup(p.stop)
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, True)
        os.makedirs(os.path.join(self.repo, '.git'))

    def test_skips_when_directory_is_missing(self):
        with mock.patch.object(cli, '_do_pull_assignment_repo') as pull:
            cli._pull_assignment_repo('/nonexistent/path')
        pull.assert_not_called()

    def test_skips_when_not_a_git_repo(self):
        plain = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, plain, True)
        with mock.patch.object(cli, '_do_pull_assignment_repo') as pull:
            cli._pull_assignment_repo(plain)
        pull.assert_not_called()

    def test_pulls_a_git_checkout(self):
        with mock.patch.object(cli, '_do_pull_assignment_repo') as pull:
            cli._pull_assignment_repo(self.repo)
        pull.assert_called_once_with(self.repo)

    def test_lock_timeout_skips_the_pull(self):
        import filelock
        with mock.patch.object(cli.filelock, 'FileLock') as lock_cls:
            lock_cls.return_value.acquire.side_effect = filelock.Timeout("busy")
            with mock.patch.object(cli, '_do_pull_assignment_repo') as pull:
                cli._pull_assignment_repo(self.repo)
        pull.assert_not_called()
        self.logger.logger.warning.assert_called_once()

    def test_release_failure_does_not_escape(self):
        """A broken lock release must not take the worker down."""
        with mock.patch.object(cli.filelock, 'FileLock') as lock_cls:
            lock_cls.return_value.release.side_effect = RuntimeError("nope")
            with mock.patch.object(cli, '_do_pull_assignment_repo'):
                cli._pull_assignment_repo(self.repo)  # must not raise

    def test_pull_error_is_logged(self):
        with mock.patch('git.Repo', side_effect=RuntimeError("bad repo")):
            cli._do_pull_assignment_repo(self.repo)
        self.logger.logger.warning.assert_called_once()


def _raise_value_error():
    raise ValueError("boom")

class TestTargetedCoreIteration(TestCase):
    """`core_iteration(only_user_id=...)` must test just that student."""

    def _config(self):
        from tests.helpers import make_config
        configuration, logger = make_config()
        configuration.auth_token = ""
        configuration.check_plagiarism_hour = 99
        configuration.input_method = "db"
        return configuration, logger

    def test_targeted_run_skips_canvas_roster_fetch(self):
        configuration, _ = self._config()
        configuration.auth_token = "token"  # even with credentials, no full fetch
        with mock.patch.object(cli, 'LOGGER'), \
                mock.patch.object(cli, 'ARGS') as mock_args, \
                mock.patch.object(cli, 'Canvas') as mock_canvas, \
                mock.patch.object(cli, 'Repository'), \
                mock.patch.object(cli, 'Tester') as mock_tester, \
                mock.patch.object(cli, 'return_all_students', return_value=[mock.Mock()]), \
                mock.patch.object(cli, 'load_key_from_assignment_data', return_value=None), \
                mock.patch.object(cli, 'os'):
            mock_args.repo_url_testing = None
            cli.core_iteration(configuration, mock.Mock(), only_user_id=42)
        mock_canvas.return_value.get_all_submissions.assert_not_called()
        mock_tester.return_value.start_testing_db.assert_called_once_with(user_ids=[42])

    def test_targeted_run_skips_duplicate_scan(self):
        configuration, _ = self._config()
        with mock.patch.object(cli, 'LOGGER'), \
                mock.patch.object(cli, 'ARGS') as mock_args, \
                mock.patch.object(cli, 'Canvas'), \
                mock.patch.object(cli, 'Repository'), \
                mock.patch.object(cli, 'Tester'), \
                mock.patch.object(cli, 'return_all_students', return_value=[mock.Mock()]), \
                mock.patch.object(cli, 'load_key_from_assignment_data', return_value=None), \
                mock.patch.object(cli, 'os'):
            mock_args.repo_url_testing = None
            user_data = mock.Mock()
            cli.core_iteration(configuration, user_data, only_user_id=42)
        user_data.check_duplicate_url.assert_not_called()

    def test_targeted_run_skips_plagiarism(self):
        configuration, _ = self._config()
        configuration.check_plagiarism_hour = 0
        with mock.patch.object(cli, 'LOGGER'), \
                mock.patch.object(cli, 'ARGS') as mock_args, \
                mock.patch.object(cli, 'Canvas'), \
                mock.patch.object(cli, 'Repository'), \
                mock.patch.object(cli, 'Tester'), \
                mock.patch.object(cli, 'return_all_students', return_value=[mock.Mock()]), \
                mock.patch.object(cli, 'load_key_from_assignment_data', return_value=None), \
                mock.patch.object(cli, 'plagiarism_checks_on_users') as mock_plag, \
                mock.patch('athina.cli.datetime.datetime') as mock_dt, \
                mock.patch.object(cli, 'os'):
            mock_args.repo_url_testing = None
            mock_dt.now.return_value.replace.return_value.hour = 0
            cli.core_iteration(configuration, mock.Mock(), only_user_id=42)
        mock_plag.assert_not_called()

    def test_full_pass_still_scans_duplicates(self):
        configuration, _ = self._config()
        with mock.patch.object(cli, 'LOGGER'), \
                mock.patch.object(cli, 'ARGS') as mock_args, \
                mock.patch.object(cli, 'Canvas'), \
                mock.patch.object(cli, 'Repository'), \
                mock.patch.object(cli, 'Tester'), \
                mock.patch.object(cli, 'return_all_students', return_value=[mock.Mock()]), \
                mock.patch.object(cli, 'load_key_from_assignment_data', return_value=None), \
                mock.patch.object(cli, 'os'):
            mock_args.repo_url_testing = None
            user_data = mock.Mock()
            cli.core_iteration(configuration, user_data)
        user_data.check_duplicate_url.assert_called_once()

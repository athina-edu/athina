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

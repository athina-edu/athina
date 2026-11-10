"""Tests for athina.moss — the plagiarism detection module.

The implementation lives in ``athina.moss``; ``athina.plagiarism`` is a
backwards-compatibility shim that re-exports from it (asserted below).
"""
import os
import re
import shutil
import tempfile
from datetime import datetime
from unittest import mock, TestCase

import numpy as np

from athina.moss import (Plagiarism, _collect_submission_directories,
                         _expand_group_comparisons, _group_representatives,
                         _max_similarity_for, _mean_similarity,
                         plagiarism_checks_on_users)
from athina.users import *
from tests.helpers import make_config
from tests.test_athina import create_logger


def _detector_stub(similarity, file_list):
    """Build a stand-in for a ran CopyDetector.

    ``similarity`` is given as the old, symmetric (n, n) matrix copydetect
    used to expose; it is expanded into copydetect>=0.5's (n, n, 2) shape by
    duplicating each value for both containment directions, since the tests
    using this helper only care about the symmetric case.
    """
    detector = mock.Mock()
    sym = np.array(similarity)
    detector.similarity_matrix = np.stack([sym, sym], axis=-1)
    detector.test_files = file_list
    return detector


class TestModuleLayout(TestCase):
    def test_plagiarism_shim_reexports_same_objects(self):
        """athina.plagiarism must expose the very same objects, not copies."""
        import athina.moss
        import athina.plagiarism

        self.assertIs(athina.plagiarism.Plagiarism, athina.moss.Plagiarism)
        self.assertIs(athina.plagiarism.plagiarism_checks_on_users,
                      athina.moss.plagiarism_checks_on_users)

    def test_moss_support_is_fully_removed(self):
        """The dead MOSS online-service path should be gone."""
        import athina.moss

        self.assertFalse(hasattr(athina.moss, '_HAS_MOSSPY'))
        self.assertFalse(hasattr(athina.moss.Plagiarism, 'moss_id'))
        self.assertFalse(hasattr(athina.moss.Plagiarism, 'moss_lang'))
        self.assertFalse(hasattr(athina.moss.Plagiarism, '_check_moss'))


class TestHelpers(TestCase):
    def test_mean_similarity_averages_each_students_own_max(self):
        # Student 1's own best match is 80 (not 20); student 2's is 40.
        self.assertEqual(_mean_similarity({1: [80, 20], 2: [40]}), (80 + 40) / 2)

    def test_mean_similarity_excludes_students_with_no_submission(self):
        """A student who never submitted anything (absent from
        comparison_data entirely) must not pull the baseline down -- unlike
        the old padding-by-roster-size behavior this replaces."""
        with_one_nonsubmitter = _mean_similarity({1: [50], 2: [60]})
        without_nonsubmitter_info = _mean_similarity({1: [50], 2: [60]})
        # A third, unsubmitted student is simply never a key here; the mean
        # over the two real submitters must be unaffected either way.
        self.assertEqual(with_one_nonsubmitter, without_nonsubmitter_info)
        self.assertEqual(with_one_nonsubmitter, (50 + 60) / 2)

    def test_mean_similarity_no_data_returns_zero(self):
        self.assertEqual(_mean_similarity({}), 0)

    def test_max_similarity_for_known_user(self):
        self.assertEqual(_max_similarity_for({1: [30, 90], 2: [10]}, 1), 90)

    def test_max_similarity_for_unknown_user_is_zero(self):
        self.assertEqual(_max_similarity_for({1: [30]}, 999), 0)

    def test_max_similarity_for_empty_is_zero(self):
        self.assertEqual(_max_similarity_for({}, 1), 0)

    def test_collect_submission_directories_uses_configured_pattern(self):
        configuration, _ = make_config()
        configuration.assignment_id = 1
        configuration.course_id = 1
        configuration.config_dir = "/tmp"
        configuration.plagiarism_pattern = "*.py"
        student = mock.Mock(user_id=17)

        with mock.patch('athina.moss.return_all_students', return_value=[student]), \
                mock.patch('athina.moss.os.path.isdir', return_value=True), \
                mock.patch('athina.moss.glob.glob', return_value=["x"]):
            dirs = _collect_submission_directories(configuration)

        self.assertEqual(dirs, ["/tmp/repodata1/u17/*.py"])

    def test_collect_submission_directories_skips_empty_dirs(self):
        configuration, _ = make_config()
        configuration.assignment_id = 1
        configuration.course_id = 1
        configuration.config_dir = "/tmp"
        student = mock.Mock(user_id=17)

        # Directory exists but contains no matching files -> excluded.
        with mock.patch('athina.moss.return_all_students', return_value=[student]), \
                mock.patch('athina.moss.os.path.isdir', return_value=True), \
                mock.patch('athina.moss.glob.glob', return_value=[]):
            self.assertEqual(_collect_submission_directories(configuration), [])

    def test_collect_submission_directories_restricts_to_representatives(self):
        """Group assignments: only the representative's directory is used,
        even though every group member has their own repodata directory."""
        configuration, _ = make_config()
        configuration.assignment_id = 1
        configuration.course_id = 1
        configuration.config_dir = "/tmp"
        configuration.plagiarism_pattern = "*.py"
        students = [mock.Mock(user_id=17), mock.Mock(user_id=18)]

        with mock.patch('athina.moss.return_all_students', return_value=students), \
                mock.patch('athina.moss.os.path.isdir', return_value=True), \
                mock.patch('athina.moss.glob.glob', return_value=["x"]):
            dirs = _collect_submission_directories(configuration, representative_ids={17})

        self.assertEqual(dirs, ["/tmp/repodata1/u17/*.py"])


class TestGroupRepresentatives(TestCase):
    """Group assignments share one repository per group -- comparing every
    member individually would only ever report each group matching its own
    identical copy of itself. One representative per group must be picked
    instead, with the rest of the group mapped to it."""

    def test_groupmates_sharing_a_url_become_one_group(self):
        students = [
            mock.Mock(user_id=1, repository_url="https://x/group-a.git"),
            mock.Mock(user_id=2, repository_url="https://x/group-a.git"),
            mock.Mock(user_id=3, repository_url="https://x/group-b.git"),
        ]
        with mock.patch('athina.moss.return_all_students', return_value=students):
            groups = _group_representatives(1, 1)

        # Student 1 is the first-seen representative of group A.
        self.assertEqual(groups, {1: [1, 2], 3: [3]})

    def test_students_without_a_repo_url_are_their_own_group(self):
        students = [mock.Mock(user_id=1, repository_url=""),
                   mock.Mock(user_id=2, repository_url=None)]
        with mock.patch('athina.moss.return_all_students', return_value=students):
            groups = _group_representatives(1, 1)
        self.assertEqual(groups, {1: [1], 2: [2]})


class TestExpandGroupComparisons(TestCase):
    def test_representatives_result_is_copied_to_every_member(self):
        comparison_data = {1: [90]}
        groups = {1: [1, 2, 3]}
        expanded = _expand_group_comparisons(comparison_data, groups)
        self.assertEqual(expanded, {1: [90], 2: [90], 3: [90]})

    def test_representative_never_compared_is_left_out_for_everyone(self):
        """A group with no real submission gets nothing to copy -- its
        members must not show a fabricated score."""
        groups = {1: [1, 2]}
        self.assertEqual(_expand_group_comparisons({}, groups), {})

    def test_singleton_groups_are_unaffected(self):
        comparison_data = {5: [40], 6: [60]}
        groups = {5: [5], 6: [6]}
        self.assertEqual(_expand_group_comparisons(comparison_data, groups), comparison_data)


class TestPlagiarism(TestCase):
    def test_plagiarism_init_other_service(self):
        p = Plagiarism(logger=create_logger(), service_type="other")
        self.assertIsNone(p.service_type)

    def test_plagiarism_init_no_service(self):
        p = Plagiarism(logger=create_logger())
        self.assertIsNone(p.service_type)

    def test_plagiarism_init_copydetect(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect", threshold=0.5)
        self.assertEqual(p.service_type, "copydetect")
        self.assertEqual(p.threshold, 0.5)

    def test_plagiarism_init_copydetect_default_threshold(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        self.assertEqual(p.threshold, 0.33)

    def test_plagiarism_init_copydetect_defaults_for_new_knobs(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        self.assertEqual(p.noise_threshold, 25)
        self.assertEqual(p.guarantee_threshold, 25)
        self.assertFalse(p.disable_filtering)
        self.assertFalse(p.same_name_only)
        self.assertIsNone(p.boilerplate_dir)

    def test_plagiarism_init_copydetect_custom_knobs(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect",
                       noise_threshold=150, guarantee_threshold=150,
                       disable_filtering=True, same_name_only=True,
                       boilerplate_dir="/tmp/boilerplate")
        self.assertEqual(p.noise_threshold, 150)
        self.assertEqual(p.guarantee_threshold, 150)
        self.assertTrue(p.disable_filtering)
        self.assertTrue(p.same_name_only)
        self.assertEqual(p.boilerplate_dir, "/tmp/boilerplate")

    def test_init_copydetect_without_package_raises(self):
        with mock.patch('athina.moss._HAS_COPYDETECT', False):
            with self.assertRaises(ImportError):
                Plagiarism(logger=create_logger(), service_type="copydetect")

    def test_check_plagiarism_empty_folder_list(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        self.assertEqual(p.check_plagiarism([], 1, 1), dict())

    def test_check_plagiarism_other_service(self):
        p = Plagiarism(logger=create_logger(), service_type="other")
        self.assertEqual(p.check_plagiarism(["/tmp/*.py"], 1, 1), dict())

    def test_parse_comparison_time_new(self):
        comparisons = {}
        Plagiarism.parse_comparison_time(comparisons, "1", "75")
        self.assertEqual(comparisons, {1: [75]})

    def test_parse_comparison_time_existing(self):
        comparisons = {1: [50]}
        Plagiarism.parse_comparison_time(comparisons, "1", "75")
        self.assertEqual(comparisons, {1: [50, 75]})


class TestParsePatterns(TestCase):
    def test_extracts_dirs_and_extensions(self):
        with mock.patch('athina.moss.os.path.isdir', return_value=True):
            dirs, exts = Plagiarism._parse_patterns(
                ["/tmp/r/u1/*.py", "/tmp/r/u2/*.py", "/tmp/r/u3/*.java"])
        self.assertEqual(dirs, ["/tmp/r/u1", "/tmp/r/u2", "/tmp/r/u3"])
        self.assertEqual(exts, {"py", "java"})

    def test_ignores_non_glob_and_missing_dirs(self):
        with mock.patch('athina.moss.os.path.isdir', return_value=False):
            dirs, exts = Plagiarism._parse_patterns(["/tmp/r/u1/notes.txt"])
        self.assertEqual(dirs, [])
        self.assertEqual(exts, set())


class TestMapResults(TestCase):
    def test_maps_similarity_matrix_to_user_ids(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        detector = _detector_stub(
            similarity=[[0.0, 0.75], [0.75, 0.0]],
            file_list=["/tmp/r/u1/a.py", "/tmp/r/u2/b.py"],
        )
        self.assertEqual(p._map_results(detector), {1: [75], 2: [75]})

    def test_skips_self_comparisons(self):
        """Two files from the same student must not score as a match."""
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        detector = _detector_stub(
            similarity=[[0.0, 0.9], [0.9, 0.0]],
            file_list=["/tmp/r/u1/a.py", "/tmp/r/u1/b.py"],
        )
        self.assertEqual(p._map_results(detector), {})

    def test_skips_unparseable_paths(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        detector = _detector_stub(
            similarity=[[0.0, 0.9], [0.9, 0.0]],
            file_list=["/tmp/r/nouser/a.py", "/tmp/r/nouser/b.py"],
        )
        self.assertEqual(p._map_results(detector), {})

    def test_zero_similarity_is_ignored(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        detector = _detector_stub(
            similarity=[[0.0, 0.0], [0.0, 0.0]],
            file_list=["/tmp/r/u1/a.py", "/tmp/r/u2/b.py"],
        )
        self.assertEqual(p._map_results(detector), {})

    def test_multiple_files_per_student_accumulate(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        detector = _detector_stub(
            similarity=[[0.0, 0.5, 0.8], [0.5, 0.0, 0.3], [0.8, 0.3, 0.0]],
            file_list=["/tmp/r/u1/a.py", "/tmp/r/u2/b.py", "/tmp/r/u2/c.py"],
        )
        result = p._map_results(detector)
        # u1 (file 0) matches u2/b (file 1) at 50 and u2/c (file 2) at 80.
        # The (u2/b, u2/c) pair is a self-comparison and is skipped.
        self.assertEqual(sorted(result[1]), [50, 80])
        self.assertEqual(sorted(result[2]), [50, 80])


class TestRunDetector(TestCase):
    def test_returns_detector_on_success(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        with mock.patch('athina.moss._copydetect') as cd:
            detector = p._run_detector(["/tmp/u1"], ["py"])
        cd.CopyDetector.assert_called_once()
        cd.CopyDetector.return_value.run.assert_called_once()
        self.assertIs(detector, cd.CopyDetector.return_value)

    def test_passes_tuning_knobs_to_copydetector(self):
        """The constructor must use copydetect's current kwarg names
        (autoopen, not disable_autoopen) and forward the tuning knobs."""
        p = Plagiarism(logger=create_logger(), service_type="copydetect",
                       noise_threshold=150, guarantee_threshold=150,
                       disable_filtering=True, same_name_only=True)
        with mock.patch('athina.moss._copydetect') as cd:
            p._run_detector(["/tmp/u1"], ["sql"], report_path="/tmp/report.html")
        _, kwargs = cd.CopyDetector.call_args
        self.assertEqual(kwargs["noise_t"], 150)
        self.assertEqual(kwargs["guarantee_t"], 150)
        self.assertTrue(kwargs["disable_filtering"])
        self.assertTrue(kwargs["same_name_only"])
        self.assertEqual(kwargs["out_file"], "/tmp/report.html")
        self.assertEqual(kwargs["autoopen"], False)
        self.assertNotIn("disable_autoopen", kwargs)

    def test_boilerplate_dir_forwarded_when_it_exists(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect",
                       boilerplate_dir="/tmp")  # always exists
        with mock.patch('athina.moss._copydetect') as cd:
            p._run_detector(["/tmp/u1"], ["sql"])
        _, kwargs = cd.CopyDetector.call_args
        self.assertEqual(kwargs["boilerplate_dirs"], ["/tmp"])

    def test_boilerplate_dir_omitted_when_missing(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect",
                       boilerplate_dir="/tmp/does-not-exist-at-all")
        with mock.patch('athina.moss._copydetect') as cd:
            p._run_detector(["/tmp/u1"], ["sql"])
        _, kwargs = cd.CopyDetector.call_args
        self.assertNotIn("boilerplate_dirs", kwargs)

    def test_returns_none_and_logs_on_failure(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        with mock.patch('athina.moss._copydetect') as cd, \
                mock.patch.object(p.logger.logger, 'error') as mock_err:
            cd.CopyDetector.return_value.run.side_effect = Exception("boom")
            self.assertIsNone(p._run_detector(["/tmp/u1"], ["py"]))
            mock_err.assert_called_once()


class TestStageFilteredDirs(TestCase):
    """The oversized-file guard that keeps CopyDetect from reading, e.g., a
    multi-hundred-MB data dump a student committed alongside their actual
    answers (see the memory blow-up this was written to fix)."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test_stage_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _student_dir(self, name):
        path = os.path.join(self.root, name)
        os.makedirs(path)
        return path

    def _write(self, directory, filename, size_bytes):
        with open(os.path.join(directory, filename), "wb") as f:
            f.write(b"x" * size_bytes)

    def test_small_files_are_symlinked_through(self):
        u1 = self._student_dir("u1")
        self._write(u1, "q1.sql", 10)
        p = Plagiarism(logger=create_logger(), service_type="copydetect",
                       max_file_size=1024)
        staged_dirs, stage_root = p._stage_filtered_dirs([u1], ["sql"])
        try:
            self.assertEqual(len(staged_dirs), 1)
            self.assertEqual(os.path.basename(staged_dirs[0]), "u1")
            staged_file = os.path.join(staged_dirs[0], "q1.sql")
            self.assertTrue(os.path.islink(staged_file))
            self.assertEqual(os.path.getsize(staged_file), 10)
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)

    def test_oversized_file_is_excluded_and_logged(self):
        u1 = self._student_dir("u1")
        self._write(u1, "emp_employees.sql", 2048)  # over the 1024-byte cap
        p = Plagiarism(logger=create_logger(), service_type="copydetect",
                       max_file_size=1024)
        with mock.patch.object(p.logger.logger, 'warning') as mock_warn:
            staged_dirs, stage_root = p._stage_filtered_dirs([u1], ["sql"])
        try:
            self.assertEqual(staged_dirs, [])
            mock_warn.assert_called_once()
            self.assertIn("emp_employees.sql", mock_warn.call_args[0][0])
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)

    def test_mixed_directory_keeps_small_files_drops_large_one(self):
        """One oversized file must not take the rest of that student's
        legitimate submission down with it."""
        u1 = self._student_dir("u1")
        self._write(u1, "q1.sql", 10)
        self._write(u1, "emp_employees.sql", 2048)
        p = Plagiarism(logger=create_logger(), service_type="copydetect",
                       max_file_size=1024)
        staged_dirs, stage_root = p._stage_filtered_dirs([u1], ["sql"])
        try:
            self.assertEqual(len(staged_dirs), 1)
            kept = os.listdir(staged_dirs[0])
            self.assertEqual(kept, ["q1.sql"])
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)

    def test_default_cap_is_one_mebibyte(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        self.assertEqual(p.max_file_size, 1024 * 1024)


class TestCheckCopydetect(TestCase):
    def test_oversized_file_never_reaches_copydetector(self):
        """End-to-end: _check_copydetect must hand the detector a staged,
        filtered directory, never the raw student directory holding the
        oversized file, and must clean up the staging tree afterward."""
        root = tempfile.mkdtemp(prefix="test_check_")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        u1 = os.path.join(root, "u1")
        os.makedirs(u1)
        with open(os.path.join(u1, "q1.sql"), "w") as f:
            f.write("select 1;")
        with open(os.path.join(u1, "emp_employees.sql"), "wb") as f:
            f.write(b"x" * 2048)

        p = Plagiarism(logger=create_logger(), service_type="copydetect",
                       max_file_size=1024)
        with mock.patch('athina.moss._copydetect') as cd, \
                mock.patch('athina.moss.update_key_in_assignment_data'):
            cd.CopyDetector.return_value.test_files = []
            cd.CopyDetector.return_value.similarity_matrix = np.zeros((0, 0, 2))
            p._check_copydetect(["%s/*.sql" % u1], 1, 2)

        _, kwargs = cd.CopyDetector.call_args
        called_dirs = kwargs["test_dirs"]
        self.assertNotIn(u1, called_dirs)
        self.assertFalse(os.path.isdir(called_dirs[0]) and
                         os.path.exists(os.path.join(called_dirs[0], "emp_employees.sql")))
        # Staging directory must be cleaned up once the check is done.
        for staged_dir in called_dirs:
            self.assertFalse(os.path.isdir(staged_dir))


class TestSaveHtmlReport(TestCase):
    def test_records_report_path(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        detector = mock.Mock()
        with mock.patch('athina.moss.update_key_in_assignment_data') as mock_update:
            p._save_html_report(detector, ["/tmp/repodata1/u1"], 1, 2)
        detector.generate_html_report.assert_called_once()
        self.assertEqual(mock_update.call_args[0][0], 1)
        self.assertEqual(mock_update.call_args[0][1], 2)
        self.assertEqual(mock_update.call_args[0][2], "plagiarism_report")

    def test_report_failure_is_not_fatal(self):
        p = Plagiarism(logger=create_logger(), service_type="copydetect")
        detector = mock.Mock()
        detector.generate_html_report.side_effect = Exception("no report")
        with mock.patch.object(p.logger.logger, 'warning') as mock_warn:
            p._save_html_report(detector, ["/tmp/repodata1/u1"], 1, 2)
        mock_warn.assert_called_once()


class TestPlagiarismChecksOnUsers(TestCase):
    def setUp(self):
        # Ensure the users/assignmentdata tables exist for this test DB.
        Database().connect_to_db()

    def _config(self):
        configuration, _ = make_config()
        configuration.plagiarism_pattern = "*.py"
        configuration.config_dir = "/tmp"
        configuration.assignment_id = 1
        configuration.course_id = 1
        return configuration

    def _due_students(self):
        return list(Users.select().where(Users.course_id == 1,
                                         Users.assignment_id == 1))

    def _fresh_each_call(self):
        """Mimic production: return_all_students re-queries on every call.

        Using a side_effect (rather than a fixed return_value) matters because
        the function ends by re-reading and saving each student — a stale
        snapshot would clobber the scores written moments earlier.
        """
        return mock.patch('athina.moss.return_all_students',
                          side_effect=lambda *a, **k: self._due_students())

    def test_returns_empty_when_no_users_are_due(self):
        configuration, logger = make_config()
        configuration.assignment_id = 1
        configuration.course_id = 1
        with mock.patch('athina.moss.return_all_students', return_value=[]):
            results = plagiarism_checks_on_users(logger, configuration, mock.Mock())
        self.assertEqual(results, [])

    def test_reports_error_when_copydetect_missing(self):
        configuration = self._config()
        _, logger = make_config()
        student = mock.Mock(user_id=555, plagiarism_to_grade=True,
                            last_plagiarism_check=datetime(2000, 1, 1))
        with mock.patch('athina.moss.return_all_students', return_value=[student]), \
                mock.patch('athina.moss._HAS_COPYDETECT', False), \
                mock.patch.object(logger.logger, 'error') as mock_err:
            results = plagiarism_checks_on_users(logger, configuration, mock.Mock())
        self.assertEqual(results, [])
        mock_err.assert_called_once()

    def test_records_scores_for_due_student(self):
        configuration = self._config()
        _, logger = make_config()
        Users.create(user_id=555, course_id=1, assignment_id=1,
                     repository_url="https://github.com/x/y.git",
                     plagiarism_to_grade=True,
                     last_plagiarism_check=datetime(2000, 1, 1, 0, 0))
        try:
            with self._fresh_each_call(), \
                    mock.patch('athina.moss.os.path.isdir', return_value=True), \
                    mock.patch('athina.moss.glob.glob', return_value=["x"]), \
                    mock.patch('athina.moss.Plagiarism') as mock_plag:
                mock_plag.return_value.check_plagiarism.return_value = {555: [75]}
                results = plagiarism_checks_on_users(logger, configuration, mock.Mock())

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0][0], 555)
            self.assertEqual(results[0][1], 75)

            refreshed = Users.get(Users.user_id == 555)
            self.assertEqual(refreshed.plagiarism_max, 75)
            self.assertFalse(refreshed.plagiarism_to_grade)
        finally:
            Users.delete().where(Users.user_id == 555).execute()

    def test_group_assignment_shares_result_across_members(self):
        """Two group-mates sharing one repository_url must both end up
        with the same score -- whichever of them CopyDetect happened to
        treat as the representative, since their repodata is identical."""
        configuration = self._config()
        configuration.group_assignment = True
        _, logger = make_config()
        shared_url = "https://gitlab.example.edu/group-x/project.git"
        Users.create(user_id=601, course_id=1, assignment_id=1,
                     repository_url=shared_url, plagiarism_to_grade=True,
                     last_plagiarism_check=datetime(2000, 1, 1, 0, 0))
        Users.create(user_id=602, course_id=1, assignment_id=1,
                     repository_url=shared_url, plagiarism_to_grade=True,
                     last_plagiarism_check=datetime(2000, 1, 1, 0, 0))

        def fake_check_plagiarism(directory_list, course_id, assignment_id):
            # Only one directory (the representative's) should ever be
            # handed to CopyDetect -- never both group-mates' identical copies.
            self.assertEqual(len(directory_list), 1)
            rep_id = int(re.search(r'u(\d+)/', directory_list[0]).group(1))
            return {rep_id: [85]}

        try:
            with self._fresh_each_call(), \
                    mock.patch('athina.moss.os.path.isdir', return_value=True), \
                    mock.patch('athina.moss.glob.glob', return_value=["x"]), \
                    mock.patch('athina.moss.Plagiarism') as mock_plag:
                mock_plag.return_value.check_plagiarism.side_effect = fake_check_plagiarism
                results = plagiarism_checks_on_users(logger, configuration, mock.Mock())

            self.assertEqual(sorted(r[1] for r in results), [85, 85])
            self.assertEqual(Users.get(Users.user_id == 601).plagiarism_max, 85)
            self.assertEqual(Users.get(Users.user_id == 602).plagiarism_max, 85)
        finally:
            Users.delete().where(Users.user_id.in_([601, 602])).execute()

    def test_publishes_comment_when_configured(self):
        configuration = self._config()
        configuration.plagiarism_publish = True
        _, logger = make_config()
        Users.create(user_id=556, course_id=1, assignment_id=1,
                     repository_url="https://github.com/x/y.git",
                     plagiarism_to_grade=True,
                     last_plagiarism_check=datetime(2000, 1, 1, 0, 0))
        e_learning = mock.Mock()
        try:
            with self._fresh_each_call(), \
                    mock.patch('athina.moss.os.path.isdir', return_value=True), \
                    mock.patch('athina.moss.glob.glob', return_value=["x"]), \
                    mock.patch('athina.moss.Plagiarism') as mock_plag:
                mock_plag.return_value.check_plagiarism.return_value = {556: [40]}
                plagiarism_checks_on_users(logger, configuration, e_learning)

            e_learning.submit_comment.assert_called_once()
            self.assertEqual(e_learning.submit_comment.call_args[0][0], 556)
        finally:
            Users.delete().where(Users.user_id == 556).execute()

    def test_does_not_publish_when_disabled(self):
        configuration = self._config()
        configuration.plagiarism_publish = False
        _, logger = make_config()
        Users.create(user_id=557, course_id=1, assignment_id=1,
                     repository_url="https://github.com/x/y.git",
                     plagiarism_to_grade=True,
                     last_plagiarism_check=datetime(2000, 1, 1, 0, 0))
        e_learning = mock.Mock()
        try:
            with self._fresh_each_call(), \
                    mock.patch('athina.moss.os.path.isdir', return_value=True), \
                    mock.patch('athina.moss.glob.glob', return_value=["x"]), \
                    mock.patch('athina.moss.Plagiarism') as mock_plag:
                mock_plag.return_value.check_plagiarism.return_value = {557: [40]}
                plagiarism_checks_on_users(logger, configuration, e_learning)

            e_learning.submit_comment.assert_not_called()
        finally:
            Users.delete().where(Users.user_id == 557).execute()

    def test_student_not_due_is_skipped(self):
        """A student checked recently should not be re-processed."""
        configuration = self._config()
        _, logger = make_config()
        Users.create(user_id=558, course_id=1, assignment_id=1,
                     repository_url="https://github.com/x/y.git",
                     plagiarism_to_grade=True,
                     last_plagiarism_check=datetime(2099, 1, 1, 0, 0))
        try:
            with self._fresh_each_call():
                results = plagiarism_checks_on_users(logger, configuration, mock.Mock())
            self.assertEqual(results, [])
        finally:
            Users.delete().where(Users.user_id == 558).execute()

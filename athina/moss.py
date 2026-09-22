# -*- coding: utf-8 -*-
"""Plagiarism detection for student submissions.

Runs CopyDetect (https://github.com/blingenf/copydetect) locally to compare
student submissions — no external service or API key required.

This module historically also supported the MOSS online service, which is why
the module name and the database columns are MOSS-based. MOSS support was
removed because it required the ``mosspy`` package (never declared as a
dependency), needed a per-course registration id, and has been superseded by
the local CopyDetect implementation. The ``plagiarism_max`` /
``plagiarism_average`` model fields still map to the legacy ``moss_max`` /
``moss_average`` database columns for backwards compatibility.
"""
import glob
import os
import re
from datetime import datetime, timedelta

import numpy as np
from dateutil.tz import tzlocal

from athina.url import *
from athina.users import *

try:
    import copydetect as _copydetect
    _HAS_COPYDETECT = True
except ImportError:
    _HAS_COPYDETECT = False

__all__ = ('plagiarism_checks_on_users', 'Plagiarism',)


def plagiarism_checks_on_users(logger, configuration, e_learning):
    """Submit plagiarism results for students who received a new grade.

    Only students flagged ``plagiarism_to_grade`` whose last check was more
    than 23 hours ago are considered, so the daily run does not re-check the
    same submissions. Returns a list of
    ``[user_id, max_similarity, mean_similarity]`` entries.
    """
    results = []
    all_students = return_all_students(configuration.course_id, configuration.assignment_id)
    users_graded = [user_object.user_id for user_object in all_students
                    if user_object.plagiarism_to_grade is True and
                    user_object.last_plagiarism_check + timedelta(hours=23) <=
                    datetime.now(tzlocal()).replace(tzinfo=None)]
    logger.logger.info("Checking for plagiarism...")
    logger.logger.debug(users_graded)

    if not users_graded:
        return results

    if not _HAS_COPYDETECT:
        logger.logger.error("copydetect package not installed. Run: pip install copydetect")
        return results

    plagiarism = Plagiarism(logger=logger, service_type="copydetect",
                            threshold=getattr(configuration, 'copydetect_threshold', 0.33))

    directory_list = _collect_submission_directories(configuration)

    comparison_data = plagiarism.check_plagiarism(
        directory_list, configuration.course_id, configuration.assignment_id)

    mean_similarity = _mean_similarity(comparison_data, len(all_students))
    publish = getattr(configuration, 'plagiarism_publish', False)

    for user_id in users_graded:
        user_max_value = _max_similarity_for(comparison_data, user_id)

        if publish:
            e_learning.submit_comment(user_id,
                                      """Your highest similarity score with another student: %s
                                      The mean similarity score is: %s""" %
                                      (user_max_value, mean_similarity))
        results.append([user_id, user_max_value, mean_similarity])
        logger.logger.info("> Submitted similarity results for %s: %s/%s" % (
            user_id, user_max_value, mean_similarity))

        obj = return_a_student(configuration.course_id, configuration.assignment_id, user_id)
        obj.last_plagiarism_check = datetime.now(tzlocal()).replace(tzinfo=None)
        obj.plagiarism_max = user_max_value
        obj.plagiarism_average = mean_similarity
        obj.save()

    for user_object in return_all_students(configuration.course_id, configuration.assignment_id):
        user_object.plagiarism_to_grade = False
        user_object.save()

    return results


def _collect_submission_directories(configuration):
    """Return glob patterns for every student's submission directory."""
    pattern = getattr(configuration, 'plagiarism_pattern', '*.py')
    directory_list = []
    for value in return_all_students(configuration.course_id, configuration.assignment_id):
        base_dir = "%s/repodata%s/u%s/" % (configuration.config_dir, configuration.assignment_id,
                                           value.user_id)
        if os.path.isdir(base_dir) and glob.glob("%s%s" % (base_dir, pattern)):
            directory_list.append("%s%s" % (base_dir, pattern))
    return directory_list


def _mean_similarity(comparison_data, student_count):
    """Mean similarity across all students.

    Students with no matching code are absent from ``comparison_data``, so we
    pad with zeros to keep the mean representative of the whole cohort.
    """
    values = []
    for val in comparison_data.values():
        values.extend(val)
    values.extend([0] * (student_count - len(values)))
    if not values:
        return 0
    return np.mean(np.array(values).astype(float))


def _max_similarity_for(comparison_data, user_id):
    """Highest similarity recorded for a single student (0 when unscored)."""
    try:
        return [np.max(np.array(val)) for key, val in
                comparison_data.items() if key == int(user_id)][0]
    except (RuntimeWarning, IndexError):
        return 0


class Plagiarism:
    """Compare a set of student submissions and report pairwise similarity."""

    service_type = None
    logger = None
    threshold = 0.33

    def __init__(self, logger, **kwargs):
        self.logger = logger
        service = kwargs.get("service_type", None)

        if service == "copydetect":
            if not _HAS_COPYDETECT:
                raise ImportError('copydetect package not installed. Run: pip install copydetect')
            self.service_type = "copydetect"
            self.threshold = kwargs.get("threshold", 0.33)
        else:
            self.service_type = None

    def check_plagiarism(self, folder_list, course_id, assignment_id):
        if self.service_type == "copydetect" and len(folder_list) != 0:
            return self._check_copydetect(folder_list, course_id, assignment_id)
        return dict()

    def _check_copydetect(self, folder_list, course_id, assignment_id):
        """Run CopyDetect over every submission directory.

        ``folder_list`` holds patterns like ``/tmp/.../repodata3/u17/*.py``. The
        per-student directories and file extensions are extracted from those
        patterns, then CopyDetect results are mapped back onto user IDs.
        """
        student_dirs, extensions = self._parse_patterns(folder_list)

        if not student_dirs:
            self.logger.logger.warning("CopyDetect: no student directories found.")
            return dict()

        ext_list = list(extensions) if extensions else None
        self.logger.logger.info("CopyDetect: comparing %d student directories (extensions: %s)" % (
            len(student_dirs), ext_list or "auto"))

        detector = self._run_detector(student_dirs, ext_list)
        if detector is None:
            return dict()

        self._save_html_report(detector, student_dirs, course_id, assignment_id)

        return self._map_results(detector)

    @staticmethod
    def _parse_patterns(folder_list):
        """Split glob patterns into (student directories, file extensions)."""
        student_dirs = []
        extensions = set()
        for pattern in folder_list:
            directory = os.path.dirname(pattern)
            glob_part = os.path.basename(pattern)
            if directory and os.path.isdir(directory):
                student_dirs.append(directory)
            if glob_part.startswith("*."):
                extensions.add(glob_part[2:])
        return student_dirs, extensions

    def _run_detector(self, student_dirs, ext_list):
        """Instantiate and run the CopyDetector, or None on failure."""
        try:
            detector = _copydetect.CopyDetector(
                test_dirs=student_dirs,
                extensions=ext_list,
                display_t=self.threshold,
                disable_autoopen=True
            )
            detector.run()
            return detector
        except Exception as e:
            self.logger.logger.error("CopyDetect error: %s" % str(e))
            return None

    def _save_html_report(self, detector, student_dirs, course_id, assignment_id):
        """Generate the HTML report and record its path against the assignment."""
        report_dir = os.path.join(os.path.dirname(student_dirs[0]), "..")
        report_path = os.path.join(report_dir, "copydetect_report.html")
        try:
            detector.generate_html_report(os.path.dirname(report_path))
            self.logger.logger.info("CopyDetect report saved to: %s" % report_path)
            update_key_in_assignment_data(course_id, assignment_id, "plagiarism_report", report_path)
        except Exception as e:
            self.logger.logger.warning("CopyDetect: could not generate HTML report: %s" % str(e))

    def _map_results(self, detector):
        """Map CopyDetect's similarity matrix onto per-user comparisons.

        CopyDetect indexes files, not students, so file paths are parsed to
        recover the user id from the ``.../u<id>/...`` directory layout.
        """
        file_to_user = {}
        for idx, filepath in enumerate(detector.file_list):
            match = re.search(r'u(\d+)/', filepath)
            if match:
                file_to_user[idx] = int(match.group(1))

        similarity_matrix = detector.similarity
        comparisons = dict()

        n = similarity_matrix.shape[0]
        for i in range(n):
            for j in range(i + 1, n):
                sim_pct = int(round(similarity_matrix[i][j] * 100))
                if sim_pct == 0:
                    continue
                user_i = file_to_user.get(i)
                user_j = file_to_user.get(j)
                if user_i is None or user_j is None or user_i == user_j:
                    continue  # unparseable path, or a self-comparison

                self.parse_comparison_time(comparisons, user_i, sim_pct)
                self.parse_comparison_time(comparisons, user_j, sim_pct)

        self.logger.logger.info("CopyDetect: found comparisons for %d users" % len(comparisons))
        return comparisons

    @staticmethod
    def parse_comparison_time(comparisons, item, value):
        if comparisons.get(int(item), 0) == 0:
            comparisons[int(item)] = [int(value)]
        else:
            comparisons[int(item)].append(int(value))

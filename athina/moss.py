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
import shutil
import tempfile
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

    plagiarism = Plagiarism(
        logger=logger, service_type="copydetect",
        threshold=getattr(configuration, 'copydetect_threshold', 0.33),
        noise_threshold=getattr(configuration, 'copydetect_noise_threshold', 25),
        guarantee_threshold=getattr(configuration, 'copydetect_guarantee_threshold', 25),
        disable_filtering=getattr(configuration, 'copydetect_disable_filtering', False),
        same_name_only=getattr(configuration, 'copydetect_same_name_only', False),
        boilerplate_dir=getattr(configuration, 'copydetect_boilerplate_dir', None),
        max_file_size=getattr(configuration, 'copydetect_max_file_size', 1024 * 1024),
    )

    # Group assignments share one repository per group -- every member's
    # repodata directory is a byte-for-byte copy of the same submission, so
    # comparing them individually would only ever report each group
    # "matching itself" at 100%. Compare one representative per group
    # instead, then copy that result to every member (see
    # _expand_group_comparisons).
    groups = (_group_representatives(configuration.course_id, configuration.assignment_id)
             if getattr(configuration, 'group_assignment', False) else None)

    directory_list = _collect_submission_directories(
        configuration, representative_ids=set(groups) if groups else None)

    comparison_data = plagiarism.check_plagiarism(
        directory_list, configuration.course_id, configuration.assignment_id)

    if groups:
        comparison_data = _expand_group_comparisons(comparison_data, groups)

    mean_similarity = _mean_similarity(comparison_data)
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


def _collect_submission_directories(configuration, representative_ids=None):
    """Return glob patterns for every student's submission directory.

    ``representative_ids``, when given (group assignments), restricts this
    to one representative student per group -- every other member shares
    the same repository and would otherwise just be compared against their
    own groupmates' identical copy of it.
    """
    pattern = getattr(configuration, 'plagiarism_pattern', '*.py')
    directory_list = []
    for value in return_all_students(configuration.course_id, configuration.assignment_id):
        if representative_ids is not None and value.user_id not in representative_ids:
            continue
        base_dir = "%s/repodata%s/u%s/" % (configuration.config_dir, configuration.assignment_id,
                                           value.user_id)
        if os.path.isdir(base_dir) and glob.glob("%s%s" % (base_dir, pattern)):
            directory_list.append("%s%s" % (base_dir, pattern))
    return directory_list


def _group_representatives(course_id, assignment_id):
    """Map each group's one representative student to every member
    sharing their repository_url (including the representative).

    Students with no repository_url (or one not shared with anyone else)
    are their own singleton group, so this is safe to compute even when
    most of the roster isn't actually grouped.
    """
    groups = {}
    url_to_rep = {}
    for student in return_all_students(course_id, assignment_id):
        url = (student.repository_url or "").strip()
        if not url:
            groups[student.user_id] = [student.user_id]
            continue
        rep_id = url_to_rep.get(url)
        if rep_id is None:
            url_to_rep[url] = student.user_id
            groups[student.user_id] = [student.user_id]
        else:
            groups[rep_id].append(student.user_id)
    return groups


def _expand_group_comparisons(comparison_data, groups):
    """Copy each group representative's result to every member of their
    group, so a group's one compared submission is reflected for every
    member, not just whichever one happened to be the representative."""
    expanded = dict(comparison_data)
    for rep_id, member_ids in groups.items():
        if rep_id not in comparison_data:
            continue
        for member_id in member_ids:
            if member_id != rep_id:
                expanded[member_id] = comparison_data[rep_id]
    return expanded


def _mean_similarity(comparison_data):
    """Class-wide average of each compared student's own highest match.

    This averages each student's own max similarity (one value per
    student), not every raw pairwise comparison value -- a raw pairwise
    mean is always lower than any individual's max by construction (a max
    over N values is virtually never below the mean of those same N
    values), which made the previous "class average" an apples-to-oranges
    baseline: it made every student look anomalously elevated relative to
    it, regardless of whether anything was actually unusual.

    A student with no real submission is never a key in
    ``comparison_data`` and is correctly excluded here. The previous
    implementation instead padded the raw-value list with a zero per
    student "missing" from a flattened value count -- two unrelated units
    (students vs. individual match values) -- which, depending on how many
    raw matches existed relative to roster size, could silently pull the
    baseline down using students who were never evaluated at all, exactly
    backwards from what a baseline should do.
    """
    if not comparison_data:
        return 0
    return np.mean(np.array([max(v) for v in comparison_data.values()]).astype(float))


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
    noise_threshold = 25
    guarantee_threshold = 25
    disable_filtering = False
    same_name_only = False
    boilerplate_dir = None
    max_file_size = 1024 * 1024  # bytes; see _stage_filtered_dirs

    def __init__(self, logger, **kwargs):
        self.logger = logger
        service = kwargs.get("service_type", None)

        if service == "copydetect":
            if not _HAS_COPYDETECT:
                raise ImportError('copydetect package not installed. Run: pip install copydetect')
            self.service_type = "copydetect"
            self.threshold = kwargs.get("threshold", 0.33)
            self.noise_threshold = kwargs.get("noise_threshold", 25)
            self.guarantee_threshold = kwargs.get("guarantee_threshold", 25)
            self.disable_filtering = kwargs.get("disable_filtering", False)
            self.same_name_only = kwargs.get("same_name_only", False)
            self.boilerplate_dir = kwargs.get("boilerplate_dir", None)
            self.max_file_size = kwargs.get("max_file_size", 1024 * 1024)
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
        staged_dirs, stage_root = self._stage_filtered_dirs(student_dirs, ext_list)
        try:
            if not staged_dirs:
                self.logger.logger.warning(
                    "CopyDetect: every submission was filtered out (no file under %d bytes)." %
                    self.max_file_size)
                return dict()

            self.logger.logger.info("CopyDetect: comparing %d student directories (extensions: %s)" % (
                len(staged_dirs), ext_list or "auto"))

            report_path = os.path.join(os.path.dirname(student_dirs[0]), "..", "copydetect_report.html")
            detector = self._run_detector(staged_dirs, ext_list, report_path=report_path)
            if detector is None:
                return dict()

            self._save_html_report(detector, student_dirs, course_id, assignment_id)

            return self._map_results(detector)
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)

    def _stage_filtered_dirs(self, student_dirs, ext_list):
        """Symlink each submission file under ``max_file_size`` into a scratch
        staging tree, so CopyDetector never reads an oversized file.

        Students occasionally commit a large file unrelated to their actual
        answers alongside them (a data dump, a build artifact) that happens to
        match the plagiarism glob extension -- e.g. a multi-hundred-MB SQL data
        dump matching a ``*.sql`` pattern meant for five short query answers.
        copydetect has no size cutoff of its own, and winnowing a file that
        size as though it were source code has driven this process to tens of
        GB of memory before it could be killed. Filtering file-by-file (rather
        than dropping the whole student directory on one oversized file) keeps
        every other, legitimately-sized submission from that student in the
        comparison.
        """
        stage_root = tempfile.mkdtemp(prefix="athina_copydetect_")
        staged_dirs = []
        patterns = ["*." + ext for ext in ext_list] if ext_list else ["*"]

        for directory in student_dirs:
            staged = os.path.join(stage_root, os.path.basename(directory.rstrip(os.sep)))
            kept_any = False
            for pattern in patterns:
                for filepath in glob.glob(os.path.join(directory, pattern)):
                    try:
                        size = os.path.getsize(filepath)
                    except OSError:
                        continue
                    if size > self.max_file_size:
                        self.logger.logger.warning(
                            "CopyDetect: skipping %s (%d bytes > max_file_size %d bytes)" % (
                                filepath, size, self.max_file_size))
                        continue
                    if not kept_any:
                        os.makedirs(staged, exist_ok=True)
                        kept_any = True
                    os.symlink(os.path.abspath(filepath), os.path.join(staged, os.path.basename(filepath)))
            if kept_any:
                staged_dirs.append(staged)

        return staged_dirs, stage_root

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

    def _run_detector(self, student_dirs, ext_list, report_path=None):
        """Instantiate and run the CopyDetector, or None on failure.

        copydetect>=0.5 renamed the constructor kwarg that used to be
        ``disable_autoopen`` to ``autoopen`` (and dropped the automatic-open
        default), and ``generate_html_report`` no longer takes a path -- the
        output path must be set on construction via ``out_file`` instead.
        """
        try:
            kwargs = dict(
                test_dirs=student_dirs,
                extensions=ext_list,
                display_t=self.threshold,
                noise_t=self.noise_threshold,
                guarantee_t=self.guarantee_threshold,
                disable_filtering=self.disable_filtering,
                same_name_only=self.same_name_only,
                autoopen=False,
            )
            if report_path is not None:
                kwargs["out_file"] = report_path
            if self.boilerplate_dir is not None and os.path.isdir(self.boilerplate_dir):
                kwargs["boilerplate_dirs"] = [self.boilerplate_dir]
            detector = _copydetect.CopyDetector(**kwargs)
            detector.run()
            return detector
        except Exception as e:
            self.logger.logger.error("CopyDetect error: %s" % str(e))
            return None

    def _save_html_report(self, detector, student_dirs, course_id, assignment_id):
        """Generate the HTML report and record its path against the assignment.

        The report path must already have been set as ``out_file`` when the
        detector was constructed (see ``_run_detector``); copydetect>=0.5's
        ``generate_html_report`` writes there and takes no path argument.
        """
        report_dir = os.path.join(os.path.dirname(student_dirs[0]), "..")
        report_path = os.path.join(report_dir, "copydetect_report.html")
        try:
            detector.generate_html_report()
            self.logger.logger.info("CopyDetect report saved to: %s" % report_path)
            update_key_in_assignment_data(course_id, assignment_id, "plagiarism_report", report_path)
        except Exception as e:
            self.logger.logger.warning("CopyDetect: could not generate HTML report: %s" % str(e))

    def _map_results(self, detector):
        """Map CopyDetect's similarity matrix onto per-user comparisons.

        CopyDetect indexes files, not students, so file paths are parsed to
        recover the user id from the ``.../u<id>/...`` directory layout.

        copydetect>=0.5 renamed ``file_list``/``similarity`` to
        ``test_files``/``similarity_matrix``, and the matrix now holds a pair
        of asymmetric containment percentages per file pair (how much of
        file i overlaps file j, and vice versa -- they differ when the two
        files are different lengths) instead of one symmetric value. We take
        the higher of the two, since either direction is equally valid
        evidence of overlap and a partial copy embedded in a longer file
        should not be diluted by the longer file's own size.
        """
        file_to_user = {}
        for idx, filepath in enumerate(detector.test_files):
            match = re.search(r'u(\d+)/', filepath)
            if match:
                file_to_user[idx] = int(match.group(1))

        similarity_matrix = detector.similarity_matrix
        comparisons = dict()

        n = similarity_matrix.shape[0]
        for i in range(n):
            for j in range(i + 1, n):
                sim_i, sim_j = similarity_matrix[i][j]
                sim_pct = int(round(max(sim_i, sim_j) * 100))
                if sim_pct <= 0:
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

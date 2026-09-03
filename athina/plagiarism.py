# -*- coding: utf-8 -*-
import glob
import os
import re
from datetime import timedelta, datetime

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
    # Report plagiarism to any newly submitted grades using CopyDetect
    results = []
    users_graded = [user_object.user_id for user_object in
                    return_all_students(configuration.course_id, configuration.assignment_id)
                    if user_object.plagiarism_to_grade is True and
                    user_object.last_plagiarism_check + timedelta(hours=23) <=
                    datetime.now(tzlocal()).replace(tzinfo=None)]
    logger.logger.info("Checking for plagiarism...")
    logger.logger.debug(users_graded)

    if len(users_graded) != 0:
        if not _HAS_COPYDETECT:
            logger.logger.error("copydetect package not installed. Run: pip install copydetect")
            return results

        plagiarism = Plagiarism(logger=logger,
                                service_type="copydetect",
                                threshold=getattr(configuration, 'copydetect_threshold', 0.33))
        directory_list = []
        for value in return_all_students(configuration.course_id, configuration.assignment_id):
            base_dir = "%s/repodata%s/u%s/" % (configuration.config_dir, configuration.assignment_id,
                                               value.user_id)
            pattern = getattr(configuration, 'plagiarism_pattern', '*.py')
            if os.path.isdir(base_dir) and glob.glob("%s%s" % (base_dir, pattern)):
                directory_list.append("%s%s" % (base_dir, pattern))

        # Execute plagiarism check for the directories
        comparison_data = plagiarism.check_plagiarism(directory_list, configuration.course_id,
                                                      configuration.assignment_id)

        values = []
        [[values.append(v) for v in val] for key, val in comparison_data.items()]
        # values does not include users that were found to not have any similar code
        # we add these to get the proper mean similarity scores
        db_rows = return_all_students(configuration.course_id, configuration.assignment_id)
        for i in range(0, len(db_rows) - len(values)):
            values.append(0)
        if len(values) != 0:  # this if is probably useless but kept here just in case
            mean_similarity = np.mean(np.array(values).astype(float))
        else:
            mean_similarity = 0

        plagiarism_publish = getattr(configuration, 'plagiarism_publish', False)

        for user_id in users_graded:
            try:
                user_max_value = [np.max(np.array(val)) for key, val in
                                  comparison_data.items() if key == int(user_id)][0]
            except (RuntimeWarning, IndexError):
                user_max_value = 0

            if plagiarism_publish:
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


class Plagiarism:
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
        else:
            return dict()

    def _check_copydetect(self, folder_list, course_id, assignment_id):
        """Run CopyDetect plagiarism detection locally.

        Extracts per-student directories from the glob patterns in folder_list,
        runs CopyDetect, and maps results back to user IDs.
        """
        # folder_list contains patterns like "/tmp/.../repodata3/u17/*.py"
        # Extract unique student directories and file extensions
        student_dirs = []
        extensions = set()
        for pattern in folder_list:
            # Split off the glob part to get the directory
            directory = os.path.dirname(pattern)
            glob_part = os.path.basename(pattern)
            if directory and os.path.isdir(directory):
                student_dirs.append(directory)
            # Extract extension from pattern like "*.py" -> "py"
            if glob_part.startswith("*."):
                ext = glob_part[2:]
                extensions.add(ext)

        if not student_dirs:
            self.logger.logger.warning("CopyDetect: no student directories found.")
            return dict()

        ext_list = list(extensions) if extensions else None

        self.logger.logger.info("CopyDetect: comparing %d student directories (extensions: %s)" % (
            len(student_dirs), ext_list or "auto"))

        try:
            detector = _copydetect.CopyDetector(
                test_dirs=student_dirs,
                extensions=ext_list,
                display_t=self.threshold,
                disable_autoopen=True
            )
            detector.run()
        except Exception as e:
            self.logger.logger.error("CopyDetect error: %s" % str(e))
            return dict()

        # Generate HTML report
        report_dir = os.path.join(os.path.dirname(student_dirs[0]), "..")
        report_path = os.path.join(report_dir, "copydetect_report.html")
        try:
            detector.generate_html_report(os.path.dirname(report_path))
            self.logger.logger.info("CopyDetect report saved to: %s" % report_path)
            update_key_in_assignment_data(course_id, assignment_id, "plagiarism_report", report_path)
        except Exception as e:
            self.logger.logger.warning("CopyDetect: could not generate HTML report: %s" % str(e))

        # Map file indices to user IDs by parsing directory structure
        file_list = detector.file_list
        file_to_user = {}
        for idx, filepath in enumerate(file_list):
            # Extract user ID from path like "/tmp/.../repodata3/u17/file.py"
            match = re.search(r'u(\d+)/', filepath)
            if match:
                file_to_user[idx] = int(match.group(1))

        # Parse similarity matrix into per-user comparison data
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
                if user_i is None or user_j is None:
                    continue
                if user_i == user_j:
                    continue  # skip self-comparisons

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

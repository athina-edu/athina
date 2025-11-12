# -*- coding: utf-8 -*-
import glob
import os
import re
from datetime import timedelta, datetime

import mosspy
import numpy as np
from dateutil.tz import tzlocal

from athina.url import *
from athina.users import *

__all__ = ('plagiarism_checks_on_users', 'Plagiarism',)


def plagiarism_checks_on_users(logger, configuration, e_learning):
    # Report plagiarism to any newly submitted grades (currently uses only MOSS)
    results = []
    users_graded = [user_object.user_id for user_object in
                    return_all_students(configuration.course_id, configuration.assignment_id)
                    if user_object.plagiarism_to_grade is True and
                    user_object.last_plagiarism_check + timedelta(hours=23) <=
                    datetime.now(tzlocal()).replace(tzinfo=None)]
    logger.logger.info("Checking for plagiarism...")
    logger.logger.debug(users_graded)

    # Check if the user requested a plagiarism check (in the cfg if the settings exist)
    if len(users_graded) != 0 and configuration.moss_id != 1:
        plagiarism = Plagiarism(logger=logger,
                                service_type="moss",
                                moss_id=configuration.moss_id,
                                moss_lang=configuration.moss_lang)
        directory_list = []
        for value in return_all_students(configuration.course_id, configuration.assignment_id):
            base_dir = "%s/repodata%s/u%s/" % (configuration.config_dir, configuration.assignment_id,
                                               value.user_id)
            if os.path.isdir(base_dir) and glob.glob("%s%s" % (base_dir, configuration.moss_pattern)):
                directory_list.append("%s%s" % (base_dir, configuration.moss_pattern))

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
            mean_similarity = np.mean(np.array(values).astype(np.float))
        else:
            mean_similarity = 0

        for user_id in users_graded:
            try:
                user_max_value = [np.max(np.array(val)) for key, val in
                                  comparison_data.items() if key == int(user_id)][0]
            except (RuntimeWarning, IndexError):
                user_max_value = 0

            if configuration.moss_publish:
                e_learning.submit_comment(user_id,
                                          """Your highest similarity score with another student: %s
                                          The mean similarity score is: %s""" %
                                          (user_max_value, mean_similarity))
            results.append([user_id, user_max_value, mean_similarity])
            logger.logger.info("> Submitted similarity results for %s: %s/%s" % (
                user_id, user_max_value, mean_similarity))
            obj = return_a_student(configuration.course_id, configuration.assignment_id, user_id)
            obj.last_plagiarism_check = datetime.now(tzlocal()).replace(tzinfo=None)
            obj.moss_max = user_max_value
            obj.moss_average = mean_similarity
            obj.save()

        for user_object in return_all_students(configuration.course_id, configuration.assignment_id):
            user_object.plagiarism_to_grade = False
            user_object.save()

    return results


class Plagiarism:
    service_type = None
    moss_id = None
    moss_lang = None
    dir_path = None
    logger = None

    def __init__(self, logger, **kwargs):
        if kwargs.get("service_type", 0) == "moss":
            try:
                self.service_type = kwargs["service_type"]
                self.moss_id = kwargs["moss_id"]
                self.moss_lang = kwargs["moss_lang"]
            except KeyError:
                raise KeyError('Moss type requires parameters moss_id, moss_lang')

            self.logger = logger

    def check_plagiarism(self, folder_list, course_id, assignment_id):
        if self.service_type == "moss" and len(folder_list) != 0:
            moss = mosspy.Moss(self.moss_id, self.moss_lang)
            for folder in folder_list:
                moss.addFilesByWildcard(folder)
            try:
                url = moss.send()
            except Exception:
                self.logger.logger.error("An error occured with the moss script.")
                return dict()
            except (SystemExit, KeyboardInterrupt):
                pass

            update_key_in_assignment_data(course_id, assignment_id, "moss_url", url)

            self.logger.logger.info("Attempting to get results from moss url: %s" % url)

            try:
                text = request_url(url, return_type="text")
            except IndexError:
                text = ""

            matches = re.findall(r"<TR>.+?u(\d+?)/.+?(\d+)%.+?u(\d+?)/.+?(\d+)%", text, re.DOTALL)

            comparisons = dict()
            for item in matches:
                self.parse_comparison_time(comparisons, item[0], item[1])
                self.parse_comparison_time(comparisons, item[2], item[3])
            return comparisons
        else:
            return dict()

    @staticmethod
    def parse_comparison_time(comparisons, item, value):
        if comparisons.get(int(item), 0) == 0:
            comparisons[int(item)] = [int(value)]
        else:
            comparisons[int(item)].append(int(value))

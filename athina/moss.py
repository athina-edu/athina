# -*- coding: utf-8 -*-

import re
import mosspy
from athina.users import *
from athina.url import *


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

    def check_plagiarism(self, folder_list):
        if self.service_type == "moss" and len(folder_list) != 0:
            moss = mosspy.Moss(self.moss_id, self.moss_lang)
            for folder in folder_list:
                moss.addFilesByWildcard(folder)
            try:
                url = moss.send()
            except:
                self.logger.logger.error("An error occured with the moss script.")
                return dict()

            update_key_in_assignment_data("moss_url", url)

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

# -*- coding: utf-8 -*-
"""
Plagiarism Checker

Created on Thu Sep 21 12:04:01 2017

@author: Michael Tsikerdekis
"""

import subprocess
import time
import re
import requests
import os


class Plagiarism:
    service_type = None
    moss_id = None
    moss_lang = None
    dir_path = None

    def __init__(self, **kwargs):
        if kwargs.get("service_type", 0) == "moss":
            try:
                self.service_type = kwargs["service_type"]
                self.moss_id = kwargs["moss_id"]
                self.moss_lang = kwargs["moss_lang"]
            except KeyError:
                raise KeyError('Moss type requires parameters moss_id, moss_lang')

            self.dir_path = os.path.dirname(os.path.realpath(__file__))

            # Add userid to mossnet file
            subprocess.run(" ".join(["sed", "'s/\$userid=.*;/\$userid=%d;/g'" % self.moss_id,
                                     "%s/mossnet" % self.dir_path,
                                     ">",
                                     "%s/mossnet.pl" % self.dir_path]), shell=True)

    def check_plagiarism(self, folder_list):
        if self.service_type == "moss" and len(folder_list) != 0:
            process = subprocess.Popen(
                " ".join(["perl", "%s/mossnet.pl" % self.dir_path, "-l", "python", "-d"] + folder_list),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True)
            print(" ".join(["perl", "%s/mossnet.pl" % self.dir_path, "-l", "python", "-d"] + folder_list))
            out, err = process.communicate()
            print(err)
            print(out)
            print(out.decode("ascii").split("\n")[-2])

            text = self.request_url(out.decode("ascii").split("\n")[-2], return_type="text")
            matches = re.findall("<TR>.*?u(\d*?)\/ \((.*?)\%\).*?u(\d*?)\/ \((.*?)\%\)",
                                 text, re.DOTALL)

            comparisons = dict()
            for item in matches:
                self.parse_comparison_time(comparisons, item[0], item[1])
                self.parse_comparison_time(comparisons, item[2], item[3])
            return comparisons

    @staticmethod
    def parse_comparison_time(comparisons, item, value):
        if comparisons.get(int(item), 0) == 0:
            comparisons[int(item)] = [int(value)]
        else:
            comparisons[int(item)].append(int(value))

    @staticmethod
    def request_url(url, headers=None, payload=None, method="get", return_type="json"):
        time.sleep(1)  # artificial delay in case code attempts to spam with requests
        headers = {} if headers is None else headers
        payload = {} if payload is None else payload
        if method == "get":
            r = requests.get(url, headers=headers)
        elif method == "put":
            r = requests.put(url, headers=headers, data=payload)
        if return_type == "json":
            return r.json()
        elif return_type == "text":
            return r.text

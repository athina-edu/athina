#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# TODO: Functions need to be created to change values in the user_db..or better yet, just change everything into nosql

from datetime import datetime, timezone
import pickle
import os


class Users:
    # db is meant to be a dict
    # contains object User and indexed by a user's user_id
    # Why set it as None? See below:
    # http://effbot.org/zone/default-values.htm
    db = None
    same_url_limit = 1  # Defines how many same urls are allowed, e.g., in cases of group projects this may be 2
    logger = None

    def __init__(self, logger=None):
        self.db = dict()
        self.logger = logger

    def load(self, file_location):
        """
        Load stored dataset for users and submission URLs (if exists)
        """
        # TODO: Detect incompatible or older version of pkl and reset
        try:
            pkl_file = open(file_location, 'rb')
            user_list = pickle.load(pkl_file)
            pkl_file.close()
            if self.logger is not None:
                self.logger.vprint("Loaded saved file: %s" % file_location, debug=True)
            for user_id, user_values in user_list.items():
                self.db[user_id] = pickle.loads(user_values)
        except FileNotFoundError:
            if self.logger is not None:
                self.logger.vprint("Warning: Cannot load Users db (probably this is a first run for a new assignment).")
            return self
        return self

    def save(self, dir_name):
        export_object = dict()  # this is what we will eventually save

        for user_id, user_values in self.db.items():
            export_object[user_id] = pickle.dumps(user_values)

        with open(dir_name, 'wb') as pkl_file:
            pickle.dump(export_object, pkl_file)
        os.chmod(dir_name, 0o666)

    def check_duplicate_url(self, same_url_limit=1):
        """Checks if there are duplicate urls submitted.

        @param same_url_limit: number of occurrences to be found to be considered plagiarism
        @return: None
        """
        urls = []
        ids = []
        for key, val in self.db.items():
            if val.repository_url != "" and val.repository_url is not None:
                urls.append(val.repository_url)
                ids.append(key)
                indices = [i for i, x in enumerate(urls) if x == val.repository_url]
                if len(indices) > same_url_limit:
                    for i in indices:
                        self.db[ids[i]].same_url_flag = True
                else:
                    self.db[key].same_url_flag = False

    class User:
        """
        The class containing local information obtained through canvas about each user, assignment status, last commit,
        plagiarism status and other important information for ATHINA to function.
        """
        user_id = ""
        user_fullname = ""
        secondary_id = ""  # This is usually on canvas the second column on grades (username or email of user)
        repository_url = ""
        url_date = datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc)  # The date a new url was discovered
        new_url = False  # Switched when new url is discovered on e-learning site
        commit_date = datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc)  # The day of the last commit observed
        same_url_flag = False  # This is switched to True if the repo url is found to similar with N other students
        plagiarism_to_grade = False  # Signifies whether a user received a new grade (plagiarism)
        last_plagiarism_check = datetime.now(timezone.utc)
        last_graded = datetime(1, 1, 1, 0, 0).replace(tzinfo=timezone.utc)
        last_grade = 0
        changed_state = False

        def __init__(self, user_id):
            self.user_id = user_id

            pass

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from datetime import datetime, timezone
import os
from peewee import *

# Global database object
DB = SqliteDatabase(None)


class Database:
    db = None
    same_url_limit = 1  # Defines how many same urls are allowed, e.g., in cases of group projects this may be 2
    logger = None
    db_filename = None

    def __init__(self, db_filename='database.sqlite3', logger=None):
        self.db = DB
        self.db_filename = db_filename
        self.logger = logger

        if self.logger is not None:
            self.logger.logger.debug("Connecting to db file: %s" % db_filename)

        self.connect_to_db(self.db_filename)
        if not self.database_is_healthy:
            if self.logger is not None:
                self.logger.logger.error("Warning: Cannot load db. Starting from scratch.")
            self.close_db()
            os.remove(self.db_filename)
            self.connect_to_db(self.db_filename)

        os.chmod(db_filename, 0o666)

    def __del__(self):
        self.close_db()

    @staticmethod
    def connect_to_db(db):
        DB.init(db)
        DB.connect()
        DB.create_tables([Users, AssignmentData])

    @staticmethod
    def close_db():
        DB.close()

    @property
    def database_is_healthy(self):
        try:
            user = Users.select().limit(1)[0]
            assignment = AssignmentData.select().limit(1)[0]
            return True
        except OperationalError:
            # Database specifications have changed (e.g., newer athina version)
            # delete old sql file and start a new
            return False
        except IndexError:
            return True  # If the database is empty, this is a normal error

    @staticmethod
    def check_duplicate_url(same_url_limit=1):
        """Checks if there are duplicate urls submitted.

        @param same_url_limit: number of occurrences to be found to be considered plagiarism
        @return: None
        """
        urls = dict()
        for val in Users.select().where(Users.repository_url != ""):
            if urls.get(val.repository_url, 0) == 0:
                urls[val.repository_url] = [val.user_id]
            else:
                urls[val.repository_url].append(val.user_id)

            if len(urls[val.repository_url]) > same_url_limit:
                for i in urls[val.repository_url]:
                    obj = Users.get(Users.user_id == i)
                    if obj.same_url_flag is not True:
                        obj.same_url_flag = True
                        obj.save()
            else:
                if val.same_url_flag is not False:
                    val.same_url_flag = False
                    val.save()


class Users(Model):
    """
    The class containing local information obtained through canvas about each user, assignment status, last commit,
    plagiarism status and other important information for ATHINA to function.
    """
    user_id = BigIntegerField(primary_key=True)
    course_id = BigIntegerField(default=0)
    user_fullname = TextField(default="")
    secondary_id = TextField(default="")
    repository_url = TextField(default="", null=True)
    url_date = DateTimeField(default=datetime(1, 1, 1, 0, 0))  # When a new url was found
    new_url = BooleanField(default=False)  # Switched when new url is discovered on e-learning site
    commit_date = DateTimeField(default=datetime(1, 1, 1, 0, 0))  # Day of the last commit
    same_url_flag = BooleanField(default=False)  # Is repo url found to be similar with N other students?
    plagiarism_to_grade = BooleanField(default=False)  # Signifies whether a user received a new grade (plagiarism)
    last_plagiarism_check = DateTimeField(default=datetime.now(timezone.utc).replace(tzinfo=None))
    last_graded = DateTimeField(default=datetime(1, 1, 1, 0, 0))
    changed_state = BooleanField(default=False)
    last_grade = SmallIntegerField(null=True)
    last_report = BlobField(default="", null=True)
    moss_max = IntegerField(default=0, null=True)
    moss_average = IntegerField(default=0, null=True)

    class Meta:
        database = DB


class AssignmentData(Model):
    """
    Key-value database to store extra assignment info
    """
    key = TextField(primary_key=True)
    value = TextField(default="", null=True)

    class Meta:
        database = DB


def update_key_in_assignment_data(key, value):
    try:
        obj = AssignmentData.get(AssignmentData.key == key)
        obj.value = value
        obj.save()
    except AssignmentData.DoesNotExist:
        AssignmentData.create(key=key, value=value)


def load_key_from_assignment_data(key):
    try:
        obj = AssignmentData.get(AssignmentData.key == key)
        return obj.value
    except AssignmentData.DoesNotExist:
        return None

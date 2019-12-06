#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from datetime import datetime
from dateutil.tz import tzlocal
import os
import peewee
import time
import sqlite3
import re


# Overriding peewee's execute to account for database locks and wait until other processes finish.
# This is the cleanest patchy solution. Lines of code from peewee are flagged
# There are alternative solutions such as using sqlite3 WAL (partially solves the problem for concurrent read-write)
# Other alternatives:
# from playhouse.sqliteq import SqliteQueueDatabase
# import playhouse.apsw_ext as apsw_ext
# APSW is supposedly portable across threads but it doesn't work with map
# It may however internally handle lock issues.
def _execute(self, query, commit=peewee.SENTINEL, **context_options):
    success = False
    trial_counter = 0
    while success is False:
        try:
            ctx = self.get_sql_context(**context_options)  # derived from original execute func
            sql, params = ctx.sql(query).query()  # derived from original execute func
            return self.execute_sql(sql, params, commit=commit)  # derived from original execute func
        except (peewee.OperationalError, sqlite3.OperationalError) as es:
            if "locked" in str(es):
                if trial_counter == 50:
                    DB.close()
                time.sleep(0.2)
                trial_counter += 1
                success = False
            else:
                raise peewee.OperationalError


peewee.Database.execute = _execute  # overriding peewee execute


# Global database object
DB = peewee.SqliteDatabase(None)


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
        except peewee.OperationalError:
            # Database specifications have changed (e.g., newer athina version)
            # delete old sql file and start a new
            return False
        except IndexError:
            return True  # If the database is empty, this is a normal error

    # TODO: Technically this is a tester item, not part of db function
    @staticmethod
    def check_duplicate_url(same_url_limit=1, repo_type=".git"):
        """Checks if there are duplicate urls submitted.

        @param same_url_limit: number of occurrences to be found to be considered plagiarism
        @param repo_type: in case there are variants for urls by a repository. Currently only git is supported
        @return: None
        """
        urls = dict()
        for val in Users.select().where(Users.repository_url != ""):
            truncated_url = re.sub(r"(%s)$" % repo_type, "", val.repository_url)
            if urls.get(truncated_url, 0) == 0:
                urls[truncated_url] = [val.user_id]
            else:
                urls[truncated_url].append(val.user_id)

            if len(urls[truncated_url]) > same_url_limit:
                for i in urls[truncated_url]:
                    obj = Users.get(Users.user_id == i)
                    if obj.same_url_flag is not True:
                        obj.same_url_flag = True
                        obj.save()
            else:
                if val.same_url_flag is not False:
                    val.same_url_flag = False
                    val.save()


class BaseModel(peewee.Model):
    class Meta:
        database = DB


class Users(BaseModel):
    """
    The class containing local information obtained through canvas about each user, assignment status, last commit,
    plagiarism status and other important information for ATHINA to function.
    """
    user_id = peewee.BigIntegerField(primary_key=True)
    course_id = peewee.BigIntegerField(default=0)
    user_fullname = peewee.TextField(default="")
    secondary_id = peewee.TextField(default="")
    repository_url = peewee.TextField(default="", null=True)
    url_date = peewee.DateTimeField(default=datetime(1, 1, 1, 0, 0))  # When a new url was found
    new_url = peewee.BooleanField(default=False)  # Switched when new url is discovered on e-learning site
    commit_date = peewee.DateTimeField(default=datetime(1, 1, 1, 0, 0))  # Day of the last commit
    same_url_flag = peewee.BooleanField(default=False)  # Is repo url found to be similar with N other students?
    plagiarism_to_grade = peewee.BooleanField(default=False)  # Signifies if a user received a new grade (plagiarism)
    last_plagiarism_check = peewee.DateTimeField(default=datetime.now(tzlocal()).replace(tzinfo=None))
    last_graded = peewee.DateTimeField(default=datetime(1, 1, 1, 0, 0))
    changed_state = peewee.BooleanField(default=False)
    last_grade = peewee.SmallIntegerField(null=True)
    last_report = peewee.BlobField(default="", null=True)
    moss_max = peewee.IntegerField(default=0, null=True)
    moss_average = peewee.IntegerField(default=0, null=True)
    tester_active = peewee.BooleanField(default=False)
    tester_date = peewee.DateTimeField(default=datetime(1, 1, 1, 0, 0))
    force_test = peewee.BooleanField(default=False)


class AssignmentData(BaseModel):
    """
    Key-value database to store extra assignment info
    """
    key = peewee.TextField(primary_key=True)
    value = peewee.TextField(default="", null=True)


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

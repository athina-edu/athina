#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
from datetime import datetime

import peewee
import pymysql
from dateutil.tz import tzlocal

__all__ = ('Database', 'Users', 'AssignmentData', "update_key_in_assignment_data", "load_key_from_assignment_data",
           "return_all_students", "return_a_student",)

# Global database object
DB = peewee.MySQLDatabase(None)
ATHINA_MYSQL_HOST = os.environ['ATHINA_MYSQL_HOST']
ATHINA_MYSQL_PORT = os.environ['ATHINA_MYSQL_PORT']
ATHINA_MYSQL_USERNAME = os.environ['ATHINA_MYSQL_USERNAME']
ATHINA_MYSQL_PASSWORD = os.environ['ATHINA_MYSQL_PASSWORD']

class Database:
    db = None
    same_url_limit = 1  # Defines how many same urls are allowed, e.g., in cases of group projects this may be 2
    logger = None

    def __init__(self, logger=None):
        self.db = DB
        self.logger = logger

        if self.logger is not None:
            self.logger.logger.debug("Connecting to mysql on %s:%s" % (ATHINA_MYSQL_HOST, ATHINA_MYSQL_PORT))

        self.connect_to_db()
        if not self.database_is_healthy:
            if self.logger is not None:
                self.logger.logger.error("Warning: Cannot load db. Starting from scratch.")
            self.close_db()
            self.reset_database()
            self.connect_to_db()

    def __del__(self):
        self.close_db()

    def connect_to_db(self):
        DB.init("athina", user=ATHINA_MYSQL_USERNAME, password=ATHINA_MYSQL_PASSWORD, host=ATHINA_MYSQL_HOST,
                port=int(ATHINA_MYSQL_PORT))
        try:
            DB.connect()
        except peewee.InternalError as error:
            if error.args[0] == 1049:
                # Create the database first (which outside peewee's capabilities
                self.reset_database()
                DB.connect()
        DB.create_tables([Users, AssignmentData])

    @staticmethod
    def close_db():
        DB.close()

    @staticmethod
    def reset_database():
        conn = pymysql.connect(host=ATHINA_MYSQL_HOST, user=ATHINA_MYSQL_USERNAME,
                               password=ATHINA_MYSQL_PASSWORD, port=int(ATHINA_MYSQL_PORT))
        conn.cursor().execute('DROP DATABASE IF EXISTS athina')
        conn.cursor().execute('CREATE DATABASE athina')
        conn.close()

    @property
    def database_is_healthy(self):
        try:
            dummy = Users.select().limit(1)[0]
            dummy = AssignmentData.select().limit(1)[0]
            return True
        except peewee.OperationalError:
            # Database specifications have changed (e.g., newer athina version)
            # delete old sql file and start a new
            exit(1)
            return False
        except peewee.InternalError as error:
            if error.args[0] == 1054:  # Database fields have changed
                # Create the database first (which outside peewee's capabilities
                self.reset_database()
                DB.connect()
        except IndexError:
            return True  # If the database is empty, this is a normal error

    def check_duplicate_url(self, same_url_limit=1, repo_type=".git", course_id=1, assignment_id=1):
        """Checks if there are duplicate urls submitted.

        :param same_url_limit: number of occurrences to be found to be considered plagiarism
        :param repo_type: in case there are variants for urls by a repository. Currently only git is supported
        :param course_id: course id
        :param assignment_id: assignment id
        :return: None
        """
        urls = dict()
        for val in return_all_students(course_id, assignment_id).where(Users.repository_url != ""):
            truncated_url = re.sub(r"(%s)$" % repo_type, "", val.repository_url)
            if urls.get(truncated_url, 0) == 0:
                urls[truncated_url] = [val.user_id]
            else:
                urls[truncated_url].append(val.user_id)

            if len(urls[truncated_url]) > same_url_limit:
                for i in urls[truncated_url]:
                    obj = return_a_student(course_id, assignment_id, i)
                    self._set_same_url_flag(obj, True)
            else:
                self._set_same_url_flag(val, False)

    @staticmethod
    def _set_same_url_flag(user_object, result=True):
        if user_object.same_url_flag is not result:
            user_object.same_url_flag = result
            user_object.save()


class BaseModel(peewee.Model):
    class Meta:
        database = DB


class Users(BaseModel):
    """
    The class containing local information obtained through canvas about each user, assignment status, last commit,
    plagiarism status and other important information for ATHINA to function.
    """
    user_id = peewee.BigIntegerField()
    course_id = peewee.BigIntegerField()
    assignment_id = peewee.BigIntegerField()
    user_fullname = peewee.CharField(max_length=255, default="")
    secondary_id = peewee.CharField(max_length=255, default="")
    repository_url = peewee.CharField(max_length=255, default="", null=True)
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
    use_webhook = peewee.BooleanField(default=False)
    webhook_event = peewee.BooleanField(default=False)
    webhook_token = peewee.CharField(max_length=255, default='')

    class Meta:
        db_table = 'users'
        primary_key = peewee.CompositeKey('user_id', 'course_id', 'assignment_id')
        indexes = (
            # create a non-unique index, webhook push events get a speed boost
            (('repository_url', 'webhook_token'), False),
            # create a non-unique index
            (('course_id', 'assignment_id'), False),
        )


class AssignmentData(BaseModel):
    """
    Key-value database to store extra assignment info
    """
    course_id = peewee.BigIntegerField()
    assignment_id = peewee.BigIntegerField()
    variable = peewee.CharField(max_length=255)
    variable_value = peewee.TextField(default="", null=True)

    class Meta:
        db_table = 'assignmentdata'
        primary_key = peewee.CompositeKey('variable', 'course_id', 'assignment_id')


def update_key_in_assignment_data(course_id, assignment_id, variable, variable_value):
    try:
        obj = AssignmentData.get(AssignmentData.variable == variable, AssignmentData.course_id == course_id,
                                 AssignmentData.assignment_id == assignment_id)
        obj.variable_value = variable_value
        obj.save()
    except AssignmentData.DoesNotExist:
        AssignmentData.create(variable=variable, variable_value=variable_value, assignment_id=assignment_id,
                              course_id=course_id)


def load_key_from_assignment_data(course_id, assignment_id, variable):
    try:
        obj = AssignmentData.get(AssignmentData.variable == variable, AssignmentData.course_id == course_id,
                                 AssignmentData.assignment_id == assignment_id)
        return obj.variable_value
    except AssignmentData.DoesNotExist:
        return None


def return_all_students(course_id, assignment_id):
    return Users.select().where(Users.course_id == course_id, Users.assignment_id == assignment_id)


def return_a_student(course_id, assignment_id, user_id):
    return Users.get(Users.course_id == course_id, Users.assignment_id == assignment_id, Users.user_id == user_id)

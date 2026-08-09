# Tests for athina.users (Peewee ORM models + DB helpers).
from unittest import mock, TestCase

from athina.users import Database, Users, AssignmentData, update_key_in_assignment_data, \
    load_key_from_assignment_data, return_all_students, return_a_student
from tests.test_athina import create_fake_user_db, create_logger


class TestUsers(TestCase):
    def test_database_init_with_logger(self):
        logger = create_logger()
        user_data = Database(logger=logger)
        self.assertIsNotNone(user_data.db)

    def test_database_health_empty_db(self):
        # Empty DB should be considered healthy (IndexError path)
        user_data = Database()
        user_data.db.drop_tables([Users, AssignmentData])
        user_data.db.create_tables([Users, AssignmentData])
        self.assertTrue(user_data.database_is_healthy)

    def test_update_key_in_assignment_data_create(self):
        update_key_in_assignment_data(1, 1, "cov_var", "cov_val")
        self.assertEqual(load_key_from_assignment_data(1, 1, "cov_var"), "cov_val")

    def test_update_key_in_assignment_data_update(self):
        update_key_in_assignment_data(1, 1, "cov_var2", "v1")
        update_key_in_assignment_data(1, 1, "cov_var2", "v2")
        self.assertEqual(load_key_from_assignment_data(1, 1, "cov_var2"), "v2")

    def test_load_key_missing(self):
        self.assertIsNone(load_key_from_assignment_data(1, 1, "cov_missing_key"))

    def test_check_duplicate_url(self):
        user_data = create_fake_user_db()
        user_data.check_duplicate_url(same_url_limit=1)
        obj = return_a_student(1, 1, 3)
        self.assertTrue(obj.same_url_flag)

    def test_check_duplicate_url_no_duplicate(self):
        user_data = create_fake_user_db()
        user_data.check_duplicate_url(same_url_limit=2)
        obj = return_a_student(1, 1, 3)
        self.assertFalse(obj.same_url_flag)

    def test_set_same_url_flag(self):
        user_data = create_fake_user_db()
        obj = return_a_student(1, 1, 1)
        user_data._set_same_url_flag(obj, True)
        self.assertTrue(return_a_student(1, 1, 1).same_url_flag)
        user_data._set_same_url_flag(obj, True)  # no change, no save
        user_data._set_same_url_flag(obj, False)
        self.assertFalse(return_a_student(1, 1, 1).same_url_flag)

    def test_database_health(self):
        user_data = create_fake_user_db()
        self.assertTrue(user_data.database_is_healthy)

    def test_return_all_students(self):
        create_fake_user_db()
        students = return_all_students(1, 1)
        self.assertGreaterEqual(len(list(students)), 1)

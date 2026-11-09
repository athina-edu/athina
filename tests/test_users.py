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

    def test_last_report_column_is_wide_enough_for_a_full_report(self):
        """A report may exceed 64 KB, so the column must not be a plain BLOB.

        `max_file_size` is expressed in KB (1024 by default) while MySQL's BLOB
        tops out at 65,535 bytes; overflowing it raised 1406 and aborted the
        post-grading save, which re-graded the student on every poll.
        """
        self.assertEqual(Users.last_report.field_type, 'MEDIUMBLOB')

    @staticmethod
    def _upgrade_calls(blob_type, fallback_column_present):
        """Run _upgrade_schema against a fake DB and return the emitted SQL.

        Each execute_sql returns its own cursor: the blob probe, the fallback
        probe, then (only when needed) the ALTER statements.
        """
        database = Database.__new__(Database)

        def _cursor(value):
            cur = mock.Mock()
            cur.fetchone.return_value = (value,)
            return cur

        fake_db = mock.Mock()
        fake_db.execute_sql.side_effect = [
            _cursor(blob_type),
            _cursor(1 if fallback_column_present else 0),
            None,   # ALTER last_report (only when the probe said blob)
            None,   # ALTER users ADD COLUMN (only when the column is missing)
        ]
        with mock.patch('athina.users.DB', fake_db):
            database._upgrade_schema()
        return [c[0][0] for c in fake_db.execute_sql.call_args_list]

    def test_schema_upgrade_widens_an_existing_blob_column(self):
        """Deployments created before the change still hold a 64 KB BLOB."""
        sql = self._upgrade_calls('blob', fallback_column_present=True)
        self.assertTrue(any("MEDIUMBLOB" in s for s in sql))

    def test_schema_upgrade_skips_an_already_wide_column(self):
        sql = self._upgrade_calls('mediumblob', fallback_column_present=True)
        self.assertFalse(any("MEDIUMBLOB" in s for s in sql))

    def test_schema_upgrade_adds_the_per_user_fallback_column(self):
        """The fallback clock moved from assignmentdata to users."""
        sql = self._upgrade_calls('mediumblob', fallback_column_present=False)
        added = [s for s in sql if "ADD COLUMN webhook_fallback_at" in s]
        self.assertEqual(len(added), 1)

    def test_schema_upgrade_leaves_an_existing_fallback_column_alone(self):
        sql = self._upgrade_calls('mediumblob', fallback_column_present=True)
        self.assertFalse(any("ADD COLUMN webhook_fallback_at" in s for s in sql))

    def test_schema_upgrade_survives_a_missing_table(self):
        database = Database.__new__(Database)
        fake_db = mock.Mock()
        fake_db.execute_sql.side_effect = Exception("permission denied")
        with mock.patch('athina.users.DB', fake_db):
            database._upgrade_schema()  # must not raise

    def test_schema_upgrade_is_skipped_on_sqlite(self):
        import athina.users as users_module
        database = Database.__new__(Database)
        with mock.patch.object(users_module, 'DB', users_module.DB):
            self.assertIsInstance(users_module.DB, users_module.peewee.SqliteDatabase)
            database._upgrade_schema()
        # No SQL was attempted against the sqlite backend.
        with mock.patch.object(users_module.DB, 'execute_sql') as execute:
            database._upgrade_schema()
        execute.assert_not_called()

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

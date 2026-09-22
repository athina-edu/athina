"""Tests for athina.users.import_submissions (db input mode).

`import_submissions` feeds the grading engine when no Canvas API is used
(input_method: db), so every branch here is on the critical path for
db-backed courses.
"""
from datetime import datetime
from unittest import TestCase

from athina.users import (AssignmentData, Database, Users, import_submissions,
                          return_a_student)


class TestImportSubmissions(TestCase):
    def setUp(self):
        Database().connect_to_db()
        Users.delete().where(Users.course_id == 5, Users.assignment_id == 5).execute()
        # AssignmentData persists across tests, so clear it to keep the
        # due-date assertions independent.
        AssignmentData.delete().where(AssignmentData.course_id == 5,
                                      AssignmentData.assignment_id == 5).execute()

    def tearDown(self):
        Users.delete().where(Users.course_id == 5, Users.assignment_id == 5).execute()
        AssignmentData.delete().where(AssignmentData.course_id == 5,
                                      AssignmentData.assignment_id == 5).execute()

    def _get(self, user_id):
        return return_a_student(5, 5, user_id)

    # ---- creation ------------------------------------------------------

    def test_creates_new_students(self):
        created, updated = import_submissions(5, 5, [
            {"user_id": 1, "user_fullname": "Alice", "secondary_id": "a@x.edu",
             "repository_url": "https://git/x.git"},
            {"user_id": 2, "user_fullname": "Bob", "secondary_id": "b@x.edu",
             "repository_url": "https://git/y.git"},
        ])
        self.assertEqual((created, updated), (2, 0))
        self.assertEqual(self._get(1).user_fullname, "Alice")
        self.assertEqual(self._get(2).secondary_id, "b@x.edu")

    def test_new_student_marks_url_as_new(self):
        import_submissions(5, 5, [{"user_id": 3, "repository_url": "https://git/z.git"}])
        obj = self._get(3)
        self.assertTrue(obj.new_url)
        self.assertEqual(obj.repository_url, "https://git/z.git")

    def test_new_student_commit_date_is_minimum(self):
        import_submissions(5, 5, [{"user_id": 4, "repository_url": "https://git/z.git"}])
        self.assertEqual(self._get(4).commit_date, datetime(1, 1, 1, 0, 0))

    def test_missing_optional_keys_default_to_empty(self):
        import_submissions(5, 5, [{"user_id": 5}])
        obj = self._get(5)
        self.assertEqual(obj.user_fullname, "")
        self.assertEqual(obj.secondary_id, "")
        self.assertEqual(obj.repository_url, "")

    # ---- skipping ------------------------------------------------------

    def test_entry_without_user_id_is_skipped(self):
        created, updated = import_submissions(5, 5, [
            {"user_fullname": "No Id"},
            {"user_id": 6, "user_fullname": "Has Id"},
        ])
        self.assertEqual((created, updated), (1, 0))

    def test_empty_submission_list(self):
        self.assertEqual(import_submissions(5, 5, []), (0, 0))

    # ---- updating ------------------------------------------------------

    def test_updates_existing_repository_url(self):
        import_submissions(5, 5, [{"user_id": 7, "repository_url": "https://git/old.git"}])
        created, updated = import_submissions(5, 5, [
            {"user_id": 7, "repository_url": "https://git/new.git"}])
        self.assertEqual((created, updated), (0, 1))
        self.assertEqual(self._get(7).repository_url, "https://git/new.git")

    def test_repository_url_change_flags_new_url(self):
        import_submissions(5, 5, [{"user_id": 8, "repository_url": "https://git/old.git"}])
        obj = self._get(8)
        obj.new_url = False
        obj.save()

        import_submissions(5, 5, [{"user_id": 8, "repository_url": "https://git/new.git"}])
        self.assertTrue(self._get(8).new_url)

    def test_updates_fullname_when_changed(self):
        import_submissions(5, 5, [{"user_id": 9, "user_fullname": "Old Name"}])
        created, updated = import_submissions(5, 5, [{"user_id": 9, "user_fullname": "New Name"}])
        self.assertEqual((created, updated), (0, 1))
        self.assertEqual(self._get(9).user_fullname, "New Name")

    def test_updates_secondary_id_when_changed(self):
        import_submissions(5, 5, [{"user_id": 10, "secondary_id": "old@x.edu"}])
        created, updated = import_submissions(5, 5, [{"user_id": 10, "secondary_id": "new@x.edu"}])
        self.assertEqual((created, updated), (0, 1))
        self.assertEqual(self._get(10).secondary_id, "new@x.edu")

    def test_identical_entry_is_not_counted_as_update(self):
        payload = [{"user_id": 11, "user_fullname": "Same", "secondary_id": "s@x.edu",
                    "repository_url": "https://git/same.git"}]
        import_submissions(5, 5, payload)
        created, updated = import_submissions(5, 5, payload)
        self.assertEqual((created, updated), (0, 0))

    def test_blank_values_do_not_overwrite_existing(self):
        import_submissions(5, 5, [{"user_id": 12, "user_fullname": "Keep Me",
                                   "secondary_id": "keep@x.edu"}])
        import_submissions(5, 5, [{"user_id": 12}])
        obj = self._get(12)
        self.assertEqual(obj.user_fullname, "Keep Me")
        self.assertEqual(obj.secondary_id, "keep@x.edu")

    def test_blank_repository_url_does_not_clear_existing(self):
        import_submissions(5, 5, [{"user_id": 13, "repository_url": "https://git/keep.git"}])
        import_submissions(5, 5, [{"user_id": 13, "repository_url": ""}])
        self.assertEqual(self._get(13).repository_url, "https://git/keep.git")

    # ---- due date ------------------------------------------------------

    def test_due_date_is_stored_when_provided(self):
        from athina.users import load_key_from_assignment_data
        import_submissions(5, 5, [], due_date=datetime(2030, 5, 1, 12, 0))
        self.assertIsNotNone(load_key_from_assignment_data(5, 5, "due_date"))

    def test_due_date_omitted_is_not_stored(self):
        from athina.users import load_key_from_assignment_data
        import_submissions(5, 5, [{"user_id": 14}])
        self.assertIsNone(load_key_from_assignment_data(5, 5, "due_date"))

    # ---- mixed batch ---------------------------------------------------

    def test_mixed_create_and_update_batch(self):
        import_submissions(5, 5, [{"user_id": 20, "user_fullname": "Existing"}])
        created, updated = import_submissions(5, 5, [
            {"user_id": 20, "user_fullname": "Renamed"},
            {"user_id": 21, "user_fullname": "Brand New"},
        ])
        self.assertEqual((created, updated), (1, 1))

    def test_scoped_to_course_and_assignment(self):
        """A user_id in another assignment must not be touched."""
        created, _ = import_submissions(5, 5, [{"user_id": 30}])
        self.assertEqual(created, 1)
        Users.create(user_id=30, course_id=6, assignment_id=6)
        import_submissions(5, 5, [{"user_id": 30, "user_fullname": "Scoped"}])
        self.assertEqual(return_a_student(5, 5, 30).user_fullname, "Scoped")
        self.assertEqual(return_a_student(6, 6, 30).user_fullname, "")
        Users.delete().where(Users.course_id == 6, Users.assignment_id == 6).execute()

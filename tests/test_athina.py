# athina_cli_tests.py

import unittest
from athina.logger import *
from athina.git import *
from athina.configuration import *
from athina.tester.tester import *
from athina.canvas import *
import os
import shutil
import multiprocessing


class TestFunctions(unittest.TestCase):
    def test_create_user_object(self):
        filename = "tests/user_data.sqlite3"
        if os.path.isfile(filename):
            os.remove(filename)
        user_data = self.create_fake_user_db()  # This otherwise creates a new object

        # Load identical second object, the file should be already stored.
        user_data2 = Database(db_filename=filename)
        self.assertEqual(type(user_data), type(user_data2))

    @staticmethod
    def create_fake_user_db():
        """
        This method includes several static user scenarios and should not be changed since multiple tests may depend
        on it. If you want to generate a new scenario just add a new user into the database that this method returns.

        :return: user_data object from users.py
        """
        filename = "tests/user_data.sqlite3"
        if os.path.isfile(filename):
            os.remove(filename)
        user_data = Database(db_filename=filename)

        # Normal student
        Users.create(user_id=1,
                     repository_url="https://github.com/athina-edu/testing.git",
                     url_date=datetime(1, 1, 1, 0, 0),
                     new_url=True,
                     commit_date=datetime(1, 1, 1, 0, 0))

        # Student with wrong url
        Users.create(user_id=2,
                     repository_url="https://github.com/athina-edu/testin",
                     url_date=datetime(1, 1, 1, 0, 0),
                     new_url=True,
                     commit_date=datetime(1, 1, 1, 0, 0))

        # Students 3 and 4 with same url (note this is different from user 1 by a backslash)
        Users.create(user_id=3,
                     repository_url="https://github.com/athina-edu/testing.git/",
                     url_date=datetime(1, 1, 1, 0, 0),
                     new_url=True,
                     commit_date=datetime(1, 1, 1, 0, 0))
        Users.create(user_id=4,
                     repository_url="https://github.com/athina-edu/testing.git/",
                     url_date=datetime(1, 1, 1, 0, 0),
                     new_url=True,
                     commit_date=datetime(1, 1, 1, 0, 0))

        # No URL user
        Users.create(user_id=5)

        # Student submitting after the due date (default is set 2100 in configuration module)
        Users.create(user_id=6,
                     repository_url="https://github.com/git-persistence/git-persistence",
                     url_date=datetime(2101, 1, 1, 0, 0),
                     new_url=True,
                     commit_date=datetime(2101, 1, 1, 0, 0))

        # No repo user
        Users.create(user_id=7)

        return user_data

    @staticmethod
    def create_logger():
        logger = Logger()
        logger.set_verbose(True)
        logger.set_debug(True)
        return logger

    def test_git_tester(self):
        results = []
        logger = self.create_logger()

        configuration = Configuration(logger=logger)
        # Create fake directories
        shutil.rmtree("/tmp/athina_empty/tests", ignore_errors=True)
        os.makedirs("/tmp/athina_empty/tests", exist_ok=True)
        f = open("/tmp/athina_empty/tests/test", 'a')
        f.write("#!/bin/bash\necho 80\n")
        f.close()

        configuration.simulate = False
        e_learning = Canvas(configuration, logger)
        user_data = self.create_fake_user_db()
        repository = Repository(logger, configuration, e_learning)
        results.append(repository.check_repository_changes(1))
        results.append(repository.check_repository_changes(2))
        results.append(repository.check_repository_changes(3))
        results.append(repository.check_repository_changes(4))
        results.append(repository.check_repository_changes(5))
        results.append(repository.check_repository_changes(6))
        self.assertEqual(results, [True, False, True, True, False, False])

        tester = Tester(user_data, logger, configuration, e_learning, repository)

        # First time assignment evaluation
        user_object = tester.process_student_assignment(1)
        self.assertEqual(user_object[0].new_url, False)
        self.assertGreater(user_object[0].last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(user_object[0].last_grade, 80)
        last_graded = user_object[0].last_graded

        # Second time of athina iterating through but no new commit (grading should not change)
        user_object = tester.process_student_assignment(1)
        self.assertEqual(last_graded, user_object[0].last_graded)

        # New repo check and then attempting to test again, no change
        repository.check_repository_changes(1)
        user_object = tester.process_student_assignment(1)
        self.assertEqual(last_graded, user_object[0].last_graded)

        # Second student incorrect url, run double to verify that behavior won't change due to variable flip
        user_object = tester.process_student_assignment(2)
        self.assertEqual(user_object[0].plagiarism_to_grade, False)
        self.assertEqual(user_object[0].commit_date, datetime(1, 1, 1, 0, 0))
        user_object = tester.process_student_assignment(2)
        self.assertEqual(user_object[0].plagiarism_to_grade, False)
        self.assertEqual(user_object[0].commit_date, datetime(1, 1, 1, 0, 0))

        # Third student submitted the same url like the 4th. Test the case that this is not allowed.
        user_data.check_duplicate_url(same_url_limit=1)
        user_object = tester.process_student_assignment(3)
        self.assertEqual(user_object[0].plagiarism_to_grade, False)
        self.assertEqual(user_object[0].commit_date, datetime(1, 1, 1, 0, 0))

        # Third and fourth student but now groups of 2 are allowed
        user_data.check_duplicate_url(same_url_limit=2)
        repository.check_repository_changes(3)
        user_object = tester.process_student_assignment(3)
        self.assertGreater(user_object[0].last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(user_object[0].last_grade, 80)
        self.assertGreater(user_object[1].last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(user_object[1].last_grade, 80)

        # User with no url, no grading
        user_object = tester.process_student_assignment(5)
        self.assertEqual(user_object[0].last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(user_object[0].commit_date, datetime(1, 1, 1, 0, 0))

        # Previous user corrects and submits and actual good url
        obj = Users[5]
        obj.repository_url = "https://github.com/git-persistence/git-persistence/"
        obj.url_date = datetime.now(timezone.utc).replace(tzinfo=None)
        obj.new_url = True
        obj.save()
        repository.check_repository_changes(5)
        user_object = tester.process_student_assignment(5)
        self.assertGreater(user_object[0].last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(user_object[0].last_grade, 80)
        last_graded = user_object[0].last_graded

        # User 5 submit commit after the due date
        configuration.due_date = Users[5].commit_date - timedelta(hours=24)
        obj = Users[5]
        obj.changed_state = True
        obj.save()
        user_object = tester.process_student_assignment(5)
        self.assertEqual(user_object[0].last_graded, last_graded)

        # Briefly remove due date enforcement
        # Technically enforce_due_date only moves the due date to some extreme future date
        configuration.due_date = datetime.now(timezone.utc).replace(tzinfo=None)
        obj = Users[5]
        obj.changed_state = True
        obj.save()
        user_object = tester.process_student_assignment(5)
        self.assertGreater(user_object[0].last_graded, last_graded)

        # User 6 submits late
        configuration.due_date = datetime(2100, 1, 1, 0, 0)
        user_object = tester.process_student_assignment(6)
        self.assertEqual(user_object[0].last_graded, datetime(1, 1, 1, 0, 0))

        # NO REPO operations, first run of script
        configuration.no_repo = True
        configuration.grade_update_frequency = 24
        user_object = tester.process_student_assignment(7)
        self.assertGreater(user_object[0].last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(user_object[0].last_grade, 80)
        last_graded = user_object[0].last_graded

        # Time doesn't go by, checking to see if script can run again, it shouldn't
        user_object = tester.process_student_assignment(7)
        self.assertEqual(user_object[0].last_graded, last_graded)

        # Enough time goes buy
        obj = Users[7]
        obj.last_graded = obj.last_graded - timedelta(hours=24)
        obj.save()
        user_object = tester.process_student_assignment(7)
        self.assertGreater(user_object[0].last_graded, last_graded)

    @unittest.skip("Moss service hangs for too long. Implement timeouts in moss.py")
    def test_tester_plagiarism(self):
        logger = self.create_logger()
        configuration = Configuration(logger=logger)
        # Create fake directories
        shutil.rmtree("/tmp/athina_empty/tests", ignore_errors=True)
        os.makedirs("/tmp/athina_empty/tests", exist_ok=True)
        f = open("/tmp/athina_empty/tests/test", 'a')
        f.write("#!/bin/bash\necho 80\n")
        f.close()

        e_learning = Canvas(configuration, logger)
        user_data = self.create_fake_user_db()
        repository = Repository(logger, configuration, e_learning)
        repository.check_repository_changes(1)
        repository.check_repository_changes(2)
        repository.check_repository_changes(3)
        repository.check_repository_changes(4)
        repository.check_repository_changes(5)
        repository.check_repository_changes(6)

        tester = Tester(user_data, logger, configuration, e_learning, repository)

        # First time assignment evaluation
        tester.process_student_assignment(1)
        tester.process_student_assignment(2)
        tester.process_student_assignment(3)
        tester.process_student_assignment(4)
        tester.process_student_assignment(5)
        tester.process_student_assignment(6)

        user_data.db[1].last_plagiarism_check = user_data.db[1].last_plagiarism_check - timedelta(hours=48)
        user_data.db[2].last_plagiarism_check = user_data.db[2].last_plagiarism_check - timedelta(hours=48)
        user_data.db[3].last_plagiarism_check = user_data.db[3].last_plagiarism_check - timedelta(hours=48)
        user_data.db[4].last_plagiarism_check = user_data.db[4].last_plagiarism_check - timedelta(hours=48)
        user_data.db[5].last_plagiarism_check = user_data.db[5].last_plagiarism_check - timedelta(hours=48)
        user_data.db[6].last_plagiarism_check = user_data.db[6].last_plagiarism_check - timedelta(hours=48)

        results = tester.plagiarism_checks_on_users()
        self.assertEqual(len(results), 3)

    def test_tester_docker(self):
        results = []
        logger = self.create_logger()
        configuration = Configuration(logger=logger)

        configuration.use_docker = True
        # Create fake directories
        shutil.rmtree("/tmp/athina_empty/tests", ignore_errors=True)
        os.makedirs("/tmp/athina_empty/tests", exist_ok=True)
        f = open("/tmp/athina_empty/tests/test", 'w')
        f.write("#!/bin/bash\necho 80\n")
        f.close()
        f = open("/tmp/athina_empty/Dockerfile", 'w')
        f.write("FROM ubuntu:18.04\nENTRYPOINT cd $TEST_DIR && ls && $TEST $STUDENT_DIR $TEST_DIR")
        f.close()

        e_learning = Canvas(configuration, logger)
        user_data = self.create_fake_user_db()
        repository = Repository(logger, configuration, e_learning)
        results.append(repository.check_repository_changes(1))
        results.append(repository.check_repository_changes(2))
        results.append(repository.check_repository_changes(3))
        results.append(repository.check_repository_changes(4))
        results.append(repository.check_repository_changes(5))
        results.append(repository.check_repository_changes(6))
        self.assertEqual(results, [True, False, True, True, False, False])

        tester = Tester(user_data, logger, configuration, e_learning, repository)

        # First time assignment evaluation
        user_object = tester.process_student_assignment(1)
        self.assertEqual(user_object[0].new_url, False)
        self.assertGreater(user_object[0].last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(user_object[0].last_grade, 80)

        # Parallel process
        configuration.processes = 5
        user_object_results = tester.parallel_map([1, 2, 3, 4, 5])
        self.assertEqual(user_object_results[0][0].new_url, False)
        self.assertGreater(user_object_results[0][0].last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(user_object_results[0][0].last_grade, 80)
        self.assertEqual(user_object_results[1][0].plagiarism_to_grade, False)
        self.assertEqual(user_object_results[1][0].commit_date, datetime(1, 1, 1, 0, 0))
        # Group assignment
        self.assertGreater(user_object_results[2][0].last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(user_object_results[2][0].last_grade, 80)
        self.assertGreater(user_object_results[2][1].last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(user_object_results[2][1].last_grade, 80)

    def test_tester_db_testing(self):
        logger = self.create_logger()
        configuration = Configuration(logger=logger)

        configuration.use_docker = True
        # Create fake directories
        shutil.rmtree("/tmp/athina_empty/tests", ignore_errors=True)
        os.makedirs("/tmp/athina_empty/tests", exist_ok=True)
        f = open("/tmp/athina_empty/tests/test", 'w')
        f.write("#!/bin/bash\necho 80\n")
        f.close()
        f = open("/tmp/athina_empty/Dockerfile", 'w')
        f.write("FROM ubuntu:18.04\nENTRYPOINT cd $TEST_DIR && ls && $TEST $STUDENT_DIR $TEST_DIR")
        f.close()

        e_learning = Canvas(configuration, logger)
        user_data = self.create_fake_user_db()
        repository = Repository(logger, configuration, e_learning)
        tester = Tester(user_data, logger, configuration, e_learning, repository)
        configuration.processes = 2
        user_object_results = tester.start_testing_db()
        self.assertEqual(user_object_results[0][0].new_url, False)
        self.assertGreater(user_object_results[0][0].last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(user_object_results[0][0].last_grade, 80)
        self.assertEqual(user_object_results[1][0].plagiarism_to_grade, False)
        self.assertEqual(user_object_results[1][0].commit_date, datetime(1, 1, 1, 0, 0))
        # Group assignment
        self.assertGreater(user_object_results[2][0].last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(user_object_results[2][0].last_grade, 80)
        self.assertGreater(user_object_results[2][1].last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(user_object_results[2][1].last_grade, 80)


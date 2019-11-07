# athina_cli_tests.py

import unittest
from athina.logger import *
from athina.git import *
from athina.configuration import *
from athina.tester.tester import *
from athina.canvas import *
from athina.moss import *
import os
import shutil
import multiprocessing


def create_logger():
    logger = Logger()
    logger.set_verbose(True)
    logger.set_debug(True)
    return logger


def create_test_config(msg="echo 80"):
    # Create fake directories
    shutil.rmtree("/tmp/athina_empty/tests", ignore_errors=True)
    shutil.rmtree("/tmp/athina_empty/.git", ignore_errors=True)
    os.makedirs("/tmp/athina_empty/tests", exist_ok=True)
    f = open("/tmp/athina_empty/tests/test", 'w')
    f.write("#!/bin/bash\n%s\n" % msg)
    f.close()
    f = open("/tmp/athina_empty/Dockerfile", 'w')
    f.write("FROM ubuntu:18.04\nENTRYPOINT cd $TEST_DIR && ls && $TEST $STUDENT_DIR $TEST_DIR")
    f.close()
    shutil.copytree("tests/git", "/tmp/athina_empty/.git")


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


class TestFunctions(unittest.TestCase):
    def test_create_user_object(self):
        filename = "tests/user_data.sqlite3"
        if os.path.isfile(filename):
            os.remove(filename)
        user_data = create_fake_user_db()  # This otherwise creates a new object

        # Load identical second object, the file should be already stored.
        user_data2 = Database(db_filename=filename)
        self.assertEqual(type(user_data), type(user_data2))

    def test_git_tester(self):
        results = []
        logger = create_logger()

        configuration = Configuration(logger=logger)
        # Create fake directories
        shutil.rmtree("/tmp/athina_empty/tests", ignore_errors=True)
        os.makedirs("/tmp/athina_empty/tests", exist_ok=True)
        f = open("/tmp/athina_empty/tests/test", 'a')
        f.write("#!/bin/bash\necho 80\n")
        f.close()

        configuration.simulate = False
        e_learning = Canvas(configuration, logger)
        user_data = create_fake_user_db()
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
        print(user_object[0].last_grade)
        self.assertEqual(user_object[0].new_url, False)
        self.assertGreater(user_object[0].last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(user_object[0].last_grade, 80, "First time assignment evaluation")
        last_graded = user_object[0].last_graded

        # Second time of athina iterating through but no new commit (grading should not change)
        user_object = tester.process_student_assignment(1)
        self.assertEqual(last_graded, user_object[0].last_graded)

        # Testing whether the force testing will enable testing the user again
        user_object = Users.get(Users.user_id == 1)
        user_object.force_test = True
        user_object.last_grade = 0
        user_object = tester.process_student_assignment(1)
        self.assertEqual(user_object[0].last_grade, 80, "Testing force_test option (to be enable via athina web)")

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

    @unittest.skip("Does not work and needs to fix. Empty result")
    def test_tester_plagiarism(self):
        logger = create_logger()
        configuration = Configuration(logger=logger)
        # Create fake directories
        shutil.rmtree("/tmp/athina_empty/tests", ignore_errors=True)
        os.makedirs("/tmp/athina_empty/tests", exist_ok=True)
        f = open("/tmp/athina_empty/tests/test", 'a')
        f.write("#!/bin/bash\necho 80\n")
        f.close()

        e_learning = Canvas(configuration, logger)
        user_data = create_fake_user_db()
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

        for i in range(1, 7):
            obj = Users.get(Users.user_id == i)
            obj.last_plagiarism_check = obj.last_plagiarism_check - timedelta(hours=48)
            obj.save()

        results = plagiarism_checks_on_users(logger, configuration, e_learning)
        print(results)
        self.assertEqual(len(results), 3)

    def test_tester_docker(self):
        results = []
        logger = create_logger()
        configuration = Configuration(logger=logger)

        configuration.use_docker = True
        configuration.simulate = False
        create_test_config()

        e_learning = Canvas(configuration, logger)
        user_data = create_fake_user_db()
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
        tester.spawn_worker([1, 2, 3, 4, 5])
        time.sleep(40)
        obj = Users.get(Users.user_id == 1)
        self.assertEqual(obj.new_url, False)
        self.assertGreater(obj.last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(obj.last_grade, 80)
        obj = Users.get(Users.user_id == 2)
        self.assertEqual(obj.plagiarism_to_grade, False)
        self.assertEqual(obj.commit_date, datetime(1, 1, 1, 0, 0))
        # Group assignment
        obj = Users.get(Users.user_id == 3)
        self.assertGreater(obj.last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(obj.last_grade, 80)
        obj = Users.get(Users.user_id == 4)
        self.assertGreater(obj.last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(obj.last_grade, 80)

    def test_tester_docker_errors(self):
        results = []
        logger = create_logger()
        configuration = Configuration(logger=logger)

        configuration.use_docker = True
        configuration.simulate = False
        # Create fake directories
        create_test_config("doesntexist")

        e_learning = Canvas(configuration, logger)
        user_data = create_fake_user_db()
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
        assert "command not found" in user_object[0].last_report

    def test_tester_db_testing(self):
        logger = create_logger()
        configuration = Configuration(logger=logger)

        configuration.use_docker = True
        configuration.simulate = False
        # Create fake directories
        create_test_config()

        e_learning = Canvas(configuration, logger)
        user_data = create_fake_user_db()
        repository = Repository(logger, configuration, e_learning)
        tester = Tester(user_data, logger, configuration, e_learning, repository)
        configuration.processes = 2
        tester.start_testing_db()
        time.sleep(40)
        obj = Users.get(Users.user_id == 1)
        self.assertEqual(obj.new_url, False)
        self.assertGreater(obj.last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(obj.last_grade, 80)
        obj = Users.get(Users.user_id == 2)
        self.assertEqual(obj.plagiarism_to_grade, False)
        self.assertEqual(obj.commit_date, datetime(1, 1, 1, 0, 0))
        # Group assignment
        obj = Users.get(Users.user_id == 3)
        self.assertGreater(obj.last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(obj.last_grade, 80)
        obj = Users.get(Users.user_id == 4)
        self.assertGreater(obj.last_graded, datetime(1, 1, 1, 0, 0))
        self.assertEqual(obj.last_grade, 80)

    def test_tester_timeout(self):
        logger = create_logger()
        configuration = Configuration(logger=logger)

        configuration.use_docker = True
        configuration.simulate = False
        configuration.test_timeout = 10
        # Create fake directories
        create_test_config("echo 'test'\nsleep 20\necho 80")

        e_learning = Canvas(configuration, logger)
        user_data = create_fake_user_db()
        repository = Repository(logger, configuration, e_learning)
        tester = Tester(user_data, logger, configuration, e_learning, repository)
        configuration.processes = 2
        tester.start_testing_db()
        time.sleep(40)  # delay that lets fork process end. Alt we can use os.wait but time is simpler
        obj = Users.get(Users.user_id == 1)
        self.assertNotEqual(obj.last_grade, 80)
        # Group assignment
        obj = Users.get(Users.user_id == 3)
        self.assertNotEqual(obj.last_grade, 80)
        obj = Users.get(Users.user_id == 4)
        self.assertNotEqual(obj.last_grade, 80)

    def test_multiple_iters(self):
        logger = create_logger()
        configuration = Configuration(logger=logger)

        configuration.use_docker = True
        configuration.simulate = False
        configuration.test_timeout = 10
        # Create fake directories
        create_test_config("echo 'test'\nsleep 20\necho 80")

        e_learning = Canvas(configuration, logger)
        user_data = create_fake_user_db()
        repository = Repository(logger, configuration, e_learning)
        tester = Tester(user_data, logger, configuration, e_learning, repository)
        configuration.processes = 2
        repository.check_repository_changes(1)
        repository.check_repository_changes(2)
        repository.check_repository_changes(3)
        repository.check_repository_changes(4)

        # Pretend that the tester is already running for these users
        user_object = Users.get(Users.user_id == 1)
        user_object.tester_active = True
        user_object.tester_date = datetime.now(timezone.utc).replace(tzinfo=None)
        user_object.save()
        user_object = Users.get(Users.user_id == 3)
        user_object.tester_active = True
        user_object.tester_date = datetime.now(timezone.utc).replace(tzinfo=None)
        user_object.save()
        user_object = Users.get(Users.user_id == 4)
        user_object.tester_active = True
        user_object.tester_date = datetime.now(timezone.utc).replace(tzinfo=None)
        user_object.save()

        tester.spawn_worker([1, 3, 4])
        time.sleep(5)

        cprocess = psutil.Process()
        children = cprocess.children(recursive=True)
        self.assertLessEqual(len(children), 4,
                             msg="Processes needs to be less than user records (no duplicate processes)")

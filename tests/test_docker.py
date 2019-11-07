import unittest
from tests.test_athina import create_test_config, create_logger, create_fake_user_db
from athina.tester.docker import docker_build
from athina.configuration import Configuration


class TestFunctions(unittest.TestCase):
    def test_docker_build(self):
        logger = create_logger()
        configuration = Configuration(logger=logger)

        configuration.use_docker = True
        configuration.simulate = False
        # Create fake directories
        create_test_config()
        user_data = create_fake_user_db()

        result = docker_build(configuration, logger)
        self.assertEqual(result, True, "The first time we visit a testing repo have to build the Dockerfile")
        result = docker_build(configuration, logger)
        self.assertEqual(result, False, "We do not rebuild if we have already built for a specific commit")

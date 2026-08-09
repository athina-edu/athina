# Tests for athina.configuration (Configuration hyper-object + YAML loading).
import os
from unittest import mock, TestCase

import yaml

from athina.configuration import Configuration
from tests.helpers import make_config
from tests.test_athina import create_test_config, TEST_TMP_DIR


class TestConfiguration(TestCase):
    def test_find_yaml_directory(self):
        create_test_config()
        yaml_path = os.path.join(TEST_TMP_DIR, "test_assignment.yaml")
        with open(yaml_path, "w") as f:
            f.write("auth_token: abc\n")
        result = Configuration.find_yaml(TEST_TMP_DIR + "/")
        self.assertTrue(result.endswith(".yaml"))

    def test_find_yaml_directory_no_yaml(self):
        d = "/tmp/cov_no_yaml"
        os.makedirs(d, exist_ok=True)
        result = Configuration.find_yaml(d)
        self.assertEqual(result, d)

    def test_find_yaml_file(self):
        result = Configuration.find_yaml("/tmp/somefile.yaml")
        self.assertEqual(result, "/tmp/somefile.yaml")

    def test_default_dir(self):
        Configuration.default_dir()
        self.assertTrue(os.path.isdir(Configuration.config_dir))

    def test_in_docker(self):
        with mock.patch('builtins.open', mock.mock_open(read_data="0::/docker/abc\n")):
            self.assertTrue(Configuration.in_docker())
        with mock.patch('builtins.open', mock.mock_open(read_data="0::/\n")):
            self.assertFalse(Configuration.in_docker())

    def test_check_dependencies_present(self):
        self.assertTrue(Configuration.check_dependencies(["git"]))

    def test_check_dependencies_missing(self):
        with self.assertRaises(FileNotFoundError):
            Configuration.check_dependencies(["definitely_not_a_real_binary_xyz"])

    def test_load_value_present(self):
        configuration, _ = make_config()
        configuration.load_value({"course_id": 42}, "course_id", configuration.course_id)
        self.assertEqual(configuration.course_id, 42)

    def test_load_value_absent(self):
        configuration, _ = make_config()
        configuration.load_value({}, "course_id", configuration.course_id)
        self.assertEqual(configuration.course_id, 1)

    def test_load_configuration_valid(self):
        configuration, _ = make_config()
        yaml_path = os.path.join(TEST_TMP_DIR, "test_assignment.yaml")
        with open(yaml_path, "w") as f:
            f.write("auth_token: mytoken\ncourse_id: 5\nassignment_id: 9\n")
        with mock.patch.object(configuration.logger, 'set_assignment_log_file'), \
                mock.patch.object(Configuration, 'check_dependencies', return_value=True), \
                mock.patch.object(Configuration, 'in_docker', return_value=False):
            configuration.load_configuration(yaml_path)
        self.assertEqual(configuration.auth_token, "mytoken")
        self.assertEqual(configuration.course_id, 5)
        self.assertEqual(configuration.assignment_id, 9)

    def test_load_configuration_invalid_yaml(self):
        configuration, _ = make_config()
        yaml_path = os.path.join(TEST_TMP_DIR, "bad.yaml")
        with open(yaml_path, "w") as f:
            f.write(": : : not valid yaml : :\n\t")
        with self.assertRaises(yaml.YAMLError):
            configuration.load_configuration(yaml_path)

    def test_load_configuration_no_repo_sets_pass_extra_params(self):
        configuration, _ = make_config()
        yaml_path = os.path.join(TEST_TMP_DIR, "test_assignment.yaml")
        with open(yaml_path, "w") as f:
            f.write("no_repo: true\n")
        with mock.patch.object(configuration.logger, 'set_assignment_log_file'), \
                mock.patch.object(Configuration, 'check_dependencies', return_value=True), \
                mock.patch.object(Configuration, 'in_docker', return_value=False):
            configuration.load_configuration(yaml_path)
        self.assertTrue(configuration.pass_extra_params)

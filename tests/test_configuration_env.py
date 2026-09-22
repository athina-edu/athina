"""Tests for configuration .env loading and non-Canvas mode validation.

These cover the assignment-specific .env bridge (written by athina-web) and
the input_method / output_method validation that guards db and GitLab modes.
"""
import os
import tempfile
from unittest import TestCase, mock

from athina.configuration import Configuration
from tests.helpers import make_config


class TestLoadAssignmentEnv(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.configuration, _ = make_config()
        self.configuration.config_dir = self.tmpdir
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def _write_env(self, content):
        with open(os.path.join(self.tmpdir, '.env'), 'w') as handle:
            handle.write(content)

    def test_missing_env_file_is_a_noop(self):
        os.environ.pop('ATHINA_TEST_LLM_KEY', None)
        self.configuration._load_assignment_env()
        self.assertNotIn('ATHINA_TEST_LLM_KEY', os.environ)

    def test_loads_simple_key_value(self):
        self._write_env("ATHINA_TEST_LLM_KEY=abc123\n")
        os.environ.pop('ATHINA_TEST_LLM_KEY', None)
        self.configuration._load_assignment_env()
        self.assertEqual(os.environ['ATHINA_TEST_LLM_KEY'], 'abc123')

    def test_strips_quotes_and_whitespace(self):
        self._write_env('ATHINA_TEST_Q="quoted value"\nATHINA_TEST_S=\'single\'\n')
        os.environ.pop('ATHINA_TEST_Q', None)
        os.environ.pop('ATHINA_TEST_S', None)
        self.configuration._load_assignment_env()
        self.assertEqual(os.environ['ATHINA_TEST_Q'], 'quoted value')
        self.assertEqual(os.environ['ATHINA_TEST_S'], 'single')

    def test_skips_blank_lines_comments_and_malformed(self):
        self._write_env("\n# a comment\nNO_EQUALS_SIGN\nATHINA_TEST_OK=yes\n")
        os.environ.pop('ATHINA_TEST_OK', None)
        self.configuration._load_assignment_env()
        self.assertEqual(os.environ['ATHINA_TEST_OK'], 'yes')
        self.assertNotIn('NO_EQUALS_SIGN', os.environ)

    def test_existing_environment_takes_precedence(self):
        """Explicit operator env vars must not be clobbered by the .env file."""
        self._write_env("ATHINA_TEST_PRECEDENCE=from_file\n")
        os.environ['ATHINA_TEST_PRECEDENCE'] = 'from_operator'
        self.configuration._load_assignment_env()
        self.assertEqual(os.environ['ATHINA_TEST_PRECEDENCE'], 'from_operator')

    def test_values_may_contain_equals_signs(self):
        self._write_env("ATHINA_TEST_URL=https://x/y?a=b\n")
        os.environ.pop('ATHINA_TEST_URL', None)
        self.configuration._load_assignment_env()
        self.assertEqual(os.environ['ATHINA_TEST_URL'], 'https://x/y?a=b')

    def test_unreadable_env_file_does_not_raise(self):
        path = os.path.join(self.tmpdir, '.env')
        self._write_env("ATHINA_TEST_UNREADABLE=x\n")
        os.chmod(path, 0o000)
        try:
            self.configuration._load_assignment_env()  # must not raise
        finally:
            os.chmod(path, 0o600)


class TestModeValidation(TestCase):
    """Invalid input_method / output_method fall back to canvas.

    The validation lives inline in load_configuration, so these drive a real
    YAML load with the OS-binary dependency check stubbed out.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def _load_with(self, yaml_body):
        path = os.path.join(self.tmpdir, "assignment.yaml")
        with open(path, 'w') as handle:
            handle.write(yaml_body)

        configuration, _ = make_config()
        with mock.patch.object(Configuration, 'check_dependencies', return_value=True):
            configuration.load_configuration(path)
        return configuration

    def test_unknown_output_method_defaults_to_canvas(self):
        configuration = self._load_with("output_method: bogus\n")
        self.assertEqual(configuration.output_method, "canvas")

    def test_unknown_input_method_defaults_to_canvas(self):
        configuration = self._load_with("input_method: nonsense\n")
        self.assertEqual(configuration.input_method, "canvas")

    def test_db_input_method_is_accepted(self):
        configuration = self._load_with("input_method: db\n")
        self.assertEqual(configuration.input_method, "db")

    def test_gitlab_output_is_accepted(self):
        configuration = self._load_with("output_method: gitlab_issues\ngitlab_project_id: 42\n")
        self.assertEqual(configuration.output_method, "gitlab_issues")
        self.assertEqual(configuration.gitlab_project_id, 42)

    def test_gitlab_output_without_project_id_still_loads(self):
        """A missing project id only warns; it must not abort configuration."""
        configuration = self._load_with("output_method: gitlab_issues\n")
        self.assertEqual(configuration.output_method, "gitlab_issues")
        self.assertEqual(configuration.gitlab_project_id, 0)

    def test_output_method_env_var_overrides_yaml(self):
        os.environ['OUTPUT_METHOD'] = 'gitlab_issues'
        os.environ['GITLAB_PROJECT_ID'] = '7'
        configuration = self._load_with("output_method: canvas\n")
        self.assertEqual(configuration.output_method, "gitlab_issues")
        self.assertEqual(configuration.gitlab_project_id, 7)

    def test_llm_key_from_env_enables_llm(self):
        os.environ['LLM_API_KEY'] = 'sk-from-env'
        configuration = self._load_with("llm_enabled: false\n")
        self.assertTrue(configuration.llm_enabled)
        self.assertEqual(configuration.llm_api_key, 'sk-from-env')

    def test_no_repo_implies_pass_extra_params(self):
        configuration = self._load_with("no_repo: true\n")
        self.assertTrue(configuration.pass_extra_params)


class TestCheckDependencies(TestCase):
    """check_dependencies verifies OS binaries and raises when one is absent."""

    def test_present_binary_returns_true(self):
        # 'sh' is present on any POSIX host running these tests.
        self.assertTrue(Configuration.check_dependencies(["sh"]))

    def test_empty_package_list_is_fine(self):
        self.assertTrue(Configuration.check_dependencies([]))

    def test_missing_binary_raises(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            Configuration.check_dependencies(["definitely-not-a-real-binary-xyz"])
        self.assertIn("definitely-not-a-real-binary-xyz", str(ctx.exception))

    def test_reports_the_first_missing_binary(self):
        with self.assertRaises(FileNotFoundError):
            Configuration.check_dependencies(["sh", "definitely-not-a-real-binary-xyz"])

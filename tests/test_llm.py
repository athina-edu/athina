"""Tests for athina.llm — LLM feedback generation and student code reading."""
import os
import tempfile
from unittest import TestCase, mock

from athina.llm import (generate_llm_feedback, parse_test_descriptions,
                        read_student_code)
from tests.helpers import make_config
from tests.test_athina import create_logger


def _llm_config(**overrides):
    configuration, logger = make_config()
    configuration.llm_enabled = True
    configuration.llm_endpoint_url = "https://api.example.com/v1"
    configuration.llm_api_key = "sk-test"
    configuration.llm_model = "gpt-4o-mini"
    for key, value in overrides.items():
        setattr(configuration, key, value)
    return configuration, logger


def _openai_response(content="Do this.", reasoning=None):
    """Build a fake OpenAI-compatible response object."""
    message = {"content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": message}]}
    return resp


class TestGenerateLlmFeedback(TestCase):
    def test_disabled_returns_none_without_calling_api(self):
        configuration, logger = make_config()
        configuration.llm_enabled = False
        with mock.patch('requests.post') as mock_post:
            result = generate_llm_feedback(configuration, {}, "out", [], logger)
        self.assertIsNone(result)
        mock_post.assert_not_called()

    def test_missing_api_key_returns_none(self):
        configuration, logger = _llm_config(llm_api_key="")
        with mock.patch('requests.post') as mock_post:
            result = generate_llm_feedback(configuration, {}, "out", [], logger)
        self.assertIsNone(result)
        mock_post.assert_not_called()

    def test_missing_endpoint_returns_none(self):
        configuration, logger = _llm_config(llm_endpoint_url="")
        with mock.patch('requests.post') as mock_post:
            result = generate_llm_feedback(configuration, {}, "out", [], logger)
        self.assertIsNone(result)
        mock_post.assert_not_called()

    def test_successful_generation_returns_content(self):
        configuration, logger = _llm_config()
        with mock.patch('requests.post', return_value=_openai_response("Fix your loop.")):
            result = generate_llm_feedback(configuration, {"a.py": "x"}, "out", [], logger)
        self.assertEqual(result, "Fix your loop.")

    def test_posts_to_chat_completions_with_bearer_token(self):
        configuration, logger = _llm_config()
        with mock.patch('requests.post', return_value=_openai_response("ok")) as mock_post:
            generate_llm_feedback(configuration, {}, "out", [], logger)
        url = mock_post.call_args[0][0]
        kwargs = mock_post.call_args[1]
        self.assertEqual(url, "https://api.example.com/v1/chat/completions")
        self.assertEqual(kwargs['headers']['Authorization'], "Bearer sk-test")
        self.assertEqual(kwargs['json']['model'], "gpt-4o-mini")

    def test_trailing_slash_in_endpoint_is_normalised(self):
        configuration, logger = _llm_config(llm_endpoint_url="https://api.example.com/v1/")
        with mock.patch('requests.post', return_value=_openai_response("ok")) as mock_post:
            generate_llm_feedback(configuration, {}, "out", [], logger)
        self.assertEqual(mock_post.call_args[0][0],
                         "https://api.example.com/v1/chat/completions")

    def test_system_and_user_messages_are_sent(self):
        configuration, logger = _llm_config()
        with mock.patch('requests.post', return_value=_openai_response("ok")) as mock_post:
            generate_llm_feedback(configuration, {"a.py": "print(1)"}, "FAILED", ["t1"], logger)
        messages = mock_post.call_args[1]['json']['messages']
        self.assertEqual(messages[0]['role'], 'system')
        self.assertEqual(messages[1]['role'], 'user')
        self.assertIn("print(1)", messages[1]['content'])
        self.assertIn("FAILED", messages[1]['content'])

    def test_non_200_returns_none(self):
        configuration, logger = _llm_config()
        resp = mock.Mock(status_code=500, text="server error")
        with mock.patch('requests.post', return_value=resp):
            result = generate_llm_feedback(configuration, {}, "out", [], logger)
        self.assertIsNone(result)

    def test_reasoning_content_used_when_content_empty(self):
        configuration, logger = _llm_config()
        resp = _openai_response(content="   ", reasoning="Reasoned answer.")
        with mock.patch('requests.post', return_value=resp):
            result = generate_llm_feedback(configuration, {}, "out", [], logger)
        self.assertEqual(result, "Reasoned answer.")

    def test_empty_content_and_no_reasoning_returns_none(self):
        configuration, logger = _llm_config()
        with mock.patch('requests.post', return_value=_openai_response(content="")):
            self.assertIsNone(generate_llm_feedback(configuration, {}, "out", [], logger))

    def test_request_exception_returns_none(self):
        configuration, logger = _llm_config()
        with mock.patch('requests.post', side_effect=Exception("network down")):
            self.assertIsNone(generate_llm_feedback(configuration, {}, "out", [], logger))

    def test_leaky_output_is_sanitized(self):
        configuration, logger = _llm_config()
        with mock.patch('requests.post',
                        return_value=_openai_response("def test_sum():\n    assert 1 == 1")):
            result = generate_llm_feedback(configuration, {}, "out", [], logger)
        self.assertIn("I noticed an issue", result)

    def test_works_without_logger(self):
        configuration, _ = _llm_config()
        with mock.patch('requests.post', return_value=_openai_response("ok")):
            self.assertEqual(generate_llm_feedback(configuration, {}, "out", []), "ok")


class TestReadStudentCode(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write(self, relpath, content):
        path = os.path.join(self.tmp, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as handle:
            handle.write(content)
        return path

    def test_missing_directory_returns_empty(self):
        self.assertEqual(read_student_code("/nonexistent/path/xyz"), {})

    def test_reads_code_files(self):
        self._write("main.py", "print(1)")
        self._write("notes.txt", "not code")
        result = read_student_code(self.tmp)
        self.assertIn("main.py", result)
        self.assertNotIn("notes.txt", result)
        self.assertEqual(result["main.py"], "print(1)")

    def test_skips_hidden_files(self):
        self._write(".hidden.py", "secret")
        self.assertEqual(read_student_code(self.tmp), {})

    def test_skips_ignored_directories(self):
        self._write("__pycache__/cached.py", "x")
        self._write(".git/config.py", "x")
        self._write("node_modules/pkg/index.js", "x")
        self.assertEqual(read_student_code(self.tmp), {})

    def test_supports_multiple_extensions(self):
        for name in ("a.py", "b.R", "c.java", "d.js", "e.sql"):
            self._write(name, "code")
        result = read_student_code(self.tmp)
        self.assertEqual(len(result), 5)

    def test_respects_max_files(self):
        for i in range(10):
            self._write("f%d.py" % i, "x")
        self.assertEqual(len(read_student_code(self.tmp, max_files=3)), 3)

    def test_nested_directories_use_relative_paths(self):
        self._write("pkg/sub/mod.py", "x")
        self.assertIn("pkg/sub/mod.py", read_student_code(self.tmp))

    def test_unreadable_file_is_skipped(self):
        path = self._write("bad.py", "x")
        os.chmod(path, 0o000)
        try:
            self.assertEqual(read_student_code(self.tmp), {})
        finally:
            os.chmod(path, 0o600)


class TestParseTestDescriptions(TestCase):
    def test_builds_descriptions_with_weights(self):
        configuration, _ = make_config()
        configuration.test_scripts = ["bash t1.sh", "bash t2.sh"]
        configuration.test_weights = [0.7, 0.3]
        result = parse_test_descriptions(configuration)
        self.assertEqual(len(result), 2)
        self.assertIn("weight 70%", result[0])
        self.assertIn("bash t1.sh", result[0])
        self.assertIn("weight 30%", result[1])

    def test_missing_weight_defaults_to_zero(self):
        configuration, _ = make_config()
        configuration.test_scripts = ["bash t1.sh"]
        configuration.test_weights = []
        self.assertIn("weight 0%", parse_test_descriptions(configuration)[0])

    def test_no_scripts_returns_empty(self):
        configuration, _ = make_config()
        configuration.test_scripts = []
        self.assertEqual(parse_test_descriptions(configuration), [])

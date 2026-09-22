"""Tests for athina.llm_agent_prompt — prompt building and leak sanitization."""
from unittest import TestCase

from athina.llm_agent_prompt import (build_student_message, get_prompt_hash,
                                     get_system_prompt, sanitize_output)


class TestSystemPrompt(TestCase):
    def test_get_system_prompt_is_stable(self):
        self.assertEqual(get_system_prompt(), get_system_prompt())

    def test_prompt_contains_key_rules(self):
        prompt = get_system_prompt()
        self.assertIn("NEVER reveal the test code", prompt)
        self.assertIn("NEVER reveal the reference solution", prompt)
        self.assertIn("prompt injection", prompt)

    def test_hash_is_16_hex_chars(self):
        h = get_prompt_hash()
        self.assertEqual(len(h), 16)
        int(h, 16)  # must parse as hex

    def test_hash_is_cached_and_consistent(self):
        self.assertEqual(get_prompt_hash(), get_prompt_hash())


class TestBuildStudentMessage(TestCase):
    def test_includes_all_three_sections(self):
        msg = build_student_message({"a.py": "print(1)"}, "FAILED", ["checks sum"])
        self.assertIn("=== STUDENT CODE ===", msg)
        self.assertIn("=== TEST RESULTS ===", msg)
        self.assertIn("=== WHAT THE TESTS CHECK ===", msg)
        self.assertIn("a.py", msg)
        self.assertIn("print(1)", msg)
        self.assertIn("FAILED", msg)
        self.assertIn("checks sum", msg)
        self.assertTrue(msg.rstrip().endswith("Please provide guidance for each test above."))

    def test_handles_missing_code(self):
        msg = build_student_message({}, "out", [])
        self.assertIn("(No code files found)", msg)

    def test_handles_missing_test_results(self):
        msg = build_student_message({"a.py": "x"}, "", [])
        self.assertIn("(No test results available)", msg)

    def test_handles_missing_descriptions(self):
        msg = build_student_message({"a.py": "x"}, "out", [])
        self.assertIn("(No test descriptions available)", msg)

    def test_truncates_large_files(self):
        big = "x" * 20000
        msg = build_student_message({"big.py": big}, "out", [])
        self.assertIn("truncated, 20000 total chars", msg)
        # The full 20k payload must not be present verbatim.
        self.assertLess(len(msg), 20000)

    def test_small_file_is_not_truncated(self):
        msg = build_student_message({"small.py": "x" * 100}, "out", [])
        self.assertNotIn("truncated", msg)

    def test_multiple_test_descriptions_are_bulleted(self):
        msg = build_student_message({}, "out", ["first", "second"])
        self.assertIn("- first", msg)
        self.assertIn("- second", msg)


class TestSanitizeOutput(TestCase):
    def test_passes_through_normal_feedback(self):
        text = "Your loop is off by one. Consider the last index."
        self.assertEqual(sanitize_output(text), text)

    def test_blocks_test_script_disclosure(self):
        result = sanitize_output("Here is the test script content: ...")
        self.assertIn("I noticed an issue", result)

    def test_blocks_here_is_the_test_pattern(self):
        result = sanitize_output("Here is the expected output")
        self.assertIn("I noticed an issue", result)

    def test_blocks_raw_assertions(self):
        result = sanitize_output("assert(x == 42)")
        self.assertIn("I noticed an issue", result)

    def test_blocks_leaked_test_function(self):
        result = sanitize_output("def test_sum():\n    assert(1 == 1)")
        self.assertIn("I noticed an issue", result)

    def test_blocks_test_script_disclosure_phrase(self):
        result = sanitize_output("test script contents:")
        self.assertIn("I noticed an issue", result)

    def test_case_insensitive_detection(self):
        result = sanitize_output("THIS IS THE TEST output")
        self.assertIn("I noticed an issue", result)

"""Tests for athina.llm_agent_prompt — prompt building and leak sanitization."""
from unittest import TestCase

from athina.llm_agent_prompt import (DEFAULT_MAX_CODE_CHARS, build_student_message,
                                     get_prompt_hash, get_system_prompt,
                                     render_results, sanitize_output, split_markers)


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


class TestPromptBudgets(TestCase):
    """The payload has to stay bounded no matter how chatty the source is."""

    def test_total_code_budget_caps_many_files(self):
        # Twenty 8k files would be 160k chars without a combined budget.
        code = {"f%d.py" % i: "x" * 8000 for i in range(20)}
        msg = build_student_message(code, "out", [])
        code_section = msg.split("=== TEST RESULTS ===")[0]
        self.assertLessEqual(len(code_section), DEFAULT_MAX_CODE_CHARS + 2000)
        self.assertIn("more file(s) omitted", msg)

    def test_per_file_budget_is_independent_of_total(self):
        code = {"a.py": "a" * 20000, "b.py": "b" * 20000}
        msg = build_student_message(code, "out", [], max_file_chars=1000,
                                    max_code_chars=100000)
        self.assertIn("truncated, 20000 total chars", msg)
        self.assertNotIn("more file(s) omitted", msg)

    def test_zero_disables_the_code_budget(self):
        code = {"a.py": "x" * 50000}
        msg = build_student_message(code, "out", [], max_file_chars=0,
                                    max_code_chars=0)
        self.assertIn("x" * 50000, msg)

    def test_long_test_output_is_truncated_and_says_so(self):
        msg = build_student_message({}, "y" * 50000, [], max_test_chars=1000)
        self.assertIn("chars omitted", msg)
        self.assertLess(len(msg), 50000)

    def test_truncation_keeps_head_and_tail(self):
        body = "HEAD" + ("z" * 50000) + "TAIL"
        msg = build_student_message({}, body, [], max_test_chars=1000)
        self.assertIn("HEAD", msg)
        self.assertIn("TAIL", msg)
        self.assertIn("chars omitted", msg)

    def test_marker_lines_are_kept_verbatim_ahead_of_raw_output(self):
        # This is the Java/SQL shape: a large dump of rows plus compact verdicts.
        raw = "row %d\n" * 5000 % tuple(range(5000))
        output = raw + "#ATHINA# PASS | q1.sql: output matches instructor\n" \
                      + "#ATHINA# FAIL | q2.sql: output matches instructor\n"
        msg = build_student_message({}, output, [], max_test_chars=1000)
        self.assertIn("Summary of test verdicts:", msg)
        self.assertIn("#ATHINA# PASS | q1.sql: output matches instructor", msg)
        self.assertIn("#ATHINA# FAIL | q2.sql: output matches instructor", msg)
        self.assertLess(len(msg), 20000)

    def test_raw_output_dropped_when_verdicts_use_the_budget(self):
        markers = "\n".join("#ATHINA# PASS | q%d.sql: ok" % i for i in range(200))
        msg = build_student_message({}, markers + "\nnoise\n" * 5000,
                                    [], max_test_chars=1000)
        self.assertIn("raw test output omitted", msg)
        self.assertNotIn("noise", msg)

    def test_split_markers_separates_verdicts(self):
        markers, rest = split_markers("some output\n#ATHINA# FAIL | x: bad\nmore")
        self.assertEqual(markers, ["#ATHINA# FAIL | x: bad"])
        self.assertIn("some output", rest)
        self.assertNotIn("#ATHINA#", rest)

    def test_split_markers_tolerates_empty_input(self):
        self.assertEqual(split_markers(None), ([], ""))
        self.assertEqual(split_markers(""), ([], ""))


class TestRenderedRecords(TestCase):
    """Parsed records are the preferred (compact) form of the test signal."""

    def _records(self):
        return [
            {"name": "q1.sql", "passed": True, "score": 100.0, "detail": ""},
            {"name": "q2.sql", "passed": False, "score": 0.0,
             "detail": "output differs"},
        ]

    def test_render_results_shapes_lines(self):
        lines = render_results(self._records())
        self.assertEqual(lines[0], "PASS | q1.sql: score 100")
        self.assertEqual(lines[1], "FAIL | q2.sql: output differs; score 0")

    def test_render_results_handles_unknown_verdict(self):
        lines = render_results([{"name": "t", "passed": None, "score": 0}])
        self.assertTrue(lines[0].startswith("SKIP | t"))

    def test_render_results_ignores_non_dicts(self):
        self.assertEqual(render_results(["nonsense", None]), [])

    def test_records_replace_the_bulk_of_the_raw_log(self):
        """The Java/SQL shape: 17k rows of stdout plus two verdicts."""
        raw = "\n".join("499724 Ana Smith" for _ in range(500))
        msg = build_student_message({}, raw, [], max_test_chars=4000,
                                    test_records=self._records())
        self.assertIn("Summary of test verdicts:", msg)
        self.assertIn("FAIL | q2.sql: output differs", msg)
        self.assertLess(len(msg), len(raw))

    def test_records_without_raw_output(self):
        msg = build_student_message({}, "", [], test_records=self._records())
        self.assertIn("FAIL | q2.sql", msg)
        self.assertNotIn("(No test results available)", msg)

    def test_raw_excerpt_dropped_when_records_use_the_budget(self):
        """A long verdict list is the priority; raw output only gets what is left."""
        records = [{"name": "q%d.sql" % i, "passed": False, "score": 0.0,
                    "detail": "mismatch"} for i in range(100)]
        raw = "\n".join("row" for _ in range(2000))
        msg = build_student_message({}, raw, [], max_test_chars=600,
                                    test_records=records)
        self.assertIn("raw test output omitted", msg)
        self.assertNotIn("rowrowrow", msg)

    def test_total_test_section_stays_within_budget(self):
        records = [{"name": "q%d.sql" % i, "passed": False, "score": 0.0,
                    "detail": "mismatch"} for i in range(100)]
        msg = build_student_message({}, "z" * 50000, [], max_test_chars=3000,
                                    test_records=records)
        section = msg.split("=== TEST RESULTS ===")[1].split("=== WHAT THE TESTS CHECK ===")[0]
        # Budget plus the fixed headings and the omission marker.
        self.assertLess(len(section), 3000 + 2000)

    def test_no_records_falls_back_to_markers_then_raw(self):
        raw = "some noise\n#ATHINA# FAIL | q9.sql: mismatch\n"
        msg = build_student_message({}, raw, [])
        self.assertIn("Summary of test verdicts:", msg)
        self.assertIn("FAIL | q9.sql: mismatch", msg)
        self.assertIn("some noise", msg)

    def test_signal_lines_are_preferred_over_data_rows(self):
        """Data rows are noise; the failure lines are the evidence."""
        rows = "\n".join("499724 Ana Smith" for _ in range(2000))
        tail = ("\nnot ok 2 Showing employees from Human Resources department\n"
                "java.sql.SQLException: Column 'dept_no' not found\n")
        msg = build_student_message({}, rows + tail, [],
                                    test_records=self._records())
        section = msg.split("=== TEST RESULTS ===")[1]
        self.assertIn("not ok 2", section)
        self.assertIn("SQLException", section)
        self.assertNotIn("Ana Smith", section)

    def test_excerpt_falls_back_to_the_edges_when_nothing_looks_like_signal(self):
        rows = "\n".join("499724 Ana Smith" for _ in range(2000))
        msg = build_student_message({}, rows, [], test_records=self._records())
        section = msg.split("=== TEST RESULTS ===")[1]
        self.assertIn("chars omitted", section)

    def test_very_small_limit_keeps_only_the_head(self):
        """A tiny budget cannot spare a tail, so only the head is kept."""
        msg = build_student_message({}, "a" * 500, [], max_test_chars=100)
        self.assertIn("chars omitted", msg)
        self.assertLess(len(msg), 500)

    def test_excerpt_without_omission_returns_the_signal_text(self):
        """The signal lines fit inside the budget, so no omission note is added."""
        raw = "not ok 1 short\n" + ("filler row\n" * 200)
        msg = build_student_message({}, raw, [], max_test_chars=500)
        self.assertIn("not ok 1 short", msg)
        self.assertNotIn("more chars of raw output omitted", msg)

    def test_signal_lines_over_budget_are_themselves_trimmed(self):
        """Enough failures to overflow the excerpt budget still get a note."""
        raw = "\n".join("not ok %d something failed" % i for i in range(100))
        msg = build_student_message({}, raw, [], max_test_chars=300,
                                    test_records=self._records())
        self.assertIn("more chars of raw output omitted", msg)
        self.assertIn("not ok 0", msg)

    def test_record_with_empty_detail_and_no_score(self):
        """A bare verdict renders without the optional detail/score parts."""
        self.assertEqual(render_results([{"name": "t", "passed": True}]),
                         ["PASS | t"])

    def test_excerpt_has_no_limit_when_the_test_budget_is_unlimited(self):
        """max_test_chars=0 keeps the log, but it is still signal-first."""
        raw = "not ok 1 x\n" + ("filler row\n" * 5000)
        msg = build_student_message({}, raw, [], max_test_chars=0,
                                    test_records=self._records())
        self.assertIn("not ok 1 x", msg)
        self.assertIn("FAIL | q2.sql", msg)
        self.assertNotIn("chars omitted", msg)

    def test_non_numeric_score_is_ignored(self):
        records = [{"name": "t", "passed": False, "score": "n/a", "detail": ""}]
        self.assertEqual(render_results(records), ["FAIL | t"])


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

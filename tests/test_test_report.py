"""Tests for athina.test_report (per-test record parsing).

Two notations are supported: the `athina-test.sh` helper's `ATHINA_TEST:` lines
and plain `#ATHINA#` marker lines. Anything else must fall back to one row per
script so assignments that opt out still get a breakdown.
"""
from unittest import TestCase

from athina.test_report import (MARKER, PREFIX, _MAX, derive_results,
                                parse_records)


class TestHelperNotation(TestCase):
    """`ATHINA_TEST:key|status|score|weight|name|detail`"""

    def test_full_record(self):
        rows = parse_records("ATHINA_TEST:q1|pass|100|0.5|Query 1|matches")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["key"], "q1")
        self.assertEqual(row["name"], "Query 1")
        self.assertEqual(row["score"], 100.0)
        self.assertEqual(row["weight"], 0.5)
        self.assertIs(row["passed"], True)
        self.assertEqual(row["detail"], "matches")

    def test_fail_status(self):
        rows = parse_records("ATHINA_TEST:q1|fail|0")
        self.assertIs(rows[0]["passed"], False)

    def test_unknown_status_stays_undecided(self):
        rows = parse_records("ATHINA_TEST:q1|?|50")
        self.assertIsNone(rows[0]["passed"])

    def test_defaults_when_optional_fields_are_missing(self):
        rows = parse_records("ATHINA_TEST:q1|pass|100")
        self.assertEqual(rows[0]["name"], "q1")   # name falls back to key
        self.assertEqual(rows[0]["weight"], 1.0)
        self.assertEqual(rows[0]["detail"], "")

    def test_detail_may_contain_pipes(self):
        rows = parse_records("ATHINA_TEST:q1|pass|100|1|n|a|b|c")
        self.assertEqual(rows[0]["detail"], "a|b|c")

    def test_too_few_fields_is_ignored(self):
        self.assertEqual(parse_records("ATHINA_TEST:q1|pass"), [])

    def test_non_numeric_score_is_zero(self):
        rows = parse_records("ATHINA_TEST:q1|pass|not-a-number")
        self.assertEqual(rows[0]["score"], 0.0)

    def test_score_is_clamped(self):
        self.assertEqual(parse_records("ATHINA_TEST:q|pass|150")[0]["score"], 100.0)
        self.assertEqual(parse_records("ATHINA_TEST:q|fail|-20")[0]["score"], 0.0)

    def test_bad_weight_falls_back_to_one(self):
        self.assertEqual(parse_records("ATHINA_TEST:q|pass|100|abc")[0]["weight"], 1.0)

    def test_zero_or_negative_weight_falls_back_to_one(self):
        self.assertEqual(parse_records("ATHINA_TEST:q|pass|100|0")[0]["weight"], 1.0)
        self.assertEqual(parse_records("ATHINA_TEST:q|pass|100|-3")[0]["weight"], 1.0)

    def test_long_fields_are_truncated(self):
        rows = parse_records("ATHINA_TEST:%s|pass|100|1|%s|%s"
                             % ("k" * 400, "n" * 400, "d" * (_MAX + 50)))
        self.assertEqual(len(rows[0]["key"]), 255)
        self.assertEqual(len(rows[0]["name"]), 255)
        self.assertEqual(len(rows[0]["detail"]), _MAX)

    def test_whitespace_around_lines_is_tolerated(self):
        rows = parse_records("   ATHINA_TEST:q|pass|100   ")
        self.assertEqual(rows[0]["key"], "q")

    def test_prefix_constant_matches(self):
        self.assertTrue("ATHINA_TEST:q|pass|100".startswith(PREFIX))


class TestMarkerNotation(TestCase):
    """`#ATHINA# PASS | name: detail`"""

    def test_pass_marker(self):
        rows = parse_records("#ATHINA# PASS | q1.sql: output matches")
        self.assertEqual(rows[0]["name"], "q1.sql")
        self.assertEqual(rows[0]["detail"], "output matches")
        self.assertEqual(rows[0]["score"], 100.0)
        self.assertIs(rows[0]["passed"], True)
        self.assertTrue(rows[0]["scoped"])

    def test_fail_marker(self):
        rows = parse_records("#ATHINA# FAIL | q1.sql: wrong rows")
        self.assertEqual(rows[0]["score"], 0.0)
        self.assertIs(rows[0]["passed"], False)

    def test_skip_marker_is_undecided(self):
        rows = parse_records("#ATHINA# SKIP | q1.sql: not attempted")
        self.assertIsNone(rows[0]["passed"])
        self.assertEqual(rows[0]["score"], 0.0)

    def test_marker_is_case_insensitive(self):
        rows = parse_records("#athina# pass | q1.sql: ok")
        self.assertEqual(rows[0]["name"], "q1.sql")

    def test_marker_without_detail(self):
        rows = parse_records("#ATHINA# PASS | q1.sql")
        self.assertEqual(rows[0]["name"], "q1.sql")
        self.assertEqual(rows[0]["detail"], "")

    def test_only_first_separator_splits_name_and_detail(self):
        rows = parse_records("#ATHINA# PASS | q1.sql: expected: 3 rows")
        self.assertEqual(rows[0]["name"], "q1.sql")
        self.assertEqual(rows[0]["detail"], "expected: 3 rows")

    def test_marker_without_a_name_is_ignored(self):
        self.assertEqual(parse_records("#ATHINA# PASS | "), [])
        self.assertEqual(parse_records("#ATHINA# PASS"), [])

    def test_unknown_verdict_is_not_a_marker(self):
        self.assertEqual(parse_records("#ATHINA# MAYBE | q1.sql"), [])

    def test_marker_constant_matches(self):
        self.assertTrue("#ATHINA# PASS | x".startswith(MARKER))


class TestMixedAndEmptyInput(TestCase):
    def test_both_notations_in_one_output(self):
        text = "\n".join([
            "some student output",
            "#ATHINA# PASS | q1.sql: ok",
            "ATHINA_TEST:q2|fail|0|1|Query 2|bad",
            "trailing noise",
        ])
        rows = parse_records(text)
        self.assertEqual([r["key"] for r in rows], ["q1.sql", "q2"])

    def test_empty_and_none_input(self):
        self.assertEqual(parse_records(""), [])
        self.assertEqual(parse_records(None), [])

    def test_unrelated_lines_are_ignored(self):
        self.assertEqual(parse_records("just some output\nmore output"), [])

    def test_records_are_parsed_from_bytes_decoded_text(self):
        self.assertEqual(parse_records("noise\r\n#ATHINA# PASS | q: ok\r\n")[0]["key"],
                         "q")


class TestDeriveResults(TestCase):
    """The fallback row for a script that emitted no records."""

    def test_perfect_score_passes(self):
        row = derive_results(0, "bash test", 100, weight=0.7, detail="out")[0]
        self.assertEqual(row["key"], "0:bash test")
        self.assertEqual(row["script"], "bash test")
        self.assertEqual(row["name"], "bash test")
        self.assertEqual(row["weight"], 0.7)
        self.assertEqual(row["score"], 100.0)
        self.assertIs(row["passed"], True)
        self.assertEqual(row["detail"], "out")

    def test_partial_score_does_not_pass(self):
        row = derive_results(1, "bash test2", 70)[0]
        self.assertEqual(row["score"], 70.0)
        self.assertIs(row["passed"], False)

    def test_none_score_becomes_zero(self):
        # `score` is normalised to 0.0 before the verdict, so this is a fail
        # rather than an undecided row.
        row = derive_results(0, "s", None)[0]
        self.assertEqual(row["score"], 0.0)
        self.assertIs(row["passed"], False)

    def test_score_is_clamped(self):
        self.assertEqual(derive_results(0, "s", 250)[0]["score"], 100.0)
        self.assertEqual(derive_results(0, "s", -5)[0]["score"], 0.0)

    def test_zero_weight_falls_back_to_one(self):
        self.assertEqual(derive_results(0, "s", 50, weight=0)[0]["weight"], 1.0)

    def test_detail_is_truncated(self):
        row = derive_results(0, "s", 50, detail="d" * (_MAX + 10))[0]
        self.assertEqual(len(row["detail"]), _MAX)

"""Parse the per-test records a test script can emit.

Two notations are understood, so a test can report individual results without
Athina caring where the line came from:

1. The `athina-test.sh` helper (records go to stderr so they never pollute the
   compared output)::

       ATHINA_TEST:key|status|score|weight|name|detail

   status is pass/fail/?; score is 0-100; detail is optional.

2. A plain marker line in the test's normal output (the simplest form -- just
   `echo` it alongside the existing PASS/FAIL output)::

       #ATHINA# PASS | q1.sql: output matches instructor
       #ATHINA# FAIL | q1.sql: output matches instructor

   The part before the first ": " becomes the test's name/key; the rest is the
   detail. A marker carries only a verdict, so its score is 100 or 0.

Scripts that use neither are unaffected: `derive_results` produces one row per
test script from the percentage Athina already parses, so every assignment gets a
breakdown whether or not it opts in.
"""

import re

PREFIX = "ATHINA_TEST:"
MARKER = "#ATHINA#"
_MAX = 60000  # keep a single record's detail bounded

_RECORD = re.compile(r"^" + re.escape(PREFIX) + r"(.*)$")
# #ATHINA# PASS | <name>: <detail>   (separator may be "|" or ":", detail optional)
_MARKER = re.compile(r"^" + re.escape(MARKER) + r"\s*(PASS|FAIL|SKIP)\b\s*\|?\s*(.*)$",
                     re.IGNORECASE)


def _parse_marker(line):
    """Turn a '#ATHINA# PASS | name: detail' line into a record, or None."""
    match = _MARKER.match(line.strip())
    if not match:
        return None
    verdict = match.group(1).upper()
    rest = (match.group(2) or "").strip()
    # "name: detail" -> name + detail. Only split on the FIRST ": " so a detail that
    # itself contains one is preserved; when the text after the separator is empty
    # the whole rest is the name.
    name, sep, detail = rest.partition(": ")
    name = name.strip()
    if not name:
        return None
    if verdict == "PASS":
        passed, score = True, 100.0
    elif verdict == "FAIL":
        passed, score = False, 0.0
    else:
        passed, score = None, 0.0
    return {
        "key": name[:255],
        "script": "",
        "name": name[:255],
        "weight": 1.0,
        "score": score,
        "passed": passed,
        "detail": (detail.strip() if sep else "")[:_MAX],
        # Markers name a check in plain words, so the same name can legitimately
        # appear in two test scripts (e.g. "q1.sql" in both correctness and
        # efficiency). The caller scopes these keys per script to keep them unique.
        "scoped": True,
    }


def parse_records(text):
    """Return the records found in `text` as a list of dicts."""
    results = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        marker = _parse_marker(stripped)
        if marker is not None:
            results.append(marker)
            continue
        match = _RECORD.match(stripped)
        if not match:
            continue
        parts = match.group(1).split("|")
        if len(parts) < 3:
            continue
        key, status, score = parts[0], parts[1], parts[2]
        weight = parts[3] if len(parts) > 3 else "1"
        name = parts[4] if len(parts) > 4 else key
        detail = "|".join(parts[5:]) if len(parts) > 5 else ""
        passed = None
        if status == "pass":
            passed = True
        elif status == "fail":
            passed = False
        try:
            score_value = float(score)
        except (TypeError, ValueError):
            score_value = 0.0
        try:
            weight_value = float(weight)
        except (TypeError, ValueError):
            weight_value = 1.0
        if weight_value <= 0:
            weight_value = 1.0
        results.append({
            "key": key.strip()[:255],
            "script": "",
            "name": (name or key).strip()[:255],
            "weight": weight_value,
            "score": max(0.0, min(100.0, score_value)),
            "passed": passed,
            "detail": (detail or "")[:_MAX],
        })
    return results


def derive_results(script_index, script_name, score, weight=1.0, detail=""):
    """Build the single fallback row for a test script that reported no records."""
    score = 0.0 if score is None else max(0.0, min(100.0, float(score)))
    return [{
        "key": "%d:%s" % (script_index, script_name),
        "script": script_name,
        "name": script_name,
        "weight": float(weight or 1.0),
        "score": score,
        "passed": None if score is None else score >= 100.0,
        "detail": (detail or "")[:_MAX],
    }]

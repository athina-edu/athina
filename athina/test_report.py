"""Parse the per-test records a test script can emit.

Test scripts that opt in source `athina-test.sh` and print records to stderr of the
form::

    ATHINA_TEST:key|status|score|weight|name|detail

(status is pass/fail/?; score is 0-100; detail is optional and may be empty).

Scripts that do not opt in are unaffected: `derive_results` produces one row per
test script from the percentage Athina already parses, so every assignment gets a
breakdown, whether or not it uses the protocol.
"""

import re

PREFIX = "ATHINA_TEST:"
_MAX = 60000  # keep a single record's detail bounded

_RECORD = re.compile(r"^" + re.escape(PREFIX) + r"(.*)$")


def parse_records(text):
    """Return the records found in `text` as a list of dicts."""
    results = []
    for line in (text or "").splitlines():
        match = _RECORD.match(line.strip())
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

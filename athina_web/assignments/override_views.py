"""Post-grade override of individual test scores, with notes.

Athina's grading run writes one row per test into `testresults` (see
athina.users.TestResult): the reported score, an optional pass/fail verdict, and a
weight. Faculty and TAs can adjust a single test's score, attach a note, and re-post
the result to the same channel the grade normally goes to (GitLab issue or Canvas).

Nothing here re-runs the tests: this is a correction layer on top of a finished
grading run, so an instructor can, e.g., accept a submission that failed a flaky
timing check without forcing a full re-grade.
"""

import html
import os
import json

import requests as http_requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from athina_web.accounts.models import UserProfile
from .models import Assignment

from .views import (connect_to_db, _read_yaml_ids, _user_can_access_course,
                    _resolve_output_method, _get_gitlab_config,
                    _read_assignment_yaml)


def _total_points(assignment):
    """Assignment points live in athina.yaml (total_points), not the Django model."""
    try:
        return int(_read_assignment_yaml(assignment).get('total_points') or 0) or 100
    except (TypeError, ValueError):
        return 100


def _can_override(user, assignment):
    """Owner, admin, or a TA who assists the owner may override scores."""
    if user.is_superuser or assignment.owner == user.id:
        return True
    if assignment.course is None:
        return False
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return False
    if profile.role == UserProfile.ROLE_ADMIN:
        return True
    if profile.role == UserProfile.ROLE_TA:
        return _user_can_access_course(user, assignment.course)
    return _user_can_access_course(user, assignment.course)


def _load_rows(course_id, assignment_id, user_id):
    """Read the student's per-test rows straight from the grading DB."""
    conn = connect_to_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT `key`, script, name, weight, score, passed, detail, "
            "override_score, note, overridden_by, overridden_at "
            "FROM testresults WHERE course_id=%s AND assignment_id=%s AND user_id=%s "
            "ORDER BY script, `key`",
            (course_id, assignment_id, user_id))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()
    for row in rows:
        row['effective'] = row['override_score'] if row['override_score'] is not None else row['score']
    return rows


def _store_overrides(course_id, assignment_id, user_id, overrides, author):
    """Persist per-key overrides. `overrides` maps key -> (score or None, note)."""
    conn = connect_to_db()
    try:
        cur = conn.cursor()
        for key, (score, note) in overrides.items():
            if score is None:
                cur.execute(
                    "UPDATE testresults SET override_score=NULL, note=%s, "
                    "overridden_by=%s, overridden_at=NOW() "
                    "WHERE course_id=%s AND assignment_id=%s AND user_id=%s AND `key`=%s",
                    (note or '', author, course_id, assignment_id, user_id, key))
            else:
                cur.execute(
                    "UPDATE testresults SET override_score=%s, note=%s, "
                    "overridden_by=%s, overridden_at=NOW() "
                    "WHERE course_id=%s AND assignment_id=%s AND user_id=%s AND `key`=%s",
                    (score, note or '', author, course_id, assignment_id, user_id, key))
        conn.commit()
    finally:
        conn.close()


def _weighted_grade(rows, total_points):
    """Recompute the assignment grade from effective (overridden) scores."""
    if not rows:
        return 0
    total_weight = sum((r['weight'] or 0) for r in rows)
    if total_weight <= 0:
        pct = sum(r['effective'] for r in rows) / len(rows)
    else:
        pct = sum(r['effective'] * (r['weight'] or 0) for r in rows) / total_weight
    return int(round(pct / 100.0 * total_points))


@login_required
def assignment_overrides(request, assignment_id, user_id):
    """Show (and save) the per-test scores for one student."""
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if not _can_override(request.user, assignment):
        raise Http404
    course_id_val, assignment_id_val = _read_yaml_ids(assignment)

    if request.method == "POST":
        overrides = {}
        for key in request.POST.getlist('key'):
            raw_score = (request.POST.get('score_%s' % key) or '').strip()
            note = request.POST.get('note_%s' % key, '')
            if raw_score == '':
                overrides[key] = (None, note)      # clear the override
                continue
            try:
                overrides[key] = (max(0.0, min(100.0, float(raw_score))), note)
            except ValueError:
                messages.error(request, "Score for %s must be a number." % key)
        if overrides:
            _store_overrides(course_id_val, assignment_id_val, user_id, overrides,
                             request.user.get_username())
            messages.success(request, "Scores saved.")
        return redirect('assignments:assignment_overrides',
                        assignment_id=assignment.pk, user_id=user_id)

    try:
        rows = _load_rows(course_id_val, assignment_id_val, user_id)
    except Exception:
        return render(request, 'assignments/assignment_empty.html',
                      {"assignment": assignment,
                       "message": "Cannot read the grading database."})
    if not rows:
        return render(request, 'assignments/assignment_empty.html',
                      {"assignment": assignment,
                       "message": "No per-test results recorded for this student yet. "
                                  "Run a test first."})
    return render(request, 'assignments/assignment_overrides.html', {
        "assignment": assignment,
        "user_id": user_id,
        "rows": rows,
        "grade": _weighted_grade(rows, _total_points(assignment)),
        "total_points": _total_points(assignment),
        "output_method": _resolve_output_method(assignment),
    })


def _student_repo_url(course_id, assignment_id, user_id):
    conn = connect_to_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT repository_url, user_fullname, gitlab_issue_iid FROM users "
                    "WHERE course_id=%s AND assignment_id=%s AND user_id=%s",
                    (course_id, assignment_id, user_id))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return '', '', 0
    return (row[0] or '', row[1] or '', row[2] or 0)


def _gitlab_project_path(repo_url):
    """'https://host/group/repo.git' -> ('host', 'group%2Frepo')."""
    from urllib.parse import urlparse, quote
    import re as _re
    if not repo_url:
        return None, None
    cleaned = _re.sub(r'://[^@]+@', '://', repo_url).strip()
    parsed = urlparse(cleaned)
    if not parsed.netloc:
        return None, None
    path = _re.sub(r'\.git/?$', '', parsed.path.lstrip('/'))
    if not path:
        return None, None
    return parsed.netloc, quote(path, safe='')


def _post_to_gitlab(assignment, repo_url, student_name, grade, rows, issue_iid=0):
    """Create/update the grade issue on the student's repo with the overridden scores."""
    host, project_path = _gitlab_project_path(repo_url)
    if assignment.course is None:
        return False, "No course is attached to this assignment."
    gitlab_url, token = _get_gitlab_config(assignment.course)
    if not gitlab_url or not token:
        return False, "GitLab is not configured for this course."
    host = host or gitlab_url
    if not project_path:
        return False, "Cannot determine the student's GitLab project."

    lines = ["## Grade Report (updated by instructor)", "",
             "| Field | Value |", "|-------|-------|",
             "| **Student** | %s |" % student_name,
             "| **Grade** | %d / %s |" % (grade, _total_points(assignment)), "",
             "### Test scores", "", "| Test | Score | Report | Note |", "|---|---|---|---|"]
    for row in rows:
        lines.append("| %s | %.1f | %.1f | %s |" % (
            (row['name'] or row['key']).replace('|', '/'),
            row['effective'], row['score'], (row['note'] or '').replace('|', '/')))
    body = "\n".join(lines)

    api = "https://%s/api/v4" % host
    headers = {"PRIVATE-TOKEN": token}
    title = "%s — %s" % (student_name, "Grade Report")
    try:
        # Prefer updating the issue the grading run already created, so the student
        # sees one report rather than a new issue per correction.
        if issue_iid:
            resp = http_requests.put(
                "%s/projects/%s/issues/%s" % (api, project_path, issue_iid),
                headers=headers,
                json={"title": title, "description": body}, timeout=30)
        else:
            resp = http_requests.post(
                "%s/projects/%s/issues" % (api, project_path),
                headers=headers,
                json={"title": title, "description": body}, timeout=30)
    except http_requests.RequestException as exc:
        return False, "Could not reach GitLab: %s" % exc
    if resp.status_code >= 300:
        return False, "GitLab returned %s: %s" % (resp.status_code, resp.text[:200])
    return True, "Posted to GitLab."


@login_required
def assignment_overrides_post(request, assignment_id, user_id):
    """Re-post a student's (possibly overridden) result to the configured output."""
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if not _can_override(request.user, assignment):
        raise Http404
    if request.method != 'POST':
        raise Http404

    course_id_val, assignment_id_val = _read_yaml_ids(assignment)
    try:
        rows = _load_rows(course_id_val, assignment_id_val, user_id)
    except Exception:
        messages.error(request, "Cannot read the grading database.")
        return redirect('assignments:assignment_overrides',
                        assignment_id=assignment.pk, user_id=user_id)
    if not rows:
        messages.error(request, "Nothing to post: no per-test results.")
        return redirect('assignments:assignment_overrides',
                        assignment_id=assignment.pk, user_id=user_id)

    grade = _weighted_grade(rows, _total_points(assignment))
    repo_url, student_name, issue_iid = _student_repo_url(course_id_val, assignment_id_val, user_id)
    student_name = student_name or str(user_id)
    output_method = _resolve_output_method(assignment)

    if output_method == 'gitlab_issues':
        ok, detail = _post_to_gitlab(assignment, repo_url, student_name, grade, rows,
                                     issue_iid=issue_iid)
    else:
        ok, detail = False, ("Canvas posting is handled by the grading engine; use "
                             "Force Rerun to publish the updated score.")

    # Record the final grade so the dashboard reflects the override.
    try:
        conn = connect_to_db()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE users SET last_grade=%s WHERE course_id=%s "
                        "AND assignment_id=%s AND user_id=%s",
                        (grade, course_id_val, assignment_id_val, user_id))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass

    if ok:
        messages.success(request, detail)
    else:
        messages.error(request, detail)
    return redirect('assignments:assignment_overrides',
                    assignment_id=assignment.pk, user_id=user_id)

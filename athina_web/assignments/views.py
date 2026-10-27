# from django.http import HttpResponse
from django.shortcuts import render
from django.shortcuts import redirect
from django.shortcuts import get_object_or_404
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import Http404
from django.http import HttpResponse
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from .models import Assignment, Course, Student, AssignmentRepo, StudentGroup, GroupMembership
from .forms import (AssignmentForm, CourseForm, StudentForm, StudentEditForm,
                    StudentBulkForm, AssignmentRepoForm, StudentGroupForm,
                    GroupMembersForm)
from athina_web.accounts.models import UserProfile
import os
import random
import shutil
import logging
import time
from rest_framework import generics
from .serializers import AssignmentListSerializer
import git
import html
import base64
import re
import glob
import pymysql
import yaml
import json
from datetime import timedelta
import datetime
import dateutil.parser
import requests as http_requests
import threading
from athina_web.athina_db import db_info
from athina_web.accounts.models import UserProfile
from athina_web.accounts.resend_email import send_email_safe
# Shared with the grading daemon: one signal file per (student, assignment) event.
from athina.trigger import write_full_pass_trigger, write_student_trigger


def _user_can_access_course(user, course):
    """Check if a user can access a course based on their role."""
    if course is None:
        return user.is_superuser
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return user.is_superuser
    if user.is_superuser or profile.role == UserProfile.ROLE_ADMIN:
        return True
    if profile.role == UserProfile.ROLE_FACULTY:
        return course.owner == user.id
    if profile.role == UserProfile.ROLE_TA:
        # Course.owner is an IntegerField, so compare against ids directly.
        # (Older code used course.owner_id, which Course has no attribute for.)
        return profile.managed_by.filter(id=course.owner).exists()
    return False


def _can_override_scores(user, assignment):
    """May this user adjust individual test scores for this assignment?

    Faculty/admin own it; a TA may do it for courses they assist. This is narrower
    than course management (TAs must not create/edit assignments) but broader than
    viewing, because correcting a score is grading work.
    """
    if user.is_superuser or assignment.owner == user.id:
        return True
    if assignment.course is None:
        return False
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return False
    if profile.role in (UserProfile.ROLE_ADMIN, UserProfile.ROLE_FACULTY,
                        UserProfile.ROLE_TA):
        return _user_can_access_course(user, assignment.course)
    return False


def _user_can_manage_courses(user):
    """Can this user create/edit/delete courses and assignments?

    TAs are read-only: they may view and grade for the faculty they assist,
    but must not create courses or assignments.
    """
    if user.is_superuser:
        return True
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return False
    return profile.role in (UserProfile.ROLE_ADMIN, UserProfile.ROLE_FACULTY)


def _user_can_manage_students(user, course):
    """Can this user add/import/edit/delete students in this course?

    TAs may only *view* the roster, repository links and test results — adding,
    importing, editing, deleting, provisioning or notifying students is
    admin/faculty work.
    """
    return _user_can_manage_courses(user) and _user_can_access_course(user, course)


def _get_visible_courses(user):
    """Return courses visible to the current user based on their role."""
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        if user.is_superuser:
            return Course.objects.all()
        return Course.objects.none()
    if user.is_superuser or profile.role == UserProfile.ROLE_ADMIN:
        return Course.objects.all()
    if profile.role == UserProfile.ROLE_FACULTY:
        return Course.objects.filter(owner=user.id)
    if profile.role == UserProfile.ROLE_TA:
        faculty_ids = profile.managed_by.values_list('id', flat=True)
        return Course.objects.filter(owner__in=faculty_ids)
    return Course.objects.none()


def _read_yaml_ids(assignment):
    """Read course_id and assignment_id for an assignment.
    
    For db input mode: uses Django model PKs (the IDs live in the model, not the YAML).
    For canvas input mode: reads from the YAML config (Canvas course/assignment IDs).
    
    Falls back to Django model PKs if YAML is not available."""
    yaml_path = os.path.join(settings.BASE_DIR, assignment.absolute_path, 'athina.yaml')
    try:
        with open(yaml_path, 'r') as f:
            cfg = yaml.safe_load(f)
        if cfg:
            input_method = cfg.get('input_method', 'canvas')
            if input_method == 'db':
                # db mode: IDs come from the Django model, not the YAML
                return (assignment.course_id or assignment.pk, assignment.pk)
            # canvas mode: IDs come from the YAML
            yaml_cid = cfg.get('course_id')
            yaml_aid = cfg.get('assignment_id')
            if yaml_cid is not None and yaml_aid is not None:
                return (yaml_cid, yaml_aid)
    except Exception:
        pass
    # Fallback to Django model PKs
    return (assignment.course_id or assignment.pk, assignment.pk)


def _write_group_settings(assignment, has_groups):
    """Record whether this assignment is a group assignment, for the engine.

    The engine reads GROUP_ASSIGNMENT / SAME_URL_LIMIT from the assignment YAML,
    but the web app cannot reliably edit that file — the assignment directory is
    reset from git, so a local edit would be discarded. Configuration now falls
    back to the environment for these two keys, and this writes them into the
    per-assignment .env the web app already owns.

    SAME_URL_LIMIT is set to the largest group size. The engine flags any URL
    shared by MORE than `same_url_limit` students as a duplicate and refuses to
    test them, so a group of N needs a limit of at least N. Using the biggest
    group means smaller groups pass too.

    Never edits the YAML: if it already sets these keys, the YAML wins.
    """
    env_path = os.path.join(settings.BASE_DIR, assignment.absolute_path, '.env')
    if not os.path.isfile(env_path):
        return

    largest = 0
    for group in assignment.groups.all():
        largest = max(largest, group.memberships.count())

    try:
        with open(env_path) as f:
            lines = f.read().splitlines()
    except OSError:
        return

    # Drop any previous values so re-running does not stack duplicates.
    lines = [ln for ln in lines
             if not ln.startswith('GROUP_ASSIGNMENT=') and not ln.startswith('SAME_URL_LIMIT=')]
    lines.append("GROUP_ASSIGNMENT=%s" % ('true' if has_groups else 'false'))
    lines.append("SAME_URL_LIMIT=%d" % max(largest, 1))

    try:
        with open(env_path, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        os.chmod(env_path, 0o600)
    except OSError:
        pass


def _repo_url_for(student, assignment):
    """The repository a student uses for one assignment ('' if not provisioned)."""
    if assignment is None:
        return ''
    repo = AssignmentRepo.objects.filter(student=student, assignment=assignment).first()
    return repo.repository_url if repo else ''


def _write_grading_row(student, course_id_val, assignment_id_val, repo_url):
    """Upsert one (student, course, assignment) row in the grading engine's DB.

    Best-effort: failures are logged rather than raised, because a silent failure
    here means the daemon sees no students and grading simply never happens."""
    try:
        conn = connect_to_db()
    except Exception:
        return  # grading DB not available — skip silently

    try:
        cur = conn.cursor()

        # Check if record already exists — dedup by email (secondary_id) + course/assignment,
        # NOT by user_id (Django PK). This prevents duplicates when the same student appears
        # under different Django PKs or when the web app re-syncs.
        cur.execute("SELECT user_id FROM users WHERE secondary_id=%s AND course_id=%s AND assignment_id=%s",
                    (student.email, course_id_val, assignment_id_val))
        exists = cur.fetchone()

        if exists:
            # Update existing record — use the MySQL user_id, not the Django PK
            mysql_user_id = exists[0]
            cur.execute(
                "UPDATE users SET repository_url=%s, secondary_id=%s, user_fullname=%s, "
                "new_url=1, changed_state=1, commit_date='0001-01-01 00:00:00' "
                "WHERE user_id=%s AND course_id=%s AND assignment_id=%s",
                (repo_url or '', student.email, student.username,
                 mysql_user_id, course_id_val, assignment_id_val))
        else:
            # Insert new record.
            #
            # The grading engine creates this table via Peewee, which applies its
            # field defaults in Python — so the DDL has NO database-level defaults
            # and most columns are NOT NULL. Every column must therefore be listed
            # here explicitly, with the same defaults the Peewee model uses, or
            # MySQL rejects the row with "Field '<x>' doesn't have a default value".
            #
            # changed_state=1 and new_url=1 mark the row so the daemon picks it up
            # for its first grading run.
            cur.execute(
                "INSERT INTO users (user_id, course_id, assignment_id, repository_url, "
                "secondary_id, user_fullname, url_date, new_url, commit_date, "
                "same_url_flag, plagiarism_to_grade, last_plagiarism_check, last_graded, "
                "changed_state, tester_active, tester_date, force_test, gitlab_issue_iid, "
                "use_webhook, webhook_event, webhook_token) "
                "VALUES (%s, %s, %s, %s, %s, %s, NOW(), 1, '0001-01-01 00:00:00', "
                "0, 0, NOW(), '0001-01-01 00:00:00', 1, 0, '0001-01-01 00:00:00', "
                "0, 0, 0, 0, '')",
                (student.pk, course_id_val, assignment_id_val,
                 repo_url or '', student.email, student.username))
        conn.commit()
    except Exception as exc:
        logging.getLogger('athina_web').error(
            "Failed to sync student %s (course=%s assignment=%s) to the grading database: %s",
            student.email, course_id_val, assignment_id_val, exc)
    finally:
        conn.close()


def _sync_student_to_grading_db(student, assignment=None):
    """Sync a student to the grading engine's MySQL database.

    The engine keys students by (course_id, assignment_id), so there is one row
    per assignment and each carries that assignment's repository URL. Pass an
    explicit `assignment` to sync only that one, or omit it to sync every
    assignment in the student's course.

    This is needed because the grading engine reads from the MySQL 'users' table
    while the web app stores students in its own database. course_id/assignment_id
    come from the assignment YAML (the ground truth), so the DB record always
    matches what the CLI will query."""
    assignments = [assignment] if assignment is not None else list(student.course.assignments.all())
    if not assignments:
        # No assignments exist yet. Write a course-level row so the student is not
        # silently dropped from the grading database (historical behaviour).
        _write_grading_row(student, student.course.pk, student.course.pk, '')
        return

    for each in assignments:
        course_id_val, assignment_id_val = _read_yaml_ids(each)
        _write_grading_row(student, course_id_val, assignment_id_val, _repo_url_for(student, each))


def _write_assignment_env(assignment, user_profile=None):
    """Write a .env file in the assignment directory with git credentials.
    Always uses the COURSE OWNER's credentials, not the logged-in user's.
    Includes GIT_OWNER_ID so credentials can be refreshed when tokens change."""
    # Resolve the course owner's profile (not the current user)
    if user_profile is None:
        try:
            owner_user = User.objects.get(pk=assignment.owner)
            user_profile = owner_user.profile
        except (User.DoesNotExist, UserProfile.DoesNotExist):
            return

    env_path = os.path.join(settings.BASE_DIR, assignment.absolute_path, '.env')
    lines = []
    lines.append("# Auto-generated by Athina Web — do not edit manually.")
    lines.append("# Credentials are refreshed when the faculty member updates their profile.")
    lines.append("GIT_OWNER_ID=%d" % assignment.owner)
    lines.append("GIT_OWNER_USERNAME=%s" % user_profile.user.username)

    # GitLab credentials
    if user_profile.gitlab_enabled and user_profile.gitlab_username:
        lines.append("GIT_PROVIDER=gitlab")
        lines.append("GIT_URL=%s" % user_profile.gitlab_url)
        lines.append("GIT_USERNAME=%s" % user_profile.gitlab_username)
        lines.append("GIT_PASSWORD=%s" % user_profile.gitlab_token)
    # GitHub credentials
    elif user_profile.github_enabled and user_profile.github_username:
        lines.append("GIT_PROVIDER=github")
        lines.append("GIT_URL=github.com")
        lines.append("GIT_USERNAME=%s" % user_profile.github_username)
        lines.append("GIT_PASSWORD=%s" % user_profile.github_token)
    else:
        lines.append("GIT_PROVIDER=")

    # LLM credentials (AI feedback)
    if user_profile.llm_enabled and user_profile.llm_api_key:
        lines.append("LLM_ENDPOINT_URL=%s" % user_profile.llm_endpoint_url)
        lines.append("LLM_API_KEY=%s" % user_profile.llm_api_key)
        lines.append("LLM_MODEL=%s" % user_profile.llm_model)

    # Output mode settings.
    #
    # The engine submits grades to Canvas only if a Canvas token exists; in a
    # db-input course there is no Canvas at all, so writing OUTPUT_METHOD=canvas
    # made every grade fail to upload and be silently dropped. Decide from the
    # assignment's own YAML instead of the vestigial model default.
    lines.append("OUTPUT_METHOD=%s" % _resolve_output_method(assignment))

    try:
        with open(env_path, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        os.chmod(env_path, 0o600)
    except OSError:
        pass


def _read_assignment_yaml(assignment):
    """Return this assignment's athina.yaml as a dict (empty on any problem)."""
    yaml_path = os.path.join(settings.BASE_DIR, assignment.absolute_path, 'athina.yaml')
    if not os.path.exists(yaml_path):
        return {}
    try:
        with open(yaml_path, 'r') as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _resolve_output_method(assignment):
    """Which output adapter the engine should use for this assignment.

    The assignment's own athina.yaml wins, so a faculty member can change the
    output mode by editing the YAML in the repo. A 'canvas' request is only
    honoured when the YAML actually carries a Canvas API token — a db-input
    course has no Canvas connection at all, so submitting there can never work
    and the grade would be silently dropped.

    The web app writes the result into the assignment .env. That matters
    because the engine's .env loading is process-global and only fills in
    variables that are not already set, so an absent OUTPUT_METHOD for one
    assignment could otherwise inherit another assignment's value.
    """
    cfg = _read_assignment_yaml(assignment)
    canvas_usable = bool(cfg.get('auth_token'))

    requested = cfg.get('output_method')
    if requested in ('canvas', 'gitlab_issues'):
        if requested == 'canvas' and not canvas_usable:
            return 'gitlab_issues'
        return requested

    # Nothing explicit: Canvas when it is actually wired up, else GitLab issues.
    return 'canvas' if canvas_usable else 'gitlab_issues'


def _get_owner_gitlab_host(assignment):
    """Return the GitLab host (e.g. 'gitlab.cs.wwu.edu') for the assignment owner."""
    try:
        owner_user = User.objects.get(pk=assignment.owner)
        profile = owner_user.profile
        if profile.gitlab_enabled and profile.gitlab_url:
            return profile.gitlab_url
        if profile.github_enabled:
            return 'github.com'
    except (User.DoesNotExist, UserProfile.DoesNotExist):
        pass
    return 'gitlab.com'


def _repo_url_to_web_url(repo_url):
    """Convert a student's git repo URL to the URL a browser can open.

    E.g. 'https://gitlab.cs.wwu.edu/group/repo.git' → 'https://gitlab.cs.wwu.edu/group/repo'.
    Credentials (which a submitted URL could contain) are stripped, and the
    trailing '.git' is removed so the link lands on the repository's home page.
    """
    if not repo_url:
        return ""
    import re as _re
    cleaned = _re.sub(r'://[^@]+@', '://', repo_url).strip()
    cleaned = _re.sub(r'\.git/?$', '', cleaned)
    # Only ever produce an http(s) link; anything else (git@, file://, javascript:)
    # is not something we want to put behind a click in the UI.
    if not _re.match(r'^https?://', cleaned, _re.IGNORECASE):
        return ""
    return cleaned


def _repo_url_to_issues_url(repo_url, gitlab_host):
    """Convert a student's git repo URL to a GitLab issues page URL.
    E.g. 'https://gitlab.cs.wwu.edu/group/repo.git' → 'https://gitlab.cs.wwu.edu/group/repo/-/issues'
    """
    if not repo_url or not gitlab_host:
        return ""
    import re as _re
    # Strip credentials
    cleaned = _re.sub(r'://[^@]+@', '://', repo_url)
    # Remove trailing .git and slashes
    cleaned = _re.sub(r'\.git/?$', '', cleaned)
    # Extract path after host
    match = _re.search(r'://[^/]+/(.+)', cleaned)
    if not match:
        return ""
    project_path = match.group(1).strip('/')
    if not project_path:
        return ""
    return "https://%s/%s/-/issues" % (gitlab_host, project_path)


def _refresh_env_for_user(user_id):
    """Refresh all .env files for assignments owned by a given user.
    Called when a faculty member updates their git credentials."""
    from athina_web.assignments.models import Assignment
    try:
        user_profile = User.objects.get(pk=user_id).profile
    except (User.DoesNotExist, UserProfile.DoesNotExist):
        return
    for assignment in Assignment.objects.filter(owner=user_id):
        env_path = os.path.join(settings.BASE_DIR, assignment.absolute_path, '.env')
        if os.path.exists(env_path):
            _write_assignment_env(assignment, user_profile)


@login_required()
def index(request):
    assignments(request)


@login_required
def assignments(request):
    """Show assignments grouped by course. Role-based filtering."""
    visible_courses = _get_visible_courses(request.user)
    courses = visible_courses.prefetch_related('assignments', 'students').order_by('name')
    unassigned = Assignment.objects.filter(owner=request.user.id, course__isnull=True).order_by('-active', 'name')
    return render(request, 'assignments/assignments.html', {
        "courses": courses,
        "unassigned": unassigned,
        "can_manage": _user_can_manage_courses(request.user),
    })


@login_required
def assignment_create(request, **kwargs):
    """View for creating and editing model Assignment using Assignment Form"""
    # Creating/editing assignments is admin/faculty work — TAs are read-only.
    if not _user_can_manage_courses(request.user):
        raise Http404

    user_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    active_providers = user_profile.get_active_providers()

    if request.method == "POST":
        assignment_id = kwargs.get('assignment_id', None)
        if assignment_id is not None:
            assignment = get_object_or_404(Assignment, pk=assignment_id)
            if assignment.owner != request.user.id and not request.user.is_superuser:
                raise Http404
            form = AssignmentForm(request.POST, instance=assignment, user=request.user)
        else:
            form = AssignmentForm(request.POST, user=request.user)
        if form.is_valid():
            if form.instance.pk is None:  # New assignment, create folder
                assignment = form.save(commit=False)
                assignment.owner = request.user.id
                assignment.save()
                assignment.absolute_path = "%s/%s/%s" % (settings.MEDIA_ROOT, request.user.id, assignment.name)
                assignment.save()
                if not os.path.exists(assignment.absolute_path):
                    os.makedirs(assignment.absolute_path)
                # Clone the template repo using profile credentials
                if user_profile.gitlab_token or user_profile.github_token:
                    git_username = user_profile.gitlab_username if user_profile.gitlab_enabled else user_profile.github_username
                    git_password = user_profile.gitlab_token if user_profile.gitlab_enabled else user_profile.github_token
                    git_url_source = user_profile.gitlab_url if user_profile.gitlab_enabled else 'github.com'
                    url_matches = re.findall("(.*?)://(.*?)$", assignment.git_source)
                    if url_matches and url_matches[0][0] == 'https' and git_password:
                        clone_url = "%s://%s:%s@%s" % (url_matches[0][0],
                                                           html.escape(git_username),
                                                           html.escape(git_password),
                                                           url_matches[0][1])
                        git.Repo.clone_from(clone_url, assignment.absolute_path)
                    else:
                        git.Repo.clone_from(assignment.git_source, assignment.absolute_path)
                else:
                    git.Repo.clone_from(assignment.git_source, assignment.absolute_path)
                # Write .env with the COURSE OWNER's credentials (not the logged-in user)
                _write_assignment_env(assignment)  # uses course owner's profile
                # Sync all students to the grading DB but do NOT auto-provision repos.
                # Faculty must click 'Provision Repos' manually.
                if assignment.course:
                    for student in Student.objects.filter(course=assignment.course):
                        _sync_student_to_grading_db(student)
            else:  # Existing assignment, move folder
                old_assignment = get_object_or_404(Assignment, pk=assignment_id)
                assignment = form.save(commit=False)
                if old_assignment.name != assignment.name:
                    assignment.absolute_path = "%s/%s/%s" % (settings.MEDIA_ROOT, request.user.id, assignment.name)
                    shutil.move("%s/%s" % (settings.BASE_DIR, old_assignment.absolute_path),
                                "%s/%s" % (settings.BASE_DIR, assignment.absolute_path))
                assignment.save()
                # Update .env with the course owner's current credentials
                _write_assignment_env(assignment)  # uses course owner's profile
            # Persist the due date into the grading engine's database. The form field
            # is not a model field (it lives in assignmentdata, in UTC), so it is
            # converted from the instructor's local time here. Clearing it removes
            # the row, which disables enforcement for this assignment.
            course_id_val, assignment_id_val = _read_yaml_ids(assignment)
            due_local = form.cleaned_data.get('due_date')
            if not _set_assignment_due_date(course_id_val, assignment_id_val,
                                            _local_to_utc(due_local)):
                messages.error(request, "The assignment was saved, but the due date "
                                        "could not be stored in the grading database.")
            os.chmod("%s/%s" % (settings.BASE_DIR, assignment.absolute_path), 0o755)
            return redirect('filemanager:index', inner_path="%s" % assignment.name)
    else:
        assignment_id = kwargs.get('assignment_id', None)
        if assignment_id is not None:
            assignment = get_object_or_404(Assignment, pk=assignment_id)
            if assignment.owner != request.user.id and not request.user.is_superuser:
                raise Http404
            form = AssignmentForm(instance=assignment, user=request.user)
            # Prefill from the grading DB, converted from stored UTC into the timezone
            # active for this request so the datetime-local input shows local time.
            course_id_val, assignment_id_val = _read_yaml_ids(assignment)
            stored_due = _get_assignment_due_date(course_id_val, assignment_id_val)
            if stored_due is not None:
                form.fields['due_date'].initial = timezone.localtime(stored_due)
        else:
            form = AssignmentForm(user=request.user)
            # Prefill the course when arriving from a course page
            # (e.g. "New Assignment" button on the course detail page).
            course_id = request.GET.get('course')
            if course_id:
                try:
                    course = Course.objects.get(pk=int(course_id))
                except (ValueError, TypeError, Course.DoesNotExist):
                    course = None
                if course and _user_can_access_course(request.user, course):
                    form.fields['course'].initial = course.pk
    return render(request, 'assignments/assignment_create.html', {
        'form': form,
        'active_providers': active_providers,
        'has_gitlab': user_profile.gitlab_enabled and bool(user_profile.gitlab_username and user_profile.gitlab_token),
        'has_github': user_profile.github_enabled and bool(user_profile.github_username and user_profile.github_token),
    })


@login_required
def assignment_log(request, assignment_id):
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    has_access = (assignment.owner == request.user.id or request.user.is_superuser)
    if not has_access and assignment.course:
        has_access = _user_can_access_course(request.user, assignment.course)
    if not has_access:
        raise Http404
    else:
        log_path = '%s/%s' % (settings.BASE_DIR, assignment.absolute_path)
        if not os.path.isdir(log_path):
            return render(request, 'assignments/assignment_empty.html',
                          {"assignment": assignment, "message": "Assignment directory not found yet."})
        list_of_files = glob.glob('%s/*.log' % log_path)
        if not list_of_files:
            return render(request, 'assignments/assignment_empty.html',
                          {"assignment": assignment, "message": "No log files available yet. Logs appear after the first grading run."})
        latest_file = os.path.basename(max(list_of_files, key=os.path.getctime))
        return redirect('filemanager:view_file', inner_path="%s%s%s" % (assignment.name, "|", latest_file))


@login_required
def assignment_view(request, assignment_id):
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    # Allow access if owner, admin, or user has course access (TA support)
    has_access = (assignment.owner == request.user.id or request.user.is_superuser)
    if not has_access and assignment.course:
        has_access = _user_can_access_course(request.user, assignment.course)
    if not has_access:
        raise Http404

    # Read course_id/assignment_id from the YAML (ground truth in the git repo)
    course_id_val, assignment_id_val = _read_yaml_ids(assignment)

    # Try to connect to the grading DB — gracefully handle if it's not running
    try:
        conn = connect_to_db()
    except Exception:
        return render(request, 'assignments/assignment_empty.html',
                      {"assignment": assignment,
                       "message": "Cannot connect to the grading database. "
                                  "Make sure the athina grading engine is configured and running."})

    # Build a set of enrolled student emails from the Django course catalog.
    # For db-input-mode courses: always cross-reference — only show MySQL users
    # whose email matches an enrolled Django student (empty set = no students).
    # For canvas-input-mode courses: show all MySQL users (students come from Canvas API).
    enrolled_emails = None
    if assignment.course:
        yaml_path = os.path.join(settings.BASE_DIR, assignment.absolute_path, 'athina.yaml')
        is_db_mode = False
        try:
            with open(yaml_path, 'r') as f:
                cfg = yaml.safe_load(f)
            if cfg and cfg.get('input_method') == 'db':
                is_db_mode = True
        except Exception:
            pass

        if is_db_mode:
            django_students = Student.objects.filter(course=assignment.course)
            enrolled_emails = {s.email.lower() for s in django_students}

    cur = conn.cursor()
    try:
        cur.execute('SELECT variable_value FROM assignmentdata WHERE variable = %s AND '
                    'course_id = %s AND assignment_id = %s',
                    ('plagiarism_report', course_id_val, assignment_id_val,))
        plagiarism_report = cur.fetchone() if cur.fetchone() is not None else '#'

        cur.execute('SELECT user_id, user_fullname, secondary_id, repository_url, commit_date, last_graded,'
                    'last_grade, last_report, moss_max, moss_average, force_test, llm_guidance FROM users WHERE '
                    '`course_id` = %s AND `assignment_id` = %s', (course_id_val, assignment_id_val,))
        users = []
        for user in cur.fetchall():
            # Cross-reference: skip MySQL users not in the Django course roster
            if enrolled_emails is not None:
                user_email = (user[2] or '').lower()
                if user_email not in enrolled_emails:
                    continue

            if user[3] is None and user[6] is None:
                color = "table-danger"
                info = "No repository url submitted"
            elif user[10] == 1:
                color = "table-warning"
                info = "Forced test in progress"
            elif user[4] < user[5]:
                color = "table-success"
                info = "Graded"
            else:
                color = "table-warning"
                info = "Assignment not graded yet or past due date"
            repo_url = user[3] or ''
            issues_url = _repo_url_to_issues_url(repo_url, _get_owner_gitlab_host(assignment)) if assignment.output_method == 'gitlab_issues' else ''
            repo_web_url = _repo_url_to_web_url(repo_url)
            # user[4] is commit_date: the commit the last grading run tested. Show it
            # as "last graded" so an instructor can tell which submission a grade came
            # from (it is refreshed before each test, so it tracks the graded commit).
            # Both columns use a year-1 sentinel to mean "never"; map it to None so
            # the template's {% if %} guard skips it instead of trying to render a
            # date that cannot be shifted out of UTC.
            users.append((user[0], user[1], user[2], color, info, user[6],
                          base64.b64encode(user[7] if user[7] is not None else b"").decode("ascii"),
                          None, None,
                          user[8], user[9], user[11] or '',
                          repo_url, issues_url,
                          _sentinel_to_none(user[4]), _sentinel_to_none(user[5]),
                          repo_web_url))
    finally:
        conn.close()

    if not users:
        # Don't dead-end here: a brand new assignment (or one with no grading rows
        # yet) still needs a way to provision repositories and reach the roster.
        return render(request, 'assignments/assignment_view.html', {
            "users": [], "users_len": 0,
            "assignment": assignment, "plagiarism_report": plagiarism_report,
            "gitlab_project_id": assignment.gitlab_project_id,
            "gitlab_output": assignment.output_method == 'gitlab_issues',
            "gitlab_host": _get_owner_gitlab_host(assignment),
            "can_force": False,
            "can_manage": _user_can_manage_courses(request.user),
            "can_override": _can_override_scores(request.user, assignment),
            "due_date": _get_assignment_due_date(course_id_val, assignment_id_val),
        })

    return render(request, 'assignments/assignment_view.html', {"users": users, "users_len": len(users),
                                                                "assignment": assignment, "plagiarism_report": plagiarism_report,
                                                                "gitlab_project_id": assignment.gitlab_project_id,
                                                                "gitlab_output": assignment.output_method == 'gitlab_issues',
                                                                "gitlab_host": _get_owner_gitlab_host(assignment),
                                                                # Force Rerun writes to the grading DB and is
                                                                # owner/superuser-only; hide it otherwise so TAs
                                                                # are not shown a button that 404s.
                                                                "can_force": (assignment.owner == request.user.id
                                                                              or request.user.is_superuser),
                                                                # Same actions as the home page so this page is
                                                                # self-sufficient rather than a dead end.
                                                                "can_manage": _user_can_manage_courses(request.user),
                                                                "can_override": _can_override_scores(request.user, assignment),
                                                                "due_date": _get_assignment_due_date(course_id_val,
                                                                                                   assignment_id_val)})


@login_required
def assignment_report(request, assignment_id, user_id, report_type):
    """Display a test or plagiarism report as an HTML page."""
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    has_access = (assignment.owner == request.user.id or request.user.is_superuser)
    if not has_access and assignment.course:
        has_access = _user_can_access_course(request.user, assignment.course)
    if not has_access:
        raise Http404

    # Read course_id/assignment_id from YAML (ground truth in repo)
    course_id_val, assignment_id_val = _read_yaml_ids(assignment)

    try:
        conn = connect_to_db()
    except Exception:
        return render(request, 'assignments/assignment_empty.html',
                      {"assignment": assignment, "message": "Cannot connect to grading database."})

    cur = conn.cursor()
    try:
        if report_type == 'test':
            cur.execute('SELECT last_report, user_fullname, llm_guidance FROM users WHERE user_id=%s AND '
                        'course_id=%s AND assignment_id=%s',
                        (user_id, course_id_val, assignment_id_val))
            row = cur.fetchone()
            conn.close()
            if not row or not row[0]:
                return render(request, 'assignments/assignment_empty.html',
                              {"assignment": assignment,
                               "message": "No test report available for this student."})
            report_html = row[0].decode('utf-8', errors='replace') if isinstance(row[0], bytes) else str(row[0])
            # Embed LLM guidance into the test report, right before the closing
            # "Note: Maximum possible grade..." line (if present).
            llm_guidance = row[2] or ''
            if llm_guidance:
                llm_block = (
                    "\nLLM Feedback:\n%s\n"
                    "Note: The LLM can make errors. Please review the feedback critically.\n" % llm_guidance
                )
                note_marker = "Note: Maximum possible grade"
                if note_marker in report_html:
                    report_html = report_html.replace(
                        note_marker, llm_block + note_marker, 1)
                else:
                    report_html = report_html + llm_block
            return render(request, 'assignments/report_view.html', {
                "assignment": assignment,
                "report_type": "Test",
                "student_name": row[1],
                "report_html": report_html,
            })
        elif report_type == 'plagiarism':
            cur.execute('SELECT variable_value FROM assignmentdata WHERE variable = %s AND '
                        'course_id = %s AND assignment_id = %s',
                        ('plagiarism_report', course_id_val, assignment_id_val))
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                from django.utils.http import url_has_allowed_host_and_scheme
                report_url = row[0]
                if url_has_allowed_host_and_scheme(report_url, allowed_hosts={request.get_host()}):
                    return redirect(report_url)
                return HttpResponse("Invalid report URL", status=400)
            return render(request, 'assignments/assignment_empty.html',
                          {"assignment": assignment,
                           "message": "No plagiarism report available for this assignment."})
        else:
            conn.close()
            raise Http404
    except Exception:
        conn.close()
        raise


@login_required
def assignment_guidance(request, assignment_id, user_id):
    """Return the LLM guidance for a student as JSON (for the AI Guidance modal)."""
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    has_access = (assignment.owner == request.user.id or request.user.is_superuser)
    if not has_access and assignment.course:
        has_access = _user_can_access_course(request.user, assignment.course)
    if not has_access:
        raise Http404

    course_id_val, assignment_id_val = _read_yaml_ids(assignment)
    try:
        conn = connect_to_db()
    except Exception:
        return JsonResponse({"guidance": "", "error": "Cannot connect to grading database."})

    try:
        cur = conn.cursor()
        cur.execute('SELECT llm_guidance FROM users WHERE user_id=%s AND '
                    'course_id=%s AND assignment_id=%s',
                    (user_id, course_id_val, assignment_id_val))
        row = cur.fetchone()
        conn.close()
    except Exception:
        conn.close()
        return JsonResponse({"guidance": "", "error": "Database error."})

    guidance = row[0] if row and row[0] else ""
    return JsonResponse({"guidance": guidance})


def _sentinel_to_none(value):
    """Map the grading engine's year-1 "never" timestamp to ``None``.

    The engine stores ``0001-01-01 00:00:00`` in ``commit_date`` / ``last_graded``
    for rows that have never been graded (and the web app writes it too when it
    provisions a row). Rendering it would overflow, because shifting year 1 UTC
    into a zone west of UTC lands in year 0, which datetime cannot represent.
    """
    if isinstance(value, datetime.datetime) and value.year < 2:
        return None
    return value


def _get_assignment_due_date(course_id, assignment_id):
    """Return the due date stored by the grading engine, or None.

    The engine keeps it in its own `assignmentdata` table as a naive UTC string
    (see cli.core_iteration), so this returns a UTC-aware datetime for display.
    """
    try:
        conn = connect_to_db()
    except Exception:
        return None
    try:
        cur = conn.cursor()
        cur.execute("SELECT variable_value FROM assignmentdata WHERE variable=%s "
                    "AND course_id=%s AND assignment_id=%s",
                    ('due_date', course_id, assignment_id))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    try:
        parsed = dateutil.parser.parse(row[0])
    except (ValueError, TypeError):
        return None
    # The engine's "no deadline" representation is a year-1 sentinel; rendering
    # it in a timezone west of UTC overflows (see accounts.templatetags
    # .timezone_tags). Surface it as "no due date" rather than a 500.
    if parsed.year < 2:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime.timezone.utc)
    return parsed


def _set_assignment_due_date(course_id, assignment_id, value):
    """Store (or clear, when value is None) the assignment's due date.

    `value` must already be UTC-aware. The engine parses this string with no
    timezone attached and runs in UTC, so it is stored as naive UTC.
    """
    try:
        conn = connect_to_db()
    except Exception:
        return False
    try:
        cur = conn.cursor()
        if value is None:
            cur.execute("DELETE FROM assignmentdata WHERE variable=%s "
                        "AND course_id=%s AND assignment_id=%s",
                        ('due_date', course_id, assignment_id))
        else:
            stored = timezone.make_naive(value.astimezone(datetime.timezone.utc),
                                         datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            cur.execute("INSERT INTO assignmentdata (course_id, assignment_id, variable, variable_value) "
                        "VALUES (%s, %s, %s, %s) "
                        "ON DUPLICATE KEY UPDATE variable_value = VALUES(variable_value)",
                        (course_id, assignment_id, 'due_date', stored))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def _local_to_utc(moment):
    """Convert a naive datetime entered in the user's timezone to UTC.

    The form's datetime-local input carries no zone. The instructor typed a wall
    clock time, so it is interpreted in the timezone active for this request (the
    profile setting, or the browser zone when 'auto' -- see accounts.middleware).
    """
    if moment is None:
        return None
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, timezone.get_current_timezone())
    return moment.astimezone(datetime.timezone.utc)


def connect_to_db():
    db_details = db_info()
    if not db_details.athina_mysql_host:
        raise ConnectionRefusedError(
            "ATHINA_MYSQL_HOST is not set. Configure the grading database connection "
            "in your environment variables or settings_secret.py."
        )
    return pymysql.connect(host=db_details.athina_mysql_host, user=db_details.athina_mysql_username,
                           password=db_details.athina_mysql_password, port=int(db_details.athina_mysql_port),
                           db="athina")


@login_required
def assignment_delete(request, assignment_id):
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if assignment.owner != request.user.id and not request.user.is_superuser:
        raise Http404
    try:
        shutil.rmtree("%s/%s" % (settings.BASE_DIR, assignment.absolute_path))
    except FileNotFoundError:  # this error wont affect functionality
        pass
    assignment.delete()
    return redirect('assignments:assignments')


@login_required
def get_course_assignment_id(request, absolute_path):
    with open('%s/%s/athina.yaml' % (settings.BASE_DIR, absolute_path), 'r') as stream:
        yaml_dict = yaml.load(stream, Loader=yaml.SafeLoader)
    return yaml_dict['course_id'], yaml_dict['assignment_id']


@login_required
def assignment_force(request, assignment_id, user_id):
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if assignment.owner != request.user.id and not request.user.is_superuser:
        raise Http404
    else:
        # Read course_id/assignment_id from YAML (ground truth in repo)
        course_id_val, assignment_id_val = _read_yaml_ids(assignment)

        try:
            conn = connect_to_db()
        except (ConnectionRefusedError, Exception):
            return redirect('assignments:assignment_view', assignment_id=assignment.pk)

        cur = conn.cursor()
        # The URL passes the MySQL user_id directly (user.0 in the template), so update
        # the MySQL users table by user_id.  For group assignments, also force-test any
        # other members sharing the same repository_url.
        cur.execute("SELECT repository_url FROM users WHERE user_id=%s AND course_id=%s AND assignment_id=%s LIMIT 1",
                    (user_id, course_id_val, assignment_id_val,))
        result = cur.fetchone()
        # MySQL user ids actually touched; these are the students to wake the daemon for.
        forced_user_ids = [user_id]
        if result and result[0]:
            cur.execute("SELECT user_id FROM users WHERE course_id=%s AND assignment_id=%s AND repository_url=%s",
                        (course_id_val, assignment_id_val, result[0],))
            shared = [row[0] for row in cur.fetchall() or []]
            if shared:
                forced_user_ids = shared
            cur.execute("UPDATE users SET force_test=1, changed_state=1 WHERE course_id=%s AND assignment_id=%s AND repository_url=%s",
                        (course_id_val, assignment_id_val, result[0],))
        else:
            cur.execute("UPDATE users SET force_test=1, changed_state=1 WHERE course_id=%s AND assignment_id=%s AND user_id=%s",
                        (course_id_val, assignment_id_val, user_id,))
        conn.commit()
        conn.close()
        # Wake the grading daemon now instead of waiting for its next poll, so the
        # forced run starts immediately — and only for the student(s) affected.
        _trigger_daemon(course_id_val, assignment_id_val, forced_user_ids, assignment)
        return redirect('assignments:assignment_view', assignment_id=assignment.pk)


def _trigger_daemon(course_id_val=None, assignment_id_val=None, user_ids=None,
                    assignment=None):
    """Ask the grading daemon to grade specific students now.

    The daemon lives in a different container and cannot be reached directly, so
    signal it through the shared athina_files volume (MEDIA_ROOT, resolved against
    BASE_DIR exactly as filemanager.utils does).

    A single event is written per assignment, naming the first affected student.
    That is enough even for groups: the engine pulls that repository and submits
    the grade to every student who shares it, so one job covers the whole group.
    Writing one event per member would start several jobs for the same repository.

    When the affected students cannot be identified (no ids) a full-pass marker is
    written instead, which the daemon treats as "run the whole safety-net pass".
    Best-effort either way: if the write fails the daemon still picks the change up
    on its normal poll.
    """
    try:
        root = os.path.normpath(os.path.join(settings.BASE_DIR, settings.MEDIA_ROOT))
        os.makedirs(root, exist_ok=True)
        if course_id_val is not None and assignment_id_val is not None and user_ids:
            directory = ''
            if assignment is not None:
                # Match the form the web API reports ('<dir>/'): the engine's
                # find_yaml appends '*.yaml', so the separator matters.
                directory = os.path.normpath(os.path.join(settings.BASE_DIR,
                                                          assignment.absolute_path)) + os.sep
            write_student_trigger(course_id_val, assignment_id_val, min(user_ids),
                                  assignment_dir=directory, root=root)
        else:
            write_full_pass_trigger(root=root)
    except Exception:
        # Best-effort; the daemon's next poll still finds the change.
        pass


@csrf_exempt
def push_event(request):
    if request.headers.get('X-Gitlab-Event', '') == 'Push Hook':
        try:
            json_body = json.loads(request.body)
            student_git_url = json_body['project']['git_http_url']
        except KeyError:
            return HttpResponse('ok')
        webhook_token = request.headers.get('X-Gitlab-Token', '')
        conn = connect_to_db()
        cur = conn.cursor()
        # Find the places that this git url has been used and update that it has been changed.
        # The rows we just flagged are exactly the students whose push this is, so read
        # them back and wake the daemon for those students only.
        cur.execute('SELECT user_id, course_id, assignment_id FROM users '
                    'WHERE repository_url = %s AND webhook_token = %s',
                    (student_git_url, webhook_token,))
        rows = cur.fetchall() or []
        cur.execute('UPDATE users SET webhook_event=1 WHERE repository_url = %s AND webhook_token = %s',
                    (student_git_url, webhook_token,))
        conn.commit()
        conn.close()

        if rows:
            # Group assignments share a repository, so this is normally one
            # (course, assignment) pair with one or more students.
            by_assignment = {}
            for user_id, course_id, assignment_id in rows:
                by_assignment.setdefault((course_id, assignment_id), []).append(user_id)
            for (course_id, assignment_id), user_ids in by_assignment.items():
                _trigger_daemon(course_id, assignment_id, user_ids,
                                _assignment_from_ids(course_id, assignment_id))
        else:
            # The push did not match a known repository/token. Do not start a
            # course-wide pass for it — there is nothing to grade.
            logging.getLogger('athina_web').info(
                "Webhook for %s matched no student repository; ignoring.", student_git_url)
    return HttpResponse('ok')


def _assignment_from_ids(course_id_val, assignment_id_val):
    """Best-effort lookup of the Assignment whose YAML ids match.

    Used to tell the daemon which directory to grade, so a triggered run starts
    even if the daemon has not refreshed its assignment list yet. In db input mode
    assignment_id IS the Django PK, so the direct lookup normally hits.
    """
    try:
        direct = Assignment.objects.filter(pk=assignment_id_val).first()
        if direct is not None:
            yaml_course, yaml_assignment = _read_yaml_ids(direct)
            if yaml_course == course_id_val and yaml_assignment == assignment_id_val:
                return direct
        for assignment in Assignment.objects.filter(active=True):
            try:
                yaml_course, yaml_assignment = _read_yaml_ids(assignment)
            except Exception:
                continue
            if yaml_course == course_id_val and yaml_assignment == assignment_id_val:
                return assignment
    except Exception:
        pass
    return None


class APIView(generics.ListAPIView):
    """Read-only list of active assignments, consumed by the grading daemon.

    This was a ListCreateAPIView with no permission classes and no
    authentication, so an anonymous POST created an Assignment record. It is now
    read-only: `athina-cli` only ever GETs this endpoint (it is how the daemon
    discovers what to grade), so nothing legitimate loses access.

    NOTE: the endpoint is still unauthenticated, and each response runs a git
    fetch + hard reset per assignment (see AssignmentListSerializer.get_directory)
    and exposes absolute server paths. Requiring a shared token here means
    teaching the daemon to send one too — worth doing, but it needs a coordinated
    change in athina/cli.py and the deployment config, so it is deliberately left
    as-is for now rather than half-done.
    """
    queryset = Assignment.objects.filter(active=True)
    serializer_class = AssignmentListSerializer
    http_method_names = ['get', 'head', 'options']


# =========================================================================
#  Course management views
# =========================================================================

@login_required
def course_list(request):
    """List the courses the current user can access (admin: all, faculty: own,
    TA: those owned by the faculty they assist)."""
    return render(request, 'assignments/course_list.html', {
        "courses": _get_visible_courses(request.user).order_by('name'),
        "can_manage": _user_can_manage_courses(request.user),
    })


@login_required
def course_create(request, **kwargs):
    # Creating/editing courses is admin/faculty work — TAs are read-only.
    if not _user_can_manage_courses(request.user):
        raise Http404

    course_id = kwargs.get('course_id', None)
    if request.method == "POST":
        if course_id is not None:
            course = get_object_or_404(Course, pk=course_id)
            # Only owner or admin can edit
            if course.owner != request.user.id and not request.user.is_superuser:
                try:
                    if request.user.profile.role != UserProfile.ROLE_ADMIN:
                        raise Http404
                except UserProfile.DoesNotExist:
                    raise Http404
            form = CourseForm(request.POST, instance=course)
        else:
            form = CourseForm(request.POST)
        if form.is_valid():
            c = form.save(commit=False)
            c.owner = request.user.id
            c.save()
            return redirect('assignments:course_detail', course_id=c.pk)
    else:
        if course_id is not None:
            course = get_object_or_404(Course, pk=course_id)
            # Only owner or admin can edit
            if course.owner != request.user.id and not request.user.is_superuser:
                try:
                    if request.user.profile.role != UserProfile.ROLE_ADMIN:
                        raise Http404
                except UserProfile.DoesNotExist:
                    raise Http404
            form = CourseForm(instance=course)
        else:
            form = CourseForm()
    return render(request, 'assignments/course_create.html', {'form': form})


@login_required
def course_detail(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_access_course(request.user, course):
        raise Http404
    assignments_list = course.assignments.all().order_by('-active', 'name')
    students = course.students.all().order_by('email')

    # Check if this course uses Canvas or not (by inspecting assignment YAML configs)
    has_canvas = False
    for assignment in assignments_list:
        try:
            yaml_path = os.path.join(settings.BASE_DIR, assignment.absolute_path, 'athina.yaml')
            if os.path.exists(yaml_path):
                with open(yaml_path, 'r') as f:
                    cfg = yaml.safe_load(f)
                if cfg and cfg.get('auth_token', ''):
                    has_canvas = True
                    break
        except Exception:
            pass

    return render(request, 'assignments/course_detail.html', {
        "course": course,
        "assignments": assignments_list,
        "students": students,
        "has_canvas": has_canvas,
        # can_manage gates course/assignment actions; can_manage_students gates
        # the student roster actions (TAs may view students but not manage them).
        "can_manage": _user_can_manage_courses(request.user),
        "can_manage_students": _user_can_manage_students(request.user, course),
    })


@login_required
def course_delete(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    # Only owner or admin can delete
    if course.owner != request.user.id and not request.user.is_superuser:
        try:
            if request.user.profile.role != UserProfile.ROLE_ADMIN:
                raise Http404
        except UserProfile.DoesNotExist:
            raise Http404
    # Unassign assignments (don't delete them)
    course.assignments.update(course=None)
    course.delete()
    return redirect('assignments:course_list')

# =========================================================================
#  Student management views
# =========================================================================

@login_required
def student_list(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_access_course(request.user, course):
        raise Http404
    students = course.students.all().order_by('email')
    return render(request, 'assignments/student_list.html', {
        "course": course, "students": students,
        # Repositories live on assignments, so link the instructor there for
        # provisioning rather than offering a course-wide repo action here.
        "assignments": course.assignments.all().order_by('name'),
        "has_assignments": course.assignments.exists(),
        "can_manage": _user_can_manage_students(request.user, course),
    })


@login_required
def provision_students(request, course_id, assignment_id=None):
    """Start provisioning repositories in the background.

    This used to do all the work inline. Each student costs several GitLab API
    calls and each of those has a 1s sleep (athina/url.request_url), so 30
    students is well over a minute of HTTP. Gunicorn's default 30s worker timeout
    then killed the worker mid-request: the browser hung and finally showed a 500
    (`InterfaceError` from the aborted commit), and the work half-finished.

    It now kicks off a thread and redirects to a progress page, mirroring the
    student bulk import. Re-running is still safe — existing repos are adopted
    rather than recreated."""
    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_manage_students(request.user, course):
        raise Http404

    if assignment_id is not None:
        assignment = get_object_or_404(Assignment, pk=assignment_id, course=course)
        assignments = [assignment]
    else:
        # Backwards-compatible course-level entry point. Provisioning a course
        # means "every assignment", which is what instructors expect when they
        # click it from the course roster.
        assignments = list(course.assignments.all())

    if not assignments:
        messages.warning(request, "This course has no assignments yet. Create an "
                                  "assignment before provisioning repositories.")
        return redirect('assignments:student_list', course_id=course.pk)

    # Refuse to start a second run for the same target: two threads racing would
    # both try to create the same GitLab projects.
    key = _provision_key(course.pk, assignment_id)
    state = _provision_progress.get(key)
    if state and state.get('status') == 'running':
        messages.info(request, "Provisioning is already running for this "
                               "assignment. Showing its progress.")
        return _provision_redirect(course, assignment_id)

    _provision_progress[key] = {
        'status': 'running', 'total': 0, 'current': 0,
        'current_item': 'Starting…', 'created': 0, 'adopted': 0,
        'synced': 0, 'notified': 0, 'errors': [],
        'updated_at': timezone.now(),
    }
    thread = threading.Thread(
        target=_run_provision,
        args=(course.pk, assignment_id, request.user.id),
        daemon=True,
    )
    thread.start()
    return _provision_redirect(course, assignment_id)


def _provision_key(course_id, assignment_id):
    """Separate progress per course-level and per-assignment runs."""
    return "course-%s" % course_id if assignment_id is None else "assignment-%s" % assignment_id


def _provision_redirect(course, assignment_id):
    """Redirect to the progress page for this run's scope."""
    if assignment_id is not None:
        return redirect('assignments:provision_progress',
                        assignment_id=assignment_id)
    return redirect('assignments:provision_progress_course', course_id=course.pk)


def _run_provision(course_id, assignment_id, user_id):
    """Background thread: provision repositories and sync to the grading DB.

    Writes progress into `_provision_progress` so the polling page can report
    what is happening. Every step is best-effort: one student's failure is
    recorded and the run continues, so a single bad GitLab account cannot abandon
    the rest of the roster.
    """
    key = _provision_key(course_id, assignment_id)
    logger = logging.getLogger('athina_web')
    state = _provision_progress.setdefault(key, {})

    def report(**fields):
        # Stamp every write so the status endpoint can detect a stalled run.
        fields['updated_at'] = timezone.now()
        state.update(fields)

    try:
        course = Course.objects.get(pk=course_id)
        assignments = ([Assignment.objects.get(pk=assignment_id)] if assignment_id is not None
                       else list(course.assignments.all()))
        students = list(Student.objects.filter(course=course))

        # Total units of work, so the progress bar is meaningful. A group
        # assignment costs one unit per group (or per student if ungrouped); an
        # individual assignment costs one per student per assignment.
        units = []
        for assignment in assignments:
            if assignment.is_group_assignment and assignment.groups.exists():
                units.append(('group', assignment, None))
            else:
                for student in students:
                    units.append(('student', assignment, student))
        report(total=len(units), current=0)

        created = adopted = synced = notified = 0
        errors = []

        # Ensure the faculty owner and assigned TAs are members of the course
        # group. Covers groups created before this feature existed, and is cheap
        # (idempotent) when they are already members.
        report(current_item='Checking course GitLab group…')
        gitlab_url, gitlab_token = _get_gitlab_config(course)
        if gitlab_url and gitlab_token:
            group_id, _group_name, _created = _ensure_course_group(course, gitlab_url, gitlab_token)
            if group_id is not None:
                _sync_course_group_members(course, gitlab_url, gitlab_token, group_id)

        # Group-level work first, then per-student, so the .env settings written
        # below reflect the groups that exist.
        for assignment in assignments:
            groups = list(assignment.groups.all()) if assignment.is_group_assignment else []
            _write_group_settings(assignment, has_groups=bool(groups))

        done = 0
        for kind, assignment, student in units:
            if kind == 'group':
                for group in assignment.groups.all():
                    done += 1
                    report(current=done, current_item="%s — group %s" % (assignment.name, group.name))
                    member_ids = list(group.memberships.values_list('student_id', flat=True))
                    already = (AssignmentRepo.objects
                               .filter(assignment=assignment, student_id__in=member_ids)
                               .exclude(repository_url='')
                               .exists())
                    if already:
                        adopted += 1
                    # Always re-run, even when the repo is recorded. It is
                    # idempotent and it repairs membership: a group provisioned
                    # before Maintainer was granted leaves every member unable to
                    # push to the shared protected master branch.
                    result = _provision_group_gitlab(course, assignment, group)
                    if result is True:
                        if not already:
                            created += 1
                        notified += 1  # group provisioning notifies every member
                    elif result:
                        errors.append("%s (%s): %s" % (group.name, assignment.name, result))
                # Sync every member's row for this group assignment.
                for member_id in assignment.groups.values_list('memberships__student_id', flat=True):
                    member = next((s for s in students if s.pk == member_id), None)
                    if member is not None:
                        _sync_student_to_grading_db(member, assignment=assignment)
                        synced += 1
                continue

            done += 1
            report(current=done, current_item="%s — %s" % (assignment.name, student.email))
            repo, _was_created = AssignmentRepo.objects.get_or_create(
                student=student, assignment=assignment)
            was_notified = repo.notified_at

            if not repo.repository_url:
                result = _provision_student_gitlab(course, student, assignment=assignment)
                if result is True:
                    created += 1
                    repo.refresh_from_db()
                    if repo.notified_at and not was_notified:
                        notified += 1
                elif result:
                    errors.append("%s (%s): %s" % (student.email, assignment.name, result))
            else:
                # The repo is already recorded. Re-run the provisioning step anyway
                # rather than only re-notifying: it is idempotent, and it is what
                # repairs a repository whose membership is wrong. Students created
                # before Maintainer was granted are stuck as Developer, which lets
                # them clone their repo but not push to its protected master —
                # exactly the thing the instructor clicked this button to fix.
                adopted += 1
                result = _provision_student_gitlab(course, student, assignment=assignment)
                if result not in (True, None):
                    errors.append("%s (%s): %s" % (student.email, assignment.name, result))
                # Still notify the student if we never did.
                result = _notify_student_repo(course, student, assignment=assignment)
                if result is True:
                    notified += 1
                elif result:
                    errors.append("%s (%s): %s" % (student.email, assignment.name, result))

            _sync_student_to_grading_db(student, assignment=assignment)
            synced += 1
            report(created=created, adopted=adopted, synced=synced,
                   notified=notified, errors=list(errors))

        label = (assignments[0].name if len(assignments) == 1
                 else "%d assignments" % len(assignments))
        summary = "Created %d, adopted %d, synced %d row(s), notified %d" % (
            created, adopted, synced, notified)
        report(current=len(units), status='done', current_item='Finished',
               created=created, adopted=adopted, synced=synced,
               notified=notified, errors=list(errors), summary=summary, label=label,
               started_by=user_id)
        logger.info("Provisioning complete for %s/%s: %s",
                    course_id, assignment_id, summary)
    except Exception as exc:
        # A crash in the thread must still surface to the operator, or the page
        # polls "running" forever.
        logger.exception("Provisioning failed for course=%s assignment=%s", course_id, assignment_id)
        report(status='error', current_item='Failed', error=str(exc))


@login_required
def provision_progress(request, assignment_id):
    """Live progress for provisioning one assignment's repositories."""
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    _assignment_access_or_404(request.user, assignment)
    return render(request, 'assignments/provision_progress.html', {
        "assignment": assignment,
        "course": assignment.course,
        "status_url": reverse('assignments:provision_progress_api',
                              kwargs={'assignment_id': assignment.pk}),
        "done_url": reverse('assignments:assignment_students',
                            kwargs={'assignment_id': assignment.pk}),
        "title": "Provisioning repositories for %s" % assignment.name,
        "can_manage_students": _user_can_manage_students(request.user, assignment.course),
    })


@login_required
def provision_progress_course(request, course_id):
    """Live progress for provisioning every assignment in a course."""
    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_access_course(request.user, course):
        raise Http404
    return render(request, 'assignments/provision_progress.html', {
        "assignment": None,
        "course": course,
        "status_url": reverse('assignments:provision_progress_course_api',
                              kwargs={'course_id': course.pk}),
        "done_url": reverse('assignments:student_list', kwargs={'course_id': course.pk}),
        "title": "Provisioning repositories for %s" % course.name,
        "can_manage_students": _user_can_manage_students(request.user, course),
    })


def _provision_status_payload(key, redirect_url):
    """JSON body the progress page polls."""
    state = _provision_progress.get(key)
    if not state:
        return {"status": "idle", "total": 0, "current": 0, "done": True,
                "redirect": redirect_url}
    total = state.get('total', 0)
    current = state.get('current', 0)
    percent = int((current / total) * 100) if total else 0
    status = state.get('status', 'running')

    # A run that stops making progress would otherwise leave the page polling
    # forever. If nothing has been written for a long while, report it as stalled
    # so the operator sees something actionable instead of a frozen bar.
    if status == 'running':
        last = state.get('updated_at')
        if last is not None and (timezone.now() - last).total_seconds() > _PROVISION_STALL_SECONDS:
            status = 'stalled'

    return {
        "status": status,
        "total": total,
        "current": current,
        "percent": percent,
        "current_item": state.get('current_item', ''),
        "created": state.get('created', 0),
        "adopted": state.get('adopted', 0),
        "synced": state.get('synced', 0),
        "notified": state.get('notified', 0),
        "errors": state.get('errors', []),
        "error": state.get('error', ''),
        "summary": state.get('summary', ''),
        # 'stalled' counts as done so the UI stops polling and the operator can
        # decide whether to retry (provisioning is idempotent).
        "done": status in ('done', 'error', 'stalled'),
        "redirect": redirect_url,
    }


@login_required
def provision_progress_api(request, assignment_id):
    """JSON status for one assignment's provisioning run."""
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    _assignment_access_or_404(request.user, assignment)
    return JsonResponse(_provision_status_payload(
        _provision_key(assignment.course_id, assignment.pk),
        reverse('assignments:assignment_students', kwargs={'assignment_id': assignment.pk})))


@login_required
def provision_progress_course_api(request, course_id):
    """JSON status for a whole-course provisioning run."""
    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_access_course(request.user, course):
        raise Http404
    return JsonResponse(_provision_status_payload(
        _provision_key(course.pk, None),
        reverse('assignments:student_list', kwargs={'course_id': course.pk})))


@login_required
def assignment_gitlab_check(request, assignment_id):
    """Report, per student, whether their GitLab account can be found — and why.

    Provisioning aborts for any student whose account cannot be resolved, and the
    only feedback is one line of error text after the run. When that happens for
    many students at once it is hard to tell an instructor with no accounts from
    a token that cannot see them, or from usernames that differ from the email
    prefix.

    This does the same lookups without creating anything, so the reason is
    visible up front.
    """
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if assignment.course_id is None:
        raise Http404
    course = assignment.course
    if not _user_can_manage_students(request.user, course):
        raise Http404

    gitlab_url, gitlab_token = _get_gitlab_config(course)
    if not gitlab_url or not gitlab_token:
        messages.error(request, "GitLab is not configured for this course "
                                "(missing URL or token), so accounts cannot be checked.")
        return redirect('assignments:assignment_students', assignment_id=assignment.pk)

    headers = {"PRIVATE-TOKEN": gitlab_token}
    api_base = "https://%s/api/v4" % gitlab_url

    rows = []
    counts = {'found': 0, 'not_found': 0, 'error': 0}
    for student in Student.objects.filter(course=course).order_by('email'):
        handle = (student.gitlab_username or student.username or '').strip()
        user, outcome, detail = _gitlab_lookup_user(
            api_base, headers, handle=handle, email=student.email)
        counts[outcome] = counts.get(outcome, 0) + 1
        rows.append({
            'student': student,
            'handle': handle,
            'outcome': outcome,
            'detail': detail,
            'matched': (user or {}).get('username', ''),
            'matched_name': (user or {}).get('name', ''),
        })

    return render(request, 'assignments/gitlab_check.html', {
        "assignment": assignment,
        "course": course,
        "gitlab_url": gitlab_url,
        "rows": rows,
        "counts": counts,
    })


@login_required
def assignment_gitlab_check_fix(request, assignment_id):
    """Adopt the GitLab account that the check matched for each student.

    Only writes a handle that was resolved unambiguously, so a fuzzy search can
    never silently bind a student to somebody else's account.
    """
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if assignment.course_id is None:
        raise Http404
    course = assignment.course
    if not _user_can_manage_students(request.user, course):
        raise Http404
    if request.method != "POST":
        return redirect('assignments:assignment_gitlab_check', assignment_id=assignment.pk)

    gitlab_url, gitlab_token = _get_gitlab_config(course)
    if not gitlab_url or not gitlab_token:
        messages.error(request, "GitLab is not configured for this course.")
        return redirect('assignments:assignment_students', assignment_id=assignment.pk)

    headers = {"PRIVATE-TOKEN": gitlab_token}
    api_base = "https://%s/api/v4" % gitlab_url

    updated = 0
    for student in Student.objects.filter(course=course):
        handle = (student.gitlab_username or student.username or '').strip()
        user, outcome, _detail = _gitlab_lookup_user(
            api_base, headers, handle=handle, email=student.email)
        if outcome != GITLAB_LOOKUP_FOUND:
            continue
        resolved = user.get('username')
        if resolved and student.gitlab_username != resolved:
            student.gitlab_username = resolved
            student.save(update_fields=['gitlab_username'])
            logging.getLogger('athina_web').info(
                "Adopted GitLab account '%s' for student %s", resolved, student.email)
            updated += 1

    if updated:
        messages.success(request, "Updated the GitLab username for %d student(s)." % updated)
    else:
        messages.info(request, "No student usernames needed updating.")
    return redirect('assignments:assignment_gitlab_check', assignment_id=assignment.pk)


@login_required
def assignment_gitlab_signup_email(request, assignment_id):
    """Compose and send "please create a GitLab account" to unresolvable students.

    Only students whose account could not be resolved are targeted — those are
    the ones whose repository cannot be created. Re-checked on send rather than
    trusting the previous result, so a student who has since registered is no
    longer emailed.
    """
    from . import gitlab_signup_email

    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if assignment.course_id is None:
        raise Http404
    course = assignment.course
    if not _user_can_manage_students(request.user, course):
        raise Http404

    profile = _get_owner_profile(assignment)
    gitlab_url, gitlab_token = _get_gitlab_config(course)
    if not gitlab_url or not gitlab_token:
        messages.error(request, "GitLab is not configured for this course, so "
                                "accounts cannot be checked or students emailed.")
        return redirect('assignments:assignment_students', assignment_id=assignment.pk)

    headers = {"PRIVATE-TOKEN": gitlab_token}
    api_base = "https://%s/api/v4" % gitlab_url
    site_url = request.build_absolute_uri('/').rstrip('/')
    faculty_name = request.user.get_full_name() or request.user.username
    courses = [course.name]

    # Who cannot be resolved right now.
    unresolved = []
    for student in Student.objects.filter(course=course).order_by('email'):
        handle = (student.gitlab_username or student.username or '').strip()
        _user, outcome, detail = _gitlab_lookup_user(
            api_base, headers, handle=handle, email=student.email)
        if outcome != GITLAB_LOOKUP_FOUND:
            unresolved.append({
                'student': student,
                'handle': handle,
                'outcome': outcome,
                'detail': detail,
            })

    if request.method == "POST":
        if not profile or not profile.notification_api_key:
            messages.error(request, "No Resend API key is configured. Add one on "
                                    "your profile before sending.")
            return redirect('assignments:assignment_gitlab_signup_email',
                            assignment_id=assignment.pk)

        subject_template = request.POST.get('subject', '')
        body_template = request.POST.get('body', '')
        if request.POST.get('save_as_default') == 'on' and profile:
            profile.signup_email_subject = subject_template
            profile.signup_email_body = body_template
            profile.save(update_fields=['signup_email_subject', 'signup_email_body'])
            messages.success(request, "Saved as your default message.")

        # Which of the unresolved students to email. Defaults to all of them.
        selected = set(request.POST.getlist('students')) or {
            str(row['student'].pk) for row in unresolved if row['outcome'] != GITLAB_LOOKUP_ERROR}

        sent = 0
        failed = []
        for row in unresolved:
            student = row['student']
            if str(student.pk) not in selected:
                continue
            if not student.email:
                failed.append("%s (no email address)" % student.username)
                continue

            context = gitlab_signup_email.build_context(
                student=student, gitlab_url=gitlab_url, faculty_name=faculty_name,
                site_url=site_url, courses=courses)
            subject, body = gitlab_signup_email.compose(
                subject_template, body_template, context)
            message_id = send_email_safe(
                api_key=profile.notification_api_key,
                to=student.email,
                subject=subject,
                text=body,
                from_email=profile.notification_from_email or None,
                reply_to=profile.notification_reply_to or None,
            )
            if message_id is None:
                failed.append("%s (delivery failed)" % student.email)
            else:
                sent += 1
                logging.getLogger('athina_web').info(
                    "Sent GitLab signup request to %s by '%s' (resend_id=%s)",
                    student.email, request.user.username, message_id)

        if sent:
            messages.success(request, "Sent the signup request to %d student(s)." % sent)
        if failed:
            messages.error(request, "Could not email: " + ", ".join(failed))
        return redirect('assignments:assignment_gitlab_check', assignment_id=assignment.pk)

    subject_template = (profile.signup_email_subject if profile else '') or \
        gitlab_signup_email.DEFAULT_SUBJECT
    body_template = (profile.signup_email_body if profile else '') or \
        gitlab_signup_email.DEFAULT_BODY

    preview_context = gitlab_signup_email.sample_context()
    # Real values, so the link and signature can be checked before sending.
    preview_context['gitlab_url'] = gitlab_url
    preview_context['faculty_name'] = faculty_name
    preview_context['site_url'] = site_url
    preview_subject, preview_body = gitlab_signup_email.compose(
        subject_template, body_template, preview_context)

    return render(request, 'assignments/gitlab_signup_email.html', {
        "assignment": assignment,
        "course": course,
        "gitlab_url": gitlab_url,
        "unresolved": unresolved,
        "subject_template": subject_template,
        "body_template": body_template,
        "preview_subject": preview_subject,
        "preview_body": preview_body,
        "placeholders": gitlab_signup_email.PLACEHOLDERS,
        "has_email_config": bool(profile and profile.notification_api_key),
        "email_defaults": {
            "default_subject": gitlab_signup_email.DEFAULT_SUBJECT,
            "default_body": gitlab_signup_email.DEFAULT_BODY,
            "saved_subject": (profile.signup_email_subject if profile else '') or '',
            "saved_body": (profile.signup_email_body if profile else '') or '',
        },
    })


def _get_owner_profile(assignment):
    """The profile that owns this assignment's credentials (course owner)."""
    try:
        return User.objects.get(pk=assignment.owner).profile
    except (User.DoesNotExist, UserProfile.DoesNotExist):
        return None


@login_required
def notify_students(request, course_id, assignment_id=None):
    """Re-send the repository notification email for an assignment (or a course)."""
    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_manage_students(request.user, course):
        raise Http404

    if assignment_id is not None:
        assignment = get_object_or_404(Assignment, pk=assignment_id, course=course)
        assignments = [assignment]
    else:
        assignments = list(course.assignments.all())

    sent = 0
    skipped = 0
    errors = []
    for assignment in assignments:
        for student in Student.objects.filter(course=course):
            repo = AssignmentRepo.objects.filter(student=student, assignment=assignment).first()
            if not repo or not repo.repository_url:
                skipped += 1
                continue
            result = _notify_student_repo(course, student, assignment=assignment, force=True)
            if result is True:
                sent += 1
            elif result:
                errors.append("%s (%s): %s" % (student.email, assignment.name, result))
            else:
                skipped += 1

    if errors:
        messages.error(request, "Some notifications failed:<br>" + "<br>".join(errors))
    if sent:
        messages.success(request, "Sent %d notification email(s)." % sent)
    if skipped and not sent and not errors:
        messages.info(request, "No students were notified (%d skipped — no repo or notifications disabled)." % skipped)

    return redirect('assignments:student_list', course_id=course.pk)


@login_required
def student_add(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_manage_students(request.user, course):
        raise Http404
    if request.method == "POST":
        form = StudentForm(request.POST, course=course)
        if form.is_valid():
            student = form.save(commit=False)
            student.course = course
            student.save()
            # Sync to grading DB but do NOT auto-provision repos.
            # Faculty must click 'Provision Repos' manually.
            _sync_student_to_grading_db(student)
            messages.success(request, "Student %s added. Use 'Provision Missing Repos' "
                                      "to create their GitLab repository." % student.email)
            return redirect('assignments:student_list', course_id=course.pk)
        # Invalid: fall through and re-render with the errors shown.
    else:
        form = StudentForm(course=course)
    return render(request, 'assignments/student_add.html', {"course": course, "form": form})


# Module-level dict for tracking import progress across threads
_import_progress = {}

# Same idea for repository provisioning, keyed by _provision_key(). Provisioning
# makes several GitLab calls per student, so it must not run inside a request
# (see provision_students).
_provision_progress = {}

# A provisioning run that writes no progress for this long is reported as
# 'stalled' rather than 'running', so the page cannot poll forever.
_PROVISION_STALL_SECONDS = 180


def _run_bulk_import(course_id, entries, assignment_name, has_assignments):
    """Background thread: imports students and provisions GitLab repos.

    `entries` is a list of (email, gitlab_username) tuples. The GitLab account
    defaults to the email prefix when not supplied, and the display username is
    always derived from the email prefix (see Student.save())."""
    global _import_progress
    import logging
    logger = logging.getLogger('django')
    try:
        course = Course.objects.get(pk=course_id)
        created = 0
        total = len(entries)

        for i, (email, gitlab_username) in enumerate(entries):
            # Convention: the GitLab account is the part of the email before @.
            gitlab_username = gitlab_username or email.split('@')[0]
            _import_progress[course_id] = {
                'total': total, 'current': i + 1, 'created': created,
                'status': 'running', 'current_student': email,
            }
            try:
                student, was_created = Student.objects.get_or_create(
                    course=course, email=email,
                    defaults={'gitlab_username': gitlab_username},
                )
                if was_created:
                    # Sync to grading DB but do NOT auto-provision repos.
                    # Faculty must click 'Provision Repos' manually.
                    _sync_student_to_grading_db(student)
                    created += 1
                    logger.info("Imported %s" % email)
                else:
                    # Backfill a GitLab username on an existing record if we now have one
                    if gitlab_username and not student.gitlab_username:
                        student.gitlab_username = gitlab_username
                        student.save()
                        _sync_student_to_grading_db(student)
                    logger.info("Skipped %s (already exists)" % email)
            except Exception as e:
                logger.error("Failed to import %s: %s" % (email, e))

        _import_progress[course_id] = {
            'total': total, 'current': total, 'created': created,
            'skipped': total - created, 'status': 'done', 'current_student': '',
        }
        logger.info("Import complete: %d created, %d skipped" % (created, total - created))
    except Exception as e:
        logger.error("Bulk import failed: %s" % e)
        _import_progress[course_id] = {
            'total': 0, 'current': 0, 'created': 0,
            'skipped': 0, 'status': 'error', 'current_student': str(e),
        }


@login_required
def student_bulk_import(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_manage_students(request.user, course):
        raise Http404
    if request.method == "POST":
        form = StudentBulkForm(request.POST)
        if form.is_valid():
            emails_raw = form.cleaned_data['emails']
            # Each line is "email" or "email,gitlab_username"
            # (comma/tab/space separated). The GitLab account defaults to the
            # part of the email before @.
            entries = []
            for line in emails_raw.strip().splitlines():
                line = line.strip()
                if not line or '@' not in line:
                    continue
                parts = [p.strip() for p in re.split(r'[,\t]+|\s+', line)]
                email = parts[0]
                if '@' not in email:
                    continue
                gitlab_username = parts[1] if len(parts) > 1 else ''
                entries.append((email, gitlab_username))
            total = len(entries)
            if total == 0:
                return render(request, 'assignments/student_import_result.html', {
                    "course": course, "created": 0, "total": 0,
                })

            # Only provision repos if the course has assignments
            has_assignments = course.assignments.exists()
            first_assignment = course.assignments.first() if has_assignments else None
            assignment_name = first_assignment.name if first_assignment else course.name

            # Initialize progress
            _import_progress[course.pk] = {
                'total': total, 'current': 0, 'created': 0,
                'status': 'running', 'current_student': '',
            }

            # Run import in background thread so the progress page can poll
            thread = threading.Thread(
                target=_run_bulk_import,
                args=(course.pk, entries, assignment_name, has_assignments),
                daemon=True,
            )
            thread.start()

            return redirect('assignments:import_progress', course_id=course.pk)
    else:
        form = StudentBulkForm()
    return render(request, 'assignments/student_import.html', {"course": course, "form": form})


@login_required
def import_progress(request, course_id):
    """Show a live progress page that polls for status updates."""
    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_manage_students(request.user, course):
        raise Http404
    return render(request, 'assignments/import_progress.html', {"course": course})


@login_required
def import_progress_api(request, course_id):
    """JSON endpoint polled by the progress page.

    The payload includes the email currently being imported, so it is gated the
    same way as the import it reports on.
    """
    from django.http import JsonResponse
    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_manage_students(request.user, course):
        raise Http404
    progress = _import_progress.get(course_id, None)
    if progress:
        return JsonResponse(progress)
    return JsonResponse({'status': 'idle', 'total': 0, 'current': 0})


@login_required
def student_edit(request, course_id, student_id):
    course = get_object_or_404(Course, pk=course_id)
    student = get_object_or_404(Student, pk=student_id, course=course)
    if not _user_can_manage_students(request.user, course):
        raise Http404
    if request.method == "POST":
        form = StudentEditForm(request.POST, instance=student)
        if form.is_valid():
            form.save()
            _sync_student_to_grading_db(student)
            messages.success(request, "Student %s updated." % student.email)
            return redirect('assignments:student_list', course_id=course.pk)
        # Invalid: fall through and re-render with the errors shown.
    else:
        form = StudentEditForm(instance=student)
    return render(request, 'assignments/student_edit.html', {
        "course": course, "student": student, "form": form,
    })


@login_required
def student_delete(request, course_id, student_id):
    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_manage_students(request.user, course):
        raise Http404
    student = get_object_or_404(Student, pk=student_id, course=course)
    student.delete()
    return redirect('assignments:student_list', course_id=course.pk)


@login_required
def assignment_students(request, assignment_id):
    """Per-assignment roster: one repository per student for THIS assignment.

    This is the page that answers "which students have a repo for this
    assignment?" — the course-level student list deliberately shows only the
    roster, because repositories are assignment-scoped.
    """
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    course = assignment.course

    # Access mirrors assignment_view: owner, admin, or anyone with course access.
    has_access = (assignment.owner == request.user.id or request.user.is_superuser)
    if not has_access and course:
        has_access = _user_can_access_course(request.user, course)
    if not has_access:
        raise Http404

    students = []
    if course:
        repo_by_student = {
            repo.student_id: repo
            for repo in AssignmentRepo.objects.filter(assignment=assignment)
        }
        for student in Student.objects.filter(course=course).order_by('email'):
            students.append({
                'student': student,
                'repo': repo_by_student.get(student.pk),
            })

    provisioned = sum(1 for row in students if row['repo'] and row['repo'].repository_url)
    return render(request, 'assignments/assignment_students.html', {
        "assignment": assignment,
        "course": course,
        "students": students,
        "provisioned": provisioned,
        "missing": len(students) - provisioned,
        "group_count": assignment.groups.count(),
        "ungrouped": _ungrouped_students(assignment).count() if course else 0,
        "can_manage_students": bool(course) and _user_can_manage_students(request.user, course),
    })


def _ungrouped_students(assignment):
    """Roster students with no group on this assignment."""
    if assignment.course_id is None:
        return Student.objects.none()
    grouped = (GroupMembership.objects
               .filter(group__assignment=assignment)
               .values_list('student_id', flat=True))
    return Student.objects.filter(course=assignment.course).exclude(pk__in=list(grouped))


def _assignment_access_or_404(user, assignment):
    """Mirror assignment_view's access rule; raise 404 when it fails."""
    has_access = (assignment.owner == user.id or user.is_superuser)
    if not has_access and assignment.course:
        has_access = _user_can_access_course(user, assignment.course)
    if not has_access:
        raise Http404


def _group_repo_name(assignment, group):
    """The repository name a group is provisioned with: <assignment>-g<N>."""
    return "%s-g%d" % (_group_repo_prefix(assignment), group.repo_index)


def _group_repo_prefix(assignment):
    prefix = re.sub(r'[^a-zA-Z0-9_-]', '-', (assignment.name or '').lower()).strip('-')
    return re.sub(r'-+', '-', prefix) or 'assignment'


def _groups_board_context(assignment, can_manage):
    """Shared context for the drag-and-drop grouping board."""
    groups = []
    for group in assignment.groups.all():
        members = [m.student for m in group.memberships.select_related('student')]
        member_ids = [s.pk for s in members]
        url = (AssignmentRepo.objects
               .filter(assignment=assignment, student_id__in=member_ids)
               .exclude(repository_url='')
               .values_list('repository_url', flat=True)
               .first() or '')
        groups.append({
            'group': group,
            'members': members,
            'repo_url': url,
            'repo_name': _group_repo_name(assignment, group),
        })

    return {
        "assignment": assignment,
        "course": assignment.course,
        "groups": groups,
        # Students with no group: the "Unassigned" column.
        "ungrouped": list(_ungrouped_students(assignment).order_by('email')),
        "student_count": Student.objects.filter(course=assignment.course).count(),
        # JSON for the drag-and-drop script (ids, names, memberships).
        "board_json": {
            "groups": [
                {"id": row['group'].pk, "name": row['group'].name,
                 "members": [s.pk for s in row['members']]}
                for row in groups
            ],
            "ungrouped": [s.pk for s in _ungrouped_students(assignment)],
        },
        "can_manage_students": can_manage,
    }


@login_required
def assignment_groups(request, assignment_id):
    """The group board: drag students between groups and Unassigned."""
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    _assignment_access_or_404(request.user, assignment)
    context = _groups_board_context(
        assignment, _user_can_manage_students(request.user, assignment.course))
    # A group page for an individual assignment is a dead end; send the
    # instructor somewhere useful instead of showing an empty board.
    context["is_group_assignment"] = assignment.is_group_assignment
    return render(request, 'assignments/assignment_groups.html', context)


@login_required
def assignment_group_create(request, assignment_id):
    """Create a group, optionally with its first members."""
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if assignment.course_id is None or not _user_can_manage_students(request.user, assignment.course):
        raise Http404

    if request.method == "POST":
        form = StudentGroupForm(request.POST, assignment=assignment)
        if form.is_valid():
            group = form.save(commit=False)
            group.assignment = assignment
            group.repo_index = StudentGroup(assignment=assignment).next_repo_index()
            group.save()

            # Optional initial members, passed as repeated 'members' values.
            member_ids = request.POST.getlist('members')
            _set_group_members(group, member_ids)
            # Creating a group is what makes an assignment a group assignment.
            if not assignment.is_group_assignment:
                assignment.is_group_assignment = True
                assignment.save(update_fields=['is_group_assignment'])
            messages.success(request, "Group '%s' created." % group.name)
            return redirect('assignments:assignment_groups', assignment_id=assignment.pk)
    else:
        form = StudentGroupForm(assignment=assignment)

    return render(request, 'assignments/assignment_group_form.html', {
        "assignment": assignment, "course": assignment.course, "group": None,
        "form": form,
        "eligible": _ungrouped_students(assignment).order_by('email'),
    })


def _next_group_name(assignment):
    """A random team name not already used on this assignment."""
    from .team_names import random_team_name
    taken = assignment.groups.values_list('name', flat=True)
    return random_team_name(taken)


@login_required
def assignment_group_random_name(request, assignment_id):
    """Return a random unused team name as JSON (used by the Random button)."""
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if assignment.course_id is None or not _user_can_manage_students(request.user, assignment.course):
        raise Http404
    return JsonResponse({"name": _next_group_name(assignment)})


@login_required
def assignment_group_move(request, assignment_id):
    """Move a student into a group (or out to 'Unassigned') — drag and drop.

    POST body: student=<id> and either group=<id> or group="" for Unassigned.

    Moving is the only mutation the board performs, so it lives in one place:
    a membership is replaced rather than added, keeping the "one group per
    student per assignment" rule true even if the board is used concurrently.
    """
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if assignment.course_id is None or not _user_can_manage_students(request.user, assignment.course):
        raise Http404
    if request.method != "POST":
        return JsonResponse({"error": "POST required."}, status=405)

    student = get_object_or_404(Student, pk=request.POST.get('student'), course=assignment.course)
    group_id = (request.POST.get('group') or '').strip()

    # Clear any existing membership first: this is a move, not an add.
    GroupMembership.objects.filter(assignment=assignment, student=student).delete()

    if group_id:
        group = get_object_or_404(StudentGroup, pk=group_id, assignment=assignment)
        GroupMembership.objects.create(group=group, student=student, assignment=assignment)
        # Adopt the group's shared repository, if it has one. Only a URL held by
        # two or more members counts, so the arriving student's own individual
        # URL is never mistaken for the group's.
        url = _group_shared_url(
            assignment, [m.student_id for m in group.memberships.all()])
        # update_or_create (not filter().update()) so the row always exists: an
        # absent row and an empty URL are equivalent for grading, but an explicit
        # row keeps the board and the database agreeing.
        AssignmentRepo.objects.update_or_create(
            assignment=assignment, student=student,
            defaults={'repository_url': url})
        # Grouping a student is what makes an assignment a group assignment, so
        # flip the flag the same way creating a group does. Otherwise the groups
        # would exist but provisioning would ignore them.
        if not assignment.is_group_assignment:
            assignment.is_group_assignment = True
            assignment.save(update_fields=['is_group_assignment'])
    else:
        # Moved back to Unassigned: they no longer share anyone's repository.
        AssignmentRepo.objects.update_or_create(
            assignment=assignment, student=student,
            defaults={'repository_url': ''})

    _sync_student_to_grading_db(student, assignment=assignment)

    return JsonResponse({
        "ok": True,
        "student": student.pk,
        "group": int(group_id) if group_id else None,
    })


@login_required
def assignment_group_edit(request, assignment_id, group_id):
    """Rename a group and change its members."""
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if assignment.course_id is None or not _user_can_manage_students(request.user, assignment.course):
        raise Http404
    group = get_object_or_404(StudentGroup, pk=group_id, assignment=assignment)

    if request.method == "POST":
        form = StudentGroupForm(request.POST, instance=group, assignment=assignment)
        members_form = GroupMembersForm(request.POST, group=group)
        if form.is_valid() and members_form.is_valid():
            form.save()
            _set_group_members(
                group,
                [s.pk for s in members_form.cleaned_data['students']])
            messages.success(request, "Group '%s' updated." % group.name)
            return redirect('assignments:assignment_groups', assignment_id=assignment.pk)
    else:
        form = StudentGroupForm(instance=group, assignment=assignment)
        members_form = GroupMembersForm(group=group)

    return render(request, 'assignments/assignment_group_form.html', {
        "assignment": assignment, "course": assignment.course, "group": group,
        "form": form, "members_form": members_form,
    })


def _group_shared_url(assignment, student_ids):
    """The repository URL genuinely shared by a group, or '' if there isn't one.

    A URL held by only ONE member is that member's individual repository, not the
    group's, so adopting it would silently hand the whole group one student's
    repo. Requiring at least two members to hold the same URL distinguishes a
    provisioned group repository from an individual one.
    """
    from collections import Counter
    urls = (AssignmentRepo.objects
            .filter(assignment=assignment, student_id__in=list(student_ids))
            .exclude(repository_url='')
            .values_list('repository_url', flat=True))
    counts = Counter(urls)
    for url, count in counts.most_common():
        if count >= 2:
            return url
    return ''


def _set_group_members(group, student_ids):
    """Replace a group's membership with `student_ids`.

    Also drops any AssignmentRepo rows for members that no longer have a repo —
    a student moved out of a group should not keep the group's repository URL,
    or they would be graded against it while their group-mates are not.
    """
    wanted = {int(pk) for pk in student_ids}
    existing = {m.student_id for m in group.memberships.all()}

    group.memberships.filter(student_id__in=existing - wanted).delete()
    for student_id in wanted - existing:
        # A student can only be in one group per assignment, so drop any other
        # membership before creating this one. The DB constraint enforces it too.
        GroupMembership.objects.filter(
            assignment=group.assignment, student_id=student_id).delete()
        GroupMembership.objects.create(
            group=group, student_id=student_id, assignment=group.assignment)

    # Keep the shared repo URL consistent across all current members. Only a URL
    # already held by two or more members counts as the group's.
    url = _group_shared_url(group.assignment, wanted)
    if url:
        AssignmentRepo.objects.filter(
            assignment=group.assignment, student_id__in=wanted).update(repository_url=url)
    else:
        # No shared repo yet, so no member may keep an individual URL that the
        # rest of the group does not have.
        AssignmentRepo.objects.filter(
            assignment=group.assignment, student_id__in=wanted).update(repository_url='')


@login_required
def assignment_group_delete(request, assignment_id, group_id):
    """Delete a group. Members keep their roster entry but lose the group repo."""
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if assignment.course_id is None or not _user_can_manage_students(request.user, assignment.course):
        raise Http404
    group = get_object_or_404(StudentGroup, pk=group_id, assignment=assignment)

    member_ids = list(group.memberships.values_list('student_id', flat=True))
    # Deleting the group cascades its memberships. Clear the shared repo URL so
    # the members are not left pointing at a repo that is no longer theirs.
    AssignmentRepo.objects.filter(
        assignment=assignment, student_id__in=member_ids).update(repository_url='')
    name = group.name
    group.delete()
    messages.success(request, "Group '%s' deleted." % name)
    return redirect('assignments:assignment_groups', assignment_id=assignment.pk)


@login_required
def assignment_group_autogroup(request, assignment_id):
    """Randomly group the remaining ungrouped students into groups of `size`."""
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if assignment.course_id is None or not _user_can_manage_students(request.user, assignment.course):
        raise Http404
    if request.method != "POST":
        return redirect('assignments:assignment_groups', assignment_id=assignment.pk)

    try:
        size = int(request.POST.get('size', 2))
    except (TypeError, ValueError):
        size = 2
    if size < 2:
        messages.error(request, "Group size must be at least 2.")
        return redirect('assignments:assignment_groups', assignment_id=assignment.pk)

    remaining = list(_ungrouped_students(assignment).values_list('pk', flat=True))
    if not remaining:
        messages.info(request, "Every student is already in a group.")
        return redirect('assignments:assignment_groups', assignment_id=assignment.pk)

    random.shuffle(remaining)
    created = 0
    for chunk in [remaining[i:i + size] for i in range(0, len(remaining), size)]:
        # Compute the index once: next_repo_index() reads the max each call, so
        # calling it twice for name and index would produce mismatched values.
        index = StudentGroup(assignment=assignment).next_repo_index()
        group = StudentGroup.objects.create(
            assignment=assignment,
            name=_next_group_name(assignment),
            repo_index=index,
        )
        for student_id in chunk:
            GroupMembership.objects.create(
                group=group, student_id=student_id, assignment=assignment)
        created += 1

    # Auto-grouping only makes sense on a group assignment; flip the flag so
    # provisioning creates shared repos rather than one per student.
    if created and not assignment.is_group_assignment:
        assignment.is_group_assignment = True
        assignment.save(update_fields=['is_group_assignment'])

    messages.success(request, "Created %d group(s) from %d ungrouped student(s)."
                     % (created, len(remaining)))
    return redirect('assignments:assignment_groups', assignment_id=assignment.pk)



@login_required
def assignment_provision_students(request, assignment_id):
    """Provision repositories for every student on ONE assignment."""
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if assignment.course_id is None:
        raise Http404
    return provision_students(request, assignment.course_id, assignment_id=assignment.pk)


@login_required
def assignment_notify_students(request, assignment_id):
    """Re-send repository notifications for ONE assignment."""
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if assignment.course_id is None:
        raise Http404
    return notify_students(request, assignment.course_id, assignment_id=assignment.pk)


@login_required
def assignment_repo_edit(request, assignment_id, student_id):
    """Manually set (or clear) one student's repository URL for one assignment.

    Covers the provisioning escape hatches: the student already has a repo, or
    it lives somewhere that is not managed by the GitLab integration.
    """
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    if assignment.course_id is None:
        raise Http404
    course = assignment.course
    if not _user_can_manage_students(request.user, course):
        raise Http404
    student = get_object_or_404(Student, pk=student_id, course=course)

    repo, _created = AssignmentRepo.objects.get_or_create(student=student, assignment=assignment)

    if request.method == "POST":
        form = AssignmentRepoForm(request.POST, instance=repo)
        if form.is_valid():
            form.save()
            # Push the change straight to the grading DB so the daemon picks it up.
            _sync_student_to_grading_db(student, assignment=assignment)
            messages.success(request, "Repository for %s updated." % student.email)
            return redirect('assignments:assignment_students', assignment_id=assignment.pk)
    else:
        form = AssignmentRepoForm(instance=repo)

    return render(request, 'assignments/assignment_repo_edit.html', {
        "assignment": assignment, "course": course, "student": student,
        "repo": repo, "form": form,
    })


# =========================================================================
#  GitLab auto-provisioning helper
# =========================================================================

def _get_gitlab_config(course):
    """Extract GitLab credentials for a course.
    First tries the .env file in the assignment directory, then falls back to
    reading the owner's profile. Returns (gitlab_url, gitlab_token) or (None, None)."""
    # Try reading from .env file first
    for assignment in course.assignments.all():
        env_path = os.path.join(settings.BASE_DIR, assignment.absolute_path, '.env')
        if os.path.exists(env_path):
            try:
                env = {}
                with open(env_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if '=' in line and not line.startswith('#'):
                            k, v = line.split('=', 1)
                            env[k.strip()] = v.strip()
                if env.get('GIT_PROVIDER') == 'gitlab' and env.get('GIT_PASSWORD'):
                    return env.get('GIT_URL', 'gitlab.com'), env['GIT_PASSWORD']
            except Exception:
                pass

    # Fallback: read from the course owner's profile
    try:
        owner = User.objects.get(pk=course.owner)
        profile = owner.profile
        if profile.gitlab_enabled and profile.gitlab_token:
            return profile.gitlab_url, profile.gitlab_token
    except (User.DoesNotExist, UserProfile.DoesNotExist):
        pass

    return None, None


def _notify_student_repo(course, student, assignment=None, repo_url=None, force=False):
    """Email a student that their repository for ONE assignment is ready.

    Returns True when a message was sent, False when notifications are disabled
    or the student has no address, or an error string when delivery failed.

    ``notified_at`` is tracked on the AssignmentRepo, so a student who was told
    about "SQL 1" is still notified when "SQL 2" is provisioned. Pass
    ``force=True`` to re-send regardless.
    """
    if not student.email:
        return False

    if assignment is None:
        return False

    # Resolve (and create if needed) the per-assignment repo record.
    repo, _created = AssignmentRepo.objects.get_or_create(student=student, assignment=assignment)
    if repo_url:
        repo.repository_url = repo_url
        repo.save(update_fields=['repository_url'])

    if repo.notified_at and not force:
        return False
    if not repo.repository_url:
        return False

    try:
        faculty = User.objects.get(pk=course.owner)
        faculty_profile = faculty.profile
    except (User.DoesNotExist, UserProfile.DoesNotExist):
        return False

    if not (faculty_profile.notify_students and faculty_profile.notification_api_key):
        return False

    from athina_web.accounts.resend_email import send_email_safe

    assignment_name = assignment.name
    subject = "[Athina] Your repository for %s has been created" % course.name
    text_body = (
        "Hello,\n\n"
        "A new repository has been created for you in the course '%s'.\n\n"
        "Assignment: %s\n"
        "Repository URL: %s\n\n"
        "You can start working on your assignment and push your code to this "
        "repository.\n\n"
        "If you have any questions, please contact your instructor.\n\n"
        "— Athina" % (course.name, assignment_name,
                      repo.repository_url)
    )
    html_body = (
        "<p>Hello,</p>"
        "<p>A new repository has been created for you in the course "
        "<strong>%s</strong>.</p>"
        "<ul>"
        "<li><strong>Assignment:</strong> %s</li>"
        "<li><strong>Repository URL:</strong> "
        "<a href=\"%s\">%s</a></li>"
        "</ul>"
        "<p>You can start working on your assignment and push your code to this "
        "repository.</p>"
        "<p>If you have any questions, please contact your instructor.</p>"
        "<p>— Athina</p>" % (course.name, assignment_name,
                              repo.repository_url, repo.repository_url)
    )

    message_id = send_email_safe(
        api_key=faculty_profile.notification_api_key,
        to=student.email,
        subject=subject,
        text=text_body,
        html=html_body,
        from_email=faculty_profile.notification_from_email or None,
        reply_to=faculty_profile.notification_reply_to or None,
    )
    if message_id is None:
        return "Could not send notification email to %s (check the Resend API key and sender address)." % student.email

    repo.notified_at = timezone.now()
    repo.save(update_fields=['notified_at'])
    # Audit trail: every real send is logged so duplicate deliveries can be traced.
    logging.getLogger('athina_web').info(
        "Sent repo notification to %s for course '%s' assignment '%s' (resend_id=%s)",
        student.email, course.name, assignment_name, message_id)
    return True


# GitLab access levels (https://docs.gitlab.com/ee/api/access_requests.html)
GITLAB_ACCESS_DEVELOPER = 30
GITLAB_ACCESS_MAINTAINER = 40
GITLAB_ACCESS_OWNER = 50


def _gitlab_find_user_id(api_base, headers, username):
    """Resolve a GitLab username to a user id. Returns None if not found.

    Kept for callers that only need an id. Prefer _gitlab_lookup_user when the
    reason for failure matters, because this collapses "no such account" and
    "the request failed" into the same None.
    """
    user, _outcome, _detail = _gitlab_lookup_user(api_base, headers, handle=username)
    return user['id'] if user else None


# Outcomes from _gitlab_lookup_user.
GITLAB_LOOKUP_FOUND = 'found'
GITLAB_LOOKUP_NOT_FOUND = 'not_found'
GITLAB_LOOKUP_ERROR = 'error'


def _gitlab_lookup_user(api_base, headers, handle='', email='', timeout=10):
    """Find a student's GitLab account, trying several lookup forms.

    Returns ``(user_dict_or_None, outcome, detail)`` where outcome is one of the
    GITLAB_LOOKUP_* constants.

    Two forms are attempted, in order:

    1. an exact match on the stored handle (``?username=``);
    2. a search on the email address (``?search=``), which GitLab matches against
       a user's email and display name.

    Step 2 matters: accounts provisioned through LDAP/SAML (as this deployment
    uses) often carry a username that is not the email prefix, and an exact
    username lookup simply cannot find them — the instructor then sees "student
    not found" for someone who has a perfectly good account.

    Distinguishing NOT_FOUND from ERROR is the other point of this function.
    Reporting a timeout, a bad token, or an unreachable host as "account not
    found" sends the instructor hunting for the wrong problem entirely.
    """
    attempts = []
    if handle:
        attempts.append(("exact username '%s'" % handle, {"username": handle}))
    if email:
        attempts.append(("email search '%s'" % email, {"search": email}))

    if not attempts:
        return None, GITLAB_LOOKUP_NOT_FOUND, "no username or email on the student record"

    last_detail = ""
    transport_failed = False
    for label, params in attempts:
        exact = 'username' in params
        try:
            resp = http_requests.get("%s/users" % api_base, headers=headers,
                                     params=params, timeout=timeout)
        except Exception as exc:
            # A transport failure is an ERROR, never a NOT_FOUND — the whole point
            # is not to blame the student for the network.
            transport_failed = True
            last_detail = "%s: request failed (%s)" % (label, type(exc).__name__)
            continue

        if resp.status_code == 401 or resp.status_code == 403:
            return None, GITLAB_LOOKUP_ERROR, (
                "%s: not permitted (HTTP %s). The faculty GitLab token may be "
                "expired or lack permission to read users." % (label, resp.status_code))
        if resp.status_code == 404:
            # The endpoint itself is missing — a wrong host or a GitLab version
            # without the API, not a missing student.
            return None, GITLAB_LOOKUP_ERROR, (
                "%s: the GitLab users API returned 404. Check the GitLab URL "
                "configured for this course." % label)
        if not resp.ok:
            last_detail = "%s: HTTP %s" % (label, resp.status_code)
            continue

        try:
            results = resp.json()
        except ValueError:
            last_detail = "%s: response was not JSON" % label
            continue

        if not results:
            last_detail = "%s: no match" % label
            continue

        if exact:
            # ?username= is an exact match, so the first result is the account.
            return results[0], GITLAB_LOOKUP_FOUND, "matched by %s" % label

        # A search is fuzzy. Guessing here would add the WRONG person to a
        # private repository and show them another student's work, so only accept
        # an unambiguous result: one that matches the stored handle exactly, or a
        # single hit.
        exact_name = [u for u in results if handle and u.get('username') == handle]
        if exact_name:
            return exact_name[0], GITLAB_LOOKUP_FOUND, "matched by %s" % label
        if len(results) == 1:
            return results[0], GITLAB_LOOKUP_FOUND, "matched by %s" % label
        last_detail = ("%s: ambiguous (%d possible accounts, none named '%s')"
                       % (label, len(results), handle))

    # Every attempt failed at the transport layer: that is an ERROR, not "the
    # student has no account".
    if transport_failed:
        return None, GITLAB_LOOKUP_ERROR, last_detail
    return None, GITLAB_LOOKUP_NOT_FOUND, last_detail


def _gitlab_list_group_members(api_base, headers, group_id, max_pages=50):
    """Every member of a GitLab group, following pagination.

    GitLab caps a single response at `per_page`, so without paging a group with
    more members than one page reports only the first batch. Existing members
    past that point then look absent, causing repeated re-add attempts that
    GitLab rejects (the sync would report a spurious failure).

    Returns a list of member dicts, or None when the request failed.
    """
    members = []
    page = 1
    while page <= max_pages:
        try:
            resp = http_requests.get(
                "%s/groups/%s/members" % (api_base, group_id),
                headers=headers, params={"per_page": 100, "page": page}, timeout=10)
        except Exception:
            return None
        if not resp.ok:
            return None
        batch = resp.json() or []
        members.extend(batch)
        # A short page means there is no next one.
        if len(batch) < 100:
            break
        page += 1
    return members


def _gitlab_add_group_member(api_base, headers, group_id, username, access_level):
    """Add a user to a GitLab group if not already a member (idempotent).

    Returns True if the user is (now) a member, False otherwise.
    """
    user_id = _gitlab_find_user_id(api_base, headers, username)
    if user_id is None:
        return False
    try:
        existing = _gitlab_list_group_members(api_base, headers, group_id)
        if existing is None:
            return False
        if user_id in [m.get('id') for m in existing]:
            return True
        resp = http_requests.post(
            "%s/groups/%s/members" % (api_base, group_id),
            headers=headers,
            data={"user_id": user_id, "access_level": access_level},
            timeout=10)
        return resp.ok
    except Exception:
        return False


def _gitlab_list_project_members(api_base, headers, project_id, max_pages=50):
    """Every member of a GitLab project, following pagination.

    Uses /members/all rather than /members: the former includes members who
    inherited access from the parent group, which is how faculty and TAs reach a
    student's repository. Without it they look absent and the code re-adds them.
    """
    members = []
    page = 1
    while page <= max_pages:
        try:
            resp = http_requests.get(
                "%s/projects/%s/members/all" % (api_base, project_id),
                headers=headers, params={"per_page": 100, "page": page}, timeout=10)
        except Exception:
            return None
        if not resp.ok:
            return None
        batch = resp.json() or []
        members.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return members


def _gitlab_ensure_project_member(api_base, headers, project_id, user_id, access_level):
    """Make sure a user is a member of a project at *at least* `access_level`.

    Returns ``(ok, message)``.

    Two behaviours that matter here:

    * It raises the level of an existing member. A student provisioned earlier
      was added as Developer, but GitLab's default protected branch lets only
      Maintainers push to ``master`` — so they could clone their repository and
      not submit anything. Re-running provisioning has to repair that, not skip
      the member as "already present".

    * It is used for *all* members, not just ones this code added, because the
      existing level is not otherwise knowable without a call per user.
    """
    existing = _gitlab_list_project_members(api_base, headers, project_id)
    if existing is None:
        return False, "could not read the repository's member list"

    current = None
    for member in existing:
        if member.get('id') == user_id:
            current = member
            break

    if current is None:
        try:
            resp = http_requests.post(
                "%s/projects/%s/members" % (api_base, project_id),
                headers=headers,
                data={"user_id": user_id, "access_level": access_level}, timeout=10)
        except Exception as exc:
            return False, "adding member failed (%s)" % type(exc).__name__
        if not resp.ok:
            return False, "adding member failed (HTTP %s)" % resp.status_code
        return True, "added"

    current_level = current.get('access_level') or 0
    if current_level == access_level:
        return True, "already a member"

    # Never lower anyone. The point is to make sure a student can push, not to
    # demote a member who already holds a higher role (an Owner, say).
    if current_level > access_level:
        return True, "already has a higher access level (%s)" % current_level

    # GitLab returns 409 when asked to set a level equal to the current one, but
    # guard anyway so a no-op is never reported as a failure.
    try:
        resp = http_requests.put(
            "%s/projects/%s/members/%s" % (api_base, project_id, user_id),
            headers=headers, data={"access_level": access_level}, timeout=10)
    except Exception as exc:
        return False, "updating access failed (%s)" % type(exc).__name__
    if not resp.ok:
        return False, ("updating access from %s to %s failed (HTTP %s)"
                       % (current_level, access_level, resp.status_code))
    return True, "access raised from %s to %s" % (current_level, access_level)


def _gitlab_remove_group_member(api_base, headers, group_id, user_id):
    """Remove a user from a GitLab group. Returns True when they are gone.

    Used to revoke a TA's access when they are unassigned. Without this, a
    de-assigned TA keeps Maintainer rights on the group and can still clone
    every student repository, even though the web app has already revoked their
    Django-side access.
    """
    try:
        resp = http_requests.delete(
            "%s/groups/%s/members/%s" % (api_base, group_id, user_id),
            headers=headers, timeout=10)
        # 404 means they are already gone, which is the state we want.
        return resp.ok or resp.status_code == 404
    except Exception:
        return False


def _ensure_course_group(course, gitlab_url, gitlab_token):
    """Find or create the GitLab group for a course.

    Naming: athina-[facultyid]-[coursename].
    Returns (group_id, group_name, created) or (None, None, False) on failure.
    """
    headers = {"PRIVATE-TOKEN": gitlab_token}
    api_base = "https://%s/api/v4" % gitlab_url

    faculty_id = course.owner
    course_slug = re.sub(r'[^a-zA-Z0-9_-]', '-', course.name.lower()).strip('-')
    group_name = "athina-%d-%s" % (faculty_id, course_slug)
    group_name = re.sub(r'-+', '-', group_name)

    resp = http_requests.get("%s/groups" % api_base, headers=headers,
                             params={"search": group_name}, timeout=10)
    group = None
    if resp.ok:
        for g in resp.json():
            if g.get('path') == group_name:
                group = g
                break

    if group is not None:
        return group['id'], group_name, False

    resp = http_requests.post("%s/groups" % api_base, headers=headers, data={
        "name": "Athina - %s" % course.name,
        "path": group_name,
        "visibility": "private",
    }, timeout=10)
    if not resp.ok:
        return None, None, False

    return resp.json()['id'], group_name, True


def _sync_course_group_members(course, gitlab_url, gitlab_token, group_id):
    """Ensure the faculty owner and all assigned TAs are members of the course group.

    Faculty owner -> Owner (50); assigned TAs -> Maintainer (40). Group-level
    membership means they inherit access to every student repo in the group,
    including repos created later. Returns the number of members ensured.

    Note: TAs are stored as ``ta_profile.managed_by -> [faculty]`` (see the
    repair migration 0009), so we look up TAs whose ``managed_by`` includes the
    faculty owner — matching ``_user_can_access_course``.
    """
    headers = {"PRIVATE-TOKEN": gitlab_token}
    api_base = "https://%s/api/v4" % gitlab_url
    ensured = 0

    try:
        owner = User.objects.get(pk=course.owner)
        owner_profile = owner.profile
    except (User.DoesNotExist, UserProfile.DoesNotExist):
        return 0

    # Faculty owner (explicit, even though their token created the group)
    if owner_profile.gitlab_username and _gitlab_add_group_member(
            api_base, headers, group_id, owner_profile.gitlab_username, GITLAB_ACCESS_OWNER):
        ensured += 1

    # Assigned TAs: ta.profile.managed_by contains the faculty they assist.
    ta_profiles = UserProfile.objects.filter(
        role=UserProfile.ROLE_TA, managed_by=owner)
    for ta_profile in ta_profiles:
        ta_username = ta_profile.gitlab_username
        if ta_username and _gitlab_add_group_member(
                api_base, headers, group_id, ta_username, GITLAB_ACCESS_MAINTAINER):
            ensured += 1

    return ensured


def sync_faculty_course_members(faculty_user, removed_ta_ids=()):
    """Sync group membership for every course owned by a faculty member.

    Called when TA assignments change. Returns
    (courses_synced, members_ensured, members_removed).

    `removed_ta_ids` are TAs the caller has just unassigned from this faculty.
    They are revoked from every course group, because the add-only sync below
    cannot see them: it iterates the *current* TA list, so an unassigned TA would
    otherwise keep Maintainer access to all student repositories forever.
    """
    courses_synced = 0
    members_ensured = 0
    members_removed = 0
    for course in Course.objects.filter(owner=faculty_user.id):
        # Only sync courses that already have a group (i.e. have assignments)
        if not course.assignments.exists():
            continue
        gitlab_url, gitlab_token = _get_gitlab_config(course)
        if not gitlab_url or not gitlab_token:
            continue
        group_id, _group_name, _created = _ensure_course_group(course, gitlab_url, gitlab_token)
        if group_id is None:
            continue
        members_ensured += _sync_course_group_members(course, gitlab_url, gitlab_token, group_id)
        courses_synced += 1

        # Revoke anyone just unassigned. Look up their GitLab id per course,
        # since the token and host can differ between courses.
        for ta_id in removed_ta_ids:
            try:
                ta_profile = UserProfile.objects.get(user_id=ta_id, role=UserProfile.ROLE_TA)
            except UserProfile.DoesNotExist:
                continue
            if not ta_profile.gitlab_username:
                continue
            headers = {"PRIVATE-TOKEN": gitlab_token}
            api_base = "https://%s/api/v4" % gitlab_url
            user_id = _gitlab_find_user_id(api_base, headers, ta_profile.gitlab_username)
            if user_id is None:
                continue
            if _gitlab_remove_group_member(api_base, headers, group_id, user_id):
                members_removed += 1
                logging.getLogger('athina_web').info(
                    "Revoked GitLab group access for unassigned TA %s on course '%s'",
                    ta_profile.user.username, course.name)

    return courses_synced, members_ensured, members_removed


def _build_readme(assignment):
    """Build the README that is committed to every new student repository.

    The top of the file explains how feedback is delivered (Canvas or GitLab
    issues), followed by an important note asking students to keep personal
    information out of the repository.
    """
    if assignment is not None and assignment.output_method == 'gitlab_issues':
        feedback_lines = [
            "Feedback for your submission will be posted as **GitLab issues** on this",
            "repository. Watch this project (or check the Issues tab) after each",
            "submission to see your grade and comments.",
        ]
    else:
        feedback_lines = [
            "Feedback and grades for your submission will be delivered through **Canvas LMS**.",
            "Check the assignment in Canvas after each submission to see your results.",
        ]

    return "\n".join([
        "# %s" % (assignment.name if assignment is not None else "Assignment"),
        "",
        "## How you will receive feedback",
        "",
    ] + feedback_lines + [
        "",
        "Feedback may be generated with the help of an automated grading system, which",
        "can include AI-assisted analysis of your code and test results. A human",
        "instructor always reviews final grades.",
        "",
        "---",
        "",
        "## IMPORTANT: Keep personal information out of this repository",
        "",
        "Do not put your name, email address, student ID, or any other personal",
        "information inside this repository. We already know that this is your",
        "repository, and it is linked to your account automatically.",
        "",
        "This assignment repository is private and shared only with the course",
        "teaching team (instructors and teaching assistants). Keeping personal",
        "details out of the code, comments, filenames, and commit messages protects",
        "your privacy.",
        "",
        "You may delete this README once you have read it, or replace it with your",
        "own documentation. The note above is provided for reference.",
        "",
    ])


def _seed_repo_readme(gitlab_url, token, project_id, assignment):
    """Create an initial commit containing a README.md via the GitLab Commit API.

    Uses the Commit API (actions[]) so the repository gets a ``master`` branch
    with one commit. This matters because the grading engine reads the latest
    commit from ``master`` — a repo with only a .git dir has no branch, so the
    engine would see no commit. Best-effort: returns True on success.
    """
    if project_id is None:
        return False
    try:
        content = _build_readme(assignment)
        resp = http_requests.post(
            "https://%s/api/v4/projects/%s/repository/commits" % (gitlab_url, project_id),
            headers={"PRIVATE-TOKEN": token},
            json={
                "branch": "master",
                "commit_message": "Add README with feedback and privacy instructions",
                "actions": [{
                    "action": "create",
                    "file_path": "README.md",
                    "content": content,
                }],
            },
            timeout=10,
        )
        return resp.ok
    except Exception:
        return False


def _provision_group_gitlab(course, assignment, group):
    """Create ONE repository shared by every member of a group.

    Returns True, or an error string. On success every member's AssignmentRepo
    carries the same URL — which is exactly how the grading engine recognises a
    group (it groups students whose repository_url matches, gated by
    same_limit). The members are all added to the repo as developers.

    Repository name: <assignment>-g<N>.
    """
    gitlab_url, gitlab_token = _get_gitlab_config(course)
    if not gitlab_url or not gitlab_token:
        return "GitLab is not configured for this course (missing URL or token)."

    members = [m.student for m in group.memberships.select_related('student')]
    if not members:
        return "Group '%s' has no members." % group.name

    headers = {"PRIVATE-TOKEN": gitlab_token}
    api_base = "https://%s/api/v4" % gitlab_url

    # Resolve every member's GitLab account first. A member who cannot be found
    # is reported but does not block the others: the repo is still useful and the
    # instructor can fix the account later.
    resolved = []      # (student, gitlab_user_id)
    unresolved = []
    for student in members:
        candidate = (student.gitlab_username or student.username or '').strip()
        if not candidate:
            unresolved.append("%s (no GitLab username)" % student.email)
            continue
        resp = http_requests.get("%s/users" % api_base, headers=headers,
                                 params={"username": candidate}, timeout=10)
        if not resp.ok or not resp.json():
            unresolved.append("%s (%s)" % (student.email, candidate))
            continue
        if student.gitlab_username != candidate:
            student.gitlab_username = candidate
            student.save(update_fields=['gitlab_username'])
        resolved.append((student, resp.json()[0]['id']))

    if not resolved:
        return ("None of the GitLab accounts for group '%s' could be found (%s)." %
                (group.name, ", ".join(unresolved)))

    group_id, group_name, group_created = _ensure_course_group(course, gitlab_url, gitlab_token)
    if group_id is None:
        return ("Failed to create GitLab group for course '%s'. Check the faculty "
                "GitLab token has permission to create groups." % course.name)
    if group_created:
        _sync_course_group_members(course, gitlab_url, gitlab_token, group_id)

    repo_name = _group_repo_name(assignment, group)

    check_resp = http_requests.get(
        "%s/projects/%s%%2F%s" % (api_base, group_name, repo_name),
        headers=headers, timeout=10)
    if check_resp.status_code == 200:
        project = check_resp.json()
    else:
        resp = http_requests.post("%s/projects" % api_base, headers=headers, data={
            "name": repo_name,
            "namespace_id": group_id,
            "visibility": "private",
        }, timeout=10)
        if not resp.ok:
            return ("Failed to create GitLab repo '%s' (HTTP %s). Check the faculty "
                    "GitLab token has permission to create projects in group '%s'." %
                    (repo_name, resp.status_code, group_name))
        project = resp.json()
        _seed_repo_readme(gitlab_url, gitlab_token, project.get('id'), assignment)

    url = project.get('http_url_to_repo', '')

    # Add every resolvable member, or raise them to Maintainer if they are already
    # one. Maintainer rather than Developer because a new project protects `master`
    # with "Allowed to push: Maintainers" — a Developer cannot push, so a student
    # could clone their repository but never submit work to it. In a group the
    # repository is shared, so this is also what lets any member push a group
    # submission.
    for student, gitlab_user_id in resolved:
        ok, message = _gitlab_ensure_project_member(
            api_base, headers, project['id'], gitlab_user_id, GITLAB_ACCESS_MAINTAINER)
        if not ok:
            unresolved.append("%s (%s)" % (student.email, message))

    # Record the shared URL on every member so the engine sees one group.
    # The same URL on multiple students is intended here, not a duplicate.
    for student in members:
        repo, _created = AssignmentRepo.objects.get_or_create(
            student=student, assignment=assignment)
        repo.repository_url = url
        repo.save(update_fields=['repository_url'])
        _sync_student_to_grading_db(student, assignment=assignment)

    for student in members:
        _notify_student_repo(course, student, assignment=assignment, repo_url=url)

    if unresolved:
        return ("Created repo '%s' but these members need attention: %s"
                % (repo_name, ", ".join(unresolved)))
    return True


def _provision_student_gitlab(course, student, assignment_name=None, assignment=None):
    """
    Create GitLab group + repo for a student (skips if they already exist).

    If the student belongs to a group on this assignment, delegates to
    _provision_group_gitlab so the whole group shares ONE repository — a group
    assignment. Otherwise the repo is per student: assignmentname-username
    (e.g. sql1-alice).

    Returns True on success, or an error string describing the failure so the
    caller can surface it to the faculty. The student's GitLab username is
    validated BEFORE the repo is created, so we never create a private repo
    that the student cannot access.

    New repositories are seeded with a README.md explaining how feedback is
    delivered and asking students not to commit personal information.

    The resulting URL is recorded on the (student, assignment) AssignmentRepo
    row — repositories are per assignment, not per course.
    """
    if assignment is None:
        return "No assignment supplied, so a repository cannot be created."
    assignment_name = assignment_name or assignment.name

    # Group assignment: one repo for the whole group.
    membership = (GroupMembership.objects
                  .filter(group__assignment=assignment, student=student)
                  .select_related('group')
                  .first())
    if membership is not None:
        return _provision_group_gitlab(course, assignment, membership.group)

    gitlab_url, gitlab_token = _get_gitlab_config(course)
    if not gitlab_url or not gitlab_token:
        return "GitLab is not configured for this course (missing URL or token)."

    # The row that owns the repo URL for this student + assignment.
    repo, _created = AssignmentRepo.objects.get_or_create(student=student, assignment=assignment)

    headers = {"PRIVATE-TOKEN": gitlab_token}
    api_base = "https://%s/api/v4" % gitlab_url

    # 0. Resolve the student's GitLab account BEFORE creating the repo. If it
    #    does not exist they would be locked out of a private repo, so fail
    #    loudly rather than creating one they cannot access.
    #
    #    The GitLab account defaults to the email prefix, which is the common
    #    convention. An explicitly set gitlab_username always wins, so a student
    #    whose GitLab handle differs can still be provisioned. The resolved name
    #    is stored, so the repo name and issue titles stay consistent.
    candidate = (student.gitlab_username or student.username or '').strip()
    if not candidate and not student.email:
        return ("Student '%s' has no email address or GitLab username, so their "
                "GitLab account cannot be determined." % student.email)

    # Resolve the account, distinguishing "no such account" from "the request
    # failed". Reporting the latter as the former sends the instructor looking for
    # the wrong problem.
    user, outcome, detail = _gitlab_lookup_user(
        api_base, headers, handle=candidate, email=student.email)

    if outcome == GITLAB_LOOKUP_ERROR:
        return ("Could not check GitLab for student '%s': %s" % (student.email, detail))

    if user is None:
        return ("No GitLab account found for student '%s' (tried %s) on %s. "
                "If their GitLab username differs from their email prefix, set it "
                "on the student record — the Repositories page has an 'Edit Repo' "
                "link that also edits the username. Otherwise the account may not "
                "exist on GitLab yet, or it may be invisible to this token."
                % (student.email, detail or ("'%s'" % candidate), gitlab_url))

    gitlab_user_id = user['id']
    # Store the name that actually resolved, so later steps and re-runs agree —
    # and so the instructor can see which account was matched.
    resolved_username = user.get('username') or candidate
    if student.gitlab_username != resolved_username:
        student.gitlab_username = resolved_username
        student.save(update_fields=['gitlab_username'])

    # 1. Find or create the course group
    group_id, group_name, group_created = _ensure_course_group(course, gitlab_url, gitlab_token)
    if group_id is None:
        return ("Failed to create GitLab group 'athina-%s' for course '%s'. Check "
                "the faculty GitLab token has permission to create groups." %
                (course.owner, course.name))

    # When the group is first created, add the faculty owner and any assigned TAs
    # so they have access to every repo in the course (including future ones).
    if group_created:
        _sync_course_group_members(course, gitlab_url, gitlab_token, group_id)

    # 2. Check if repo already exists, create if not
    prefix = re.sub(r'[^a-zA-Z0-9_-]', '-', assignment_name.lower()).strip('-') if assignment_name else 'assignment'
    prefix = re.sub(r'-+', '-', prefix)
    # Prefer the explicit GitLab username for repo naming; fall back to the derived username.
    repo_owner_name = student.gitlab_username or student.username
    repo_name = "%s-%s" % (prefix, repo_owner_name)

    # Check if the project already exists in this group
    check_resp = http_requests.get(
        "%s/projects/%s%%2F%s" % (api_base, group_name, repo_name),
        headers=headers, timeout=10)

    if check_resp.status_code == 200:
        # Repo already exists — record the URL, but still fall through to the
        # membership step. Returning here used to skip it entirely, so a repo
        # adopted from an earlier run was never shared with its student: they had
        # a repository listed in the UI that they could not open.
        project = check_resp.json()
        repo.repository_url = project.get('http_url_to_repo', '')
        repo.save(update_fields=['repository_url'])
    else:
        # Create the repo
        resp = http_requests.post("%s/projects" % api_base, headers=headers, data={
            "name": repo_name,
            "namespace_id": group_id,
            "visibility": "private",
        }, timeout=10)

        if not resp.ok:
            return ("Failed to create GitLab repo '%s' (HTTP %s). Check the faculty "
                    "GitLab token has permission to create projects in group '%s'." %
                    (repo_name, resp.status_code, group_name))

        project = resp.json()
        repo.repository_url = project.get('http_url_to_repo', '')
        repo.save(update_fields=['repository_url'])

        # Seed an initial commit (README.md) so the repo has a 'master' branch that
        # the grading engine can read. Best-effort — provisioning still succeeds if
        # it fails, and we never touch repos that already existed.
        _seed_repo_readme(gitlab_url, gitlab_token, project.get('id'), assignment)

    # Add the student, or raise them to Maintainer if they are already a member.
    #
    # Why Maintainer and not Developer: a new project protects `master` with
    # "Allowed to push: Maintainers", so a Developer cannot push to it. The
    # student could clone their repository and had no way to submit work to it.
    # Maintainer lets them push their own assignment. GitLab offers no
    # per-branch protection API here (that is Premium), so the access level is the
    # only lever — and it is per-project, scoped to one student's own repository.
    ok, message = _gitlab_ensure_project_member(
        api_base, headers, project['id'], gitlab_user_id, GITLAB_ACCESS_MAINTAINER)
    if not ok:
        return ("Created repo '%s' but could not give student '%s' access: %s. "
                "They cannot submit work until this is fixed."
                % (repo_name, student.gitlab_username, message))

    # 4. Optionally notify the student via Resend
    _notify_student_repo(course, student, assignment=assignment, repo_url=repo.repository_url)

    return True

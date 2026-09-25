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
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from .models import Assignment, Course, Student, AssignmentRepo
from .forms import (AssignmentForm, CourseForm, StudentForm, StudentEditForm,
                    StudentBulkForm, AssignmentRepoForm)
from athina_web.accounts.models import UserProfile
import os
import shutil
import logging
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
import dateutil.parser
import requests as http_requests
import threading
from athina_web.athina_db import db_info
from athina_web.accounts.models import UserProfile


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
            os.chmod("%s/%s" % (settings.BASE_DIR, assignment.absolute_path), 0o755)
            return redirect('filemanager:index', inner_path="%s" % assignment.name)
    else:
        assignment_id = kwargs.get('assignment_id', None)
        if assignment_id is not None:
            assignment = get_object_or_404(Assignment, pk=assignment_id)
            if assignment.owner != request.user.id and not request.user.is_superuser:
                raise Http404
            form = AssignmentForm(instance=assignment, user=request.user)
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
            users.append((user[0], user[1], user[2], color, info, user[6],
                          base64.b64encode(user[7] if user[7] is not None else b"").decode("ascii"),
                          None, None,
                          user[8], user[9], user[11] or '',
                          repo_url, issues_url))
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
                                                                "can_manage": _user_can_manage_courses(request.user)})


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


def connect_to_db():
    """Connect to the grading engine's MySQL database."""
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
        if result and result[0]:
            cur.execute("UPDATE users SET force_test=1, changed_state=1 WHERE course_id=%s AND assignment_id=%s AND repository_url=%s",
                        (course_id_val, assignment_id_val, result[0],))
        else:
            cur.execute("UPDATE users SET force_test=1, changed_state=1 WHERE course_id=%s AND assignment_id=%s AND user_id=%s",
                        (course_id_val, assignment_id_val, user_id,))
        conn.commit()
        conn.close()
        return redirect('assignments:assignment_view', assignment_id=assignment.pk)


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
        # Find the places that this git url has been used and update that it has been changed
        result = cur.execute('UPDATE users SET webhook_event=1 WHERE repository_url = %s AND webhook_token = %s',
                             (student_git_url, webhook_token,))
        conn.commit()
        conn.close()
    return HttpResponse('ok')


class APIView(generics.ListCreateAPIView):
    """This class defines the create behavior of our rest api."""
    queryset = Assignment.objects.filter(active=True)
    serializer_class = AssignmentListSerializer


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
    """Provision per-assignment GitLab repos and sync to the grading DB.

    Repositories belong to an assignment, not a course, so this is driven by the
    assignment: every student gets ``<assignment>-<gitlab_username>`` for THAT
    assignment. Re-running it is safe — an existing repo is adopted rather than
    recreated, and a student is only emailed once per assignment."""
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

    created = 0
    adopted = 0
    synced = 0
    notified = 0
    errors = []

    # Ensure the faculty owner and assigned TAs are members of the course group.
    # This covers groups created before this feature existed, and is cheap
    # (idempotent) when they are already members.
    members_ensured = 0
    gitlab_url, gitlab_token = _get_gitlab_config(course)
    if gitlab_url and gitlab_token:
        group_id, _group_name, _created = _ensure_course_group(course, gitlab_url, gitlab_token)
        if group_id is not None:
            members_ensured = _sync_course_group_members(course, gitlab_url, gitlab_token, group_id)

    students = list(Student.objects.filter(course=course))
    for assignment in assignments:
        for student in students:
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
                    # result is an error string — surface it to the faculty
                    errors.append("%s (%s): %s" % (student.email, assignment.name, result))
            else:
                # Repo already exists — still notify the student if we never did.
                adopted += 1
                result = _notify_student_repo(course, student, assignment=assignment)
                if result is True:
                    notified += 1
                elif result:
                    errors.append("%s (%s): %s" % (student.email, assignment.name, result))

            # Sync this assignment's row so the daemon sees the repo URL.
            _sync_student_to_grading_db(student, assignment=assignment)
            synced += 1

    label = assignments[0].name if len(assignments) == 1 else "%d assignments" % len(assignments)
    if errors:
        messages.error(request, "Some repositories could not be provisioned:<br>" + "<br>".join(errors))
    if created:
        messages.success(request, "Created %d new repo(s) for %s and synced %d student/assignment row(s)."
                         % (created, label, synced))
    elif not errors:
        messages.info(request, "All students already have repos for %s. Synced %d row(s) to the grading database."
                      % (label, synced))
    if adopted and not created:
        messages.info(request, "Adopted %d existing repo(s) for %s." % (adopted, label))
    if notified:
        messages.success(request, "Sent %d notification email(s) to students." % notified)
    if members_ensured:
        messages.info(request, "Ensured %d course member(s) (faculty/TAs) have group access." % members_ensured)

    return redirect('assignments:assignment_students', assignment_id=assignment.pk) \
        if assignment_id is not None else redirect('assignments:student_list', course_id=course.pk)


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
        "can_manage_students": bool(course) and _user_can_manage_students(request.user, course),
    })


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
    """Resolve a GitLab username to a user id. Returns None if not found."""
    if not username:
        return None
    try:
        resp = http_requests.get("%s/users" % api_base, headers=headers,
                                 params={"username": username}, timeout=10)
        if resp.ok and resp.json():
            return resp.json()[0]['id']
    except Exception:
        pass
    return None


def _gitlab_add_group_member(api_base, headers, group_id, username, access_level):
    """Add a user to a GitLab group if not already a member (idempotent).

    Returns True if the user is (now) a member, False otherwise.
    """
    user_id = _gitlab_find_user_id(api_base, headers, username)
    if user_id is None:
        return False
    try:
        member_check = http_requests.get(
            "%s/groups/%s/members" % (api_base, group_id),
            headers=headers, params={"per_page": 100}, timeout=10)
        existing_ids = [m['id'] for m in member_check.json()] if member_check.ok else []
        if user_id in existing_ids:
            return True
        resp = http_requests.post(
            "%s/groups/%s/members" % (api_base, group_id),
            headers=headers,
            data={"user_id": user_id, "access_level": access_level},
            timeout=10)
        return resp.ok
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


def sync_faculty_course_members(faculty_user):
    """Sync group membership for every course owned by a faculty member.

    Called when TA assignments change. Returns (courses_synced, members_ensured).
    """
    courses_synced = 0
    members_ensured = 0
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
    return courses_synced, members_ensured


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


def _provision_student_gitlab(course, student, assignment_name=None, assignment=None):
    """
    Create GitLab group + repo for a student (skips if they already exist).
    Repo naming: assignmentname-username (e.g. sql1-alice).

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
    if not candidate:
        return ("Student '%s' has no email address or GitLab username, so their "
                "GitLab account cannot be determined." % student.email)

    user_resp = http_requests.get("%s/users" % api_base, headers=headers,
                                  params={"username": candidate}, timeout=10)
    if not user_resp.ok or not user_resp.json():
        return ("Could not find a GitLab account '%s' for student '%s' on %s. "
                "If their GitLab username differs from their email prefix, set it "
                "on the student record. Otherwise the account must be created on "
                "GitLab first." % (candidate, student.email, gitlab_url))

    gitlab_user_id = user_resp.json()[0]['id']
    # Store the name that actually resolved, so later steps and re-runs agree.
    if student.gitlab_username != candidate:
        student.gitlab_username = candidate
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
        # Repo already exists — just record the URL
        project = check_resp.json()
        repo.repository_url = project.get('http_url_to_repo', '')
        repo.save(update_fields=['repository_url'])
        return True

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

    # 3. Add student as developer (skip if already a member)
    member_check = http_requests.get(
        "%s/projects/%s/members" % (api_base, project['id']),
        headers=headers, timeout=10)
    existing_ids = [m['id'] for m in member_check.json()] if member_check.ok else []
    if gitlab_user_id not in existing_ids:
        member_resp = http_requests.post("%s/projects/%s/members" % (api_base, project['id']),
                                         headers=headers, data={
                                             "user_id": gitlab_user_id,
                                             "access_level": GITLAB_ACCESS_DEVELOPER,
                                         }, timeout=10)
        if not member_resp.ok:
            return ("Created repo '%s' but failed to add student '%s' as a member "
                    "(HTTP %s). The student cannot access their private repo until "
                    "this is fixed." % (repo_name, student.gitlab_username, member_resp.status_code))

    # 4. Optionally notify the student via Resend
    _notify_student_repo(course, student, assignment=assignment, repo_url=repo.repository_url)

    return True

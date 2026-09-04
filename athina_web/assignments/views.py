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
from django.views.decorators.csrf import csrf_exempt
from .models import Assignment, Course, Student
from .forms import AssignmentForm, CourseForm, StudentForm, StudentEditForm, StudentBulkForm
from athina_web.accounts.models import UserProfile
import os
import shutil
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
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return user.is_superuser
    if user.is_superuser or profile.role == UserProfile.ROLE_ADMIN:
        return True
    if profile.role == UserProfile.ROLE_FACULTY and course.owner == user.id:
        return True
    if profile.role == UserProfile.ROLE_TA and course.owner_id in profile.managed_by.values_list('id', flat=True):
        return True
    return False


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


def _sync_student_to_grading_db(student):
    """Insert/update a student record in the grading engine's MySQL database.
    This is needed because the grading engine reads from the MySQL 'users' table,
    while the web app stores students in its own SQLite database.
    
    Reads course_id/assignment_id from the assignment YAML (the ground truth)
    so that the DB record always matches what the CLI will query."""
    try:
        conn = connect_to_db()
    except Exception:
        return  # grading DB not available — skip silently

    try:
        cur = conn.cursor()
        # Read IDs from the YAML (ground truth in the git repo)
        assignment = student.course.assignments.first()
        if assignment:
            course_id_val, assignment_id_val = _read_yaml_ids(assignment)
        else:
            course_id_val = student.course.pk
            assignment_id_val = student.course.pk

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
                (student.repository_url or '', student.email, student.username,
                 mysql_user_id, course_id_val, assignment_id_val))
        else:
            # Insert new record
            cur.execute(
                "INSERT INTO users (user_id, course_id, assignment_id, repository_url, "
                "secondary_id, user_fullname, url_date, new_url, commit_date) "
                "VALUES (%s, %s, %s, %s, %s, %s, NOW(), 1, '0001-01-01 00:00:00')",
                (student.pk, course_id_val, assignment_id_val,
                 student.repository_url or '', student.email, student.username))
        conn.commit()
    except Exception:
        pass  # best-effort — don't break the web app if grading DB has issues
    finally:
        conn.close()


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

    # Output mode settings
    lines.append("OUTPUT_METHOD=%s" % assignment.output_method)
    if assignment.output_method == 'gitlab_issues' and assignment.gitlab_project_id:
        lines.append("GITLAB_PROJECT_ID=%d" % assignment.gitlab_project_id)

    try:
        with open(env_path, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        os.chmod(env_path, 0o600)
    except OSError:
        pass


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
    })


@login_required
def assignment_create(request, **kwargs):
    """View for creating and editing model Assignment using Assignment Form"""
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
                # Provision GitLab repos for any existing students in this course
                # who don't have a repo yet (they were added before any assignment existed).
                if assignment.course:
                    for student in Student.objects.filter(course=assignment.course):
                        if not student.repository_url:
                            _provision_student_gitlab(assignment.course, student,
                                                      assignment_name=assignment.name)
                        # Ensure all students are synced to the grading DB
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
        return render(request, 'assignments/assignment_empty.html',
                      {"assignment": assignment,
                       "message": "No student submissions recorded yet. "
                                  "Students will appear here once they submit their repositories and grading has run."})

    return render(request, 'assignments/assignment_view.html', {"users": users, "users_len": len(users),
                                                                "assignment": assignment, "plagiarism_report": plagiarism_report,
                                                                "gitlab_project_id": assignment.gitlab_project_id,
                                                                "gitlab_output": assignment.output_method == 'gitlab_issues',
                                                                "gitlab_host": _get_owner_gitlab_host(assignment)})


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
    courses = Course.objects.filter(owner=request.user.id).order_by('name')
    return render(request, 'assignments/course_list.html', {"courses": courses})


@login_required
def course_create(request, **kwargs):
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
        "has_assignments": course.assignments.exists(),
    })


@login_required
def provision_students(request, course_id):
    """Provision GitLab repos and sync to grading DB for all students missing a repo."""
    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_access_course(request.user, course):
        raise Http404

    first_assignment = course.assignments.first()
    if not first_assignment:
        return redirect('assignments:student_list', course_id=course.pk)

    created = 0
    synced = 0
    for student in Student.objects.filter(course=course):
        if not student.repository_url:
            if _provision_student_gitlab(course, student, assignment_name=first_assignment.name):
                created += 1
        # Always sync to grading DB (handles students added before any assignment)
        _sync_student_to_grading_db(student)
        synced += 1

    if created:
        messages.success(request, "Provisioned %d new repo(s) and synced %d student(s) to the grading database." % (created, synced))
    else:
        messages.info(request, "All students already have repos. Synced %d student(s) to the grading database." % synced)

    return redirect('assignments:student_list', course_id=course.pk)


@login_required
def student_add(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_access_course(request.user, course):
        raise Http404
    if request.method == "POST":
        form = StudentForm(request.POST)
        if form.is_valid():
            student = form.save(commit=False)
            student.course = course
            student.save()
            # Only provision repos and sync to grading DB if the course has assignments.
            # There's nothing to grade if there are no assignments, so skip provisioning.
            if course.assignments.exists():
                first_assignment = course.assignments.first()
                assignment_name = first_assignment.name if first_assignment else course.name
                _provision_student_gitlab(course, student, assignment_name=assignment_name)
                _sync_student_to_grading_db(student)
            return redirect('assignments:student_list', course_id=course.pk)
    else:
        form = StudentForm()
    return render(request, 'assignments/student_add.html', {"course": course, "form": form})


# Module-level dict for tracking import progress across threads
_import_progress = {}


def _run_bulk_import(course_id, emails, assignment_name, has_assignments):
    """Background thread: imports students and provisions GitLab repos."""
    global _import_progress
    import logging
    logger = logging.getLogger('django')
    try:
        course = Course.objects.get(pk=course_id)
        created = 0
        total = len(emails)

        for i, email in enumerate(emails):
            username = email.split('@')[0]
            _import_progress[course_id] = {
                'total': total, 'current': i + 1, 'created': created,
                'status': 'running', 'current_student': email,
            }
            try:
                student, was_created = Student.objects.get_or_create(
                    course=course, email=email,
                    defaults={'username': username},
                )
                if was_created:
                    if has_assignments:
                        _provision_student_gitlab(course, student, assignment_name=assignment_name)
                        _sync_student_to_grading_db(student)
                    created += 1
                    logger.info("Imported %s" % email)
                else:
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
    if not _user_can_access_course(request.user, course):
        raise Http404
    if request.method == "POST":
        form = StudentBulkForm(request.POST)
        if form.is_valid():
            emails_raw = form.cleaned_data['emails']
            emails = [line.strip() for line in emails_raw.strip().splitlines()
                      if line.strip() and '@' in line.strip()]
            total = len(emails)
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
                args=(course.pk, emails, assignment_name, has_assignments),
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
    if not _user_can_access_course(request.user, course):
        raise Http404
    return render(request, 'assignments/import_progress.html', {"course": course})


@login_required
def import_progress_api(request, course_id):
    """JSON endpoint polled by the progress page."""
    from django.http import JsonResponse
    progress = _import_progress.get(course_id, None)
    if progress:
        return JsonResponse(progress)
    return JsonResponse({'status': 'idle', 'total': 0, 'current': 0})


@login_required
def student_edit(request, course_id, student_id):
    course = get_object_or_404(Course, pk=course_id)
    student = get_object_or_404(Student, pk=student_id, course=course)
    if not _user_can_access_course(request.user, course):
        raise Http404
    if request.method == "POST":
        form = StudentEditForm(request.POST, instance=student)
        if form.is_valid():
            form.save()
            _sync_student_to_grading_db(student)
            return redirect('assignments:student_list', course_id=course.pk)
    else:
        form = StudentEditForm(instance=student)
    return render(request, 'assignments/student_edit.html', {
        "course": course, "student": student, "form": form,
    })


@login_required
def student_delete(request, course_id, student_id):
    course = get_object_or_404(Course, pk=course_id)
    if not _user_can_access_course(request.user, course):
        raise Http404
    student = get_object_or_404(Student, pk=student_id, course=course)
    student.delete()
    return redirect('assignments:student_list', course_id=course.pk)


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


def _provision_student_gitlab(course, student, assignment_name=None):
    """
    Create GitLab group + repo for a student (skips if they already exist).
    Repo naming: assignmentname-username (e.g. sql1-alice).
    """
    gitlab_url, gitlab_token = _get_gitlab_config(course)
    if not gitlab_url or not gitlab_token:
        return False

    headers = {"PRIVATE-TOKEN": gitlab_token}
    api_base = "https://%s/api/v4" % gitlab_url

    # 1. Find or create the course group
    # Naming: athina-[facultyid]-[coursename]
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

    if group is None:
        resp = http_requests.post("%s/groups" % api_base, headers=headers, data={
            "name": "Athina - %s" % course.name,
            "path": group_name,
            "visibility": "private",
        }, timeout=10)
        if resp.ok:
            group = resp.json()
        else:
            return False

    group_id = group['id']

    # 2. Check if repo already exists, create if not
    prefix = re.sub(r'[^a-zA-Z0-9_-]', '-', assignment_name.lower()).strip('-') if assignment_name else 'assignment'
    prefix = re.sub(r'-+', '-', prefix)
    repo_name = "%s-%s" % (prefix, student.username)

    # Check if the project already exists in this group
    check_resp = http_requests.get(
        "%s/projects/%s%%2F%s" % (api_base, group_name, repo_name),
        headers=headers, timeout=10)

    if check_resp.status_code == 200:
        # Repo already exists — just record the URL
        project = check_resp.json()
        student.repository_url = project.get('http_url_to_repo', '')
        student.gitlab_username = student.username
        student.save()
        return True

    # Create the repo
    resp = http_requests.post("%s/projects" % api_base, headers=headers, data={
        "name": repo_name,
        "namespace_id": group_id,
        "visibility": "private",
    }, timeout=10)

    if not resp.ok:
        return False

    project = resp.json()
    student.repository_url = project.get('http_url_to_repo', '')
    student.gitlab_username = student.username
    student.save()

    # 3. Add student as developer (skip if already a member)
    if student.gitlab_username:
        user_resp = http_requests.get("%s/users" % api_base, headers=headers,
                                      params={"username": student.gitlab_username}, timeout=10)
        if user_resp.ok and user_resp.json():
            gitlab_user_id = user_resp.json()[0]['id']
            # Check if already a member
            member_check = http_requests.get(
                "%s/projects/%s/members" % (api_base, project['id']),
                headers=headers, timeout=10)
            existing_ids = [m['id'] for m in member_check.json()] if member_check.ok else []
            if gitlab_user_id not in existing_ids:
                http_requests.post("%s/projects/%s/members" % (api_base, project['id']),
                                   headers=headers, data={
                                       "user_id": gitlab_user_id,
                                       "access_level": 30,
                                   }, timeout=10)
    return True

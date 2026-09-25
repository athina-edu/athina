# accounts/views.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.contrib import messages
from .models import UserProfile
from .forms import FacultyCreateForm, TACreateForm, TAAssignForm
import requests as http_requests
import logging
import re
import secrets
import string


def _generate_password(length=16):
    """Generate a secure random password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pw = ''.join(secrets.choice(alphabet) for _ in range(length))
        # Ensure it has at least one of each type
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in "!@#$%^&*" for c in pw)):
            return pw


def _get_user_role(user):
    """Get the role of a user from their profile."""
    try:
        return user.profile.role
    except UserProfile.DoesNotExist:
        if user.is_superuser:
            return UserProfile.ROLE_ADMIN
        return UserProfile.ROLE_FACULTY


@login_required
def profile(request):
    """Display and edit the current user's GitLab, GitHub credentials, and password."""
    from django.contrib import messages as django_messages
    user_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        # GitLab
        user_profile.gitlab_enabled = request.POST.get('gitlab_enabled') == 'on'
        user_profile.gitlab_url = request.POST.get('gitlab_url', 'gitlab.com').strip() or 'gitlab.com'
        user_profile.gitlab_username = request.POST.get('gitlab_username', '').strip()
        user_profile.gitlab_token = request.POST.get('gitlab_token', '').strip()
        # GitHub
        user_profile.github_enabled = request.POST.get('github_enabled') == 'on'
        user_profile.github_username = request.POST.get('github_username', '').strip()
        user_profile.github_token = request.POST.get('github_token', '').strip()
        # LLM (AI Feedback)
        user_profile.llm_enabled = request.POST.get('llm_enabled') == 'on'
        user_profile.llm_endpoint_url = request.POST.get('llm_endpoint_url', 'https://api.openai.com/v1').strip() or 'https://api.openai.com/v1'
        user_profile.llm_api_key = request.POST.get('llm_api_key', '').strip()
        user_profile.llm_model = request.POST.get('llm_model', 'gpt-4o-mini').strip() or 'gpt-4o-mini'
        user_profile.notify_students = request.POST.get('notify_students') == 'on'
        user_profile.notification_api_key = request.POST.get('notification_api_key', '').strip()
        user_profile.notification_from_email = request.POST.get('notification_from_email', '').strip()
        user_profile.notification_reply_to = request.POST.get('notification_reply_to', '').strip()
        user_profile.save()

        # Refresh .env files for all assignments owned by this user
        from athina_web.assignments.views import _refresh_env_for_user
        _refresh_env_for_user(request.user.id)

        # Password change
        current_password = request.POST.get('current_password', '')
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')

        if new_password:
            if not current_password:
                django_messages.error(request, "Please enter your current password to change it.")
            elif not request.user.check_password(current_password):
                django_messages.error(request, "Current password is incorrect.")
            elif new_password != confirm_password:
                django_messages.error(request, "New passwords do not match.")
            elif len(new_password) < 8:
                django_messages.error(request, "New password must be at least 8 characters.")
            else:
                request.user.set_password(new_password)
                request.user.save()
                # Re-authenticate so the session doesn't expire
                from django.contrib.auth import login
                login(request, request.user)
                django_messages.success(request, "Password changed successfully.")

        return redirect('accounts:profile')
    active_providers = user_profile.get_active_providers()
    return render(request, 'accounts/profile.html', {
        "user_profile": user_profile,
        "active_providers": active_providers,
    })


@login_required
@login_required
def llm_models(request):
    """Fetch available models from the user's LLM endpoint."""
    import re as _re
    from urllib.parse import urlparse

    endpoint_url = request.GET.get('endpoint_url', '').strip().rstrip('/')
    api_key = request.GET.get('api_key', '').strip()

    if not endpoint_url or not api_key:
        return JsonResponse({"models": [], "error": "Enter endpoint URL and API key first."})

    # SSRF protection: reject private/internal URLs
    try:
        parsed = urlparse(endpoint_url)
        if parsed.scheme not in ('https', 'http'):
            return JsonResponse({"models": [], "error": "Only http/https URLs are allowed."}, status=400)
        hostname = parsed.hostname or ''
        BLOCKED = {'169.254.169.254', 'metadata.google.internal', '127.0.0.1', 'localhost', '0.0.0.0',
                    '169.254.170.2', 'fd00::ec2:internal'}
        if hostname in BLOCKED or _re.match(r'^(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|169\.254\.)', hostname):
            return JsonResponse({"models": [], "error": "Internal/private URLs are not allowed."}, status=400)
    except Exception:
        return JsonResponse({"models": [], "error": "Invalid URL format."}, status=400)

    try:
        url = "%s/models" % endpoint_url
        headers = {
            "Authorization": "Bearer %s" % api_key,
        }
        resp = http_requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return JsonResponse({"models": [], "error": "API returned status %d" % resp.status_code})

        data = resp.json()
        models = []
        for m in data.get('data', []):
            models.append({
                "id": m.get('id', ''),
                "owned_by": m.get('owned_by', ''),
            })
        # Sort by id
        models.sort(key=lambda x: x['id'])
        return JsonResponse({"models": models, "error": None})
    except Exception as e:
        return JsonResponse({"models": [], "error": str(e)})


@login_required
def test_resend(request):
    """Send a test email through Resend using the credentials in the POST body.

    Lets faculty verify their Resend API key and from-address before relying on
    student notifications. The email is sent to the current user's own address.
    """
    from athina_web.accounts.resend_email import send_email, ResendError

    api_key = request.POST.get('api_key', '').strip()
    from_email = request.POST.get('from_email', '').strip()
    reply_to = request.POST.get('reply_to', '').strip()
    to_email = request.POST.get('to_email', '').strip() or request.user.email

    if not api_key:
        return JsonResponse({"ok": False, "error": "Enter your Resend API key first."}, status=400)
    if not to_email:
        return JsonResponse({"ok": False, "error": "No recipient address available."}, status=400)

    try:
        message_id = send_email(
            api_key=api_key,
            to=to_email,
            subject="[Athina] Resend test email",
            text=("This is a test email from Athina.\n\n"
                  "If you received this, your Resend API key and sender address are "
                  "configured correctly and student notifications will be delivered."),
            html=("<p>This is a test email from <strong>Athina</strong>.</p>"
                  "<p>If you received this, your Resend API key and sender address are "
                  "configured correctly and student notifications will be delivered.</p>"),
            from_email=from_email or None,
            reply_to=reply_to or None,
        )
        return JsonResponse({"ok": True, "id": message_id, "to": to_email})
    except ResendError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


@login_required
def gitlab_repos(request):
    """Return repositories from the user's active Git hosting providers as JSON.
    Results are cached for 5 minutes to avoid hammering the API on every page load."""
    from django.core.cache import cache

    user_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    provider = request.GET.get('provider', 'gitlab')
    force_refresh = request.GET.get('refresh', '') == '1'

    cache_key = "repos_%d_%s" % (request.user.id, provider)
    cached = cache.get(cache_key) if not force_refresh else None
    if cached is not None:
        return JsonResponse(cached)

    if provider == 'gitlab':
        if not user_profile.gitlab_enabled or not user_profile.gitlab_token or not user_profile.gitlab_username:
            return JsonResponse({"repos": [], "error": "Enable GitLab and set credentials in your profile first."})
        headers = {"PRIVATE-TOKEN": user_profile.gitlab_token}
        api_base = "https://%s/api/v4" % user_profile.gitlab_url
        try:
            all_repos = []
            page = 1
            max_pages = 20
            while page <= max_pages:
                resp = http_requests.get(
                    "%s/projects" % api_base,
                    headers=headers,
                    params={"per_page": 100, "page": page, "order_by": "name", "sort": "asc",
                            "membership": "true", "simple": "true"},
                    timeout=30,
                )
                if not resp.ok:
                    return JsonResponse({"repos": [], "error": "GitLab API returned %d" % resp.status_code})
                batch = resp.json()
                if not batch:
                    break
                all_repos.extend(batch)
                if len(batch) < 100:
                    break
                page += 1
            repos = [{"name": p["name"], "http_url": p.get("http_url_to_repo", ""),
                      "full_path": p.get("path_with_namespace", p["name"]), "provider": "gitlab"}
                     for p in all_repos]
            result = {"repos": repos, "error": None}
            cache.set(cache_key, result, 300)  # cache 5 minutes
            return JsonResponse(result)
        except Exception as e:
            return JsonResponse({"repos": [], "error": str(e)})

    elif provider == 'github':
        if not user_profile.github_enabled or not user_profile.github_token:
            return JsonResponse({"repos": [], "error": "Enable GitHub and set credentials in your profile first."})
        headers = {
            "Authorization": "token %s" % user_profile.github_token,
            "Accept": "application/vnd.github.v3+json",
        }
        api_base = "https://api.github.com"
        try:
            all_repos = []
            page = 1
            max_pages = 20
            while page <= max_pages:
                resp = http_requests.get(
                    "%s/user/repos" % api_base,
                    headers=headers,
                    params={"per_page": 100, "page": page, "sort": "full_name",
                            "direction": "asc", "type": "all"},
                    timeout=30,
                )
                if not resp.ok:
                    return JsonResponse({"repos": [], "error": "GitHub API returned %d" % resp.status_code})
                batch = resp.json()
                if not batch:
                    break
                all_repos.extend(batch)
                if len(batch) < 100:
                    break
                page += 1
            repos = [{"name": r["name"], "http_url": r.get("clone_url", ""),
                      "full_path": r["full_name"], "provider": "github"}
                     for r in all_repos]
            result = {"repos": repos, "error": None}
            cache.set(cache_key, result, 300)
            return JsonResponse(result)
        except Exception as e:
            return JsonResponse({"repos": [], "error": str(e)})

    return JsonResponse({"repos": [], "error": "Unknown provider: %s" % provider})


# =========================================================================
#  User management views
# =========================================================================

@login_required
def user_list(request):
    """List users based on role. Admin sees all, Faculty sees TAs assigned to them."""
    user_profile = _get_user_profile_or_403(request.user)

    if request.user.is_superuser or user_profile.role == UserProfile.ROLE_ADMIN:
        users = User.objects.select_related('profile').exclude(
            id=request.user.id).order_by('username')
    elif user_profile.role == UserProfile.ROLE_FACULTY:
        # Faculty sees TAs whose profile.managed_by includes this faculty user
        users = User.objects.filter(
            profile__role=UserProfile.ROLE_TA,
            profile__managed_by=request.user
        ).select_related('profile').order_by('username')
    else:
        users = User.objects.none()

    # Tell the template who is reset/delete-able rather than re-deriving the
    # permission rules in template logic (easy to get wrong with `and`/`or`).
    manageable_ids = {u.pk for u in users if _can_manage_user(request.user, user_profile, u)}

    return render(request, 'accounts/user_list.html', {
        "users": users,
        "user_profile": user_profile,
        "manageable_ids": manageable_ids,
    })


@login_required
def create_user(request):
    """Create a new user (Faculty by admin, TA by faculty)."""
    user_profile = _get_user_profile_or_403(request.user)

    if request.user.is_superuser or user_profile.role == UserProfile.ROLE_ADMIN:
        # Admin can create Faculty (default) or TA (via ?role=ta)
        requested_role = request.GET.get('role', 'faculty')
        if requested_role == 'ta':
            form_class = TACreateForm
            target_role = UserProfile.ROLE_TA
            template = 'accounts/create_ta.html'
        else:
            form_class = FacultyCreateForm
            target_role = UserProfile.ROLE_FACULTY
            template = 'accounts/create_faculty.html'
    elif user_profile.role == UserProfile.ROLE_FACULTY:
        form_class = TACreateForm
        target_role = UserProfile.ROLE_TA
        template = 'accounts/create_ta.html'
    else:
        raise Http404

    if request.method == "POST":
        form = form_class(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            email = form.cleaned_data['email']
            # Defaults to the email prefix when left blank (see BaseUserCreateForm).
            gitlab_username = form.cleaned_data.get('gitlab_username') or email.split('@')[0]
            password = _generate_password()

            new_user = User.objects.create(
                username=username,
                email=email,
                password=make_password(password),
                is_active=True,
            )
            profile, _ = UserProfile.objects.get_or_create(user=new_user)
            profile.role = target_role
            profile.gitlab_username = gitlab_username
            profile.save()

            messages.success(request, "Account created successfully.")
            return render(request, 'accounts/user_created.html', {
                "new_user": new_user,
                "new_profile": profile,
                "password": password,
            })
    else:
        form = form_class()

    return render(request, template, {"form": form})


@login_required
def assign_tas(request):
    """Faculty: assign TAs to themselves."""
    user_profile = _get_user_profile_or_403(request.user)
    if user_profile.role != UserProfile.ROLE_FACULTY and not request.user.is_superuser:
        raise Http404

    if request.method == "POST":
        form = TAAssignForm(faculty_user=request.user, data=request.POST)
        if form.is_valid():
            # MultipleChoiceField yields strings; normalise to ints so the
            # membership test below compares like with like.
            selected_ids = set(int(i) for i in form.cleaned_data['tas'])
            selected_users = User.objects.filter(id__in=selected_ids)
            # Set managed_by on each TA's profile pointing to this faculty user
            for ta in selected_users:
                ta_profile, _ = UserProfile.objects.get_or_create(user=ta)
                ta_profile.managed_by.add(request.user)
            # Remove this faculty from any TA that was unselected
            for ta_profile in UserProfile.objects.filter(role=UserProfile.ROLE_TA):
                if ta_profile.user_id not in selected_ids:
                    ta_profile.managed_by.remove(request.user)

            # Propagate the change to GitLab: ensure the faculty owner and all
            # assigned TAs are members of each course group they own, so TAs can
            # access the student repositories.
            from athina_web.assignments.views import sync_faculty_course_members
            courses_synced, _members_ensured = sync_faculty_course_members(request.user)

            if courses_synced:
                messages.success(request, "TA assignments updated. Synced %d course "
                                          "group(s) on GitLab." % courses_synced)
            else:
                messages.success(request, "TA assignments updated.")
            return redirect('accounts:user_list')
    else:
        form = TAAssignForm(faculty_user=request.user)

    return render(request, 'accounts/assign_tas.html', {"form": form})


@login_required
def edit_user(request, user_id):
    """Edit a user's GitLab username (admin: anyone, faculty: their TAs).

    The GitLab username is what lets the grading/group-sync code add the user to
    course groups, so it is the one field worth correcting after creation.
    """
    user_profile = _get_user_profile_or_403(request.user)
    target = get_object_or_404(User, pk=user_id)

    if not _can_manage_user(request.user, user_profile, target):
        raise Http404

    target_profile = _profile_for(target)

    if request.method == "POST":
        gitlab_username = request.POST.get('gitlab_username', '').strip()
        if not gitlab_username:
            # Same convention as account creation: fall back to the email prefix.
            gitlab_username = target.email.split('@')[0]
        target_profile.gitlab_username = gitlab_username
        target_profile.save()
        messages.success(request, "Updated GitLab username for %s." % target.username)
        return redirect('accounts:user_list')

    return render(request, 'accounts/edit_user.html', {
        "target": target, "target_profile": target_profile,
    })


@login_required
def delete_user(request, user_id):
    """Delete a user (admin can delete anyone, faculty can delete their TAs)."""
    user_profile = _get_user_profile_or_403(request.user)
    target = get_object_or_404(User, pk=user_id)

    if not _can_manage_user(request.user, user_profile, target):
        raise Http404

    username = target.username
    target.delete()
    messages.success(request, "User %s deleted." % username)
    return redirect('accounts:user_list')


@login_required
def reset_password(request, user_id):
    """Reset another user's password.

    Admins may reset anyone; faculty may reset TAs assigned to them. A new
    random password is generated and shown once so it can be passed on.
    """
    user_profile = _get_user_profile_or_403(request.user)
    target = get_object_or_404(User, pk=user_id)

    # An admin resetting their own password via this page would be confusing;
    # they should use the profile page, which also verifies the old password.
    if target.pk == request.user.pk:
        messages.error(request, "Use your profile page to change your own password.")
        return redirect('accounts:user_list')

    if not _can_manage_user(request.user, user_profile, target):
        raise Http404

    if request.method != "POST":
        # Require a POST so the reset can't be triggered by a stray GET/link.
        return render(request, 'accounts/password_reset_confirm.html', {
            "target": target,
            "target_profile": _profile_for(target),
        })

    password = _generate_password()
    target.set_password(password)
    target.save(update_fields=['password'])

    # A password reset is a security event — leave an audit trail.
    logging.getLogger('athina_web').info(
        "Password reset for user '%s' (id=%s) by '%s' (id=%s)",
        target.username, target.pk, request.user.username, request.user.pk)

    return render(request, 'accounts/password_reset_done.html', {
        "target": target,
        "target_profile": _profile_for(target),
        "password": password,
    })


def _profile_for(user):
    """Return the user's profile, creating a default one if absent."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def _can_manage_user(actor, actor_profile, target):
    """Can `actor` manage (reset password / delete) `target`?

    Admins and superusers may manage anyone. Faculty may manage only TAs
    assigned to them. Everything else is denied.
    """
    if actor.is_superuser or actor_profile.role == UserProfile.ROLE_ADMIN:
        return True
    if actor_profile.role == UserProfile.ROLE_FACULTY:
        target_profile = _profile_for(target)
        return (target_profile.role == UserProfile.ROLE_TA and
                actor in target_profile.managed_by.all())
    return False


def _get_user_profile_or_403(user):
    """Get or create user profile, raise 404 if not faculty/admin."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if user.is_superuser or profile.role in (UserProfile.ROLE_ADMIN, UserProfile.ROLE_FACULTY, UserProfile.ROLE_TA):
        return profile
    raise Http404

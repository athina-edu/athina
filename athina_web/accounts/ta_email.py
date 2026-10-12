"""TA account invitation emails.

The faculty member composes one message and it is personalised per TA. The body
is a normal Python format string, so ``{name}``, ``{username}``, ``{password}``
and friends are substituted at send time.

Two hazards shape this module:

1. The template is stored verbatim and later passed to ``str.format``. A stray
   brace (common in prose, and in the placeholder hints we show the faculty)
   would raise ``KeyError``/``ValueError`` at send time — after they have already
   clicked "send". So composition never raises: unknown placeholders are left
   alone and malformed braces are tolerated.

2. ``{password}`` may be empty (see the caller): the plaintext only exists for a
   short window after reset. An empty password in an email is worse than an
   obvious placeholder, so it is rendered as a visible notice.
"""

import re

from athina_web.email_render import render_subject, safe_substitute

# Placeholders a faculty member may use. Also shown as guidance in the UI.
PLACEHOLDERS = (
    ('{name}', 'The TA\'s name (username, or email prefix if unset)'),
    ('{username}', 'Login username'),
    ('{password}', 'Temporary password'),
    ('{email}', 'The TA\'s email address'),
    ('{site_url}', 'Address of the Athina web interface'),
    ('{faculty_name}', 'Your name, as the person inviting them'),
    ('{course_list}', 'Names of the courses they are assisting with'),
)

# Shown when there is no password to send. Deliberately loud.
MISSING_PASSWORD_NOTICE = (
    "[[PASSWORD NOT AVAILABLE — click 'Reset & send' to generate a new one]]"
)

DEFAULT_SUBJECT = "Your Athina account for {course_list}"

DEFAULT_BODY = """\
Hello {name},

An Athina account has been created for you so you can help grade assignments
for {course_list}.

Sign in here: {site_url}

    Username: {username}
    Temporary password: {password}

You will be asked to choose a new password the first time you sign in.

If you have any questions, just reply to this message.

Thanks,
{faculty_name}
"""


def _safe_substitute(template, values):
    """Kept as a thin alias: substitution lives in athina_web.email_render.

    The logic is shared with the GitLab signup message, which has the same
    "user-authored template reaching str.format" hazard.
    """
    return safe_substitute(template, values)


def build_credentials_context(*, user, profile, site_url, faculty_name, courses=()):
    """The values substituted into the email template for one TA."""
    # Prefer a real name, then the login, then the email prefix. Accounts created
    # by `create_user` only ever have a username, so this usually lands on that.
    display_name = ((user.get_full_name() or '').strip()
                    or (user.username or '').strip()
                    or (user.email or '').split('@')[0]
                    or 'there')
    course_list = ", ".join(courses) if courses else "your courses"
    return {
        'name': display_name,
        'username': user.username,
        'email': user.email or '',
        'password': profile.temp_password or MISSING_PASSWORD_NOTICE,
        'site_url': site_url,
        'faculty_name': faculty_name,
        'course_list': course_list,
    }


def compose_ta_email(*, subject_template, body_template, context):
    """Render the subject and body for one TA. Never raises."""
    subject = render_subject(subject_template, context, default=DEFAULT_SUBJECT)
    body = safe_substitute(body_template or DEFAULT_BODY, context)
    return subject, body


def sample_context():
    """Example values so the faculty member can preview the message shape."""
    return {
        'name': 'Alex Rivera',
        'username': 'arivera',
        'email': 'arivera@university.edu',
        'password': 'Temp-Passw0rd!23',
        'site_url': 'https://athina.example.edu',
        'faculty_name': 'Prof. Jordan Lee',
        'course_list': 'CSCI 330, CSCI 424',
    }

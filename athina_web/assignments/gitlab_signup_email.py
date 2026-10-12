"""Emails asking a student to create a GitLab account.

Provisioning cannot create a repository for a student whose GitLab account does
not exist — the repository is private, so a repo without them as a member would
lock them out. The remedy is to ask the student to register, which is what this
message does.

Kept separate from `accounts/ta_email.py` because the audience, the call to
action, and the placeholders all differ.
"""

from athina_web.email_render import render_subject, safe_substitute

# Placeholders available in this template, shown as guidance in the UI.
PLACEHOLDERS = (
    ('{name}', "The student's name (or email prefix)"),
    ('{email}', "The student's email address"),
    ('{gitlab_url}', 'The GitLab server they must register on'),
    ('{course_list}', 'The courses they are enrolled in'),
    ('{faculty_name}', 'Your name, as the person asking'),
    ('{site_url}', 'Address of the Athina web interface'),
)

# GitLab offers no signup link that survives a private instance's settings, so
# the message points at the server root and names the account to register. The
# account name matters: provision matches on it, so registering under a
# different handle still fails.
DEFAULT_SUBJECT = "Action needed: create your GitLab account for {course_list}"

DEFAULT_BODY = """\
Hello {name},

Your course uses GitLab for assignment submissions, and we could not find a
GitLab account for you on {gitlab_url}.

Please create one here:

    {gitlab_url}

Use the username {email} if you can. If you choose a different username, reply
to this message so we can match your account.

You do not need to create any project or repository - we will set that up for
you once your account exists. After registering, let us know and your
repository will be created.

If you already have a GitLab account on {gitlab_url} under a different
username, reply with that username instead.

If you have any questions, just reply to this message.

Thanks,
{faculty_name}
"""


def build_context(*, student, gitlab_url, faculty_name, site_url, courses=()):
    """Values substituted into the message for one student."""
    display_name = ((student.username or '').strip()
                    or (student.email or '').split('@')[0]
                    or 'there')
    return {
        'name': display_name,
        'email': student.email or '',
        'gitlab_url': gitlab_url,
        'course_list': ", ".join(courses) if courses else "your course",
        'faculty_name': faculty_name,
        'site_url': site_url,
    }


def compose(subject_template, body_template, context):
    """Render the subject and body for one student. Never raises."""
    subject = render_subject(subject_template, context, default=DEFAULT_SUBJECT)
    body = safe_substitute(body_template or DEFAULT_BODY, context)
    return subject, body


def sample_context():
    """Example values for the preview."""
    return {
        'name': 'Alex Rivera',
        'email': 'arivera@university.edu',
        'gitlab_url': 'gitlab.example.edu',
        'course_list': 'CSCI 330',
        'faculty_name': 'Prof. Jordan Lee',
        'site_url': 'https://athina.example.edu',
    }

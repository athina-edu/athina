"""Safe rendering of user-authored email templates.

Faculty members compose messages with ``{placeholder}`` tokens, which are stored
verbatim and substituted per recipient at send time. The template is therefore
user input that reaches ``str.format``, and ``str.format`` raises on:

* an unknown placeholder (``KeyError``) — an easy typo;
* a stray brace in prose (``ValueError``) — e.g. "use {curly braces}";
* a positional token (``IndexError``) — ``{0}``.

Any of those would surface *after* the faculty member pressed send, failing the
whole run. So substitution never raises: it tries the normal path and, if that
fails, replaces only the placeholders we know about and leaves everything else
untouched.
"""

import re


def safe_substitute(template, values):
    """Substitute ``{placeholders}`` in `template` without ever raising."""
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        result = template
        for key, value in values.items():
            result = re.sub(r'\{' + re.escape(key) + r'\}',
                            lambda _m, v=value: str(v), result)
        return result


def render_subject(template, values, default=''):
    """Render a subject line, collapsed to a single line.

    A newline in a subject makes the email header invalid, so any whitespace is
    flattened regardless of what the template contained.
    """
    subject = safe_substitute(template or default, values)
    return " ".join(subject.split())

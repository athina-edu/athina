"""Resend (https://resend.com) email delivery helper.

Resend is a transactional email API. Sending an email is a single POST to
https://api.resend.com/emails with a Bearer token.

This module is intentionally dependency-light: it uses ``requests`` (already a
project dependency) rather than pulling in the official ``resend`` SDK, so it
works inside the grading/web containers without extra installs.
"""

import logging

import requests

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"

# Resend's shared sandbox sender. Works without a verified domain, but can only
# deliver to the account owner's own address. Users should set their own
# verified ``from`` address for real student delivery.
DEFAULT_FROM = "Athina <onboarding@resend.dev>"


class ResendError(Exception):
    """Raised when the Resend API rejects a request."""


def send_email(api_key, to, subject, text=None, html=None, from_email=None,
               reply_to=None, timeout=15):
    """Send a single email through Resend.

    Args:
        api_key: Resend API key (``re_...``). Required.
        to: Recipient address, or a list of addresses.
        subject: Email subject line.
        text: Plain-text body (at least one of ``text``/``html`` required).
        html: HTML body.
        from_email: Sender. Defaults to Resend's sandbox sender.
        reply_to: Optional reply-to address.
        timeout: HTTP timeout in seconds.

    Returns:
        The Resend message id (str) on success.

    Raises:
        ResendError: if the API key is missing, the payload is invalid, or the
            API returns a non-2xx response.
    """
    if not api_key:
        raise ResendError("No Resend API key configured.")
    if not to:
        raise ResendError("No recipient address provided.")
    if not text and not html:
        raise ResendError("Email must have a text or html body.")

    recipients = [to] if isinstance(to, str) else list(to)

    payload = {
        "from": from_email or DEFAULT_FROM,
        "to": recipients,
        "subject": subject,
    }
    if text:
        payload["text"] = text
    if html:
        payload["html"] = html
    if reply_to:
        payload["reply_to"] = reply_to

    try:
        resp = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": "Bearer %s" % api_key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise ResendError("Could not reach Resend: %s" % exc) from exc

    if resp.status_code >= 300:
        # Resend returns {"statusCode":..., "message":..., "name":...}
        detail = resp.text
        try:
            body = resp.json()
            detail = body.get("message") or body.get("error") or detail
        except ValueError:
            pass
        raise ResendError("Resend API error (HTTP %s): %s" % (resp.status_code, detail))

    try:
        return resp.json().get("id", "")
    except ValueError:
        return ""


def send_email_safe(api_key, to, subject, text=None, html=None, from_email=None,
                    reply_to=None):
    """Like :func:`send_email` but never raises — logs failures instead.

    Use this on non-critical paths (e.g. provisioning a batch of repos) where a
    mail failure must not abort the surrounding operation.
    """
    try:
        return send_email(api_key, to, subject, text=text, html=html,
                          from_email=from_email, reply_to=reply_to)
    except ResendError as exc:
        logger.warning("Resend delivery to %s failed: %s", to, exc)
        return None

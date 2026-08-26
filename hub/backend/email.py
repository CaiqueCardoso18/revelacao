"""Sending transactional email (password reset, verification).

No real provider is wired in yet -- that needs an account+API key from
something like Resend/Postmark/SES that only the project owner can create.
Until EMAIL_PROVIDER is set, this just logs the email so the reset/verify
flow is fully testable end-to-end without a real inbox.
"""

import logging
import os

logger = logging.getLogger("revelacao.email")

EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER") or None  # e.g. "resend" -- unset/empty means "log only"


def send_email(to: str, subject: str, body: str):
    if EMAIL_PROVIDER is None:
        logger.warning("EMAIL NOT SENT (no provider configured) -- to=%s subject=%r\n%s", to, subject, body)
        return

    raise NotImplementedError(
        f"EMAIL_PROVIDER={EMAIL_PROVIDER!r} is set but no integration for it exists yet. "
        "Add the API call here once a provider + API key are chosen."
    )

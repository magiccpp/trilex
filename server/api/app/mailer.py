"""Outbound mail.

The 'smtp' backend talks to the internal mail container over the private Docker
network; that container is never published to the internet, so this service has
a mail relay without the host running a public SMTP listener.

The 'log' backend prints the code to stdout, which keeps auth fully working
before DNS and DKIM are in place.
"""
import logging, smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import settings

log = logging.getLogger("thriauga.mail")

SUBJECT = "Your Þríauga sign-in code"

BODY = """\
Your sign-in code is:

    {code}

It expires in {minutes} minutes and can be used once.

If you did not ask to sign in to Þríauga, ignore this message -
someone typed your address by mistake, and no account was changed.
"""


def _message(to: str, code: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = SUBJECT
    msg["From"] = formataddr((settings.MAIL_FROM_NAME, settings.MAIL_FROM))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=settings.MAIL_FROM.split("@")[-1])
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(BODY.format(code=code, minutes=settings.CODE_TTL_SEC // 60))
    return msg


def send_code(to: str, code: str) -> bool:
    if settings.MAIL_BACKEND == "log":
        log.warning("[auth] code for %s -> %s (valid %ds)",
                    to, code, settings.CODE_TTL_SEC)
        return True
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as s:
            if settings.SMTP_STARTTLS:
                s.starttls()
            if settings.SMTP_USER:
                s.login(settings.SMTP_USER, settings.SMTP_PASS)
            s.send_message(_message(to, code))
        log.info("[auth] code mailed to %s", to)
        return True
    except Exception as exc:
        # Never surface the reason to the caller - that would let someone probe
        # which addresses exist. Operators see it in the container log.
        log.error("[auth] send failed for %s: %s", to, exc)
        return False

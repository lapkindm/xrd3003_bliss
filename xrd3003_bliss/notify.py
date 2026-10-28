"""E-mail notifications for long measurements.

Same form as xeuss_bliss/notify.py on xeuss20 (2026-10-28): recipients first,
description optional, mail by SMTP to the local Postfix (localhost:25) with an
explicit From address. The control PC delivers mail itself (Postfix, loopback
only; tested to uni-tuebingen.de on 2026-10-10). A long program wrapped in
``notifying`` sends one e-mail when it finishes and one when it stops on an
error; Ctrl-C sends nothing (you stopped it yourself)::

    from xrd3003_bliss.notify import notifying
    with notifying("a@x.de, b@y.de", "spatial scan -25..25") as status:
        for j, pos in enumerate(positions):
            status["point"] = f"{j + 1}/{len(positions)} (y = {pos:g})"
            ...

Recipients are given per run (``to``): they get both e-mails. Error e-mails
also always go to ERRORS_ALWAYS_TO, which the instrument's session sets
(e.g. in its session script)::

    import xrd3003_bliss.notify
    xrd3003_bliss.notify.ERRORS_ALWAYS_TO = "responsible@example.org"
    xrd3003_bliss.notify.MAIL_DOMAIN = "host.example.org"   # sender <user>@MAIL_DOMAIN

MAIL_DOMAIN is the Postfix myhostname/myorigin (main.cf), needed when the host
itself only knows its short name; without it the e-mail has no From header
and Postfix fills in the sender.
"""

from __future__ import annotations

import contextlib
import datetime
import getpass
import smtplib
import socket
import traceback
from email.message import EmailMessage

try:  # Bliss turns Ctrl-C inside a scan into ScanAbort (scan.py, 2.4: only that)
    from bliss.scanning.scan import ScanAbort
except ImportError:  # pragma: no cover
    class ScanAbort(BaseException):
        pass

SMTP_HOST = "localhost"
SMTP_PORT = 25
MAIL_DOMAIN: str | None = None  # Postfix myhostname/myorigin, for the From header (set by the session)
ERRORS_ALWAYS_TO: str | None = None  # every error e-mail also goes here (set by the session)
SUBJECT_PREFIX = "[XRD-3003TT]"


def recipients(to: str | None, error: bool) -> str:
    """The addresses of one e-mail: ``to`` (comma-separated), plus
    ERRORS_ALWAYS_TO for an error e-mail; duplicates removed."""
    addresses = [a.strip() for a in (to or "").split(",") if a.strip()]
    if error and ERRORS_ALWAYS_TO:
        addresses.insert(0, ERRORS_ALWAYS_TO)
    unique: list[str] = []
    for a in addresses:
        if a.lower() not in (u.lower() for u in unique):
            unique.append(a)
    return ", ".join(unique)


def notify(subject: str, text: str, to: str | None = None, error: bool = False) -> bool:
    """Send one e-mail to ``to`` (plus ERRORS_ALWAYS_TO if ``error``). Never
    raises: a failure is printed and returns False, so a mail problem cannot
    stop a measurement."""
    try:
        to = recipients(to, error)
        if not to:
            return False
        msg = EmailMessage()
        if MAIL_DOMAIN:
            msg["From"] = f"{getpass.getuser()}@{MAIL_DOMAIN}"
        msg["To"] = to
        msg["Subject"] = f"{SUBJECT_PREFIX} {subject}"
        msg.set_content(text)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"Could not send the notification e-mail ({exc})")
        return False


def _context() -> list[str]:
    """Where the data goes and which scan ran last."""
    lines = []
    try:
        from bliss import current_session

        lines.append(f"Data file: {current_session.scan_saving.filename}")
        scans = current_session.scans
        if scans:
            s = scans[-1]
            lines.append(f"Last scan: #{s.scan_number} {s.scan_info.get('title', s.name)}")
    except Exception:  # noqa: BLE001
        pass
    return lines


@contextlib.contextmanager
def notifying(to: str | None, what: str | None = None):
    """Send an e-mail when the wrapped program finishes (to ``to``) or stops
    on an error (to ``to`` and ERRORS_ALWAYS_TO). ``to``: address(es), comma-
    separated (None: only error e-mails to ERRORS_ALWAYS_TO); ``what``: optional
    description for the subject, default "measurement started <day time>".
    Yields a dict: what the program puts in it (e.g. "point") is reported.

        with notifying("a@x.de"):
            sct(36000)
    """
    status: dict = {}
    start = datetime.datetime.now()
    if not what:
        what = f"measurement started {start:%a %d %b %H:%M}"
    host = socket.gethostname()

    def body(head: str) -> str:
        end = datetime.datetime.now()
        lines = [head, "", f"Program: {what}",
                 f"Started: {start:%a %d %b %H:%M}, ended: {end:%a %d %b %H:%M} "
                 f"(after {str(end - start).split('.')[0]})"]
        lines += [f"{k.capitalize()}: {v}" for k, v in status.items()]
        lines += _context() + ["", f"-- {host}"]
        return "\n".join(lines)

    try:
        yield status
    except (KeyboardInterrupt, ScanAbort, GeneratorExit):
        raise  # stopped by hand (Ctrl-C, also inside a scan), or Python cleaning up: no e-mail
    except BaseException as exc:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-6:])
        notify(f"STOPPED: {what}",
               body(f"The measurement stopped on an error:\n\n{type(exc).__name__}: {exc}")
               + f"\n\nEnd of the traceback:\n{tb}", to=to, error=True)
        raise
    else:
        notify(f"Finished: {what}", body("The measurement finished normally."), to=to)

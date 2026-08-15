"""Publishing a settlement schedule to a link an insurer can open.

Three decisions are baked in here and each of them matters more than it looks.

**The link serves a frozen copy.** Publishing renders the schedule and stores
the HTML. If the link served live data, an assessor adjusting a figure next
week would silently change the document already sitting on an insurer's claim
file, and it would be useless in a dispute. Changing a figure means publishing
version 2; version 1 remains exactly as it was sent, and both stay listed.

**The passcode is optional but hashed.** These documents carry a claimant's
name, address and photographs of their home. A guessable URL is the only thing
between that and the open internet, so the slug is 18 bytes of entropy and a
passcode can be added on top. The passcode is hashed with the same context the
application uses for user passwords — never stored in the clear.

**Every open is recorded.** The assessor wants to answer "did the insurer ever
look at this". Failed passcode attempts are recorded too: repeated failures on
a link sent to one recipient is worth seeing.
"""
from __future__ import annotations

import datetime as dt
import re

DEFAULT_EXPIRY_DAYS = 90

# Anything that only makes sense inside the application: the print toolbar,
# links back into the claim, the CSV export. A published copy is a document,
# not a page with navigation.
_TOOLBAR = re.compile(r'<div class="toolbar">.*?</div>', re.DOTALL)


def strip_app_chrome(html: str) -> str:
    """Remove in-app controls from a rendered report before freezing it."""
    return _TOOLBAR.sub("", html, count=1)


def normalise_passcode(raw: str) -> str:
    """Passcodes get read down a phone line. Case and spacing should not matter."""
    return re.sub(r"\s+", "", (raw or "")).upper()


def hash_passcode(pwd_context, raw: str) -> str:
    raw = normalise_passcode(raw)
    return pwd_context.hash(raw) if raw else ""


def check_passcode(pwd_context, share, raw: str) -> bool:
    if not share.passcode_hash:
        return True
    try:
        return pwd_context.verify(normalise_passcode(raw), share.passcode_hash)
    except Exception:  # noqa: BLE001 - a malformed hash must not 500 the page
        return False


def expiry_from_days(days: int | None) -> dt.datetime | None:
    if not days:
        return None
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=int(days))


def next_version(existing: list) -> int:
    return max((s.version or 0 for s in existing), default=0) + 1


def client_ip(request) -> str:
    """Best-effort caller address behind Railway's proxy.

    Stored to help an assessor recognise repeat access, not to identify an
    individual. Only the first hop is kept and the field is capped.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def describe_viewer(request) -> dict:
    return {
        "ip": client_ip(request),
        "user_agent": (request.headers.get("user-agent") or "")[:400],
        "referrer": (request.headers.get("referer") or "")[:400],
    }


def summarise_agent(agent: str) -> str:
    """A readable label for the access log rather than a raw UA string."""
    a = (agent or "").lower()
    if not a:
        return "Unknown"
    platform = (
        "iPhone" if "iphone" in a else
        "iPad" if "ipad" in a else
        "Android" if "android" in a else
        "Mac" if "macintosh" in a or "mac os" in a else
        "Windows" if "windows" in a else
        "Linux" if "linux" in a else "Unknown"
    )
    browser = (
        "Edge" if "edg/" in a else
        "Chrome" if "chrome" in a and "chromium" not in a else
        "Firefox" if "firefox" in a else
        "Safari" if "safari" in a else "Browser"
    )
    return f"{browser} on {platform}"

"""Shared result, error and time helpers.

Every command prints exactly one JSON object on stdout. Exit codes follow deploy.py:
0 done, 1 usage error, 2 blocked (investigate), 3 waiting for a person.
"""

from datetime import datetime, timezone
import json
import os
import sys

OK, USAGE, BLOCKED, WAITING = 0, 1, 2, 3
PYTHON = "py -3" if os.name == "nt" else "python3"
CLI = f"{PYTHON} .github/skills/m365-demo-seed/scripts/m365demo.py"


class Stop(Exception):
    """A handled stop: reported as one JSON object with `reason` and `message`."""

    code = BLOCKED

    def __init__(self, reason, message, **extra):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.extra = extra

    def report(self):
        return {"ok": False, "reason": self.reason, "message": self.message, **self.extra}


class Blocked(Stop):
    code = BLOCKED


class Waiting(Stop):
    """A person must act (sign in, type passwords, grant consent) before this can continue."""

    code = WAITING


class Usage(Stop):
    code = USAGE


def emit(obj, stream=None):
    # ASCII-only JSON: Windows pipes use the ANSI code page, which cannot encode every name.
    stream = stream or sys.stdout
    stream.write(json.dumps(obj, ensure_ascii=True, separators=(",", ":")) + "\n")
    stream.flush()


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(text):
    """An ISO 8601 timestamp as an aware datetime, or None."""
    if not text:
        return None
    try:
        value = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

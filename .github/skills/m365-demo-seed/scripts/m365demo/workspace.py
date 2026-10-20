"""The local workspace: one owner-only home for demos, passwords and runs, never the repository.

    <home>/                     ~/.copilot/m365-demo-seed (M365_DEMO_HOME overrides it)
      demo-users.json           demo users' passwords, 0600 (typed by the user, never in chat)
      demos/<name>/demo.json    the authored demo content (no secrets)
      runs/<run id>/            one folder per seed, cleanup or restore run
      run.lock                  one run at a time (they relax and restore the same tenant settings)

Folders are 0700 and files 0600 on macOS and Linux; on Windows the user's profile ACLs apply.
"""

import json
import os
from pathlib import Path
import re
import tempfile
import time

from .common import Blocked, Usage, utc_now
from . import proc

HOME_ENV = "M365_DEMO_HOME"
IS_WINDOWS = os.name == "nt"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def home():
    override = os.environ.get(HOME_ENV)
    return Path(override).expanduser() if override else Path.home() / ".copilot" / "m365-demo-seed"


def private_dir(path):
    """Create a folder, and any missing folders between the home and it, owner-only."""
    path = Path(path)
    root = home()
    folders = [path]
    if root in path.parents:
        folders = [root, *[parent for parent in reversed(path.parents) if root in parent.parents], path]
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
        if not IS_WINDOWS:
            os.chmod(folder, 0o700)
    return path


def ensure_home():
    return private_dir(home())


def _shared(action, tries=15):
    """Run a file operation, retrying briefly while Windows reports the file open in another process.

    Windows refuses to replace or delete a file another process is reading (for example `monitor`
    reading status.json, or an indexer); the reader lets go within moments.
    """
    for attempt in range(tries):
        try:
            return action()
        except PermissionError:
            if not IS_WINDOWS or attempt == tries - 1:
                raise
            time.sleep(min(0.5, 0.05 * (attempt + 1)))
    return None


def remove(path, *, scrub=False):
    """Delete a file if it exists; with scrub, empty it when Windows will not let it be deleted."""
    path = Path(path)
    try:
        _shared(lambda: path.unlink(missing_ok=True))
    except PermissionError:
        if scrub:
            try:
                path.write_bytes(b"")
            except OSError:
                pass


def write_private(path, data):
    """Write a file atomically, owner-only from its first byte."""
    path = Path(path)
    private_dir(path.parent)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _shared(lambda: os.replace(temporary, path))
        if not IS_WINDOWS:
            os.chmod(path, 0o600)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def write_json(path, obj):
    return write_private(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def read_json(path, default=None):
    try:
        text = _shared(lambda: Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default
    try:
        return json.loads(text)
    except ValueError as error:
        raise Blocked("unreadable-file", f"{path} is not valid JSON (line {error.lineno}).") from None


def check_name(name, what="name"):
    if not isinstance(name, str) or not NAME.match(name):
        raise Usage("bad-name", f"Use a short {what} of lowercase letters, digits and '-': {name!r}.")
    return name


def demos_dir():
    return home() / "demos"


def demo_dir(name):
    return demos_dir() / check_name(name, "demo name")


def users_file():
    return home() / "demo-users.json"


def runs_dir():
    return home() / "runs"


def lock_file():
    return home() / "run.lock"


def read_lock():
    """The active run's lock record, or None. A lock whose process is gone is removed."""
    path = lock_file()
    try:
        record = read_json(path)
    except Blocked:
        # Unreadable: a writer killed mid-write long ago, or one writing right now.
        try:
            age = time.time() - path.stat().st_mtime
        except FileNotFoundError:
            return None
        if age < 60:
            return {"runId": "unknown", "kind": "unknown"}
        remove(path)
        return None
    if not record:
        return None
    if proc.alive(record.get("pid")):
        return record
    remove(path)
    return None


def acquire_lock(run_id, kind):
    ensure_home()
    path = lock_file()
    for _ in range(2):
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            active = read_lock()
            if active:
                raise Blocked("another-run", f"Another {active.get('kind', 'demo')} run is active "
                              f"({active.get('runId')}): one run at a time, because every run relaxes and "
                              "restores the same tenant settings.", activeRun=active.get("runId")) from None
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "runId": run_id, "kind": kind, "startedAt": utc_now()}, handle)
        return path
    raise Blocked("lock-failed", f"Could not take the run lock {path}.")


def set_lock_pid(run_id, pid):
    """Hand the lock to the process that does the run (the detached worker)."""
    record = read_json(lock_file())
    if record and record.get("runId") == run_id and record.get("pid") != pid:
        write_json(lock_file(), {**record, "pid": pid})


def release_lock(run_id):
    record = read_json(lock_file())
    if record and record.get("runId") == run_id:
        remove(lock_file())

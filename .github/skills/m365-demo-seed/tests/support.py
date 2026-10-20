"""Shared test setup: an isolated workspace, the fake tenant, and running the CLI like an agent does."""

import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

TESTS = Path(__file__).resolve().parent
SKILL = TESTS.parent
SCRIPTS = SKILL / "scripts"
CLI = SCRIPTS / "m365demo.py"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

from fake_tenant import ADMIN, FakeTenant  # noqa: E402
from m365demo import auth, proc, sample, workspace  # noqa: E402

ISOLATED = ("M365_DEMO_HOME", "M365_DEMO_LOGIN_URL", "M365_DEMO_GRAPH_URL", "M365_DEMO_TIME_SCALE")


class WorkspaceCase(unittest.TestCase):
    """Each test gets its own workspace home; the real ~/.copilot is never touched."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        clean = {key: value for key, value in os.environ.items() if key not in ISOLATED}
        self._env = mock.patch.dict(os.environ, {**clean, "M365_DEMO_HOME": str(self.home)}, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def write_demo(self, name, people, admin=ADMIN, **extra):
        """The template's sample story for these demo users, written as the demo `name`."""
        doc = sample.build(admin, [(upn, None) for upn in people])
        doc.update(extra)
        folder = self.home / "demos" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "demo.json").write_text(json.dumps(doc), encoding="utf-8")
        return doc

    def fabricate_run(self, run_id, doc, *, kind="seed", tenant_id="11111111-1111-4111-8111-111111111111",
                      outcome="seeded", created=True, tenant_phase=None, state="finished"):
        """A finished run folder as the worker leaves it, without seeding anything."""
        folder = self.home / "runs" / run_id
        workspace.write_json(folder / "run.json", {"runId": run_id, "kind": kind, "demo": doc.get("name", "launch"),
                                                   "tenantId": tenant_id, "adminUpn": doc["adminUpn"]})
        workspace.write_json(folder / "status.json", {"state": state, "pid": None})
        if kind == "seed":
            workspace.write_json(folder / "demo.json", doc)
            items = {}
            if created:
                items = {f"file:{f['key']}": {"status": "done", "itemId": f"item-{f['key']}", "owner": "x",
                                              "name": f["name"]} for f in doc.get("files") or []}
                items.update({f"mail:{t['key']}": {"status": "done", "sent": [{"index": 0, "conversationId": "c"}]}
                              for t in doc.get("mail") or []})
                items.update({f"meeting:{m['key']}": {"status": "done", "eventId": "e", "subject": m["subject"]}
                              for m in doc.get("meetings") or []})
            workspace.write_json(folder / "manifest.json", {"version": 1, "runId": run_id, "items": items})
        if outcome:
            workspace.write_json(folder / "result.json", {"outcome": outcome, "guide": str(folder / "guide.html")})
        if tenant_phase:
            workspace.write_json(folder / "tenant_state.json", {"version": 1, "runId": run_id, "phase": tenant_phase})
        return folder

    def store_passwords(self, passwords):
        workspace.write_json(workspace.users_file(), {"version": 1, "users": {
            upn: {"password": value, "setAt": "now"} for upn, value in passwords.items()}})


class TenantCase(WorkspaceCase):
    """A workspace plus a running fake tenant, with the tool pointed at it and its waits removed."""

    time_scale = "0"

    def setUp(self):
        super().setUp()
        self.fake = FakeTenant().start()
        self.addCleanup(self.fake.stop)
        os.environ.update(self.fake.env())
        os.environ["M365_DEMO_TIME_SCALE"] = self.time_scale

    def receipt_file(self, **overrides):
        return self.fake.receipts_file(self.tmp / "status.json", **overrides)

    def session(self, upn, password):
        """A signed-in session for a fake user (the fake allows password sign-in for the admin too)."""
        session, verdict = auth.password_sign_in(self.fake.receipt(), upn, password)
        if not session:
            raise AssertionError(f"{upn} could not sign in: {verdict}")
        session.account, session.user_id = upn, self.fake.users[upn]["id"]
        return session


class WorkerCase(TenantCase):
    """End-to-end runs: detached workers against the fake tenant, with waits scaled to 1%."""

    time_scale = "0.01"

    def setUp(self):
        super().setUp()
        self.fake.device_interval = 1
        self.addCleanup(self.stop_workers)

    def stop_workers(self):
        """Never leave a worker running past its test (it would talk to a stopped fake tenant)."""
        root = self.home / "runs"
        if not root.is_dir():
            return
        for folder in root.iterdir():
            status = workspace.read_json(folder / "status.json", default={}) or {}
            pid = status.get("pid")
            if not proc.alive(pid):
                continue
            (folder / "cancel").write_text("stop", encoding="utf-8")
            self.fake.hold_sign_in.set()
            deadline = time.monotonic() + 15
            while proc.alive(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            if proc.alive(pid) and os.name != "nt":
                os.kill(pid, signal.SIGKILL)

    def wait_for(self, run_id, states, timeout=60):
        """Poll `monitor` until the run reaches one of `states`; returns (exit code, report)."""
        deadline = time.monotonic() + timeout
        while True:
            code, out, _ = run_cli("monitor", "--run", run_id)
            if out.get("state") in states or time.monotonic() > deadline:
                if out.get("state") not in states:
                    log = (self.home / "runs" / run_id / "log.txt")
                    detail = log.read_text(encoding="utf-8") if log.exists() else ""
                    raise AssertionError(f"run {run_id} is {out.get('state')}, expected {states}: {out}\n{detail}")
                return code, out
            time.sleep(0.2)


def run_cli(*args, env=None, stdin=None, timeout=120):
    """Run the CLI like an agent would: (exit code, the one JSON object on stdout, stderr)."""
    result = subprocess.run([sys.executable, "-B", str(CLI), *map(str, args)], capture_output=True, text=True,
                            env={**os.environ, **(env or {})}, input=stdin, timeout=timeout)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise AssertionError(f"expected one JSON line on stdout, got {result.stdout!r} (stderr {result.stderr!r})")
    return result.returncode, json.loads(lines[0]), result.stderr


@contextlib.contextmanager
def captured_stdout():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield buffer

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest
from unittest import mock

from support import FakeTenant, WorkspaceCase

from m365demo import common, proc, receipts, transport, workspace

POSIX = os.name != "nt"


class WorkspaceTests(WorkspaceCase):
    def test_home_follows_the_override(self):
        self.assertEqual(workspace.home(), self.home)

    @unittest.skipUnless(POSIX, "POSIX modes")
    def test_private_files_and_folders_are_owner_only(self):
        path = workspace.write_json(self.home / "runs" / "r1" / "run.json", {"a": 1})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), 0o700)

    def test_atomic_write_leaves_no_temporary_files(self):
        path = self.home / "x.json"
        workspace.write_json(path, {"v": 1})
        workspace.write_json(path, {"v": 2})
        self.assertEqual(workspace.read_json(path), {"v": 2})
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ["x.json"])

    def test_unreadable_json_is_blocked_with_the_line(self):
        path = self.home / "bad.json"
        self.home.mkdir(parents=True)
        path.write_text("{\n  nope\n}", encoding="utf-8")
        with self.assertRaises(common.Blocked) as caught:
            workspace.read_json(path)
        self.assertEqual(caught.exception.reason, "unreadable-file")
        self.assertIn("line 2", caught.exception.message)

    def test_missing_json_returns_the_default(self):
        self.assertEqual(workspace.read_json(self.home / "none.json", default={}), {})

    def test_names_are_short_slugs(self):
        for good in ("contoso", "q3-launch", "a1"):
            self.assertEqual(workspace.check_name(good), good)
        for bad in ("", "Upper", "-lead", "a/b", "..", "x" * 42, "sp ace", None):
            with self.assertRaises(common.Usage):
                workspace.check_name(bad)

    def test_one_run_at_a_time(self):
        workspace.acquire_lock("run-a", "seed")
        with self.assertRaises(common.Blocked) as caught:
            workspace.acquire_lock("run-b", "cleanup")
        self.assertEqual(caught.exception.reason, "another-run")
        self.assertEqual(caught.exception.extra["activeRun"], "run-a")
        workspace.release_lock("run-b")
        self.assertTrue(workspace.lock_file().exists())
        workspace.release_lock("run-a")
        self.assertFalse(workspace.lock_file().exists())

    def test_a_lock_left_by_a_dead_process_is_replaced(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        workspace.ensure_home()
        workspace.lock_file().write_text(json.dumps({"pid": child.pid, "runId": "old", "kind": "seed"}), encoding="utf-8")
        self.assertIsNone(workspace.read_lock())
        workspace.acquire_lock("new", "seed")
        self.assertEqual(workspace.read_lock()["runId"], "new")

    def test_an_unreadable_lock_counts_as_active_briefly_then_is_removed(self):
        workspace.ensure_home()
        workspace.lock_file().write_text('{"pid": 12', encoding="utf-8")
        self.assertEqual(workspace.read_lock()["runId"], "unknown")
        with self.assertRaises(common.Blocked):
            workspace.acquire_lock("new", "seed")
        old = workspace.lock_file().stat().st_mtime - 120
        os.utime(workspace.lock_file(), (old, old))
        self.assertIsNone(workspace.read_lock())
        workspace.acquire_lock("new", "seed")
        self.assertEqual(workspace.read_lock()["runId"], "new")


class WindowsSharingTests(WorkspaceCase):
    def test_a_file_held_open_elsewhere_is_retried_on_windows(self):
        attempts = []

        def busy_twice():
            attempts.append(1)
            if len(attempts) < 3:
                raise PermissionError(32, "The process cannot access the file")
            return "done"

        with mock.patch.object(workspace, "IS_WINDOWS", True), mock.patch.object(workspace.time, "sleep"):
            self.assertEqual(workspace._shared(busy_twice), "done")
        self.assertEqual(len(attempts), 3)
        attempts.clear()
        with self.assertRaises(PermissionError):
            workspace._shared(busy_twice)
        self.assertEqual(len(attempts), 1, "other systems do not retry")

    def test_remove_scrubs_a_file_it_cannot_delete(self):
        path = workspace.write_private(self.home / "code.txt", "one-time code")
        with mock.patch.object(Path, "unlink", side_effect=PermissionError(5, "denied")), \
                mock.patch.object(workspace, "IS_WINDOWS", True), mock.patch.object(workspace.time, "sleep"):
            workspace.remove(path, scrub=True)
        self.assertEqual(path.read_bytes(), b"")
        workspace.remove(path)
        self.assertFalse(path.exists())
        workspace.remove(path)


class ProcessTests(unittest.TestCase):
    def test_alive(self):
        self.assertTrue(proc.alive(os.getpid()))
        for value in (None, "x", 0, -1):
            self.assertFalse(proc.alive(value))
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertFalse(proc.alive(child.pid))

    def test_spawn_detached_runs_python_with_output_in_the_log(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "log.txt"
            pid = proc.spawn_detached(["-c", "print('detached ok')"], log)
            child = proc.DETACHED.pop()
            self.assertEqual(child.pid, pid)
            self.assertEqual(child.wait(timeout=30), 0)
            self.assertIn("detached ok", log.read_text(encoding="utf-8"))


class TransportTests(unittest.TestCase):
    def test_endpoint_overrides_must_be_loopback(self):
        with mock.patch.dict(os.environ, {"M365_DEMO_GRAPH_URL": "https://evil.example"}):
            with self.assertRaises(common.Blocked) as caught:
                transport.graph_url()
            self.assertEqual(caught.exception.reason, "bad-endpoint-override")
        with mock.patch.dict(os.environ, {"M365_DEMO_LOGIN_URL": "http://127.0.0.1.evil.example:80"}):
            with self.assertRaises(common.Blocked):
                transport.login_url()
        with mock.patch.dict(os.environ, {"M365_DEMO_LOGIN_URL": "http://127.0.0.1:9/"}):
            self.assertEqual(transport.login_url(), "http://127.0.0.1:9")
            self.assertTrue(transport.testing())

    def test_defaults_are_the_public_cloud(self):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("M365_DEMO_")}
        with mock.patch.dict(os.environ, clean, clear=True):
            self.assertEqual(transport.login_url(), "https://login.microsoftonline.com")
            self.assertEqual(transport.graph_url(), "https://graph.microsoft.com")
            self.assertFalse(transport.testing())

    def test_http_errors_are_answers_and_network_errors_raise(self):
        fake = FakeTenant().start()
        try:
            answer = transport.request("GET", f"{fake.url}/v1.0/nowhere")
            self.assertEqual(answer.status, 404)
            self.assertFalse(answer.ok)
            self.assertEqual(answer.json()["error"]["code"], "NotFound")
        finally:
            fake.stop()
        with self.assertRaises(transport.TransportError):
            transport.request("GET", f"{fake.url}/v1.0/me", timeout=5)


class ReceiptTests(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeTenant()

    def test_receipt_is_normalised(self):
        path = self.fake.receipts_file(self.tmp / "s.json", tenant_id=self.fake.tenant.upper())
        receipt = receipts.load(path)
        self.assertEqual(receipt["tenant_id"], self.fake.tenant)
        self.assertEqual(receipt["client_id"], self.fake.client)
        self.assertEqual(receipt["consent"], "granted")
        self.assertIn("Policy.ReadWrite.ConditionalAccess", receipt["delegated_scopes"])

    def test_no_section_means_not_set_up(self):
        path = self.tmp / "s.json"
        path.write_text(json.dumps({"result": "status", "phases": {}}), encoding="utf-8")
        self.assertIsNone(receipts.load(path))
        with self.assertRaises(common.Blocked) as caught:
            receipts.require(path)
        self.assertEqual(caught.exception.reason, "not-set-up")
        self.assertIn("--with-m365-demo", caught.exception.extra["next"][0])

    def test_invalid_ids_are_blocked(self):
        path = self.fake.receipts_file(self.tmp / "s.json", client_id="not-a-guid")
        with self.assertRaises(common.Blocked) as caught:
            receipts.load(path)
        self.assertEqual(caught.exception.reason, "receipts-invalid")

    def test_unreadable_receipts_are_blocked(self):
        with self.assertRaises(common.Blocked):
            receipts.load(self.tmp / "missing.json")
        bad = self.tmp / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        with self.assertRaises(common.Blocked):
            receipts.load(bad)

    def test_the_runner_is_found_from_the_package(self):
        self.assertTrue(receipts.DEPLOY.is_file(), receipts.DEPLOY)


class CommonTests(unittest.TestCase):
    def test_emit_is_one_ascii_json_line(self):
        import io
        buffer = io.StringIO()
        common.emit({"name": "Zoë", "n": 1}, buffer)
        text = buffer.getvalue()
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text.count("\n"), 1)
        self.assertTrue(text.isascii())
        self.assertEqual(json.loads(text)["name"], "Zoë")

    def test_stop_report_and_codes(self):
        stop = common.Waiting("x", "y", next=["z"])
        self.assertEqual(stop.code, common.WAITING)
        self.assertEqual(stop.report(), {"ok": False, "reason": "x", "message": "y", "next": ["z"]})
        self.assertEqual(common.Blocked("a", "b").code, 2)
        self.assertEqual(common.Usage("a", "b").code, 1)

    def test_parse_time(self):
        self.assertIsNotNone(common.parse_time("2026-01-01T00:00:00Z").tzinfo)
        self.assertIsNone(common.parse_time("nope"))
        self.assertIsNone(common.parse_time(None))


if __name__ == "__main__":
    unittest.main()

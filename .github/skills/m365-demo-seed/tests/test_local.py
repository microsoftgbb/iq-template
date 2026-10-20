import os
from pathlib import Path
import unittest
from unittest import mock

from support import WorkspaceCase, run_cli

from m365demo import local, workspace
from m365demo.common import Blocked, Usage, Waiting

PEOPLE = [f"user{i}@contoso-demo.example" for i in range(3)]
CLEAN = "20260101-000000-seed-clean"
UNCLEANED = "20260101-000001-seed-uncleaned"
OPEN = "20260101-000002-seed-open"
RESTORE = "20260101-000003-restore-launch"


class LocalCleanupTests(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.doc = self.write_demo("launch", PEOPLE)
        self.store_passwords({upn: "x" for upn in PEOPLE})
        self.fabricate_run(CLEAN, self.doc, created=False)
        self.fabricate_run(UNCLEANED, self.doc)
        self.fabricate_run(OPEN, self.doc, tenant_phase="restore-failed", outcome="seeded")
        self.fabricate_run(RESTORE, self.doc, kind="restore", outcome="restored")
        (self.home / "notes.txt").write_text("mine", encoding="utf-8")

    def paths(self, entries):
        return sorted(Path(entry["path"]).name for entry in entries)

    def test_the_inventory_keeps_what_the_tenant_still_needs(self):
        report = local.inventory("runs")
        self.assertEqual(report["confirmToken"], "CLEANUP-RUNS")
        self.assertEqual(self.paths(report["remove"]), [CLEAN, RESTORE])
        kept = {Path(entry["path"]).name: entry["reason"] for entry in report["kept"]}
        self.assertIn("not restored", kept[OPEN])
        self.assertIn("still in the tenant", kept[UNCLEANED])
        self.assertEqual([Path(p).name for p in report["foreign"]], ["notes.txt"])
        self.assertTrue((self.home / "runs" / CLEAN).exists(), "an inventory removes nothing")
        wider = local.inventory("runs", include_uncleaned=True)
        self.assertEqual(self.paths(wider["remove"]), [CLEAN, UNCLEANED, RESTORE])
        self.assertEqual([Path(e["path"]).name for e in wider["kept"]], [OPEN], "an open restore is never given up")

    def test_execute_needs_the_inventory_token(self):
        with self.assertRaises(Usage):
            local.execute("runs", "CLEANUP-ALL")
        with self.assertRaises(Usage):
            local.execute("runs", None)
        self.assertTrue((self.home / "runs" / CLEAN).exists())

    def test_runs_depth_keeps_demos_and_passwords(self):
        out = local.execute("runs", "CLEANUP-RUNS")
        self.assertEqual(sorted(Path(p).name for p in out["removed"]), [CLEAN, RESTORE])
        self.assertFalse((self.home / "runs" / CLEAN).exists())
        self.assertTrue((self.home / "runs" / UNCLEANED).exists())
        self.assertTrue(workspace.users_file().exists())
        self.assertTrue((self.home / "demos" / "launch" / "demo.json").exists())

    def test_all_depth_removes_the_workspace_when_nothing_else_is_left(self):
        for name in (UNCLEANED, OPEN):
            local._remove(self.home / "runs" / name, [])
        (self.home / "notes.txt").unlink()
        out = local.execute("all", "CLEANUP-ALL")
        self.assertFalse(self.home.exists())
        self.assertIn(str(self.home), out["removed"])

    def test_all_depth_keeps_the_workspace_with_foreign_files(self):
        local.execute("all", "CLEANUP-ALL", include_uncleaned=True)
        self.assertFalse(workspace.users_file().exists())
        self.assertFalse((self.home / "demos").exists())
        self.assertTrue((self.home / "notes.txt").exists())
        self.assertTrue((self.home / "runs" / OPEN).exists())

    def test_files_that_cannot_be_removed_are_reported(self):
        def stuck(path, onexc=None, onerror=None):
            target = os.path.join(path, "guide.html")
            error = PermissionError(32, "in use")
            if onexc:
                onexc(os.unlink, target, error)
            else:
                onerror(os.unlink, target, (PermissionError, error, None))

        with mock.patch.object(local.shutil, "rmtree", side_effect=stuck):
            with self.assertRaises(Waiting) as caught:
                local.execute("runs", "CLEANUP-RUNS")
        self.assertEqual(caught.exception.reason, "partial")
        self.assertEqual(len(caught.exception.extra["locked"]), 2)
        self.assertEqual(caught.exception.code, 3)

    def test_an_active_run_blocks_local_cleanup(self):
        workspace.acquire_lock("20260101-000009-seed-launch", "seed")
        self.addCleanup(workspace.release_lock, "20260101-000009-seed-launch")
        with self.assertRaises(Blocked):
            local.inventory("runs")

    def test_cli(self):
        code, out, _ = run_cli("local-cleanup")
        self.assertEqual((code, out["confirmToken"]), (0, "CLEANUP-RUNS"))
        self.assertIn("--confirm CLEANUP-RUNS", out["next"][0])
        code, out, _ = run_cli("local-cleanup", "--confirm", "CLEANUP-RUNS")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.home / "runs" / CLEAN).exists())
        code, out, _ = run_cli("local-cleanup", "--depth", "everything")
        self.assertEqual(code, 1)

    def test_nothing_to_clean(self):
        with mock.patch.dict(os.environ, {"M365_DEMO_HOME": str(self.tmp / "absent")}):
            report = local.inventory("all")
        self.assertEqual(report["remove"], [])


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest
from unittest import mock

from support import WorkerCase, run_cli
from fake_tenant import ADMIN, graph_error

from m365demo import auth, runs, tenant, workspace
from m365demo.graph import Graph

MEGAN = "megan@contoso-demo.example"
ALEX = "alex@contoso-demo.example"
LEE = "lee@contoso-demo.example"
PASSWORDS = {MEGAN: "Megan-Pass-1", ALEX: "Alex-Pass-1", LEE: "Lee-Pass-1"}
POSIX = os.name != "nt"


class RunTests(WorkerCase):
    def setUp(self):
        super().setUp()
        for upn, password in PASSWORDS.items():
            self.fake.add_user(upn, password)
        self.policy = self.fake.add_policy("Require MFA for all users")
        self.fake.mfa[self.fake.users[MEGAN]["id"]] = "enabled"
        self.receipts = self.receipt_file()
        code, out, _ = run_cli("demo", "new", "launch", "--admin", ADMIN, "--person", f"{MEGAN}=Megan Bowen",
                               "--person", f"{ALEX}=Alex Wilber", "--person", f"{LEE}=Lee Gu")
        self.assertEqual(code, 0, out)
        self.sha = out["sha256"]
        self.store_passwords(PASSWORDS)

    def seed(self, *extra, approve=None, tenant_id=None):
        return run_cli("seed", "--demo", "launch", "--tenant", tenant_id or self.fake.tenant,
                       "--approve", approve or self.sha, "--receipts-file", self.receipts, *extra)

    def tenant_is_as_found(self):
        self.assertEqual(self.fake.policies[self.policy]["conditions"]["users"]["excludeGroups"], [])
        self.assertEqual(self.fake.mfa[self.fake.users[MEGAN]["id"]], "enabled")
        self.assertFalse([g for g in self.fake.groups.values() if g["securityEnabled"]], "no exclusion group is left")

    def test_seed_end_to_end(self):
        self.fake.hold_sign_in.clear()
        code, out, err = self.seed()
        self.assertEqual((code, out["state"]), (3, "waiting-for-sign-in"), out)
        code_file = Path(out["codeFile"])
        self.assertIn("FAKE-CODE-42", code_file.read_text(encoding="utf-8"))
        if POSIX:
            self.assertEqual(stat.S_IMODE(code_file.stat().st_mode), 0o600)
        self.assertNotIn("FAKE-CODE-42", json.dumps(out) + err, "the one-time code never reaches the agent")
        self.assertIn(str(code_file), out["next"][0])
        self.assertEqual(out["account"], ADMIN)

        code, status, _ = run_cli("status", "--receipts-file", self.receipts)
        self.assertEqual(status["state"], "running")
        code, again, _ = self.seed("--again")
        self.assertEqual((code, again["reason"]), (2, "another-run"))

        self.fake.hold_sign_in.set()
        code, done = self.wait_for(out["runId"], {"finished"})
        self.assertEqual(code, 0)
        self.assertEqual(done["result"]["outcome"], "seeded", done)
        self.assertEqual(done["progress"]["done"], done["progress"]["total"])
        self.assertEqual(done["tenant"]["phase"], "restored")
        self.assertFalse(code_file.exists())
        self.assertIsNone(workspace.read_lock())
        self.tenant_is_as_found()

        copied = (self.home / "runs" / out["runId"] / "demo.json").read_bytes()
        self.assertEqual(hashlib.sha256(copied).hexdigest(), self.sha, "the run seeds exactly the approved bytes")
        guide = Path(done["result"]["guide"]).read_text(encoding="utf-8")
        self.assertIn("Presenter guide", guide)
        self.assertIn("contoso-my.sharepoint.com", guide)
        self.assertIn("teams.microsoft.com/l/channel/", guide)
        self.assertEqual(len(self.fake.events), 2)
        self.assertTrue(self.fake.drives)
        log = (self.home / "runs" / out["runId"] / "log.txt").read_text(encoding="utf-8")
        for secret in [*PASSWORDS.values(), "FAKE-CODE-42", "Admin-Pass-1"]:
            self.assertNotIn(secret, log)

        code, status, _ = run_cli("status", "--receipts-file", self.receipts)
        self.assertEqual((status["state"], status["lastRun"]["runId"]), ("seeded", out["runId"]))
        code, past, _ = run_cli("history")
        self.assertEqual([(r["runId"], r["outcome"]) for r in past["runs"]], [(out["runId"], "seeded")])
        code, again, _ = self.seed()
        self.assertEqual((code, again["reason"]), (1, "already-seeded"))

    def test_the_gates(self):
        code, out, _ = self.seed(approve="0" * 64)
        self.assertEqual((code, out["reason"], out["sha256"]), (1, "not-approved", self.sha))
        code, out, _ = self.seed(tenant_id="99999999-9999-4999-8999-999999999999")
        self.assertEqual((code, out["reason"]), (1, "wrong-tenant"))
        pending = self.fake.receipts_file(self.tmp / "pending.json", consent="pending")
        code, out, _ = run_cli("seed", "--demo", "launch", "--tenant", self.fake.tenant, "--approve", self.sha,
                               "--receipts-file", pending)
        self.assertEqual((code, out["reason"]), (3, "consent-missing"))
        run_cli("users", "clear", "--upn", LEE)
        code, out, _ = self.seed()
        self.assertEqual((code, out["reason"], out["missing"]), (3, "needs-passwords", [LEE]))
        (self.home / "demos" / "launch" / "demo.json").write_text("{}", encoding="utf-8")
        code, out, _ = self.seed()
        self.assertEqual((code, out["reason"]), (1, "invalid-demo"))
        self.assertFalse((self.home / "runs").exists(), "no run starts when a gate fails")

    def test_cancel_while_waiting_for_sign_in_changes_nothing(self):
        self.fake.hold_sign_in.clear()
        code, out, _ = self.seed()
        self.assertEqual(out["state"], "waiting-for-sign-in")
        code, cancelled, _ = run_cli("cancel", "--run", out["runId"])
        self.assertEqual(code, 0)
        code, done = self.wait_for(out["runId"], {"cancelled"})
        self.assertEqual(self.fake.calls("PATCH"), [])
        self.assertEqual(self.fake.calls("POST", r"/groups$"), [])
        self.assertFalse(Path(out["codeFile"]).exists())
        code, status, _ = run_cli("status", "--receipts-file", self.receipts)
        self.assertEqual(status["state"], "ready-to-seed")
        self.assertIn(f"resume --run {out['runId']}", status["next"][0])

    def test_an_expired_sign_in_ends_the_run_and_asks_for_a_person(self):
        self.fake.device_script = ["expired"]
        code, out, _ = self.seed()
        code, done = self.wait_for(out["runId"], {"failed"})
        self.assertEqual((code, done["result"]["reason"]), (3, "sign-in-expired"))
        self.assertEqual(self.fake.calls("PATCH"), [])

    def test_the_wrong_administrator_is_refused_before_any_change(self):
        self.fake.add_user("other-admin@contoso-demo.example", "x", wids=["62e90394-69f5-4237-9190-012177145e10"])
        self.fake.device_account = "other-admin@contoso-demo.example"
        code, out, _ = self.seed()
        code, done = self.wait_for(out["runId"], {"failed"})
        self.assertEqual((code, done["result"]["reason"]), (3, "wrong-account"))
        self.assertEqual(self.fake.calls("PATCH"), [])

    def test_an_interrupted_run_must_be_restored_first(self):
        run = runs.new_run("seed", "launch")
        receipt = self.fake.receipt()
        workspace.write_json(run.path("run.json"), {
            "runId": run.id, "kind": "seed", "demo": "launch", "tenantId": receipt["tenant_id"],
            "clientId": receipt["client_id"], "adminUpn": ADMIN, "receipt": receipt, "createdAt": "now"})
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        workspace.write_json(run.path("status.json"), {"state": "seeding", "pid": dead.pid})
        self.fake.policies[self.policy]["state"] = "disabled"
        admin, _ = auth.password_sign_in(receipt, ADMIN, "Admin-Pass-1")
        self.fake.policies[self.policy]["state"] = "enabled"
        users = [{"upn": upn, "id": self.fake.users[upn]["id"]} for upn in PASSWORDS]
        tenant.prepare(Graph(admin), users, tenant.State(run.path("tenant_state.json"), run.id), lambda line: None)
        self.assertNotEqual(self.fake.policies[self.policy]["conditions"]["users"]["excludeGroups"], [])

        code, status, _ = run_cli("status", "--receipts-file", self.receipts)
        self.assertEqual((status["state"], status["runsToRestore"]), ("needs-restore", [run.id]))
        code, out, _ = self.seed()
        self.assertEqual((code, out["reason"]), (2, "needs-restore"))
        code, interrupted, _ = run_cli("monitor", "--run", run.id)
        self.assertEqual((code, interrupted["state"]), (2, "interrupted"))
        self.assertIn(f"restore --run {run.id}", interrupted["next"][0])

        code, restoring, _ = run_cli("restore", "--run", run.id, "--tenant", self.fake.tenant,
                                     "--receipts-file", self.receipts)
        code, done = self.wait_for(restoring["runId"], {"finished"})
        self.assertEqual(done["result"]["outcome"], "restored")
        self.tenant_is_as_found()
        code, status, _ = run_cli("status", "--receipts-file", self.receipts)
        self.assertEqual(status["state"], "ready-to-seed")
        code, out, _ = run_cli("restore", "--run", run.id, "--tenant", self.fake.tenant, "--receipts-file", self.receipts)
        self.assertEqual((code, out["state"]), (0, "nothing-to-restore"))

    def test_resume_finishes_what_failed_without_repeating_anything(self):
        self.fake.delays["delivery"] = 10 ** 6
        code, out, _ = self.seed()
        code, first = self.wait_for(out["runId"], {"finished"}, timeout=120)
        self.assertEqual(first["result"]["outcome"], "seeded-with-failures")
        self.assertTrue(all(f["item"].startswith("mail:") for f in first["result"]["failures"]))
        self.tenant_is_as_found()
        code, status, _ = run_cli("status", "--receipts-file", self.receipts)
        self.assertEqual(status["state"], "seeded")
        self.assertIn(f"resume --run {out['runId']}", status["next"][1])

        self.fake.delays["delivery"] = 0
        for box in self.fake.mailboxes.values():
            for message in box:
                message["hidden"] = 0
        code, again, _ = run_cli("resume", "--run", out["runId"], "--tenant", self.fake.tenant,
                                 "--receipts-file", self.receipts)
        code, done = self.wait_for(out["runId"], {"finished"})
        self.assertEqual(done["result"]["outcome"], "seeded")
        doc = json.loads((self.home / "runs" / out["runId"] / "demo.json").read_text(encoding="utf-8"))
        sent = sum(1 for box in self.fake.mailboxes.values() for m in box if m["folder"] == "sentitems")
        self.assertEqual(sent, sum(len(t["messages"]) for t in doc["mail"]))
        self.assertEqual(len(self.fake.events), 2)
        self.assertEqual(len(self.fake.invites), 3)
        self.tenant_is_as_found()
        code, out2, _ = run_cli("resume", "--run", out["runId"], "--tenant", self.fake.tenant,
                                "--receipts-file", self.receipts)
        self.assertEqual((code, out2["reason"]), (1, "complete"))

    def test_a_failed_resume_keeps_the_seed_guard(self):
        self.fake.delays["delivery"] = 10 ** 6
        code, out, _ = self.seed()
        code, first = self.wait_for(out["runId"], {"finished"}, timeout=120)
        self.assertEqual(first["result"]["outcome"], "seeded-with-failures")
        self.fake.device_script = ["expired"]
        code, again, _ = run_cli("resume", "--run", out["runId"], "--tenant", self.fake.tenant,
                                 "--receipts-file", self.receipts)
        code, failed = self.wait_for(out["runId"], {"failed"})
        self.assertEqual(failed["result"]["reason"], "sign-in-expired")
        self.assertGreater(failed["created"], 0)
        self.assertIn(f"resume --run {out['runId']}", failed["next"][0])

        code, status, _ = run_cli("status", "--receipts-file", self.receipts)
        self.assertEqual(status["state"], "partly-seeded")
        self.assertEqual(status["next"], [f"{runs.CLI} resume --run {out['runId']} --tenant {self.fake.tenant}"])
        code, refused, _ = self.seed()
        self.assertEqual((code, refused["reason"], refused["previousRun"]), (1, "already-seeded", out["runId"]))
        info = json.loads((self.home / "runs" / out["runId"] / "run.json").read_text(encoding="utf-8"))
        self.assertEqual([a["outcome"] for a in info["attempts"]], ["seeded-with-failures"])

    def test_a_run_restored_separately_reports_its_own_end(self):
        policy_path = r"^/v1\.0/identity/conditionalAccess/policies/(?P<pid>[^/]+)$"
        answers = {"count": 0}

        def second_patch_fails(request, pid):
            answers["count"] += 1
            if answers["count"] == 2:
                return graph_error(500, "InternalServerError", "Try again later.")
            return self.fake.patch_policy(request, pid)
        self.fake.route("PATCH", policy_path, second_patch_fails)
        code, out, _ = self.seed()
        code, stuck = self.wait_for(out["runId"], {"needs-restore"})
        self.assertEqual(code, 2)
        self.assertEqual(stuck["tenant"]["phase"], "restore-failed")
        self.assertEqual(stuck["next"], [f"{runs.CLI} restore --run {out['runId']} --tenant {self.fake.tenant}"])

        code, restoring, _ = run_cli("restore", "--run", out["runId"], "--tenant", self.fake.tenant,
                                     "--receipts-file", self.receipts)
        self.wait_for(restoring["runId"], {"finished"})
        self.tenant_is_as_found()
        code, after, _ = run_cli("monitor", "--run", out["runId"])
        self.assertEqual((code, after["state"], after["tenant"]["phase"]), (0, "finished", "restored"))
        self.assertTrue(after["next"][0].startswith("Open the presenter guide"))
        code, status, _ = run_cli("status", "--receipts-file", self.receipts)
        self.assertEqual(status["state"], "seeded")

    def test_a_seed_that_loses_the_race_for_the_lock_leaves_no_run_behind(self):
        workspace.acquire_lock("someone-else", "seed")
        self.addCleanup(workspace.release_lock, "someone-else")
        with mock.patch.object(runs, "_check_quiet"):
            with self.assertRaises(runs.Blocked) as caught:
                runs.start_seed("launch", self.receipts, self.fake.tenant, self.sha)
        self.assertEqual(caught.exception.reason, "another-run")
        self.assertEqual(list((self.home / "runs").iterdir()), [])


if __name__ == "__main__":
    unittest.main()

import json
import os
import unittest

from support import TenantCase, WorkspaceCase, run_cli
from fake_tenant import FakeTenant

from m365demo import workspace

MEGAN = "megan@contoso-demo.example"
ALEX = "alex@contoso-demo.example"


class CliTests(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.receipts = FakeTenant().receipts_file(self.tmp / "status.json")

    def status(self, *extra):
        code, out, _ = run_cli("status", "--receipts-file", self.receipts, *extra)
        self.assertEqual(code, 0)
        self.assertTrue(out["ok"])
        return out

    def test_status_walks_the_setup(self):
        not_set_up = self.tmp / "empty.json"
        not_set_up.write_text(json.dumps({"result": "status", "phases": {}}), encoding="utf-8")
        code, out, _ = run_cli("status", "--receipts-file", not_set_up)
        self.assertEqual((code, out["state"]), (0, "not-set-up"))
        self.assertIn("--with-m365-demo", out["next"][0])

        pending = FakeTenant().receipts_file(self.tmp / "pending.json", consent="pending")
        code, out, _ = run_cli("status", "--receipts-file", pending)
        self.assertEqual(out["state"], "consent-pending")

        self.assertEqual(self.status()["state"], "needs-demo")
        self.write_demo("launch", [MEGAN, ALEX])
        out = self.status()
        self.assertEqual((out["state"], out["demo"]), ("needs-passwords", "launch"))
        self.assertEqual(out["users"]["missing"], [MEGAN, ALEX])
        self.assertIn("users set --demo launch", out["next"][0])

        workspace.write_json(workspace.users_file(), {"version": 1, "users": {
            MEGAN: {"password": "x"}, ALEX: {"password": "y"}}})
        self.assertEqual(self.status("--demo", "launch")["state"], "ready-to-seed")

    def test_status_reports_an_active_run(self):
        workspace.acquire_lock("20260101-000000-seed", "seed")
        try:
            out = self.status()
        finally:
            workspace.release_lock("20260101-000000-seed")
        self.assertEqual(out["state"], "running")

    def test_usage_errors_exit_1_with_json(self):
        for args in (("bogus",), ("users",), ("users", "set", "--upn", "nope"), ("status", "--demo", "Bad Name")):
            code, out, _ = run_cli(*args, "--receipts-file", self.receipts) if args[0] == "status" else run_cli(*args)
            self.assertEqual(code, 1, args)
            self.assertFalse(out["ok"])

    def test_users_set_refuses_without_a_terminal(self):
        code, out, _ = run_cli("users", "set", "--upn", MEGAN, stdin="hunter2\nhunter2\n")
        self.assertEqual((code, out["reason"]), (3, "needs-your-terminal"))
        self.assertFalse(workspace.users_file().exists())

    def test_a_non_loopback_override_is_blocked(self):
        code, out, _ = run_cli("status", "--receipts-file", self.receipts,
                               env={"M365_DEMO_LOGIN_URL": "https://login.evil.example"})
        self.assertEqual((code, out["reason"]), (2, "bad-endpoint-override"))


class CliTenantTests(TenantCase):
    def setUp(self):
        super().setUp()
        self.receipts = self.receipt_file()
        self.fake.add_user(MEGAN, "Megan-Pass-1")
        self.fake.add_user(ALEX, "Alex-Pass-1")
        self.write_demo("launch", [MEGAN, ALEX])

    def store(self, **passwords):
        workspace.write_json(workspace.users_file(), {"version": 1, "users": {
            upn: {"password": value} for upn, value in passwords.items()}})

    def test_users_check_ready(self):
        self.store(**{MEGAN: "Megan-Pass-1", ALEX: "Alex-Pass-1"})
        code, out, err = run_cli("users", "check", "--demo", "launch", "--receipts-file", self.receipts)
        self.assertEqual(code, 0, out)
        self.assertEqual((out["passed"], out["allReady"]), (2, True))
        for secret in ("Megan-Pass-1", "Alex-Pass-1"):
            self.assertNotIn(secret, json.dumps(out) + err)

    def test_users_check_waits_for_a_fix(self):
        self.store(**{MEGAN: "Megan-Pass-1", ALEX: "wrong"})
        code, out, _ = run_cli("users", "check", "--receipts-file", self.receipts)
        self.assertEqual((code, out["reason"]), (3, "users-not-ready"))
        self.assertEqual(out["failed"], 1)
        self.assertNotIn("wrong", json.dumps(out))

    def test_users_check_without_setup_is_blocked(self):
        empty = self.tmp / "empty.json"
        empty.write_text(json.dumps({"result": "status", "phases": {}}), encoding="utf-8")
        code, out, _ = run_cli("users", "check", "--demo", "launch", "--receipts-file", empty)
        self.assertEqual((code, out["reason"]), (2, "not-set-up"))

    def test_users_status_and_clear(self):
        self.store(**{MEGAN: "Megan-Pass-1"})
        code, out, _ = run_cli("users", "status", "--demo", "launch")
        self.assertEqual((code, out["missing"]), (0, [ALEX]))
        self.assertNotIn("Megan-Pass-1", json.dumps(out))
        code, out, _ = run_cli("users", "clear")
        self.assertEqual((code, out["removed"]), (0, [MEGAN]))

    def test_users_clear_for_a_demo_keeps_other_users(self):
        other = "other@contoso-demo.example"
        self.store(**{MEGAN: "m", other: "o"})
        code, out, _ = run_cli("users", "clear", "--demo", "launch")
        self.assertEqual((code, out["removed"], out["remaining"]), (0, [MEGAN], 1))
        code, out, _ = run_cli("users", "status", "--upn", other)
        self.assertEqual(out["missing"], [])


if __name__ == "__main__":
    unittest.main()

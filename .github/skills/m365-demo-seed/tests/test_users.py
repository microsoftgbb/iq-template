import contextlib
import io
import json
import os
import stat
import unittest

from support import TenantCase, WorkspaceCase
from fake_tenant import E5_PLANS

from m365demo import users, workspace
from m365demo.common import Blocked, Usage, Waiting

POSIX = os.name != "nt"
MEGAN = "megan@contoso-demo.example"
ALEX = "alex@contoso-demo.example"


class Prompts:
    """Scripted answers for the yes/no question and the masked password prompts."""

    def __init__(self, answers=(), secrets=()):
        self.answers, self.secrets, self.asked = list(answers), list(secrets), []

    def ask(self, prompt):
        self.asked.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def secret(self, prompt):
        self.asked.append(prompt)
        if not self.secrets:
            raise EOFError
        return self.secrets.pop(0)


class StoreTests(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.stderr = io.StringIO()
        quiet = contextlib.redirect_stderr(self.stderr)
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def set(self, upns, prompts, **options):
        return users.set_passwords(upns, command="users set", interactive=True, ask=prompts.ask, secret=prompts.secret,
                                   **options)

    def test_one_shared_password_is_typed_twice_and_stored_owner_only(self):
        result = self.set([MEGAN, ALEX], Prompts(answers=[""], secrets=["S3cret!", "S3cret!"]))
        self.assertEqual(result["stored"], [MEGAN, ALEX])
        self.assertNotIn("S3cret!", json.dumps(result) + self.stderr.getvalue())
        self.assertEqual(users.password(MEGAN), "S3cret!")
        self.assertEqual(users.password(ALEX.upper()), "S3cret!")
        if POSIX:
            self.assertEqual(stat.S_IMODE(workspace.users_file().stat().st_mode), 0o600)

    def test_separate_passwords_with_a_retry_after_a_mismatch(self):
        prompts = Prompts(answers=["n"], secrets=["one", "typo", "one", "one", "two", "two"])
        self.set([MEGAN, ALEX], prompts)
        self.assertEqual((users.password(MEGAN), users.password(ALEX)), ("one", "two"))
        self.assertIn(f"Password for {MEGAN}: ", prompts.asked)

    def test_empty_or_repeatedly_different_entries_change_nothing(self):
        with self.assertRaises(Usage) as caught:
            self.set([MEGAN], Prompts(secrets=["", "a", "b", "c", "d"]))
        self.assertEqual(caught.exception.reason, "password-not-set")
        self.assertFalse(workspace.users_file().exists())

    def test_cancel_changes_nothing(self):
        self.set([MEGAN], Prompts(secrets=["keep", "keep"]))
        with self.assertRaises(Usage) as caught:
            self.set([MEGAN, ALEX], Prompts(answers=["y"], secrets=["new"]))
        self.assertEqual(caught.exception.reason, "cancelled")
        self.assertEqual(users.password(MEGAN), "keep")
        self.assertIsNone(users.password(ALEX))

    def test_missing_only_skips_stored_users(self):
        self.set([MEGAN], Prompts(secrets=["m", "m"]))
        result = self.set([MEGAN, ALEX], Prompts(secrets=["a", "a"]), missing_only=True)
        self.assertEqual(result["stored"], [ALEX])
        self.assertEqual(users.password(MEGAN), "m")

    def test_refuses_without_a_terminal(self):
        with self.assertRaises(Waiting) as caught:
            users.set_passwords([MEGAN], command="python3 m365demo.py users set --demo x", interactive=False)
        self.assertEqual(caught.exception.reason, "needs-your-terminal")
        self.assertIn("users set --demo x", caught.exception.extra["next"][0])

    def test_status_and_clear_never_show_passwords(self):
        self.set([MEGAN, ALEX], Prompts(answers=["y"], secrets=["pw-1", "pw-1"]))
        report = users.status([MEGAN, ALEX, "new@contoso-demo.example"])
        self.assertEqual(report["missing"], ["new@contoso-demo.example"])
        self.assertNotIn("pw-1", json.dumps(report))
        self.assertEqual(users.clear([MEGAN])["removed"], [MEGAN])
        self.assertEqual(users.clear()["removed"], [ALEX])
        self.assertFalse(workspace.users_file().exists())

    @unittest.skipUnless(POSIX, "POSIX modes")
    def test_status_fixes_a_too_open_file(self):
        self.set([MEGAN], Prompts(secrets=["x", "x"]))
        os.chmod(workspace.users_file(), 0o644)
        self.assertFalse(users.status([MEGAN])["ownerOnly"])
        self.assertEqual(stat.S_IMODE(workspace.users_file().stat().st_mode), 0o600)

    def test_targets(self):
        self.assertEqual(users.targets([" Megan@Contoso-Demo.Example ", MEGAN]), [MEGAN])
        with self.assertRaises(Usage):
            users.targets(["not-an-upn"])
        self.write_demo("launch", [ALEX, MEGAN])
        self.assertEqual(users.targets(None, "launch"), [ALEX, MEGAN])
        self.assertEqual(users.targets(), [ALEX, MEGAN])
        with self.assertRaises(Usage):
            users.targets(None, "missing")

    def test_a_corrupt_store_is_blocked(self):
        workspace.ensure_home()
        workspace.users_file().write_text("[]", encoding="utf-8")
        with self.assertRaises(Blocked):
            users.load()


class LicenseTests(unittest.TestCase):
    def test_families(self):
        plans = [{"servicePlanName": "TEAMS1", "provisioningStatus": "PendingProvisioning"},
                 {"servicePlanName": "EXCHANGE_S_STANDARD", "provisioningStatus": "Disabled"},
                 {"servicePlanName": "SHAREPOINTWAC", "provisioningStatus": "Success"}]
        self.assertEqual(users.family(plans, "teams"), "pending")
        self.assertEqual(users.family(plans, "exchange"), "disabled")
        self.assertEqual(users.family(plans, "sharepoint"), "missing")
        self.assertEqual(users.family(E5_PLANS, "sharepoint"), "ok")


class CheckTests(TenantCase):
    def setUp(self):
        super().setUp()
        self.receipt = self.fake.receipt()
        workspace.ensure_home()

    def store(self, **passwords):
        workspace.write_json(workspace.users_file(), {"version": 1, "users": {
            upn: {"password": value, "setAt": "now"} for upn, value in passwords.items()}})

    def test_all_ready(self):
        self.fake.add_user(MEGAN, "m-pw")
        self.fake.add_user(ALEX, "a-pw")
        self.store(**{MEGAN: "m-pw", ALEX: "a-pw"})
        report = users.check([MEGAN, ALEX], self.receipt)
        self.assertTrue(report["allReady"])
        self.assertEqual(report["licenses"]["ok"], 2)
        text = json.dumps(report)
        for secret in ("m-pw", "a-pw", "access", "refresh"):
            self.assertNotIn(secret, text)

    def test_pending_mfa_passes_with_licenses_unchecked(self):
        self.fake.add_user(MEGAN, "m-pw", error=50076)
        self.store(**{MEGAN: "m-pw"})
        report = users.check([MEGAN], self.receipt)
        self.assertTrue(report["allReady"])
        self.assertEqual(report["users"][0]["licenses"]["status"], "unchecked")

    def test_a_bad_password_or_missing_entry_waits_for_the_person(self):
        self.fake.add_user(MEGAN, "right")
        self.store(**{MEGAN: "wrong"})
        with self.assertRaises(Waiting) as caught:
            users.check([MEGAN, ALEX], self.receipt)
        stop = caught.exception
        self.assertEqual(stop.reason, "users-not-ready")
        self.assertEqual([u["status"] for u in stop.extra["users"]], ["bad-password", "no-password"])
        self.assertEqual(len(stop.extra["next"]), 2)
        self.assertNotIn("wrong", json.dumps(stop.report()))

    def test_missing_licenses_wait_for_the_person(self):
        self.fake.add_user(MEGAN, "m-pw", plans=[p for p in E5_PLANS if not p["servicePlanName"].startswith("TEAMS")])
        self.store(**{MEGAN: "m-pw"})
        with self.assertRaises(Waiting) as caught:
            users.check([MEGAN], self.receipt)
        self.assertEqual(caught.exception.reason, "licenses-not-ready")
        self.assertEqual(caught.exception.extra["users"][0]["licenses"]["missing"], ["teams"])

    def test_incomplete_consent_is_reported(self):
        self.fake.granted = [s for s in self.fake.granted if s != "ChatMessage.Send"]
        self.fake.add_user(MEGAN, "m-pw")
        self.store(**{MEGAN: "m-pw"})
        with self.assertRaises(Waiting) as caught:
            users.check([MEGAN], self.receipt)
        row = caught.exception.extra["users"][0]
        self.assertEqual((row["status"], row["reason"]), ("consent-needed", "scopes-missing"))
        self.assertIn("ChatMessage.Send", row["hint"])

    def test_a_broken_app_is_blocked(self):
        self.fake.add_user(MEGAN, "m-pw", error=7000218)
        self.store(**{MEGAN: "m-pw"})
        with self.assertRaises(Blocked) as caught:
            users.check([MEGAN], self.receipt)
        self.assertEqual(caught.exception.reason, "demo-app")


if __name__ == "__main__":
    unittest.main()

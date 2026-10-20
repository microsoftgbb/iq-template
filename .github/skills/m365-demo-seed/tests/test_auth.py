import itertools
import os
import stat
import unittest
from unittest import mock

from support import TenantCase, captured_stdout
from fake_tenant import ADMIN, GLOBAL_ADMINISTRATOR, OTHER, SCOPES

from m365demo import auth
from m365demo.common import Blocked, Waiting
from m365demo.graph import Graph

POSIX = os.name != "nt"
MEGAN = "megan@contoso-demo.example"


class HelperTests(unittest.TestCase):
    def test_claims_of_malformed_tokens_are_empty(self):
        for token in (None, "", "a.b", "a.%%%.c", "a.bm90IGpzb24.c"):
            self.assertEqual(auth.claims(token), {})

    def test_scope_names_drop_the_resource_and_case(self):
        self.assertEqual(auth.scope_names("https://graph.microsoft.com/User.Read openid Mail.Send"),
                         {"user.read", "openid", "mail.send"})
        self.assertEqual(auth.scope_names(["Chat.Create"]), {"chat.create"})
        self.assertEqual(auth.scope_names(None), set())

    def test_classify(self):
        self.assertEqual(auth.classify([50076], "", "x")["status"], "ok-pending-mfa")
        self.assertTrue(auth.classify([53003], "", "x")["passwordValid"])
        verdict = auth.classify([], "AADSTS99999: Something.", "x@y.z")
        self.assertEqual((verdict["status"], verdict["passwordValid"]), ("error", False))
        self.assertIn("AADSTS99999", verdict["hint"])


class DeviceCodeTests(TenantCase):
    def setUp(self):
        super().setUp()
        self.receipt = self.fake.receipt()
        self.code_file = self.home / "runs" / "r1" / "signin-code.txt"
        self.sleeps = []

    def sign_in(self, expected=ADMIN, **poll):
        with captured_stdout() as out:
            device = auth.start_device_code(self.receipt, self.code_file, account=expected)
            self.code_text = self.code_file.read_text(encoding="utf-8")
            self.code_mode = stat.S_IMODE(self.code_file.stat().st_mode)
            try:
                tokens = auth.poll_device_code(self.receipt, device, self.code_file, sleep=self.sleeps.append, **poll)
                return auth.admin_session(self.receipt, tokens, expected_upn=expected, me=lambda s: Graph(s).me())
            finally:
                self.assertEqual(out.getvalue(), "", "nothing may reach stdout")
                self.assertFalse(self.code_file.exists(), "the code file is removed after the sign-in")

    def test_the_code_goes_only_to_an_owner_only_file(self):
        self.fake.device_script = ["pending", "slow_down", "ok"]
        session = self.sign_in()
        self.assertEqual(session.account, ADMIN)
        self.assertEqual(session.warnings, [])
        self.assertIn("FAKE-CODE-42", self.code_text)
        self.assertIn(f"as {ADMIN}", self.code_text)
        if POSIX:
            self.assertEqual(self.code_mode, 0o600)
        self.assertEqual(self.sleeps, [0, 0, 5])
        start = self.fake.calls("POST", "devicecode$")[0]
        self.assertEqual(start["form"], {"client_id": self.fake.client, "scope": auth.GRAPH_SCOPE})

    def test_declined_and_expired_need_a_person(self):
        for outcome, reason in (("declined", "sign-in-declined"), ("expired", "sign-in-expired"),
                                ("consent", "consent-missing")):
            self.fake.device_script = ["pending", outcome]
            with self.assertRaises(Waiting) as caught:
                self.sign_in()
            self.assertEqual(caught.exception.reason, reason)

    def test_the_wait_ends_at_the_deadline(self):
        self.fake.device_script = ["pending"] * 10
        ticks = itertools.count(0, 400)
        with self.assertRaises(Waiting) as caught:
            self.sign_in(clock=lambda: next(ticks))
        self.assertEqual(caught.exception.reason, "sign-in-expired")

    def test_no_sign_in_without_consent(self):
        self.receipt = self.fake.receipt(consent="pending")
        with self.assertRaises(Waiting) as caught:
            auth.start_device_code(self.receipt, self.code_file)
        self.assertEqual(caught.exception.reason, "consent-missing")
        self.assertIn("--grant-m365-demo-consent", caught.exception.extra["next"][0])
        self.assertEqual(self.fake.requests, [])

    def test_a_missing_app_is_blocked(self):
        self.receipt = self.fake.receipt(client_id=OTHER)
        with self.assertRaises(Blocked) as caught:
            auth.start_device_code(self.receipt, self.code_file)
        self.assertEqual(caught.exception.reason, "app-not-found")
        self.assertFalse(self.code_file.exists())

    def test_the_wrong_account_needs_a_new_sign_in(self):
        self.fake.add_user(MEGAN, "pw", wids=[GLOBAL_ADMINISTRATOR])
        self.fake.device_account = MEGAN
        with self.assertRaises(Waiting) as caught:
            self.sign_in()
        self.assertEqual(caught.exception.reason, "wrong-account")
        self.assertIn(MEGAN, caught.exception.message)

    def test_the_account_check_ignores_case(self):
        self.assertEqual(self.sign_in(expected=ADMIN.upper()).account, ADMIN)

    def test_the_wrong_tenant_or_app_is_blocked(self):
        for claim, reason in (("tid", "wrong-tenant"), ("appid", "wrong-app")):
            self.fake.claims_override = {claim: OTHER}
            with self.assertRaises(Blocked) as caught:
                self.sign_in()
            self.assertEqual(caught.exception.reason, reason)

    def test_missing_consented_scopes_are_named(self):
        self.fake.granted = [s for s in SCOPES if s != "Policy.ReadWrite.ConditionalAccess"]
        with self.assertRaises(Waiting) as caught:
            self.sign_in()
        self.assertEqual(caught.exception.reason, "consent-missing")
        self.assertIn("Policy.ReadWrite.ConditionalAccess", caught.exception.message)

    def test_sign_in_only_scopes_are_not_required_in_the_token(self):
        self.fake.granted = [s for s in SCOPES if s not in ("openid", "offline_access")]
        self.assertEqual(self.sign_in().account, ADMIN)

    def test_a_non_global_administrator_gets_a_warning(self):
        self.fake.users[ADMIN]["wids"] = ["fe930be7-5e62-47db-91af-98c3a49a38b1"]
        session = self.sign_in()
        self.assertTrue(any(w.startswith("not-global-administrator") for w in session.warnings))

    def test_unverified_consent_is_a_warning(self):
        self.receipt = self.fake.receipt(consent="unverified")
        self.assertTrue(any(w.startswith("consent-unverified") for w in self.sign_in().warnings))

    def test_an_opaque_access_token_is_checked_through_the_id_token_and_graph(self):
        self.fake.opaque = True
        session = self.sign_in()
        self.assertEqual(session.account, ADMIN)
        self.assertEqual(session.claims, {})
        self.fake.claims_override = {"tid": OTHER}
        with self.assertRaises(Blocked):
            self.sign_in()

    def test_unknown_scopes_are_a_warning_not_a_failure(self):
        self.fake.opaque = True
        self.fake.omit_scope = True
        session = self.sign_in()
        self.assertTrue(any(w.startswith("scopes-unverified") for w in session.warnings))


class SessionTests(TenantCase):
    def setUp(self):
        super().setUp()
        self.receipt = self.fake.receipt()
        self.now = [1000.0]
        self.fake.add_user(MEGAN, "Megan-Pass-1")

    def session(self):
        session, verdict = auth.password_sign_in(self.receipt, MEGAN, "Megan-Pass-1", clock=lambda: self.now[0])
        self.assertEqual(verdict["status"], "ok")
        return session

    def renewals(self):
        return [r for r in self.fake.calls("POST", "/token$") if r["form"].get("grant_type") == "refresh_token"]

    def test_renews_five_minutes_before_expiry(self):
        session = self.session()
        first = session.token()
        self.now[0] += 3600 - 400
        self.assertEqual(session.token(), first)
        self.now[0] += 200
        second = session.token()
        self.assertNotEqual(second, first)
        self.assertEqual(len(self.renewals()), 1)
        self.assertEqual(self.renewals()[0]["form"]["client_id"], self.fake.client)

    def test_a_failed_renewal_keeps_the_valid_token_then_expires(self):
        session = self.session()
        first = session.token()
        self.fake.refresh_fails = True
        self.now[0] += 3600 - 120
        self.assertEqual(session.token(), first)
        self.assertEqual(session.token(), first)
        self.assertEqual(len(self.renewals()), 1, "backs off between renewal attempts")
        self.now[0] += 61
        self.assertEqual(session.token(), first)
        self.assertEqual(len(self.renewals()), 2)
        self.now[0] += 120
        with self.assertRaises(Blocked) as caught:
            session.token()
        self.assertEqual(caught.exception.reason, "session-expired")

    def test_graph_renews_once_after_a_401(self):
        session = self.session()
        stale = session.token()
        self.fake.access.pop(stale)
        self.assertEqual(Graph(session).me()["userPrincipalName"], MEGAN)
        self.assertEqual(len(self.renewals()), 1)


class PasswordSignInTests(TenantCase):
    def setUp(self):
        super().setUp()
        self.receipt = self.fake.receipt()

    def test_success_sends_exactly_the_public_client_fields(self):
        self.fake.add_user(MEGAN, "Megan-Pass-1")
        session, verdict = auth.password_sign_in(self.receipt, MEGAN, "Megan-Pass-1")
        self.assertEqual(verdict["status"], "ok")
        self.assertEqual(session.account, MEGAN)
        form = self.fake.calls("POST", "/token$")[0]["form"]
        self.assertEqual(set(form), {"grant_type", "client_id", "username", "password", "scope"})
        self.assertEqual(form["grant_type"], "password")
        self.assertEqual(form["password"], "***")

    def test_failures_are_classified(self):
        cases = {50126: ("bad-password", False), 50034: ("not-found", False), 50057: ("disabled", False),
                 50053: ("locked", False), 50055: ("password-expired", False), 50076: ("ok-pending-mfa", True),
                 50079: ("ok-pending-mfa", True), 53003: ("ok-pending-mfa", True), 65001: ("consent-needed", True),
                 700016: ("app-not-found", False), 7000218: ("app-not-public-client", False), 12345: ("error", False)}
        for code, (status, valid) in cases.items():
            upn = f"user{code}@contoso-demo.example"
            self.fake.add_user(upn, "pw", error=code)
            session, verdict = auth.password_sign_in(self.receipt, upn, "pw")
            self.assertIsNone(session)
            self.assertEqual((verdict["status"], verdict["passwordValid"]), (status, valid), code)
            self.assertNotIn("pw", verdict["hint"].split())

    def test_wrong_password_and_unknown_user(self):
        self.fake.add_user(MEGAN, "right")
        self.assertEqual(auth.password_sign_in(self.receipt, MEGAN, "wrong")[1]["status"], "bad-password")
        self.assertEqual(auth.password_sign_in(self.receipt, "nobody@contoso-demo.example", "x")[1]["status"], "not-found")

    def test_network_failure_is_reported(self):
        with mock.patch.dict(os.environ, {"M365_DEMO_LOGIN_URL": "http://127.0.0.1:9"}):
            session, verdict = auth.password_sign_in(self.receipt, MEGAN, "x")
        self.assertIsNone(session)
        self.assertEqual(verdict["reason"], "network")


if __name__ == "__main__":
    unittest.main()

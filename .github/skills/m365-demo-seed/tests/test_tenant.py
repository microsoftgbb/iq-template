import unittest

from support import TenantCase
from fake_tenant import ADMIN, Reply, graph_error

from m365demo import auth, tenant
from m365demo.common import Blocked
from m365demo.graph import Graph

MEGAN = "megan@contoso-demo.example"
ALEX = "alex@contoso-demo.example"


class TenantTests(TenantCase):
    def setUp(self):
        super().setUp()
        self.admin = self.session(ADMIN, "Admin-Pass-1")
        self.graph = Graph(self.admin)
        self.megan = self.fake.add_user(MEGAN, "m-pw")
        self.alex = self.fake.add_user(ALEX, "a-pw")
        self.users = [{"upn": MEGAN, "id": self.megan["id"]}, {"upn": ALEX, "id": self.alex["id"]}]
        self.state = tenant.State(self.tmp / "tenant_state.json", "run-1")
        self.logs = []

    def can_sign_in(self, upn, password):
        return auth.password_sign_in(self.fake.receipt(), upn, password)[1]["status"]

    def security_groups(self):
        return [g for g in self.fake.groups.values() if g["securityEnabled"]]

    def test_users_sign_in_only_while_prepared_and_everything_is_restored_exactly(self):
        blocking = self.fake.add_policy("Require MFA for all users")
        disabled = self.fake.add_policy("Disabled policy", state="disabled")
        report_only = self.fake.add_policy("Report only", state="enabledForReportingButNotEnforced")
        other = self.fake.add_policy("Excludes break-glass", exclude_groups=["break-glass"])
        self.fake.mfa[self.megan["id"]] = "enforced"
        self.assertEqual(self.can_sign_in(MEGAN, "m-pw"), "ok-pending-mfa")
        self.assertEqual(self.can_sign_in(ALEX, "a-pw"), "ok-pending-mfa")

        tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.assertEqual(self.state.phase, "prepared")
        self.assertEqual(self.can_sign_in(MEGAN, "m-pw"), "ok")
        self.assertEqual(self.can_sign_in(ALEX, "a-pw"), "ok")
        (group,) = self.security_groups()
        self.assertTrue(group["displayName"].startswith(tenant.GROUP_NAME + " "))
        self.assertEqual(sorted(group["members"]), sorted([self.megan["id"], self.alex["id"]]))
        self.assertNotIn(self.admin.user_id, group["members"], "the administrator is never excluded")
        self.assertEqual(self.fake.policies[other]["conditions"]["users"]["excludeGroups"], ["break-glass", group["id"]])
        for untouched in (disabled, report_only):
            self.assertEqual(self.fake.policies[untouched]["conditions"]["users"]["excludeGroups"], [])
        summary = self.state.summary()
        self.assertEqual((summary["policiesExcluded"], summary["perUserMfaTurnedOff"]), (2, 1))

        self.assertEqual(tenant.restore(self.graph, self.state, self.logs.append), [])
        self.assertEqual(self.state.phase, "restored")
        self.assertEqual(self.fake.policies[blocking]["conditions"]["users"]["excludeGroups"], [])
        self.assertEqual(self.fake.policies[other]["conditions"]["users"]["excludeGroups"], ["break-glass"])
        self.assertEqual(self.fake.mfa[self.megan["id"]], "enforced")
        self.assertEqual(self.security_groups(), [], "the run's group is deleted")
        self.assertEqual(self.can_sign_in(MEGAN, "m-pw"), "ok-pending-mfa")
        self.assertFalse(self.state.needs_restore)

    def test_a_blocked_mfa_registration_waits_for_the_prepared_tenant(self):
        # AADSTS53004: Entra asks the user to register for MFA and blocks the registration as risky.
        self.fake.mfa_code = 53004
        self.fake.add_policy("Multifactor authentication for all users")
        session, verdict = auth.password_sign_in(self.fake.receipt(), MEGAN, "m-pw")
        self.assertIsNone(session)
        self.assertEqual((verdict["status"], verdict["passwordValid"]), ("ok-pending-mfa", True))
        self.assertIn("register for MFA", verdict["hint"])

        tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.assertEqual(self.can_sign_in(MEGAN, "m-pw"), "ok")
        self.assertEqual(tenant.restore(self.graph, self.state, self.logs.append), [])
        self.assertEqual(self.can_sign_in(MEGAN, "m-pw"), "ok-pending-mfa")

    def test_a_look_alike_group_is_never_used(self):
        eve = self.fake.add_user("eve@contoso-demo.example", "e-pw")
        decoy = {"id": "aaaaaaaa-0000-4000-8000-000000000001", "displayName": tenant.GROUP_NAME,
                 "mailNickname": "m365-demo-seed-mfa-exclusion", "mailEnabled": False, "securityEnabled": True,
                 "groupTypes": [], "owners": [eve["id"]], "members": [eve["id"]], "team": None, "deleted": False}
        self.fake.groups[decoy["id"]] = decoy
        self.fake.add_policy("Require MFA for all users")
        tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.assertNotEqual(self.state.data["exclusionGroup"]["id"], decoy["id"])
        self.assertEqual(self.can_sign_in("eve@contoso-demo.example", "e-pw"), "ok-pending-mfa")
        self.assertEqual(self.can_sign_in(ALEX, "a-pw"), "ok")
        tenant.restore(self.graph, self.state, self.logs.append)
        self.assertEqual([g["id"] for g in self.security_groups()], [decoy["id"]], "the look-alike is left alone")

    def interrupt_group_creation(self):
        self.fake.route("POST", r"^/v1\.0/groups$", self.fake.processed_then(self.fake.create_group, Reply(drop=True)))
        with self.assertRaises(Blocked) as caught:
            tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.assertEqual(caught.exception.reason, "network")
        self.fake.route("POST", r"^/v1\.0/groups$", self.fake.create_group)
        record = self.state.data["exclusionGroup"]
        self.assertNotIn("id", record)
        self.assertTrue(record["created"])
        return record

    def test_an_interrupted_group_creation_is_found_and_deleted(self):
        record = self.interrupt_group_creation()
        (group,) = self.security_groups()
        self.assertEqual(group["displayName"], record["displayName"])
        self.assertEqual(tenant.restore(self.graph, self.state, self.logs.append), [])
        self.assertEqual(self.security_groups(), [])

    def test_an_interrupted_group_creation_is_adopted_when_preparing_again(self):
        self.fake.add_policy("Require MFA for all users")
        self.interrupt_group_creation()
        tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.assertEqual(len(self.security_groups()), 1, "no second group")
        self.assertEqual(self.can_sign_in(ALEX, "a-pw"), "ok")
        tenant.restore(self.graph, self.state, self.logs.append)
        self.assertEqual(self.security_groups(), [])

    def test_outsiders_in_the_run_group_stop_the_preparation(self):
        policy = self.fake.add_policy("Require MFA for all users")
        self.interrupt_group_creation()
        eve = self.fake.add_user("eve@contoso-demo.example", "e-pw")
        self.security_groups()[0]["members"].append(eve["id"])
        with self.assertRaises(Blocked) as caught:
            tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.assertEqual(caught.exception.reason, "exclusion-group")
        self.assertEqual(self.fake.policies[policy]["conditions"]["users"]["excludeGroups"], [])
        self.assertEqual(self.can_sign_in("eve@contoso-demo.example", "e-pw"), "ok-pending-mfa")

    def test_a_refused_group_creation_leaves_nothing_to_find(self):
        self.fake.route("POST", r"^/v1\.0/groups$", lambda request: graph_error(403, "Forbidden", "No."))
        with self.assertRaises(Blocked):
            tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.assertNotIn("exclusionGroup", self.state.data)
        self.assertEqual(tenant.restore(self.graph, self.state, self.logs.append), [])
        self.assertEqual(self.fake.calls("GET", r"/groups$"), [])

    def test_security_defaults_are_turned_off_and_back_on(self):
        self.fake.security_defaults = True
        tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.assertFalse(self.fake.security_defaults)
        self.assertEqual(self.can_sign_in(ALEX, "a-pw"), "ok")
        tenant.restore(self.graph, self.state, self.logs.append)
        self.assertTrue(self.fake.security_defaults)

    def test_refused_security_defaults_stop_before_any_other_change(self):
        self.fake.security_defaults = True
        self.fake.security_defaults_forbidden = True
        self.fake.add_policy("Require MFA")
        with self.assertRaises(Blocked) as caught:
            tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.assertEqual(caught.exception.reason, "security-defaults")
        self.assertFalse(self.state.data["securityDefaults"]["disabled"])
        self.assertEqual(self.fake.groups, {})
        self.assertEqual(tenant.restore(self.graph, self.state, self.logs.append), [])
        self.assertTrue(self.fake.security_defaults)

    def test_a_group_the_run_created_is_deleted_when_nothing_references_it(self):
        self.fake.add_policy("Require MFA")
        self.fake.delays["members"] = 2
        tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        group_id = self.state.data["exclusionGroup"]["id"]
        self.assertTrue(self.state.data["exclusionGroup"]["created"])
        tenant.restore(self.graph, self.state, self.logs.append)
        self.assertNotIn(group_id, self.fake.groups)
        self.assertTrue(self.state.data["exclusionGroup"]["deleted"])

    def test_a_policy_that_cannot_change_is_reported_and_left_alone(self):
        locked = self.fake.add_policy("Locked policy")
        self.fake.policy_patch_forbidden.add(locked)
        tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.assertEqual([p["id"] for p in self.state.data["policies"]["skipped"]], [locked])
        self.assertTrue(any("could not exclude" in line for line in self.logs))
        self.assertEqual(self.can_sign_in(ALEX, "a-pw"), "ok-pending-mfa")
        self.assertEqual(tenant.restore(self.graph, self.state, self.logs.append), [])

    def test_an_interrupted_preparation_continues_from_its_records(self):
        self.fake.add_policy("Require MFA")
        self.fake.mfa[self.alex["id"]] = "enabled"
        mfa_route = r"^/beta/users/(?P<uid>[^/]+)/authentication/requirements$"
        self.fake.route("PATCH", mfa_route, lambda request, uid: graph_error(500, "Internal", "boom"))
        with self.assertRaises(Blocked):
            tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.assertEqual(self.state.phase, "preparing")
        self.assertTrue(self.state.needs_restore and self.state.resumable)
        self.fake.route("PATCH", mfa_route, self.fake.patch_mfa)
        tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.assertEqual(self.state.phase, "prepared")
        self.assertEqual(len(self.state.data["policies"]["touched"]), 1, "nothing is changed twice")
        tenant.restore(self.graph, self.state, self.logs.append)
        self.assertEqual(self.fake.mfa[self.alex["id"]], "enabled")

    def test_the_context_restores_after_an_error(self):
        self.fake.add_policy("Require MFA")
        with self.assertRaises(RuntimeError):
            with tenant.prepared(self.graph, self.users, self.state, self.logs.append):
                self.assertEqual(self.can_sign_in(ALEX, "a-pw"), "ok")
                raise RuntimeError("seeding failed")
        self.assertEqual(self.state.phase, "restored")
        self.assertEqual(self.can_sign_in(ALEX, "a-pw"), "ok-pending-mfa")

    def test_a_failed_restore_is_recorded_and_can_be_retried(self):
        policy = self.fake.add_policy("Require MFA")
        tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.fake.policy_patch_forbidden.add(policy)
        problems = tenant.restore(self.graph, self.state, self.logs.append)
        self.assertEqual(len(problems), 1)
        self.assertEqual(self.state.phase, "restore-failed")
        self.assertTrue(self.state.needs_restore and not self.state.resumable)
        self.assertIn(self.state.data["exclusionGroup"]["id"], self.fake.groups, "kept while a policy references it")
        with self.assertRaises(Blocked):
            tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.fake.policy_patch_forbidden.clear()
        self.assertEqual(tenant.restore(self.graph, self.state, self.logs.append), [])
        self.assertEqual(self.state.phase, "restored")
        self.assertNotIn(self.state.data["exclusionGroup"]["id"], self.fake.groups)

    def test_preparing_again_after_a_restore_starts_over(self):
        self.fake.security_defaults = True
        tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        tenant.restore(self.graph, self.state, self.logs.append)
        tenant.prepare(self.graph, self.users, self.state, self.logs.append)
        self.assertFalse(self.fake.security_defaults)
        self.assertEqual(len(self.state.data["history"]), 1)
        tenant.restore(self.graph, self.state, self.logs.append)
        self.assertTrue(self.fake.security_defaults)

    def test_restore_without_changes_does_nothing(self):
        self.assertEqual(tenant.restore(self.graph, self.state, self.logs.append), [])
        self.assertEqual(self.state.phase, "new")
        self.assertEqual(self.fake.calls("PATCH"), [])


if __name__ == "__main__":
    unittest.main()

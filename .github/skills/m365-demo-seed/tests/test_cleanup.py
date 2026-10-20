from datetime import date
import unittest
from unittest import mock

from support import TenantCase, WorkerCase, run_cli
from fake_tenant import ADMIN

from m365demo import cleanup, content, runs, sample
from m365demo.graph import Graph

MEGAN = "megan@contoso-demo.example"
ALEX = "alex@contoso-demo.example"
LEE = "lee@contoso-demo.example"
NAMES = {MEGAN: "Megan Bowen", ALEX: "Alex Wilber", LEE: "Lee Gu"}
PASSWORDS = {upn: upn.split("@")[0] for upn in NAMES}


class CleanerTests(TenantCase):
    def setUp(self):
        super().setUp()
        self.admin = self.session(ADMIN, "Admin-Pass-1")
        for upn in NAMES:
            self.fake.add_user(upn, PASSWORDS[upn], name=NAMES[upn])
        self.doc = sample.build(ADMIN, [(upn, name) for upn, name in NAMES.items()])
        self.sessions = {upn: self.session(upn, PASSWORDS[upn]) for upn in NAMES}
        self.people = {p["key"]: {**p, "id": self.fake.users[p["upn"]]["id"], "session": self.sessions[p["upn"]]}
                       for p in self.doc["people"]}
        self.by_upn = {p["upn"]: p for p in self.people.values()}

    def seed(self, name):
        manifest = content.Manifest(self.tmp / f"{name}.json", name, "launch")
        seeder = content.Seeder(self.doc, self.people, self.admin, manifest, run_id=name, log=lambda line: None,
                                advance=lambda n: None, cancelled=lambda: False, today=date(2026, 10, 14))
        for step in (seeder.prepare_team, seeder.seed_files, seeder.seed_mail, seeder.seed_chats,
                     seeder.seed_meetings, seeder.seed_channel):
            step()
        self.assertEqual(seeder.failures, [])
        return manifest

    def graph_for(self, upn):
        session = self.sessions.get(upn)
        if not session:
            raise cleanup.Stop("no-session", f"{upn} could not sign in.")
        return Graph(session)

    def clean(self, manifest, others=()):
        shared = cleanup.shared_with(others)
        cleaner = cleanup.Cleaner(manifest, self.by_upn, self.admin, shared=shared, log=lambda line: None,
                                  advance=lambda n: None, cancelled=lambda: False, graph_for=self.graph_for)
        cleaner.run()
        return cleaner

    def everything_from(self, manifest):
        """What the fake tenant still holds of a run's content."""
        items = manifest.data["items"]
        conversations = {s["conversationId"] for k, e in items.items() if k.startswith("mail:") for s in e["sent"]}
        uids = {e["iCalUId"] for k, e in items.items() if k.startswith("meeting:")}
        files = {e["itemId"] for k, e in items.items() if k.startswith("file:")}
        left = {
            "mail": [m for box in self.fake.mailboxes.values() for m in box if m["conversationId"] in conversations],
            "events": [e for cal in self.fake.calendars.values() for e in cal.values() if e["iCalUId"] in uids],
            "files": [i for drive in self.fake.drives.values() for i in drive.values() if i["id"] in files],
            "teams": [g for g in self.fake.groups.values() if g["team"]],
            "groupChats": [c for c in self.fake.chats.values() if c["chatType"] in ("group", "meeting")],
            "seededOneOnOne": [m for c in self.fake.chats.values() if c["chatType"] == "oneOnOne"
                               for m in c["messages"] if not m.get("deleted") and m["id"] != "before"],
        }
        return {kind: found for kind, found in left.items() if found}

    def test_a_cleanup_removes_exactly_what_the_run_created(self):
        pair = self.doc["chats"][0]["members"]
        self.assertEqual(len(pair), 2)
        self.fake.chats["19:pair@thread.v2"] = {"id": "19:pair@thread.v2", "chatType": "oneOnOne", "topic": None,
                                                "members": sorted(self.people[k]["id"] for k in pair), "deleted": False,
                                                "messages": [{"id": "before", "from": self.people[pair[0]]["upn"],
                                                              "body": {}}]}
        unrelated = self.fake.deliver(LEE, "Lunch on Friday?", sender=ALEX)
        self.fake.drives[MEGAN] = {"Personal/notes.docx": {"id": "item-mine", "name": "notes.docx",
                                                           "path": "Personal/notes.docx"}}
        manifest = self.seed("run-1")
        self.assertTrue(self.everything_from(manifest))
        self.assertTrue(all("WelcomeEmailDisabled" in g["resourceBehaviorOptions"] for g in self.fake.groups.values()))
        notices = [m for box in self.fake.mailboxes.values() for m in box if m.get("kind") == "message"]
        self.assertTrue(notices, "sharing sends notices")

        rows = cleanup.plan(manifest.data, {})
        self.assertEqual({r["kind"] for r in rows}, {"team", "chat", "meeting", "file", "mail"})
        self.assertEqual([r["kind"] for r in rows], sorted((r["kind"] for r in rows), key=cleanup.ORDER.index))

        cleaner = self.clean(manifest)
        self.assertEqual(cleaner.failures, [])
        self.assertEqual(self.everything_from(manifest), {})
        self.assertEqual(manifest.created(), 0)
        self.assertEqual(cleanup.plan(manifest.data, {}), [])
        self.assertEqual(self.fake.chats["19:pair@thread.v2"]["messages"][0].get("deleted"), None,
                         "an earlier message in the one-on-one chat stays")
        self.assertIn("19:pair@thread.v2", self.fake.chats, "the one-on-one chat itself stays")
        self.assertIn(unrelated, self.fake.mailboxes[LEE])
        self.assertIn("Personal/notes.docx", self.fake.drives[MEGAN])
        leftovers = [m["subject"] for box in self.fake.mailboxes.values() for m in box if m.get("kind") in ("event",
                                                                                                          "message")]
        self.assertEqual(leftovers, ["Lunch on Friday?"], "invitations, cancellations and share notices are swept")
        self.assertGreater(cleaner.swept["meetingNotices"], 0)
        self.assertGreater(cleaner.swept["shareNotices"], 0)

    def test_files_another_live_seed_uses_are_kept_until_that_run_is_cleaned(self):
        first = self.seed("run-1")
        second = self.seed("run-2")
        shared_ids = {e["itemId"] for k, e in first.data["items"].items() if k.startswith("file:")}
        self.assertEqual(shared_ids, {e["itemId"] for k, e in second.data["items"].items() if k.startswith("file:")})
        rows = cleanup.plan(first.data, cleanup.shared_with([("run-2", second.data)]))
        self.assertEqual({r["action"] for r in rows if r["kind"] == "file"}, {"keep"})

        cleaner = self.clean(first, [("run-2", second.data)])
        self.assertEqual(len(cleaner.kept), 3)
        self.assertEqual(first.created(), 0, "the first run is clean; the second run is responsible for the files")
        self.assertEqual(len([i for d in self.fake.drives.values() for i in d.values() if i["id"] in shared_ids]), 3)
        self.assertTrue(self.everything_from(second))

        self.clean(second, [("run-1", first.data)])
        self.assertEqual(self.everything_from(second), {})
        self.assertEqual([i for d in self.fake.drives.values() for i in d.values() if i["id"] in shared_ids], [])

    def test_a_user_who_cannot_sign_in_keeps_their_items_until_a_retry(self):
        manifest = self.seed("run-1")
        owner = self.doc["files"][0]["owner"]
        upn = self.people[owner]["upn"]
        session = self.sessions.pop(upn)
        cleaner = self.clean(manifest)
        failed = {f["item"] for f in cleaner.failures}
        self.assertIn("file:asset-status", failed)
        self.assertTrue(all("could not sign in" in f["error"] for f in cleaner.failures))
        self.assertGreater(manifest.created(), 0)
        self.assertNotIn("team", failed, "the administrator's items do not depend on demo users")
        self.sessions[upn] = session
        retry = self.clean(manifest)
        self.assertEqual(retry.failures, [])
        self.assertEqual(self.everything_from(manifest), {})

    def test_chat_deletions_are_spaced_a_second_apart(self):
        manifest = self.seed("run-1")
        waits = []
        with mock.patch.object(cleanup.pace, "sleep", side_effect=waits.append):
            self.clean(manifest)
        self.assertTrue(any(wait > 0.5 for wait in waits), waits)

    def test_the_plan_hash_follows_the_plan(self):
        manifest = self.seed("run-1")
        rows = cleanup.plan(manifest.data, {})
        self.assertEqual(cleanup.digest(rows), cleanup.digest(cleanup.plan(manifest.data, {})))
        manifest.item("mail:pump-b-window")["deleted"] = True
        self.assertNotEqual(cleanup.digest(rows), cleanup.digest(cleanup.plan(manifest.data, {})))


class CleanupRunTests(WorkerCase):
    def setUp(self):
        super().setUp()
        for upn, password in PASSWORDS.items():
            self.fake.add_user(upn, password, name=NAMES[upn])
        self.fake.add_policy("Require MFA for all users")
        self.receipts = self.receipt_file()
        code, out, _ = run_cli("demo", "new", "launch", "--admin", ADMIN, *[f"--person={u}={n}" for u, n in NAMES.items()])
        self.assertEqual(code, 0, out)
        self.sha = out["sha256"]
        self.store_passwords(PASSWORDS)
        code, seeding, _ = run_cli("seed", "--demo", "launch", "--tenant", self.fake.tenant, "--approve", self.sha,
                                   "--receipts-file", self.receipts)
        self.seed_run = seeding["runId"]
        self.wait_for(self.seed_run, {"finished"})

    def test_plan_approve_clean_and_seed_again(self):
        code, plan, _ = run_cli("cleanup", "--run", self.seed_run)
        self.assertEqual(code, 0, plan)
        self.assertEqual(plan["counts"]["file"], {"delete": 3, "keep": 0})
        self.assertIn(f"--approve {plan['sha256']}", plan["next"][0])
        code, refused, _ = run_cli("cleanup", "--run", self.seed_run, "--tenant", self.fake.tenant, "--approve", "0" * 64,
                                   "--receipts-file", self.receipts)
        self.assertEqual((code, refused["reason"]), (1, "not-approved"))
        code, half, _ = run_cli("cleanup", "--run", self.seed_run, "--tenant", self.fake.tenant)
        self.assertEqual((code, half["reason"]), (1, "usage"))

        code, started, _ = run_cli("cleanup", "--run", self.seed_run, "--tenant", self.fake.tenant,
                                   "--approve", plan["sha256"], "--receipts-file", self.receipts)
        self.assertIn(started["state"], ("waiting-for-sign-in", "resolving-users", "preparing-tenant", "signing-in-users",
                                         "cleaning", "restoring", "finished"), started)
        code, done = self.wait_for(started["runId"], {"finished"})
        self.assertEqual((code, done["result"]["outcome"], done["result"]["remaining"]), (0, "cleaned", 0))
        self.assertEqual(done["target"], self.seed_run)
        self.assertEqual(done["tenant"]["phase"], "restored")
        self.assertEqual(self.fake.events, {})
        self.assertFalse([g for g in self.fake.groups.values() if g["team"] or g["securityEnabled"]])

        code, status, _ = run_cli("status", "--receipts-file", self.receipts)
        self.assertEqual((status["state"], status["lastRun"]["cleanedBy"]), ("ready-to-seed", started["runId"]))
        code, nothing, _ = run_cli("cleanup", "--run", self.seed_run)
        self.assertEqual(nothing["items"], [])
        code, again, _ = run_cli("seed", "--demo", "launch", "--tenant", self.fake.tenant, "--approve", self.sha,
                                 "--receipts-file", self.receipts)
        self.assertNotEqual(again.get("reason"), "already-seeded")
        self.wait_for(again["runId"], {"finished"})
        code, past, _ = run_cli("history")
        self.assertEqual([r["kind"] for r in past["runs"]], ["seed", "cleanup", "seed"])

    def test_only_seed_runs_can_be_cleaned(self):
        code, plan, _ = run_cli("cleanup", "--run", self.seed_run)
        code, started, _ = run_cli("cleanup", "--run", self.seed_run, "--tenant", self.fake.tenant,
                                   "--approve", plan["sha256"], "--receipts-file", self.receipts)
        self.wait_for(started["runId"], {"finished"})
        code, out, _ = run_cli("cleanup", "--run", started["runId"])
        self.assertEqual((code, out["reason"]), (1, "not-a-seed"))
        self.assertTrue(runs.RUN_ID.match(started["runId"]))


if __name__ == "__main__":
    unittest.main()

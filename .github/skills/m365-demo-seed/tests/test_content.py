from datetime import date
import io
import unittest
import zipfile

from support import TenantCase
from fake_tenant import ADMIN, Reply, claims_challenge, graph_error

from m365demo import content, sample

MEGAN = "megan@contoso-demo.example"
ALEX = "alex@contoso-demo.example"
LEE = "lee@contoso-demo.example"
NAMES = {MEGAN: "Megan Bowen", ALEX: "Alex Wilber", LEE: "Lee Gu"}


class HelperTests(unittest.TestCase):
    def test_business_days_skip_weekends(self):
        friday, saturday = date(2026, 10, 16), date(2026, 10, 17)
        self.assertEqual(content.business_day(friday, 1), date(2026, 10, 19))
        self.assertEqual(content.business_day(friday, -5), date(2026, 10, 9))
        self.assertEqual(content.business_day(saturday, 0), date(2026, 10, 19))
        self.assertEqual(content.business_day(date(2026, 10, 19), -1), friday)

    def test_meeting_thread_from_the_join_url(self):
        url = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_ABC123%40thread.v2/0?context=%7b%7d"
        self.assertEqual(content.meeting_thread(url), "19:meeting_ABC123@thread.v2")
        self.assertIsNone(content.meeting_thread(None))

    def test_reply_text_goes_above_the_quoted_thread(self):
        quoted = '<html><body><div id="appendonsend"></div><hr><div>old</div></body></html>'
        self.assertEqual(content.merge_reply(quoted, "<p>new</p>"),
                         '<html><body><div><p>new</p></div><div id="appendonsend"></div><hr><div>old</div></body></html>')
        self.assertEqual(content.merge_reply("<html><body><p>x</p></body></html>", "n"),
                         "<html><body><div>n</div><p>x</p></body></html>")
        self.assertEqual(content.merge_reply("", "n"), "<div>n</div>")


class SeederTests(TenantCase):
    def setUp(self):
        super().setUp()
        self.admin = self.session(ADMIN, "Admin-Pass-1")
        for upn in NAMES:
            self.fake.add_user(upn, upn.split("@")[0], name=NAMES[upn])
        self.doc = sample.build(ADMIN, [(upn, name) for upn, name in NAMES.items()])
        self.people = {p["key"]: {**p, "id": self.fake.users[p["upn"]]["id"],
                                  "session": self.session(p["upn"], p["upn"].split("@")[0])} for p in self.doc["people"]}
        self.manifest = content.Manifest(self.tmp / "manifest.json", "run-1", "launch")
        self.logs, self.progress, self.cancel = [], [], False

    def seeder(self):
        return content.Seeder(self.doc, self.people, self.admin, self.manifest, run_id="run-1", log=self.logs.append,
                              advance=self.progress.append, cancelled=lambda: self.cancel, today=date(2026, 10, 14))

    def seed_all(self, seeder=None):
        seeder = seeder or self.seeder()
        for step in (seeder.prepare_team, seeder.seed_files, seeder.seed_mail, seeder.seed_chats, seeder.seed_meetings,
                     seeder.seed_channel):
            step()
        return seeder

    def sent_by_everyone(self):
        return sum(1 for box in self.fake.mailboxes.values() for m in box if m["folder"] == "sentitems")

    def test_a_full_seed_creates_every_item_as_its_owner(self):
        seeder = self.seed_all()
        self.assertEqual(seeder.failures, [])
        self.assertEqual(sum(self.progress), content.units(self.doc))
        self.assertEqual(self.manifest.counts(), {"done": len(self.manifest.data["items"])})

        # Files: uploaded by their owners, valid packages, shared, and linked from mail.
        uploaded = {item["name"]: (owner, item) for owner, files in self.fake.drives.items() for item in files.values()}
        self.assertEqual(sorted(uploaded), sorted(f["name"] for f in self.doc["files"]))
        for spec in self.doc["files"]:
            owner, item = uploaded[spec["name"]]
            self.assertEqual(owner, self.people[spec["owner"]]["upn"])
            self.assertEqual(item["path"], f"Operations/{spec['name']}")
            self.assertTrue(zipfile.is_zipfile(io.BytesIO(item["data"])))
        self.assertEqual(len(self.fake.invites), 3)
        self.assertTrue(all(invite["sendInvitation"] and invite["roles"] == ["write"] for invite in self.fake.invites))

        # Mail: every message sent by its author; replies thread with quotes and keep the links.
        self.assertEqual(self.sent_by_everyone(), sum(len(t["messages"]) for t in self.doc["mail"]))
        planner = self.people[self.doc["mail"][0]["messages"][0]["from"]]["upn"]
        first = next(m for m in self.fake.mailboxes[planner] if m["folder"] == "sentitems")
        self.assertIn("contoso-my.sharepoint.com", first["body"]["content"])
        replies = [m for box in self.fake.mailboxes.values() for m in box
                   if m["folder"] == "sentitems" and m["subject"].startswith("RE: ")]
        self.assertTrue(replies)
        self.assertTrue(all('<div id="appendonsend">' in m["body"]["content"] for m in replies))

        # Chats, meetings and their chats.
        normal = [c for c in self.fake.chats.values() if c["chatType"] != "meeting"]
        self.assertEqual(sorted(len(c["messages"]) for c in normal),
                         sorted(len(c["messages"]) for c in self.doc["chats"]))
        self.assertEqual(len(self.fake.events), 2)
        sync = next(e for e in self.fake.events.values() if e["request"]["subject"] == "Weekly operations sync")
        self.assertEqual(sync["request"]["start"], {"dateTime": "2026-10-12T10:00:00", "timeZone": "UTC"})
        self.assertEqual(len(self.fake.chats[sync["thread"]]["messages"]), 2)

        # Team: created by the admin, owned by the demo owner, admin removed, posts and replies by their authors.
        (group,) = [g for g in self.fake.groups.values() if g["team"]]
        owner = self.people[self.doc["channel"]["owner"]]
        self.assertEqual(group["owners"], [owner["id"]])
        self.assertNotIn(self.admin.user_id, group["members"])
        channels = {c["displayName"]: c for c in group["team"]["channels"].values()}
        self.assertEqual(sorted(channels), ["General", "Pump B maintenance"])
        posts = channels["Pump B maintenance"]["messages"]
        self.assertEqual([len(p["replies"]) for p in posts], [2, 1])
        self.assertEqual(posts[0]["subject"], "Pump B outage starts Wednesday")

    def test_a_cancelled_run_resumes_without_repeating_anything(self):
        seeder = self.seeder()
        seeder.prepare_team()
        seeder.seed_files()
        calls = {"count": 0}

        def cancel_after_two():
            calls["count"] += 1
            return calls["count"] > 3
        seeder._cancelled = cancel_after_two
        with self.assertRaises(content.Cancelled):
            seeder.seed_mail()
        partial = self.sent_by_everyone()
        self.assertGreater(partial, 0)
        resumed = content.Seeder(self.doc, self.people, self.admin, content.Manifest(self.tmp / "manifest.json"),
                                 run_id="run-1", log=self.logs.append, advance=self.progress.append,
                                 cancelled=lambda: False, today=date(2026, 10, 14))
        self.manifest = resumed.manifest
        self.seed_all(resumed)
        self.assertEqual(self.sent_by_everyone(), sum(len(t["messages"]) for t in self.doc["mail"]))
        self.assertEqual(len(self.fake.invites), 3, "files are not uploaded and shared twice")
        self.assertEqual(len([g for g in self.fake.groups.values()]), 1, "the team is not created twice")

    def test_lagging_tenant_services_are_waited_out(self):
        owner_upn = self.people[self.doc["files"][0]["owner"]]["upn"]
        self.fake.delays.update(team=2, team_ready=2, chat=1, delivery=2, members=1)
        self.fake.delays["drive"][owner_upn] = 2
        self.fake.delays["roster"][self.people[self.doc["channel"]["channels"][0]["posts"][0]["from"]]["upn"]] = 3
        seeder = self.seed_all()
        self.assertEqual(seeder.failures, [])

    def test_conditional_access_still_reaching_teams_is_waited_out(self):
        teams = ((r"^/v1\.0/chats$", self.fake.create_chat),
                 (r"^/v1\.0/chats/(?P<cid>[^/]+)/messages$", self.fake.chat_post),
                 (r"^/v1\.0/teams/(?P<gid>[^/]+)/channels/(?P<cid>[^/]+)/messages$", self.fake.channel_post))
        for pattern, handler in teams:
            self.fake.route("POST", pattern, self.fake.first_refused(handler, 2, claims_challenge(header=False)))
        seeder = self.seed_all()
        self.assertEqual(seeder.failures, [])
        self.assertTrue(any("Conditional Access changes have not reached" in line for line in self.logs))
        normal = [c for c in self.fake.chats.values() if c["chatType"] != "meeting"]
        self.assertEqual(sorted(len(c["messages"]) for c in normal),
                         sorted(len(c["messages"]) for c in self.doc["chats"]), "no message is posted twice")

    def test_a_reply_to_mail_that_was_only_just_delivered_is_retried(self):
        refused = graph_error(404, "ErrorItemNotFound", "The specified object was not found in the store.")
        self.fake.route("POST", r"^/v1\.0/me/messages/(?P<mid>[^/]+)/createReplyAll$",
                        self.fake.first_refused(self.fake.create_reply_all, 2, refused))
        seeder = self.seed_all()
        self.assertEqual(seeder.failures, [])
        self.assertEqual(self.sent_by_everyone(), sum(len(t["messages"]) for t in self.doc["mail"]))

    def test_replies_thread_by_conversation_when_drafts_lack_an_internet_message_id(self):
        self.fake.reply_without_internet_id = True
        seeder = self.seed_all()
        self.assertEqual(seeder.failures, [])
        self.assertEqual(self.sent_by_everyone(), sum(len(t["messages"]) for t in self.doc["mail"]))

    def test_an_undelivered_message_fails_only_its_thread(self):
        self.fake.delays["delivery"] = 10 ** 6
        seeder = self.seed_all()
        failed = sorted(f["item"] for f in seeder.failures)
        self.assertEqual(failed, sorted(f"mail:{t['key']}" for t in self.doc["mail"] if len(t["messages"]) > 1))
        self.assertTrue(all("did not reach" in f["error"] for f in seeder.failures))
        self.assertEqual(self.manifest.item("chat:vibration")["status"], "done")

    def test_a_user_who_could_not_sign_in_only_loses_their_own_items(self):
        missing = self.doc["files"][1]["owner"]
        self.people[missing]["session"] = None
        seeder = self.seed_all()
        self.assertIn("file:pump-b-plan", [f["item"] for f in seeder.failures])
        self.assertTrue(all("could not sign in" in f["error"] for f in seeder.failures))
        self.assertEqual(self.manifest.item("file:asset-status")["status"], "done")

    def test_meeting_chat_is_best_effort(self):
        self.fake.meeting_chat_forbidden = True
        seeder = self.seed_all()
        self.assertEqual(seeder.failures, [])
        entry = self.manifest.item("meeting:weekly-sync")
        self.assertEqual(entry["status"], "done")
        self.assertTrue(entry["chatStatus"].startswith("skipped"))
        self.assertEqual(sum(self.progress), content.units(self.doc))

    def test_a_send_that_timed_out_is_not_sent_twice(self):
        seeder = self.seeder()
        seeder.seed_files()
        send = r"^/v1\.0/me/messages/(?P<mid>[^/]+)/send$"
        self.fake.route("POST", send, self.fake.processed_then(self.fake.send_message, Reply(drop=True)))
        seeder.seed_mail()
        self.assertEqual(sorted(f["item"] for f in seeder.failures), sorted(f"mail:{t['key']}" for t in self.doc["mail"]))
        pending = [e.get("pending") for k, e in self.manifest.data["items"].items() if k.startswith("mail:")]
        self.assertTrue(all(pending), "each thread recorded the draft whose send timed out")
        sent_before = self.sent_by_everyone()
        self.fake.route("POST", send, self.fake.send_message)
        seeder.failures.clear()
        seeder.seed_mail()
        self.assertEqual(seeder.failures, [])
        self.assertEqual(self.sent_by_everyone(), sum(len(t["messages"]) for t in self.doc["mail"]))
        self.assertEqual(sent_before, len(self.doc["mail"]), "the first sends went through before the timeout")
        for thread in self.doc["mail"]:
            entry = self.manifest.item(f"mail:{thread['key']}")
            self.assertEqual([s["index"] for s in entry["sent"]], list(range(len(thread["messages"]))))
            self.assertNotIn("pending", entry)

    def test_a_reply_without_an_internet_message_id_is_not_sent_twice(self):
        self.fake.reply_without_internet_id = True
        send = r"^/v1\.0/me/messages/(?P<mid>[^/]+)/send$"

        def replies_time_out(request, mid):
            draft = self.fake.message(request["caller"], mid)
            answer = self.fake.send_message(request, mid)
            return Reply(drop=True) if draft and draft["subject"].startswith("RE: ") else answer

        self.fake.route("POST", send, replies_time_out)
        seeder = self.seeder()
        seeder.seed_files()
        seeder.seed_mail()
        pending = [self.manifest.item(f"mail:{t['key']}").get("pending") for t in self.doc["mail"]]
        self.assertTrue(all(p and p["index"] == 1 and not p["internetMessageId"] for p in pending), pending)
        self.fake.route("POST", send, self.fake.send_message)
        seeder.failures.clear()
        seeder.seed_mail()
        self.assertEqual(seeder.failures, [])
        self.assertEqual(self.sent_by_everyone(), sum(len(t["messages"]) for t in self.doc["mail"]))

    def test_an_unsent_draft_is_sent_on_resume(self):
        seeder = self.seeder()
        seeder.seed_files()
        send = r"^/v1\.0/me/messages/(?P<mid>[^/]+)/send$"
        self.fake.route("POST", send, lambda request, mid: Reply(drop=True))
        seeder.seed_mail()
        drafts = sum(1 for box in self.fake.mailboxes.values() for m in box if m["folder"] == "drafts")
        self.fake.route("POST", send, self.fake.send_message)
        seeder.failures.clear()
        seeder.seed_mail()
        self.assertEqual(seeder.failures, [])
        self.assertEqual(self.sent_by_everyone(), sum(len(t["messages"]) for t in self.doc["mail"]))
        left = sum(1 for box in self.fake.mailboxes.values() for m in box if m["folder"] == "drafts")
        self.assertEqual((drafts, left), (len(self.doc["mail"]), 0), "the recorded drafts were sent, not recreated")

    def test_an_interrupted_team_creation_is_adopted(self):
        groups = r"^/v1\.0/groups$"
        self.fake.route("POST", groups, self.fake.processed_then(self.fake.create_group, Reply(drop=True)))
        seeder = self.seeder()
        seeder.prepare_team()
        self.assertEqual([f["item"] for f in seeder.failures], ["team"])
        self.assertTrue(self.manifest.item("team").get("mailNickname"))
        self.assertEqual(self.manifest.created(), 1, "a possibly created group counts as content")
        self.fake.route("POST", groups, self.fake.create_group)
        self.seed_all()
        self.assertEqual(len([g for g in self.fake.groups.values() if g["team"]]), 1)
        self.assertEqual(len(self.fake.groups), 1, "no second group")

    def test_events_are_not_duplicated_on_retry(self):
        seeder = self.seeder()
        seeder.seed_files()
        seeder.seed_meetings()
        for entry in self.manifest.data["items"].values():
            entry.pop("eventId", None)
        seeder.seed_meetings()
        self.assertEqual(len(self.fake.events), 2, "the transactionId makes event creation idempotent")


if __name__ == "__main__":
    unittest.main()

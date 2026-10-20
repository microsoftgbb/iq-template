import copy
import json
import unittest

from support import WorkspaceCase, run_cli
from fake_tenant import FakeTenant

from m365demo import demo, pages, sample, text

ADMIN = "admin@contoso-demo.example"
PEOPLE = [f"user{i}@contoso-demo.example" for i in range(8)]


def sample_doc(count=4):
    return sample.build(ADMIN, [(upn, None) for upn in PEOPLE[:count]])


class SampleTests(unittest.TestCase):
    def test_the_sample_is_valid_for_two_to_eight_people(self):
        for count in range(2, 9):
            doc = sample_doc(count)
            errors, warnings = demo.validate(doc)
            self.assertEqual(errors, [], count)
            self.assertEqual(warnings, [], count)
            keys = [p["key"] for p in doc["people"]]
            self.assertEqual(len(set(keys)), count)
            for thread in doc["mail"]:
                first = thread["messages"][0]
                self.assertNotIn(first["from"], first["to"] + first.get("cc", []))
            for chat in doc["chats"]:
                self.assertGreaterEqual(len(set(chat["members"])), 2)

    def test_names_and_keys_come_from_the_given_people(self):
        doc = sample.build(ADMIN, [("megan.b@contoso-demo.example", "Megan Bowen"),
                                   ("megan.c@contoso-demo.example", "Megan Clark"), ("x1@contoso-demo.example", None)])
        self.assertEqual([p["key"] for p in doc["people"]], ["megan", "megan-2", "x1"])
        self.assertEqual(doc["people"][2]["name"], "X1")
        self.assertIn("Hi Megan,", doc["mail"][0]["messages"][0]["body"])


class ValidationTests(unittest.TestCase):
    def errors(self, mutate, count=4):
        doc = sample_doc(count)
        mutate(doc)
        return demo.validate(doc)[0]

    def assertError(self, mutate, fragment, count=4):
        errors = self.errors(mutate, count)
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_people(self):
        self.assertError(lambda d: d["people"].pop() and d["people"].pop() and d["people"].pop(), "people: needs at least 2")
        self.assertError(lambda d: d["people"][1].update(key=d["people"][0]["key"]), "people[1].key: repeats")
        self.assertError(lambda d: d["people"][1].update(upn=d["people"][0]["upn"].upper()), "people[1].upn: repeats")
        self.assertError(lambda d: d["people"][0].update(upn=ADMIN), "is the administrator")
        self.assertError(lambda d: d["people"][0].update(key="Bad Key"), "people[0].key: must be lowercase")
        self.assertError(lambda d: d.update(adminUpn="nope"), "adminUpn: must be a user principal name")
        self.assertError(lambda d: d["people"][0].update(upn="bad"), "people[0].upn: must be a user principal name")
        doc = sample_doc()
        doc["people"][0]["upn"] = "someone@elsewhere.example"
        self.assertTrue(any("spans several domains" in w for w in demo.validate(doc)[1]))

    def test_mail(self):
        self.assertError(lambda d: d["mail"][0]["messages"][0].update(to=[]), "mail[0].messages[0].to: needs at least 1")
        self.assertError(lambda d: d["mail"][0]["messages"][0].update(to=["ghost"]), "unknown person 'ghost'")
        self.assertError(lambda d: d["mail"][0]["messages"][1].update(to=["x"]), "reply all")

        def outsider(doc):
            thread = doc["mail"][0]
            on_thread = {thread["messages"][0]["from"], *thread["messages"][0]["to"], *thread["messages"][0].get("cc", [])}
            thread["messages"][1]["from"] = next(p["key"] for p in doc["people"] if p["key"] not in on_thread)
        self.assertError(outsider, "is not on this thread", count=8)
        self.assertError(lambda d: d["mail"][0]["messages"][0].update(body="See {{file:nope}}"), "unknown file")
        self.assertError(lambda d: d["mail"][0]["messages"][0].update(body="Hi {{name}}"), "unknown placeholder")
        self.assertError(lambda d: d["mail"].append(copy.deepcopy(d["mail"][0])), "repeats the mail thread key")

    def test_chats(self):
        self.assertError(lambda d: d["chats"][1].pop("topic"), "chats[1].topic: is required")
        self.assertError(lambda d: d["chats"][0]["messages"][0].update(**{"from": d["people"][3]["key"]}),
                         "is not a member of this chat")
        self.assertError(lambda d: d["chats"].append({"key": "again", "members": list(d["chats"][0]["members"]),
                                                      "messages": [{"from": d["chats"][0]["members"][0], "text": "x"}]}),
                         "repeats the one-on-one chat")

    def test_channel(self):
        self.assertError(lambda d: d["channel"]["channels"][0].update(name="Bad/Name"), "cannot contain")
        self.assertError(lambda d: d["channel"]["channels"].append(copy.deepcopy(d["channel"]["channels"][0])),
                         "repeats the channel")
        self.assertError(lambda d: d["channel"].update(members=[]) or d["channel"]["channels"][0]["posts"][0].update(
            **{"from": d["people"][1]["key"]}), "is not on the team")
        self.assertError(lambda d: d["channel"]["channels"][0].update(posts=[]), "needs at least 1")

    def test_meetings(self):
        self.assertError(lambda d: d["meetings"][0].update(day=40), "meetings[0].day")
        self.assertError(lambda d: d["meetings"][0].update(day=True), "meetings[0].day")
        self.assertError(lambda d: d["meetings"][0].update(start="9:30"), "24-hour time")
        self.assertError(lambda d: d["meetings"][0].update(minutes=5), "15 to 240")
        self.assertError(lambda d: d["meetings"][0].update(attendees=[d["meetings"][0]["organizer"]]),
                         "already the sender or organizer")

    def test_files(self):
        self.assertError(lambda d: d["files"][0].update(name="Bad:Name.xlsx"), "files[0].name: cannot contain")
        self.assertError(lambda d: d["files"][0].update(name="Asset.docx"), "must end with .xlsx")
        self.assertError(lambda d: d["files"][0].update(folder="../up"), "folder")
        self.assertError(lambda d: d["files"][0].update(type="pdf"), "must be one of docx, xlsx, pptx")
        self.assertError(lambda d: d["files"][0]["sheets"][0].update(name="A/B"), "cannot contain")
        self.assertError(lambda d: d["files"][0]["sheets"][0]["rows"].append([{"x": 1}]), "cells must be")
        self.assertError(lambda d: d["files"][1].update(sections=[{"heading": "Only a heading"}]),
                         "needs at least one paragraph or bullet")
        self.assertError(lambda d: d["files"][2]["slides"][0].update(bullets=["x"] * 9), "allows at most 8")

    def test_guide_and_document(self):
        self.assertError(lambda d: d["guide"]["prompts"][0].update(**{"as": "ghost"}), "unknown person")
        self.assertError(lambda d: d.update(version=2), "version: must be 1")
        self.assertError(lambda d: [d.pop(k) for k in ("files", "mail", "chats", "channel", "meetings")], "has no content")
        doc = sample_doc()
        doc.pop("guide")
        self.assertTrue(any(w.startswith("guide:") for w in demo.validate(doc)[1]))
        self.assertEqual(demo.validate([])[0], ["demo: must hold a JSON object"])

    def test_counts(self):
        counts = demo.counts(sample_doc(5))
        self.assertEqual((counts["people"], counts["files"], counts["mailThreads"], counts["meetings"]), (5, 3, 3, 2))
        self.assertEqual(counts["channels"], 2)


class TextTests(unittest.TestCase):
    def test_html(self):
        rendered = text.to_html("Hi <team> & **all**,\n\n- one {{file:plan}}\n- two\n\nLine a\nline b",
                                {"plan": ("Plan.docx", "https://x.example/p?a=1&b=2")})
        self.assertEqual(rendered, '<p>Hi &lt;team&gt; &amp; <b>all</b>,</p><ul><li>one <a href="https://x.example/p?'
                                   'a=1&amp;b=2">Plan.docx</a></li><li>two</li></ul><p>Line a<br>line b</p>')
        self.assertEqual(text.to_html("{{file:gone}}"), "<p>gone</p>")
        self.assertEqual(text.to_plain("See {{file:plan}}.", {"plan": "Plan.docx"}), "See Plan.docx.")
        self.assertEqual(text.unknown_placeholders("{{file:ok}} {{bad}}"), ["{{bad}}"])


class DemoCliTests(WorkspaceCase):
    def test_new_validate_preview_list(self):
        code, out, _ = run_cli("demo", "new", "launch", "--admin", ADMIN, "--person", f"{PEOPLE[0]}=Megan Bowen",
                               "--person", PEOPLE[1], "--person", PEOPLE[2])
        self.assertEqual(code, 0, out)
        self.assertTrue(out["valid"])
        self.assertEqual(out["counts"]["people"], 3)
        self.assertIn("demo preview --demo launch", out["next"][0])
        code, again, _ = run_cli("demo", "new", "launch", "--admin", ADMIN, "--person", PEOPLE[0], "--person", PEOPLE[1])
        self.assertEqual((code, again["reason"]), (1, "exists"))

        code, checked, _ = run_cli("demo", "validate", "--demo", "launch")
        self.assertEqual((code, checked["sha256"]), (0, out["sha256"]))

        receipts = FakeTenant().receipts_file(self.tmp / "status.json")
        code, preview, _ = run_cli("demo", "preview", "--demo", "launch", "--receipts-file", receipts)
        self.assertEqual(code, 0, preview)
        page = (self.home / "demos" / "launch" / "preview.html").read_text(encoding="utf-8")
        self.assertIn("Pump B maintenance window", page)
        self.assertIn(out["sha256"], page)
        self.assertIn("never the administrator", page)
        self.assertIn("aka.ms/demoiq", page)
        self.assertIn(f"--approve {out['sha256']}", preview["next"][1])
        self.assertIn(FakeTenant().tenant, preview["next"][1])

        code, listed, _ = run_cli("demo", "list")
        self.assertEqual([d["demo"] for d in listed["demos"]], ["launch"])

    def test_preview_refuses_an_invalid_demo(self):
        doc = self.write_demo("broken", PEOPLE[:3])
        doc["mail"][0]["messages"][0]["to"] = ["ghost"]
        (self.home / "demos" / "broken" / "demo.json").write_text(json.dumps(doc), encoding="utf-8")
        code, out, _ = run_cli("demo", "preview", "--demo", "broken")
        self.assertEqual((code, out["reason"]), (1, "invalid-demo"))
        self.assertTrue(any("ghost" in e for e in out["errors"]))
        self.assertFalse((self.home / "demos" / "broken" / "preview.html").exists())

    def test_new_checks_its_people(self):
        for args, reason in ((["--person", PEOPLE[0]], "people"), (["--person", "bad", "--person", PEOPLE[1]], "bad-upn"),
                             (["--person", ADMIN, "--person", PEOPLE[1]], "people"),
                             (["--person", PEOPLE[0], "--person", PEOPLE[0]], "people")):
            code, out, _ = run_cli("demo", "new", "x", "--admin", ADMIN, *args)
            self.assertEqual((code, out["reason"]), (1, reason), args)

    def test_pages_escape_authored_text(self):
        doc = sample_doc(3)
        doc["title"] = "<script>alert(1)</script>"
        doc["mail"][0]["messages"][0]["body"] = "<img src=x onerror=alert(1)>"
        page = pages.preview("x", doc, "0" * 64)
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<img src=x", page)


if __name__ == "__main__":
    unittest.main()

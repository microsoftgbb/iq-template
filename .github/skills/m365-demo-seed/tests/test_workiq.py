import json
import unittest

from support import WorkspaceCase, run_cli
from fake_tenant import FakeTenant

from m365demo import runs, workiq
from m365demo.common import Usage

ADMIN = "admin@contoso-demo.example"
PEOPLE = [f"user{i}@contoso-demo.example" for i in range(4)]
RUN = "20260101-000000-seed-launch"


class WorkIqTests(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.doc = self.write_demo("launch", PEOPLE)
        self.fabricate_run(RUN, self.doc)
        self.run = runs.Run(RUN)

    def results(self, answers, asked_as=PEOPLE[0]):
        path = self.tmp / "results.json"
        path.write_text(json.dumps({"askedAs": asked_as, "answers": answers}), encoding="utf-8")
        return path

    def test_prompts_put_copilot_chat_first_and_name_the_demo_user(self):
        out = workiq.prompts(self.run, 3)
        self.assertEqual([p["surface"] for p in out["prompts"]],
                         ["Microsoft 365 Copilot Chat", "Microsoft 365 Copilot Chat", "Outlook"])
        self.assertTrue(all(p["as"] in out["demoUsers"] for p in out["prompts"]))
        self.assertEqual(out["demoUsers"], PEOPLE)
        self.assertEqual(len(workiq.prompts(self.run, 50)["prompts"]), len(self.doc["guide"]["prompts"]))

    def test_citation_matching(self):
        names = ["Pump B Maintenance Plan.docx", "Pump B maintenance window: parts and contractor",
                 "Weekly operations sync"]
        self.assertEqual(workiq.matches("RE: Pump B maintenance window: parts and contractor", names), [names[1]])
        self.assertEqual(workiq.matches("pump b maintenance plan", names), [names[0]])
        self.assertEqual(workiq.matches("Notes from the Weekly operations sync", names), [names[2]])
        self.assertEqual(workiq.matches("Weekly", names), [], "short fragments do not count")
        self.assertEqual(workiq.matches("", names), [])

    def test_verdicts(self):
        found = workiq.record(self.run, self.results([
            {"prompt": self.doc["guide"]["prompts"][0]["prompt"], "answered": True,
             "citations": [{"title": "Pump B Maintenance Plan.docx", "url": "https://x"}, {"title": "Unrelated"}]},
            {"prompt": "Anything else?", "answered": False, "citations": []}]))
        self.assertEqual((found["verdict"], found["prompts"], found["answered"], found["withSeededCitations"]),
                         ("found-seeded-content", 2, 1, 1))
        self.assertTrue(found["askedAsIsDemoUser"])
        self.assertEqual(found["answers"][0]["seededCitations"], ["Pump B Maintenance Plan.docx"])
        answered = workiq.record(self.run, self.results([{"prompt": "x", "answered": True,
                                                          "citations": [{"title": "Something else entirely"}]}]))
        self.assertEqual(answered["verdict"], "answered-without-seeded-citations")
        nothing = workiq.record(self.run, self.results([{"prompt": "x", "answered": False}], asked_as=ADMIN))
        self.assertEqual((nothing["verdict"], nothing["askedAsIsDemoUser"]), ("not-found", False))
        stored = json.loads((self.home / "runs" / RUN / "workiq.json").read_text(encoding="utf-8"))
        self.assertEqual([c["verdict"] for c in stored["checks"]],
                         ["found-seeded-content", "answered-without-seeded-citations", "not-found"])
        self.assertEqual(stored["latest"]["verdict"], "not-found")

    def test_only_created_items_count_as_seeded(self):
        self.fabricate_run("20260101-000000-seed-empty", self.doc, created=False)
        out = workiq.record(runs.Run("20260101-000000-seed-empty"), self.results([
            {"prompt": "x", "answered": True, "citations": [{"title": "Pump B Maintenance Plan.docx"}]}]))
        self.assertEqual(out["verdict"], "answered-without-seeded-citations")

    def test_bad_results_are_refused(self):
        for answers in ([], [{"prompt": "x"}], [{"prompt": "x", "answered": "yes"}],
                        [{"prompt": "x", "answered": True, "citations": [{"url": "u"}]}]):
            with self.assertRaises(Usage):
                workiq.record(self.run, self.results(answers))

    def test_status_reports_verified_only_for_a_demo_user_finding_seeded_content(self):
        receipts = FakeTenant().receipts_file(self.tmp / "status.json")
        self.store_passwords({upn: "x" for upn in PEOPLE})
        code, status, _ = run_cli("status", "--receipts-file", receipts)
        self.assertEqual(status["state"], "seeded")
        self.assertIn(f"workiq prompts --run {RUN}", status["next"][-1])
        cited = [{"prompt": "x", "answered": True, "citations": [{"title": "Weekly operations sync"}]}]
        code, out, _ = run_cli("workiq", "record", "--run", RUN, "--results", self.results(cited, asked_as=ADMIN))
        self.assertEqual((code, out["verdict"]), (0, "found-seeded-content"))
        code, status, _ = run_cli("status", "--receipts-file", receipts)
        self.assertEqual(status["state"], "seeded", "asked as the administrator, it says little about the content")
        run_cli("workiq", "record", "--run", RUN, "--results", self.results(cited))
        code, status, _ = run_cli("status", "--receipts-file", receipts)
        self.assertEqual(status["state"], "workiq-verified")
        self.assertEqual(status["workiq"]["withSeededCitations"], 1)

    def test_cli_prompts(self):
        code, out, _ = run_cli("workiq", "prompts", "--run", RUN, "--count", "2")
        self.assertEqual((code, len(out["prompts"])), (0, 2))
        code, out, _ = run_cli("workiq", "prompts", "--run", "not-a-run")
        self.assertEqual((code, out["reason"]), (1, "bad-run"))


if __name__ == "__main__":
    unittest.main()

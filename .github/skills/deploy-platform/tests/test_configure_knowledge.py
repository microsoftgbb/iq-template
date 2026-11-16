import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import configure_knowledge as knowledge  # noqa: E402

LAKEHOUSE = "22222222-2222-4222-8222-222222222222"
ONTOLOGY = "33333333-3333-4333-8333-333333333333"
EARLIER_LAKEHOUSE = "44444444-4444-4444-8444-444444444444"
EARLIER_ONTOLOGY = "55555555-5555-4555-8555-555555555555"
LOGICAL_ID = "66666666-6666-4666-8666-666666666666"
STARTER = 'project_name = "demo"\nknowledge_lakehouse_id = null\nknowledge_ontologies = {}\n'


def configure(text, replace=False):
    return knowledge.configure(text, LAKEHOUSE, ONTOLOGY, "scenario", 'The "site and asset" facts.', replace)


def earlier():
    return knowledge.configure(STARTER, EARLIER_LAKEHOUSE, EARLIER_ONTOLOGY, "earlier-sample", "Sample facts.")[0]


class ConfigureTests(unittest.TestCase):
    def test_the_starter_settings_are_filled_once(self):
        text, replaced = configure(STARTER)
        self.assertEqual(replaced, [])
        self.assertIn(f'knowledge_lakehouse_id = "{LAKEHOUSE}"\n', text)
        self.assertIn(f'  scenario = {{\n    ontology_id = "{ONTOLOGY}"\n'
                      '    description = "The \\"site and asset\\" facts."\n', text)
        self.assertEqual(configure(text), (text, []))

    def test_an_earlier_registration_is_replaced_only_when_asked(self):
        with self.assertRaises(knowledge.ReplacementRequired) as stop:
            configure(earlier())
        message = str(stop.exception)
        self.assertIn(f'knowledge_lakehouse_id "{EARLIER_LAKEHOUSE}" with "{LAKEHOUSE}"', message)
        self.assertIn(f"knowledge_ontologies.earlier-sample (ontology {EARLIER_ONTOLOGY}) with scenario "
                      f"(ontology {ONTOLOGY})", message)
        text, replaced = configure(earlier(), replace=True)
        self.assertEqual((text, len(replaced)), (configure(STARTER)[0], 2))

    def test_custom_registrations_are_never_replaced(self):
        second = earlier().replace("  }\n}", f'  }}\n  other = {{\n    ontology_id = "{ONTOLOGY}"\n'
                                             '    description = "x"\n  }\n}')
        commented = earlier().replace('description = "Sample facts."', 'description = "Sample facts." # kept')
        variable = earlier().replace(f'"{EARLIER_LAKEHOUSE}"', "var.lakehouse")
        for custom in (second, commented, variable):
            with self.subTest(custom=custom), self.assertRaisesRegex(ValueError, "already customized"):
                configure(custom, replace=True)

    def test_windows_line_endings_are_kept(self):
        text, _ = configure(earlier().replace("\n", "\r\n"), replace=True)
        self.assertEqual(text, configure(STARTER)[0].replace("\n", "\r\n"))


class MainTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "infra").mkdir()
        (self.root / "workspace" / "Scenario.Lakehouse").mkdir(parents=True)
        (self.root / "workspace" / "Scenario.Lakehouse" / ".platform").write_text(
            json.dumps({"config": {"logicalId": LOGICAL_ID}}), encoding="utf-8")
        self.tfvars = self.root / "infra" / "terraform.tfvars"
        self.tfvars.write_text(earlier(), encoding="utf-8")

    def main(self, *extra, lakehouse=LAKEHOUSE):
        output = io.StringIO()
        arguments = ["--lakehouse-id", lakehouse, "--ontology-id", ONTOLOGY, "--registration", "scenario",
                     "--description", "Site and asset facts.", *extra]
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            try:
                knowledge.main(arguments, root=self.root)
                code = 0
            except SystemExit as exit:
                code = exit.code
        return code, output.getvalue()

    def test_replacement_needs_the_flag_and_reports_what_it_replaces(self):
        before = self.tfvars.read_bytes()
        code, output = self.main()
        self.assertEqual(code, knowledge.REPLACEMENT_REQUIRED)
        self.assertTrue(output.startswith("Replace the earlier knowledge registration"))
        self.assertEqual(self.main("--replace", "--dry-run")[0], 0)
        self.assertEqual(self.tfvars.read_bytes(), before)
        code, output = self.main("--replace")
        self.assertEqual(code, 0)
        self.assertIn("Replaced knowledge_lakehouse_id", output)
        self.assertIn("scenario = {", self.tfvars.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(os.stat(self.tfvars).st_mode), 0o600)

    def test_logical_ids_and_invalid_registrations_are_refused(self):
        before = self.tfvars.read_bytes()
        self.assertEqual(self.main("--replace", lakehouse=LOGICAL_ID)[0], 1)
        for registration in ("Scenario", "s--ample", "x"):
            with self.subTest(registration=registration):
                code, output = self.main("--replace", "--registration", registration)
                self.assertEqual(code, 1, output)
        self.assertEqual(self.tfvars.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import studio_agent as studio  # noqa: E402


SCHEMA = "iq_demo_assistant"
WORKSPACE = "11111111-1111-4111-8111-111111111111"
ONTOLOGY = "22222222-2222-4222-8222-222222222222"
SEARCH = "https://srch-demo.search.windows.net"


def connection(name, parameter_set, values, status="Connected"):
    return {"name": name, "properties": {
        "connectionParametersSet": {"name": parameter_set, "values": {k: {"value": v} for k, v in values.items()}},
        "statuses": [{"status": status}],
    }}


def ready_state():
    configuration = {"settings": {"GenerativeActionsEnabled": True},
                     "aISettings": {key: False for key in studio.GROUNDING_OFF}}
    bot = {"botid": "b", "accesscontrolpolicy": 1, "configuration": json.dumps(configuration),
           "publishedon": "2026-10-14T12:00:00Z",
           "synchronizationstatus": json.dumps({"currentSynchronizationState": {"state": "Synchronized"}})}
    components = [
        {"schemaname": f"{SCHEMA}.action.{action}", "modifiedon": "2026-10-14T11:00:00Z",
         "data": f"mode: Invoker\noperationId: {operation}\nconnectionReference: {studio.reference_name(SCHEMA, connector)}"}
        for action, (operation, connector) in studio.ACTIONS.items()
    ] + [
        {"schemaname": f"{SCHEMA}.topic.ConnectionProof", "modifiedon": "2026-10-14T11:00:00Z", "data": "kind: AdaptiveDialog"},
        {"schemaname": f"{SCHEMA}.gpt.default", "modifiedon": "2026-10-14T11:00:00Z", "data": "webBrowsing: false"},
    ]
    references = {studio.reference_name(SCHEMA, studio.FABRIC_IQ): "a" * 32,
                  studio.reference_name(SCHEMA, studio.FOUNDRY_IQ): "b" * 32}
    statuses = {"a" * 32: "Connected", "b" * 32: "Connected"}
    return bot, components, references, statuses


class StudioAgentTests(unittest.TestCase):
    def test_schema_names_follow_the_publisher_prefix_contract(self):
        self.assertEqual(studio.schema_name(SCHEMA), SCHEMA)
        for value in ("", "assistant", "iq-demo", "1q_demo", "averylongprefix_demo"):
            with self.assertRaises(studio.StudioError):
                studio.schema_name(value)

    def test_the_shipped_agent_renders_only_into_a_pac_workspace(self):
        values = {"agent_schema": SCHEMA, "agent_display_name": "Demo Assistant",
                  "knowledge_base_name": "demo-knowledge", "search_api_version": "2026-08-01-preview"}
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with self.assertRaises(studio.StudioError):
                studio.render(studio.SOURCE, workspace, values)
            (workspace / ".mcs").mkdir()
            written = studio.render(studio.SOURCE, workspace, values)
            self.assertNotIn("README.md", written)
            self.assertIn("actions/FoundryIQKnowledge.mcs.yml", written)
            rendered = "".join((workspace / name).read_text(encoding="utf-8") for name in written)
            self.assertNotIn("{{", rendered)
            self.assertIn(studio.reference_name(SCHEMA, studio.FABRIC_IQ), rendered)
            self.assertIn("demo-knowledge", rendered)
            self.assertIn(f"{SCHEMA}.topic.Escalate", rendered)
            self.assertEqual(rendered.count("mode: Invoker"), 2)

    def test_unknown_placeholders_fail_instead_of_rendering_partially(self):
        with self.assertRaisesRegex(studio.StudioError, "Unknown placeholders: other"):
            studio.render_text("{{agent_schema}} {{other}}", {"agent_schema": SCHEMA})

    def test_configuration_turns_grounding_off_and_preserves_other_settings(self):
        original = json.dumps({"$kind": "BotConfiguration", "gPTSettings": {"x": 1},
                               "aISettings": {"$kind": "AISettings", "useModelKnowledge": True, "optInUseLatestModels": False}})
        text, changed = studio.desired_configuration(original)
        configuration = json.loads(text)
        self.assertTrue(changed)
        self.assertEqual(configuration["gPTSettings"], {"x": 1})
        self.assertEqual(configuration["aISettings"]["$kind"], "AISettings")
        self.assertTrue(configuration["settings"]["GenerativeActionsEnabled"])
        self.assertTrue(all(configuration["aISettings"][key] is False for key in studio.GROUNDING_OFF))
        self.assertFalse(studio.desired_configuration(text)[1])

    def test_connections_are_reused_only_for_the_same_target(self):
        expected = studio.expected_connections(WORKSPACE, ONTOLOGY, SEARCH)
        fabric_set, fabric_values = expected[studio.FABRIC_IQ]
        search_set, search_values = expected[studio.FOUNDRY_IQ]
        connected = connection("c" * 32, fabric_set, fabric_values)
        other = connection("d" * 32, fabric_set, {"workspace_id": WORKSPACE, "ontology_id": WORKSPACE})
        self.assertEqual(studio.plan_connection([other, connected], fabric_set, fabric_values), ("reuse", "c" * 32, "Connected"))
        pending = connection("e" * 32, search_set, {"targetUri": SEARCH + "/"}, status="Error")
        self.assertEqual(studio.plan_connection([pending], search_set, search_values), ("sign-in", "e" * 32, "NotConnected"))
        self.assertEqual(studio.plan_connection([other], fabric_set, fabric_values), ("create", None, None))

    def test_a_correct_published_agent_is_ready(self):
        self.assertEqual(studio.evaluate(SCHEMA, *ready_state()), [])

    def test_unsafe_or_incomplete_agents_are_blocked(self):
        bot, components, references, statuses = ready_state()
        bot["accesscontrolpolicy"] = 2
        components[0]["data"] = components[0]["data"].replace("Invoker", "Maker")
        components[-1]["modifiedon"] = "2026-10-14T13:00:00Z"
        references[studio.reference_name(SCHEMA, studio.FOUNDRY_IQ)] = None
        statuses["a" * 32] = "NotConnected"
        blockers = " ".join(studio.evaluate(SCHEMA, bot, components, references, statuses))
        for expected in ("Agent readers", "Invoker", "not published", "unbound", "must sign in"):
            self.assertIn(expected, blockers)

    def test_find_reports_whether_the_agent_exists_without_changing_it(self):
        for bots, expected in (([], ("missing", None)), ([{"botid": "b", "schemaname": SCHEMA}], ("found", "b"))):
            fake = (lambda entity, params, bots=bots: bots), None
            stdout = io.StringIO()
            with mock.patch.object(studio, "dataverse", return_value=fake), contextlib.redirect_stdout(stdout):
                code = studio.main(["find", "--tenant-id", WORKSPACE, "--dataverse-url", "https://org.crm.dynamics.com",
                                    "--agent-schema", SCHEMA])
            report = json.loads(stdout.getvalue())
            self.assertEqual((code, report["status"], report["bot_id"]), (0, *expected))

    def test_find_proves_the_dataverse_url_belongs_to_the_chosen_environment(self):
        organization = {"Detail": {"EnvironmentId": ONTOLOGY, "TenantId": WORKSPACE}}
        stderr = io.StringIO()
        with mock.patch.object(studio, "token", return_value="token"), \
                mock.patch.object(studio, "call", return_value=organization), contextlib.redirect_stderr(stderr):
            code = studio.main(["find", "--tenant-id", WORKSPACE, "--dataverse-url", "https://org.crm.dynamics.com",
                                "--agent-schema", SCHEMA, "--environment-id", "33333333-3333-4333-8333-333333333333"])
        self.assertEqual(code, 2)
        self.assertIn(f"belongs to environment {ONTOLOGY}", stderr.getvalue())

    def test_bearer_requests_never_follow_redirects(self):
        self.assertIsNone(studio.NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example"))
        self.assertTrue(any(isinstance(handler, studio.NoRedirect) for handler in studio.OPENER.handlers))

    def test_find_diagnoses_default_environments_before_looking_up_agents(self):
        for prefix in ("Default-", "default-"):
            with self.subTest(prefix=prefix):
                organization = {"Detail": {"EnvironmentId": prefix + WORKSPACE, "TenantId": WORKSPACE}}
                stderr = io.StringIO()
                with mock.patch.object(studio, "token", return_value="token"), \
                        mock.patch.object(studio, "call", return_value=organization), \
                        mock.patch.object(studio, "dataverse") as dataverse, contextlib.redirect_stderr(stderr):
                    code = studio.main(["find", "--tenant-id", WORKSPACE,
                                        "--dataverse-url", "https://org.crm.dynamics.com",
                                        "--agent-schema", SCHEMA, "--environment-id", WORKSPACE])
                self.assertEqual(code, 2)
                self.assertIn("non-Default environment", stderr.getvalue())
                self.assertIn("dotnet pac env list", stderr.getvalue())
                self.assertNotIn("EnvironmentId must be a GUID", stderr.getvalue())
                dataverse.assert_not_called()

    def test_find_still_checks_tenant_and_accepts_the_matching_environment(self):
        for tenant, expected_code in ((WORKSPACE, 0), (ONTOLOGY, 2)):
            with self.subTest(tenant=tenant):
                organization = {"Detail": {"EnvironmentId": WORKSPACE, "TenantId": tenant}}
                stderr, stdout = io.StringIO(), io.StringIO()
                with mock.patch.object(studio, "token", return_value="token"), \
                        mock.patch.object(studio, "call", return_value=organization), \
                        mock.patch.object(studio, "dataverse", return_value=(mock.Mock(return_value=[]), None)) as dataverse, \
                        contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                    code = studio.main(["find", "--tenant-id", WORKSPACE,
                                        "--dataverse-url", "https://org.crm.dynamics.com",
                                        "--agent-schema", SCHEMA, "--environment-id", WORKSPACE])
                self.assertEqual(code, expected_code)
                if expected_code:
                    self.assertIn("another tenant", stderr.getvalue())
                    dataverse.assert_not_called()
                else:
                    self.assertEqual(json.loads(stdout.getvalue()), {"status": "missing", "bot_id": None})
                    dataverse.assert_called_once()

    def test_cloud_commands_validate_inputs_before_any_sign_in(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = studio.main(["check", "--tenant-id", "not-a-guid", "--dataverse-url", "https://org.crm.dynamics.com",
                                "--agent-schema", SCHEMA, "--environment-id", WORKSPACE])
        self.assertEqual(code, 2)
        self.assertIn("tenant-id must be a GUID", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

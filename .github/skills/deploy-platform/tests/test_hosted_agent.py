import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hosted_agent as hosted  # noqa: E402

REPO = Path(__file__).resolve().parents[4]
DEFINITION = REPO / "agents" / "scenario-assistant" / "hosted-agent.json"
VALUES = {
    "chat_deployment": "chat",
    "toolbox_endpoint": "https://ai-demo.services.ai.azure.com/api/projects/p/toolboxes/demo-tools/mcp?api-version=v1",
    "fabric_iq_server_url": "https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/w/items/o/ontologyEndpoint",
    "fabric_iq_connection": "fabric-scenario",
    "knowledge_base_mcp_endpoint": "https://srch.search.windows.net/knowledgebases/kb/mcp?api-version=2026-08-01-preview",
    "knowledge_base_connection": "knowledge-base",
}


def write(data):
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / "hosted-agent.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return directory, path


class RenderTests(unittest.TestCase):
    def test_the_shipped_definition_renders_every_placeholder(self):
        data = hosted.render(DEFINITION, VALUES)
        text = json.dumps(data)
        self.assertNotIn("{{", text)
        self.assertEqual(data["agent"]["environment_variables"]["TOOLBOX_ENDPOINT"], VALUES["toolbox_endpoint"])
        labels = {tool["server_label"]: tool for tool in data["toolbox"]["tools"]}
        self.assertEqual(labels["scenario_ontology"]["type"], "fabric_iq_preview")
        self.assertEqual(labels["knowledge_base"]["allowed_tools"], ["knowledge_base_retrieve"])

    def test_missing_or_unknown_placeholders_are_refused(self):
        values = dict(VALUES, chat_deployment="")
        with self.assertRaisesRegex(hosted.DefinitionError, "has no value"):
            hosted.render(DEFINITION, values)
        values = dict(VALUES)
        values.pop("knowledge_base_connection")
        with self.assertRaisesRegex(hosted.DefinitionError, "Unknown placeholder"):
            hosted.render(DEFINITION, values)
        # A spelling the renderer does not fill is refused rather than published as a literal.
        for spelling in ("{{ chat_deployment }}", "{{Chat}}"):
            data = hosted.render(DEFINITION, VALUES)
            data["agent"]["environment_variables"]["AZURE_AI_MODEL_DEPLOYMENT_NAME"] = spelling
            with self.subTest(spelling=spelling), self.assertRaisesRegex(hosted.DefinitionError, "still contains placeholders"):
                hosted.validate(data)

    def test_unsafe_or_unsupported_settings_are_refused(self):
        base = json.loads(DEFINITION.read_text(encoding="utf-8"))
        changes = (
            (("agent", "cpu"), "3", "documented hosted-agent size"),
            (("agent", "environment_variables"), {"FOUNDRY_PROJECT_ENDPOINT": "x"}, "reserved"),
            (("agent", "protocol_versions"), [{"protocol": "invocations", "version": "2.0.0"}], "responses protocol"),
        )
        for path, value, pattern in changes:
            with self.subTest(path=path):
                data = json.loads(json.dumps(base))
                data[path[0]][path[1]] = value
                directory, file = write(data)
                self.addCleanup(directory.cleanup)
                with self.assertRaisesRegex(hosted.DefinitionError, pattern):
                    hosted.render(file, VALUES)
        data = json.loads(json.dumps(base))
        data["toolbox"]["tools"][0]["require_approval"] = "always"
        directory, file = write(data)
        self.addCleanup(directory.cleanup)
        with self.assertRaisesRegex(hosted.DefinitionError, "require_approval"):
            hosted.render(file, VALUES)
        data = json.loads(json.dumps(base))
        data["toolbox"]["tools"][1]["server_label"] = data["toolbox"]["tools"][0]["server_label"]
        directory, file = write(data)
        self.addCleanup(directory.cleanup)
        with self.assertRaisesRegex(hosted.DefinitionError, "unique"):
            hosted.render(file, VALUES)


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.data = hosted.render(DEFINITION, VALUES)
        self.desired = hosted.agent_definition(self.data["agent"], "crdemo.azurecr.io/scenario-assistant:abc")

    def test_a_version_matches_only_with_the_same_image_size_protocols_and_settings(self):
        current = json.loads(json.dumps(self.desired))
        self.assertTrue(hosted.definition_matches(current, self.desired))
        for key, value in (("container_configuration", {"image": "crdemo.azurecr.io/scenario-assistant:old"}),
                           ("cpu", "1"), ("environment_variables", {}), ("kind", "prompt"),
                           ("protocol_versions", [{"protocol": "responses", "version": "1.0.0"}])):
            with self.subTest(key=key):
                changed = dict(current, **{key: value})
                self.assertFalse(hosted.definition_matches(changed, self.desired))
        self.assertFalse(hosted.definition_matches(None, self.desired))

    def test_numeric_sizes_are_published_and_compared_in_canonical_form(self):
        numeric = dict(self.data["agent"], cpu=0.5)
        hosted.validate(dict(self.data, agent=numeric))
        desired = hosted.agent_definition(numeric, "crdemo.azurecr.io/scenario-assistant:abc")
        self.assertEqual((desired["cpu"], desired["memory"]), ("0.5", "1Gi"))
        # The service may echo either form; an unchanged definition must not publish a new version.
        for echoed in ("0.5", 0.5):
            self.assertTrue(hosted.definition_matches(dict(desired, cpu=echoed), desired))
        self.assertTrue(hosted.definition_matches(dict(self.desired, cpu=1.0, memory="2Gi"),
                                                  dict(self.desired, cpu="1", memory="2Gi")))

    def test_published_tools_match_by_label_and_every_desired_setting(self):
        tools = self.data["toolbox"]["tools"]
        published = [dict(tool, id=f"tool-{index}") for index, tool in enumerate(reversed(tools))]
        self.assertTrue(hosted.tools_match(published, tools))
        changed = json.loads(json.dumps(published))
        changed[0]["server_url"] = "https://elsewhere.example/mcp"
        self.assertFalse(hosted.tools_match(changed, tools))
        self.assertFalse(hosted.tools_match(published[:1], tools))
        self.assertFalse(hosted.tools_match(None, tools))

    def test_the_latest_version_is_read_from_the_agent_object(self):
        self.assertEqual(hosted.latest_version({"versions": {"latest": {"version": "3"}}}), {"version": "3"})
        for value in (None, {}, {"versions": {}}, {"versions": {"latest": {"status": "active"}}}):
            self.assertIsNone(hosted.latest_version(value))


def message(text):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


class EvidenceTests(unittest.TestCase):
    def test_tool_calls_pair_function_outputs_and_inline_mcp_outputs(self):
        response = {"status": "completed", "output": [
            {"type": "function_call", "name": "scenario_ontology___ask_ontology", "call_id": "c1", "status": "completed"},
            {"type": "function_call_output", "call_id": "c1", "output": "Pump B at North Works"},
            {"type": "mcp_call", "name": "knowledge_base_retrieve", "server_label": "knowledge_base",
             "output": {"documents": ["starter.txt"]}},
            message("answer"),
        ]}
        calls = hosted.tool_calls(response)
        self.assertEqual([call["name"] for call in calls], ["scenario_ontology___ask_ontology", "knowledge_base_retrieve"])
        self.assertEqual(calls[0]["output"], "Pump B at North Works")
        self.assertIn("starter.txt", calls[1]["output"])
        self.assertEqual(hosted.output_text(response), "answer")

    def test_live_facts_must_come_from_the_ontology_tool_output(self):
        good = {"status": "completed", "output": [
            {"type": "function_call", "name": "scenario_ontology___ask_ontology", "call_id": "c1"},
            {"type": "function_call_output", "call_id": "c1", "output": "Pump B | Maintenance | North Works"},
            message("Pump B is at North Works.")]}
        problems, evidence = hosted.ontology_evidence(good, "scenario_ontology", ("Pump B", "North Works"))
        self.assertEqual(problems, [])
        self.assertEqual(evidence["tool_calls"], ["scenario_ontology___ask_ontology"])
        answer_only = {"status": "completed", "output": [message("Pump B is at North Works.")]}
        problems, _ = hosted.ontology_evidence(answer_only, "scenario_ontology", ("Pump B",))
        self.assertEqual(problems, ["no scenario_ontology tool call"])
        # A similarly named tool is not the ontology tool, and a tool result without an answer is not ready.
        lookalike = json.loads(json.dumps(good))
        lookalike["output"][0]["name"] = "not_scenario_ontology___ask"
        self.assertIn("no scenario_ontology tool call", hosted.ontology_evidence(lookalike, "scenario_ontology", ("Pump B",))[0])
        silent = dict(good, output=good["output"][:2])
        self.assertEqual(hosted.ontology_evidence(silent, "scenario_ontology", ("Pump B",))[0],
                         ["the response has no assistant answer"])
        failed = {"status": "completed", "output": [
            {"type": "mcp_call", "name": "ask_ontology", "server_label": "scenario_ontology", "error": "denied"},
            message("unavailable")]}
        problems, _ = hosted.ontology_evidence(failed, "scenario_ontology", ("Pump B",))
        self.assertIn("the scenario_ontology tool call failed", problems)
        problems, _ = hosted.ontology_evidence({"status": "failed", "error": {"message": "boom"}}, "x", ())
        self.assertIn("boom", problems[0])
        # Agent Framework turns a tool exception or MCP isError into an "Error:" result; its text is never evidence.
        errored = json.loads(json.dumps(good))
        errored["output"][1]["output"] = "Error: Function failed. Exception: Pump B at North Works"
        problems, _ = hosted.ontology_evidence(errored, "scenario_ontology", ("Pump B", "North Works"))
        self.assertEqual(problems, ["the scenario_ontology tool call failed",
                                    "the scenario_ontology tool output lacks Pump B, North Works"])

    def test_documents_must_be_returned_by_the_knowledge_tool(self):
        response = {"status": "completed", "output": [
            {"type": "function_call", "name": "knowledge_base___knowledge_base_retrieve", "call_id": "c1"},
            {"type": "function_call_output", "call_id": "c1", "output": "[ref] starter.txt"},
            message("(Source: starter.txt)")]}
        self.assertEqual(hosted.document_evidence(response, "knowledge_base", "starter.txt")[0], [])
        response["output"][1]["output"] = "other.txt"
        self.assertEqual(hosted.document_evidence(response, "knowledge_base", "starter.txt")[0],
                         ["the knowledge_base tool output does not reference starter.txt"])
        response["output"][1]["output"] = "Error: Function failed. Exception: starter.txt"
        self.assertEqual(hosted.document_evidence(response, "knowledge_base", "starter.txt")[0],
                         ["the knowledge_base tool call failed",
                          "the knowledge_base tool output does not reference starter.txt"])
        # An inline MCP result marked isError is a failure too, even when its text names the document.
        inline = {"status": "completed", "output": [
            {"type": "mcp_call", "name": "knowledge_base_retrieve", "server_label": "knowledge_base",
             "output": {"isError": True, "content": [{"type": "text", "text": "starter.txt is unavailable"}]}},
            message("(Source: starter.txt)")]}
        self.assertEqual(hosted.document_evidence(inline, "knowledge_base", "starter.txt")[0],
                         ["the knowledge_base tool call failed",
                          "the knowledge_base tool output does not reference starter.txt"])

    def test_only_the_exact_fresh_challenge_is_proof(self):
        challenge = "connection proof 12345678-1234-4234-8234-123456789abc"
        self.assertTrue(hosted.proof_matches({"status": "completed", "output": [message(hosted.PROOF_PREFIX + challenge)]},
                                             challenge))
        for response in ({"status": "completed", "output": [message(hosted.PROOF_PREFIX + "connection proof old")]},
                         {"status": "failed", "output": [message(hosted.PROOF_PREFIX + challenge)]},
                         {"status": "completed", "output": []}, None):
            self.assertFalse(hosted.proof_matches(response, challenge))


if __name__ == "__main__":
    unittest.main()

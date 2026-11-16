import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import foundry_agent as foundry  # noqa: E402

REPO = Path(__file__).resolve().parents[4]
VALUES = {
    "agent_display_name": "Scenario assistant – with IQ", "agent_model_deployment": "agent",
    "agent_reasoning_effort": "low",
    "knowledge_base_mcp_endpoint": "https://srch.search.windows.net/knowledgebases/demo-knowledge/mcp?api-version=v1",
    "knowledge_base_connection": "knowledge-base",
    "public_context_mcp_endpoint": "https://srch.search.windows.net/knowledgebases/demo-public-context/mcp?api-version=v1",
    "public_context_connection": "public-context",
    "fabric_iq_mcp_endpoint": "https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/w/items/o/ontologyEndpoint",
    "fabric_iq_connection": "fabric-iq-scenario",
    "work_iq_mail_mcp_endpoint": "https://workiq.svc.cloud.microsoft/mcp",
    "work_iq_mail_connection": "work-iq",
}
WITHOUT_MAIL = {key: value for key, value in VALUES.items() if not key.startswith("work_iq_mail_")}
WITH_IQ = REPO / foundry.VARIANTS["with_iq"]
# Any source marker, such as [D1], [F1 §2] or [Web1].
MARKER = re.compile(r"\[([A-Za-z]+)\d+(?:\s*§\s*[\w.-]+)?\]")
# The sample's facts reach an agent only through its tools: the without-IQ variant shares the instructions.
SAMPLE_FACTS = ("Pump", "Conveyor", "North Works", "South Works", "demo-site", "demo-asset", "Operational",
                "Maintenance", "starter.txt")


class FoundryAgentTests(unittest.TestCase):
    def copy(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        shutil.copytree(REPO / "agents", root / "agents")
        return root

    def test_the_reviewed_definitions_render_complete_agent_versions(self):
        tools = {}
        for variant, source in foundry.VARIANTS.items():
            body = foundry.render(REPO / source, VALUES, variant)
            self.assertEqual(set(body), {"description", "definition", "metadata"})
            definition = body["definition"]
            self.assertEqual((definition["kind"], definition["model"], definition["reasoning"]),
                             ("prompt", "agent", {"effort": "low"}))
            self.assertEqual(definition["instructions"], (REPO / source / "instructions.md").read_text(encoding="utf-8"))
            self.assertEqual(body["description"], "Scenario assistant – with IQ")
            self.assertEqual(body["metadata"], {foundry.DIGEST_KEY: foundry.digest(body), "source": source.split("/")[1],
                                                "variant": variant})
            # Tools authenticate through their connections: no headers and no per-request inputs.
            self.assertEqual([set(tool) for tool in definition["tools"]],
                             [foundry.TOOL_KEYS] * len(definition["tools"]))
            self.assertNotIn("structured_inputs", definition)
            self.assertNotIn("{{", json.dumps(body))
            tools[variant] = {tool["server_label"]: (tool["allowed_tools"], tool["project_connection_id"])
                              for tool in definition["tools"]}
            for tool in definition["tools"]:
                self.assertEqual((tool["type"], tool["require_approval"]), ("mcp", "never"))
                self.assertLessEqual(len(tool["server_description"]), 1024)
        self.assertEqual(tools["with_iq"], {
            "fabric_iq_ontology": (["ask_ontology"], "fabric-iq-scenario"),
            "foundry_iq_knowledge": (["knowledge_base_retrieve"], "knowledge-base"),
            "web_iq_public_context": (["knowledge_base_retrieve"], "public-context"),
            "work_iq_mail": (["fetch"], "work-iq")})
        self.assertEqual(tools["without_iq"], {})

    def test_the_mail_tool_reads_one_search_of_the_users_mail(self):
        # Work IQ's server also offers tools that act: the tool allows only fetch, of one fixed search.
        mail = json.loads((WITH_IQ / "tools" / "WorkIQMail.json").read_text(encoding="utf-8"))
        self.assertIn('exactly this one path for the asset\'s name: /me/messages?$search="<asset name>"'
                      "&$select=subject,from,receivedDateTime,webLink,body&$top=10 ", mail["server_description"])
        self.assertIn("never ask it to send, reply, schedule or change anything", mail["server_description"])

    def test_the_digest_follows_the_content_not_the_metadata(self):
        body = foundry.render(WITH_IQ, VALUES, "with_iq")
        again = foundry.render(WITH_IQ, VALUES, "with_iq")
        self.assertEqual(body, again)
        self.assertEqual(foundry.digest({**body, "metadata": {"other": "x"}}), body["metadata"][foundry.DIGEST_KEY])
        changed = foundry.render(WITH_IQ, {**VALUES, "agent_reasoning_effort": "medium"}, "with_iq")
        self.assertNotEqual(changed["metadata"][foundry.DIGEST_KEY], body["metadata"][foundry.DIGEST_KEY])

    def test_omitting_work_iq_mail_leaves_out_exactly_that_tool(self):
        full = foundry.render(WITH_IQ, VALUES, "with_iq")
        # Without Work IQ, Terraform outputs no mail connection, so its placeholders need no values.
        body = foundry.render(WITH_IQ, WITHOUT_MAIL, "with_iq", omit={"WorkIQMail"})
        self.assertEqual(body["definition"]["tools"],
                         [tool for tool in full["definition"]["tools"] if tool["server_label"] != "work_iq_mail"])
        self.assertEqual({key: value for key, value in body["definition"].items() if key != "tools"},
                         {key: value for key, value in full["definition"].items() if key != "tools"})
        # Enabling Work IQ later changes the digest, so the runner publishes a new version.
        self.assertEqual(body["metadata"][foundry.DIGEST_KEY], foundry.digest(body))
        self.assertNotEqual(body["metadata"][foundry.DIGEST_KEY], full["metadata"][foundry.DIGEST_KEY])
        with self.assertRaisesRegex(foundry.DefinitionError, r"No value for \{\{work_iq_mail_mcp_endpoint\}\}"):
            foundry.render(WITH_IQ, WITHOUT_MAIL, "with_iq")

    def test_only_an_optional_tool_that_the_source_has_can_be_omitted(self):
        cases = {
            "Only an optional tool .* not FabricIQOntology": (WITH_IQ, {"FabricIQOntology"}),
            "Only an optional tool .* not MailTools": (WITH_IQ, {"WorkIQMail", "MailTools"}),
            "has no WorkIQMail tool to omit": (REPO / foundry.VARIANTS["without_iq"], {"WorkIQMail"}),
            "not a string": (WITH_IQ, "WorkIQMail"),
        }
        for message, (source, omit) in cases.items():
            with self.subTest(message), self.assertRaisesRegex(foundry.DefinitionError, message):
                foundry.render(source, VALUES, "with_iq", omit=omit)

    def test_a_model_without_reasoning_takes_no_reasoning_setting(self):
        body = foundry.render(REPO / foundry.VARIANTS["without_iq"], {**VALUES, "agent_reasoning_effort": None},
                              "without_iq")
        self.assertNotIn("reasoning", body["definition"])

    def test_every_placeholder_needs_a_known_value(self):
        with self.assertRaisesRegex(foundry.DefinitionError, r"No value for \{\{fabric_iq_connection\}\}"):
            foundry.render(WITH_IQ, {**VALUES, "fabric_iq_connection": ""}, "with_iq")
        root = self.copy()
        path = root / foundry.VARIANTS["without_iq"] / "agent.json"
        path.write_text(path.read_text(encoding="utf-8").replace("{{agent_display_name}}", "{{tenant_secret}}"),
                        encoding="utf-8")
        with self.assertRaisesRegex(foundry.DefinitionError, r"Unknown placeholder \{\{tenant_secret\}\}"):
            foundry.render(path.parent, VALUES, "without_iq")

    def test_tools_are_exact_read_only_mcp_tools_without_approval_steps(self):
        cases = {
            "require_approval must be never": {"require_approval": "always"},
            "allowed_tools must name at least one tool": {"allowed_tools": []},
            "server_description must be 1 to 1,024 characters": {"server_description": "x" * 1025},
            "keys must be exactly": {"authorization": "x"},
            "keys must be exactly allowed_tools": {"headers": {"Authorization": "Bearer " + "x"}},
            "type must be mcp": {"type": "openapi"},
            "server_label must be": {"server_label": "Fabric IQ"},
        }
        for message, change in cases.items():
            with self.subTest(message):
                root = self.copy()
                path = root / foundry.VARIANTS["with_iq"] / "tools" / "WorkIQMail.json"
                path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), **change}), encoding="utf-8")
                with self.assertRaisesRegex(foundry.DefinitionError, message):
                    foundry.load(path.parent.parent)

    def test_unknown_placeholders_fail_even_in_an_omitted_tool(self):
        root = self.copy()
        path = root / foundry.VARIANTS["with_iq"] / "tools" / "WorkIQMail.json"
        tool = json.loads(path.read_text(encoding="utf-8"))
        tool["server_url"] = "{{work_iq_assertion}}"
        path.write_text(json.dumps(tool), encoding="utf-8")
        for omit in (frozenset(), {"WorkIQMail"}):
            with self.subTest(omit=omit), self.assertRaisesRegex(foundry.DefinitionError,
                                                                 r"Unknown placeholder \{\{work_iq_assertion\}\}"):
                foundry.render(path.parent.parent, VALUES, "with_iq", omit=omit)
        with self.assertRaisesRegex(foundry.DefinitionError, r"Unknown placeholder \{\{work_iq_assertion\}\}"):
            foundry.validate(root)

    def test_definitions_keep_their_parts_in_their_own_files(self):
        root = self.copy()
        source = root / foundry.VARIANTS["with_iq"]
        shutil.copy(source / "tools" / "WorkIQMail.json", source / "tools" / "WorkIQMailCopy.json")
        with self.assertRaisesRegex(foundry.DefinitionError, "its own server_label"):
            foundry.load(source)
        agent = json.loads((source / "agent.json").read_text(encoding="utf-8"))
        agent["definition"]["instructions"] = "inline"
        (source / "agent.json").write_text(json.dumps(agent), encoding="utf-8")
        with self.assertRaisesRegex(foundry.DefinitionError, "must not inline instructions or tools"):
            foundry.load(source)

    def test_the_shared_files_must_stay_byte_identical(self):
        root = self.copy()
        self.assertEqual(foundry.validate(root), {
            "with_iq": ["FabricIQOntology", "FoundryIQKnowledge", "WebIQPublicContext", "WorkIQMail"],
            "without_iq": []})
        path = root / foundry.VARIANTS["without_iq"] / "instructions.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nOne more rule.\n", encoding="utf-8")
        with self.assertRaisesRegex(foundry.DefinitionError, "instructions.md must be byte-identical in all variants"):
            foundry.validate(root)

    def test_the_shared_instructions_keep_the_reply_contract(self):
        text = (WITH_IQ / "instructions.md").read_text(encoding="utf-8")
        # Short enough to review in one read, and without braces, which some renderers read as variables.
        self.assertLessEqual(len(text.strip()), 8000)
        self.assertNotRegex(text, r"[{}]")
        self.assertEqual({match.group(1) for match in MARKER.finditer(text)}, {"D", "F", "Web", "W"})
        for line in ("- [D1] ScenarioOntology · <entity> · <key>", "- [F1] <document title> · <section>",
                     "- [Web1] <page title> · <site> · <URL>",
                     "- [W1] Outlook · <sender> · <date> · <subject> · <link>"):
            self.assertIn(f"\n{line}\n", text)
        self.assertLess(text.index('A line "Sources"'), text.index('A line "Steps"'))
        # Foundry's runtime otherwise cites knowledge-base results in its own format.
        self.assertIn("never another citation format such as 【1:2†source】", text)
        # The instructions name each tool as its server label spells it.
        opening = text[:text.index("RULES")].casefold()
        for _, tool in foundry.load(WITH_IQ)["tools"]:
            self.assertIn(tool["server_label"].replace("_", " "), opening)
        self.assertEqual([fact for fact in SAMPLE_FACTS if fact.casefold() in text.casefold()], [])

    def test_status_compares_the_latest_version_with_the_rendered_digest(self):
        body = foundry.render(WITH_IQ, VALUES, "with_iq")
        latest = {"versions": {"latest": {"version": "3", "metadata": dict(body["metadata"])}}}
        self.assertEqual(foundry.status(None, body), "missing")
        self.assertEqual(foundry.status(latest, body), "current")
        latest["versions"]["latest"]["metadata"][foundry.DIGEST_KEY] = "0" * 64
        self.assertEqual(foundry.status(latest, body), "changed")
        self.assertEqual(foundry.status({"versions": {"latest": {"version": "1"}}}, body), "changed")

    def test_agent_urls_address_only_valid_agent_names(self):
        endpoint = "https://ai-demo.services.ai.azure.com/api/projects/demo/"
        self.assertEqual(foundry.agent_url(endpoint), "https://ai-demo.services.ai.azure.com/api/projects/demo/agents"
                                                      "?api-version=v1")
        self.assertEqual(foundry.agent_url(endpoint, "demo-with-iq", versions=True),
                         "https://ai-demo.services.ai.azure.com/api/projects/demo/agents/demo-with-iq/versions"
                         "?api-version=v1")
        for name in ("../other", "a" * 64, "-leading", "with space"):
            with self.subTest(name=name), self.assertRaises(foundry.DefinitionError):
                foundry.agent_url(endpoint, name)

    def test_the_cli_renders_and_validates_offline(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(WITHOUT_MAIL, handle)
        self.addCleanup(Path(handle.name).unlink)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(foundry.main(["render", "--source", str(WITH_IQ), "--values", handle.name,
                                           "--variant", "with_iq", "--omit", "WorkIQMail"]), 0)
        body = json.loads(stdout.getvalue())
        self.assertEqual(body, foundry.render(WITH_IQ, WITHOUT_MAIL, "with_iq", omit={"WorkIQMail"}))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(foundry.main(["render", "--source", str(WITH_IQ), "--values", handle.name,
                                           "--variant", "with_iq"]), 1)
        self.assertIn("No value for {{work_iq_mail_mcp_endpoint}}", stderr.getvalue())
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(foundry.main(["validate"]), 0)
        self.assertEqual(set(json.loads(stdout.getvalue())), set(foundry.VARIANTS))


if __name__ == "__main__":
    unittest.main()

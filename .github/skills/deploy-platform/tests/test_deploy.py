import ast
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import stat
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.parse


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import deploy  # noqa: E402

REPO = Path(__file__).resolve().parents[4]
WORKSPACE = "11111111-1111-4111-8111-111111111111"
LAKEHOUSE = "22222222-2222-4222-8222-222222222222"
ONTOLOGY = "33333333-3333-4333-8333-333333333333"
SEED = "44444444-4444-4444-8444-444444444444"
BIND = "55555555-5555-4555-8555-555555555555"
CONNECTION = "66666666-6666-4666-8666-666666666666"
OPERATION = "77777777-7777-4777-8777-777777777777"
SEED_JOB = "88888888-8888-4888-8888-888888888888"
BIND_JOB = "99999999-9999-4999-8999-999999999999"
SQL_ENDPOINT = ("example-endpoint.datawarehouse.fabric.microsoft.com", "14141414-1414-4414-8414-141414141414")
RERUN_JOB = "13131313-1313-4313-8313-131313131313"
TENANT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
COMMIT = "c" * 40
# The deployed scenario's names and identities, so these tests follow the SCENARIO descriptor.
NAMES = {key: name for key, _, name in deploy.SCENARIO.items}
FOLDERS = {key: f"{name}.{kind}" for key, kind, name in deploy.SCENARIO.items}
LOGICAL_IDS = {data["metadata"]["displayName"]: data["config"]["logicalId"]
               for data in (json.loads(path.read_text(encoding="utf-8"))
                            for path in (REPO / "workspace").glob("*/.platform"))}
PLAN_LOGICAL_ID = LOGICAL_IDS[deploy.SCENARIO.plan]
TOKEN = "header.eyJvaWQiOiAidXNlciJ9.signature"
REQUEST = {
    "gitProviderDetails": {"gitProviderType": "GitHub", "ownerName": "example", "repositoryName": "demo-repo",
                           "branchName": "main", "directoryName": "/workspace"},
    "myGitCredentials": {"source": "ConfiguredConnection", "connectionId": CONNECTION},
}


def reply(status=200, body=None, headers=None):
    return deploy.Response(status, headers or {}, b"" if body is None else json.dumps(body).encode())


class FakeShell:
    def __init__(self, rules=()):
        self.rules = list(rules)
        self.calls = []

    def which(self, name):
        return f"/usr/bin/{name}"

    def run(self, args, **options):
        self.calls.append(args)
        for match, outcome in self.rules:
            if match(args):
                return outcome(args, options) if callable(outcome) else outcome
        raise AssertionError(f"unexpected command: {args}")

    def matching(self, match):
        return [call for call in self.calls if match(call)]


class FakeHttp:
    def __init__(self):
        self.routes = []
        self.calls = []
        self.attempts = []

    def add(self, method, pattern, *responses):
        self.routes.append((method, re.compile(pattern), list(responses)))

    def request(self, method, url, headers=None, body=None, timeout=120, attempts=None):
        self.calls.append((method, url, body, dict(headers or {})))
        self.attempts.append(attempts)
        for route_method, pattern, responses in self.routes:
            if route_method == method and pattern.search(url):
                return responses.pop(0) if len(responses) > 1 else responses[0]
        raise AssertionError(f"unexpected request: {method} {url}")

    def posts(self, pattern):
        return [(url, body) for method, url, body, _ in self.calls if method == "POST" and re.search(pattern, url)]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def terraform(command):
    return lambda args: args[0] == "terraform" and len(args) > 2 and args[2] == command


def command(*prefix):
    return lambda args: args[:len(prefix)] == list(prefix)


TOKEN_RULE = (command("az", "account", "get-access-token"),
              deploy.Result(0, json.dumps({"accessToken": TOKEN, "expires_on": 9999999999})))
GIT_RULES = [
    (command("git", "ls-remote"), deploy.Result(0, f"{COMMIT}\trefs/heads/main\n")),
    (command("git", "rev-parse", "HEAD"), deploy.Result(0, COMMIT + "\n")),
    (command("git", "status", "--porcelain"), deploy.Result(0, "")),
]


def plan_rules(changes, apply_code=0, plan_code=2):
    def plan(args, options):
        path = next(arg for arg in args if arg.startswith("-out="))[5:]
        Path(path).write_bytes(b"saved plan for " + path.encode())
        return deploy.Result(plan_code, "plan output")

    shown = {"resource_changes": [{"address": address, "change": {"actions": actions}} for address, actions in changes]}
    return [(terraform("plan"), plan), (terraform("show"), deploy.Result(0, json.dumps(shown))),
            (terraform("apply"), deploy.Result(apply_code, "apply output")),
            (terraform("state"), deploy.Result(1, "", "No state file was found!"))]


def make_root(test):
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    root = Path(directory.name)
    shutil.copytree(REPO / "workspace", root / "workspace")
    (root / "infra").mkdir()
    (root / "infra" / "main.tf").write_text("# fixture\n", encoding="utf-8")
    (root / "infra" / "terraform.tfvars").write_text('project_name = "demo"\n', encoding="utf-8")
    return root


def make_context(test, root, shell=None, http=None, **overrides):
    args = deploy.parse_arguments(["up", "--non-interactive"])
    for key, value in overrides.items():
        setattr(args, key, value)
    clock = Clock()
    ctx = deploy.Context(args, root=root, shell=shell or FakeShell(), http=http or FakeHttp(), sleep=clock.sleep,
                         clock=clock, interactive=False, environ={"FABRIC_GITHUB_TOKEN": "pat-value"})
    ctx.messages = []
    ctx.log = ctx.messages.append
    ctx.cache["variables"] = {"fabric_git_enabled": False, "knowledge_lakehouse_id": None}
    ctx.start("test")
    return ctx


def tmdl(bound, workspace=WORKSPACE, pins=True, onelake=False, concrete=False):
    def part(path, text):
        return {"path": path, "payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"}

    parts = []
    for table in deploy.SCENARIO.tables:
        text = f"table {table}\n"
        if bound:
            text += (f"\tpartition {table} = entity\n\t\tmode: directLake\n\t\tsource\n\t\t\tentityName: {table}\n"
                     f"\t\t\tschemaName: dbo\n\t\t\texpressionSource: {deploy.SCENARIO.expression}\n\n")
            if pins:
                text += (f"\t\tannotation ONT_WorkspaceId = {workspace}\n\n"
                         f"\t\tannotation ONT_ItemId = {LAKEHOUSE}\n\n"
                         "\t\tannotation ONT_ItemKind = Lakehouse\n")
            else:
                text += "\t\tannotation ONT_PinnedAtUtc = 2026-10-26T14:11:16.269Z\n"
        parts.append(part(f"tables/{table}.tmdl", text))
    if bound and onelake:
        # The earlier binder's form: Fabric stripped its pins.
        parts.append(part("expressions.tmdl", f"expression {deploy.SCENARIO.expression} =\n\t\tlet\n\t\t\tSource = "
                          f'AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/{workspace}/{LAKEHOUSE}", '
                          "[HierarchicalNavigation=true])\n\t\tin\n\t\t\tSource\n"))
    elif bound:
        # As written (concrete), or as Fabric stores it: ALM tokens that name the pinned lakehouse.
        source = (f'Sql.Database("{SQL_ENDPOINT[0]}", "{SQL_ENDPOINT[1]}")' if concrete else
                  f'Sql.Database("ONT_ALM_SQL_ENDPOINT", "ONT_ALM_SQL_DATABASE-Lakehouse:{workspace}:{LAKEHOUSE}")')
        parts.append(part("expressions.tmdl", f"expression {deploy.SCENARIO.expression} =\r\n\t\tlet\r\n\t\t\tdatabase = "
                          f"{source}\r\n\t\tin\r\n\t\t\tdatabase\r\n"))
    return {"definition": {"parts": parts}}


def incoming(root):
    changes = []
    for path in sorted((root / "workspace").glob("*/.platform")):
        data = json.loads(path.read_text(encoding="utf-8"))
        changes.append({"itemMetadata": {"itemIdentifier": {"logicalId": data["config"]["logicalId"]},
                                         "itemType": data["metadata"]["type"],
                                         "displayName": data["metadata"]["displayName"]},
                        "remoteChange": "Added", "conflictType": "None"})
    return changes


ITEMS = {"value": [
    {"id": LAKEHOUSE, "type": "Lakehouse", "displayName": NAMES["lakehouse"], "workspaceId": WORKSPACE},
    {"id": SEED, "type": "Notebook", "displayName": NAMES["seed"], "workspaceId": WORKSPACE},
    {"id": ONTOLOGY, "type": "Ontology", "displayName": NAMES["ontology"], "workspaceId": WORKSPACE},
    {"id": BIND, "type": "Notebook", "displayName": NAMES["bind"], "workspaceId": WORKSPACE},
]}
RUNS = ((SEED, SEED_JOB), (BIND, BIND_JOB))


def bootstrap_http(root, connection_states=("ConnectedAndInitialized",), status=None, definitions=(True,)):
    http = FakeHttp()
    details = REQUEST["gitProviderDetails"]
    http.add("GET", r"/git/connection$", *[reply(body={"gitConnectionState": state, "gitProviderDetails": details})
                                           for state in connection_states])
    http.add("POST", r"/git/connect$", reply(body={}))
    http.add("GET", r"/git/myGitCredentials$", reply(body={"source": "ConfiguredConnection", "connectionId": CONNECTION}))
    http.add("POST", r"/git/initializeConnection$",
             reply(body={"requiredAction": "UpdateFromGit", "workspaceHead": None, "remoteCommitHash": COMMIT}))
    http.add("GET", r"/git/status$", reply(body=status or {"workspaceHead": None, "remoteCommitHash": COMMIT,
                                                          "changes": incoming(root)}))
    http.add("POST", r"/git/updateFromGit\?beta=true$", reply(202, headers={"x-ms-operation-id": OPERATION, "Retry-After": "1"}))
    http.add("GET", rf"/v1/operations/{OPERATION}$", reply(body={"status": "Succeeded"}))
    http.add("GET", rf"/workspaces/{WORKSPACE}/items$", reply(body=ITEMS))
    http.add("GET", rf"/ontologies/{ONTOLOGY}$",
             reply(body={"logicalId": LOGICAL_IDS[NAMES["ontology"]], "properties": {"generation": 2}}))
    http.add("GET", rf"/lakehouses/{LAKEHOUSE}$", reply(body={"properties": {"defaultSchema": "dbo", "sqlEndpointProperties": {
        "connectionString": SQL_ENDPOINT[0], "id": SQL_ENDPOINT[1], "provisioningStatus": "Success"}}}))
    http.add("POST", r"/getDefinition$", *[reply(body=tmdl(bound)) for bound in definitions])
    return http


def bootstrap_context(test, root, http, **overrides):
    ctx = make_context(test, root, FakeShell([TOKEN_RULE, *GIT_RULES]), http, **overrides)
    ctx.cache["outputs"] = {"dev_workspace_id": WORKSPACE, "github_connect_request": REQUEST}
    ctx.cache["variables"].update(fabric_git_enabled=True, github_branch_name="main")
    return ctx


class PlanTests(unittest.TestCase):
    def test_a_saved_plan_is_applied_only_with_its_exact_hash(self):
        root = make_root(self)
        shell = FakeShell(plan_rules([("azurerm_resource_group.main", ["create"])]))
        ctx = make_context(self, root, shell)
        with self.assertRaises(deploy.NeedsApproval) as stop:
            deploy.converge(ctx, "platform")
        approval = stop.exception.approval
        self.assertEqual(approval["summary"]["counts"], {"create": 1})
        self.assertFalse(shell.matching(terraform("apply")))
        ctx.args.approve = {"platform": "0" * 12}
        with self.assertRaises(deploy.NeedsApproval):
            deploy.converge(ctx, "platform")
        ctx.args.approve = {"platform": approval["sha256"][:12]}
        self.assertEqual(deploy.converge(ctx, "platform"), "applied")
        self.assertEqual(len(shell.matching(terraform("plan"))), 1, "approval must not re-plan")
        self.assertIn(approval["plan"], shell.matching(terraform("apply"))[0])
        self.assertEqual(ctx.state.section("pending_plans"), {})
        self.assertFalse(Path(approval["plan"]).exists())

    def test_a_changed_configuration_discards_the_saved_plan(self):
        root = make_root(self)
        shell = FakeShell(plan_rules([("azurerm_resource_group.main", ["create"])]))
        ctx = make_context(self, root, shell)
        with self.assertRaises(deploy.NeedsApproval) as first:
            deploy.converge(ctx, "platform")
        (root / "infra" / "terraform.tfvars").write_text('project_name = "other"\n', encoding="utf-8")
        with self.assertRaises(deploy.NeedsApproval) as second:
            deploy.converge(ctx, "platform")
        self.assertEqual(len(shell.matching(terraform("plan"))), 2)
        self.assertNotEqual(first.exception.approval["plan"], second.exception.approval["plan"])
        self.assertFalse(Path(first.exception.approval["plan"]).exists())

    def test_deletions_need_allow_replace_even_with_yes(self):
        root = make_root(self)
        ctx = make_context(self, root, FakeShell(plan_rules([("fabric_workspace.dev", ["delete", "create"])])), yes=True)
        with self.assertRaises(deploy.NeedsApproval) as stop:
            deploy.converge(ctx, "platform")
        self.assertIn("--allow-replace", " ".join(stop.exception.next_steps))
        ctx.args.allow_replace = True
        self.assertEqual(deploy.converge(ctx, "platform"), "applied")

    def test_full_plans_never_change_the_registration_silently(self):
        root = make_root(self)
        changes = [("module.fabric_user_auth.azuread_application_pre_authorized.clients[\"azure_cli\"]", ["create"])]
        ctx = make_context(self, root, FakeShell(plan_rules(changes)), yes=True)
        with self.assertRaisesRegex(deploy.Blocked, "user-auth registration"):
            deploy.converge(ctx, "knowledge")

    def test_partial_identity_after_platform_resources_needs_manual_review(self):
        root = make_root(self)
        shell = FakeShell([(terraform("state"), deploy.Result(0, "azurerm_resource_group.main\n"))])
        ctx = make_context(self, root, shell, yes=True)
        ctx.initialized = True
        with self.assertRaisesRegex(deploy.Blocked, "registration is incomplete"):
            deploy.phase_identity(ctx)
        self.assertFalse(shell.matching(terraform("plan")))

    def test_ambient_terraform_overrides_are_refused(self):
        root = make_root(self)
        for key in ("TF_CLI_ARGS_plan", "TF_WORKSPACE", "TF_VAR_location"):
            ctx = make_context(self, root)
            ctx.environ = {"FABRIC_GITHUB_TOKEN": "pat-value", key: "-target=fabric_workspace.dev"}
            with self.assertRaisesRegex(deploy.Blocked, key):
                deploy.terraform_env(ctx, True)

    def test_native_deployment_plans_and_applies_without_a_pat_or_placeholder(self):
        root = make_root(self)
        shell = FakeShell(plan_rules([("azurerm_resource_group.main", ["create"])]))
        ctx = make_context(self, root, shell, yes=True)
        ctx.environ = {}
        with mock.patch.object(shell, "run", wraps=shell.run) as native:
            self.assertEqual(deploy.converge(ctx, "platform"), "applied")
        for call in native.call_args_list:
            self.assertNotIn("TF_VAR_github_token", call.kwargs["env"])

    def test_after_knowledge_setup_plans_and_applies_carry_the_web_iq_key_and_teardown_does_not(self):
        root = make_root(self)
        shell = FakeShell(plan_rules([("restful_resource.public_context_source[0]", ["create"])]))
        ctx = make_context(self, root, shell, yes=True)
        ctx.cache["variables"]["knowledge_lakehouse_id"] = LAKEHOUSE
        ctx.environ = {}
        with self.assertRaisesRegex(deploy.Waiting, "Web IQ key"):
            deploy.converge(ctx, "knowledge")
        self.assertFalse(shell.calls)
        ctx.environ = {"TF_VAR_web_iq_api_key": "ambient"}
        with self.assertRaisesRegex(deploy.Blocked, "Unset TF_VAR_web_iq_api_key"):
            deploy.converge(ctx, "knowledge")
        ctx.environ = {"WEB_IQ_API_KEY": "key with space"}
        with self.assertRaisesRegex(deploy.Blocked, "invalid HTTP header characters"):
            deploy.converge(ctx, "knowledge")
        ctx.environ = {"WEB_IQ_API_KEY": " web-iq-key "}
        with mock.patch.object(shell, "run", wraps=shell.run) as native:
            self.assertEqual(deploy.converge(ctx, "knowledge"), "applied")
            keyed = {call.args[0][2]: call.kwargs["env"].get("TF_VAR_web_iq_api_key") for call in native.call_args_list
                     if call.args[0][2] in ("plan", "apply")}
            self.assertTrue(all("WEB_IQ_API_KEY" not in call.kwargs["env"] for call in native.call_args_list))
        self.assertEqual(keyed, {"plan": "web-iq-key", "apply": "web-iq-key"})
        self.assertNotIn("web-iq-key", ctx.state.path.read_text(encoding="utf-8"))

    def test_teardown_and_earlier_plans_never_carry_the_web_iq_key(self):
        # An ephemeral variable set at plan must be set at apply, so it can't depend on whether the shell has the key.
        for phase, lakehouse, changes in (("destroy", LAKEHOUSE, [("fabric_workspace.dev", ["delete"])]),
                                          ("platform", None, [("azurerm_resource_group.main", ["create"])])):
            with self.subTest(phase=phase):
                shell = FakeShell(plan_rules(changes))
                ctx = make_context(self, make_root(self), shell, allow_delete=True)
                ctx.args.approve = {}
                ctx.cache["variables"]["knowledge_lakehouse_id"] = lakehouse
                ctx.environ = {"WEB_IQ_API_KEY": "key with space"}
                with mock.patch.object(shell, "run", wraps=shell.run) as native:
                    with self.assertRaises(deploy.NeedsApproval) as approval:
                        deploy.converge(ctx, phase)
                    ctx.args.approve = {phase: approval.exception.approval["sha256"][:12]}
                    ctx.environ = {}
                    self.assertEqual(deploy.converge(ctx, phase), "applied")
                for call in native.call_args_list:
                    self.assertNotIn("TF_VAR_web_iq_api_key", call.kwargs["env"])
                    self.assertNotIn("WEB_IQ_API_KEY", call.kwargs["env"])
                self.assertEqual(len(shell.matching(terraform("apply"))), 1)

    def test_opted_in_git_requires_a_pat_before_planning(self):
        root = make_root(self)
        ctx = make_context(self, root)
        ctx.environ = {}
        ctx.cache["variables"]["fabric_git_enabled"] = True
        with self.assertRaisesRegex(deploy.Waiting, "PAT is not available"):
            deploy.converge(ctx, "platform")
        self.assertFalse(ctx.shell.calls)
        ctx.environ["FABRIC_GITHUB_TOKEN"] = "repository-pat"
        self.assertEqual(deploy.terraform_env(ctx, True)["TF_VAR_github_token"], "repository-pat")

    def test_preflight_checks_the_pat_and_the_work_iq_principal_only_when_opted_in(self):
        subscription = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        account = {"id": subscription, "tenantId": TENANT, "user": {"type": "user"}}
        shell = FakeShell([
            TOKEN_RULE,
            (command("terraform", "version"), deploy.Result(0, json.dumps({"terraform_version": "1.16.5"}))),
            (command("az", "account", "show"), deploy.Result(0, json.dumps(account))),
            (command("az", "ad", "sp", "show"), deploy.Result(0, '{"id": "shared-service"}')),
        ])
        ctx = make_context(self, make_root(self), shell, subscription=subscription)
        ctx.environ = {}
        ctx.cache["variables"].update(search_sku="serverless", work_iq_enabled=False)
        with mock.patch.object(deploy, "check_web_iq", return_value="Web IQ ready"), \
                mock.patch.object(deploy, "state_addresses", return_value=deploy.PLATFORM_ADDRESSES), \
                mock.patch.object(deploy, "check_search_tier", return_value="Search offered"), \
                mock.patch.object(deploy, "check_capacity_resize", return_value="Fabric capacity F2"), \
                mock.patch.object(deploy.runtime, "check_environment"), \
                mock.patch.object(deploy, "work_iq_api") as work_iq_api, \
                mock.patch.object(deploy, "check_pat") as check_pat:
            deploy.phase_preflight(ctx)
            check_pat.assert_not_called()
            work_iq_api.assert_not_called()
            self.assertFalse([message for message in ctx.messages if "Copilot Credits" in message])
            ctx.cache["variables"].update(fabric_git_enabled=True, work_iq_enabled=True)
            deploy.phase_preflight(ctx)
            check_pat.assert_called_once_with(ctx, ctx.cache["variables"])
            work_iq_api.assert_called_once_with(ctx)
            self.assertTrue([message for message in ctx.messages if "Copilot Credits" in message])

    def test_the_identity_bootstrap_plan_must_stay_inside_its_module(self):
        root = make_root(self)
        changes = [("module.fabric_user_auth.azuread_application.fabric", ["create"]),
                   ("azurerm_resource_group.main", ["create"])]
        shell = FakeShell(plan_rules(changes))
        ctx = make_context(self, root, shell, yes=True)
        ctx.initialized = True
        with self.assertRaisesRegex(deploy.Blocked, "outside the module"):
            deploy.phase_identity(ctx)
        self.assertIn("-target=module.fabric_user_auth", shell.matching(terraform("plan"))[0])
        self.assertFalse(shell.matching(terraform("apply")))

    def test_an_apply_discards_the_other_phases_saved_plans_it_made_stale(self):
        root = make_root(self)
        shell = FakeShell(plan_rules([("azurerm_container_app.app[0]", ["create"])]))
        ctx = make_context(self, root, shell)
        with self.assertRaises(deploy.NeedsApproval) as stop:
            deploy.converge(ctx, "app", variables={"app_image_tag": "0123456789ab"})
        stale = stop.exception.approval["plan"]
        ctx.args.yes = True
        self.assertEqual(deploy.converge(ctx, "knowledge"), "applied")
        self.assertEqual(ctx.state.section("pending_plans"), {})
        self.assertFalse(Path(stale).exists())
        self.assertIn("Discarding the saved app plan, which this apply made stale.", ctx.messages)

    def test_the_identity_approval_discloses_the_consent_granted_next(self):
        changes = [("module.fabric_user_auth.azuread_application.fabric", ["create"])]
        for manual, disclosed in ((False, True), (True, False)):
            ctx = make_context(self, make_root(self), FakeShell(plan_rules(changes)), manual_grants=manual)
            ctx.initialized = True
            with self.assertRaises(deploy.NeedsApproval) as stop:
                deploy.phase_identity(ctx)
            self.assertEqual("tenant-wide as you" in stop.exception.approval["description"], disclosed)

    def test_a_failed_apply_hands_its_output_to_the_refusal_mapping(self):
        denied = ("│ Error: creating Resource Group \"rg-demo\"\n│ Status=403 Code=\"AuthorizationFailed\" Message=\"The "
                  "client 'u' with object id 'o' does not have authorization to perform action "
                  "'Microsoft.Resources/subscriptions/resourcegroups/write' over scope '/subscriptions/s'\"")
        rules = [(terraform("apply"), deploy.Result(1, denied)), *plan_rules([("azurerm_resource_group.main", ["create"])])]
        ctx = make_context(self, make_root(self), FakeShell(rules), yes=True)
        with self.assertRaises(deploy.Blocked) as stop:
            deploy.converge(ctx, "platform")
        self.assertIn("AuthorizationFailed", stop.exception.detail)
        self.assertNotIn("AuthorizationFailed", stop.exception.message)
        self.assertFalse([step for step in stop.exception.next_steps if "scoped sign-in" in step],
                         "an Azure RBAC refusal is not a Fabric sign-in problem")
        grants = deploy.as_missing_permission(ctx, "platform", stop.exception).grants
        self.assertEqual(len(grants), 1)
        self.assertIn("Contributor (or Owner)", grants[0])

    def test_no_changes_leaves_nothing_behind(self):
        root = make_root(self)
        ctx = make_context(self, root, FakeShell(plan_rules([], plan_code=0)))
        self.assertEqual(deploy.converge(ctx, "platform"), "unchanged")
        self.assertEqual(list((ctx.state.directory / "plans").iterdir()), [])


class TeardownTests(unittest.TestCase):
    SUBSCRIPTION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    CLIENT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    AGENTS = ["demo-with-iq", "demo-without-iq"]

    def context(self, **options):
        ctx = make_context(self, make_root(self), **options)
        ctx.args.command = "down"
        ctx.args.allow_delete = options.get("allow_delete", False)
        ctx.initialized = True
        ctx.state.section("target").update(subscription_id=self.SUBSCRIPTION, tenant_id=TENANT, user_object_id="user")
        ctx.state.section("choices").update(local_state=True)
        ctx.cache["variables"].update(project_name="demo", github_owner="example", github_repository_name="demo-repo")
        return ctx

    def snapshot(self):
        return {"repository": "example/demo-repo", "project_name": "demo", "resource_ids": {},
                "agents": self.AGENTS, "app_client_id": self.CLIENT, "workspaces": {"dev": WORKSPACE},
                "foundry": {"name": "ai-demo", "resource_group": "rg-demo", "location": "westeurope"}}

    def state_rules(self, state):
        return [
            TOKEN_RULE,
            (command("git", "remote", "get-url"), deploy.Result(0, "https://github.com/example/demo-repo.git\n")),
            (command("gh", "repo", "view"), deploy.Result(0, json.dumps({
                "nameWithOwner": "example/demo-repo", "isTemplate": False}))),
            (command("az", "account", "show"), deploy.Result(0, json.dumps({
                "id": self.SUBSCRIPTION, "tenantId": TENANT, "user": {"type": "user"}}))),
            (terraform("show"), deploy.Result(0, json.dumps(state))),
        ]

    def test_destroy_needs_explicit_deletion_and_exact_plan_approval_even_with_yes(self):
        ctx = self.context(yes=True)
        ctx.shell = FakeShell(plan_rules([
            ("fabric_workspace.dev", ["delete"]),
            ("module.fabric_user_auth.azuread_application.fabric", ["delete"]),
        ]))
        callback = mock.Mock()
        with self.assertRaisesRegex(deploy.NeedsApproval, "deletes the environment"):
            deploy.converge(ctx, "destroy", before_apply=callback)
        ctx.args.allow_delete = True
        with self.assertRaises(deploy.NeedsApproval) as approval:
            deploy.converge(ctx, "destroy", before_apply=callback)
        callback.assert_not_called()
        self.assertFalse(ctx.shell.matching(terraform("apply")))
        ctx.args.approve = {"destroy": approval.exception.approval["sha256"][:12]}
        self.assertEqual(deploy.converge(ctx, "destroy", before_apply=callback), "applied")
        callback.assert_called_once()
        plan, = ctx.shell.matching(terraform("plan"))
        self.assertIn("-destroy", plan)
        self.assertFalse(any(argument.startswith("-target") for argument in plan))

    def test_destroy_refuses_creations_targets_and_variable_overrides(self):
        ctx = self.context(yes=True, allow_delete=True)
        ctx.shell = FakeShell(plan_rules([("fabric_workspace.dev", ["delete", "create"])]))
        with self.assertRaisesRegex(deploy.Blocked, "non-delete"):
            deploy.converge(ctx, "destroy")
        for options in ({"target": deploy.IDENTITY_TARGET}, {"variables": {"search_identity_ready": "false"}}):
            with self.subTest(options=options), self.assertRaisesRegex(deploy.Blocked, "untargeted"):
                deploy.converge(ctx, "destroy", **options)
        self.assertFalse(ctx.shell.matching(terraform("apply")))

    def test_snapshot_preserves_only_cleanup_identifiers_and_survives_partial_destroy(self):
        ctx = self.context()
        foundry = f"/subscriptions/{self.SUBSCRIPTION}/resourceGroups/rg-demo/providers/Microsoft.CognitiveServices/accounts/ai-demo"
        agents = {key: {"name": f"demo-{key.replace('_', '-')}", "display_name": "Scenario assistant",
                        "source": source} for key, source in deploy.foundry.VARIANTS.items()}
        state = {"values": {"root_module": {"resources": [
            {"address": "azapi_resource.foundry", "mode": "managed",
             "values": {"id": foundry, "location": "westeurope", "secret": "do-not-persist"}},
            {"address": "fabric_workspace.dev", "mode": "managed", "values": {"id": WORKSPACE}},
        ]}, "outputs": {"foundry_agents": {"value": agents},
                        "app": {"value": {"client_id": self.CLIENT, "url": "https://ca-demo.example.io"}},
                        "unrelated_secret": {"value": "do-not-persist"}}}}
        ctx.shell = FakeShell(self.state_rules(state))
        with mock.patch.object(deploy.runtime, "check_environment"):
            snapshot = deploy.teardown_snapshot(ctx)
            ctx.shell = FakeShell(self.state_rules({"values": {}}))
            self.assertEqual(deploy.teardown_snapshot(ctx), snapshot)
        self.assertEqual(snapshot["workspaces"], {"dev": WORKSPACE})
        self.assertEqual(snapshot["agents"], self.AGENTS)
        self.assertEqual(snapshot["app_client_id"], self.CLIENT)
        self.assertEqual(snapshot["foundry"]["name"], "ai-demo")
        self.assertNotIn("do-not-persist", ctx.state.path.read_text())

    def test_snapshot_refuses_changed_resource_identity_or_foreign_subscription(self):
        ctx = self.context()
        ctx.state.section("destroy")["snapshot"] = self.snapshot()
        for identity in (WORKSPACE, f"/subscriptions/{TENANT}/resourceGroups/other"):
            state = {"values": {"root_module": {"resources": [
                {"address": "fabric_workspace.dev", "mode": "managed", "values": {"id": identity}},
            ]}}}
            ctx.shell = FakeShell(self.state_rules(state))
            with self.subTest(identity=identity), mock.patch.object(deploy.runtime, "check_environment"), \
                    self.assertRaises(deploy.Blocked):
                deploy.teardown_snapshot(ctx)

    def test_wrong_user_or_missing_receipts_block_before_any_plan(self):
        ctx = self.context()
        ctx.state.section("target")["user_object_id"] = "other-user"
        ctx.shell = FakeShell(self.state_rules({}))
        with self.assertRaisesRegex(deploy.Blocked, "original Azure user"):
            deploy.teardown_snapshot(ctx)
        ctx = self.context()
        ctx.state.data["target"] = {}
        with self.assertRaisesRegex(deploy.Blocked, "original deployment receipts"):
            deploy.teardown_snapshot(ctx)
        self.assertFalse(ctx.shell.matching(terraform("plan")))

    def test_the_reviewed_destroy_plan_alone_deletes_the_app_and_agents(self):
        # The agents are Foundry project objects and the app a Terraform resource: nothing is deleted before the plan.
        self.assertFalse(hasattr(deploy, "delete_runtime"))
        self.assertNotIn("runtime-delete", deploy.APPROVAL_KEYS)
        ctx = self.context(allow_delete=True)
        ctx.shell = FakeShell(plan_rules([], plan_code=0))
        snapshot = self.snapshot()
        with mock.patch.object(deploy, "teardown_snapshot", return_value=snapshot), \
                mock.patch.object(deploy, "purge_foundry") as purge:
            self.assertIn("managed resources deleted", deploy.phase_destroy(ctx))
        purge.assert_called_once_with(ctx, snapshot)
        self.assertFalse(ctx.http.calls)
        steps = " ".join(deploy.teardown_next(snapshot))
        self.assertIn(WORKSPACE, steps)
        self.assertIn("not immediate data erasure", steps)
        self.assertIn("Deleted applications", steps)
        for agent in self.AGENTS:
            self.assertIn(agent, steps)
        self.assertTrue((ctx.root / "infra" / "terraform.tfvars").exists())

    def test_foundry_purge_requires_separate_approval_and_confirms_absence(self):
        ctx = self.context(yes=True, allow_delete=True)
        ctx.shell = FakeShell([TOKEN_RULE, (command("az", "cognitiveservices", "account", "purge"), deploy.Result(0))])
        path = (f"/subscriptions/{self.SUBSCRIPTION}/providers/Microsoft.CognitiveServices/locations/westeurope"
                "/resourceGroups/rg-demo/deletedAccounts/ai-demo")
        ctx.http.add("GET", re.escape(path), reply(body={"id": path}), reply(body={"id": path}), reply(404))
        with self.assertRaises(deploy.NeedsApproval):
            deploy.purge_foundry(ctx, self.snapshot())
        self.assertFalse(ctx.shell.matching(command("az", "cognitiveservices", "account", "purge")))
        ctx.args.approve = {"purge-foundry": True}
        deploy.purge_foundry(ctx, self.snapshot())
        self.assertEqual(len(ctx.shell.matching(command("az", "cognitiveservices", "account", "purge"))), 1)
        self.assertIn("--subscription", ctx.shell.calls[-1])

    def test_full_destroy_precedes_purge_and_a_failed_apply_keeps_the_inventory(self):
        for code in (0, 1):
            with self.subTest(apply_code=code):
                ctx = self.context(allow_delete=True)
                events = []
                rules = plan_rules([("fabric_workspace.dev", ["delete"])], apply_code=code)

                def apply(args, options):
                    events.append("terraform")
                    return deploy.Result(code)

                rules.insert(0, (terraform("apply"), apply))
                ctx.shell = FakeShell(rules)
                snapshot = self.snapshot()
                ctx.state.section("destroy")["snapshot"] = snapshot
                with mock.patch.object(deploy, "teardown_snapshot", return_value=snapshot), \
                        mock.patch.object(deploy, "purge_foundry", side_effect=lambda *_: events.append("purge")):
                    with self.assertRaises(deploy.NeedsApproval) as approval:
                        deploy.phase_destroy(ctx)
                    self.assertEqual(events, [])
                    self.assertNotIn("started_at", ctx.state.section("destroy"))
                    ctx.args.approve = {"destroy": approval.exception.approval["sha256"][:12]}
                    if code:
                        with self.assertRaisesRegex(deploy.Blocked, "state is preserved"):
                            deploy.phase_destroy(ctx)
                        self.assertEqual(events, ["terraform"])
                        self.assertNotIn("managed_resources_deleted_at", ctx.state.section("destroy"))
                    else:
                        deploy.phase_destroy(ctx)
                        self.assertEqual(events, ["terraform", "purge"])
                self.assertEqual(ctx.state.section("destroy")["snapshot"], snapshot)
                # Recorded before the apply, so a partial teardown can't be resumed with up.
                self.assertIn("started_at", ctx.state.section("destroy"))
                ctx.args.command = "up"
                with self.assertRaisesRegex(deploy.Blocked, "being torn down"):
                    deploy.run_phases(ctx, ("config",))

    def test_down_cannot_reenter_deployment_after_teardown_started(self):
        ctx = self.context()
        ctx.state.section("destroy")["started_at"] = "earlier"
        ctx.state.save()
        ctx.args.command = "up"
        with self.assertRaisesRegex(deploy.Blocked, "being torn down"):
            deploy.run_phases(ctx, ("config",))
        self.assertFalse(ctx.shell.calls)
        args = deploy.parse_arguments(["down", "--allow-delete", "--approve", "destroy=abcdef012345",
                                       "--approve", "purge-foundry"])
        self.assertTrue(args.allow_delete)
        self.assertEqual(args.approve, {"destroy": "abcdef012345", "purge-foundry": True})

    def shared_group(self, ctx, rules, present=True):
        """Fake ARM: this resource group's Smart Detection action group, and the subscription's detector rules."""
        resource_group = f"/subscriptions/{self.SUBSCRIPTION}/resourceGroups/rg-demo"
        group = f"{resource_group}/providers/microsoft.insights/actionGroups/{deploy.SMART_DETECTION_GROUP}"
        ctx.http.add("GET", re.escape(urllib.parse.quote(group)), reply(body={"id": group}) if present else reply(404))
        ctx.http.add("GET", r"/providers/Microsoft\.AlertsManagement/smartDetectorAlertRules\?",
                     reply(body={"value": [{"id": rule, "properties": {"actionGroups": {"groupIds": [group]}}}
                                           for rule in rules]}))
        snapshot = self.snapshot()
        snapshot["resource_ids"] = {"azurerm_resource_group.main": resource_group}
        return snapshot, resource_group

    def test_deleting_a_shared_smart_detection_group_needs_its_own_approval_even_with_yes(self):
        ctx = self.context(yes=True, allow_delete=True)
        ctx.shell = FakeShell([TOKEN_RULE] + plan_rules([], plan_code=0))
        rules = f"/subscriptions/{self.SUBSCRIPTION}/resourceGroups/%s/providers/microsoft.alertsmanagement/smartDetectorAlertRules"
        snapshot, _ = self.shared_group(ctx, [f"{rules % 'rg-other'}/Failure Anomalies - appi-other",
                                              f"{rules % 'rg-demo'}/Failure Anomalies - appi-demo"])
        with mock.patch.object(deploy, "teardown_snapshot", return_value=snapshot), \
                mock.patch.object(deploy, "purge_foundry"):
            with self.assertRaises(deploy.NeedsApproval) as approval:
                deploy.phase_destroy(ctx)
            self.assertIn("shared-action-group", " ".join(approval.exception.next_steps))
            self.assertIn("appi-other", str(approval.exception))
            self.assertNotIn("appi-demo", str(approval.exception))
            self.assertFalse(ctx.shell.matching(terraform("plan")))
            ctx.args.approve = {"shared-action-group": True}
            self.assertIn("managed resources deleted", deploy.phase_destroy(ctx))

    def test_a_smart_detection_group_used_only_here_or_absent_needs_no_extra_approval(self):
        inside = (f"/subscriptions/{self.SUBSCRIPTION}/resourceGroups/rg-demo/providers/microsoft.alertsmanagement/"
                  "smartDetectorAlertRules/Failure Anomalies - appi-demo")
        for present, rules in ((True, [inside]), (False, [])):
            with self.subTest(present=present):
                ctx = self.context(allow_delete=True)
                ctx.shell = FakeShell([TOKEN_RULE] + plan_rules([], plan_code=0))
                snapshot, _ = self.shared_group(ctx, rules, present=present)
                with mock.patch.object(deploy, "teardown_snapshot", return_value=snapshot), \
                        mock.patch.object(deploy, "purge_foundry"):
                    self.assertIn("managed resources deleted", deploy.phase_destroy(ctx))
        self.assertIn("shared-action-group", deploy.DESTRUCTIVE_APPROVALS)


class BootstrapTests(unittest.TestCase):
    def test_a_fresh_workspace_syncs_the_reviewed_commit_with_the_bootstrap_plan(self):
        root = make_root(self)
        http = bootstrap_http(root, connection_states=("NotConnected", "Connected"))
        ctx = bootstrap_context(self, root, http)
        detail = deploy.phase_bootstrap(ctx)
        self.assertIn(LAKEHOUSE, detail)
        self.assertEqual(http.posts(r"/git/connect$")[0][1], REQUEST)
        (_, body), = http.posts(r"/git/updateFromGit\?beta=true$")
        self.assertEqual(body["remoteCommitHash"], COMMIT)
        self.assertEqual(body["options"]["deploymentPlan"], {"logicalId": PLAN_LOGICAL_ID, "referenceType": "ByLogicalId"})
        self.assertFalse(http.posts(r"/jobs/execute/instances"))
        receipts = ctx.state.section("bootstrap")
        self.assertEqual((receipts["lakehouse_id"], receipts["ontology_id"]), (LAKEHOUSE, ONTOLOGY))
        self.assertEqual(receipts["update"]["status"], "Succeeded")
        self.assertTrue(all(headers.get("Authorization") == f"Bearer {TOKEN}" for *_, headers in http.calls))

    def test_an_accepted_initialization_is_observed_after_a_timeout_not_resubmitted(self):
        root = make_root(self)
        first = bootstrap_http(root, connection_states=("Connected",))
        first.routes.insert(0, ("POST", re.compile(r"/git/initializeConnection$"),
                                [reply(202, headers={"x-ms-operation-id": SEED_JOB, "Retry-After": "5"})]))
        first.add("GET", rf"/v1/operations/{SEED_JOB}$", reply(body={"status": "Running"}))
        with self.assertRaises(deploy.Waiting):
            deploy.phase_bootstrap(bootstrap_context(self, root, first, timeout_minutes=1))
        second = bootstrap_http(root, connection_states=("Connected",))
        second.add("GET", rf"/v1/operations/{SEED_JOB}$", reply(body={"status": "Succeeded"}))
        second.add("GET", rf"/v1/operations/{SEED_JOB}/result$",
                   reply(body={"requiredAction": "UpdateFromGit", "workspaceHead": None, "remoteCommitHash": COMMIT}))
        resumed = bootstrap_context(self, root, second)
        resumed.state.load()
        deploy.phase_bootstrap(resumed)
        self.assertEqual(len(first.posts(r"/git/initializeConnection$")), 1)
        self.assertFalse(second.posts(r"/git/initializeConnection$"))
        self.assertEqual(resumed.state.section("bootstrap")["initialize"]["status"], "Succeeded")
        self.assertTrue(resumed.state.section("bootstrap")["initialize"]["validated"])

    def test_a_resumed_initialization_is_validated_even_when_already_initialized(self):
        root = make_root(self)
        http = bootstrap_http(root)
        http.add("GET", rf"/v1/operations/{SEED_JOB}$", reply(body={"status": "Succeeded"}))
        http.add("GET", rf"/v1/operations/{SEED_JOB}/result$", reply(body={"requiredAction": "CommitToGit"}))
        ctx = bootstrap_context(self, root, http)
        ctx.state.section("bootstrap")["initialize"] = {"operation": SEED_JOB, "status": "Running"}
        with self.assertRaisesRegex(deploy.Blocked, "CommitToGit"):
            deploy.phase_bootstrap(ctx)
        self.assertFalse(http.posts(r"/git/updateFromGit"))

    def test_a_persisted_initialization_result_is_reused_instead_of_reinitializing(self):
        root = make_root(self)
        http = bootstrap_http(root, connection_states=("Connected",))
        ctx = bootstrap_context(self, root, http)
        ctx.state.section("bootstrap")["initialize"] = {"status": "Succeeded", "result": {
            "requiredAction": "UpdateFromGit", "workspaceHead": None, "remoteCommitHash": COMMIT}}
        deploy.phase_bootstrap(ctx)
        self.assertFalse(http.posts(r"/git/initializeConnection$"))
        self.assertEqual(len(http.posts(r"/git/updateFromGit")), 1)

    def test_an_update_accepted_without_a_receipt_is_never_resubmitted_automatically(self):
        root = make_root(self)
        http = bootstrap_http(root)
        ctx = bootstrap_context(self, root, http)
        ctx.state.section("bootstrap")["update"] = {"status": "Unknown", "at": "earlier"}
        with self.assertRaises(deploy.Blocked) as stop:
            deploy.phase_bootstrap(ctx)
        self.assertIn("--retry-failed", " ".join(stop.exception.next_steps))
        self.assertFalse(http.posts(r"/git/updateFromGit"))

    def test_an_unavailable_plan_attachment_stops_without_an_explicit_fallback(self):
        root = make_root(self)
        http = bootstrap_http(root)
        http.routes.insert(0, ("POST", re.compile(r"/git/updateFromGit\?beta=true$"), [reply(403, {
            "errorCode": "FeatureNotAvailable", "message": "Specifying a deploymentPlan is not supported."})]))
        with self.assertRaises(deploy.Blocked) as stop:
            deploy.phase_bootstrap(bootstrap_context(self, root, http))
        self.assertIn("FeatureNotAvailable", stop.exception.message)
        self.assertIn("--bootstrap-fallback notebooks", " ".join(stop.exception.next_steps))
        self.assertFalse(http.posts(r"/git/updateFromGit$"))

    def test_the_fallback_syncs_without_the_plan_then_seeds_and_binds_in_order(self):
        root = make_root(self)
        http = bootstrap_http(root, definitions=(False, True))
        http.routes.insert(0, ("POST", re.compile(r"/git/updateFromGit\?beta=true$"), [reply(403, {
            "errorCode": "FeatureNotAvailable", "message": "Specifying a deploymentPlan is not supported."})]))
        http.add("POST", r"/git/updateFromGit$", reply(202, headers={"x-ms-operation-id": OPERATION}))
        for item, job in RUNS:
            http.add("GET", rf"/items/{item}/jobs/instances$", reply(body={"value": []}))
            http.add("POST", rf"/notebooks/{item}/jobs/execute/instances\?jobType=RunNotebook$", reply(202, headers={
                "Location": f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE}/items/{item}/jobs/instances/{job}"}))
            http.add("GET", rf"/items/{item}/jobs/instances/{job}$",
                     reply(body={"status": "InProgress"}), reply(body={"status": "Completed"}))
        ctx = bootstrap_context(self, root, http, bootstrap_fallback="notebooks")
        deploy.phase_bootstrap(ctx)
        (_, body), = http.posts(r"/git/updateFromGit$")
        self.assertNotIn("deploymentPlan", body["options"])
        runs = http.posts(r"/jobs/execute/instances")
        self.assertEqual([item in url for (url, _), (item, _) in zip(runs, RUNS)], [True, True])
        self.assertEqual([body["executionData"]["parameters"] for _, body in runs],
                         [deploy.notebook_parameters(step.parameters) for step in deploy.SCENARIO.runs])
        self.assertEqual(runs[0][1]["executionData"]["parameters"]["allow_other_workspaces"], {"value": False, "type": "bool"})
        self.assertEqual(ctx.state.section("bootstrap")["jobs"][NAMES["bind"]]["status"], "Completed")
        self.assertFalse(ctx.state.section("bootstrap")["update"]["with_plan"])

    def test_an_active_run_is_observed_instead_of_resubmitted(self):
        root = make_root(self)
        http = FakeHttp()
        http.add("GET", rf"/items/{SEED}/jobs/instances$", reply(body={"value": [{"id": SEED_JOB, "status": "InProgress"}]}))
        http.add("GET", rf"/items/{SEED}/jobs/instances/{SEED_JOB}$", reply(body={"status": "Completed"}))
        ctx = make_context(self, root, FakeShell([TOKEN_RULE]), http)
        deploy.run_notebook(ctx, WORKSPACE, SEED, NAMES["seed"], ctx.state.section("bootstrap"), skip_completed=True)
        self.assertFalse(http.posts(r"/jobs/execute/instances"))

    def test_a_failed_run_is_not_resubmitted_without_retry_failed(self):
        root = make_root(self)
        http = FakeHttp()
        http.add("GET", rf"/items/{SEED}/jobs/instances$", reply(body={"value": []}))
        ctx = make_context(self, root, FakeShell([TOKEN_RULE]), http)
        receipts = {"jobs": {NAMES["seed"]: {"id": SEED_JOB, "status": "Failed", "failure": "schema mismatch"}}}
        with self.assertRaisesRegex(deploy.Blocked, "schema mismatch") as stop:
            deploy.run_notebook(ctx, WORKSPACE, SEED, NAMES["seed"], receipts, skip_completed=True)
        self.assertIn("--retry-failed", " ".join(stop.exception.next_steps))

    def test_a_different_git_target_is_never_repointed(self):
        root = make_root(self)
        http = bootstrap_http(root)
        http.routes.insert(0, ("GET", re.compile(r"/git/connection$"), [reply(body={
            "gitConnectionState": "ConnectedAndInitialized",
            "gitProviderDetails": {**REQUEST["gitProviderDetails"], "branchName": "feature"}})]))
        with self.assertRaisesRegex(deploy.Blocked, "different Git target"):
            deploy.phase_bootstrap(bootstrap_context(self, root, http))

    def test_only_additions_of_reviewed_items_sync_automatically(self):
        root = make_root(self)
        changes = incoming(root)
        changes[0] = {**changes[0], "remoteChange": "Modified", "workspaceChange": "Modified"}
        http = bootstrap_http(root, status={"workspaceHead": "d" * 40, "remoteCommitHash": COMMIT, "changes": changes})
        with self.assertRaisesRegex(deploy.Blocked, "Only additions"):
            deploy.phase_bootstrap(bootstrap_context(self, root, http))
        self.assertFalse(http.posts(r"/git/updateFromGit"))

    def test_fabric_must_sync_exactly_the_reviewed_commit(self):
        root = make_root(self)
        http = bootstrap_http(root, status={"workspaceHead": None, "remoteCommitHash": "e" * 40, "changes": incoming(root)})
        with self.assertRaisesRegex(deploy.Blocked, "not the reviewed"):
            deploy.phase_bootstrap(bootstrap_context(self, root, http))

    def test_commit_to_git_is_never_chosen_automatically(self):
        root = make_root(self)
        http = bootstrap_http(root, connection_states=("Connected",))
        http.routes.insert(0, ("POST", re.compile(r"/git/initializeConnection$"), [reply(body={"requiredAction": "CommitToGit"})]))
        with self.assertRaisesRegex(deploy.Blocked, "CommitToGit"):
            deploy.phase_bootstrap(bootstrap_context(self, root, http))

    def test_binding_checks_target_local_pins_not_placeholders(self):
        decode = deploy.base64.b64decode

        def parts(definition):
            return {part["path"]: decode(part["payload"]).decode() for part in definition["definition"]["parts"]}

        self.assertEqual(deploy.binding_problems(parts(tmdl(True)), WORKSPACE, LAKEHOUSE, SQL_ENDPOINT), [])
        first, last = deploy.SCENARIO.tables[0], deploy.SCENARIO.tables[-1]
        self.assertIn(f"{first} has no partition yet",
                      deploy.binding_problems(parts(tmdl(False)), WORKSPACE, LAKEHOUSE, SQL_ENDPOINT))
        placeholder = deploy.binding_problems(parts(tmdl(True, "00000000-0000-0000-0000-000000000000")), WORKSPACE,
                                              LAKEHOUSE, SQL_ENDPOINT)
        self.assertIn(f"{last} is not pinned to the DEV workspace", placeholder)

    def test_binding_requires_source_pins_and_the_dev_sql_endpoint(self):
        decode = deploy.base64.b64decode

        def parts(definition):
            return {part["path"]: decode(part["payload"]).decode() for part in definition["definition"]["parts"]}

        table = deploy.SCENARIO.tables[0]
        # Graph materialization identifies the source by its pins, which Fabric strips from a OneLake expression.
        for unpinned in (parts(tmdl(True, pins=False)), parts(tmdl(True, pins=False, onelake=True))):
            problems = deploy.binding_problems(unpinned, WORKSPACE, LAKEHOUSE, SQL_ENDPOINT)
            self.assertIn(f"{table} is not pinned to the DEV lakehouse", problems)
            self.assertIn(f"{table} source kind is not Lakehouse", problems)
        self.assertEqual(deploy.binding_problems(parts(tmdl(True, onelake=True)), WORKSPACE, LAKEHOUSE, SQL_ENDPOINT),
                         ["expressions.tmdl does not read the DEV lakehouse SQL endpoint"])
        bound = parts(tmdl(True))
        self.assertEqual(deploy.binding_problems(bound, WORKSPACE, LAKEHOUSE, SQL_ENDPOINT), [])
        elsewhere = {**bound, "expressions.tmdl": bound["expressions.tmdl"].replace(LAKEHOUSE, ONTOLOGY)}
        self.assertEqual(deploy.binding_problems(elsewhere, WORKSPACE, LAKEHOUSE, SQL_ENDPOINT),
                         ["expressions.tmdl does not read the DEV lakehouse SQL endpoint"])
        concrete = parts(tmdl(True, concrete=True))
        self.assertIn("expressions.tmdl does not read the DEV lakehouse SQL endpoint",
                      deploy.binding_problems(concrete, WORKSPACE, LAKEHOUSE, (SQL_ENDPOINT[0], ONTOLOGY)))
        self.assertEqual(deploy.binding_problems(concrete, WORKSPACE, LAKEHOUSE, (SQL_ENDPOINT[0].upper(), SQL_ENDPOINT[1])), [])
        expression = deploy.SCENARIO.expression
        other_source = {path: text.replace(f"expressionSource: {expression}", "expressionSource: DatabaseQuery")
                        for path, text in bound.items()}
        self.assertIn(f"{table} partition expressionSource is not {expression}",
                      deploy.binding_problems(other_source, WORKSPACE, LAKEHOUSE, SQL_ENDPOINT))


    def test_git_synchronized_workspaces_refuse_definition_updates(self):
        root = make_root(self)
        http = bootstrap_http(root)
        with self.assertRaisesRegex(deploy.Blocked, "natively imported items only") as stop:
            deploy.phase_bootstrap(bootstrap_context(self, root, http, update_definitions=True))
        self.assertIn("Fabric source control", " ".join(stop.exception.next_steps))
        self.assertFalse(http.calls)

    def test_notebook_parameters_are_typed_for_fabric(self):
        self.assertEqual(deploy.notebook_parameters({"name": "Onboarding", "flag": False, "count": 3, "ratio": 0.5}), {
            "name": {"value": "Onboarding", "type": "string"}, "flag": {"value": False, "type": "bool"},
            "count": {"value": 3, "type": "int"}, "ratio": {"value": 0.5, "type": "float"}})
        with self.assertRaisesRegex(deploy.Blocked, "must be a string"):
            deploy.notebook_parameters({"missing": None})

    def test_the_scenario_descriptor_matches_the_plan_and_notebook_parameter_cells(self):
        ctx = make_context(self, make_root(self))
        expected = deploy.workspace_items(ctx)
        plan = (ctx.root / "workspace" / expected[deploy.SCENARIO.plan]["directory"] / "plan.yml").read_text(encoding="utf-8")
        groups = {}
        for block in re.split(r"(?m)^  - name: ", plan)[1:]:
            groups[block.splitlines()[0].strip()] = {
                name: {"true": True, "false": False}.get(value.strip(), value.strip())
                for name, value in re.findall(r"(?m)^ +- name: (\w+)\n +value: (.+)$", block)}
        for step in deploy.SCENARIO.runs:
            name = deploy.scenario_name(step.item)
            self.assertEqual(groups[name], step.parameters, name)
            source = (ctx.root / "workspace" / expected[name]["directory"] / "notebook-content.py").read_text(encoding="utf-8")
            cell = source.split("# PARAMETERS CELL ********************", 1)[1].split("# METADATA", 1)[0]
            defaults = {node.targets[0].id: node.value.value for node in ast.parse(cell).body if isinstance(node, ast.Assign)}
            for key, value in step.parameters.items():
                self.assertIs(type(defaults[key]), type(value), f"{name}.{key}")

    def test_the_scenario_descriptor_matches_the_ontology_and_binder(self):
        ctx = make_context(self, make_root(self))
        expected = deploy.workspace_items(ctx)
        folder = ctx.root / "workspace" / expected[NAMES["ontology"]]["directory"]
        model = (folder / "model.tmdl").read_text(encoding="utf-8")
        self.assertEqual(sorted(re.findall(r"(?m)^ref table (\S+)$", model)), sorted(deploy.SCENARIO.tables))
        binder = ctx.root / "workspace" / expected[NAMES["bind"]]["directory"] / "notebook-content.py"
        self.assertIn(f'expression_name = "{deploy.SCENARIO.expression}"', binder.read_text(encoding="utf-8"))
        self.assertRegex(deploy.SCENARIO.registration, r"^[a-z][a-z0-9-]{0,30}[a-z0-9]$")


class ScenarioContractTests(unittest.TestCase):
    """The names other owners rely on: the workspace items, the knowledge registration and the agents."""

    def test_the_scenario_is_the_template_sample(self):
        self.assertEqual(deploy.SCENARIO.items, (("lakehouse", "Lakehouse", "Scenario"), ("seed", "Notebook", "SeedScenario"),
                                                 ("ontology", "Ontology", "ScenarioOntology"),
                                                 ("bind", "Notebook", "BindScenario")))
        self.assertEqual((deploy.SCENARIO.plan, deploy.SCENARIO.registration, deploy.SCENARIO.expression),
                         ("Bootstrap", "scenario", "ScenarioLakehouse"))
        self.assertEqual(deploy.SCENARIO.tables, ("demo_sites", "demo_assets"))
        self.assertEqual([(step.item, step.parameters, step.skip_completed) for step in deploy.SCENARIO.runs], [
            ("seed", {"lakehouse_name": "Scenario", "allow_other_workspaces": False}, True),
            ("bind", {"lakehouse_name": "Scenario", "ontology_name": "ScenarioOntology"}, False)])
        self.assertEqual(deploy.run_order(), "SeedScenario and BindScenario")
        self.assertEqual(deploy.SCENARIO.expected_facts, ("Pump B", "North Works"))
        # The default knowledge_folders key, which SeedScenario's Files/documents folder fills.
        self.assertEqual(deploy.SCENARIO.document_source, "documents")

    def test_the_scenario_descriptor_is_self_consistent(self):
        lakehouse, ontology = deploy.scenario_name("lakehouse"), deploy.scenario_name("ontology")
        notebooks = {key for key, kind, _ in deploy.SCENARIO.items if kind == "Notebook"}
        for step in deploy.SCENARIO.runs:
            self.assertIn(step.item, notebooks)
            self.assertEqual(step.parameters["lakehouse_name"], lakehouse)
            self.assertEqual(step.parameters.get("ontology_name", ontology), ontology)
        # One single-entity question, with the Status value quoted, about rows only the live ontology holds.
        self.assertTrue(deploy.SCENARIO.ontology_question.startswith(f"Using the live {ontology}, which Asset rows"))
        self.assertIn('Status value "Maintenance"', deploy.SCENARIO.ontology_question)
        self.assertEqual(deploy.knowledge.registration_name(deploy.SCENARIO.registration), deploy.SCENARIO.registration)
        self.assertEqual(deploy.knowledge.description_text(deploy.SCENARIO.registration_description),
                         deploy.SCENARIO.registration_description)
        self.assertIn("indexed starter document", deploy.SCENARIO.document_question)
        # The billable public-context question names no site, asset or other private detail.
        question = deploy.SCENARIO.public_context_question
        self.assertIn("pump", question.lower())
        for private in (*deploy.SCENARIO.expected_facts, "Pump A", "demo-", lakehouse, ontology):
            self.assertNotIn(private, question)
        for fact in deploy.SCENARIO.expected_facts:
            self.assertIn(fact, deploy.SCENARIO.app_question)
        self.assertIn(deploy.SCENARIO.app_question, deploy.APP_NEXT)
        args = deploy.parse_arguments(["up"])
        self.assertEqual((args.ontology_question, args.document_question),
                         (deploy.SCENARIO.ontology_question, deploy.SCENARIO.document_question))

    def test_the_agents_follow_the_contract(self):
        self.assertEqual(deploy.foundry.VARIANTS, {"with_iq": "agents/scenario-with-iq",
                                                   "without_iq": "agents/scenario-without-iq"})
        self.assertEqual(deploy.foundry.OPTIONAL_TOOLS, frozenset({"WorkIQMail"}))
        # Terraform names the agents the runner publishes, from the same sources.
        infra = "\n".join(path.read_text(encoding="utf-8") for path in sorted((REPO / "infra").glob("*.tf")))
        for key, source in deploy.foundry.VARIANTS.items():
            self.assertRegex(infra, rf'(?m)^\s+{key}\s+= {{ name = "\${{var\.project_name}}-{key.replace("_", "-")}", '
                                    rf'display_name = "[^"]+", source = "{source}" }}$')


class NativeBootstrapTests(unittest.TestCase):
    def context(self, *, definitions=(True,), **options):
        root = make_root(self)
        expected = deploy.workspace_items(make_context(self, root))
        ids = {item["displayName"]: item["id"] for item in ITEMS["value"]}
        ids[deploy.SCENARIO.plan] = CONNECTION
        self.items = [{"id": ids[name], "workspaceId": WORKSPACE, "displayName": name,
                       "type": item["type"], "logicalId": item["logical_id"]}
                      for name, item in expected.items()]
        self.result = {"importItemDefinitionsDetails": [
            {"itemId": item["id"], "itemDisplayName": item["displayName"], "itemType": item["type"],
             "itemLogicalId": item["logicalId"], "operationType": "Create", "operationStatus": "Succeeded"}
            for item in self.items]}
        http = bootstrap_http(root, definitions=definitions)
        http.routes.insert(0, ("GET", re.compile(rf"/workspaces/{WORKSPACE}/items$"),
                                [reply(body={"value": []}), reply(body={"value": self.items})]))
        http.add("POST", r"/items/bulkImportDefinitions\?beta=true$",
                 reply(202, headers={"x-ms-operation-id": OPERATION, "Retry-After": "1"}))
        http.add("GET", rf"/operations/{OPERATION}/result$", reply(body=self.result))
        files = "\0".join(path.relative_to(root).as_posix() for path in sorted((root / "workspace").rglob("*"))
                         if path.is_file()) + "\0"
        shell = FakeShell([TOKEN_RULE, *GIT_RULES, (command("git", "ls-files"), deploy.Result(0, files))])
        ctx = make_context(self, root, shell, http, **options)
        ctx.environ = {}
        ctx.cache["variables"]["github_branch_name"] = "main"
        ctx.cache["outputs"] = {"dev_workspace_id": WORKSPACE}
        return ctx

    def test_default_import_uses_the_plan_and_preserves_all_definition_bytes_without_git(self):
        ctx = self.context()
        self.assertIn(LAKEHOUSE, deploy.phase_bootstrap(ctx))
        (_, body), = ctx.http.posts(r"/items/bulkImportDefinitions\?beta=true$")
        self.assertEqual(body["options"], {"allowPairingByName": False, "deploymentPlan": {
            "logicalId": PLAN_LOGICAL_ID, "referenceType": "ByLogicalId"}})
        for part in body["definitionParts"]:
            self.assertEqual(part["payloadType"], "InlineBase64")
            source = ctx.root / "workspace" / part["path"].lstrip("/")
            self.assertEqual(base64.b64decode(part["payload"]), source.read_bytes())
        self.assertEqual(sum(part["path"].endswith("/.platform") for part in body["definitionParts"]), len(ITEMS["value"]) + 1)
        self.assertFalse(any("/git/" in url or urllib.parse.urlsplit(url).hostname == "api.github.com"
                             for _, url, *_ in ctx.http.calls))
        self.assertFalse(ctx.http.posts(r"/jobs/execute/instances"))
        self.assertTrue(ctx.state.section("bootstrap")["native"]["import"]["validated"])
        before = len(ctx.http.posts(r"/bulkImportDefinitions"))
        deploy.phase_bootstrap(ctx)
        self.assertEqual(len(ctx.http.posts(r"/bulkImportDefinitions")), before, "reruns must preserve live definitions")

    def test_an_accepted_import_resumes_without_resubmitting(self):
        ctx = self.context(timeout_minutes=1)
        ctx.http.routes.insert(0, ("GET", re.compile(rf"/operations/{OPERATION}$"),
                                  [reply(body={"status": "Running"})]))
        with self.assertRaises(deploy.Waiting):
            deploy.phase_bootstrap(ctx)
        self.assertEqual(ctx.state.section("bootstrap")["native"]["import"]["status"], "Running")
        ctx.http.routes.pop(0)
        ctx.start("bootstrap")
        deploy.phase_bootstrap(ctx)
        self.assertEqual(len(ctx.http.posts(r"/bulkImportDefinitions")), 1)

    def test_a_reviewed_retry_imports_only_missing_items_after_partial_failure(self):
        ctx = self.context()
        ctx.http.routes.insert(0, ("GET", re.compile(rf"/operations/{OPERATION}$"),
                                  [reply(body={"status": "Failed", "error": {"message": "Notebook failed"}})]))
        with self.assertRaisesRegex(deploy.Blocked, "Notebook failed"):
            deploy.phase_bootstrap(ctx)
        missing = {NAMES["ontology"], NAMES["bind"]}
        existing = [item for item in self.items if item["displayName"] not in missing]
        ctx.http.routes.insert(0, ("GET", re.compile(rf"/workspaces/{WORKSPACE}/items$"),
                                  [reply(body={"value": existing}), reply(body={"value": self.items})]))
        result = {"importItemDefinitionsDetails": [item for item in self.result["importItemDefinitionsDetails"]
                                                   if item["itemDisplayName"] in missing]}
        ctx.http.routes.insert(0, ("POST", re.compile(r"/items/bulkImportDefinitions\?beta=true$"), [reply(body=result)]))
        ctx.args.retry_failed = True
        deploy.phase_bootstrap(ctx)
        _, body = ctx.http.posts(r"/bulkImportDefinitions")[-1]
        platforms = [json.loads(base64.b64decode(part["payload"]))["metadata"]["displayName"]
                     for part in body["definitionParts"] if part["path"].endswith("/.platform")]
        self.assertEqual(set(platforms), missing)
        self.assertFalse(any(part["path"].startswith(f"/{FOLDERS['lakehouse']}/") for part in body["definitionParts"]))

    def test_missing_receipts_and_partial_success_stop_instead_of_claiming_readiness(self):
        for response in (reply(202), reply(body={})):
            with self.subTest(status=response.status, body=response.json()):
                ctx = self.context()
                ctx.http.routes.insert(0, ("POST", re.compile(r"/items/bulkImportDefinitions\?beta=true$"), [response]))
                with self.assertRaises(deploy.Blocked):
                    deploy.phase_bootstrap(ctx)
                with self.assertRaises(deploy.Blocked):
                    deploy.phase_bootstrap(ctx)
                self.assertEqual(len(ctx.http.posts(r"/bulkImportDefinitions")), 1)
                self.assertFalse(ctx.http.posts(r"/jobs/execute/instances"))
        ctx = self.context()
        result = {"importItemDefinitionsDetails": [
            {**detail, "operationStatus": "SucceededDespiteFailures"} for detail in self.result["importItemDefinitionsDetails"]]}
        ctx.http.routes.insert(0, ("POST", re.compile(r"/items/bulkImportDefinitions\?beta=true$"), [reply(body=result)]))
        with self.assertRaisesRegex(deploy.Blocked, "SucceededDespiteFailures"):
            deploy.phase_bootstrap(ctx)
        self.assertEqual(ctx.state.section("bootstrap")["native"]["import"]["status"], "Failed")

    def test_conflicting_names_or_renamed_logical_ids_are_not_overwritten(self):
        for changes in ({"logicalId": TENANT}, {"displayName": "RenamedScenario"}, {"workspaceId": TENANT}):
            with self.subTest(changes=changes):
                ctx = self.context()
                item = next(item for item in self.items if item["displayName"] == NAMES["lakehouse"])
                ctx.http.routes.insert(0, ("GET", re.compile(rf"/workspaces/{WORKSPACE}/items$"),
                                          [reply(body={"value": [{**item, **changes}]})]))
                with self.assertRaises(deploy.Blocked):
                    deploy.phase_bootstrap(ctx)
                self.assertFalse(ctx.http.posts(r"/bulkImportDefinitions"))

    def test_imported_definitions_are_not_replaced_after_source_changes(self):
        ctx = self.context()
        deploy.phase_bootstrap(ctx)
        notebook = ctx.root / "workspace" / FOLDERS["seed"] / "notebook-content.py"
        notebook.write_text(notebook.read_text(encoding="utf-8") + "\n# changed source\n", encoding="utf-8")
        with self.assertRaisesRegex(deploy.Blocked, "changed or unrecorded definitions"):
            deploy.phase_bootstrap(ctx)
        self.assertEqual(len(ctx.http.posts(r"/bulkImportDefinitions")), 1)

    def test_import_reads_only_tracked_parts_and_rejects_symlinks(self):
        ctx = self.context()
        extra = ctx.root / "workspace" / FOLDERS["lakehouse"] / ".env"
        extra.write_text("not a definition", encoding="utf-8")
        parts = deploy.workspace_definition_parts(ctx, deploy.workspace_items(ctx))
        self.assertFalse(any(part["path"].endswith(".env") for group in parts.values() for part in group))
        notebook = ctx.root / "workspace" / FOLDERS["seed"] / "notebook-content.py"
        notebook.unlink()
        notebook.symlink_to(extra)
        with self.assertRaisesRegex(deploy.Blocked, "Not a regular workspace definition"):
            deploy.phase_bootstrap(ctx)
        self.assertFalse(ctx.http.posts(r"/bulkImportDefinitions"))

    def test_plan_attachment_failure_requires_an_explicit_notebook_fallback(self):
        ctx = self.context(definitions=(False, True))
        ctx.http.routes.insert(0, ("POST", re.compile(r"/items/bulkImportDefinitions\?beta=true$"),
                                  [reply(403, {"errorCode": "FeatureNotAvailable"})]))
        with self.assertRaises(deploy.Blocked) as stop:
            deploy.phase_bootstrap(ctx)
        self.assertIn("--bootstrap-fallback notebooks", " ".join(stop.exception.next_steps))
        self.assertFalse(ctx.http.posts(r"/items/bulkImportDefinitions$"))
        # The refused request did not create items.
        ctx.http.routes.insert(0, ("GET", re.compile(rf"/workspaces/{WORKSPACE}/items$"),
                                  [reply(body={"value": []}), reply(body={"value": self.items})]))
        ctx.args.bootstrap_fallback = "notebooks"
        ctx.http.add("POST", r"/items/bulkImportDefinitions$", reply(body=self.result))
        for item, job in RUNS:
            ctx.http.add("GET", rf"/items/{item}/jobs/instances$", reply(body={"value": []}))
            ctx.http.add("POST", rf"/notebooks/{item}/jobs/execute/instances\?jobType=RunNotebook$",
                         reply(202, headers={"Location":
                             f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE}/items/{item}/jobs/instances/{job}"}))
            ctx.http.add("GET", rf"/items/{item}/jobs/instances/{job}$", reply(body={"status": "Completed"}))
        deploy.phase_bootstrap(ctx)
        (_, body), = ctx.http.posts(r"/items/bulkImportDefinitions$")
        self.assertNotIn("deploymentPlan", body["options"])
        runs = [url for url, _ in ctx.http.posts(r"/jobs/execute/instances")]
        self.assertEqual([item in url for url, (item, _) in zip(runs, RUNS)], [True, True])


    def accept_updates(self, ctx, response=None):
        ctx.http.routes.insert(0, ("POST", re.compile(r"/updateDefinition\?updateMetadata=true$"), [
            response or reply(202, headers={"x-ms-operation-id": OPERATION, "Retry-After": "1"})]))

    def jobs(self, ctx, *runs, completed=()):
        for item, job in runs:
            ctx.http.add("GET", rf"/items/{item}/jobs/instances$",
                         reply(body={"value": [{"id": job, "status": "Completed"}] if item in completed else []}))
            ctx.http.add("POST", rf"/notebooks/{item}/jobs/execute/instances\?jobType=RunNotebook$", reply(202, headers={
                "Location": f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE}/items/{item}/jobs/instances/{job}"}))
            ctx.http.add("GET", rf"/items/{item}/jobs/instances/{job}$", reply(body={"status": "Completed"}))

    def change(self, ctx, relative):
        path = ctx.root / "workspace" / relative
        path.write_text(path.read_text(encoding="utf-8") + "\n# reviewed change\n", encoding="utf-8")
        return path

    def approval(self, ctx):
        ctx.args.update_definitions = True
        with self.assertRaises(deploy.NeedsApproval) as stop:
            deploy.phase_bootstrap(ctx)
        return stop.exception.approval

    def test_changed_definitions_are_replaced_only_after_an_exact_item_update_approval(self):
        ctx = self.context()
        deploy.phase_bootstrap(ctx)
        notebook = self.change(ctx, f"{FOLDERS['seed']}/notebook-content.py")
        with self.assertRaisesRegex(deploy.Blocked, f"{NAMES['seed']} already exists with changed") as stop:
            deploy.phase_bootstrap(ctx)
        steps = " ".join(stop.exception.next_steps)
        self.assertIn(f"git diff {COMMIT[:12]} HEAD -- workspace/{FOLDERS['seed']}", steps)
        self.assertIn("--update-definitions", steps)
        ctx.args.update_definitions, ctx.args.yes = True, True
        with self.assertRaises(deploy.NeedsApproval) as stop:
            deploy.phase_bootstrap(ctx)
        approval = stop.exception.approval
        self.assertEqual((approval["key"], approval["summary"]["counts"]), ("item-update", {"update": 1}))
        self.assertIn(f"--approve item-update={approval['sha256'][:12]}", " ".join(stop.exception.next_steps))
        ctx.args.approve = {"item-update": "0" * 12}
        with self.assertRaisesRegex(deploy.NeedsApproval, "does not match the pending change"):
            deploy.phase_bootstrap(ctx)
        self.assertFalse(ctx.http.posts(r"/updateDefinition"))
        self.accept_updates(ctx)
        self.jobs(ctx, (SEED, SEED_JOB))
        ctx.args.approve = {"item-update": approval["sha256"][:12]}
        self.assertIn(f"updated {NAMES['seed']}", deploy.phase_bootstrap(ctx))
        (url, body), = ctx.http.posts(r"/updateDefinition")
        self.assertTrue(url.endswith(f"/workspaces/{WORKSPACE}/items/{SEED}/updateDefinition?updateMetadata=true"))
        parts = {part["path"]: base64.b64decode(part["payload"]) for part in body["definition"]["parts"]}
        self.assertEqual(parts, {"notebook-content.py": notebook.read_bytes(),
                                 ".platform": (notebook.parent / ".platform").read_bytes()})
        (run, run_body), = ctx.http.posts(r"/jobs/execute/instances")
        self.assertIn(SEED, run)
        self.assertEqual(run_body["executionData"]["parameters"]["allow_other_workspaces"], {"value": False, "type": "bool"})
        native = ctx.state.section("bootstrap")["native"]
        self.assertEqual((native["updates"][NAMES["seed"]]["status"], native["rerun"]), ("Succeeded", []))
        deploy.phase_bootstrap(ctx)
        self.assertEqual((len(ctx.http.posts(r"/updateDefinition")), len(ctx.http.posts(r"/jobs/execute/instances"))), (1, 1))

    def test_an_updated_notebook_runs_again_even_after_an_unobserved_earlier_run(self):
        ctx = self.context()
        deploy.phase_bootstrap(ctx)
        self.change(ctx, f"{FOLDERS['seed']}/notebook-content.py")
        approval = self.approval(ctx)
        # A run of the old definition that an earlier `up` stopped observing has finished since.
        parameters = next(step.parameters for step in deploy.SCENARIO.runs if step.item == "seed")
        ctx.state.section("bootstrap")["jobs"] = {NAMES["seed"]: {"id": SEED_JOB, "status": "Submitted", "parameters": parameters}}
        self.accept_updates(ctx)
        self.jobs(ctx, (SEED, RERUN_JOB))
        ctx.http.routes.insert(0, ("GET", re.compile(rf"/items/{SEED}/jobs/instances$"),
                                   [reply(body={"value": [{"id": SEED_JOB, "status": "Completed"}]})]))
        ctx.args.approve = {"item-update": approval["sha256"][:12]}
        deploy.phase_bootstrap(ctx)
        (run, _), = ctx.http.posts(r"/jobs/execute/instances")
        self.assertIn(SEED, run)
        self.assertEqual(ctx.state.section("bootstrap")["jobs"][NAMES["seed"]]["id"], RERUN_JOB)

    def test_lakehouse_definitions_are_never_replaced(self):
        ctx = self.context()
        deploy.phase_bootstrap(ctx)
        self.change(ctx, f"{FOLDERS['lakehouse']}/lakehouse.metadata.json")
        with self.assertRaises(deploy.Blocked) as stop:
            deploy.phase_bootstrap(ctx)
        self.assertNotIn("--update-definitions", " ".join(stop.exception.next_steps))
        ctx.args.update_definitions, ctx.args.approve = True, {"item-update": "f" * 12}
        with self.assertRaisesRegex(deploy.Blocked, f"never replaces the definition of Lakehouse {NAMES['lakehouse']}"):
            deploy.phase_bootstrap(ctx)
        self.assertFalse(ctx.http.posts(r"/updateDefinition"))

    def test_an_updated_ontology_is_rebound_and_verified(self):
        ctx = self.context(definitions=(True, False, True))
        deploy.phase_bootstrap(ctx)
        self.change(ctx, f"{FOLDERS['ontology']}/model.tmdl")
        approval = self.approval(ctx)
        self.assertEqual(approval["summary"]["changes"], [{"action": "update", "address": f"Ontology {NAMES['ontology']}"}])
        self.accept_updates(ctx)
        self.jobs(ctx, *RUNS, completed=(SEED,))
        ctx.args.approve = {"item-update": approval["sha256"][:12]}
        self.assertIn(f"updated {NAMES['ontology']}", deploy.phase_bootstrap(ctx))
        (url, body), = ctx.http.posts(r"/updateDefinition")
        self.assertIn(f"/items/{ONTOLOGY}/updateDefinition", url)
        paths = {part["path"] for part in body["definition"]["parts"]}
        self.assertTrue({".platform", "model.tmdl"} <= paths and any(path.startswith("entities/") for path in paths))
        self.assertFalse(any(path.startswith("/") for path in paths))
        runs = [url for url, _ in ctx.http.posts(r"/jobs/execute/instances")]
        self.assertEqual(len(runs), 1, "the seed completed already; only the binder reruns")
        self.assertIn(BIND, runs[0])

    def test_an_update_accepted_without_a_receipt_is_resubmitted_only_after_review(self):
        ctx = self.context()
        deploy.phase_bootstrap(ctx)
        self.change(ctx, f"{FOLDERS['bind']}/notebook-content.py")
        approval = self.approval(ctx)
        self.accept_updates(ctx, reply(202))
        ctx.args.approve = {"item-update": approval["sha256"][:12]}
        for _ in range(2):
            with self.assertRaises(deploy.Blocked):
                deploy.phase_bootstrap(ctx)
        self.assertEqual(len(ctx.http.posts(r"/updateDefinition")), 1)
        self.assertEqual(ctx.state.section("bootstrap")["native"]["updates"][NAMES["bind"]]["status"], "Unknown")
        self.accept_updates(ctx)
        self.jobs(ctx, (BIND, BIND_JOB))
        ctx.args.retry_failed = True
        self.assertIn(f"updated {NAMES['bind']}", deploy.phase_bootstrap(ctx))
        self.assertEqual(len(ctx.http.posts(r"/updateDefinition")), 2)

    def test_items_removed_from_the_repository_are_reported_not_deleted(self):
        ctx = self.context()
        deploy.phase_bootstrap(ctx)
        native = ctx.state.section("bootstrap")["native"]
        native["item_ids"]["RetiredNotebook"] = TENANT
        native["definitions"]["RetiredNotebook"] = "0" * 64
        self.assertIn("left retired items in DEV: RetiredNotebook", deploy.phase_bootstrap(ctx))
        self.assertEqual(native["retired"], {"RetiredNotebook": TENANT})
        self.assertFalse([call for call in ctx.http.calls if call[0] == "DELETE"])
        self.assertEqual(len(ctx.http.posts(r"/bulkImportDefinitions")), 1)


class KnowledgeRegistrationTests(unittest.TestCase):
    READY = {"dev_workspace_id": WORKSPACE, "knowledge_base_name": "demo-knowledge",
             "ontology_check_knowledge_base_name": "demo-ontology-check",
             "knowledge_source_names": {"documents": "demo-documents", "manuals": "demo-manuals"},
             "knowledge_base_mcp_endpoint": "https://srch.search.windows.net/knowledgebases/demo-knowledge/mcp?api-version=x",
             "knowledge_base_connection_id": "c", "knowledge_setup_pending": False,
             "ontology_knowledge_sources": {deploy.SCENARIO.registration: {"ontology_id": ONTOLOGY, "workspace_id": WORKSPACE}}}

    def run_phase(self, ctx, ready=None):
        with mock.patch.object(deploy, "resolve_items", return_value={"lakehouse": LAKEHOUSE, "ontology": ONTOLOGY}), \
                mock.patch.object(deploy, "converge_knowledge", return_value="applied") as converge, \
                mock.patch.object(deploy, "outputs", return_value=ready or self.READY):
            try:
                return deploy.phase_knowledge(ctx), converge.call_count
            finally:
                self.converged = converge.call_count

    def context(self, *results):
        script = lambda args: len(args) > 1 and args[1].endswith("configure_knowledge.py")  # noqa: E731
        self.shell = FakeShell([(lambda args: script(args) and "--replace" in args, deploy.Result(0, "Replaced.")),
                                (script, results[0])])
        ctx = make_context(self, make_root(self), self.shell)
        ctx.cache["variables"].update(knowledge_lakehouse_id=None, knowledge_ontologies={}, work_iq_enabled=False)
        return ctx

    def test_starter_settings_are_filled_without_an_approval(self):
        ctx = self.context(deploy.Result(0, "Updated only the starter knowledge settings."))
        self.assertIn("applied", self.run_phase(ctx)[0])
        (call,) = self.shell.calls
        self.assertEqual(call[call.index("--registration") + 1], deploy.SCENARIO.registration)
        self.assertEqual(call[call.index("--description") + 1], deploy.SCENARIO.registration_description)

    def test_an_earlier_registration_is_replaced_only_after_approval(self):
        replacement = ("Replace the earlier knowledge registration in infra/terraform.tfvars: "
                       f"knowledge_ontologies.earlier-sample (ontology {SEED}) with {deploy.SCENARIO.registration} "
                       f"(ontology {ONTOLOGY}).\n")
        ctx = self.context(deploy.Result(deploy.knowledge.REPLACEMENT_REQUIRED, replacement))
        with self.assertRaises(deploy.NeedsApproval) as stop:
            self.run_phase(ctx)
        self.assertEqual(stop.exception.approval["key"], "knowledge-registration")
        self.assertIn("knowledge_ontologies.earlier-sample", stop.exception.approval["description"])
        self.assertEqual((self.converged, [call for call in self.shell.calls if "--replace" in call]), (0, []))
        ctx.args.yes = True
        self.assertIn("applied", self.run_phase(ctx)[0])
        self.assertEqual(len([call for call in self.shell.calls if "--replace" in call]), 1)

    def test_custom_registrations_stop_for_review(self):
        ctx = self.context(deploy.Result(1, "", "Knowledge configuration failed: knowledge_ontologies is already customized"))
        with self.assertRaisesRegex(deploy.Blocked, "already customized"):
            self.run_phase(ctx)
        self.assertEqual(self.converged, 0)

    def test_a_changed_work_iq_setting_is_reported_until_the_agents_are_published_again(self):
        for applied, reminded in (({"enabled": False}, True), ({"enabled": True}, False)):
            with self.subTest(applied=applied):
                ctx = self.context(deploy.Result(0, "Updated only the starter knowledge settings."))
                ctx.state.section("agents")["with_iq"] = {"name": "demo-with-iq", "work_iq_enabled": True}
                report = self.run_phase(ctx, {**self.READY, "work_iq": applied})[0]
                self.assertEqual("still names the removed work-iq connection" in report, reminded)
                self.assertEqual("Run deploy.py up through the agent phase." in report, reminded)

    def test_the_folder_sources_and_the_ontology_check_base_must_exist(self):
        report = self.run_phase(self.context(deploy.Result(0, "Updated only the starter knowledge settings.")))[0]
        self.assertIn("knowledge base demo-knowledge with OneLake folder sources (documents, manuals)", report)
        self.assertIn("live ontology source in demo-ontology-check", report)
        incomplete = {name: value for name, value in self.READY.items()
                      if name not in ("ontology_check_knowledge_base_name", "knowledge_base_mcp_endpoint")}
        incomplete["knowledge_source_names"] = {"manuals": "demo-manuals"}
        with self.assertRaises(deploy.Blocked) as stop:
            self.run_phase(self.context(deploy.Result(0, "Updated only the starter knowledge settings.")), incomplete)
        self.assertEqual(stop.exception.message, "Knowledge registration is incomplete: ontology_check_knowledge_base_name "
                         "is missing; knowledge_base_mcp_endpoint is missing; knowledge_source_names has no "
                         f"{deploy.SCENARIO.document_source} source")


class ReadinessTests(unittest.TestCase):
    def test_ingestion_is_ready_after_a_clean_sync_even_when_later_syncs_index_nothing(self):
        observed = {"lastSynchronizationState": {"errors": [], "itemsSkipped": 0, "itemsUpdatesFailed": 0,
                                                 "itemsUpdatesProcessed": 1},
                    "statistics": {"averageItemsProcessedPerSynchronization": 1, "totalSynchronization": 1}}
        self.assertEqual(deploy.ingestion_state(observed)[0], "ready")
        # Observed live: the integer average floors to 0 after later no-op syncs.
        later = {"lastSynchronizationState": {"errors": [], "itemsUpdatesFailed": 0, "itemsUpdatesProcessed": 0},
                 "statistics": {"averageItemsProcessedPerSynchronization": 0, "totalSynchronization": 5}}
        self.assertEqual(deploy.ingestion_state(later)[0], "ready")
        self.assertEqual(deploy.ingestion_state({"currentSynchronizationState": None})[0], "pending")
        failed = {"lastSynchronizationState": {"errors": [{"errorMessage": "Forbidden"}], "itemsUpdatesFailed": 1}}
        self.assertEqual(deploy.ingestion_state(failed), ("failed", "Forbidden"))

    def test_one_failed_sync_waits_but_repeated_failures_block(self):
        root = make_root(self)
        url = "https://srch.search.windows.net/knowledgesources/docs/status?api-version=x"

        def failed(end):
            return reply(body={"lastSynchronizationState": {"endTime": end, "errors": [{"errorMessage": "Forbidden"}]}})

        http = FakeHttp()
        http.add("GET", r"/status", failed("t1"), reply(body={"lastSynchronizationState": {"endTime": "t2", "errors": []}}))
        ctx = make_context(self, root, FakeShell([TOKEN_RULE]), http)
        self.assertEqual(deploy.wait_ingestion(ctx, url)["items_processed_last"], 0)
        http = FakeHttp()
        http.add("GET", r"/status", failed("t1"), failed("t1"), failed("t2"))
        ctx = make_context(self, root, FakeShell([TOKEN_RULE]), http)
        with self.assertRaisesRegex(deploy.Blocked, "consecutive synchronizations: Forbidden"):
            deploy.wait_ingestion(ctx, url)

    def test_live_ontology_evidence_requires_matching_ids_and_source_data(self):
        name = f"demo-{deploy.SCENARIO.registration}-ontology"
        source = {"knowledge_source_name": name, "workspace_id": WORKSPACE, "ontology_id": ONTOLOGY}
        body = {"activity": [{"type": "fabricOntology", "knowledgeSourceName": name}],
                "references": [{"type": "fabricOntology", "workspaceId": WORKSPACE, "ontologyId": ONTOLOGY,
                                "sourceData": {"fabricRawData": "\n".join(("Name",) + deploy.SCENARIO.expected_facts)}}]}
        self.assertEqual(deploy.ontology_evidence(body, source, deploy.SCENARIO.expected_facts)[0], [])
        wrong = json.loads(json.dumps(body))
        wrong["references"][0]["ontologyId"] = LAKEHOUSE
        self.assertIn("no fabricOntology reference for the DEV workspace and ontology",
                      deploy.ontology_evidence(wrong, source, deploy.SCENARIO.expected_facts)[0])
        snapshot = {"activity": [{"type": "indexedOneLake"}], "references": [{"type": "indexedOneLake"}]}
        self.assertIn(f"no fabricOntology activity for {name}",
                      deploy.ontology_evidence(snapshot, source, deploy.SCENARIO.expected_facts)[0])
        self.assertEqual(deploy.document_evidence(snapshot, "demo-documents")[0], [])

    def test_public_context_evidence_needs_a_cited_web_iq_call(self):
        cited = {"activity": [{"type": "mcpServer", "knowledgeSourceName": "demo-web",
                               "mcpServerArguments": {"toolName": "web"}}],
                 "references": [{"type": "mcpServer", "toolName": "web", "sourceData": {"title": "Pump care"}}]}
        self.assertEqual(deploy.public_context_evidence(cited, "demo-web"), ([], {"activity": 1, "references": 1}))
        failed = {"activity": [{"type": "mcpServer", "knowledgeSourceName": "demo-web", "error": "401"}], "references": []}
        self.assertEqual(deploy.public_context_evidence(failed, "demo-web")[0],
                         ["the Web IQ tool call reported an error", "no cited Web IQ reference"])
        self.assertIn("no mcpServer activity for demo-web",
                      deploy.public_context_evidence({"activity": [{"type": "indexedOneLake"}]}, "demo-web")[0])

    def test_verify_waits_for_every_folder_source_and_asks_each_question_of_its_own_base(self):
        sources = {"documents": "demo-documents", "manuals": "demo-manuals", "unattached": "demo-unattached"}
        ready = {
            "search_endpoint": "https://srch.search.windows.net",
            "knowledge_base_mcp_endpoint": "https://srch.search.windows.net/knowledgebases/demo-knowledge/mcp?api-version=v1",
            "knowledge_base_name": "demo-knowledge", "ontology_check_knowledge_base_name": "demo-ontology-check",
            "knowledge_source_names": sources,
            "ontology_knowledge_sources": {deploy.SCENARIO.registration: {"knowledge_source_name": "demo-scenario-ontology"}},
            "public_context_knowledge_base_name": "demo-public-context", "public_context_knowledge_source_name": "web",
        }
        ctx = make_context(self, make_root(self))
        ctx.cache["outputs"] = ready
        with mock.patch.object(deploy, "wait_ingestion", return_value={"synchronizations": 1}) as ingestion, \
                mock.patch.object(deploy, "verify_web_iq", return_value="Web IQ verified"), \
                mock.patch.object(deploy, "verify_work_iq", return_value={
                    "enabled": True, "connection": "work-iq", "consent": "granted"}) as work_iq, \
                mock.patch.object(deploy, "prove_retrieval", return_value={"references": 1}) as retrieval:
            report = deploy.phase_verify(ctx)
        self.assertEqual(work_iq.call_args.args[1:], ())
        self.assertIn("Work IQ's work-iq connection is consented for WorkIQAgent.Ask; each user consents to it once",
                      report)
        self.assertEqual(ctx.state.section("verify")["work_iq"]["connection"], "work-iq")
        # Every folder source is indexed before readiness, whether or not the agents' knowledge base attaches it.
        self.assertEqual([call.args[1:] for call in ingestion.call_args_list],
                         [(f"https://srch.search.windows.net/knowledgesources/{name}/status?api-version=v1", name)
                          for _, name in sorted(sources.items())])
        ontology, document, public = (call.args[1:4] for call in retrieval.call_args_list)
        self.assertEqual(ontology[0], "https://srch.search.windows.net/knowledgebases/demo-ontology-check/retrieve?api-version=v1")
        self.assertEqual((ontology[1], ontology[2]["kind"], ontology[2]["knowledgeSourceName"]),
                         (deploy.SCENARIO.ontology_question, "fabricOntology", "demo-scenario-ontology"))
        self.assertEqual(document[0], "https://srch.search.windows.net/knowledgebases/demo-knowledge/retrieve?api-version=v1")
        self.assertEqual((document[1], document[2]["kind"], document[2]["knowledgeSourceName"]),
                         (deploy.SCENARIO.document_question, "indexedOneLake", "demo-documents"))
        # Only the agents' minimal-effort knowledge base takes the question as a search intent.
        self.assertEqual([call.kwargs.get("intents", False) for call in retrieval.call_args_list], [False, True, False])
        self.assertTrue(public[0].startswith("https://srch.search.windows.net/knowledgebases/demo-public-context/retrieve?"))
        self.assertIn("ingestion succeeded for 3 OneLake folder source(s)", report)
        self.assertIn("live ontology retrieval through demo-ontology-check and document retrieval through demo-knowledge",
                      report)
        self.assertEqual(set(ctx.state.section("verify")["ingestion"]), set(sources))
        for missing, message in (("ontology_check_knowledge_base_name", "outputs are missing"),
                                 ("knowledge_source_names", "outputs are missing"),
                                 ("knowledge_base_mcp_endpoint", "has no api-version")):
            ctx = make_context(self, make_root(self))
            ctx.cache["outputs"] = {name: value for name, value in ready.items() if name != missing}
            with self.subTest(missing=missing), mock.patch.object(deploy, "wait_ingestion") as ingestion, \
                    self.assertRaisesRegex(deploy.Blocked, message):
                deploy.phase_verify(ctx)
            ingestion.assert_not_called()
        # Without Work IQ, readiness says so instead of claiming a consented connection.
        ctx = make_context(self, make_root(self))
        ctx.cache["outputs"] = ready
        with mock.patch.object(deploy, "wait_ingestion", return_value={"synchronizations": 1}), \
                mock.patch.object(deploy, "verify_web_iq", return_value="Web IQ verified"), \
                mock.patch.object(deploy, "verify_work_iq", return_value={"enabled": False}), \
                mock.patch.object(deploy, "prove_retrieval", return_value={"references": 1}):
            report = deploy.phase_verify(ctx)
        self.assertIn("Work IQ is off (work_iq_enabled = false)", report)
        self.assertNotIn("WorkIQAgent.Ask", report)

    def test_retrieval_sends_the_user_token_for_the_live_source(self):
        root = make_root(self)
        http = FakeHttp()
        source = {"knowledge_source_name": "ks", "workspace_id": WORKSPACE, "ontology_id": ONTOLOGY}
        http.add("POST", r"/retrieve\?api-version=", reply(body={
            "activity": [{"type": "fabricOntology", "knowledgeSourceName": "ks"}],
            "references": [{"type": "fabricOntology", "workspaceId": WORKSPACE, "ontologyId": ONTOLOGY,
                            "sourceData": {"fabricRawData": ",".join(deploy.SCENARIO.expected_facts)}}]}))
        ctx = make_context(self, root, FakeShell([TOKEN_RULE]), http)
        deploy.prove_retrieval(ctx, "https://srch.search.windows.net/knowledgebases/kb/retrieve?api-version=x", "q",
                               {"kind": "fabricOntology", "knowledgeSourceName": "ks"},
                               lambda body: deploy.ontology_evidence(body, source, deploy.SCENARIO.expected_facts))
        _, _, body, headers = http.calls[0]
        self.assertEqual(body["knowledgeSourceParams"][0]["kind"], "fabricOntology")
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(headers["x-ms-query-source-authorization"], TOKEN)

    def test_retrieval_sends_an_intent_to_a_minimal_effort_base(self):
        root = make_root(self)
        http = FakeHttp()
        http.add("POST", r"/retrieve\?api-version=", reply(body={
            "activity": [{"type": "indexedOneLake", "knowledgeSourceName": "docs"}],
            "references": [{"type": "indexedOneLake"}]}))
        ctx = make_context(self, root, FakeShell([TOKEN_RULE]), http)
        evidence = deploy.prove_retrieval(
            ctx, "https://srch.search.windows.net/knowledgebases/kb/retrieve?api-version=x", "q",
            {"kind": "indexedOneLake", "knowledgeSourceName": "docs"},
            lambda body: deploy.document_evidence(body, "docs"), intents=True)
        _, _, body, _ = http.calls[0]
        self.assertEqual(body["intents"], [{"type": "semantic", "search": "q"}])
        self.assertNotIn("messages", body)
        self.assertEqual(body["knowledgeSourceParams"], [{"kind": "indexedOneLake", "knowledgeSourceName": "docs"}])
        self.assertEqual(evidence["references"], 1)

    def test_the_pat_check_sends_the_pat_only_to_github(self):
        root = make_root(self)
        http = FakeHttp()
        http.add("GET", r"^https://api\.github\.com/repos/example/demo-repo/contents/workspace\?ref=main$", reply(body=[]))
        ctx = make_context(self, root, FakeShell(), http)
        deploy.check_pat(ctx, {"github_owner": "example", "github_repository_name": "demo-repo",
                               "github_branch_name": "main", "github_directory_name": "/workspace"})
        (_, _, _, headers), = http.calls
        self.assertEqual(headers["Authorization"], "Bearer pat-value")


SEARCH_URL = ("https://management.azure.com/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Search"
              "/searchServices/srch-demo?api-version=2026-09-01-preview")
OFFERINGS = {"regions": [{"regionName": "centralus", "skus": [{"name": "basic"}, {"name": "serverless"}]},
                         {"regionName": "westeurope", "skus": [{"name": "basic"}]}]}


def search_service(principal=None, state="Succeeded", status="running"):
    body = {"properties": {"provisioningState": state, "status": status}}
    if principal:
        body["identity"] = {"type": "SystemAssigned", "principalId": principal}
    return reply(body=body)


def az_rest(fragment):
    return lambda args: args[:2] == ["az", "rest"] and any(fragment in arg for arg in args)


class SearchTests(unittest.TestCase):
    def search_context(self, http, shell_rules=(), **overrides):
        ctx = make_context(self, make_root(self), FakeShell([*shell_rules, TOKEN_RULE]), http, **overrides)
        ctx.cache["outputs"] = {"search_management_url": SEARCH_URL}
        return ctx

    def test_the_serverless_identity_is_enabled_once_and_verified_despite_http_500(self):
        http = FakeHttp()
        http.add("GET", r"/searchServices/srch-demo\?", search_service(), search_service(), search_service("p" * 8))
        http.add("PATCH", r"/searchServices/srch-demo\?", reply(500, body={"error": {"code": "Unknown"}}))
        ctx = self.search_context(http)
        self.assertEqual(deploy.ensure_search_identity(ctx), "p" * 8)
        patches = [(url, body) for method, url, body, _ in http.calls if method == "PATCH"]
        self.assertEqual(patches, [(SEARCH_URL, {"identity": {"type": "SystemAssigned"}})])
        self.assertTrue(any("HTTP 500" in message for message in ctx.messages))
        self.assertEqual(ctx.state.section("platform")["search_identity"]["principal_id"], "p" * 8)
        self.assertTrue(all(headers["Authorization"] == "Bearer " + TOKEN for *_, headers in http.calls))

    def test_an_existing_identity_is_never_patched(self):
        http = FakeHttp()
        http.add("GET", r"/searchServices/srch-demo\?", search_service("p" * 8))
        self.assertEqual(deploy.ensure_search_identity(self.search_context(http)), "p" * 8)
        self.assertEqual([method for method, *_ in http.calls], ["GET"])
        http = FakeHttp()
        http.add("GET", r"/searchServices/srch-demo\?", search_service("p" * 8, "Provisioning", "provisioning"),
                 search_service("p" * 8))
        self.assertEqual(deploy.ensure_search_identity(self.search_context(http)), "p" * 8)
        self.assertEqual([method for method, *_ in http.calls], ["GET", "GET"])

    def test_a_refused_or_unverified_identity_update_stops(self):
        http = FakeHttp()
        http.add("GET", r"/searchServices/srch-demo\?", search_service())
        http.add("PATCH", r"/searchServices/srch-demo\?", reply(403, body={"error": {"code": "AuthorizationFailed"}}))
        with self.assertRaisesRegex(deploy.Blocked, "HTTP 403 AuthorizationFailed"):
            deploy.ensure_search_identity(self.search_context(http))
        http = FakeHttp()
        http.add("GET", r"/searchServices/srch-demo\?", search_service())
        http.add("PATCH", r"/searchServices/srch-demo\?", reply(500))
        # The identity wait has its own budget, even when the phase's observation window is nearly spent.
        ctx = self.search_context(http, timeout_minutes=1)
        ctx.clock.now = 55
        with self.assertRaisesRegex(deploy.Blocked, "did not become ready with a system-assigned identity"):
            deploy.ensure_search_identity(ctx)
        self.assertEqual(len([call for call in http.calls if call[0] == "GET"]), 38)

    def test_a_failed_service_or_a_foreign_url_is_never_patched(self):
        for principal in (None, "p" * 8):
            http = FakeHttp()
            http.add("GET", r"/searchServices/srch-demo\?", search_service(principal, "Failed", "error"))
            with self.assertRaisesRegex(deploy.Blocked, "Failed/error"):
                deploy.ensure_search_identity(self.search_context(http))
            self.assertNotIn("PATCH", [method for method, *_ in http.calls])
        ctx = self.search_context(FakeHttp())
        ctx.cache["outputs"] = {"search_management_url": SEARCH_URL.replace("management.azure.com", "example.com")}
        with self.assertRaisesRegex(deploy.Blocked, "not a Search service URL"):
            deploy.ensure_search_identity(ctx)
        ctx.cache["outputs"] = {}
        self.assertIsNone(deploy.ensure_search_identity(ctx))
        with self.assertRaisesRegex(deploy.Blocked, "search_management_url output is missing"):
            deploy.ensure_search_identity(ctx, required=True)

    def test_the_platform_phase_finishes_with_the_search_identity(self):
        http = FakeHttp()
        http.add("GET", r"/searchServices/srch-demo\?", search_service("p" * 8))
        ctx = self.search_context(http, plan_rules([], plan_code=0))
        ctx.cache["variables"].update(knowledge_lakehouse_id=None)
        ctx.cache["outputs"].update(dev_workspace_id=WORKSPACE, dev_workspace_name="demo-dev",
                                    web_iq={"resource_id": "web-iq-account"})
        self.assertIn("Search identity " + "p" * 8, deploy.phase_platform(ctx))
        del ctx.cache["outputs"]["web_iq"]
        with self.assertRaisesRegex(deploy.Blocked, "Terraform outputs are missing: web_iq"):
            deploy.phase_platform(ctx)

    def test_a_knowledge_plan_creating_serverless_search_is_applied_in_two_reviewed_plans(self):
        replaced = ("azapi_resource.search", ["delete", "create"])
        grant = ("azurerm_role_assignment.search_foundry_user[0]", ["create"])
        shown = [[replaced, grant], [replaced], [grant]]

        def plan(args, options):
            path = next(arg for arg in args if arg.startswith("-out="))[5:]
            Path(path).write_bytes(" ".join(args).encode())
            return deploy.Result(2, "plan output")

        def show(args, options):
            changes = shown.pop(0)
            return deploy.Result(0, json.dumps({"resource_changes": [
                {"address": address, "change": {"actions": actions}} for address, actions in changes]}))

        outputs = deploy.Result(0, json.dumps({"search_management_url": {"value": SEARCH_URL}}))
        rules = [(terraform("plan"), plan), (terraform("show"), show), (terraform("apply"), deploy.Result(0)),
                 (terraform("console"), deploy.Result(0, json.dumps(json.dumps(
                     {"fabric_git_enabled": False, "knowledge_lakehouse_id": None})))),
                 (terraform("output"), outputs)]
        http = FakeHttp()
        http.add("GET", r"/searchServices/srch-demo\?", search_service("o" * 8), search_service(), search_service("n" * 8))
        http.add("PATCH", r"/searchServices/srch-demo\?", reply(500))
        ctx = self.search_context(http, rules, yes=True, allow_replace=True)
        ctx.initialized = True
        ctx.cache["variables"].update(search_sku="serverless")
        self.assertEqual(deploy.converge_knowledge(ctx), "applied")
        plans = ctx.shell.matching(terraform("plan"))
        self.assertEqual(["-var=search_identity_ready=false" in plan for plan in plans], [False, True, False])
        applied = [Path(args[-1]).name for args in ctx.shell.matching(terraform("apply"))]
        self.assertEqual(len(applied), 2, "the unstaged plan must never be applied")
        self.assertEqual([method for method, *_ in http.calls].count("PATCH"), 1)
        self.assertEqual(ctx.state.section("pending_plans"), {})
        ctx = self.search_context(FakeHttp(), plan_rules([replaced, grant]), yes=True, allow_replace=True)
        ctx.cache["outputs"] = {}
        ctx.cache["variables"].update(search_sku="basic")
        self.assertEqual(deploy.converge_knowledge(ctx), "applied")
        self.assertEqual(len(ctx.shell.matching(terraform("plan"))), 1, "dedicated tiers get their identity at creation")

    def test_an_unoffered_tier_stops_before_any_plan(self):
        ctx = self.search_context(FakeHttp(), [(az_rest("fetchOfferings"), deploy.Result(0, json.dumps(OFFERINGS)))])
        with self.assertRaises(deploy.Blocked) as stop:
            deploy.check_search_tier(ctx, "sub", {"location": "westeurope", "search_location": None,
                                                  "search_sku": "serverless"})
        self.assertIn("Regions offering serverless: centralus.", stop.exception.next_steps)
        self.assertEqual(deploy.check_search_tier(ctx, "sub", {"location": "centralus", "search_location": "westeurope",
                                                               "search_sku": "basic"}),
                         "Search basic offered in westeurope")

    def test_the_serverless_feature_is_registered_as_you_once_per_subscription(self):
        feature = {"state": "NotRegistered"}

        def register(args, options):
            feature["state"] = "Registered"
            return deploy.Result(0)

        rules = [(az_rest("fetchOfferings"), deploy.Result(0, json.dumps(OFFERINGS))),
                 (az_rest("features/Serverless"),
                  lambda args, options: deploy.Result(0, json.dumps({"properties": dict(feature)}))),
                 (command("az", "feature", "register"), register),
                 (command("az", "provider", "register"), deploy.Result(0))]
        values = {"location": "centralus", "search_location": None, "search_sku": "serverless"}
        ctx = self.search_context(FakeHttp(), rules, manual_grants=True)
        with self.assertRaises(deploy.Waiting) as stop:
            deploy.check_search_tier(ctx, "sub", values)
        self.assertIn("az feature register --namespace Microsoft.Search --name Serverless",
                      " ".join(stop.exception.next_steps))
        self.assertFalse(ctx.shell.matching(command("az", "feature", "register")))
        ctx = self.search_context(FakeHttp(), rules)
        self.assertIn("Microsoft.Search re-registered", deploy.check_search_tier(ctx, "sub", values))
        self.assertEqual(len(ctx.shell.matching(command("az", "feature", "register"))), 1)
        self.assertIn("--wait", ctx.shell.matching(command("az", "provider", "register"))[0])
        # The receipt keeps later runs from changing the subscription again.
        self.assertTrue(deploy.check_search_tier(ctx, "sub", values).endswith("Microsoft.Search/Serverless registered"))
        self.assertEqual(len(ctx.shell.matching(command("az", "provider", "register"))), 1)
        # Registered by someone else (or an interrupted run): the provider is re-registered once to propagate it.
        ctx = self.search_context(FakeHttp(), rules)
        self.assertIn("Microsoft.Search re-registered", deploy.check_search_tier(ctx, "sub", values))
        self.assertFalse(ctx.shell.matching(command("az", "feature", "register")))
        ctx = self.search_context(FakeHttp(), rules, manual_grants=True)
        self.assertTrue(deploy.check_search_tier(ctx, "sub", values).endswith("Microsoft.Search/Serverless registered"))
        self.assertFalse(ctx.shell.matching(command("az", "provider", "register")))

    def test_a_refused_feature_registration_names_the_owner_grant(self):
        denied = ("(AuthorizationFailed) The client 'user@example.com' with object id 'o' does not have authorization "
                  "to perform action 'Microsoft.Features/providers/Microsoft.Search/features/register/action' over "
                  "scope '/subscriptions/sub' or the scope is invalid.")
        rules = [(az_rest("features/Serverless"), deploy.Result(0, json.dumps({"properties": {"state": "NotRegistered"}}))),
                 (command("az", "feature", "register"), deploy.Result(1, "", denied))]
        ctx = self.search_context(FakeHttp(), rules)
        with self.assertRaises(deploy.Blocked) as stop:
            deploy.serverless_feature(ctx, "sub")
        grants = deploy.permission_grants(ctx, "preflight", stop.exception.message)
        self.assertEqual(len(grants), 1)
        self.assertIn("registers the required resource providers and preview features", grants[0])
        self.assertIn("az provider register --namespace Microsoft.Search --wait", " ".join(stop.exception.next_steps))
        self.assertFalse(ctx.shell.matching(command("az", "provider", "register")))


class CapacityQuotaTests(unittest.TestCase):
    SUBSCRIPTION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    RESOURCE = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-demo/providers/Microsoft.Fabric/capacities/fcdemo"

    def check(self, live, target, used=506, location="Central US", resource=None):
        capacity = {"sku": {"name": live}, "location": location}
        quota = {"value": [{"name": {"value": "CapacityQuota"}, "unit": "CU", "limit": 512, "currentValue": used}]}
        self.shell = FakeShell([(az_rest("/capacities/fcdemo?"), deploy.Result(0, json.dumps(capacity))),
                                (az_rest("/usages?"), deploy.Result(0, json.dumps(quota)))])
        ctx = make_context(self, make_root(self), self.shell)
        ctx.cache["outputs"] = {"fabric_capacity_resource_id": resource or self.RESOURCE}
        values = {"fabric_capacity_sku": target, "fabric_location": None, "location": "centralus"}
        return deploy.check_capacity_resize(ctx, self.SUBSCRIPTION, values)

    def test_a_larger_sku_needs_quota_only_for_the_added_cus(self):
        self.assertEqual(self.check("F2", "F8", used=506), "Fabric centralus: 6 CU available for F2 -> F8 (+6 CU)")
        with self.assertRaisesRegex(deploy.Blocked, "F2 -> F8 needs 6"):
            self.check("F2", "F8", used=507)

    def test_an_unchanged_or_smaller_sku_needs_no_quota(self):
        for live, target in (("F8", "F8"), ("F8", "F2")):
            with self.subTest(live=live, target=target):
                self.assertIn("no added quota needed", self.check(live, target, used=512))
                self.assertFalse(self.shell.matching(az_rest("/usages?")))

    def test_a_new_region_needs_quota_for_the_whole_sku(self):
        with self.assertRaisesRegex(deploy.Blocked, "but F8 needs 8"):
            self.check("F8", "F8", used=506, location="West Europe")

    def test_a_capacity_outside_the_subscription_is_never_read(self):
        with self.assertRaisesRegex(deploy.Blocked, "selected subscription"):
            self.check("F2", "F8", resource=self.RESOURCE.replace(self.SUBSCRIPTION, TENANT))
        self.assertFalse(self.shell.calls)


class ConsentTests(unittest.TestCase):
    AUTH = {"tenant_id": TENANT, "client_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "service_principal_object_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "login_scope": "api://bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/.default",
            "api_permissions_url": "https://portal.azure.com/#approve",
            "delegated_scopes": ["Capacity.ReadWrite.All", "Item.Execute.All", "Workspace.ReadWrite.All"]}
    RESOURCE = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    GRANTED = {"consentType": "AllPrincipals", "resourceId": RESOURCE,
               "scope": "Capacity.ReadWrite.All Item.Execute.All Workspace.ReadWrite.All"}

    def consent_context(self, http, shell_rules=(), **overrides):
        root = make_root(self)
        shell = FakeShell([*shell_rules, TOKEN_RULE,
                           (command("az", "ad", "sp", "show"), deploy.Result(0, json.dumps({"id": self.RESOURCE})))])
        ctx = make_context(self, root, shell, http, **overrides)
        ctx.cache["outputs"] = {"fabric_user_auth": self.AUTH}
        ctx.state.section("target").update(tenant_id=TENANT, subscription_id="sub", user_object_id="user")
        return ctx, shell

    @staticmethod
    def fabric_reads(http):
        for path in ("workspaces", "capacities", "connections"):
            http.add("GET", rf"/v1/{path}$", reply(body={"value": []}))

    def test_missing_consent_is_granted_as_you_for_exactly_the_declared_scopes(self):
        http = FakeHttp()
        http.add("GET", r"oauth2PermissionGrants$", reply(body={"value": []}), reply(body={"value": [self.GRANTED]}))
        http.add("POST", r"/v1\.0/oauth2PermissionGrants$", reply(201, self.GRANTED))
        self.fabric_reads(http)
        ctx, shell = self.consent_context(http)
        self.assertIn("by you just now", deploy.phase_consent(ctx))
        (_, body), = http.posts(r"oauth2PermissionGrants$")
        self.assertEqual(body, {"clientId": self.AUTH["service_principal_object_id"], "consentType": "AllPrincipals",
                                "resourceId": self.RESOURCE, "scope": self.GRANTED["scope"]})
        self.assertFalse(shell.matching(command("az", "ad", "app", "permission")))
        receipt = ctx.state.section("consent")
        self.assertEqual(receipt["login_scope"], self.AUTH["login_scope"])
        self.assertIn("granted_at", receipt)
        self.assertIn("verified_at", receipt)

    def test_a_refused_grant_hands_over_the_administrator_link(self):
        http = FakeHttp()
        http.add("GET", r"oauth2PermissionGrants$", reply(body={"value": []}))
        http.add("POST", r"/v1\.0/oauth2PermissionGrants$", reply(403, {"error": {
            "code": "Authorization_RequestDenied", "message": "Insufficient privileges to complete the operation."}}))
        ctx, _ = self.consent_context(http)
        with self.assertRaises(deploy.MissingPermission) as stop:
            deploy.phase_consent(ctx)
        self.assertEqual(stop.exception.code, deploy.WAITING)
        self.assertIn("Authorization_RequestDenied", stop.exception.message)
        self.assertIn("https://portal.azure.com/#approve", " ".join(stop.exception.grants))
        self.assertEqual(stop.exception.next_steps[0], stop.exception.grants[0])
        self.assertFalse([call for call in http.calls if "/v1/workspaces" in call[1]])

    def test_a_partial_grant_gains_only_the_missing_declared_scopes(self):
        http = FakeHttp()
        partial = {"id": "grant/1", "consentType": "AllPrincipals", "resourceId": self.RESOURCE,
                   "scope": "Capacity.ReadWrite.All Other.Scope"}
        complete = dict(partial, scope="Capacity.ReadWrite.All Item.Execute.All Other.Scope Workspace.ReadWrite.All")
        http.add("GET", r"oauth2PermissionGrants$", reply(body={"value": [partial]}), reply(body={"value": [complete]}))
        http.add("PATCH", r"/v1\.0/oauth2PermissionGrants/grant%2F1$", reply(204))
        self.fabric_reads(http)
        ctx, _ = self.consent_context(http)
        deploy.phase_consent(ctx)
        (_, _, body, _), = [call for call in http.calls if call[0] == "PATCH"]
        self.assertEqual(body, {"scope": complete["scope"]})
        self.assertFalse(http.posts(r"oauth2PermissionGrants"))

    def test_manual_grants_leave_consent_to_an_administrator(self):
        http = FakeHttp()
        http.add("GET", r"oauth2PermissionGrants$", reply(body={"value": []}))
        ctx, _ = self.consent_context(http, manual_grants=True)
        with self.assertRaises(deploy.Waiting) as stop:
            deploy.phase_consent(ctx)
        self.assertIn("https://portal.azure.com/#approve", " ".join(stop.exception.next_steps))
        self.assertEqual([call[0] for call in http.calls], ["GET"])

    def test_unreadable_grants_wait_for_an_explicit_confirmation(self):
        http = FakeHttp()
        http.add("GET", r"oauth2PermissionGrants$", reply(403, {"error": {"code": "Authorization_RequestDenied"}}))
        self.fabric_reads(http)
        ctx, _ = self.consent_context(http)
        with self.assertRaises(deploy.MissingPermission) as stop:
            deploy.phase_consent(ctx)
        self.assertIn("--consent-confirmed", " ".join(stop.exception.next_steps))
        self.assertIn("https://portal.azure.com/#approve", " ".join(stop.exception.grants))
        self.assertFalse(http.posts(r"oauth2PermissionGrants"))
        self.assertFalse([call for call in http.calls if "/v1/workspaces" in call[1]])
        ctx.args.consent_confirmed = True
        self.assertIn("confirmed by an administrator", deploy.phase_consent(ctx))

    def test_only_a_fresh_grant_is_given_time_to_reach_fabric(self):
        http = FakeHttp()
        http.add("GET", r"oauth2PermissionGrants$", reply(body={"value": []}), reply(body={"value": [self.GRANTED]}))
        http.add("POST", r"/v1\.0/oauth2PermissionGrants$", reply(201, self.GRANTED))
        http.add("GET", r"/v1/workspaces$", reply(403), reply(403), reply(body={"value": []}))
        http.add("GET", r"/v1/capacities$", reply(body={"value": []}))
        ctx, _ = self.consent_context(http)
        deploy.phase_consent(ctx)
        self.assertEqual(len([call for call in http.calls if call[1].endswith("/v1/workspaces")]), 3)
        http = FakeHttp()
        http.add("GET", r"oauth2PermissionGrants$", reply(body={"value": [self.GRANTED]}))
        http.add("GET", r"/v1/workspaces$", reply(403))
        ctx, _ = self.consent_context(http)
        with self.assertRaisesRegex(deploy.Waiting, "Fabric refused /v1/workspaces"):
            deploy.phase_consent(ctx)
        self.assertEqual(len([call for call in http.calls if call[1].endswith("/v1/workspaces")]), 1)

    APP = {"client_id": "abababab-abab-4bab-8bab-abababababab", "tenant_id": TENANT,
           "service_principal_object_id": "acacacac-acac-4cac-8cac-acacacacacac",
           "api_permissions_url": "https://portal.azure.com/#app"}

    def app_grants(self, http, *responses):
        """The Fabric registration's grant exists; the app registration's grants answer with `responses`."""
        http.add("GET", rf"servicePrincipals/{self.AUTH['service_principal_object_id']}/oauth2PermissionGrants$",
                 reply(body={"value": [self.GRANTED]}))
        http.add("GET", rf"servicePrincipals/{self.APP['service_principal_object_id']}/oauth2PermissionGrants$",
                 *responses)
        self.fabric_reads(http)

    def test_the_app_sign_in_scope_is_granted_once_the_platform_created_its_registration(self):
        http = FakeHttp()
        self.app_grants(http)
        ctx, _ = self.consent_context(http)
        # Before the platform apply there is no app registration yet: the app phase grants it then.
        self.assertNotIn("sign-in scope", deploy.phase_consent(ctx))
        self.assertFalse(http.posts(r"oauth2PermissionGrants"))
        granted = {"consentType": "AllPrincipals", "resourceId": self.RESOURCE, "scope": "user_impersonation"}
        http = FakeHttp()
        self.app_grants(http, reply(body={"value": []}), reply(body={"value": [granted]}))
        http.add("POST", r"/v1\.0/oauth2PermissionGrants$", reply(201, granted))
        ctx, shell = self.consent_context(http)
        ctx.cache["outputs"]["app"] = self.APP
        self.assertIn("the app's Microsoft Foundry sign-in scope is granted tenant-wide by you just now",
                      deploy.phase_consent(ctx))
        (_, body), = http.posts(r"oauth2PermissionGrants$")
        self.assertEqual(body, {"clientId": self.APP["service_principal_object_id"], "consentType": "AllPrincipals",
                                "resourceId": self.RESOURCE, "scope": "user_impersonation"})
        # The resource is the Microsoft Foundry API principal behind https://ai.azure.com.
        self.assertIn(deploy.FOUNDRY_API_APP_ID, shell.matching(command("az", "ad", "sp", "show"))[-1])

    def test_manual_grants_leave_the_app_sign_in_scope_to_each_user_or_an_administrator(self):
        http = FakeHttp()
        self.app_grants(http, reply(body={"value": []}))
        ctx, _ = self.consent_context(http, manual_grants=True)
        ctx.cache["outputs"]["app"] = self.APP
        self.assertIn("each user consents at first sign-in", deploy.phase_consent(ctx))
        self.assertFalse(http.posts(r"oauth2PermissionGrants"))
        self.assertTrue(any("https://portal.azure.com/#app" in message for message in ctx.messages))


ACCOUNT = {"id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "tenantId": TENANT, "user": {"type": "user"}}
REPOSITORY_RULES = [
    (command("gh", "auth", "status"), deploy.Result(0)),
    (command("git", "remote", "get-url", "origin"), deploy.Result(0, "https://github.com/example/demo-repo.git\n")),
    (command("gh", "repo", "view"), deploy.Result(0, json.dumps({"nameWithOwner": "example/demo-repo",
                                                                 "isTemplate": False}))),
]
SIGNED_IN = [
    (command("az", "account", "show"), deploy.Result(0, json.dumps(ACCOUNT))),
    TOKEN_RULE,
]


def signing_in(args):
    return args[:2] == ["az", "login"] or args[:3] == ["gh", "auth", "login"]


def requested_audience(args):
    return args[args.index("--resource" if "--resource" in args else "--scope") + 1]


class SignInTests(unittest.TestCase):
    SUBSCRIPTION = ACCOUNT["id"]

    def context(self, rules, phases=deploy.PHASES, **overrides):
        overrides.setdefault("subscription", self.SUBSCRIPTION)
        ctx = make_context(self, make_root(self), FakeShell([*rules, *REPOSITORY_RULES]), **overrides)
        ctx.phases = phases
        return ctx

    def test_a_signed_in_host_starts_without_sign_ins_after_checking_every_audience(self):
        ctx = self.context(SIGNED_IN)
        ctx.state.section("consent")["login_scope"] = "api://client/.default"
        summary = deploy.phase_signin(ctx)
        self.assertFalse(ctx.shell.matching(signing_in))
        probes = ctx.shell.matching(command("az", "account", "get-access-token"))
        self.assertEqual({requested_audience(call) for call in probes},
                         {deploy.ARM_API + "/", deploy.GRAPH_API, deploy.FABRIC_API, "api://client/.default",
                          deploy.SEARCH_RESOURCE, deploy.FOUNDRY_SCOPE})
        self.assertTrue(all(call[call.index("--tenant") + 1] == TENANT for call in probes))
        for expected in ("GitHub CLI for example/demo-repo", f"Azure CLI in tenant {TENANT}", "Microsoft Foundry"):
            self.assertIn(expected, summary)
        # The agents and app need no other CLI than GitHub's and Azure's.
        self.assertEqual({call[0] for call in ctx.shell.calls}, {"gh", "git", "az"})

    def test_a_missing_sign_in_runs_with_its_code_only_on_stderr(self):
        signed, seen = set(), {}

        def account(args, options):
            return deploy.Result(0, json.dumps(ACCOUNT)) if signed else deploy.Result(1, "", "not signed in")

        def login(args, options):
            signed.add("az")
            seen.update(options)
            return deploy.Result(0)

        ctx = self.context([(command("az", "account", "show"), account), (command("az", "login"), login),
                            (command("az", "account", "set"), deploy.Result(0)), TOKEN_RULE])
        deploy.phase_signin(ctx)
        self.assertEqual(ctx.shell.matching(lambda args: signing_in(args) or args[:3] == ["az", "account", "set"]), [
            ["az", "login", "--use-device-code"],
            ["az", "account", "set", "--subscription", self.SUBSCRIPTION],
        ])
        self.assertTrue(seen["stream"] and not seen["interactive"])
        self.assertEqual(seen["env"]["AZURE_CORE_LOGIN_EXPERIENCE_V2"], "off")
        self.assertTrue(any(message.startswith("Sign-in 1 of 1: Azure CLI as the deploying user") for message in ctx.messages))

    def test_declining_lists_every_missing_sign_in_at_once(self):
        def token(args, options):
            failing = requested_audience(args) in (deploy.FABRIC_API, deploy.FOUNDRY_SCOPE)
            return deploy.Result(1, "", "AADSTS50076") if failing else TOKEN_RULE[1]

        ctx = self.context([(command("az", "account", "show"), deploy.Result(0, json.dumps(ACCOUNT))),
                            (command("az", "account", "get-access-token"), token)])
        ctx.interactive, ctx.prompt = True, lambda text: "n\n"
        with self.assertRaises(deploy.Waiting) as stop:
            deploy.phase_signin(ctx)
        steps = " ".join(stop.exception.next_steps)
        for scope in (deploy.FABRIC_API + "/.default", deploy.FOUNDRY_SCOPE):
            self.assertIn(f"Run: az login --use-device-code --tenant {TENANT} --scope {scope}", steps)
        self.assertIn(f"Then: az account set --subscription {self.SUBSCRIPTION}", steps)
        self.assertFalse(ctx.shell.matching(signing_in))

    def test_an_unfinished_device_code_sign_in_waits_with_its_command(self):
        ctx = self.context([(command("az", "account", "show"), deploy.Result(1)),
                            (command("az", "login"), deploy.Result(1, "", "code expired"))], phases=("signin", "config"))
        with self.assertRaisesRegex(deploy.Waiting, "Sign-in did not complete") as stop:
            deploy.phase_signin(ctx)
        self.assertIn("Run: az login --use-device-code", stop.exception.next_steps)

    def test_github_cli_is_checked_before_any_other_sign_in(self):
        ctx = make_context(self, make_root(self), FakeShell([(command("gh", "auth", "status"), deploy.Result(1))]))
        with self.assertRaises(deploy.Waiting) as stop:
            deploy.phase_signin(ctx)
        self.assertIn("Run: gh auth login --hostname github.com --git-protocol https --web", stop.exception.next_steps)
        self.assertEqual(ctx.shell.calls, [["gh", "auth", "status", "--hostname", "github.com"]])

    def test_foundry_is_signed_in_to_only_when_the_agent_phase_runs(self):
        for through, expected in (("verify", False), ("agent", True)):
            with self.subTest(through=through):
                ctx = self.context(SIGNED_IN, deploy.PHASES[:deploy.PHASES.index(through) + 1])
                self.assertEqual(("Microsoft Foundry", {"scope": deploy.FOUNDRY_SCOPE}) in deploy.token_audiences(ctx),
                                 expected)

    def test_only_the_token_audiences_that_fail_are_signed_in_again(self):
        def token(args, options):
            fabric = requested_audience(args) == deploy.FABRIC_API
            return deploy.Result(1, "", "AADSTS50076") if fabric else TOKEN_RULE[1]

        account = (command("az", "account", "show"), deploy.Result(0, json.dumps(ACCOUNT)))
        ctx = self.context([account, (command("az", "account", "get-access-token"), token)])
        pending, _ = deploy.missing_sign_ins(ctx)
        self.assertEqual([(command, after) for _, command, after in pending], [(
            ["az", "login", "--use-device-code", "--tenant", TENANT, "--scope", deploy.FABRIC_API + "/.default"],
            (["az", "account", "set", "--subscription", self.SUBSCRIPTION],))])
        # Without a Resource Manager token the whole sign-in expired: one plain login, not one per audience.
        ctx = self.context([account, (command("az", "account", "get-access-token"), deploy.Result(1))])
        pending, _ = deploy.missing_sign_ins(ctx)
        self.assertEqual([command for _, command, _ in pending], [["az", "login", "--use-device-code", "--tenant", TENANT]])

    def test_a_service_principal_or_another_tenant_is_not_the_deploying_user(self):
        principal = dict(ACCOUNT, user={"type": "servicePrincipal"})
        ctx = self.context([(command("az", "account", "show"), deploy.Result(0, json.dumps(principal)))])
        with self.assertRaisesRegex(deploy.Blocked, "service principal"):
            deploy.azure_sign_in(ctx)
        other = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        ctx = self.context([(command("az", "account", "show"), deploy.Result(0, json.dumps(ACCOUNT)))], tenant=other)
        self.assertEqual(deploy.azure_sign_in(ctx)[2], ["az", "login", "--use-device-code", "--tenant", other])

    def test_teardown_signs_in_only_for_what_it_uses(self):
        ctx = self.context([], ("signin", "destroy"))
        # Teardown deletes the registration behind the deployment scope, so a resumed `down` never requires it.
        ctx.state.section("consent")["login_scope"] = "api://client/.default"
        self.assertEqual([label for label, _ in deploy.token_audiences(ctx)],
                         ["Azure Resource Manager", "Microsoft Graph", "Fabric"])
        ctx.phases = deploy.PHASES
        self.assertIn("the Fabric deployment scope", [label for label, _ in deploy.token_audiences(ctx)])

    def test_the_last_sign_in_round_is_checked_before_giving_up(self):
        logins = []

        def account(args, options):
            return deploy.Result(0, json.dumps(ACCOUNT)) if len(logins) == 3 else deploy.Result(1, "", "not signed in")

        ctx = self.context([(command("az", "account", "show"), account), TOKEN_RULE,
                            (command("az", "login"), lambda args, options: logins.append(args) or deploy.Result(0)),
                            (command("az", "account", "set"), deploy.Result(0))])
        self.assertIn(f"Azure CLI in tenant {TENANT}", deploy.phase_signin(ctx))
        self.assertEqual(len(logins), 3)


class PermissionTests(unittest.TestCase):
    def context(self):
        ctx = make_context(self, make_root(self))
        ctx.state.section("target")["subscription_id"] = "sub-1"
        ctx.state.section("consent")["api_permissions_url"] = "https://portal.azure.com/#approve"
        return ctx

    def test_each_refusal_names_its_own_grant_and_other_failures_name_none(self):
        ctx = self.context()
        arm = ("Status=403 Code=\"AuthorizationFailed\" Message=\"The client 'u' with object id 'o' does not have "
               "authorization to perform action '{}' over scope '/subscriptions/sub-1/resourceGroups/rg'\"")
        cases = (
            ("platform", "│ Error: creating role assignment\n│ " + arm.format("Microsoft.Authorization/roleAssignments/write"),
             ("Role Based Access Control Administrator", "sub-1")),
            ("platform", "│ Error: creating Resource Group\n│ " + arm.format("Microsoft.Resources/subscriptions/resourcegroups/write"),
             ("Contributor (or Owner) on subscription sub-1",)),
            ("identity", "│ Error: Could not create application\n│ unexpected status 403 with OData error: "
                         "Authorization_RequestDenied: Insufficient privileges to complete the operation.",
             ("Application Developer",)),
            ("platform", "│ Error: RequestDisallowedByPolicy: Resource 'x' was disallowed by policy.",
             ("Azure Policy administrator",)),
            ("bootstrap", "Bulk Import with the Bootstrap plan failed with HTTP 403 InsufficientScopes: missing scope",
             ("https://portal.azure.com/#approve",)),
            ("bootstrap", "Listing DEV items failed with HTTP 403 Unauthorized: denied", ("fabric_capacity_admins",)),
            ("knowledge", "│ Error: Create operation\n│   with fabric_workspace_role_assignment.search_onelake_contributor,\n"
                          "│ StatusCode: 403 Forbidden", ("fabric_capacity_admins",)),
            ("config", "terraform init failed; nothing was planned.\nStatus=403 Code=\"AuthorizationPermissionMismatch\"",
             ("Storage Blob Data Contributor",)),
            ("app", "az acr build failed (exit 1).\n(AuthorizationFailed) The client 'u' with object id 'o' does not have "
                    "authorization to perform action 'Microsoft.ContainerRegistry/registries/scheduleRun/action' over "
                    "scope '/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.ContainerRegistry/registries/cr'",
             ("Contributor (or Owner) on subscription sub-1",)),
        )
        for phase, text, fragments in cases:
            with self.subTest(phase=phase, text=text[:60]):
                grants = deploy.permission_grants(ctx, phase, text)
                self.assertEqual(len(grants), 1, grants)
                for fragment in fragments:
                    self.assertIn(fragment, grants[0])
        for phase, text in (
            ("bootstrap", "Bulk Import with the Bootstrap plan failed with HTTP 403 FeatureNotAvailable: no plans"),
            ("verify", "fabricOntology retrieval is not verified: Retrieval from scenario failed with HTTP 403"),
            ("platform", "terraform apply failed during platform; state is preserved.\n│ Error: quota exceeded"),
            ("app", "az acr build failed (exit 1).\nnpm error 403 403 Forbidden - GET https://registry.npmjs.org/react"),
            # The platform plan grants Foundry User; a refusal right after it is propagation, named by its own step.
            ("agent", "Creating agent demo-with-iq failed with HTTP 403 PermissionDenied: denied"),
        ):
            with self.subTest(phase=phase, text=text[:60]):
                self.assertEqual(deploy.permission_grants(ctx, phase, text), [])

    def test_a_refusal_waits_for_its_grant_and_reaches_the_report(self):
        refusal = deploy.Blocked(
            "terraform apply failed during platform; state is preserved.",
            "Investigate the error above, then rerun: a fresh plan is created from the current state.",
            detail="│ Error: creating role assignment\n│ AuthorizationFailed: does not have authorization to perform "
                   "action 'Microsoft.Authorization/roleAssignments/write' over scope 'x'")
        with mock.patch.dict(deploy.PHASE_FUNCTIONS, {"platform": mock.Mock(side_effect=refusal)}):
            code, report = deploy.run_phases(self.context(), ("platform",))
        self.assertEqual((code, report["result"]), (deploy.WAITING, "waiting"))
        self.assertTrue(report["message"].startswith("Missing permission: terraform apply failed"))
        self.assertIn("Role Based Access Control Administrator", report["grants"][0])
        self.assertEqual(report["next"][0], report["grants"][0])
        self.assertIn("Investigate the error above, then rerun: a fresh plan is created from the current state.",
                      report["next"])
        with mock.patch.dict(deploy.PHASE_FUNCTIONS, {"platform": mock.Mock(side_effect=deploy.Blocked("quota"))}):
            code, report = deploy.run_phases(self.context(), ("platform",))
        self.assertEqual(code, deploy.BLOCKED)
        self.assertNotIn("grants", report)


class WorkIqTests(unittest.TestCase):
    REGISTRATION = {"enabled": True, "client_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "tenant_id": TENANT,
                    "service_principal_object_id": "ffffffff-ffff-4fff-8fff-ffffffffffff",
                    "connection_name": "work-iq", "mcp_endpoint": "https://workiq.svc.cloud.microsoft/mcp",
                    "oauth_redirect_url": "https://global.consent.azure-apim.net/redirect/abc",
                    "delegated_scopes": ["WorkIQAgent.Ask"], "api_permissions_url": "https://portal.azure.com/#work-iq"}
    RESOURCE = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    GRANTED = {"consentType": "AllPrincipals", "resourceId": RESOURCE, "scope": "WorkIQAgent.Ask"}

    def context(self, http, registration=None, **overrides):
        shell = FakeShell([TOKEN_RULE, (command("az", "ad", "sp", "show"), deploy.Result(0, json.dumps({"id": self.RESOURCE})))])
        ctx = make_context(self, make_root(self), shell, http, **overrides)
        ctx.cache["outputs"] = {"work_iq": dict(registration or self.REGISTRATION)}
        ctx.state.section("target").update(tenant_id=TENANT, subscription_id="sub", user_object_id="user")
        return ctx, shell

    def test_missing_consent_is_granted_as_you_for_only_work_iq_ask_without_a_billable_call(self):
        http = FakeHttp()
        http.add("GET", r"oauth2PermissionGrants$", reply(body={"value": []}), reply(body={"value": [self.GRANTED]}))
        http.add("POST", r"/v1\.0/oauth2PermissionGrants$", reply(201, self.GRANTED))
        ctx, shell = self.context(http)
        self.assertEqual(deploy.verify_work_iq(ctx), {"enabled": True, "connection": "work-iq", "consent": "granted"})
        (_, body), = http.posts(r"oauth2PermissionGrants$")
        self.assertEqual(body, {"clientId": self.REGISTRATION["service_principal_object_id"], "consentType": "AllPrincipals",
                                "resourceId": self.RESOURCE, "scope": "WorkIQAgent.Ask"})
        self.assertIn(deploy.WORK_IQ_APP_ID, shell.matching(command("az", "ad", "sp", "show"))[0])
        # Each user's own consent proves the route; the runner never calls Work IQ, which bills every call.
        self.assertFalse([entry for entry in http.calls if "workiq" in entry[1]])

    def test_manual_grants_leave_work_iq_consent_to_an_administrator(self):
        http = FakeHttp()
        http.add("GET", r"oauth2PermissionGrants$", reply(body={"value": []}))
        ctx, _ = self.context(http, manual_grants=True)
        with self.assertRaises(deploy.Waiting) as stop:
            deploy.verify_work_iq(ctx)
        steps = " ".join(stop.exception.next_steps)
        self.assertIn("https://portal.azure.com/#work-iq", steps)
        self.assertIn("WorkIQAgent.Ask", steps)
        self.assertFalse(http.posts(r"oauth2PermissionGrants"))

    def test_a_disabled_work_iq_is_skipped_without_any_call(self):
        http = FakeHttp()
        ctx, shell = self.context(http, registration={"enabled": False})
        self.assertEqual(deploy.verify_work_iq(ctx), {"enabled": False})
        self.assertFalse(http.calls)
        self.assertFalse(shell.calls)

    def test_the_route_needs_the_oauth_connection_and_the_deployment_tenant(self):
        for change, message in (({"oauth_redirect_url": None}, "no Work IQ connection"),
                                ({"mcp_endpoint": "https://example.com/mcp"}, "no Work IQ connection"),
                                ({"tenant_id": "99999999-9999-4999-8999-999999999999"}, "tenant differs")):
            with self.subTest(change=change):
                http = FakeHttp()
                ctx, _ = self.context(http, registration={**self.REGISTRATION, **change})
                with self.assertRaisesRegex(deploy.Blocked, message):
                    deploy.verify_work_iq(ctx)
                self.assertFalse(http.calls)


class StateAndCliTests(unittest.TestCase):
    def test_console_variables_ignore_remote_backend_lock_notices(self):
        expected = {"project_name": "demo", "fabric_git_enabled": False}
        stdout = "\n".join((
            "Acquiring state lock. This may take a few moments...",
            json.dumps(json.dumps(expected)),
            "Releasing state lock. This may take a few moments...",
        ))
        shell = FakeShell([(terraform("console"), deploy.Result(0, stdout))])
        ctx = make_context(self, make_root(self), shell)
        ctx.initialized = True
        ctx.cache.pop("variables")
        self.assertEqual(deploy.variables(ctx), expected)

    def test_console_variables_still_reject_invalid_or_failed_evaluations(self):
        notice = "Releasing state lock. This may take a few moments..."
        for result in (
            deploy.Result(0, notice),
            deploy.Result(0, '"not JSON"\n' + notice),
            deploy.Result(1, json.dumps(json.dumps({"project_name": "demo"})), "evaluation failed"),
        ):
            with self.subTest(result=result):
                shell = FakeShell([(terraform("console"), result)])
                ctx = make_context(self, make_root(self), shell)
                ctx.initialized = True
                ctx.cache.pop("variables")
                with self.assertRaises(deploy.Blocked):
                    deploy.variables(ctx)
                self.assertNotIn("variables", ctx.cache)

    def test_native_commands_resolve_windows_launchers_in_every_mode(self):
        launcher = r"C:\Program Files\Azure\az.cmd"
        shell = deploy.Shell()
        for options in ({}, {"interactive": True}, {"stream": True}):
            with self.subTest(options=options), \
                    mock.patch.object(shell, "which", return_value=launcher), \
                    mock.patch.object(deploy.subprocess, "run") as run, \
                    mock.patch.object(deploy.subprocess, "Popen") as popen:
                run.return_value.returncode = 0
                popen.return_value.stdout = []
                popen.return_value.wait.return_value = 0
                shell.run(["az", "version"], **options)
                native = popen if options.get("stream") else run
                self.assertEqual(native.call_args.args[0], [launcher, "version"])

    def test_one_time_codes_never_reach_reports(self):
        github = "First copy your one-time code:\n\n      AB12-CD34\n\nThen visit https://github.com/login/device"
        entra = "To sign in, open https://microsoft.com/devicelogin and enter the code FV3ABC9XY to authenticate."
        for text, code in ((github, "AB12-CD34"), (entra, "FV3ABC9XY")):
            redacted = deploy.tail(text)
            self.assertNotIn(code, redacted)
            self.assertIn("<one-time code redacted>", redacted)
        self.assertIn("11111111-1111-4111-8111-111111111111", deploy.tail(WORKSPACE))

    def test_receipts_are_private_and_targets_cannot_drift(self):
        root = make_root(self)
        ctx = make_context(self, root)
        deploy.choose(ctx, "subscription", "first")
        self.assertEqual(stat.S_IMODE(os.stat(ctx.state.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(ctx.state.directory).st_mode), 0o700)
        self.assertEqual(deploy.choose(ctx, "subscription", None), "first")
        with self.assertRaisesRegex(deploy.Blocked, "conflicts"):
            deploy.choose(ctx, "subscription", "second")

    def test_status_reads_receipts_and_the_template_is_refused(self):
        root = make_root(self)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(deploy.main(["status", "--json"], root=root), deploy.OK)
        self.assertEqual(json.loads(stdout.getvalue())["phases"]["config"]["status"], "not run")
        shell = FakeShell([
            (command("gh", "auth", "status"), deploy.Result(0)),
            (command("git", "remote", "get-url", "origin"), deploy.Result(0, "https://github.com/example/template.git\n")),
            (command("gh", "repo", "view"), deploy.Result(0, json.dumps({"nameWithOwner": "example/template", "isTemplate": True}))),
        ])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            code = deploy.main(["up", "--json"], root=root, shell=shell, http=FakeHttp(), interactive=False)
        self.assertEqual(code, deploy.BLOCKED)
        report = json.loads(stdout.getvalue())
        self.assertIn("reusable template", report["message"])
        # Refused in the first phase, before any Azure sign-in.
        self.assertEqual(report["phase"], "signin")
        self.assertFalse(shell.matching(command("az")))

    def test_admin_actions_are_the_default_and_only_manual_grants_opt_out(self):
        self.assertEqual(deploy.PHASES[0], "signin")
        self.assertTrue(deploy.parse_arguments(["up", "--manual-grants"]).manual_grants)
        self.assertFalse(deploy.parse_arguments(["up"]).manual_grants)
        for retired in ("--grant-admin-consent", "--register-search-serverless"):
            with self.assertRaises(SystemExit) as stop, contextlib.redirect_stderr(io.StringIO()):
                deploy.parse_arguments(["up", retired])
            self.assertEqual(stop.exception.code, deploy.USAGE)
        done = (deploy.OK, {"result": "done", "phases": {}})
        with mock.patch.object(deploy, "run_phases", return_value=done) as runner, \
                contextlib.redirect_stdout(io.StringIO()):
            deploy.main(["down"], root=make_root(self), shell=FakeShell(), http=FakeHttp(), interactive=False)
        self.assertEqual(runner.call_args.args[1], ("signin", "destroy"))

    def test_there_is_no_single_phase_entry_that_skips_earlier_gates(self):
        # Nor the derived project's retire-sample and demo commands.
        for argv in (["run", "platform"], ["retire-sample"], ["demo", "check"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as stop, \
                    contextlib.redirect_stderr(io.StringIO()):
                deploy.parse_arguments(argv)
            self.assertEqual(stop.exception.code, deploy.USAGE)

    def test_plan_approvals_need_a_hash_and_actions_take_none(self):
        self.assertEqual(deploy.parse_arguments(["up", "--approve", "platform=ABCDEF012345"]).approve,
                         {"platform": "abcdef012345"})
        self.assertEqual(deploy.parse_arguments(["up", "--approve", "agent-publish"]).approve, {"agent-publish": True})
        self.assertEqual(deploy.parse_arguments(["up", "--approve", "app=ABCDEF012345"]).approve, {"app": "abcdef012345"})
        self.assertEqual(deploy.parse_arguments(["up", "--approve", "item-update=ABCDEF012345"]).approve,
                         {"item-update": "abcdef012345"})
        # Earlier agent and app routes' approvals are gone, and so is sample retirement.
        for value in ("platform", "platform=xyz", "agent-publish=1", "unknown", "item-update", "app", "agent-create",
                      "agent-connections", "app-register", "runtime-delete", "agent-deploy", "app-deploy",
                      "retire-sample=abcdef012345"):
            with self.subTest(value=value), self.assertRaises(SystemExit) as stop, \
                    contextlib.redirect_stderr(io.StringIO()):
                deploy.parse_arguments(["up", "--approve", value])
            self.assertEqual(stop.exception.code, deploy.USAGE)
        self.assertEqual(set(deploy.APPROVAL_KEYS), {"identity", "platform", "knowledge", "app", "destroy", "item-update",
                                                     "knowledge-registration", "agent-publish", "app-build",
                                                     "purge-foundry", "shared-action-group"})
        self.assertEqual(set(deploy.DESTRUCTIVE_APPROVALS), {"destroy", "purge-foundry", "item-update",
                                                             "shared-action-group"})


class FakeFoundry(FakeHttp):
    """Foundry Agent Service's agents API over an in-memory project; other routes behave as in FakeHttp."""

    def __init__(self, endpoint, agents=None):
        super().__init__()
        self.endpoint = endpoint
        self.agents = dict(agents or {})
        self.publish_errors = []

    def request(self, method, url, headers=None, body=None, timeout=120, attempts=None):
        if not url.startswith(self.endpoint + "/agents"):
            return super().request(method, url, headers, body, timeout, attempts)
        self.calls.append((method, url, body, dict(headers or {})))
        self.attempts.append(attempts)
        path, _, query = url[len(self.endpoint):].partition("?")
        assert query == "api-version=v1", url
        parts = path.strip("/").split("/")
        if method == "GET" and len(parts) == 2:
            agent = self.agents.get(parts[1])
            return reply(404, {"error": {"code": "not_found"}}) if agent is None else reply(body=agent)
        if method == "POST" and self.publish_errors:
            return self.publish_errors.pop(0)
        if method == "POST" and parts == ["agents"]:
            return reply(body=self.store(body["name"], body))
        if method == "POST" and len(parts) == 3 and parts[2] == "versions":
            return reply(body=self.store(parts[1], body)["versions"]["latest"])
        raise AssertionError(f"unexpected request: {method} {url}")

    def store(self, name, body):
        previous = self.agents.get(name)
        version = str(int(previous["versions"]["latest"]["version"]) + 1) if previous else "1"
        latest = {"object": "agent.version", "name": name, "version": version, "description": body["description"],
                  "metadata": body["metadata"], "definition": body["definition"]}
        self.agents[name] = {"object": "agent", "id": name, "name": name, "versions": {"latest": latest}}
        return self.agents[name]


class AgentAndAppTests(unittest.TestCase):
    ENDPOINT = "https://ai-demo.services.ai.azure.com/api/projects/demo"
    SEARCH = "https://srch.search.windows.net"
    AGENTS = {"with_iq": ("demo-with-iq", "Scenario assistant – with IQ"),
              "without_iq": ("demo-without-iq", "Scenario assistant – without IQ")}
    WITH_IQ_TOOLS = ["fabric_iq_ontology", "foundry_iq_knowledge", "web_iq_public_context", "work_iq_mail"]
    REGISTRY = "crdemoab12"
    APP_URL = "https://ca-demo.blue-sea-123.swedencentral.azurecontainerapps.io"
    CLIENT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    APP_PRINCIPAL = "acacacac-acac-4cac-8cac-acacacacacac"
    FOUNDRY_PRINCIPAL = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    LISTING = ("100644 blob 1111111111111111111111111111111111111111\tapp/Dockerfile\n"
               "100644 blob 2222222222222222222222222222222222222222\tapp/server/main.ts\n")

    def outputs(self, reasoning="low", work_iq=True):
        mail = {"name": "work-iq", "server_url": "https://workiq.svc.cloud.microsoft/mcp"}
        return {
            "foundry_project_endpoint": self.ENDPOINT,
            "foundry_agents": {key: {"name": name, "display_name": display, "source": deploy.foundry.VARIANTS[key]}
                               for key, (name, display) in self.AGENTS.items()},
            "agent_settings": {"model": "gpt-5.4-mini", "model_deployment": "agent", "reasoning_effort": reasoning},
            "foundry_tool_connections": {
                "knowledge_base": {"name": "demo-knowledge-mcp",
                                   "server_url": f"{self.SEARCH}/knowledgebases/demo-knowledge/mcp?api-version=v1"},
                "public_context": {"name": "public-context-mcp",
                                   "server_url": f"{self.SEARCH}/knowledgebases/demo-public-context/mcp?api-version=v1"},
                "work_iq_mail": mail if work_iq else None},
            "fabric_iq_connections": {deploy.SCENARIO.registration: {
                "name": "fabric-iq-scenario", "server_url": f"https://api.fabric.microsoft.com/v1/mcp/dataPlane/"
                                                            f"workspaces/{WORKSPACE}/items/{ONTOLOGY}/ontologyEndpoint"}},
            "work_iq": {"enabled": True, "connection_name": "work-iq"} if work_iq else {"enabled": False},
            "container_registry": {"name": self.REGISTRY, "login_server": f"{self.REGISTRY}.azurecr.io",
                                   "repository": "app"},
            "app": {"url": None, "name": "ca-demo", "image_tag": None, "client_id": self.CLIENT, "tenant_id": TENANT,
                    "service_principal_object_id": self.APP_PRINCIPAL,
                    "api_permissions_url": "https://portal.azure.com/#app"},
        }

    def agent_context(self, agents=None, status="", **outputs):
        root = make_root(self)
        shutil.copytree(REPO / "agents", root / "agents")
        http = FakeFoundry(self.ENDPOINT, agents)
        ctx = make_context(self, root, FakeShell([TOKEN_RULE, (command("git", "status", "--porcelain"),
                                                                deploy.Result(0, status))]), http)
        ctx.cache["outputs"] = {**self.outputs(), **outputs}
        return ctx, http

    def current(self, ctx, *keys):
        """Live agents that already run the reviewed definitions of `keys`."""
        _, agents, _ = deploy.foundry_agents(ctx)
        project = FakeFoundry(self.ENDPOINT)
        for agent in agents:
            if agent["key"] in keys:
                project.store(agent["name"], agent["body"])
        return project.agents

    @staticmethod
    def labels(body):
        return [tool["server_label"] for tool in body["definition"]["tools"]]

    def test_the_agents_are_created_after_one_approval_that_names_them(self):
        ctx, http = self.agent_context()
        with self.assertRaises(deploy.NeedsApproval) as stop:
            deploy.phase_agent(ctx)
        message = stop.exception.message
        for name, display in self.AGENTS.values():
            self.assertIn(f"Create Foundry agent {name} ({display})", message)
        for source in deploy.foundry.VARIANTS.values():
            self.assertIn(source, message)
        self.assertFalse(http.posts(r"/agents"))

        ctx.args.approve = {"agent-publish": True}
        report = deploy.phase_agent(ctx)
        created = {body["name"]: body for url, body in http.posts(r"/agents\?api-version=v1$")}
        self.assertEqual(sorted(created), sorted(name for name, _ in self.AGENTS.values()))
        self.assertEqual({name: self.labels(body) for name, body in created.items()},
                         {"demo-with-iq": self.WITH_IQ_TOOLS, "demo-without-iq": []})
        instructions = {body["definition"]["instructions"] for body in created.values()}
        self.assertEqual(instructions, {(REPO / "agents" / "scenario-with-iq" / "instructions.md").read_text(encoding="utf-8")})
        for body in created.values():
            self.assertEqual((body["definition"]["kind"], body["definition"]["model"], body["definition"]["reasoning"]),
                             ("prompt", "agent", {"effort": "low"}))
            # The digest covers the version body; the name only addresses the agent.
            version = {key: value for key, value in body.items() if key != "name"}
            self.assertEqual(body["metadata"][deploy.foundry.DIGEST_KEY], deploy.foundry.digest(version))
            # Every placeholder is filled at deployment; tools take no per-request input.
            self.assertNotIn("{{", json.dumps(body))
        tools = {tool["server_label"]: tool for tool in created["demo-with-iq"]["definition"]["tools"]}
        self.assertEqual(tools["fabric_iq_ontology"]["project_connection_id"], "fabric-iq-scenario")
        self.assertIn(f"/items/{ONTOLOGY}/ontologyEndpoint", tools["fabric_iq_ontology"]["server_url"])
        self.assertEqual((tools["work_iq_mail"]["project_connection_id"], tools["work_iq_mail"]["server_url"]),
                         ("work-iq", "https://workiq.svc.cloud.microsoft/mcp"))
        self.assertEqual(tools["foundry_iq_knowledge"]["project_connection_id"], "demo-knowledge-mcp")
        self.assertEqual(tools["web_iq_public_context"]["project_connection_id"], "public-context-mcp")
        for method, url, body, headers in http.calls:
            self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        scopes = {call[call.index("--scope") + 1] for call in ctx.shell.matching(command("az", "account", "get-access-token"))}
        self.assertEqual(scopes, {deploy.FOUNDRY_SCOPE})
        self.assertIn("demo-with-iq v1 (" + ", ".join(self.WITH_IQ_TOOLS) + ")", report)
        self.assertIn("demo-without-iq v1 (no tools)", report)
        self.assertIn("gpt-5.4-mini", report)
        receipt = ctx.state.section("agents")["with_iq"]
        self.assertEqual((receipt["name"], receipt["version"], receipt["published"]["status"], receipt["work_iq_enabled"]),
                         ("demo-with-iq", "1", "done", True))

    def test_work_iq_mail_is_published_only_while_work_iq_is_enabled(self):
        # Off: the with-IQ agent leaves out its optional mail tool, whose connection does not exist.
        ctx, http = self.agent_context(**self.outputs(work_iq=False))
        ctx.args.yes = True
        with mock.patch.object(deploy.foundry, "render", wraps=deploy.foundry.render) as render:
            report = deploy.phase_agent(ctx)
        self.assertEqual({call.args[2]: call.kwargs["omit"] for call in render.call_args_list},
                         {"with_iq": frozenset({"WorkIQMail"}), "without_iq": frozenset()})
        created = {body["name"]: body for _, body in http.posts(r"/agents\?api-version=v1$")}
        self.assertEqual(self.labels(created["demo-with-iq"]), self.WITH_IQ_TOOLS[:-1])
        self.assertNotIn("work_iq", json.dumps(created["demo-with-iq"]["definition"]["tools"]))
        self.assertIn("Work IQ is off", report)
        # On: the definition and its digest change, so only the with-IQ agent gets a new version, with the tool.
        for work_iq, tools, version in ((True, self.WITH_IQ_TOOLS, "2"), (False, self.WITH_IQ_TOOLS[:-1], "3")):
            with self.subTest(work_iq=work_iq):
                ctx.cache["outputs"] = self.outputs(work_iq=work_iq)
                before = len(http.posts(r"/agents"))
                self.assertIn(f"demo-with-iq v{version} ", deploy.phase_agent(ctx))
                (url, body), = http.posts(r"/agents")[before:]
                self.assertTrue(url.startswith(f"{self.ENDPOINT}/agents/demo-with-iq/versions?"))
                self.assertEqual(self.labels(body), tools)
                self.assertEqual(ctx.state.section("agents")["with_iq"]["work_iq_enabled"], work_iq)

    def test_a_changed_work_iq_setting_reminds_to_publish_the_agents_again(self):
        ctx, _ = self.agent_context()
        ctx.args.yes = True
        deploy.phase_agent(ctx)
        self.assertIsNone(deploy.work_iq_change(ctx, self.outputs(work_iq=True)))
        change = deploy.work_iq_change(ctx, self.outputs(work_iq=False))
        self.assertIn("still names the removed work-iq connection", change)
        self.assertIn("through the agent phase", change)

    def test_uncommitted_or_untracked_agent_definitions_are_never_published(self):
        for status in (" M agents/scenario-with-iq/instructions.md\n", "?? agents/scenario-with-iq/tools/Draft.json\n"):
            with self.subTest(status=status):
                ctx, http = self.agent_context(status=status)
                ctx.args.yes = True
                with self.assertRaisesRegex(deploy.Blocked, "agent definitions have uncommitted changes") as stop:
                    deploy.phase_agent(ctx)
                self.assertIn("git status -- agents", " ".join(stop.exception.next_steps))
                self.assertEqual(ctx.shell.matching(command("git", "status")),
                                 [["git", "status", "--porcelain", "--untracked-files=all", "--", "agents"]])
                self.assertFalse(http.calls)

    def test_current_agents_publish_nothing_and_need_no_approval(self):
        ctx, _ = self.agent_context()
        ctx.http.agents = self.current(ctx, *deploy.foundry.VARIANTS)
        self.assertIn("agents run their reviewed definitions", deploy.phase_agent(ctx))
        self.assertFalse(ctx.http.posts(r"/agents"))

    def test_a_changed_definition_publishes_a_new_version_of_only_that_agent(self):
        ctx, http = self.agent_context()
        http.agents = self.current(ctx, "without_iq")
        http.store("demo-with-iq", {"description": "older", "metadata": {deploy.foundry.DIGEST_KEY: "0" * 64},
                                    "definition": {"kind": "prompt", "model": "agent"}})
        with self.assertRaisesRegex(deploy.NeedsApproval, "Publish a new version of Foundry agent demo-with-iq") as stop:
            deploy.phase_agent(ctx)
        self.assertNotIn("demo-without-iq", stop.exception.message)
        ctx.args.yes = True
        self.assertIn("demo-with-iq v2", deploy.phase_agent(ctx))
        (url, body), = http.posts(r"/agents")
        self.assertTrue(url.startswith(f"{self.ENDPOINT}/agents/demo-with-iq/versions?"))
        self.assertNotIn("name", body)
        self.assertEqual(ctx.state.section("agents")["with_iq"]["published"]["version"], "2")

    def test_a_refused_or_failed_publish_is_recorded_and_not_reported_ready(self):
        for status, expected in ((403, "Foundry User"), (400, "failed with HTTP 400")):
            with self.subTest(status=status):
                ctx, http = self.agent_context()
                http.publish_errors = [reply(status, {"error": {"code": "invalid", "message": "rejected"}})]
                ctx.args.yes = True
                with self.assertRaises(deploy.Blocked) as stop:
                    deploy.phase_agent(ctx)
                self.assertIn(expected, stop.exception.message + " ".join(stop.exception.next_steps))
                self.assertEqual(ctx.state.section("agents")["with_iq"]["published"]["status"], "failed")
                self.assertNotIn("ready_at", ctx.state.section("agents")["with_iq"])

    def test_a_publish_that_does_not_take_is_not_reported_ready(self):
        ctx, http = self.agent_context()
        ctx.args.yes = True
        http.store = lambda name, body: {"object": "agent", "name": name, "versions": {"latest": {"version": "1"}}}
        with self.assertRaisesRegex(deploy.Blocked, "does not run its reviewed definition"):
            deploy.phase_agent(ctx)

    def test_a_model_without_reasoning_omits_the_setting(self):
        ctx, http = self.agent_context(agent_settings={"model": "gpt-4.1", "model_deployment": "agent",
                                                       "reasoning_effort": None})
        ctx.args.yes = True
        deploy.phase_agent(ctx)
        for _, body in http.posts(r"/agents"):
            self.assertNotIn("reasoning", body["definition"])

    def test_agents_are_rendered_only_from_complete_terraform_outputs(self):
        tools = self.outputs()["foundry_tool_connections"]
        cases = {
            "missing from Terraform outputs: knowledge_base_connection, knowledge_base_mcp_endpoint":
                {"foundry_tool_connections": {**tools, "knowledge_base": None}},
            "missing from Terraform outputs: work_iq_mail_connection, work_iq_mail_mcp_endpoint":
                {"foundry_tool_connections": {**tools, "work_iq_mail": None}},
            "not a Foundry project endpoint": {"foundry_project_endpoint": "https://example.com/api/projects/demo"},
            "must name exactly the with_iq and without_iq agents": {"foundry_agents": {}},
            "with_iq entry must name": {"foundry_agents": {**self.outputs()["foundry_agents"],
                                                           "with_iq": {"name": "x", "display_name": "x",
                                                                       "source": "agents/other"}}},
        }
        for message, outputs in cases.items():
            with self.subTest(message):
                ctx, http = self.agent_context(**outputs)
                ctx.args.yes = True
                with self.assertRaisesRegex(deploy.Blocked, message):
                    deploy.phase_agent(ctx)
                self.assertFalse(http.calls)

    def app_context(self, *, tags=(), build_code=0, status="", health=(200,), client=None, grants=None):
        root = make_root(self)
        tfvars = root / "infra" / "terraform.tfvars"
        tfvars.write_text('project_name = "demo"\napp_image_tag = null # deploy.py sets it\n', encoding="utf-8")
        tag = deploy.hashlib.sha256(self.LISTING.encode()).hexdigest()[:12]
        built = list(tags)

        def show_tags(args, options):
            if not built:
                return deploy.Result(1, "", "ERROR: Error: repository name not known to registry. Correlation ID: x.")
            return deploy.Result(0, json.dumps(built))

        def build(args, options):
            if not build_code:
                built.append(tag)
            return deploy.Result(build_code, "Run ID: ca1 was successful after 1m40s")

        def output(args, options):
            values = self.outputs()
            applied = re.search(r'(?m)^app_image_tag = "([0-9a-f]{12})"', tfvars.read_text(encoding="utf-8"))
            if applied and ctx.shell.matching(terraform("apply")):
                values["app"].update(url=self.APP_URL, image_tag=applied[1])
            return deploy.Result(0, json.dumps({name: {"value": value} for name, value in values.items()}))

        variables = {"fabric_git_enabled": False, "knowledge_lakehouse_id": LAKEHOUSE}
        rules = [
            TOKEN_RULE,
            (command("az", "ad", "sp", "show"), deploy.Result(0, json.dumps({"id": self.FOUNDRY_PRINCIPAL}))),
            (command("git", "status", "--porcelain"), deploy.Result(0, status)),
            (command("git", "ls-tree", "-r", "HEAD"), deploy.Result(0, self.LISTING)),
            (command("az", "acr", "repository", "show-tags"), show_tags),
            (command("az", "acr", "build"), build),
            (terraform("output"), output),
            (terraform("console"), deploy.Result(0, json.dumps(json.dumps(variables)))),
            *plan_rules([("azurerm_container_app.app[0]", ["create"])]),
        ]
        granted = {"consentType": "AllPrincipals", "resourceId": self.FOUNDRY_PRINCIPAL, "scope": "user_impersonation"}
        http = FakeHttp()
        http.add("GET", rf"servicePrincipals/{self.APP_PRINCIPAL}/oauth2PermissionGrants$",
                 *(grants or [reply(body={"value": [granted]})]))
        http.add("POST", r"/v1\.0/oauth2PermissionGrants$", reply(201, granted))
        http.add("GET", r"/healthz$", *[reply(code) for code in health])
        http.add("GET", r"/api/config$", reply(body={"mock": False, "clientId": client or self.CLIENT, "tenantId": TENANT}))
        ctx = make_context(self, root, FakeShell(rules), http)
        ctx.initialized = True
        ctx.environ["WEB_IQ_API_KEY"] = "web-iq-key"
        ctx.cache["outputs"] = self.outputs()
        ctx.state.section("target").update(tenant_id=TENANT, subscription_id="sub", user_object_id="user")
        return ctx, tag

    def test_the_committed_app_is_built_once_and_deployed_by_its_reviewed_plan(self):
        granted = {"consentType": "AllPrincipals", "resourceId": self.FOUNDRY_PRINCIPAL, "scope": "user_impersonation"}
        ctx, tag = self.app_context(grants=[reply(body={"value": []}), reply(body={"value": [granted]})])
        with self.assertRaises(deploy.NeedsApproval) as stop:
            deploy.phase_app(ctx)
        self.assertEqual(stop.exception.approval["key"], "app-build")
        self.assertIn(f"app:{tag}", stop.exception.message)
        self.assertFalse(ctx.shell.matching(command("az", "acr", "build")))
        # The app's sign-in scope was granted tenant-wide first, as the assumed administrator.
        (_, grant), = ctx.http.posts(r"oauth2PermissionGrants$")
        self.assertEqual((grant["clientId"], grant["resourceId"], grant["scope"]),
                         (self.APP_PRINCIPAL, self.FOUNDRY_PRINCIPAL, "user_impersonation"))
        ctx.args.approve = {"app-build": True}
        with self.assertRaises(deploy.NeedsApproval) as stop:
            deploy.phase_app(ctx)
        self.assertEqual(stop.exception.approval["key"], "app")
        build, = ctx.shell.matching(command("az", "acr", "build"))
        # Built from app/ alone with its own Dockerfile: for a local context, az acr build reads --file from the
        # working directory, the repository root.
        self.assertEqual(build, ["az", "acr", "build", "--registry", self.REGISTRY, "--image", f"app:{tag}",
                                 "--file", "app/Dockerfile", "--platform", "linux/amd64", "--only-show-errors", "app"])
        status = ctx.shell.matching(command("git", "status", "--porcelain"))[0]
        self.assertEqual(status[-3:], ["--untracked-files=all", "--", "app"])
        # The plan names the tag; tfvars keeps the deployed one until the plan is approved, so an earlier phase's
        # full plan never deploys an unapproved image.
        plan, = ctx.shell.matching(terraform("plan"))
        self.assertIn(f"-var=app_image_tag={tag}", plan)
        self.assertIn("app_image_tag = null # deploy.py sets it",
                      (ctx.root / "infra" / "terraform.tfvars").read_text(encoding="utf-8"))
        ctx.args.approve = {"app": stop.exception.approval["sha256"][:12]}
        report = deploy.phase_app(ctx)
        self.assertIn(f'app_image_tag = "{tag}" # deploy.py sets it',
                      (ctx.root / "infra" / "terraform.tfvars").read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(os.stat(ctx.root / "infra" / "terraform.tfvars").st_mode), 0o600)
        self.assertEqual(len(ctx.shell.matching(command("az", "acr", "build"))), 1, "the image is built once")
        self.assertIn(f"{self.APP_URL} runs app:{tag}", report)
        self.assertIn("the app's Microsoft Foundry sign-in scope is granted tenant-wide", report)
        self.assertIn("not proof of consent", report)
        self.assertEqual(len(ctx.http.posts(r"oauth2PermissionGrants$")), 1, "the grant is made once")
        self.assertEqual(ctx.state.section("app")["image_tag"], tag)
        self.assertEqual(ctx.state.section("app")["url"], self.APP_URL)

    def test_an_existing_image_skips_the_build(self):
        ctx, tag = self.app_context(tags=["0" * 12])
        ctx.shell.rules.insert(0, (command("az", "acr", "repository", "show-tags"),
                                   deploy.Result(0, json.dumps(["0" * 12, tag]))))
        ctx.args.yes = True
        self.assertIn(f"runs app:{tag}", deploy.phase_app(ctx))
        self.assertFalse(ctx.shell.matching(command("az", "acr", "build")))
        self.assertNotIn("build", ctx.state.section("app"))

    def test_uncommitted_or_untracked_app_files_are_never_built(self):
        for status in (" M app/src/App.tsx\n", "?? app/src/scratch.ts\n"):
            with self.subTest(status=status):
                ctx, _ = self.app_context(status=status)
                ctx.args.yes = True
                with self.assertRaisesRegex(deploy.Blocked, "uncommitted changes"):
                    deploy.phase_app(ctx)
                self.assertFalse(ctx.shell.matching(command("az", "acr")))

    def test_a_failed_build_stops_before_terraform(self):
        ctx, _ = self.app_context(build_code=1)
        ctx.args.yes = True
        with self.assertRaisesRegex(deploy.Blocked, "az acr build failed") as stop:
            deploy.phase_app(ctx)
        self.assertIn("build and push", " ".join(stop.exception.next_steps))
        self.assertFalse(ctx.shell.matching(terraform("plan")))
        self.assertIn("app_image_tag = null", (ctx.root / "infra" / "terraform.tfvars").read_text(encoding="utf-8"))

    def test_the_tag_is_written_only_to_one_reviewed_tfvars_line(self):
        for text in ('project_name = "demo"\n', 'app_image_tag = null\napp_image_tag = null\n',
                     'app_image_tag = "latest"\n'):
            with self.subTest(text=text):
                ctx, tag = self.app_context(tags=["x"])
                (ctx.root / "infra" / "terraform.tfvars").write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(deploy.Blocked, "exactly one top-level app_image_tag"):
                    deploy.set_app_image_tag(ctx, tag)
                self.assertEqual((ctx.root / "infra" / "terraform.tfvars").read_text(encoding="utf-8"), text)
                # The phase checks the line before it builds anything.
                ctx.args.yes = True
                with self.assertRaisesRegex(deploy.Blocked, "exactly one top-level app_image_tag"):
                    deploy.phase_app(ctx)
                self.assertFalse(ctx.shell.matching(command("az", "acr", "build")))

    def test_an_app_that_serves_another_registration_or_never_starts_is_not_ready(self):
        ctx, _ = self.app_context(client="dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        ctx.args.yes = True
        with self.assertRaisesRegex(deploy.Blocked, "does not serve this deployment's sign-in registration"):
            deploy.phase_app(ctx)
        self.assertNotIn("verified_at", ctx.state.section("app"))
        ctx, _ = self.app_context(health=(503,))
        ctx.args.yes = True
        with self.assertRaisesRegex(deploy.Waiting, "does not answer its health check"):
            deploy.phase_app(ctx)
        # The health loop polls on its own budget, so the HTTP helper sends each probe once.
        self.assertEqual({attempts for (_, url, *_), attempts in zip(ctx.http.calls, ctx.http.attempts)
                          if url.endswith("/healthz")}, {1})

    def test_the_app_phase_reports_how_to_use_and_compare_the_deployment(self):
        steps = " ".join(deploy.APP_NEXT)
        for text in ("sign in", "consent card", "Ask both", "second user", deploy.SCENARIO.app_question):
            self.assertIn(text, steps)
        for retired in ("ms app", "Power Platform", "rehearse", "presenter"):
            self.assertNotIn(retired, steps)


class FoundryPreflightTests(unittest.TestCase):
    def test_model_quota_is_summed_per_quota_for_the_deployments_still_to_create(self):
        usages = [{"name": {"value": "OpenAI.GlobalStandard.gpt-5.4-mini"}, "limit": 250, "currentValue": 0},
                  {"name": {"value": "OpenAI.GlobalStandard.text-embedding-3-large"}, "limit": 100, "currentValue": 40}]
        shell = FakeShell([(command("az", "cognitiveservices", "usage", "list"), deploy.Result(0, json.dumps(usages)))])
        ctx = make_context(self, make_root(self), shell)
        values = {"location": "swedencentral",
                  "chat_model": {"name": "gpt-5.4-mini", "sku": "GlobalStandard", "capacity": 200},
                  "agent_model": {"name": "gpt-5.4-mini", "sku": "GlobalStandard", "capacity": 100},
                  "embedding_model": {"name": "text-embedding-3-large", "sku": "GlobalStandard", "capacity": 50}}
        with self.assertRaisesRegex(deploy.Blocked, r"250 units free in swedencentral; chat_model \+ agent_model need 300"):
            deploy.check_model_quota(ctx, values, ["chat_model", "embedding_model", "agent_model"])
        # An existing deployment adds only the agent deployment, which fits.
        self.assertEqual(deploy.check_model_quota(ctx, values, ["agent_model"]), "OpenAI.GlobalStandard.gpt-5.4-mini 250 free")
        self.assertEqual(set(deploy.MODEL_DEPLOYMENTS.values()) - deploy.PLATFORM_ADDRESSES, set())

    def test_preflight_requires_the_foundry_api_principal_the_app_signs_in_to(self):
        missing = "ERROR: Resource 'x' does not exist or one of its queried reference-property objects are not present."
        shell = FakeShell([(command("az", "ad", "sp", "show"), deploy.Result(1, "", missing))])
        ctx = make_context(self, make_root(self), shell)
        with self.assertRaisesRegex(deploy.Blocked, "Microsoft Foundry's API") as stop:
            deploy.foundry_api(ctx)
        self.assertIn(f"az ad sp create --id {deploy.FOUNDRY_API_APP_ID}", " ".join(stop.exception.next_steps))
        self.assertIn(deploy.FOUNDRY_API_APP_ID, shell.calls[0])
        # Terraform reads the same principal for the app's sign-in registration.
        self.assertIn(f'foundry_api_client_id = "{deploy.FOUNDRY_API_APP_ID}"',
                      (REPO / "infra" / "app.tf").read_text(encoding="utf-8"))

    def test_preflight_requires_the_work_iq_principal_a_global_administrator_provisions(self):
        shell = FakeShell([(command("az", "ad", "sp", "show"), deploy.Result(1, "", "Resource does not exist"))])
        ctx = make_context(self, make_root(self), shell)
        with self.assertRaisesRegex(deploy.Blocked, "not enabled for the Work IQ API") as stop:
            deploy.work_iq_api(ctx)
        self.assertIn(f"az ad sp create --id {deploy.WORK_IQ_APP_ID}", " ".join(stop.exception.next_steps))
        self.assertIn("work_iq_enabled = false", " ".join(stop.exception.next_steps))
        self.assertIn(f'work_iq_api_client_id = "{deploy.WORK_IQ_APP_ID}"',
                      (REPO / "infra" / "work_iq.tf").read_text(encoding="utf-8"))


class FakeOpener:
    """urllib's opener behind deploy.Http: each open() returns, or raises, the next scripted outcome."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.methods = []

    def open(self, request, timeout):
        self.methods.append(request.get_method())
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeResponse(io.BytesIO):
    def __init__(self, status=200, body=b"{}", headers=None):
        super().__init__(body)
        self.status, self.headers = status, headers or {}


def http_error(status, headers=None):
    return urllib.error.HTTPError("https://example.test/x", status, "error", headers or {}, io.BytesIO(b"{}"))


class RetryTests(unittest.TestCase):
    URL = "https://example.test/x"
    TRANSIENT = deploy.Result(1, "", "ERROR: ('Connection aborted.', RemoteDisconnected('Remote end closed connection "
                                     "without response'))")

    def http(self, *outcomes):
        sleeps = []
        http = deploy.Http(sleep=sleeps.append)
        http._opener = FakeOpener(*outcomes)
        return http, sleeps

    def test_a_read_retries_a_busy_service_and_a_dropped_connection_then_succeeds(self):
        http, sleeps = self.http(http_error(503, {"Retry-After": "120"}), ConnectionResetError(104, "reset"),
                                 FakeResponse(body=b'{"ok": true}'))
        response = http.request("GET", self.URL)
        self.assertEqual((response.status, response.json()), (200, {"ok": True}))
        self.assertEqual(http._opener.methods, ["GET"] * 3)
        # Retry-After is honored up to its cap; without it, the backoff doubles.
        self.assertEqual(sleeps, [60, deploy.RETRY_BACKOFF * 2])

    def test_refusals_writes_and_lasting_failures_are_not_retried_beyond_the_bound(self):
        for status in (401, 403, 404, 409):
            with self.subTest(status=status):
                http, sleeps = self.http(http_error(status), FakeResponse())
                self.assertEqual(http.request("GET", self.URL).status, status)
                self.assertEqual((http._opener.methods, sleeps), (["GET"], []))
        # A second write could repeat what the service already did, so a write is sent once.
        http, _ = self.http(http_error(503), FakeResponse())
        self.assertEqual(http.request("POST", self.URL, body={}).status, 503)
        http, _ = self.http(ConnectionResetError(104, "reset"), FakeResponse())
        with self.assertRaises(deploy.TransportError):
            http.request("POST", self.URL, body={})
        self.assertEqual(http._opener.methods, ["POST"])
        # A read gives up after RETRY_ATTEMPTS transient failures; a lasting one, such as a bad certificate, at once.
        http, sleeps = self.http(*[urllib.error.URLError(TimeoutError("timed out"))] * 4)
        with self.assertRaisesRegex(deploy.TransportError, "TimeoutError"):
            http.request("GET", self.URL)
        self.assertEqual((len(http._opener.methods), sleeps), (deploy.RETRY_ATTEMPTS, [2, 4]))
        http, _ = self.http(urllib.error.URLError(ssl.SSLCertVerificationError("bad certificate")), FakeResponse())
        with self.assertRaises(deploy.TransportError):
            http.request("GET", self.URL)
        self.assertEqual(len(http._opener.methods), 1)
        # Helpers that keep their own retry loop opt out.
        http, _ = self.http(http_error(503), FakeResponse())
        self.assertEqual(http.request("GET", self.URL, attempts=1).status, 503)

    def test_the_runner_wires_its_own_sleep_into_the_http_helper(self):
        clock = Clock()
        ctx = deploy.Context(deploy.parse_arguments(["up", "--non-interactive"]), root=make_root(self),
                             sleep=clock.sleep, clock=clock, interactive=False, environ={})
        ctx.http._opener = FakeOpener(http_error(429), FakeResponse())
        self.assertEqual(ctx.http.request("GET", self.URL).status, 200)
        self.assertEqual(clock.now, deploy.RETRY_BACKOFF)

    def test_service_principal_lookups_retry_only_transient_failures(self):
        outcomes = [self.TRANSIENT, deploy.Result(0, '{"id": "principal"}')]
        shell = FakeShell([(command("az", "ad", "sp", "show"), lambda args, options: outcomes.pop(0))])
        ctx = make_context(self, make_root(self), shell)
        deploy.foundry_api(ctx)
        self.assertEqual((len(shell.calls), ctx.clock.now), (2, deploy.RETRY_BACKOFF))
        for stderr, message in (("ERROR: Insufficient privileges to complete the operation.", "Could not read"),
                                ("ERROR: Resource 'x' does not exist or one of its queried reference-property objects "
                                 "are not present.", "no service principal")):
            with self.subTest(stderr=stderr):
                shell = FakeShell([(command("az", "ad", "sp", "show"), deploy.Result(1, "", stderr))])
                ctx = make_context(self, make_root(self), shell)
                with self.assertRaisesRegex(deploy.Blocked, message):
                    deploy.foundry_api(ctx)
                self.assertEqual(len(shell.calls), 1)
        # A failure that outlasts the retries is reported as it is, never as a missing principal.
        shell = FakeShell([(command("az", "ad", "sp", "show"), self.TRANSIENT)])
        ctx = make_context(self, make_root(self), shell)
        with self.assertRaisesRegex(deploy.Blocked, "Could not read"):
            deploy.foundry_api(ctx)
        self.assertEqual(len(shell.calls), deploy.RETRY_ATTEMPTS)

    def test_read_only_lookups_retry_a_busy_service_but_never_a_refusal(self):
        lookup = ["rest", "--method", "get", "--url", "https://management.azure.com/x"]
        outcomes = [deploy.Result(1, "", "ERROR: (ServiceUnavailable) The service is busy. Status: 503"),
                    deploy.Result(0, '{"id": "x"}')]
        shell = FakeShell([(command("az", "rest"), lambda args, options: outcomes.pop(0))])
        ctx = make_context(self, make_root(self), shell)
        self.assertEqual(deploy.az_json(ctx, lookup, "Reading x"), {"id": "x"})
        self.assertEqual(len(shell.calls), 2)
        shell = FakeShell([(command("az", "rest"), deploy.Result(1, "", "ERROR: (AuthorizationFailed) denied. Status: 403"))])
        ctx = make_context(self, make_root(self), shell)
        with self.assertRaisesRegex(deploy.Blocked, "Reading x failed"):
            deploy.az_json(ctx, lookup, "Reading x")
        self.assertEqual(len(shell.calls), 1)


class WebIqTests(unittest.TestCase):
    RESOURCE_ID = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.WebIQ/accounts/demo"

    def web_context(self, *, key=None, state="Succeeded", search=None):
        http = FakeHttp()
        http.add("GET", r"/providers/Microsoft\.WebIQ/accounts/demo\?api-version=2026-06-01-preview$",
                 reply(body={"id": self.RESOURCE_ID, "properties": {"provisioningState": state}}))
        if search:
            http.add("POST", r"^https://api\.microsoft\.ai/v3/search/web$", search)
        ctx = make_context(self, make_root(self), FakeShell([TOKEN_RULE]), http)
        ctx.cache["outputs"] = {"web_iq": {"resource_id": self.RESOURCE_ID}}
        ctx.state.section("target")["subscription_id"] = "sub"
        if key:
            ctx.environ["WEB_IQ_API_KEY"] = key
        return ctx

    def test_missing_web_iq_output_blocks_readiness_without_cloud_calls(self):
        ctx = make_context(self, make_root(self))
        for outputs in ({}, {"web_iq": None}, {"web_iq": {}}):
            with self.subTest(outputs=outputs):
                ctx.cache["outputs"] = outputs
                with self.assertRaisesRegex(deploy.Blocked, "Terraform outputs are missing: web_iq"):
                    deploy.verify_web_iq(ctx)
                self.assertFalse(ctx.http.calls)
                self.assertFalse(ctx.shell.calls)

    def test_every_deployment_requires_terms_and_the_pinned_subscription_api(self):
        ctx = make_context(self, make_root(self))
        with self.assertRaisesRegex(deploy.Waiting, "terms and paid usage"):
            deploy.check_web_iq(ctx, "sub", {"web_iq_terms_accepted": False})
        self.assertFalse(ctx.shell.calls)
        for versions, succeeds in (([], False), ([deploy.WEB_IQ_API_VERSION], True)):
            provider = {"registrationState": "NotRegistered",
                        "resourceTypes": [{"resourceType": "accounts", "apiVersions": versions}]}
            ctx.shell = FakeShell([(command("az", "provider", "show"), deploy.Result(0, json.dumps(provider)))])
            values = {"web_iq_terms_accepted": True}
            if succeeds:
                self.assertIn("portal AppID linking remains manual", deploy.check_web_iq(ctx, "sub", values))
            else:
                with self.assertRaisesRegex(deploy.Blocked, "pinned Web IQ accounts API"):
                    deploy.check_web_iq(ctx, "sub", values)
            self.assertIn("sub", ctx.shell.calls[0])
            self.assertFalse(ctx.shell.matching(command("az", "provider", "register")))

    def test_document_and_ontology_success_cannot_bypass_web_iq_readiness(self):
        ctx = make_context(self, make_root(self))
        ctx.cache["outputs"] = {
            "search_endpoint": "https://demo.search.windows.net",
            "knowledge_base_mcp_endpoint": "https://demo.search.windows.net/knowledgebases/knowledge/mcp?api-version=test",
            "knowledge_base_name": "knowledge",
            "ontology_check_knowledge_base_name": "ontology-check",
            "knowledge_source_names": {deploy.SCENARIO.document_source: "docs"},
            "ontology_knowledge_sources": {deploy.SCENARIO.registration: {"knowledge_source_name": "ontology"}},
        }
        with mock.patch.object(deploy, "wait_ingestion", return_value={}), \
                mock.patch.object(deploy, "prove_retrieval", return_value={}) as retrieval:
            with self.assertRaisesRegex(deploy.Blocked, "Terraform outputs are missing: web_iq"):
                deploy.phase_verify(ctx)
        self.assertEqual(retrieval.call_count, 2)
        self.assertNotIn("dev_ready_at", ctx.state.section("verify"))
        ctx.cache["outputs"].update(public_context_knowledge_base_name="demo public", public_context_knowledge_source_name="web")
        with mock.patch.object(deploy, "wait_ingestion", return_value={}), \
                mock.patch.object(deploy, "verify_web_iq", return_value="Web IQ verified"), \
                mock.patch.object(deploy, "verify_work_iq",
                                  return_value={"enabled": True, "connection": "work-iq", "consent": "granted"}), \
                mock.patch.object(deploy, "prove_retrieval", return_value={"references": 2}) as retrieval:
            self.assertIn("Web IQ public context retrieved through Foundry IQ (2 cited references)", deploy.phase_verify(ctx))
        url, question, parameters = retrieval.call_args.args[1:4]
        self.assertTrue(url.startswith("https://demo.search.windows.net/knowledgebases/demo%20public/retrieve?api-version="))
        self.assertEqual((question, parameters["kind"], retrieval.call_args.kwargs), (
            deploy.SCENARIO.public_context_question, "mcpServer", {"attempts": 1}))
        self.assertIn("dev_ready_at", ctx.state.section("verify"))

    def test_a_provisioned_account_waits_for_portal_onboarding_not_automatic_keys(self):
        ctx = self.web_context()
        with self.assertRaisesRegex(deploy.Waiting, "authenticated search is unverified") as stop:
            deploy.verify_web_iq(ctx)
        self.assertIn(deploy.WEB_IQ_PORTAL, " ".join(stop.exception.next_steps))
        self.assertEqual(ctx.state.section("web_iq")["status"], "onboarding_pending")
        self.assertFalse(ctx.http.posts("search/web"))

    def test_provisioning_and_target_failures_never_send_the_key(self):
        for state, error in (("Creating", deploy.Waiting), ("Failed", deploy.Blocked), ("Canceled", deploy.Blocked)):
            ctx = self.web_context(key="private-key", state=state)
            with self.assertRaises(error):
                deploy.verify_web_iq(ctx)
            self.assertFalse(ctx.http.posts("search/web"))
            self.assertNotIn("private-key", json.dumps(ctx.http.calls))
        ctx = self.web_context(key="private-key")
        ctx.cache["outputs"]["web_iq"]["resource_id"] = self.RESOURCE_ID.replace("/sub/", "/another/")
        with self.assertRaisesRegex(deploy.Blocked, "selected subscription"):
            deploy.verify_web_iq(ctx)
        self.assertFalse(ctx.http.calls)

    def test_invalid_header_characters_never_reach_http_or_error_output(self):
        for key in ("private\nkey", "private key", "private\u0100key"):
            ctx = self.web_context(key=key)
            with self.assertRaisesRegex(deploy.Blocked, "invalid HTTP header characters") as stop:
                deploy.verify_web_iq(ctx)
            self.assertNotIn(key, str(stop.exception))
            self.assertFalse(ctx.http.posts("search/web"))

    def test_a_cited_live_probe_keeps_secrets_and_retrieved_content_out_of_receipts(self):
        result = {"title": "Microsoft Web IQ", "url": "https://learn.microsoft.com/en-us/web-iq/",
                  "content": "Grounding passage", "crawledAt": "2026-10-19T00:00:00Z"}
        for dated in (True, False):
            with self.subTest(dated=dated):
                item = result if dated else {key: value for key, value in result.items() if key != "crawledAt"}
                ctx = self.web_context(key="private-key", search=reply(body={"webResults": [item]}))
                message = deploy.verify_web_iq(ctx)
                self.assertIn("agent/app integration remain unverified", message)
                receipt = ctx.state.section("web_iq")
                self.assertEqual(receipt["status"], "search_verified")
                self.assertEqual(receipt["dated_result_count"], int(dated))
                post = [call for call in ctx.http.calls if call[0] == "POST"]
                self.assertEqual(len(post), 1)
                _, url, body, headers = post[0]
                self.assertEqual(url, deploy.WEB_IQ_SEARCH)
                self.assertEqual(headers, {"x-apikey": "private-key"})
                self.assertEqual(body["query"], deploy.WEB_IQ_QUESTION)
                self.assertEqual(body["contentFormat"], "passage")
                self.assertEqual(body["safeSearch"], "strict")
                for private in ("private-key", "Grounding passage"):
                    self.assertNotIn(private, ctx.state.path.read_text(encoding="utf-8"))
                    self.assertNotIn(private, " ".join(ctx.messages))

    def test_errors_empty_results_and_missing_citations_are_not_search_success(self):
        bad_results = ({}, {"webResults": []}, {"webResults": "bad"}, {"webResults": [None]},
                       {"webResults": [{"title": "Title", "content": "Text", "url": "javascript:alert(1)"}]},
                       {"webResults": [{"title": "Title", "url": "https://example.com"}]})
        for response in (reply(401, {"message": "private-key"}), reply(403), reply(429), reply(500),
                         *(reply(body=body) for body in bad_results)):
            ctx = self.web_context(key="private-key", search=response)
            ctx.state.section("web_iq").update(status="search_verified", verified_at="stale")
            with self.assertRaises(deploy.Blocked) as stop:
                deploy.verify_web_iq(ctx)
            self.assertNotIn("private-key", str(stop.exception))
            self.assertEqual(ctx.state.section("web_iq")["status"], "search_unverified")
            self.assertNotIn("verified_at", ctx.state.section("web_iq"))
            self.assertEqual(len(ctx.http.posts("search/web")), 1, "billable searches must not be retried")


if __name__ == "__main__":
    unittest.main()

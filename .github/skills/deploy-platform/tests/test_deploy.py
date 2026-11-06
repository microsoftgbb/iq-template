import base64
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock


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
TENANT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
COMMIT = "c" * 40
PLAN_LOGICAL_ID = "17fca947-7a9f-4822-91ac-cd6aa8256e75"
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

    def add(self, method, pattern, *responses):
        self.routes.append((method, re.compile(pattern), list(responses)))

    def request(self, method, url, headers=None, body=None, timeout=120):
        self.calls.append((method, url, body, dict(headers or {})))
        for route_method, pattern, responses in self.routes:
            if route_method == method and pattern.search(url):
                response = responses.pop(0) if len(responses) > 1 else responses[0]
                return response(url, body) if callable(response) else response
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
    (root / "agents" / "scenario-assistant").mkdir(parents=True)
    shutil.copy(REPO / deploy.AGENT_SOURCE / "hosted-agent.json", root / deploy.AGENT_SOURCE / "hosted-agent.json")
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
    ctx.cache["variables"] = {"fabric_git_enabled": False}
    ctx.start("test")
    return ctx


def tmdl(bound, workspace=WORKSPACE):
    def part(path, text):
        return {"path": path, "payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"}

    parts = []
    for table in deploy.SAMPLE_TABLES:
        text = f"table {table}\n"
        if bound:
            # The live shape: getDefinition keeps the partition and pin timestamp but elides the source pins.
            text += (f"\tpartition {table} = entity\n\t\tmode: directLake\n\t\tsource\n"
                     f"\t\t\tentityName: {table}\n\t\t\tschemaName: dbo\n\t\t\texpressionSource: ScenarioLakehouse\n\n"
                     "\t\tannotation ONT_PinnedAtUtc = 2026-01-01T00:00:00.000Z\n")
        parts.append(part(f"tables/{table}.tmdl", text))
    if bound:
        parts.append(part("expressions.tmdl", "expression ScenarioLakehouse =\n\t\tlet\n\t\t\tSource = "
                          f'AzureStorage.DataLake("https://onelake.dfs.fabric.microsoft.com/{workspace}/{LAKEHOUSE}", '
                          "[HierarchicalNavigation=true])\n\t\tin\n\t\t\tSource\n"))
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
    {"id": LAKEHOUSE, "type": "Lakehouse", "displayName": "Scenario", "workspaceId": WORKSPACE},
    {"id": SEED, "type": "Notebook", "displayName": "SeedScenario", "workspaceId": WORKSPACE},
    {"id": ONTOLOGY, "type": "Ontology", "displayName": "ScenarioOntology", "workspaceId": WORKSPACE},
    {"id": BIND, "type": "Notebook", "displayName": "BindScenario", "workspaceId": WORKSPACE},
]}


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
             reply(body={"logicalId": "e6a99499-e3f5-4a9b-8288-3985c73a05e5", "properties": {"generation": 2}}))
    http.add("GET", rf"/lakehouses/{LAKEHOUSE}$", reply(body={"properties": {"defaultSchema": "dbo"}}))
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

    def test_preflight_checks_the_pat_only_for_opted_in_git(self):
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
        ctx.cache["variables"].update(search_sku="serverless", location="centralus")
        with mock.patch.object(deploy, "check_web_iq", return_value="Web IQ ready"), \
                mock.patch.object(deploy, "state_addresses", return_value=deploy.PLATFORM_ADDRESSES), \
                mock.patch.object(deploy, "check_search_tier", return_value="Search offered"), \
                mock.patch.object(deploy.runtime, "check_environment"), \
                mock.patch.object(deploy, "check_pat") as check_pat:
            deploy.phase_preflight(ctx)
            check_pat.assert_not_called()
            ctx.cache["variables"]["fabric_git_enabled"] = True
            deploy.phase_preflight(ctx)
            check_pat.assert_called_once_with(ctx, ctx.cache["variables"])
            # A region without documented hosted agents stops before anything is provisioned.
            ctx.cache["variables"]["location"] = "westus2"
            with self.assertRaisesRegex(deploy.Blocked, "not documented in westus2"):
                deploy.phase_preflight(ctx)

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


PROJECT = "https://ai-demo.services.ai.azure.com/api/projects/demo-project"
SUBSCRIPTION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ENVIRONMENT_ID = (f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-demo/providers/Microsoft.App/"
                  "managedEnvironments/cae-demo")
APP_ID = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-demo/providers/Microsoft.App/containerApps/ca-demo-web"


class TeardownTests(unittest.TestCase):
    SUBSCRIPTION = SUBSCRIPTION

    def context(self, **options):
        ctx = make_context(self, make_root(self), **options)
        ctx.args.command = "down"
        ctx.args.allow_delete = options.get("allow_delete", False)
        ctx.initialized = True
        ctx.state.section("target").update(subscription_id=self.SUBSCRIPTION, tenant_id=TENANT, user_object_id="user")
        ctx.state.section("choices").update(local_state=True)
        ctx.cache["variables"].update(project_name="demo", github_owner="example", github_repository_name="demo-repo")
        return ctx

    def snapshot(self, runtime=True):
        return {"repository": "example/demo-repo", "project_name": "demo", "resource_ids": {},
                "hosted_agent": {"project_endpoint": PROJECT, "name": "demo-assistant", "toolbox_name": "demo-tools"}
                if runtime else None,
                "container_app": APP_ID if runtime else None, "workspaces": {"dev": WORKSPACE},
                "foundry": {"name": "ai-demo", "resource_group": "rg-demo", "location": "westeurope"}}

    def runtime_http(self, *, app=(200, 404), agent=(200, 404), toolbox=(200, 404), deleted=202):
        http = FakeHttp()
        for method, pattern, statuses in (("GET", re.escape(APP_ID) + r"\?", app),
                                          ("GET", r"/agents/demo-assistant\?api-version=v1$", agent),
                                          ("GET", r"/toolboxes/demo-tools\?api-version=v1$", toolbox)):
            http.add(method, pattern, *[reply(status, {} if status == 200 else None) for status in statuses])
        http.add("DELETE", re.escape(APP_ID) + r"\?", reply(deleted))
        http.add("DELETE", r"/agents/demo-assistant\?api-version=v1&force=true$", reply(204))
        http.add("DELETE", r"/toolboxes/demo-tools\?api-version=v1$", reply(204))
        return http

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
        state = {"values": {"root_module": {"resources": [
            {"address": "azapi_resource.foundry", "mode": "managed",
             "values": {"id": foundry, "location": "westeurope", "secret": "do-not-persist"}},
            {"address": "fabric_workspace.dev", "mode": "managed", "values": {"id": WORKSPACE}},
        ]}, "outputs": {
            "hosted_agent": {"value": {"name": "demo-assistant", "toolbox_name": "demo-tools", "project_endpoint": PROJECT,
                                       "responses_url": "do-not-persist"}},
            "web_app": {"value": {"name": "ca-demo-web", "environment_id": ENVIRONMENT_ID, "client_id": "do-not-persist"}},
            "unrelated_secret": {"value": "do-not-persist"}}}}
        ctx.shell = FakeShell(self.state_rules(state))
        with mock.patch.object(deploy.runtime, "check_environment"):
            snapshot = deploy.teardown_snapshot(ctx)
            ctx.shell = FakeShell(self.state_rules({"values": {}}))
            self.assertEqual(deploy.teardown_snapshot(ctx), snapshot)
        self.assertEqual(snapshot["workspaces"], {"dev": WORKSPACE})
        self.assertEqual(snapshot["foundry"]["name"], "ai-demo")
        self.assertEqual(snapshot["container_app"], APP_ID)
        self.assertEqual(snapshot["hosted_agent"], {"project_endpoint": PROJECT, "name": "demo-assistant",
                                                    "toolbox_name": "demo-tools"})
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

    def test_an_identity_only_deployment_tears_down_without_agent_outputs(self):
        ctx = self.context()
        state = {"values": {"root_module": {"child_modules": [{"resources": [
            {"address": "module.fabric_user_auth.azuread_application.fabric", "mode": "managed",
             "values": {"id": "/applications/app-object"}}]}]},
            "outputs": {"fabric_user_auth_scope": {"value": "api://client/.default"}}}}
        ctx.shell = FakeShell(self.state_rules(state))
        with mock.patch.object(deploy.runtime, "check_environment"):
            snapshot = deploy.teardown_snapshot(ctx)
        self.assertIsNone(snapshot["hosted_agent"])
        self.assertIsNone(snapshot["container_app"])
        ctx = self.context()
        ctx.state.section("phases")["agent"] = {"status": "approval"}
        ctx.shell = FakeShell(self.state_rules(state))
        with mock.patch.object(deploy.runtime, "check_environment"), \
                self.assertRaisesRegex(deploy.Blocked, "outputs are missing"):
            deploy.teardown_snapshot(ctx)

    def test_wrong_user_blocks_before_any_delete(self):
        ctx = self.context()
        ctx.state.section("target")["user_object_id"] = "other-user"
        ctx.shell = FakeShell(self.state_rules({}))
        with self.assertRaisesRegex(deploy.Blocked, "original Azure user"):
            deploy.teardown_snapshot(ctx)
        self.assertFalse(ctx.http.calls)

    def test_missing_receipts_are_not_silently_skipped(self):
        ctx = self.context()
        ctx.state.data["target"] = {}
        with self.assertRaisesRegex(deploy.Blocked, "original deployment receipts"):
            deploy.teardown_snapshot(ctx)

    def test_runtime_deletes_exact_app_agent_and_toolbox_only_after_approval(self):
        ctx = self.context(yes=True, allow_delete=True)
        ctx.shell = FakeShell([TOKEN_RULE])
        ctx.http = self.runtime_http()
        with self.assertRaises(deploy.NeedsApproval) as stop:
            deploy.delete_runtime(ctx, self.snapshot())
        self.assertFalse([call for call in ctx.http.calls if call[0] == "DELETE"])
        for name in ("Container App ca-demo-web", "hosted agent demo-assistant", "toolbox demo-tools"):
            self.assertIn(name, stop.exception.approval["description"])
        ctx.args.approve = {"runtime-delete": True}
        ctx.http = self.runtime_http()
        deploy.delete_runtime(ctx, self.snapshot())
        deletes = [url for method, url, *_ in ctx.http.calls if method == "DELETE"]
        self.assertEqual(len(deletes), 3)
        self.assertTrue(deletes[0].startswith(deploy.ARM_API + APP_ID))
        self.assertTrue(deletes[1].endswith("/agents/demo-assistant?api-version=v1&force=true"))
        self.assertTrue(deletes[2].endswith("/toolboxes/demo-tools?api-version=v1"))
        self.assertIn("runtime_deleted_at", ctx.state.section("destroy"))
        # Already gone: nothing to approve or delete.
        ctx.args.approve, ctx.http = {}, self.runtime_http(app=(404,), agent=(404,), toolbox=(404,))
        deploy.delete_runtime(ctx, self.snapshot())
        self.assertFalse([call for call in ctx.http.calls if call[0] == "DELETE"])

    def test_a_deletion_still_running_is_observed_on_rerun(self):
        ctx = self.context(allow_delete=True, approve={"runtime-delete": True})
        ctx.shell = FakeShell([TOKEN_RULE])
        ctx.http = self.runtime_http(app=(200,), agent=(404,), toolbox=(404,))
        ctx.deadline = ctx.clock() + 30
        with self.assertRaisesRegex(deploy.Waiting, "still being deleted"):
            deploy.delete_runtime(ctx, self.snapshot())
        self.assertNotIn("runtime_deleted_at", ctx.state.section("destroy"))
        ctx.http = self.runtime_http(app=(404,), agent=(404,), toolbox=(404,))
        deploy.delete_runtime(ctx, self.snapshot())
        self.assertIn("runtime_deleted_at", ctx.state.section("destroy"))

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

    def test_delete_failures_stop_before_terraform_or_completion(self):
        ctx = self.context(allow_delete=True, approve={"runtime-delete": True})
        ctx.shell = FakeShell([TOKEN_RULE])
        ctx.http = self.runtime_http(agent=(404,), toolbox=(404,), deleted=403)
        with self.assertRaisesRegex(deploy.Blocked, "Deleting the Container App"):
            deploy.delete_runtime(ctx, self.snapshot())
        self.assertNotIn("runtime_deleted_at", ctx.state.section("destroy"))

    def test_no_infrastructure_changes_still_cleans_runtime_and_reports_retention(self):
        ctx = self.context(allow_delete=True)
        ctx.shell = FakeShell(plan_rules([], plan_code=0))
        snapshot = self.snapshot()
        with mock.patch.object(deploy, "teardown_snapshot", return_value=snapshot), \
                mock.patch.object(deploy, "delete_runtime") as runtime_delete, \
                mock.patch.object(deploy, "purge_foundry") as purge:
            self.assertIn("managed resources deleted", deploy.phase_destroy(ctx))
        runtime_delete.assert_called_once_with(ctx, snapshot)
        purge.assert_called_once_with(ctx, snapshot)
        steps = " ".join(deploy.teardown_next(snapshot))
        self.assertIn(WORKSPACE, steps)
        self.assertIn("not immediate data erasure", steps)
        self.assertIn("are not deleted", steps)
        self.assertTrue((ctx.root / "infra" / "terraform.tfvars").exists())

    def test_runtime_deletion_precedes_full_destroy_and_purge_and_failed_apply_keeps_inventory(self):
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
                        mock.patch.object(deploy, "delete_runtime", side_effect=lambda *_: events.append("runtime")), \
                        mock.patch.object(deploy, "purge_foundry", side_effect=lambda *_: events.append("purge")):
                    with self.assertRaises(deploy.NeedsApproval) as approval:
                        deploy.phase_destroy(ctx)
                    self.assertEqual(events, [])
                    ctx.args.approve = {"destroy": approval.exception.approval["sha256"][:12]}
                    if code:
                        with self.assertRaisesRegex(deploy.Blocked, "state is preserved"):
                            deploy.phase_destroy(ctx)
                        self.assertEqual(events, ["runtime", "terraform"])
                        self.assertNotIn("managed_resources_deleted_at", ctx.state.section("destroy"))
                    else:
                        deploy.phase_destroy(ctx)
                        self.assertEqual(events, ["runtime", "terraform", "purge"])
                self.assertEqual(ctx.state.section("destroy")["snapshot"], snapshot)

    def test_down_cannot_reenter_deployment_after_teardown_started(self):
        ctx = self.context()
        ctx.state.section("destroy")["started_at"] = "earlier"
        ctx.state.save()
        ctx.args.command = "up"
        with self.assertRaisesRegex(deploy.Blocked, "being torn down"):
            deploy.run_phases(ctx, ("config",))
        self.assertFalse(ctx.shell.calls)
        args = deploy.parse_arguments(["down", "--allow-delete", "--approve", "destroy=abcdef012345",
                                       "--approve", "runtime-delete", "--approve", "purge-foundry"])
        self.assertTrue(args.allow_delete)
        self.assertEqual(args.approve["destroy"], "abcdef012345")


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
        for item, job in ((SEED, SEED_JOB), (BIND, BIND_JOB)):
            http.add("GET", rf"/items/{item}/jobs/instances$", reply(body={"value": []}))
            http.add("POST", rf"/notebooks/{item}/jobs/execute/instances\?jobType=RunNotebook$", reply(202, headers={
                "Location": f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE}/items/{item}/jobs/instances/{job}"}))
            http.add("GET", rf"/items/{item}/jobs/instances/{job}$",
                     reply(body={"status": "InProgress"}), reply(body={"status": "Completed"}))
        ctx = bootstrap_context(self, root, http, bootstrap_fallback="notebooks")
        deploy.phase_bootstrap(ctx)
        (_, body), = http.posts(r"/git/updateFromGit$")
        self.assertNotIn("deploymentPlan", body["options"])
        runs = [url for url, _ in http.posts(r"/jobs/execute/instances")]
        self.assertEqual([SEED in runs[0], BIND in runs[1]], [True, True])
        self.assertEqual(ctx.state.section("bootstrap")["jobs"]["BindScenario"]["status"], "Completed")
        self.assertFalse(ctx.state.section("bootstrap")["update"]["with_plan"])

    def test_an_active_run_is_observed_instead_of_resubmitted(self):
        root = make_root(self)
        http = FakeHttp()
        http.add("GET", rf"/items/{SEED}/jobs/instances$", reply(body={"value": [{"id": SEED_JOB, "status": "InProgress"}]}))
        http.add("GET", rf"/items/{SEED}/jobs/instances/{SEED_JOB}$", reply(body={"status": "Completed"}))
        ctx = make_context(self, root, FakeShell([TOKEN_RULE]), http)
        deploy.run_notebook(ctx, WORKSPACE, SEED, "SeedScenario", ctx.state.section("bootstrap"), skip_completed=True)
        self.assertFalse(http.posts(r"/jobs/execute/instances"))

    def test_a_failed_run_is_not_resubmitted_without_retry_failed(self):
        root = make_root(self)
        http = FakeHttp()
        http.add("GET", rf"/items/{SEED}/jobs/instances$", reply(body={"value": []}))
        ctx = make_context(self, root, FakeShell([TOKEN_RULE]), http)
        receipts = {"jobs": {"SeedScenario": {"id": SEED_JOB, "status": "Failed", "failure": "schema mismatch"}}}
        with self.assertRaisesRegex(deploy.Blocked, "schema mismatch") as stop:
            deploy.run_notebook(ctx, WORKSPACE, SEED, "SeedScenario", receipts, skip_completed=True)
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

    def test_binding_checks_target_local_source_not_placeholders(self):
        decode = deploy.base64.b64decode

        def parts(definition):
            return {part["path"]: decode(part["payload"]).decode() for part in definition["definition"]["parts"]}

        self.assertEqual(deploy.binding_problems(parts(tmdl(True)), WORKSPACE, LAKEHOUSE), [])
        self.assertIn("demo_sites has no partition yet", deploy.binding_problems(parts(tmdl(False)), WORKSPACE, LAKEHOUSE))
        placeholder = deploy.binding_problems(parts(tmdl(True, "00000000-0000-0000-0000-000000000000")), WORKSPACE, LAKEHOUSE)
        self.assertEqual(placeholder, ["expressions.tmdl does not read the DEV lakehouse"])
        rerouted = parts(tmdl(True))
        rerouted["tables/demo_assets.tmdl"] = rerouted["tables/demo_assets.tmdl"].replace(
            "expressionSource: ScenarioLakehouse", "expressionSource: DatabaseQuery")
        self.assertEqual(deploy.binding_problems(rerouted, WORKSPACE, LAKEHOUSE),
                         ["demo_assets does not read dbo.demo_assets through ScenarioLakehouse"])


class NativeBootstrapTests(unittest.TestCase):
    def context(self, *, definitions=(True,), **options):
        root = make_root(self)
        expected = deploy.workspace_items(make_context(self, root))
        ids = {item["displayName"]: item["id"] for item in ITEMS["value"]}
        ids["Bootstrap"] = CONNECTION
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
        self.assertEqual(sum(part["path"].endswith("/.platform") for part in body["definitionParts"]), 5)
        self.assertFalse(any("/git/" in url or "api.github.com" in url for _, url, *_ in ctx.http.calls))
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
        missing = {"ScenarioOntology", "BindScenario"}
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
        self.assertFalse(any(part["path"].startswith("/Scenario.Lakehouse/") for part in body["definitionParts"]))

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
                item = next(item for item in self.items if item["displayName"] == "Scenario")
                ctx.http.routes.insert(0, ("GET", re.compile(rf"/workspaces/{WORKSPACE}/items$"),
                                          [reply(body={"value": [{**item, **changes}]})]))
                with self.assertRaises(deploy.Blocked):
                    deploy.phase_bootstrap(ctx)
                self.assertFalse(ctx.http.posts(r"/bulkImportDefinitions"))

    def test_imported_definitions_are_not_replaced_after_source_changes(self):
        ctx = self.context()
        deploy.phase_bootstrap(ctx)
        notebook = ctx.root / "workspace" / "SeedScenario.Notebook" / "notebook-content.py"
        notebook.write_text(notebook.read_text(encoding="utf-8") + "\n# changed source\n", encoding="utf-8")
        with self.assertRaisesRegex(deploy.Blocked, "changed or unrecorded definitions"):
            deploy.phase_bootstrap(ctx)
        self.assertEqual(len(ctx.http.posts(r"/bulkImportDefinitions")), 1)

    def test_import_reads_only_tracked_parts_and_rejects_symlinks(self):
        ctx = self.context()
        extra = ctx.root / "workspace" / "Scenario.Lakehouse" / ".env"
        extra.write_text("not a definition", encoding="utf-8")
        parts = deploy.workspace_definition_parts(ctx, deploy.workspace_items(ctx))
        self.assertFalse(any(part["path"].endswith(".env") for group in parts.values() for part in group))
        notebook = ctx.root / "workspace" / "SeedScenario.Notebook" / "notebook-content.py"
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
        for item, job in ((SEED, SEED_JOB), (BIND, BIND_JOB)):
            ctx.http.add("GET", rf"/items/{item}/jobs/instances$", reply(body={"value": []}))
            ctx.http.add("POST", rf"/notebooks/{item}/jobs/execute/instances\?jobType=RunNotebook$",
                         reply(202, headers={"Location":
                             f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE}/items/{item}/jobs/instances/{job}"}))
            ctx.http.add("GET", rf"/items/{item}/jobs/instances/{job}$", reply(body={"status": "Completed"}))
        deploy.phase_bootstrap(ctx)
        (_, body), = ctx.http.posts(r"/items/bulkImportDefinitions$")
        self.assertNotIn("deploymentPlan", body["options"])
        runs = [url for url, _ in ctx.http.posts(r"/jobs/execute/instances")]
        self.assertEqual([SEED in runs[0], BIND in runs[1]], [True, True])


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
        source = {"knowledge_source_name": "demo-scenario-ontology", "workspace_id": WORKSPACE, "ontology_id": ONTOLOGY}
        body = {"activity": [{"type": "fabricOntology", "knowledgeSourceName": "demo-scenario-ontology"}],
                "references": [{"type": "fabricOntology", "workspaceId": WORKSPACE, "ontologyId": ONTOLOGY,
                                "sourceData": {"fabricRawData": "AssetName,SiteName\nPump B,North Works"}}]}
        self.assertEqual(deploy.ontology_evidence(body, source, deploy.EXPECTED_FACTS)[0], [])
        wrong = json.loads(json.dumps(body))
        wrong["references"][0]["ontologyId"] = LAKEHOUSE
        self.assertIn("no fabricOntology reference for the DEV workspace and ontology",
                      deploy.ontology_evidence(wrong, source, deploy.EXPECTED_FACTS)[0])
        snapshot = {"activity": [{"type": "indexedOneLake"}], "references": [{"type": "indexedOneLake"}]}
        self.assertIn("no fabricOntology activity for demo-scenario-ontology",
                      deploy.ontology_evidence(snapshot, source, deploy.EXPECTED_FACTS)[0])
        self.assertEqual(deploy.document_evidence(snapshot, "demo-documents")[0], [])

    def test_retrieval_sends_the_user_token_for_the_live_source(self):
        root = make_root(self)
        http = FakeHttp()
        source = {"knowledge_source_name": "ks", "workspace_id": WORKSPACE, "ontology_id": ONTOLOGY}
        http.add("POST", r"/retrieve\?api-version=", reply(body={
            "activity": [{"type": "fabricOntology", "knowledgeSourceName": "ks"}],
            "references": [{"type": "fabricOntology", "workspaceId": WORKSPACE, "ontologyId": ONTOLOGY,
                            "sourceData": {"fabricRawData": "Pump B,North Works"}}]}))
        ctx = make_context(self, root, FakeShell([TOKEN_RULE]), http)
        deploy.prove_retrieval(ctx, "https://srch.search.windows.net/knowledgebases/kb/retrieve?api-version=x", "q",
                               {"kind": "fabricOntology", "knowledgeSourceName": "ks"},
                               lambda body: deploy.ontology_evidence(body, source, deploy.EXPECTED_FACTS))
        _, _, body, headers = http.calls[0]
        self.assertEqual(body["knowledgeSourceParams"][0]["kind"], "fabricOntology")
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(headers["x-ms-query-source-authorization"], TOKEN)

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
                 (terraform("console"), deploy.Result(0, json.dumps(json.dumps({"fabric_git_enabled": False})))),
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
    return args[:2] == ["az", "login"]


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
        requested = {call[call.index("--resource" if "--resource" in call else "--scope") + 1] for call in probes}
        self.assertEqual(requested, {deploy.ARM_API + "/", deploy.GRAPH_API, deploy.FABRIC_API, "api://client/.default",
                                     deploy.SEARCH_RESOURCE, deploy.FOUNDRY_RESOURCE})
        self.assertTrue(all(call[call.index("--tenant") + 1] == TENANT for call in probes))
        for expected in ("GitHub CLI for example/demo-repo", f"Azure CLI in tenant {TENANT}", "Microsoft Foundry"):
            self.assertIn(expected, summary)
        # No Power Platform or Managed Runtime CLI is involved any more.
        self.assertFalse(ctx.shell.matching(lambda args: args[0] in ("dotnet", "ms", "pac")))

    def test_missing_sign_ins_run_back_to_back_with_codes_only_on_stderr(self):
        signed, seen = set(), {}

        def signed_in(name, result):
            return lambda args, options: result if name in signed else deploy.Result(1, "", "not signed in")

        def sign_in(name):
            def run(args, options):
                signed.add(name)
                seen[name] = options
                return deploy.Result(0)
            return run

        ctx = self.context([
            (command("az", "account", "show"), signed_in("az", deploy.Result(0, json.dumps(ACCOUNT)))),
            (command("az", "login"), sign_in("az")),
            (command("az", "account", "set"), deploy.Result(0)),
            TOKEN_RULE,
        ])
        deploy.phase_signin(ctx)
        self.assertEqual(ctx.shell.matching(lambda args: signing_in(args) or args[:3] == ["az", "account", "set"]), [
            ["az", "login", "--use-device-code"],
            ["az", "account", "set", "--subscription", self.SUBSCRIPTION],
        ])
        self.assertTrue(all(options["stream"] and not options["interactive"] for options in seen.values()))
        self.assertEqual(seen["az"]["env"]["AZURE_CORE_LOGIN_EXPERIENCE_V2"], "off")
        self.assertTrue(any(message.startswith("Sign-in 1 of 1: Azure CLI") for message in ctx.messages))

    def test_declining_lists_every_missing_sign_in_at_once(self):
        ctx = self.context([(command("az", "account", "show"), deploy.Result(1))])
        ctx.interactive, ctx.prompt = True, lambda text: "n\n"
        with self.assertRaises(deploy.Waiting) as stop:
            deploy.phase_signin(ctx)
        steps = " ".join(stop.exception.next_steps)
        for expected in ("Run: az login --use-device-code", f"Then: az account set --subscription {self.SUBSCRIPTION}"):
            self.assertIn(expected, steps)
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

    def test_the_foundry_token_is_needed_only_when_the_agent_or_app_runs(self):
        ctx = self.context(SIGNED_IN, deploy.PHASES[:deploy.PHASES.index("verify") + 1])
        self.assertEqual(deploy.sign_in_needs(ctx), {"foundry": False})
        deploy.phase_signin(ctx)
        probes = ctx.shell.matching(command("az", "account", "get-access-token"))
        self.assertNotIn(deploy.FOUNDRY_RESOURCE, [call[call.index("--resource") + 1] for call in probes
                                                   if "--resource" in call])
        for phases in (deploy.PHASES, ("signin", "app")):
            ctx.phases = phases
            self.assertEqual(deploy.sign_in_needs(ctx), {"foundry": True})

    def test_only_the_token_audiences_that_fail_are_signed_in_again(self):
        def token(args, options):
            fabric = "--resource" in args and args[args.index("--resource") + 1] == deploy.FABRIC_API
            return deploy.Result(1, "", "AADSTS50076") if fabric else TOKEN_RULE[1]

        account = (command("az", "account", "show"), deploy.Result(0, json.dumps(ACCOUNT)))
        ctx = self.context([account, (command("az", "account", "get-access-token"), token)])
        pending, _ = deploy.missing_sign_ins(ctx, {"foundry": True})
        self.assertEqual([(command, after) for _, command, after in pending], [(
            ["az", "login", "--use-device-code", "--tenant", TENANT, "--scope", deploy.FABRIC_API + "/.default"],
            (["az", "account", "set", "--subscription", self.SUBSCRIPTION],))])
        # Without a Resource Manager token the whole sign-in expired: one plain login, not one per audience.
        ctx = self.context([account, (command("az", "account", "get-access-token"), deploy.Result(1))])
        pending, _ = deploy.missing_sign_ins(ctx, {"foundry": True})
        self.assertEqual([command for _, command, _ in pending], [["az", "login", "--use-device-code", "--tenant", TENANT]])

    def test_a_service_principal_or_another_tenant_is_not_the_deploying_user(self):
        principal = dict(ACCOUNT, user={"type": "servicePrincipal"})
        ctx = self.context([(command("az", "account", "show"), deploy.Result(0, json.dumps(principal)))])
        with self.assertRaisesRegex(deploy.Blocked, "service principal"):
            deploy.azure_sign_in(ctx)
        other = "ffffffff-ffff-4fff-8fff-ffffffffffff"
        ctx = self.context([(command("az", "account", "show"), deploy.Result(0, json.dumps(ACCOUNT)))], tenant=other)
        self.assertEqual(deploy.azure_sign_in(ctx)[2], ["az", "login", "--use-device-code", "--tenant", other])

    def test_teardown_signs_in_only_to_the_audiences_it_will_use(self):
        ctx = self.context([], ("signin", "destroy"))
        self.assertEqual(deploy.sign_in_needs(ctx), {"foundry": False})
        ctx.state.section("agent")["name"] = "demo-assistant"
        self.assertEqual(deploy.sign_in_needs(ctx), {"foundry": True})
        ctx.state.section("destroy").update(runtime_deleted_at="earlier")
        self.assertEqual(deploy.sign_in_needs(ctx), {"foundry": False})
        # Teardown deletes the registration behind the deployment scope, so a resumed `down` never requires it.
        ctx.state.section("consent")["login_scope"] = "api://client/.default"
        self.assertNotIn("the Fabric deployment scope",
                         [label for label, _ in deploy.token_audiences(ctx, {"foundry": False})])
        ctx.phases = deploy.PHASES
        self.assertIn("the Fabric deployment scope",
                      [label for label, _ in deploy.token_audiences(ctx, {"foundry": False})])

    def test_the_last_sign_in_round_is_checked_before_giving_up(self):
        logins = []

        def show(args, options):
            return deploy.Result(0, json.dumps(ACCOUNT)) if len(logins) == 3 else deploy.Result(1, "", "not signed in")

        ctx = self.context([(command("az", "account", "show"), show), TOKEN_RULE,
                            (command("az", "account", "set"), deploy.Result(0)),
                            (command("az", "login"), lambda args, options: logins.append(args) or deploy.Result(0))])
        self.assertIn("Azure CLI in tenant", deploy.phase_signin(ctx))
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
            ("agent", "Creating hosted agent demo-assistant failed with HTTP 403 PermissionDenied: The principal lacks "
                      "Microsoft.CognitiveServices/accounts/AIServices/agents/write", ("Foundry User",)),
            ("app", "Building web:abc in crdemo failed (exit 1).\nAuthorizationFailed: The client 'u' does not have "
                    "authorization to perform action 'Microsoft.ContainerRegistry/registries/listBuildSourceUploadUrl/action' "
                    "over scope 'x'", ("Contributor (or Owner) on subscription sub-1",)),
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
            ("app", "Building web:abc in crdemo failed (exit 1).\nnpm ERR! 403 403 Forbidden - GET "
                    "https://registry.npmjs.org/react"),
            ("agent", "The hosted agent's agent-ontology answer is not verified: no scenario_ontology tool call"),
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


class StateAndCliTests(unittest.TestCase):
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
        self.assertFalse(shell.matching(lambda args: args[0] in ("az", "dotnet", "ms")))

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
        with self.assertRaises(SystemExit) as stop, contextlib.redirect_stderr(io.StringIO()):
            deploy.parse_arguments(["run", "platform"])
        self.assertEqual(stop.exception.code, deploy.USAGE)

    def test_plan_approvals_need_a_hash_and_actions_take_none(self):
        self.assertEqual(deploy.parse_arguments(["up", "--approve", "platform=ABCDEF012345"]).approve,
                         {"platform": "abcdef012345"})
        self.assertEqual(deploy.parse_arguments(["up", "--approve", "agent-deploy"]).approve, {"agent-deploy": True})
        for value in ("platform", "platform=xyz", "agent-deploy=1", "agent-create", "app-register", "unknown"):
            with self.assertRaises(SystemExit) as stop, contextlib.redirect_stderr(io.StringIO()):
                deploy.parse_arguments(["up", "--approve", value])
            self.assertEqual(stop.exception.code, deploy.USAGE)


AGENT_TREE, APP_TREE = "a" * 40, "b" * 40
REGISTRY = {"id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-demo/providers/Microsoft.ContainerRegistry/"
                  "registries/crdemo", "name": "crdemo", "login_server": "crdemo.azurecr.io"}
FABRIC_SERVER = f"https://api.fabric.microsoft.com/v1/mcp/dataPlane/workspaces/{WORKSPACE}/items/{ONTOLOGY}/ontologyEndpoint"
KB_ENDPOINT = "https://srch.search.windows.net/knowledgebases/demo-knowledge/mcp?api-version=2026-08-01-preview"
WEB_URL = "https://ca-demo-web.happysky-1234.centralus.azurecontainerapps.io"
IDENTITY_ID = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-demo/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-demo-web"
WEB_CLIENT, WEB_PRINCIPAL, API_PRINCIPAL = ("c1111111-1111-4111-8111-111111111111", "c2222222-2222-4222-8222-222222222222",
                                            "c3333333-3333-4333-8333-333333333333")
RUNTIME_OUTPUTS = {
    "hosted_agent": {"name": "demo-assistant", "toolbox_name": "demo-tools", "project_endpoint": PROJECT,
                     "responses_url": f"{PROJECT}/agents/demo-assistant/endpoint/protocols/openai/responses?api-version=v1",
                     "toolbox_endpoint": f"{PROJECT}/toolboxes/demo-tools/mcp?api-version=v1"},
    "container_registry": REGISTRY,
    "model_deployments": {"chat": "chat", "embedding": "embedding"},
    "knowledge_base_mcp_endpoint": KB_ENDPOINT,
    "knowledge_base_connection_id": "/subscriptions/x/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/"
                                    "ai-demo/projects/demo-project/connections/knowledge-base",
    "fabric_iq_connections": {"scenario": {"id": "fabric-id", "name": "fabric-scenario", "server_url": FABRIC_SERVER}},
    "web_app": {"name": "ca-demo-web", "url": WEB_URL, "environment_id": ENVIRONMENT_ID, "identity_id": IDENTITY_ID,
                "client_id": WEB_CLIENT, "service_principal_object_id": WEB_PRINCIPAL, "tenant_id": TENANT,
                "scope": "https://ai.azure.com/user_impersonation", "api_principal_object_id": API_PRINCIPAL,
                "api_permissions_url": "https://portal.azure.com/#web"},
}


def completed(*items, text):
    return {"id": "caresp_1", "object": "response", "status": "completed", "output": [
        *items, {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]}


def tool_turn(name, output, text):
    return completed({"type": "function_call", "name": name, "call_id": "call_1", "arguments": "{}", "status": "completed"},
                     {"type": "function_call_output", "call_id": "call_1", "output": output}, text=text)


def agent_replies(ontology_output="Pump B (Maintenance) is at North Works.", document_output="starter.txt: Git stores"):
    def respond(url, body):
        question = body["input"]
        if question.startswith("connection proof "):
            return reply(body=completed(text=deploy.hosted.PROOF_PREFIX + question))
        if question == deploy.ONTOLOGY_QUESTION:
            return reply(body=tool_turn("scenario_ontology___ask_ontology", ontology_output,
                                        "Pump B is in Maintenance at North Works. (Source: ontology)"))
        return reply(body=tool_turn("knowledge_base___knowledge_base_retrieve", document_output,
                                    "Git stores definitions. (Source: starter.txt)"))
    return respond


class AgentAndAppTests(unittest.TestCase):
    def context(self, rules=(), *, built=False, **overrides):
        state = {"built": set(["scenario-assistant:" + AGENT_TREE[:16], "web:" + APP_TREE[:16]] if built else [])}

        def show(args, options):
            image = args[args.index("--image") + 1]
            return deploy.Result(0) if image in state["built"] else deploy.Result(1, "", f"{image} not found")

        def build(args, options):
            state["built"].add(args[args.index("--image") + 1])
            return deploy.Result(0, "build log")

        shell = FakeShell([*rules, TOKEN_RULE, *GIT_RULES,
                           (command("git", "rev-parse", f"HEAD:{deploy.AGENT_SOURCE}"), deploy.Result(0, AGENT_TREE)),
                           (command("git", "rev-parse", f"HEAD:{deploy.APP_SOURCE}"), deploy.Result(0, APP_TREE)),
                           (command("az", "acr", "repository", "show"), show),
                           (command("az", "acr", "build"), build)])
        ctx = make_context(self, make_root(self), shell, **overrides)
        ctx.state.section("target").update(tenant_id=TENANT, subscription_id=SUBSCRIPTION)
        ctx.cache["outputs"] = json.loads(json.dumps(RUNTIME_OUTPUTS))
        ctx.cache["variables"].update(github_branch_name="main", project_name="demo")
        return ctx

    def rendered(self, ctx, image=f"crdemo.azurecr.io/scenario-assistant:{AGENT_TREE[:16]}"):
        definition = deploy.hosted.render(ctx.root / deploy.AGENT_SOURCE / "hosted-agent.json", {
            "chat_deployment": "chat", "toolbox_endpoint": RUNTIME_OUTPUTS["hosted_agent"]["toolbox_endpoint"],
            "fabric_iq_server_url": FABRIC_SERVER, "fabric_iq_connection": "fabric-scenario",
            "knowledge_base_mcp_endpoint": KB_ENDPOINT, "knowledge_base_connection": "knowledge-base"})
        return definition, deploy.hosted.agent_definition(definition["agent"], image)

    def agent_http(self, ctx, *, existing=False, status="active", responses=None):
        definition, desired = self.rendered(ctx)
        http = FakeHttp()
        toolbox = reply(body={"name": "demo-tools", "default_version": "1"})
        latest = {"version": "1", "status": status, "definition": desired}
        http.add("GET", r"/toolboxes/demo-tools\?api-version=v1$", *([toolbox] if existing else [reply(404), toolbox]))
        http.add("GET", r"/toolboxes/demo-tools/versions/1\?api-version=v1$", reply(body=definition["toolbox"]))
        http.add("POST", r"/toolboxes/demo-tools/versions\?api-version=v1$", reply(body={"version": "1"}))
        http.add("GET", r"/agents/demo-assistant\?api-version=v1$",
                 reply(body={"name": "demo-assistant", "versions": {"latest": latest}}) if existing else reply(404))
        http.add("POST", rf"{re.escape(PROJECT)}/agents\?api-version=v1$",
                 reply(body={"name": "demo-assistant", "versions": {"latest": dict(latest, status="creating")}}))
        http.add("POST", r"/agents/demo-assistant/versions\?api-version=v1$", reply(body=dict(latest, version="2")))
        http.add("GET", r"/agents/demo-assistant/versions/\d+\?api-version=v1$",
                 reply(body={"status": "creating"}),
                 reply(body={"status": status, "instance_identity": {"principal_id": "agent-principal"}}))
        http.add("POST", r"/endpoint/protocols/openai/responses\?api-version=v1$", responses or agent_replies())
        return http

    def test_the_agent_is_published_once_after_approval_and_proved_as_the_user(self):
        ctx = self.context()
        ctx.http = self.agent_http(ctx)
        with self.assertRaises(deploy.NeedsApproval) as stop:
            deploy.phase_agent(ctx)
        description = stop.exception.approval["description"]
        for fragment in ("build image scenario-assistant:" + AGENT_TREE[:16], "toolbox demo-tools",
                         "hosted agent demo-assistant"):
            self.assertIn(fragment, description)
        self.assertFalse(ctx.shell.matching(command("az", "acr", "build")))
        self.assertFalse(ctx.http.posts(r"/toolboxes|/agents"))

        ctx.args.approve = {"agent-deploy": True}
        ctx.http = self.agent_http(ctx)
        self.assertIn("ready as you", deploy.phase_agent(ctx))
        build, = ctx.shell.matching(command("az", "acr", "build"))
        self.assertEqual(build[build.index("--platform") + 1], "linux/amd64")
        self.assertEqual(build[-2], deploy.AGENT_SOURCE)
        (toolbox_url, toolbox), = ctx.http.posts(r"/toolboxes/demo-tools/versions")
        labels = [tool["server_label"] for tool in toolbox["tools"]]
        self.assertEqual(labels, [deploy.ONTOLOGY_TOOL, deploy.KNOWLEDGE_TOOL])
        self.assertEqual(toolbox["tools"][0]["project_connection_id"], "fabric-scenario")
        self.assertEqual(toolbox["tools"][1]["server_url"], KB_ENDPOINT)
        (_, created), = ctx.http.posts(rf"{re.escape(PROJECT)}/agents\?")
        definition = created["definition"]
        self.assertEqual(definition["container_configuration"]["image"],
                         f"crdemo.azurecr.io/scenario-assistant:{AGENT_TREE[:16]}")
        self.assertEqual(definition["environment_variables"]["TOOLBOX_ENDPOINT"],
                         RUNTIME_OUTPUTS["hosted_agent"]["toolbox_endpoint"])
        self.assertEqual(definition["environment_variables"]["AZURE_AI_MODEL_DEPLOYMENT_NAME"], "chat")
        receipts = ctx.state.section("agent")
        self.assertEqual(receipts["version"]["version"], "1")
        self.assertEqual(receipts["identity_principal_id"], "agent-principal")
        self.assertTrue((ctx.root / receipts["ontology"]["response"]).is_file())
        # Tokens only go to the Foundry project.
        self.assertTrue(all(url.startswith(PROJECT) for _, url, _, headers in ctx.http.calls if headers))

        # A rerun with everything live and unchanged only re-proves readiness.
        ctx.args.approve = {}
        ctx.http = self.agent_http(ctx, existing=True)
        deploy.phase_agent(ctx)
        self.assertFalse(ctx.http.posts(r"/toolboxes|/agents\?|/agents/demo-assistant/versions"))
        self.assertEqual(len(ctx.shell.matching(command("az", "acr", "build"))), 1)

    def test_a_changed_definition_creates_one_new_version_and_a_failed_one_needs_retry_failed(self):
        ctx = self.context(built=True, approve={"agent-deploy": True})
        ctx.http = self.agent_http(ctx, existing=True)
        definition, _ = self.rendered(ctx, image="crdemo.azurecr.io/scenario-assistant:older")
        ctx.http.routes = [route for route in ctx.http.routes if not route[1].pattern.endswith(r"/agents/demo-assistant\?api-version=v1$")]
        ctx.http.add("GET", r"/agents/demo-assistant\?api-version=v1$", reply(body={"versions": {"latest": {
            "version": "1", "status": "active", "definition": deploy.hosted.agent_definition(
                definition["agent"], "crdemo.azurecr.io/scenario-assistant:older")}}}))
        deploy.phase_agent(ctx)
        self.assertEqual(len(ctx.http.posts(r"/agents/demo-assistant/versions")), 1)
        self.assertEqual(ctx.state.section("agent")["version"]["version"], "2")

        ctx = self.context(built=True)
        ctx.http = self.agent_http(ctx, existing=True, status="failed")
        with self.assertRaisesRegex(deploy.Blocked, "is failed") as stop:
            deploy.phase_agent(ctx)
        self.assertIn("--retry-failed", " ".join(stop.exception.next_steps))
        self.assertFalse(ctx.http.posts(r"/agents"))
        ctx.args.retry_failed, ctx.args.approve = True, {"agent-deploy": True}
        ctx.http = self.agent_http(ctx, existing=True, status="failed")
        with self.assertRaisesRegex(deploy.Blocked, "is failed"):
            deploy.phase_agent(ctx)
        self.assertEqual(len(ctx.http.posts(r"/agents/demo-assistant/versions")), 1)

    def test_a_provisioning_version_is_observed_not_recreated(self):
        ctx = self.context(built=True)
        ctx.http = self.agent_http(ctx, existing=True, status="creating")
        ctx.deadline = ctx.clock() + 40
        with self.assertRaisesRegex(deploy.Waiting, "still creating"):
            deploy.phase_agent(ctx)
        self.assertFalse(ctx.http.posts(r"/agents|/toolboxes"))

    def test_only_committed_published_source_is_built(self):
        ctx = self.context([(command("git", "status", "--porcelain"), deploy.Result(0, " M agents/scenario-assistant/main.py"))])
        ctx.http = self.agent_http(ctx)
        with self.assertRaisesRegex(deploy.Blocked, "uncommitted changes"):
            deploy.phase_agent(ctx)
        ctx = self.context([(command("git", "ls-remote"), deploy.Result(0, f"{'d' * 40}\trefs/heads/main\n"))])
        ctx.http = self.agent_http(ctx)
        with self.assertRaisesRegex(deploy.Blocked, "not the published origin/main"):
            deploy.phase_agent(ctx)
        self.assertFalse(ctx.shell.matching(command("az", "acr")))

    def test_a_correct_sounding_answer_without_live_tool_evidence_is_not_readiness(self):
        ctx = self.context(built=True)
        ctx.http = self.agent_http(ctx, existing=True, responses=agent_replies(ontology_output="no rows"))
        with self.assertRaisesRegex(deploy.Blocked, "tool output lacks Pump B, North Works"):
            deploy.phase_agent(ctx)
        self.assertTrue((ctx.root / ".artifacts" / "deploy" / "evidence" / "agent-ontology-failed.json").is_file())

        def wrong_proof(url, body):
            return reply(body=completed(text=deploy.hosted.PROOF_PREFIX + "connection proof older"))

        ctx = self.context(built=True)
        ctx.http = self.agent_http(ctx, existing=True, responses=wrong_proof)
        with self.assertRaisesRegex(deploy.Blocked, "fresh challenge"):
            deploy.phase_agent(ctx)

    def app_http(self, *, granted=False, deployed=False, state="Succeeded", config=None, proof=True):
        http = FakeHttp()
        grant = {"consentType": "AllPrincipals", "resourceId": API_PRINCIPAL, "scope": "user_impersonation", "id": "g1"}
        grants = [reply(body={"value": [grant]})] if granted else [reply(body={"value": []}), reply(body={"value": []}),
                                                                   reply(body={"value": [grant]})]
        http.add("GET", rf"/servicePrincipals/{WEB_PRINCIPAL}/oauth2PermissionGrants$", *grants)
        http.add("POST", r"/v1\.0/oauth2PermissionGrants$", reply(201, {}))
        http.add("GET", re.escape(ENVIRONMENT_ID) + r"\?", reply(body={"location": "centralus"}))
        live = {"properties": {"provisioningState": state, "latestRevisionName": "r2", "latestReadyRevisionName": "r2"}}
        if deployed:
            live = deploy.container_app_body("centralus", RUNTIME_OUTPUTS["web_app"], REGISTRY,
                                             RUNTIME_OUTPUTS["hosted_agent"],
                                             f"crdemo.azurecr.io/web:{APP_TREE[:16]}", "demo")
            live["properties"].update(provisioningState=state, latestRevisionName="r2", latestReadyRevisionName="r2")
        http.add("GET", re.escape(APP_ID) + r"\?", *([reply(body=live)] if deployed else [reply(404), reply(body=live)]))
        http.add("PUT", re.escape(APP_ID) + r"\?", reply(201, {}))
        http.add("GET", re.escape(WEB_URL) + "/healthz$", reply(503), reply(200))
        http.add("GET", re.escape(WEB_URL) + "/config.json$", reply(body=config or {
            "clientId": WEB_CLIENT, "tenantId": TENANT, "scope": "https://ai.azure.com/user_impersonation",
            "agentName": "demo-assistant"}))

        def relay(url, body):
            text = deploy.hosted.PROOF_PREFIX + (body["input"] if proof else "connection proof older")
            return reply(body=completed(text=text))

        http.add("POST", re.escape(WEB_URL) + "/api/responses$", relay)
        return http

    def test_the_app_is_consented_built_deployed_and_relays_a_fresh_challenge_as_the_user(self):
        ctx = self.context()
        ctx.http = self.app_http()
        with self.assertRaises(deploy.NeedsApproval) as stop:
            deploy.phase_app(ctx)
        for fragment in ("grant tenant-wide consent", "build image web:" + APP_TREE[:16], "create Container App ca-demo-web"):
            self.assertIn(fragment, stop.exception.approval["description"])
        self.assertFalse([call for call in ctx.http.calls if call[0] in ("POST", "PUT")])

        ctx.args.approve = {"app-deploy": True}
        ctx.http = self.app_http()
        self.assertIn(WEB_URL, deploy.phase_app(ctx))
        (_, grant), = ctx.http.posts(r"/oauth2PermissionGrants$")
        self.assertEqual(grant, {"clientId": WEB_PRINCIPAL, "consentType": "AllPrincipals", "resourceId": API_PRINCIPAL,
                                 "scope": "user_impersonation"})
        build, = ctx.shell.matching(command("az", "acr", "build"))
        self.assertEqual(build[-2], deploy.APP_SOURCE)
        (_, _, body, _), = [call for call in ctx.http.calls if call[0] == "PUT"]
        container = body["properties"]["template"]["containers"][0]
        self.assertEqual(container["image"], f"crdemo.azurecr.io/web:{APP_TREE[:16]}")
        env = {item["name"]: item["value"] for item in container["env"]}
        self.assertEqual(env["AGENT_RESPONSES_URL"], RUNTIME_OUTPUTS["hosted_agent"]["responses_url"])
        self.assertEqual(env["ENTRA_CLIENT_ID"], WEB_CLIENT)
        self.assertNotIn("secret", json.dumps(body).lower())
        self.assertEqual(body["properties"]["configuration"]["registries"],
                         [{"server": "crdemo.azurecr.io", "identity": IDENTITY_ID}])
        self.assertEqual(ctx.state.section("app")["revision"], "r2")

    def test_an_up_to_date_app_is_only_verified(self):
        ctx = self.context(built=True)
        ctx.http = self.app_http(granted=True, deployed=True)
        deploy.phase_app(ctx)
        self.assertFalse([call for call in ctx.http.calls if call[0] == "PUT"])
        self.assertFalse(ctx.http.posts(r"/oauth2PermissionGrants$"))
        self.assertFalse(ctx.shell.matching(command("az", "acr", "build")))

    def test_failed_revisions_foreign_settings_and_wrong_relays_are_not_readiness(self):
        for options, pattern in (({"state": "Failed"}, "Container App is Failed"),
                                 ({"config": {"clientId": "other"}}, "different sign-in settings"),
                                 ({"proof": False}, "did not relay this check's fresh challenge")):
            with self.subTest(options=options):
                ctx = self.context(built=True)
                ctx.http = self.app_http(granted=True, deployed=True, **options)
                with self.assertRaisesRegex(deploy.Blocked, pattern):
                    deploy.phase_app(ctx)
                self.assertNotIn("verified_at", ctx.state.section("app"))

    def test_manual_grants_leave_the_web_sign_in_consent_to_an_administrator(self):
        ctx = self.context(built=True, manual_grants=True, approve={"app-deploy": True})
        ctx.http = self.app_http(deployed=True)
        with self.assertRaisesRegex(deploy.Waiting, "--manual-grants") as stop:
            deploy.phase_app(ctx)
        self.assertIn("https://portal.azure.com/#web", " ".join(stop.exception.next_steps))
        self.assertFalse(ctx.http.posts(r"/oauth2PermissionGrants$"))

    def test_app_targets_must_stay_inside_the_deployment(self):
        for change, pattern in (({"url": "https://example.com"}, "not a Container Apps address"),
                                ({"environment_id": "/subscriptions/other/resourceGroups/rg/providers/Microsoft.App/"
                                                    "managedEnvironments/x"}, "not a Container Apps target"),
                                ({"tenant_id": "ffffffff-ffff-4fff-8fff-ffffffffffff"}, "tenant differs")):
            with self.subTest(change=change):
                ctx = self.context(built=True)
                ctx.cache["outputs"]["web_app"].update(change)
                ctx.http = self.app_http()
                with self.assertRaisesRegex(deploy.Blocked, pattern):
                    deploy.phase_app(ctx)
                self.assertFalse([call for call in ctx.http.calls if call[0] in ("POST", "PUT")])

    def test_the_final_report_tells_the_user_how_to_try_the_app(self):
        steps = " ".join(deploy.APP_NEXT)
        for fragment in ("sign in", "Check connection", "Pump B", "Foundry Agent Consumer"):
            self.assertIn(fragment, steps)


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
            "knowledge_source_status_url": "https://demo.search.windows.net/knowledgesources/docs/status?api-version=test",
            "knowledge_base_name": "knowledge",
            "knowledge_source_name": "docs",
            "ontology_knowledge_sources": {"scenario": {"knowledge_source_name": "ontology"}},
        }
        with mock.patch.object(deploy, "wait_ingestion", return_value={}), \
                mock.patch.object(deploy, "prove_retrieval", return_value={}) as retrieval:
            with self.assertRaisesRegex(deploy.Blocked, "Terraform outputs are missing: web_iq"):
                deploy.phase_verify(ctx)
        self.assertEqual(retrieval.call_count, 2)
        self.assertNotIn("dev_ready_at", ctx.state.section("verify"))

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

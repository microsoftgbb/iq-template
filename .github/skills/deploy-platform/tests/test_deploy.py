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


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import configure_m365_demo  # noqa: E402
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

    def request(self, method, url, headers=None, body=None, timeout=120, raw=None):
        self.calls.append((method, url, body if raw is None else raw, dict(headers or {})))
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


def studio(subcommand):
    return lambda args: len(args) > 2 and args[1].endswith("studio_agent.py") and args[2] == subcommand


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
    (root / "app" / "src").mkdir(parents=True)
    # Not copied: a deployed checkout legitimately sets its own schema here.
    (root / "app" / "src" / "config.ts").write_text(
        "// The only app setting: the schema name passed to `pac copilot init --schema-name`.\n"
        "export const STUDIO_AGENT_SCHEMA = ''\n", encoding="utf-8")
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
    ctx.start("test")
    return ctx


def tmdl(bound, workspace=WORKSPACE):
    def part(path, text):
        return {"path": path, "payload": base64.b64encode(text.encode()).decode(), "payloadType": "InlineBase64"}

    parts = []
    for table in deploy.SAMPLE_TABLES:
        text = f"table {table}\n"
        if bound:
            text += (f"\tpartition {table} = entity\n\t\tmode: directLake\n\n"
                     f"\t\tannotation ONT_WorkspaceId = {workspace}\n\n"
                     f"\t\tannotation ONT_ItemId = {LAKEHOUSE}\n\n"
                     "\t\tannotation ONT_ItemKind = Lakehouse\n")
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

    def test_no_changes_leaves_nothing_behind(self):
        root = make_root(self)
        ctx = make_context(self, root, FakeShell(plan_rules([], plan_code=0)))
        self.assertEqual(deploy.converge(ctx, "platform"), "unchanged")
        self.assertEqual(list((ctx.state.directory / "plans").iterdir()), [])


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

    def test_binding_checks_target_local_pins_not_placeholders(self):
        decode = deploy.base64.b64decode

        def parts(definition):
            return {part["path"]: decode(part["payload"]).decode() for part in definition["definition"]["parts"]}

        self.assertEqual(deploy.binding_problems(parts(tmdl(True)), WORKSPACE, LAKEHOUSE), [])
        self.assertIn("demo_sites has no partition yet", deploy.binding_problems(parts(tmdl(False)), WORKSPACE, LAKEHOUSE))
        placeholder = deploy.binding_problems(parts(tmdl(True, "00000000-0000-0000-0000-000000000000")), WORKSPACE, LAKEHOUSE)
        self.assertIn("demo_assets is not pinned to the DEV workspace", placeholder)


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
        ctx.cache["variables"] = {"knowledge_lakehouse_id": None}
        ctx.cache["outputs"].update(dev_workspace_id=WORKSPACE, dev_workspace_name="demo-dev",
                                    github_connect_request=REQUEST)
        self.assertIn("Search identity " + "p" * 8, deploy.phase_platform(ctx))

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
                 (terraform("output"), outputs)]
        http = FakeHttp()
        http.add("GET", r"/searchServices/srch-demo\?", search_service("o" * 8), search_service(), search_service("n" * 8))
        http.add("PATCH", r"/searchServices/srch-demo\?", reply(500))
        ctx = self.search_context(http, rules, yes=True, allow_replace=True)
        ctx.initialized = True
        ctx.cache["variables"] = {"search_sku": "serverless"}
        self.assertEqual(deploy.converge_knowledge(ctx), "applied")
        plans = ctx.shell.matching(terraform("plan"))
        self.assertEqual(["-var=search_identity_ready=false" in plan for plan in plans], [False, True, False])
        applied = [Path(args[-1]).name for args in ctx.shell.matching(terraform("apply"))]
        self.assertEqual(len(applied), 2, "the unstaged plan must never be applied")
        self.assertEqual([method for method, *_ in http.calls].count("PATCH"), 1)
        self.assertEqual(ctx.state.section("pending_plans"), {})
        ctx = self.search_context(FakeHttp(), plan_rules([replaced, grant]), yes=True, allow_replace=True)
        ctx.cache["outputs"] = {}
        ctx.cache["variables"] = {"search_sku": "basic"}
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

    def test_the_serverless_feature_is_registered_only_with_its_explicit_flag(self):
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
        ctx = self.search_context(FakeHttp(), rules)
        with self.assertRaisesRegex(deploy.Waiting, "Microsoft.Search/Serverless preview feature"):
            deploy.check_search_tier(ctx, "sub", values)
        self.assertFalse(ctx.shell.matching(command("az", "feature", "register")))
        ctx = self.search_context(FakeHttp(), rules, register_search_serverless=True)
        self.assertIn("Microsoft.Search re-registered", deploy.check_search_tier(ctx, "sub", values))
        self.assertEqual(len(ctx.shell.matching(command("az", "feature", "register"))), 1)
        self.assertIn("--wait", ctx.shell.matching(command("az", "provider", "register"))[0])
        # An interrupted earlier run left the feature registered: the flag still completes the provider step.
        ctx = self.search_context(FakeHttp(), rules, register_search_serverless=True)
        self.assertIn("Microsoft.Search re-registered", deploy.check_search_tier(ctx, "sub", values))
        self.assertFalse(ctx.shell.matching(command("az", "feature", "register")))
        self.assertEqual(len(ctx.shell.matching(command("az", "provider", "register"))), 1)
        ctx = self.search_context(FakeHttp(), rules)
        self.assertTrue(deploy.check_search_tier(ctx, "sub", values).endswith("Microsoft.Search/Serverless registered"))
        self.assertFalse(ctx.shell.matching(command("az", "provider", "register")))


class ConsentTests(unittest.TestCase):
    AUTH = {"tenant_id": TENANT, "client_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "service_principal_object_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "login_scope": "api://bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/.default",
            "api_permissions_url": "https://portal.azure.com/#approve",
            "delegated_scopes": ["Capacity.ReadWrite.All", "Item.Execute.All", "Workspace.ReadWrite.All"]}

    def consent_context(self, http, shell_rules=(), **overrides):
        root = make_root(self)
        shell = FakeShell([*shell_rules, TOKEN_RULE,
                           (command("az", "ad", "sp", "show"), deploy.Result(0, json.dumps({"id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd"})))])
        ctx = make_context(self, root, shell, http, **overrides)
        ctx.cache["outputs"] = {"fabric_user_auth": self.AUTH}
        ctx.state.section("target").update(tenant_id=TENANT, subscription_id="sub", user_object_id="user")
        return ctx, shell

    def test_missing_consent_waits_for_an_administrator(self):
        http = FakeHttp()
        http.add("GET", r"oauth2PermissionGrants$", reply(body={"value": []}))
        ctx, shell = self.consent_context(http)
        with self.assertRaises(deploy.Waiting) as stop:
            deploy.phase_consent(ctx)
        steps = " ".join(stop.exception.next_steps)
        self.assertIn("https://portal.azure.com/#approve", steps)
        self.assertIn("--grant-admin-consent", steps)
        self.assertFalse(shell.matching(command("az", "ad", "app", "permission")))

    def test_an_administrator_grants_exactly_the_declared_scopes(self):
        http = FakeHttp()
        granted = {"consentType": "AllPrincipals", "resourceId": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                   "scope": " ".join(self.AUTH["delegated_scopes"])}
        http.add("GET", r"oauth2PermissionGrants$", reply(body={"value": []}), reply(body={"value": [granted]}))
        for path in ("workspaces", "capacities", "connections"):
            http.add("GET", rf"/v1/{path}$", reply(body={"value": []}))
        grant = (command("az", "ad", "app", "permission", "grant"), deploy.Result(0))
        ctx, shell = self.consent_context(http, [grant], grant_admin_consent=True)
        deploy.phase_consent(ctx)
        call, = shell.matching(command("az", "ad", "app", "permission", "grant"))
        self.assertEqual(call[call.index("--scope") + 1], "Capacity.ReadWrite.All Item.Execute.All Workspace.ReadWrite.All")
        self.assertEqual(call[call.index("--consent-type") + 1], "AllPrincipals")
        self.assertEqual(call[call.index("--api") + 1], deploy.POWER_BI_APP_ID)

    def test_unreadable_grants_wait_for_an_explicit_confirmation(self):
        http = FakeHttp()
        http.add("GET", r"oauth2PermissionGrants$", reply(403, {"error": {"code": "Authorization_RequestDenied"}}))
        for path in ("workspaces", "capacities", "connections"):
            http.add("GET", rf"/v1/{path}$", reply(body={"value": []}))
        ctx, _ = self.consent_context(http)
        with self.assertRaises(deploy.Waiting) as stop:
            deploy.phase_consent(ctx)
        self.assertIn("--consent-confirmed", " ".join(stop.exception.next_steps))
        self.assertFalse([call for call in http.calls if "/v1/workspaces" in call[1]])
        ctx.args.consent_confirmed = True
        self.assertIn("consent granted", deploy.phase_consent(ctx))


DEMO_CLIENT = "ffffffff-ffff-4fff-8fff-ffffffffffff"
DEMO_SP = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
GRAPH_SP = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


class M365DemoTests(unittest.TestCase):
    OUTPUT = {"tenant_id": TENANT, "client_id": DEMO_CLIENT, "service_principal_object_id": DEMO_SP,
              "delegated_scopes": ["Policy.ReadWrite.ConditionalAccess", "User.Read", "offline_access"],
              "api_permissions_url": "https://portal.azure.com/#m365demo"}
    GRANTED = {"consentType": "AllPrincipals", "resourceId": GRAPH_SP,
               "scope": "Policy.ReadWrite.ConditionalAccess User.Read offline_access"}

    def demo_context(self, http, shell_rules=(), enabled=True, changes=(), output=None, **overrides):
        root = make_root(self)
        demo = output or self.OUTPUT
        shell = FakeShell([
            *shell_rules, TOKEN_RULE,
            (command("az", "ad", "sp", "show"), deploy.Result(0, json.dumps({"id": GRAPH_SP}))),
            (terraform("output"), deploy.Result(0, json.dumps({"m365_demo": {"value": demo}}))),
            *plan_rules(list(changes), plan_code=2 if changes else 0),
        ])
        ctx = make_context(self, root, shell, http, **overrides)
        ctx.initialized = True
        ctx.cache.update(variables={"m365_demo_enabled": enabled}, outputs={"m365_demo": demo})
        ctx.state.section("target").update(tenant_id=TENANT, subscription_id="sub", user_object_id="user")
        return ctx, shell

    def ready_http(self, grants=None):
        http = FakeHttp()
        http.add("GET", rf"/servicePrincipals/{DEMO_SP}/oauth2PermissionGrants$",
                 *(grants or [reply(body={"value": [self.GRANTED]})]))
        return http

    def test_the_phase_is_skipped_unless_requested(self):
        http = FakeHttp()
        ctx, shell = self.demo_context(http, enabled=False)
        self.assertIn("not enabled", deploy.phase_m365_demo(ctx))
        self.assertFalse(shell.matching(terraform("plan")))
        self.assertFalse(http.calls)
        self.assertNotIn("m365_demo", ctx.state.data)

    def test_with_m365_demo_sets_the_variable_and_plans_only_the_app_for_approval(self):
        helper = (lambda args: len(args) > 1 and str(args[1]).endswith("configure_m365_demo.py"), deploy.Result(0))
        changes = [("azuread_application.m365_demo[0]", ["create"]), ("azuread_service_principal.m365_demo[0]", ["create"])]
        ctx, shell = self.demo_context(FakeHttp(), [helper], enabled=False, changes=changes, with_m365_demo=True)
        with self.assertRaises(deploy.NeedsApproval) as stop:
            deploy.phase_m365_demo(ctx)
        self.assertEqual(len(shell.matching(helper[0])), 1)
        self.assertEqual(stop.exception.approval["key"], "m365-demo")
        self.assertEqual(stop.exception.approval["summary"]["counts"], {"create": 2})
        self.assertFalse(shell.matching(terraform("apply")))

    def test_the_plan_refuses_unrelated_changes(self):
        changes = [("azuread_application.m365_demo[0]", ["create"]), ("azapi_resource.search", ["update"])]
        ctx, shell = self.demo_context(FakeHttp(), changes=changes, yes=True)
        with self.assertRaisesRegex(deploy.Blocked, "azapi_resource.search"):
            deploy.phase_m365_demo(ctx)
        self.assertFalse(shell.matching(terraform("apply")))

    def test_a_registration_in_another_tenant_is_refused(self):
        output = dict(self.OUTPUT, tenant_id="12121212-1212-4212-8212-121212121212")
        http = self.ready_http()
        ctx, _ = self.demo_context(http, output=output)
        with self.assertRaisesRegex(deploy.Blocked, "tenant differs"):
            deploy.phase_m365_demo(ctx)
        self.assertFalse(http.calls)

    def test_missing_consent_waits_for_an_administrator(self):
        http = self.ready_http(grants=[reply(body={"value": []})])
        ctx, shell = self.demo_context(http)
        with self.assertRaises(deploy.Waiting) as stop:
            deploy.phase_m365_demo(ctx)
        steps = " ".join(stop.exception.next_steps)
        self.assertIn("https://portal.azure.com/#m365demo", steps)
        self.assertIn("--grant-m365-demo-consent", steps)
        self.assertFalse(shell.matching(command("az", "ad", "app", "permission")))

    def test_an_administrator_grants_exactly_the_declared_scopes(self):
        http = self.ready_http(grants=[reply(body={"value": []}), reply(body={"value": [self.GRANTED]})])
        grant = (command("az", "ad", "app", "permission", "grant"), deploy.Result(0))
        ctx, shell = self.demo_context(http, [grant], grant_m365_demo_consent=True)
        self.assertIn("consent granted", deploy.phase_m365_demo(ctx))
        call, = shell.matching(command("az", "ad", "app", "permission", "grant"))
        self.assertEqual(call[call.index("--id") + 1], DEMO_CLIENT)
        self.assertEqual(call[call.index("--api") + 1], deploy.MICROSOFT_GRAPH_APP_ID)
        self.assertEqual(call[call.index("--scope") + 1], "Policy.ReadWrite.ConditionalAccess User.Read offline_access")
        self.assertEqual(call[call.index("--consent-type") + 1], "AllPrincipals")
        receipt = ctx.state.section("m365_demo")
        self.assertEqual((receipt["client_id"], receipt["consent"]), (DEMO_CLIENT, "granted"))
        self.assertEqual(receipt["delegated_scopes"], ["Policy.ReadWrite.ConditionalAccess", "User.Read", "offline_access"])
        self.assertEqual(stat.S_IMODE(os.stat(ctx.state.path).st_mode), 0o600)

    def test_the_fabric_consent_flag_never_grants_the_demo_scopes(self):
        http = self.ready_http(grants=[reply(body={"value": []})])
        ctx, shell = self.demo_context(http, grant_admin_consent=True)
        with self.assertRaises(deploy.Waiting):
            deploy.phase_m365_demo(ctx)
        self.assertFalse(shell.matching(command("az", "ad", "app", "permission")))

    def test_unreadable_grants_leave_consent_unverified_without_blocking(self):
        http = self.ready_http(grants=[reply(403, {"error": {"code": "Authorization_RequestDenied"}})])
        ctx, _ = self.demo_context(http)
        self.assertIn("consent unverified", deploy.phase_m365_demo(ctx))
        self.assertEqual(ctx.state.section("m365_demo")["consent"], "unverified")

    def test_status_reports_the_receipt(self):
        root = make_root(self)
        ctx = make_context(self, root)
        ctx.state.section("m365_demo").update(client_id=DEMO_CLIENT, consent="granted")
        ctx.state.save()
        _, report = deploy.status_report(ctx)
        self.assertEqual(report["m365_demo"]["client_id"], DEMO_CLIENT)
        self.assertEqual(report["phases"]["m365-demo"]["status"], "not run")

    def test_the_phase_runs_after_verify_and_takes_a_plan_hash(self):
        self.assertLess(deploy.PHASES.index("verify"), deploy.PHASES.index("m365-demo"))
        self.assertLess(deploy.PHASES.index("m365-demo"), deploy.PHASES.index("agent"))
        args = deploy.parse_arguments(["up", "--with-m365-demo", "--through", "m365-demo",
                                       "--approve", "m365-demo=ABCDEF012345"])
        self.assertEqual((args.with_m365_demo, args.through, args.approve),
                         (True, "m365-demo", {"m365-demo": "abcdef012345"}))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            deploy.parse_arguments(["up", "--approve", "m365-demo"])


class ConfigureM365DemoTests(unittest.TestCase):
    def infra(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        infra = Path(directory.name)
        (infra / "terraform.tfvars").write_bytes(text.encode())
        return infra

    def test_it_appends_or_flips_the_one_setting_and_keeps_everything_else(self):
        self.assertEqual(configure_m365_demo.configure('project_name = "demo"\n'),
                         'project_name = "demo"\nm365_demo_enabled = true\n')
        self.assertEqual(configure_m365_demo.configure('a = 1'), 'a = 1\nm365_demo_enabled = true\n')
        self.assertEqual(configure_m365_demo.configure('a = 1\r\nm365_demo_enabled = false # off\r\n'),
                         'a = 1\r\nm365_demo_enabled = true # off\r\n')
        for text in ('m365_demo_enabled = true\nm365_demo_enabled = false\n', 'm365_demo_enabled = var.x\n'):
            with self.assertRaises(ValueError):
                configure_m365_demo.configure(text)

    def test_enable_writes_owner_only_once_and_refuses_ambiguous_inputs(self):
        infra = self.infra('project_name = "demo"\n')
        self.assertEqual(configure_m365_demo.enable(infra, dry_run=True), "would enable")
        self.assertNotIn("m365_demo_enabled", (infra / "terraform.tfvars").read_text())
        self.assertEqual(configure_m365_demo.enable(infra), "enabled")
        self.assertEqual(stat.S_IMODE(os.stat(infra / "terraform.tfvars").st_mode), 0o600)
        self.assertEqual(configure_m365_demo.enable(infra), "unchanged")
        (infra / "extra.auto.tfvars").write_text("x = 1\n")
        with self.assertRaisesRegex(ValueError, "auto-loaded"):
            configure_m365_demo.enable(infra)
        linked = self.infra("")
        (linked / "terraform.tfvars").unlink()
        (linked / "terraform.tfvars").symlink_to(infra / "terraform.tfvars")
        with self.assertRaisesRegex(ValueError, "symlinked"):
            configure_m365_demo.enable(linked)


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
            (command("git", "remote", "get-url", "origin"), deploy.Result(0, "https://github.com/example/template.git\n")),
            (command("gh", "repo", "view"), deploy.Result(0, json.dumps({"nameWithOwner": "example/template", "isTemplate": True}))),
        ])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            code = deploy.main(["up", "--through", "config", "--json"], root=root, shell=shell, http=FakeHttp(),
                               interactive=False)
        self.assertEqual(code, deploy.BLOCKED)
        self.assertIn("reusable template", json.loads(stdout.getvalue())["message"])

    def test_there_is_no_single_phase_entry_that_skips_earlier_gates(self):
        with self.assertRaises(SystemExit) as stop, contextlib.redirect_stderr(io.StringIO()):
            deploy.parse_arguments(["run", "platform"])
        self.assertEqual(stop.exception.code, deploy.USAGE)

    def test_plan_approvals_need_a_hash_and_actions_take_none(self):
        self.assertEqual(deploy.parse_arguments(["up", "--approve", "platform=ABCDEF012345"]).approve,
                         {"platform": "abcdef012345"})
        self.assertEqual(deploy.parse_arguments(["up", "--approve", "agent-create"]).approve, {"agent-create": True})
        for value in ("platform", "platform=xyz", "agent-create=1", "unknown"):
            with self.assertRaises(SystemExit) as stop, contextlib.redirect_stderr(io.StringIO()):
                deploy.parse_arguments(["up", "--approve", value])
            self.assertEqual(stop.exception.code, deploy.USAGE)


class AgentAndAppTests(unittest.TestCase):
    SCHEMA = "iq_demo_assistant"
    ENVIRONMENT = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"

    def agent_context(self, root, rules):
        shell = FakeShell([
            (command("dotnet", "tool", "restore"), deploy.Result(0)),
            (command("dotnet", "pac", "env", "who"), deploy.Result(0, "Org URL: https://org.crm.dynamics.com/\n")),
            (command("dotnet", "pac", "copilot", "push"), deploy.Result(0)),
            (command("dotnet", "pac", "copilot", "publish"), deploy.Result(0)),
            (studio("find"), deploy.Result(0, json.dumps({"status": "found", "bot_id": "bot"}))),
            (studio("render"), deploy.Result(0, json.dumps({"status": "rendered"}))),
            *rules,
        ])
        ctx = make_context(self, root, shell, environment_id=self.ENVIRONMENT,
                           environment_url="https://org.crm.dynamics.com")
        ctx.state.section("target").update(tenant_id=TENANT)
        ctx.cache["outputs"] = {"studio_agent_schema_name": self.SCHEMA, "studio_agent_display_name": "demo assistant",
                                "knowledge_base_name": "demo-knowledge", "dev_workspace_id": WORKSPACE,
                                "search_endpoint": "https://srch.search.windows.net",
                                "ontology_knowledge_sources": {"scenario": {"ontology_id": ONTOLOGY}}}
        return ctx, shell

    def test_the_agent_waits_for_the_owner_to_fix_their_connections(self):
        root = make_root(self)
        (root / ".artifacts" / "studio" / self.SCHEMA / ".mcs").mkdir(parents=True)
        plan = {"status": "plan", "sign_in": "https://make.powerapps.com/environments/env/connections",
                "connections": {"shared_fabriciqmcpserver": {"action": "sign-in", "connection": "a" * 32},
                                "shared_azureaisearchfoundryiq": {"action": "reuse", "connection": "b" * 32,
                                                                  "status": "Connected"}}}
        ctx, shell = self.agent_context(root, [(studio("connections"), deploy.Result(0, json.dumps(plan)))])
        with self.assertRaises(deploy.Waiting) as stop:
            deploy.phase_agent(ctx)
        self.assertIn(plan["sign_in"], " ".join(stop.exception.next_steps))
        self.assertFalse(shell.matching(lambda args: "--apply" in args))
        self.assertEqual(len(shell.matching(command("dotnet", "pac", "copilot", "push"))), 1)
        find, = shell.matching(studio("find"))
        self.assertEqual(find[find.index("--environment-id") + 1], self.ENVIRONMENT)

    def test_a_synchronizing_publish_is_observed_not_repeated(self):
        root = make_root(self)
        project = root / ".artifacts" / "studio" / self.SCHEMA
        (project / ".mcs").mkdir(parents=True)
        connected = {"status": "connected", "connections": {
            "shared_fabriciqmcpserver": {"action": "reuse", "connection": "a" * 32, "status": "Connected"},
            "shared_azureaisearchfoundryiq": {"action": "reuse", "connection": "b" * 32, "status": "Connected"}}}
        reports = [{"status": "blocked", "blockers": ["The agent is not published and synchronized (state: Pending); "
                                                      "recheck after publishing finishes."]}] * 2 + [{"status": "ready"}]
        ctx, shell = self.agent_context(root, [
            (studio("connections"), deploy.Result(0, json.dumps(connected))),
            (studio("configure"), deploy.Result(0, json.dumps({"status": "plan", "bot_id": "bot", "changes": []}))),
            (studio("check"), lambda args, options: deploy.Result(0, json.dumps(reports.pop(0)))),
        ])
        digest = deploy.tree_digest(project)
        ctx.state.section("agent").update(pushed=digest, published={"digest": digest, "at": "earlier", "status": "started"})
        self.assertIn("ready", deploy.phase_agent(ctx))
        self.assertFalse(shell.matching(command("dotnet", "pac", "copilot", "publish")))
        self.assertFalse(shell.matching(command("dotnet", "pac", "copilot", "push")))

    def test_a_failed_publish_is_not_repeated_without_retry_failed(self):
        root = make_root(self)
        project = root / ".artifacts" / "studio" / self.SCHEMA
        (project / ".mcs").mkdir(parents=True)
        connected = {"status": "connected", "connections": {
            "shared_fabriciqmcpserver": {"action": "reuse", "connection": "a" * 32, "status": "Connected"},
            "shared_azureaisearchfoundryiq": {"action": "reuse", "connection": "b" * 32, "status": "Connected"}}}
        blocked = {"status": "blocked", "blockers": ["The agent has changes that are not published."]}
        ctx, shell = self.agent_context(root, [
            (studio("connections"), deploy.Result(0, json.dumps(connected))),
            (studio("configure"), deploy.Result(0, json.dumps({"status": "plan", "bot_id": "bot", "changes": []}))),
            (studio("check"), deploy.Result(1, json.dumps(blocked))),
        ])
        digest = deploy.tree_digest(project)
        ctx.state.section("agent").update(pushed=digest, published={"digest": digest, "at": "earlier", "status": "failed"})
        with self.assertRaisesRegex(deploy.Blocked, "publish started at earlier failed"):
            deploy.phase_agent(ctx)
        self.assertFalse(shell.matching(command("dotnet", "pac", "copilot", "publish")))

    def test_an_interrupted_app_registration_is_never_repeated_automatically(self):
        root = make_root(self)
        (root / "package-lock.json").write_text("{}", encoding="utf-8")
        (root / "node_modules").mkdir()
        status = {"success": True, "signedIn": True, "activeAccount": {"homeAccountId": f"user.{TENANT}"}}
        ctx, shell = self.agent_context(root, [(command("ms", "auth", "status"), deploy.Result(0, json.dumps(status)))])
        ctx.state.section("app").update(installed=deploy.sha256_file(root / "package-lock.json"),
                                        init_started_at="earlier")
        with self.assertRaisesRegex(deploy.Blocked, "may have registered the app") as stop:
            deploy.phase_app(ctx)
        self.assertIn("--retry-failed", " ".join(stop.exception.next_steps))
        self.assertFalse(shell.matching(command("ms", "app", "init")))

    def test_a_data_source_addition_is_verified_in_ms_config(self):
        root = make_root(self)
        (root / "package-lock.json").write_text("{}", encoding="utf-8")
        (root / "node_modules").mkdir()
        (root / "ms.config.json").write_text(json.dumps({"environmentId": self.ENVIRONMENT, "connectionReferences": {}}),
                                             encoding="utf-8")
        status = {"success": True, "signedIn": True, "activeAccount": {"homeAccountId": f"user.{TENANT}"}}
        ctx, shell = self.agent_context(root, [(command("ms", "auth", "status"), deploy.Result(0, json.dumps(status))),
                                               (command("ms", "app", "add", "data-source"), deploy.Result(0))])
        ctx.args.approve = {"app-register": True}
        ctx.state.section("app").update(installed=deploy.sha256_file(root / "package-lock.json"))
        with self.assertRaisesRegex(deploy.Blocked, "without a Copilot Studio connection reference"):
            deploy.phase_app(ctx)
        self.assertEqual(len(shell.matching(command("ms", "app", "add", "data-source"))), 1)
        self.assertNotIn("registered_at", ctx.state.section("app"))

    def test_the_app_targets_exactly_one_agent_schema(self):
        root = make_root(self)
        config = root / "app" / "src" / "config.ts"
        self.assertTrue(deploy.set_agent_schema(config, "iq_demo_assistant"))
        self.assertIn("export const STUDIO_AGENT_SCHEMA = 'iq_demo_assistant'", config.read_text(encoding="utf-8"))
        self.assertFalse(deploy.set_agent_schema(config, "iq_demo_assistant"))
        with self.assertRaisesRegex(deploy.Blocked, "targets agent iq_demo_assistant"):
            deploy.set_agent_schema(config, "iq_other_assistant")

    def test_app_init_repository_access_errors_offer_admin_guidance_without_retrying(self):
        errors = (
            ("Microsoft GitHub App can't access the bound repository.", False, True),
            ("GitHub App authentication failed.", False, False),
            ("The environment is not eligible.", False, False),
            ("", True, True),
        )
        for message, interactive, needs_install in errors:
            with self.subTest(message=message, interactive=interactive):
                root = make_root(self)
                (root / "package-lock.json").write_text("{}", encoding="utf-8")
                (root / "node_modules").mkdir()
                status = {"success": True, "signedIn": True,
                          "activeAccount": {"homeAccountId": f"user.{TENANT}"}}
                ctx, shell = self.agent_context(root, [
                    (command("ms", "auth", "status"), deploy.Result(0, json.dumps(status))),
                    (command("ms", "app", "init"), deploy.Result(1, "", message)),
                ])
                ctx.interactive = interactive
                ctx.args.approve = {"app-register": True}
                ctx.state.section("app").update(installed=deploy.sha256_file(root / "package-lock.json"))
                with self.assertRaises(deploy.Blocked) as stop:
                    deploy.phase_app(ctx)
                self.assertIn("ms app init failed (exit 1).", stop.exception.message)
                if message:
                    self.assertIn(message, stop.exception.message)
                guidance = " ".join(stop.exception.next_steps)
                if needs_install:
                    self.assertIn("https://github.com/marketplace/microsoft-managed-apps", guidance)
                    self.assertIn("ms app info", guidance)
                    self.assertIn("--retry-failed only after confirming no app was registered", guidance)
                    if interactive:
                        self.assertIn("If the terminal reported", guidance)
                else:
                    self.assertEqual(guidance, "")
                self.assertEqual(len(shell.matching(command("ms", "app", "init"))), 1)
                saved = json.loads(ctx.state.path.read_text(encoding="utf-8"))["app"]
                self.assertIn("init_started_at", saved)
                self.assertNotIn("registered_at", saved)
                with self.assertRaisesRegex(deploy.Blocked, "may have registered the app"):
                    deploy.phase_app(ctx)
                self.assertEqual(len(shell.matching(command("ms", "app", "init"))), 1)

    def test_app_registration_surfaces_both_user_authorizations_without_claiming_readiness(self):
        root = make_root(self)
        (root / "package-lock.json").write_text("{}", encoding="utf-8")
        (root / "node_modules").mkdir()
        (root / "ms.config.json").write_text(json.dumps({
            "environmentId": self.ENVIRONMENT,
            "connectionReferences": {"studio": {"id": "/apis/shared_microsoftcopilotstudio"}},
        }), encoding="utf-8")
        status = {"success": True, "signedIn": True, "activeAccount": {"homeAccountId": f"user.{TENANT}"}}
        ctx, shell = self.agent_context(root, [(command("ms", "auth", "status"), deploy.Result(0, json.dumps(status)))])
        ctx.state.section("app").update(installed=deploy.sha256_file(root / "package-lock.json"))
        for _ in range(2):
            message = deploy.phase_app(ctx)
            self.assertIn(f"https://make.powerapps.com/environments/{self.ENVIRONMENT}/connections", message)
            self.assertIn("each user", message)
            self.assertIn("when prompted", message)
            self.assertNotIn("app ready", message)
        steps = " ".join(deploy.APP_NEXT)
        self.assertLess(steps.index("Before playback"), steps.index("ms app dev"))
        self.assertIn("Studio user-connections", steps)
        self.assertIn("Fabric IQ and Foundry IQ", steps)
        self.assertNotIn("ms app deploy", steps)
        self.assertFalse(shell.matching(command("ms", "app", "init")))
        self.assertFalse(shell.matching(command("ms", "app", "add", "data-source")))
        self.assertIn("registered_at", ctx.state.section("app"))


if __name__ == "__main__":
    unittest.main()

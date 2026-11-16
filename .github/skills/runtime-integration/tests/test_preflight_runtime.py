import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".github/skills/runtime-integration/scripts/preflight_runtime.py"
SPEC = importlib.util.spec_from_file_location("preflight_runtime", SCRIPT)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)

SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
TENANT = "22222222-2222-4222-8222-222222222222"
OTHER_TENANT = "33333333-3333-4333-8333-333333333333"
REPOSITORY = "fixture/runtime-proof"
SECRET = "must-not-appear-in-output"
# Every native command the helper may run: none signs in, writes or provisions.
READ_ONLY = (
    ("git", "remote", "get-url", "origin"), ("git", "--version"), ("gh", "repo", "view"), ("node", "--version"),
    ("az", "version"), ("az", "account", "show"), ("az", "ad", "sp", "show"), ("az", "provider", "show"),
    ("az", "rest", "--method", "get"),
)


def provider(namespace, resource_type, *locations):
    return {
        "namespace": namespace,
        "registrationState": "Registered",
        "resourceTypes": [
            {"resourceType": "operations", "locations": []},
            {"resourceType": resource_type, "locations": list(locations)},
        ],
    }


class RuntimePreflightTests(unittest.TestCase):
    def setUp(self):
        executable = patch.object(runtime.shutil, "which", return_value=None)
        executable.start()
        self.addCleanup(executable.stop)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.commands = []
        self.metadata = {"nameWithOwner": REPOSITORY, "isTemplate": False}
        self.versions = {"node": "v24.11.0", "git": "git version 2.27.0", "az": "2.60.0"}
        self.azure = {"id": SUBSCRIPTION, "tenantId": TENANT, "userType": "user"}
        self.principal = {
            "appId": runtime.FOUNDRY_API,
            "oauth2PermissionScopes": [{"value": "user_impersonation", "isEnabled": True}],
        }
        self.providers = {
            namespace: provider(namespace, resource_type, "Sweden Central", "Central US")
            for namespace, resource_type in runtime.REGIONAL
        }
        self.principal_missing = False
        self.quota = {
            "value": [{
                "name": {"value": "CapacityQuota"},
                "unit": "CU",
                "limit": 512,
                "currentValue": 4,
            }],
        }

    def test_native_command_resolves_windows_launcher(self):
        launcher = r"C:\Program Files\Azure\az.cmd"
        with patch.object(runtime.shutil, "which", return_value=launcher), \
                patch.object(runtime.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
            self.assertEqual(runtime.command(["az", "version"], ROOT), "{}")
            self.assertEqual(run.call_args.args[0], [launcher, "version"])

    def native(self, arguments, **kwargs):
        self.commands.append(arguments)
        if arguments[:4] == ["git", "remote", "get-url", "origin"]:
            output = f"https://github.com/{REPOSITORY}.git"
        elif arguments[:3] == ["gh", "repo", "view"]:
            output = json.dumps(self.metadata)
        elif arguments == ["node", "--version"]:
            output = self.versions["node"]
        elif arguments == ["git", "--version"]:
            output = self.versions["git"]
        elif arguments[:2] == ["az", "version"]:
            output = json.dumps({"azure-cli": self.versions["az"], "extensions": {}})
        elif arguments[:3] == ["az", "account", "show"]:
            output = json.dumps(self.azure)
        elif arguments[:4] == ["az", "ad", "sp", "show"]:
            if self.principal_missing:
                return subprocess.CompletedProcess(arguments, 3, stdout="", stderr=SECRET)
            output = json.dumps(self.principal)
        elif arguments[:3] == ["az", "provider", "show"]:
            output = json.dumps(self.providers[arguments[4]])
        elif arguments[:2] == ["az", "rest"]:
            output = json.dumps(self.quota)
        else:
            self.fail(f"Unexpected native command: {arguments}")
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    def run_preflight(self, sku="F2", **options):
        with patch.object(runtime.subprocess, "run", side_effect=self.native):
            return runtime.preflight(
                ROOT, REPOSITORY, SUBSCRIPTION, TENANT, "swedencentral", "centralus", sku, **options
            )

    def test_success_is_only_a_candidate_and_only_reads(self):
        os.environ["FABRIC_GITHUB_TOKEN"] = SECRET
        report = self.run_preflight()
        self.assertEqual(report["status"], "candidate")
        self.assertTrue(report["fabric_git_token_available"])
        self.assertEqual(report["foundry_api"], {"app_id": runtime.FOUNDRY_API, "scope": "user_impersonation"})
        self.assertEqual([region["resource_type"] for region in report["regions"]],
                         ["Microsoft.CognitiveServices/accounts", "Microsoft.App/managedEnvironments",
                          "Microsoft.ContainerRegistry/registries"])
        self.assertEqual(report["tools"], {"node": "24.11.0", "git": "2.27.0", "az": "2.60.0"})
        self.assertEqual((report["target"]["location"], report["target"]["fabric_location"]),
                         ("swedencentral", "centralus"))
        self.assertEqual(report["fabric_quota"]["available_cus"], 508)
        self.assertEqual(report["unverified"], runtime.UNVERIFIED)
        self.assertNotIn(SECRET, json.dumps(report))
        report["unverified"].clear()
        self.assertTrue(runtime.UNVERIFIED)
        for command in self.commands:
            self.assertTrue(any(tuple(command[:len(prefix)]) == prefix for prefix in READ_ONLY), command)
        [quota] = [command for command in self.commands if command[:2] == ["az", "rest"]]
        self.assertTrue(quota[quota.index("--url") + 1].startswith(
            f"https://management.azure.com/subscriptions/{SUBSCRIPTION}/providers/"
            "Microsoft.Fabric/locations/centralus/usages?"))

    def test_missing_pat_does_not_turn_metadata_access_into_platform_readiness(self):
        report = self.run_preflight()
        self.assertFalse(report["fabric_git_token_available"])
        self.assertEqual(report["status"], "candidate")
        self.assertTrue(report["unverified"])

    def test_work_iq_is_optional_and_its_prerequisites_are_only_listed(self):
        default = self.run_preflight()
        commands, self.commands = self.commands, []
        report = self.run_preflight(work_iq=True)
        # The flag adds no native call: nothing reads Microsoft 365, Work IQ or billing.
        self.assertEqual(self.commands, commands)
        self.assertEqual(default["work_iq"]["requested"], False)
        self.assertIn("--work-iq", default["work_iq"]["note"])
        self.assertNotIn("manual", default["work_iq"])
        self.assertEqual((report["status"], report["work_iq"]["requested"]), ("candidate", True))
        manual = "\n".join(report["work_iq"]["manual"])
        for prerequisite in ("mailbox", "Copilot Credits spending policy", "Microsoft 365 Copilot licence",
                             "WorkIQAgent.Ask and offline_access", f"az ad sp create --id {runtime.WORK_IQ_API}",
                             "one-time consent"):
            self.assertIn(prerequisite, manual)
        self.assertEqual(report["unverified"], [*runtime.UNVERIFIED, runtime.WORK_IQ_UNVERIFIED])

    def test_template_is_refused_before_azure_calls(self):
        self.metadata["isTemplate"] = True
        with self.assertRaisesRegex(runtime.PreflightError, "derived repository"):
            self.run_preflight()
        self.assertFalse(any(command[0] == "az" for command in self.commands))

    def test_missing_or_string_template_flag_is_refused(self):
        for value in (None, "false", 0):
            with self.subTest(value=value):
                self.metadata["isTemplate"] = value
                with self.assertRaises(runtime.PreflightError):
                    self.run_preflight()

    def test_wrong_azure_target_or_application_identity_is_refused(self):
        for key, value in (
            ("id", OTHER_TENANT),
            ("tenantId", OTHER_TENANT),
            ("userType", "servicePrincipal"),
            ("userType", None),
        ):
            with self.subTest(key=key, value=value):
                original = self.azure[key]
                self.azure[key] = value
                with self.assertRaisesRegex(runtime.PreflightError, "delegated user"):
                    self.run_preflight()
                self.azure[key] = original

    def test_the_foundry_api_principal_must_exist_and_publish_its_delegated_scope(self):
        self.principal_missing = True
        with self.assertRaises(runtime.PreflightError) as error:
            self.run_preflight()
        self.assertIn(f"az ad sp create --id {runtime.FOUNDRY_API}", str(error.exception))
        self.assertNotIn(SECRET, str(error.exception))
        self.principal_missing = False
        for scopes in ([], [{"value": "user_impersonation", "isEnabled": False}], [{"value": "other", "isEnabled": True}],
                       None):
            with self.subTest(scopes=scopes):
                self.principal["oauth2PermissionScopes"] = scopes
                with self.assertRaisesRegex(runtime.PreflightError, "user_impersonation"):
                    self.run_preflight()
        self.principal = {"appId": "other", "oauth2PermissionScopes": []}
        with self.assertRaisesRegex(runtime.PreflightError, "Foundry API principal"):
            self.run_preflight()

    def test_the_main_region_must_offer_foundry_container_apps_and_the_registry(self):
        for namespace, resource_type in runtime.REGIONAL:
            with self.subTest(namespace=namespace):
                original = self.providers[namespace]
                self.providers[namespace] = provider(namespace, resource_type, "Central US")
                with self.assertRaisesRegex(runtime.PreflightError, f"{resource_type} is not offered in swedencentral"):
                    self.run_preflight()
                self.assertFalse(any(command[:2] == ["az", "rest"] for command in self.commands[-1:]))
                self.providers[namespace] = original
        for value in ({}, {"namespace": "Microsoft.App", "resourceTypes": None},
                      {"namespace": "Microsoft.App", "resourceTypes": [{"resourceType": "managedEnvironments"}]}):
            with self.subTest(value=value), self.assertRaises(runtime.PreflightError):
                runtime.region_check(value, "Microsoft.App", "managedEnvironments", "swedencentral")

    def test_malformed_regions_and_skus_are_refused_before_any_call(self):
        for location, fabric_location, sku, current in (
            ("Sweden Central", "centralus", "F2", None),
            ("swedencentral", "centralus?unexpected=1", "F2", None),
            ("swedencentral", None, "F2", None),
            ("swedencentral", "centralus", "F3", None),
            ("swedencentral", "centralus", "F2", "F3"),
        ):
            with self.subTest(location=location, fabric_location=fabric_location, sku=sku, current=current), \
                    patch.object(runtime.subprocess, "run", side_effect=self.native), \
                    self.assertRaises(runtime.PreflightError):
                runtime.preflight(ROOT, REPOSITORY, SUBSCRIPTION, TENANT, location, fabric_location, sku,
                                  current_fabric_sku=current)
        self.assertEqual(self.commands, [])

    def test_old_tools_are_refused(self):
        for tool, output, found in (
            ("node", "v24.10.0", "Node 24.10.0"),
            ("git", "git version 2.26.9", "Git 2.26.9"),
            ("az", "2.59.9", "Azure CLI 2.59.9"),
        ):
            with self.subTest(tool=tool):
                original = self.versions[tool]
                self.versions[tool] = output
                with self.assertRaisesRegex(
                    runtime.PreflightError, r"Use Node >= 24\.11\.0, Git >= 2\.27\.0 and Azure CLI >= 2\.60\.0"
                ) as error:
                    self.run_preflight()
                self.assertIn(f"(found {found})", str(error.exception))
                self.versions[tool] = original

    def test_provider_target_conflicts_do_not_print_the_override(self):
        os.environ["ARM_SUBSCRIPTION_ID"] = OTHER_TENANT
        with self.assertRaises(runtime.PreflightError) as error:
            self.run_preflight()
        self.assertIn("ARM_SUBSCRIPTION_ID", str(error.exception))
        self.assertNotIn(OTHER_TENANT, str(error.exception))
        self.assertEqual(self.commands, [])

    def test_provider_credentials_are_not_printed_or_silently_used(self):
        for key in ("ARM_CLIENT_SECRET", "ARM_CLIENT_ID", "FABRIC_CLIENT_ID", "ARM_OIDC_TOKEN"):
            with self.subTest(key=key):
                os.environ[key] = SECRET
                with self.assertRaises(runtime.PreflightError) as error:
                    self.run_preflight()
                self.assertIn(key, str(error.exception))
                self.assertNotIn(SECRET, str(error.exception))
                del os.environ[key]

    def test_managed_identity_and_oidc_flags_require_review(self):
        for value in ("true", "1", "unexpected"):
            with self.subTest(value=value):
                os.environ["ARM_USE_MSI"] = value
                with self.assertRaisesRegex(runtime.PreflightError, "ARM_USE_MSI"):
                    self.run_preflight()
        os.environ["ARM_USE_MSI"] = "false"
        self.assertEqual(self.run_preflight()["status"], "candidate")

    def test_zero_or_exhausted_fabric_quota_blocks(self):
        for limit, used in ((0, 0), (4, 4), (4, 3)):
            with self.subTest(limit=limit, used=used):
                self.quota["value"][0].update(limit=limit, currentValue=used)
                with self.assertRaisesRegex(runtime.PreflightError, "available CUs"):
                    self.run_preflight()

    def test_fabric_quota_rejects_unknown_fallbacks_and_malformed_values(self):
        for value in (
            None,
            {},
            {"usageInfo": {"Microsoft.Fabric/capacities": [{"limit": 0, "description": "No Limit"}]}},
            {"value": []},
            {"value": [], "nextLink": "https://management.azure.com/next"},
            {"value": [None]},
            {"value": [{"name": {"value": "CapacityQuota"}, "unit": "CU", "limit": True, "currentValue": 0}]},
            {"value": [{"name": {"value": "CapacityQuota"}, "unit": "CU", "limit": 4.5, "currentValue": 0}]},
        ):
            with self.subTest(value=value), self.assertRaises(runtime.PreflightError):
                runtime.fabric_quota_check(value, "centralus", "F2")

    def test_fabric_quota_checks_exact_sku_threshold(self):
        quota = self.quota["value"][0]
        quota.update(limit=8, currentValue=4)
        self.assertEqual(runtime.fabric_quota_check(self.quota, "centralus", "F4")["required_cus"], 4)
        with self.assertRaisesRegex(runtime.PreflightError, "needs 8"):
            runtime.fabric_quota_check(self.quota, "centralus", "F8")
        for location, sku in (("centralus?unexpected=1", "F2"), ("centralus", "F3"), (None, "F2")):
            with self.subTest(location=location, sku=sku), self.assertRaises(runtime.PreflightError):
                runtime.fabric_quota_check(self.quota, location, sku)

    def test_fabric_quota_for_a_resize_counts_only_the_added_cus(self):
        quota = self.quota["value"][0]
        quota.update(limit=8, currentValue=2)
        self.assertEqual(runtime.fabric_quota_check(self.quota, "centralus", "F8", current_sku="F2")["required_cus"], 6)
        with self.assertRaisesRegex(runtime.PreflightError, "F4 -> F16 needs 12"):
            runtime.fabric_quota_check(self.quota, "centralus", "F16", current_sku="F4")
        with self.assertRaises(runtime.PreflightError):
            runtime.fabric_quota_check(self.quota, "centralus", "F8", current_sku="F3")

    def test_an_existing_capacity_needs_quota_only_for_the_cus_a_larger_sku_adds(self):
        # This deployment's existing capacity and others use up the region's quota.
        self.quota["value"][0].update(limit=16, currentValue=16)
        with self.assertRaisesRegex(runtime.PreflightError, "0 available CUs, but F2 needs 2"):
            self.run_preflight()
        for current in ("F2", "F4"):
            with self.subTest(current=current):
                fabric = self.run_preflight(current_fabric_sku=current)["fabric_quota"]
                self.assertEqual((fabric["current_sku"], fabric["required_cus"]), (current, 0))
        with self.assertRaisesRegex(runtime.PreflightError, "F2 -> F4 needs 2"):
            self.run_preflight("F4", current_fabric_sku="F2")

    def test_fabric_quota_requires_one_complete_cu_record(self):
        for mode in ("duplicate", "wrong-unit", "negative", "pagination"):
            with self.subTest(mode=mode):
                quota = {
                    "value": [{
                        "name": {"value": "CapacityQuota"}, "unit": "CU",
                        "limit": 512, "currentValue": 4,
                    }],
                }
                if mode == "duplicate":
                    quota["value"].append(dict(quota["value"][0]))
                elif mode == "wrong-unit":
                    quota["value"][0]["unit"] = "Count"
                elif mode == "negative":
                    quota["value"][0]["currentValue"] = -1
                else:
                    quota["nextLink"] = "https://management.azure.com/next"
                with self.assertRaises(runtime.PreflightError):
                    runtime.fabric_quota_check(quota, "centralus", "F2")

    def test_native_command_errors_do_not_echo_sensitive_output(self):
        result = subprocess.CompletedProcess(["az"], 1, stdout=SECRET, stderr=SECRET)
        with patch.object(runtime.subprocess, "run", return_value=result):
            with self.assertRaises(runtime.PreflightError) as error:
                runtime.command(["az", "account", "show", "--output", "json"], ROOT)
        self.assertNotIn(SECRET, str(error.exception))
        self.assertIn("exit code 1", str(error.exception))

    def test_json_failures_do_not_become_empty_success(self):
        for value in (SECRET, "[]", "null", json.dumps([SECRET])):
            with self.subTest(value=value), patch.object(runtime, "command", return_value=value):
                with self.assertRaises(runtime.PreflightError) as error:
                    runtime.json_command(["az", "account", "show", "--output", "json"], ROOT)
                self.assertNotIn(SECRET, str(error.exception))

    def test_timeouts_and_missing_tools_stop_the_probe(self):
        for error in (FileNotFoundError(SECRET), subprocess.TimeoutExpired(["az"], 120)):
            with self.subTest(error=type(error).__name__):
                with patch.object(runtime.subprocess, "run", side_effect=error):
                    with self.assertRaises(runtime.PreflightError) as caught:
                        runtime.command(["az", "version"], ROOT)
                self.assertNotIn(SECRET, str(caught.exception))

    def test_github_origins_are_explicit_and_uncredentialed(self):
        for value in (
            f"https://github.com/{REPOSITORY}.git",
            f"git@github.com:{REPOSITORY}.git",
        ):
            self.assertEqual(runtime.remote_repository(value), REPOSITORY)
        for value in (
            f"https://user:{SECRET}@github.com/{REPOSITORY}",
            f"https://github.com/{REPOSITORY}?token={SECRET}",
            f"https://example.com/{REPOSITORY}",
            "https://github.com/fixture/..",
        ):
            with self.subTest(value=value):
                with self.assertRaises(runtime.PreflightError) as caught:
                    runtime.remote_repository(value)
                self.assertNotIn(SECRET, str(caught.exception))

    def test_identifiers_and_versions_are_validated(self):
        for value in ("not-a-guid", "00000000-0000-0000-0000-000000000000", None):
            with self.subTest(value=value), self.assertRaises(runtime.PreflightError):
                runtime.identifier(value, "tenant-id")
        self.assertEqual(runtime.tool_version("v24.11.0", "node"), (24, 11, 0))
        self.assertEqual(runtime.tool_version("git version 2.53.0 (Apple Git)", "git"), (2, 53, 0))
        with self.assertRaises(runtime.PreflightError):
            runtime.tool_version(SECRET, "node")

    def test_main_failure_has_no_success_report(self):
        arguments = [
            str(SCRIPT), "--repository", REPOSITORY,
            "--subscription-id", SUBSCRIPTION, "--tenant-id", TENANT,
            "--location", "swedencentral",
        ]
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("sys.argv", arguments),
            patch.object(runtime, "preflight", side_effect=runtime.PreflightError("blocked")),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as error,
        ):
            runtime.main()
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Runtime preflight blocked", stderr.getvalue())

    def test_main_passes_the_optional_flags_and_prints_only_the_report(self):
        arguments = [
            str(SCRIPT), "--repository", REPOSITORY,
            "--subscription-id", SUBSCRIPTION, "--tenant-id", TENANT,
            "--location", "swedencentral", "--fabric-capacity-sku", "F4",
            "--current-fabric-capacity-sku", "F2", "--work-iq",
        ]
        stdout = io.StringIO()
        with (
            patch("sys.argv", arguments),
            patch.object(runtime, "preflight", return_value={"status": "candidate"}) as preflight,
            contextlib.redirect_stdout(stdout),
        ):
            runtime.main()
        self.assertEqual(json.loads(stdout.getvalue()), {"status": "candidate"})
        self.assertEqual(preflight.call_args.args[4:], ("swedencentral", "swedencentral", "F4"))
        self.assertEqual(preflight.call_args.kwargs, {"current_fabric_sku": "F2", "work_iq": True})


if __name__ == "__main__":
    unittest.main()

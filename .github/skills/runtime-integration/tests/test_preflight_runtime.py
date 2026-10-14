import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
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


def catalog(name):
    return {
        "success": True,
        "items": [{"name": name, "isBlocked": False, "isTabular": False}],
    }


def actions(*operations):
    return {
        "success": True,
        "items": [
            {"id": operation, "behavior": "Allow", "deprecated": False}
            for operation in operations
        ],
    }


class RuntimePreflightTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.commands = []
        self.metadata = {"nameWithOwner": REPOSITORY, "isTemplate": False}
        self.azure = {"id": SUBSCRIPTION, "tenantId": TENANT, "userType": "user"}
        self.authentication = {
            "success": True,
            "signedIn": True,
            "activeAccount": {
                "username": SECRET,
                "homeAccountId": f"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.{TENANT}",
            },
        }
        self.connector = "shared_azureagentservice"
        self.quota = {
            "value": [{
                "name": {"value": "CapacityQuota"},
                "unit": "CU",
                "limit": 512,
                "currentValue": 4,
            }],
        }

    def native(self, arguments, **kwargs):
        self.commands.append(arguments)
        if arguments[:4] == ["git", "remote", "get-url", "origin"]:
            output = f"https://github.com/{REPOSITORY}.git"
        elif arguments[:3] == ["gh", "repo", "view"]:
            output = json.dumps(self.metadata)
        elif arguments == ["node", "--version"]:
            output = "v24.11.0"
        elif arguments == ["git", "--version"]:
            output = "git version 2.27.0"
        elif arguments == ["ms", "--version"]:
            output = runtime.cli_pin(ROOT)
        elif arguments[:3] == ["az", "account", "show"]:
            output = json.dumps(self.azure)
        elif arguments[:2] == ["az", "rest"]:
            output = json.dumps(self.quota)
        elif arguments[:3] == ["ms", "auth", "status"]:
            output = json.dumps(self.authentication)
        elif arguments[:3] == ["ms", "connector", "list"]:
            output = json.dumps(catalog(arguments[4]))
        elif arguments[:3] == ["ms", "connector", "list-actions"]:
            output = json.dumps(actions(*runtime.CONNECTORS[arguments[4]]))
        else:
            self.fail(f"Unexpected native command: {arguments}")
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    def run_preflight(self):
        with patch.object(runtime.subprocess, "run", side_effect=self.native):
            return runtime.preflight(ROOT, REPOSITORY, SUBSCRIPTION, TENANT, ROOT, "centralus", "F2")

    def test_success_is_only_a_candidate_and_redacts_native_account_output(self):
        os.environ["FABRIC_GITHUB_TOKEN"] = SECRET
        os.environ["MS_CLI_ORIGIN"] = "user/chosen-origin"
        report = self.run_preflight()
        self.assertEqual(report["status"], "candidate")
        self.assertTrue(report["fabric_git_token_available"])
        self.assertEqual(len(report["connectors"]), len(runtime.CONNECTORS))
        self.assertEqual(report["fabric_quota"]["available_cus"], 508)
        self.assertEqual(report["unverified"], runtime.UNVERIFIED)
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertEqual(os.environ["MS_CLI_ORIGIN"], "user/chosen-origin")
        report["unverified"].clear()
        self.assertTrue(runtime.UNVERIFIED)
        probes = [command for command in self.commands if command[:2] == ["ms", "connector"]]
        self.assertEqual(
            probes,
            [
                ["ms", "connector", "list", "--search", name, "--json"]
                if kind == "list"
                else ["ms", "connector", "list-actions", "--connector", name, "--json"]
                for name in runtime.CONNECTORS
                for kind in ("list", "list-actions")
            ],
        )
        self.assertTrue(all("--include-blocked" not in command for command in self.commands))
        self.assertTrue(all("login" not in command and "create" not in command for command in self.commands))

    def test_missing_pat_does_not_turn_catalog_access_into_platform_readiness(self):
        report = self.run_preflight()
        self.assertFalse(report["fabric_git_token_available"])
        self.assertEqual(report["status"], "candidate")
        self.assertTrue(report["unverified"])

    def test_template_is_refused_before_ms_or_azure_calls(self):
        self.metadata["isTemplate"] = True
        with self.assertRaisesRegex(runtime.PreflightError, "derived repository"):
            self.run_preflight()
        self.assertFalse(any(command[0] in ("ms", "az") for command in self.commands))

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

    def test_cached_managed_runtime_account_is_not_an_authentication_receipt(self):
        self.authentication["signedIn"] = False
        with self.assertRaisesRegex(runtime.PreflightError, "ms auth login"):
            self.run_preflight()
        self.authentication["signedIn"] = True
        self.authentication["activeAccount"]["homeAccountId"] = f"subject.{OTHER_TENANT}"
        with self.assertRaisesRegex(runtime.PreflightError, "different home tenants"):
            self.run_preflight()

    def test_malformed_active_account_is_refused(self):
        for value in (None, {}, {"homeAccountId": "missing-tenant"}):
            with self.subTest(value=value):
                self.authentication["activeAccount"] = value
                with self.assertRaises(runtime.PreflightError):
                    self.run_preflight()

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

    def test_allowed_native_items_envelope(self):
        self.assertEqual(
            runtime.connector_check(catalog(self.connector), actions("InvokeAgent"), self.connector, ("InvokeAgent",)),
            {"connector": self.connector, "allowed_operations": ["InvokeAgent"]},
        )

    def test_zero_or_exhausted_fabric_quota_blocks_before_connector_probes(self):
        for limit, used in ((0, 0), (4, 4), (4, 3)):
            with self.subTest(limit=limit, used=used):
                self.quota["value"][0].update(limit=limit, currentValue=used)
                self.commands.clear()
                with self.assertRaisesRegex(runtime.PreflightError, "available CUs"):
                    self.run_preflight()
                self.assertFalse(any(command[:2] == ["ms", "connector"] for command in self.commands))

    def test_fabric_quota_rejects_unknown_fallbacks_and_malformed_values(self):
        for value in (
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
        for location, sku in (("centralus?unexpected=1", "F2"), ("centralus", "F3")):
            with self.subTest(location=location, sku=sku), self.assertRaises(runtime.PreflightError):
                runtime.fabric_quota_check(self.quota, location, sku)

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

    def test_missing_or_wrong_catalog_envelopes_are_errors(self):
        for value in ({}, {"actions": []}, {"items": {}}, {"items": [None]}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(runtime.PreflightError, "items array"):
                    runtime.connector_check(value, actions("InvokeAgent"), self.connector, ("InvokeAgent",))

    def test_connector_governance_is_strict(self):
        for value in (True, None, "false", 0):
            with self.subTest(value=value):
                data = catalog(self.connector)
                data["items"][0]["isBlocked"] = value
                with self.assertRaises(runtime.PreflightError):
                    runtime.connector_check(data, actions("InvokeAgent"), self.connector, ("InvokeAgent",))

    def test_connector_matches_must_be_unique_and_non_tabular(self):
        for mode in ("missing", "duplicate", "tabular"):
            with self.subTest(mode=mode):
                data = catalog(self.connector)
                if mode == "missing":
                    data["items"] = []
                elif mode == "duplicate":
                    data["items"].append(dict(data["items"][0]))
                else:
                    data["items"][0]["isTabular"] = True
                with self.assertRaises(runtime.PreflightError):
                    runtime.connector_check(data, actions("InvokeAgent"), self.connector, ("InvokeAgent",))

    def test_required_actions_cannot_be_denied_deprecated_missing_or_duplicated(self):
        for mode in ("denied", "deprecated", "missing", "duplicate", "unknown"):
            with self.subTest(mode=mode):
                data = actions("InvokeAgent")
                if mode == "denied":
                    data["items"][0]["behavior"] = "Deny"
                elif mode == "deprecated":
                    data["items"][0]["deprecated"] = True
                elif mode == "missing":
                    data["items"] = []
                elif mode == "duplicate":
                    data["items"].append(dict(data["items"][0]))
                else:
                    del data["items"][0]["deprecated"]
                with self.assertRaises(runtime.PreflightError):
                    runtime.connector_check(catalog(self.connector), data, self.connector, ("InvokeAgent",))

    def test_native_command_errors_do_not_echo_sensitive_output(self):
        result = subprocess.CompletedProcess(["ms"], 1, stdout=SECRET, stderr=SECRET)
        with patch.object(runtime.subprocess, "run", return_value=result):
            with self.assertRaises(runtime.PreflightError) as error:
                runtime.command(["ms", "auth", "status", "--json"], ROOT)
        self.assertNotIn(SECRET, str(error.exception))
        self.assertIn("exit code 1", str(error.exception))

    def test_json_failures_do_not_become_empty_success(self):
        for value in (SECRET, "[]", json.dumps({"success": False, "errorMessage": SECRET})):
            with self.subTest(value=value), patch.object(runtime, "command", return_value=value):
                with self.assertRaises(runtime.PreflightError) as error:
                    runtime.json_command(["ms", "auth", "status", "--json"], ROOT)
                self.assertNotIn(SECRET, str(error.exception))

    def test_timeouts_and_missing_tools_stop_the_probe(self):
        for error in (FileNotFoundError(SECRET), subprocess.TimeoutExpired(["ms"], 120)):
            with self.subTest(error=type(error).__name__):
                with patch.object(runtime.subprocess, "run", side_effect=error):
                    with self.assertRaises(runtime.PreflightError) as caught:
                        runtime.command(["ms", "--version"], ROOT)
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

    def test_cli_pin_must_be_unique(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / ".devcontainer/features/managed-runtime/install.sh"
            source.parent.mkdir(parents=True)
            for content in ("", "cli_version=0.27.0\ncli_version=0.28.0\n"):
                source.write_text(content, encoding="utf-8")
                with self.assertRaises(runtime.PreflightError):
                    runtime.cli_pin(root)

    def test_main_failure_has_no_success_report(self):
        arguments = [
            str(SCRIPT), "--repository", REPOSITORY,
            "--subscription-id", SUBSCRIPTION, "--tenant-id", TENANT,
            "--fabric-location", "centralus",
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


if __name__ == "__main__":
    unittest.main()

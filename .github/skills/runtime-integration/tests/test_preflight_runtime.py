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


class RuntimePreflightTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.commands = []
        self.metadata = {"nameWithOwner": REPOSITORY, "isTemplate": False}
        self.azure = {"id": SUBSCRIPTION, "tenantId": TENANT, "userType": "user"}
        self.versions = {"azure-cli": "2.80.0", "azure-cli-core": "2.80.0", "extensions": {}}
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
        elif arguments == ["git", "--version"]:
            output = "git version 2.27.0"
        elif arguments[:2] == ["az", "version"]:
            output = json.dumps(self.versions)
        elif arguments[:3] == ["az", "account", "show"]:
            output = json.dumps(self.azure)
        elif arguments[:2] == ["az", "rest"]:
            output = json.dumps(self.quota)
        else:
            self.fail(f"Unexpected native command: {arguments}")
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    def run_preflight(self):
        with patch.object(runtime.subprocess, "run", side_effect=self.native):
            return runtime.preflight(ROOT, REPOSITORY, SUBSCRIPTION, TENANT, "centralus", "centralus", "F2")

    def test_success_is_only_a_candidate_and_never_signs_in_or_provisions(self):
        os.environ["FABRIC_GITHUB_TOKEN"] = SECRET
        report = self.run_preflight()
        self.assertEqual(report["status"], "candidate")
        self.assertTrue(report["fabric_git_token_available"])
        self.assertEqual(report["fabric_quota"]["available_cus"], 508)
        self.assertEqual(report["azure_cli_version"], "2.80.0")
        self.assertEqual(report["target"]["location"], "centralus")
        self.assertEqual(report["unverified"], runtime.UNVERIFIED)
        self.assertNotIn(SECRET, json.dumps(report))
        report["unverified"].clear()
        self.assertTrue(runtime.UNVERIFIED)
        self.assertTrue(all("login" not in command and "create" not in command for command in self.commands))
        self.assertFalse(any(command[0] in ("ms", "pac", "dotnet") for command in self.commands))

    def test_old_tools_are_refused(self):
        self.versions["azure-cli"] = "2.79.9"
        with self.assertRaisesRegex(runtime.PreflightError, "Azure CLI >= 2.80.0"):
            self.run_preflight()

    def test_regions_without_documented_hosted_agents_are_refused_before_any_call(self):
        with patch.object(runtime.subprocess, "run", side_effect=self.native):
            with self.assertRaisesRegex(runtime.PreflightError, "not documented in westus2"):
                runtime.preflight(ROOT, REPOSITORY, SUBSCRIPTION, TENANT, "westus2", "centralus", "F2")
        self.assertEqual(self.commands, [])
        for value in ("Central US", "centralus?x=1", None):
            with self.subTest(value=value), self.assertRaises(runtime.PreflightError):
                runtime.hosted_agent_region(value)

    def test_missing_pat_does_not_turn_access_into_platform_readiness(self):
        report = self.run_preflight()
        self.assertFalse(report["fabric_git_token_available"])
        self.assertEqual(report["status"], "candidate")
        self.assertTrue(report["unverified"])

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

    def test_native_command_errors_do_not_echo_sensitive_output(self):
        result = subprocess.CompletedProcess(["az"], 1, stdout=SECRET, stderr=SECRET)
        with patch.object(runtime.subprocess, "run", return_value=result):
            with self.assertRaises(runtime.PreflightError) as error:
                runtime.command(["az", "account", "show", "--output", "json"], ROOT)
        self.assertNotIn(SECRET, str(error.exception))
        self.assertIn("exit code 1", str(error.exception))

    def test_json_failures_do_not_become_empty_success(self):
        for value in (SECRET, "[]", json.dumps([SECRET])):
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
            "--location", "centralus",
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

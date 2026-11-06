#!/usr/bin/env python3
"""Check a derived project's hosted-agent and Container Apps route without provisioning or signing in."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse
from uuid import UUID


# Regions that documented Foundry hosted agents in November 2026. A missing region is a
# reason to check the current documentation, not proof that the service is unavailable.
HOSTED_AGENT_REGIONS = frozenset({
    "australiaeast", "brazilsouth", "canadacentral", "canadaeast", "centralus", "eastus", "eastus2",
    "francecentral", "germanywestcentral", "italynorth", "japaneast", "japanwest", "koreacentral",
    "northcentralus", "norwayeast", "polandcentral", "southafricanorth", "southcentralus", "southindia",
    "southeastasia", "spaincentral", "swedencentral", "switzerlandnorth", "switzerlandwest", "uaenorth",
    "uksouth", "ukwest", "westcentralus", "westeurope", "westus", "westus3",
})
MINIMUM_AZURE_CLI = (2, 80, 0)
UNVERIFIED = [
    "Image builds, toolbox and hosted agent publishing, and Container Apps provisioning",
    "Search new-service regional capacity and model deployment quota",
    "Per-user Fabric IQ delegation through the toolbox and knowledge-base retrieval",
    "Browser sign-in consent, other users' Foundry roles and second-user isolation",
    "Document ingestion, live ontology references and denied-user behavior",
    "Target-local Fabric bindings",
]


class PreflightError(ValueError):
    pass


def command(arguments, checkout):
    try:
        result = subprocess.run(
            arguments,
            cwd=checkout,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PreflightError(
            f"{arguments[0]} could not complete the read-only check "
            f"({type(error).__name__})."
        ) from None
    if result.returncode:
        # Native output can include account information or authentication receipts.
        raise PreflightError(
            f"{' '.join(arguments[:3])} failed with exit code {result.returncode}. "
            "Run that read-only command in your terminal to inspect its diagnostic; "
            "do not enable debug logging or paste credentials into chat."
        )
    return result.stdout


def json_command(arguments, checkout):
    try:
        value = json.loads(command(arguments, checkout))
    except json.JSONDecodeError:
        raise PreflightError(f"{arguments[0]} did not return a JSON object.") from None
    if not isinstance(value, dict):
        raise PreflightError(f"{arguments[0]} did not return a JSON object.")
    return value


def identifier(value, label):
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise PreflightError(f"{label} must be an explicitly chosen UUID.") from None
    if not parsed.int:
        raise PreflightError(f"{label} cannot be the zero UUID.")
    return str(parsed)


def repository_name(value):
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_.-]+)", value)
    if not match or match[2] in (".", "..") or match[2].endswith(".git"):
        raise PreflightError("repository must be OWNER/REPO, without a URL or .git suffix.")
    return value


def remote_repository(value):
    value = value.strip()
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    else:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or parsed.query
            or parsed.fragment
        ):
            raise PreflightError("The selected checkout must use an uncredentialed github.com origin.")
        path = parsed.path.removeprefix("/")
    return repository_name(path.removesuffix(".git"))


def tool_version(value, label):
    match = re.fullmatch(
        rf"(?:{re.escape(label)} version |v)?(\d+)\.(\d+)\.(\d+)(?:[^\r\n]*)?",
        value.strip(),
    )
    if not match:
        raise PreflightError(f"Could not read the {label} version.")
    return tuple(int(part) for part in match.groups())


def region(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9]+", value):
        raise PreflightError(f"{label} must be an explicitly chosen Azure region short name.")
    return value


def fabric_quota_check(value, location, sku):
    region(location, "fabric-location")
    if sku not in (
        "F2", "F4", "F8", "F16", "F32", "F64", "F128", "F256",
        "F512", "F1024", "F2048",
    ):
        raise PreflightError("fabric-capacity-sku must be a supported Fabric F-series SKU.")
    quotas = value.get("value")
    if not isinstance(quotas, list) or any(not isinstance(item, dict) for item in quotas):
        raise PreflightError("Fabric regional usages must return a native value array.")
    if value.get("nextLink"):
        raise PreflightError("Fabric regional quota is paginated; inspect every page before proceeding.")
    matches = [
        item for item in quotas
        if isinstance(item.get("name"), dict) and item["name"].get("value") == "CapacityQuota"
    ]
    if len(matches) != 1:
        raise PreflightError("Expected exactly one authoritative Fabric CapacityQuota record.")
    quota = matches[0]
    limit, used = quota.get("limit"), quota.get("currentValue")
    if (
        quota.get("unit") != "CU"
        or type(limit) is not int
        or type(used) is not int
        or limit < 0
        or used < 0
    ):
        raise PreflightError("Fabric must report explicit nonnegative integer CU quota and usage.")
    required = int(sku[1:])
    available = limit - used
    if available < required:
        raise PreflightError(
            f"Fabric {location} has {available} available CUs, but {sku} needs {required}. "
            "Choose an approved region or obtain quota; no resources were provisioned."
        )
    return {"location": location, "sku": sku, "available_cus": available, "required_cus": required}


def hosted_agent_region(location):
    region(location, "location")
    if location not in HOSTED_AGENT_REGIONS:
        raise PreflightError(
            f"Foundry hosted agents were not documented in {location}. Check the current region list "
            "in the hosted agents documentation, or choose a listed region; no resources were provisioned."
        )
    return location


def check_environment(subscription, tenant):
    for key, expected in (
        ("ARM_SUBSCRIPTION_ID", subscription),
        ("ARM_TENANT_ID", tenant),
        ("FABRIC_TENANT_ID", tenant),
    ):
        value = os.environ.get(key)
        if value and identifier(value, key) != expected:
            raise PreflightError(f"{key} conflicts with the explicitly chosen target.")
    for key in (
        "ARM_CLIENT_ID",
        "ARM_CLIENT_SECRET",
        "ARM_CLIENT_CERTIFICATE_PATH",
        "ARM_CLIENT_CERTIFICATE_PASSWORD",
        "ARM_OIDC_TOKEN",
        "ARM_OIDC_TOKEN_FILE_PATH",
        "FABRIC_CLIENT_ID",
        "FABRIC_CLIENT_SECRET",
    ):
        if os.environ.get(key):
            raise PreflightError(
                f"Review the {key} authentication override before using this delegated-user flow."
            )
    for key in ("ARM_USE_MSI", "ARM_USE_OIDC"):
        if os.environ.get(key, "").lower() not in ("", "false", "0"):
            raise PreflightError(
                f"Review the {key} authentication override before using this delegated-user flow."
            )


def preflight(checkout, repository, subscription, tenant, location, fabric_location, fabric_sku):
    repository = repository_name(repository)
    subscription = identifier(subscription, "subscription-id")
    tenant = identifier(tenant, "tenant-id")
    hosted_agent_region(location)
    region(fabric_location, "fabric-location")
    check_environment(subscription, tenant)

    actual = remote_repository(command(["git", "remote", "get-url", "origin"], checkout))
    if actual.lower() != repository.lower():
        raise PreflightError("The checkout's origin does not match the selected derived repository.")
    metadata = json_command(
        ["gh", "repo", "view", repository, "--json", "nameWithOwner,isTemplate"], checkout
    )
    # Forks inherit isTemplate, so this also refuses forks of the template.
    if (
        not isinstance(metadata.get("nameWithOwner"), str)
        or metadata["nameWithOwner"].lower() != repository.lower()
        or metadata.get("isTemplate") is not False
    ):
        raise PreflightError("Use a verified derived repository, not the reusable template.")

    git = tool_version(command(["git", "--version"], checkout), "git")
    versions = json_command(["az", "version", "--output", "json"], checkout)
    azure_cli = tool_version(str(versions.get("azure-cli") or ""), "azure-cli")
    if git < (2, 27, 0) or azure_cli < MINIMUM_AZURE_CLI:
        raise PreflightError("Use Git >= 2.27.0 and Azure CLI >= 2.80.0 (ACR builds and hosted agents).")

    azure = json_command(
        [
            "az", "account", "show", "--subscription", subscription,
            "--query", "{id:id,tenantId:tenantId,userType:user.type}", "--output", "json",
        ],
        checkout,
    )
    if (
        azure.get("id") != subscription
        or azure.get("tenantId") != tenant
        or azure.get("userType") != "user"
    ):
        raise PreflightError("Azure must use the chosen subscription/tenant and a delegated user.")

    quota = json_command(
        [
            "az", "rest", "--method", "get", "--url",
            f"https://management.azure.com/subscriptions/{subscription}/providers/"
            f"Microsoft.Fabric/locations/{fabric_location}/usages?api-version=2023-11-01",
            "--output", "json",
        ],
        checkout,
    )
    fabric = fabric_quota_check(quota, fabric_location, fabric_sku)

    return {
        "schema_version": 2,
        "status": "candidate",
        "target": {"repository": repository, "subscription_id": subscription, "tenant_id": tenant,
                   "location": location},
        "azure_cli_version": ".".join(map(str, azure_cli)),
        "fabric_quota": fabric,
        "fabric_git_token_available": bool(
            os.environ.get("TF_VAR_github_token") or os.environ.get("FABRIC_GITHUB_TOKEN")
        ),
        "unverified": list(UNVERIFIED),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--repository", required=True, metavar="OWNER/REPO")
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--location", required=True, help="the Foundry and resource-group region")
    parser.add_argument("--fabric-location", help="defaults to --location")
    parser.add_argument("--fabric-capacity-sku", default="F2")
    args = parser.parse_args()
    try:
        result = preflight(
            args.checkout.resolve(), args.repository, args.subscription_id, args.tenant_id,
            args.location, args.fabric_location or args.location, args.fabric_capacity_sku,
        )
    except (OSError, PreflightError) as error:
        parser.exit(1, f"Runtime preflight blocked: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

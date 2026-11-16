#!/usr/bin/env python3
"""Check a derived project's Foundry prompt-agent and Container Apps route without provisioning or signing in."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlparse
from uuid import UUID


# The resource behind https://ai.azure.com tokens: the app's users sign in for its user_impersonation scope.
FOUNDRY_API = "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe"
FOUNDRY_SCOPE = "user_impersonation"
# Work IQ's application, whose WorkIQAgent.Ask the optional mail tool's registration declares.
WORK_IQ_API = "fdcc1f02-fc51-4226-8753-f668596af7f7"
# The regional resource types the route deploys in the main location.
REGIONAL = (
    ("Microsoft.CognitiveServices", "accounts"),
    ("Microsoft.App", "managedEnvironments"),
    ("Microsoft.ContainerRegistry", "registries"),
)
FABRIC_SKUS = ("F2", "F4", "F8", "F16", "F32", "F64", "F128", "F256", "F512", "F1024", "F2048")
# The Graph-based `az ad` (oauth2PermissionScopes, `permission grant --scope`) needs Azure CLI 2.37.0 or later.
MINIMUM_AZURE_CLI = (2, 60, 0)
MINIMUM_VERSIONS = {"node": (24, 11, 0), "git": (2, 27, 0), "az": MINIMUM_AZURE_CLI}
TOOL_NAMES = {"node": "Node", "git": "Git", "az": "Azure CLI"}
UNVERIFIED = [
    "Model deployment quota and Search new-service regional capacity (deploy.py's preflight checks model quota)",
    "ACR Tasks in this subscription, which deploy.py's app phase builds the image with",
    "Web IQ for this subscription: its terms, eligibility and API key",
    "Each user's Foundry role and consent to the app's Foundry access and to the agents' Fabric IQ tool",
    "Agent invocation as the signed-in user, its MCP tool calls and native knowledge retrieval",
    "Document ingestion, live ontology references and denied-user behavior",
    "Cross-user isolation and target-local Fabric bindings",
]
WORK_IQ_OPTIONAL = (
    "Optional and off by default (work_iq_enabled = false). Before you enable it, rerun with --work-iq "
    "to list its prerequisites."
)
WORK_IQ_MANUAL = [
    "The tenant's Work IQ service principal, which a Global Administrator creates once: "
    + f"az ad sp create --id {WORK_IQ_API} (a conflict means it exists)",
    "Administrator consent to the Work IQ registration's WorkIQAgent.Ask and offline_access, at the "
    + "api_permissions_url of terraform output work_iq",
    "A Copilot Credits spending policy that covers the Work IQ API and each user: an agent's Work IQ calls are "
    + "billed per use, also for users with a Microsoft 365 Copilot licence",
    "A Microsoft 365 mailbox for each user",
    "Each user's own one-time consent, at the link the app shows for their first Work IQ question",
]
WORK_IQ_UNVERIFIED = "Work IQ mail as each signed-in user, after every prerequisite in work_iq.manual"


class PreflightError(ValueError):
    pass


def command(arguments, checkout):
    try:
        result = subprocess.run(
            [shutil.which(arguments[0]) or arguments[0], *arguments[1:]],
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


def dotted(version):
    return ".".join(map(str, version))


def region(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9]+", value):
        raise PreflightError(f"{label} must be an explicitly chosen Azure region short name.")
    return value


def capacity_sku(value, label):
    if value not in FABRIC_SKUS:
        raise PreflightError(f"{label} must be a supported Fabric F-series SKU.")
    return value


def foundry_api_check(principal):
    """The tenant's Microsoft Foundry API principal must publish the delegated scope the app's users consent to."""
    if principal.get("appId") != FOUNDRY_API:
        raise PreflightError("Azure did not return the tenant's Microsoft Foundry API principal.")
    scopes = principal.get("oauth2PermissionScopes")
    if not isinstance(scopes, list) or not any(
        isinstance(scope, dict) and scope.get("value") == FOUNDRY_SCOPE and scope.get("isEnabled") is True
        for scope in scopes
    ):
        raise PreflightError(f"Microsoft Foundry's API principal does not publish an enabled {FOUNDRY_SCOPE} scope.")
    return {"app_id": FOUNDRY_API, "scope": FOUNDRY_SCOPE}


def region_check(provider, namespace, resource_type, location):
    """Require the resource type in the chosen region; registration itself is Terraform's."""
    types = provider.get("resourceTypes")
    if str(provider.get("namespace", "")).lower() != namespace.lower() or not isinstance(types, list):
        raise PreflightError(f"Azure did not return the {namespace} resource provider.")
    matches = [
        item for item in types
        if isinstance(item, dict) and str(item.get("resourceType", "")).lower() == resource_type.lower()
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("locations"), list):
        raise PreflightError(f"Expected exactly one {namespace}/{resource_type} resource type with its regions.")
    regions = {re.sub(r"\s+", "", str(region)).lower() for region in matches[0]["locations"]}
    if location not in regions:
        raise PreflightError(
            f"{namespace}/{resource_type} is not offered in {location}. "
            "Choose a region that offers Foundry, Container Apps and Container Registry together."
        )
    return {"resource_type": f"{namespace}/{resource_type}", "registration_state": provider.get("registrationState")}


def fabric_quota_check(value, location, sku, current_sku=None):
    """Require regional quota for `sku`, or only for the CUs it adds to an existing `current_sku`."""
    region(location, "fabric-location")
    capacity_sku(sku, "fabric-capacity-sku")
    if current_sku is not None and current_sku not in FABRIC_SKUS:
        raise PreflightError("The existing capacity reports an unsupported Fabric SKU.")
    quotas = value.get("value") if isinstance(value, dict) else None
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
    # An unchanged or smaller SKU adds no CUs.
    required = max(int(sku[1:]) - (int(current_sku[1:]) if current_sku else 0), 0)
    available = limit - used
    if required and available < required:
        change = f"{current_sku} -> {sku}" if current_sku else sku
        raise PreflightError(
            f"Fabric {location} has {available} available CUs, but {change} needs {required}. "
            "Choose an approved region or obtain quota; no resources were provisioned."
        )
    return {
        "location": location, "sku": sku, "current_sku": current_sku,
        "available_cus": available, "required_cus": required,
    }


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


def work_iq_report(requested):
    """Work IQ is opt-in. This helper only lists its prerequisites; it never calls Microsoft 365 or Work IQ."""
    if not requested:
        return {"requested": False, "note": WORK_IQ_OPTIONAL}
    return {"requested": True, "manual": list(WORK_IQ_MANUAL)}


def preflight(
    checkout, repository, subscription, tenant, location, fabric_location, fabric_sku,
    *, current_fabric_sku=None, work_iq=False,
):
    repository = repository_name(repository)
    subscription = identifier(subscription, "subscription-id")
    tenant = identifier(tenant, "tenant-id")
    region(location, "location")
    region(fabric_location, "fabric-location")
    capacity_sku(fabric_sku, "fabric-capacity-sku")
    if current_fabric_sku is not None:
        capacity_sku(current_fabric_sku, "current-fabric-capacity-sku")
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

    versions = {
        "node": tool_version(command(["node", "--version"], checkout), "node"),
        "git": tool_version(command(["git", "--version"], checkout), "git"),
        "az": tool_version(
            str(json_command(["az", "version", "--output", "json"], checkout).get("azure-cli", "")), "az"
        ),
    }
    old = [name for name, minimum in MINIMUM_VERSIONS.items() if versions[name] < minimum]
    if old:
        required = [f"{TOOL_NAMES[name]} >= {dotted(minimum)}" for name, minimum in MINIMUM_VERSIONS.items()]
        found = ", ".join(f"{TOOL_NAMES[name]} {dotted(versions[name])}" for name in old)
        raise PreflightError(f"Use {', '.join(required[:-1])} and {required[-1]} (found {found}).")

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

    try:
        principal = json_command(
            [
                "az", "ad", "sp", "show", "--id", FOUNDRY_API, "--query",
                "{appId:appId,oauth2PermissionScopes:oauth2PermissionScopes[].{value:value,isEnabled:isEnabled}}",
                "--output", "json",
            ],
            checkout,
        )
    except PreflightError:
        raise PreflightError(
            "This tenant has no readable Microsoft Foundry API principal (Azure Machine Learning Services). "
            f"An administrator can create it with: az ad sp create --id {FOUNDRY_API}"
        ) from None
    foundry_api = foundry_api_check(principal)

    regions = []
    for namespace, resource_type in REGIONAL:
        provider = json_command(
            [
                "az", "provider", "show", "--namespace", namespace, "--subscription", subscription, "--query",
                "{namespace:namespace,registrationState:registrationState,"
                + "resourceTypes:resourceTypes[].{resourceType:resourceType,locations:locations}}",
                "--output", "json",
            ],
            checkout,
        )
        regions.append(region_check(provider, namespace, resource_type, location))

    quota = json_command(
        [
            "az", "rest", "--method", "get", "--url",
            f"https://management.azure.com/subscriptions/{subscription}/providers/"
            + f"Microsoft.Fabric/locations/{fabric_location}/usages?api-version=2023-11-01",
            "--output", "json",
        ],
        checkout,
    )
    fabric = fabric_quota_check(quota, fabric_location, fabric_sku, current_sku=current_fabric_sku)

    return {
        "schema_version": 2,
        "status": "candidate",
        "target": {
            "repository": repository, "subscription_id": subscription, "tenant_id": tenant,
            "location": location, "fabric_location": fabric_location,
        },
        "tools": {name: dotted(version) for name, version in versions.items()},
        "foundry_api": foundry_api,
        "regions": regions,
        "fabric_quota": fabric,
        "fabric_git_token_available": bool(
            os.environ.get("TF_VAR_github_token") or os.environ.get("FABRIC_GITHUB_TOKEN")
        ),
        "work_iq": work_iq_report(work_iq),
        "unverified": [*UNVERIFIED, *([WORK_IQ_UNVERIFIED] if work_iq else [])],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--repository", required=True, metavar="OWNER/REPO")
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--location", required=True, help="the main region: Foundry, the app and its registry")
    parser.add_argument("--fabric-location", help="the Fabric capacity's region (default: --location)")
    parser.add_argument("--fabric-capacity-sku", default="F2", metavar="SKU",
                        help="the Fabric SKU you'll deploy (default: F2)")
    parser.add_argument("--current-fabric-capacity-sku", metavar="SKU",
                        help="the SKU of this deployment's existing capacity in --fabric-location: "
                             "require quota only for the CUs a larger SKU adds")
    parser.add_argument("--work-iq", action="store_true",
                        help="list the prerequisites of the optional Work IQ mail tool (work_iq_enabled), "
                             "which this helper cannot check; it never calls Microsoft 365 or Work IQ")
    args = parser.parse_args()
    try:
        result = preflight(
            args.checkout.resolve(), args.repository, args.subscription_id, args.tenant_id, args.location,
            args.fabric_location or args.location, args.fabric_capacity_sku,
            current_fabric_sku=args.current_fabric_capacity_sku, work_iq=args.work_iq,
        )
    except (OSError, PreflightError) as error:
        parser.exit(1, f"Runtime preflight blocked: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

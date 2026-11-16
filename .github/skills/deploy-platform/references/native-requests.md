# Native requests for guided DEV deployment

Read this after choosing the derived repository, execution host and delegated user. It is the request contract that the [deterministic runner](../scripts/deploy.py) implements: use it to review the runner's behavior and diagnose a blocked run. Do not hand-submit mutating requests that a resumable run owns. Terraform owns infrastructure; Fabric APIs own bootstrap and notebook execution; the Foundry data plane owns the agents' versions, and ACR Tasks builds the app's image.

## Contents

- [Authentication and response receipts](#authentication-and-response-receipts)
- [Tenant-wide consent](#tenant-wide-consent)
- [Native item import (default)](#native-item-import-default)
- [Git connection and synchronization](#git-connection-and-synchronization)
- [Operation and notebook completion](#operation-and-notebook-completion)
- [Resolve and verify target bindings](#resolve-and-verify-target-bindings)
- [Serverless Search identity](#serverless-search-identity)
- [Knowledge readiness and retrieval](#knowledge-readiness-and-retrieval)
- [Foundry agents and the app](#foundry-agents-and-the-app)
- [Teardown](#teardown)

## Authentication and response receipts

Complete the Entra bootstrap, administrator consent and generated-scope user sign-in in `docs/getting-started.md` first. Use the `fabric_user_auth` output's scope manifest for deployment preflight; app creation alone does not authorize these requests. Keep the custom app login scope distinct from the Fabric token audience below.

The pinned provider's `AzureCLICredential` can receive a first-party token with
`scp: user_impersonation`. That token need not enumerate the registration's
declared named scopes. Check the selected user, tenant, resource audience and
actual consent separately; do not falsely reject this credential route by
comparing unlike scope lists, accept an arbitrary application's token, or infer
write authorization from a successful read. The custom API's `access` token is
still not a Fabric bearer token.

Use `az rest --resource https://api.fabric.microsoft.com` for ordinary reads. For requests that can return `202`, capture the **status, response headers and body**: Azure CLI's normal JSON body alone does not provide the operation receipt. Do not enable debug/verbose HTTP logging to obtain headers.

For a reviewed POST, use this shell pattern with the known Fabric URL and a non-secret JSON request file. Supply the bearer header through curl's stdin configuration, not an expanded token in process arguments. Run on the execution host with shell tracing off:

```bash
set +x
set -euo pipefail
case "$request_url" in
  https://api.fabric.microsoft.com/v1/*) ;;
  *) printf 'Unexpected Fabric request origin\n' >&2; exit 1 ;;
esac
response_dir="$(mktemp -d)"
printf 'Response receipt directory: %s\n' "$response_dir"
token="$(az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken --output tsv)"
printf 'header = "Authorization: Bearer %s"\n' "$token" |
  curl --config - --silent --show-error --fail-with-body \
    --connect-timeout 15 --max-time 120 \
    --request POST --header 'Content-Type: application/json' \
    --data-binary "@$request_file" \
    --dump-header "$response_dir/headers" \
    --output "$response_dir/body.json" --write-out '%{http_code}\n' \
    "$request_url"
unset token
```

Use a separate, private scratch directory per request. Read only the needed result/error fields, retain non-secret operation/job IDs in session artifacts, and clean up the response files when no longer needed. Do not print entire headers, tokens, claims or provider debug logs. Do not follow redirects or send authorization to a `Location`/continuation URL without verifying the expected Fabric origin and target path.

Do not blindly retry POSTs after network timeouts: the server may already have accepted the operation. Inspect its receipt/current status first. For `429`, honor `Retry-After`; retry read-only polling transient failures within the phase deadline. Treat unexpected status/error codes as failures, not empty successful responses.

The runner's HTTP helper follows the same rule. It sends a read (`GET` or `HEAD`) again when it fails in transit (a connection reset, EOF or timeout) or with HTTP `429`, `500`, `502`, `503` or `504`: at most three attempts in all, after `Retry-After` (capped at 60 seconds) or a 2- then 4-second backoff. It sends every write once and never retries `401`, `403`, `404` or another `4xx`. The Fabric, Resource Manager, Foundry and Search helpers keep their own bounded loops within the phase deadline instead. Read-only Azure CLI lookups, such as `az ad sp show`, are retried the same way when the CLI reports a transient failure, never a refusal or a missing object.

## Tenant-wide consent

The runner grants tenant-wide consent for exactly a registration's declared
delegated scopes with the signed-in user's Azure CLI Microsoft Graph token:

| Registration | Resource principal | Scopes | When |
| --- | --- | --- | --- |
| `fabric_user_auth` | Power BI Service | its `delegated_scopes` | `consent` phase |
| The app's sign-in registration (`app` output) | Microsoft Foundry's API (`https://ai.azure.com`) | `user_impersonation` | `consent` phase once the platform apply created it, else the start of the `app` phase |
| `work_iq`, only while its `enabled` is true | Work IQ | `WorkIQAgent.Ask` | `verify` phase |

For each, it reads `GET https://graph.microsoft.com/v1.0/servicePrincipals/{client-sp-object-id}/oauth2PermissionGrants`
and keeps only `consentType: AllPrincipals` grants whose `resourceId` is the
tenant's resource principal. Assuming the user is an administrator, it
then [creates](https://learn.microsoft.com/en-us/graph/api/oauth2permissiongrant-post?view=graph-rest-1.0)
a missing grant with `POST /v1.0/oauth2PermissionGrants` and
`{"clientId", "consentType": "AllPrincipals", "resourceId", "scope"}`, where
`scope` is exactly the space-separated declared scopes. A partial
grant is [updated](https://learn.microsoft.com/en-us/graph/api/oauth2permissiongrant-update?view=graph-rest-1.0)
with `PATCH /v1.0/oauth2PermissionGrants/{id}`: Graph replaces `scope`, so it
keeps every existing value and adds only the missing declared scopes. It reads
the grant back before continuing.

HTTP `401`/`403` on the write means the user lacks a role that can grant
tenant-wide consent; the runner hands over the registration's
`api_permissions_url` for an administrator instead of retrying. With
`--manual-grants` it never writes. If the read itself is refused, the user can
neither grant nor verify consent, so only `--consent-confirmed` after an
administrator's confirmation continues.

The app's grant is optional: without it, each user consents to the app's
Microsoft Foundry access at first sign-in where the tenant allows that. With
`--manual-grants`, or when the grants cannot be read, the runner only reports
the app's `api_permissions_url` and continues. Every user still consents once
to the Fabric IQ connection and, with Work IQ, to the `work-iq` connection,
through the link the app shows; no tenant-wide grant replaces that.

## Native item import (default)

With `fabric_git_enabled = false`, no PAT or Fabric Git connection is needed.
The runner sends the reviewed, tracked files beneath each `workspace/` item
folder to:

```http
POST https://api.fabric.microsoft.com/v1/workspaces/{devWorkspaceId}/items/bulkImportDefinitions?beta=true
```

```json
{
  "definitionParts": [
    {
      "path": "/Scenario.Lakehouse/.platform",
      "payload": "<base64-file-content>",
      "payloadType": "InlineBase64"
    }
  ],
  "options": {
    "allowPairingByName": false,
    "deploymentPlan": {
      "logicalId": "<Bootstrap-logical-id>",
      "referenceType": "ByLogicalId"
    }
  }
}
```

The example shows one part; include every definition part of each missing item,
including `.platform`, notebook source, lakehouse metadata, TMDL and `plan.yml`.
Preserve bytes and logical IDs. The delegated user needs `Item.ReadWrite.All`
and `Item.Execute.All`; do not substitute a service principal for notebook execution.

List current items first and match logical ID, type, name and workspace.
Refuse collisions, changed recorded definitions and unrecorded existing items.
Import additions only. Record the operation before polling and validate every
`importItemDefinitionsDetails` entry: expected identity, `operationType: Create`
and `operationStatus: Succeeded`. Missing results, `Failed` and
`SucceededDespiteFailures` block readiness.

Only an explicit `--bootstrap-fallback notebooks` permits retrying a
`FeatureNotAvailable` plan rejection without the plan. The runner then runs the
existing seed/bind checks. A timeout or missing receipt is not permission to
resubmit an import.

### Update deployed definitions

Only `--update-definitions` replaces an existing item's definition. Compare the
digest the runner recorded for each live item with the reviewed bytes, and refuse
lakehouses, items without a recorded definition and Git-synchronized workspaces.
Require an `item-update` approval of the sha256 over the workspace and each item's
ID, type, logical ID and definition digest; `--yes` never grants it. Then, per item:

```http
POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items/{itemId}/updateDefinition?updateMetadata=true
```

Send item-relative parts, including `.platform`, which applies only with
`updateMetadata=true`; notebooks default to the `fabricGitSource` format. Record the
request before submitting it and observe its operation; an unconfirmed update is
not resubmitted without `--retry-failed`. Run updated bootstrap notebooks again and
re-verify the binding: an updated ontology loses its target-local partitions, so
the binder runs before readiness.

References: [Bulk Import](https://learn.microsoft.com/rest/api/fabric/core/items/bulk-import-item-definitions),
[Update Item Definition](https://learn.microsoft.com/rest/api/fabric/core/items/update-item-definition),
[plan attachment](https://learn.microsoft.com/fabric/cicd/deployment-plan/deployment-plan-automation#import-item-definitions).

## Git connection and synchronization

This path runs only with `fabric_git_enabled = true` and a repository-scoped PAT.

Use `dev_workspace_id` and `github_connect_request` from Terraform output, not a display-name guess. Resolve the plan logical ID from the **reviewed** `workspace/Bootstrap.DeploymentPlan/.platform`.

| Step | Native request | Handling |
| --- | --- | --- |
| Inspect connection | `GET /v1/workspaces/{workspaceId}/git/connection` | Inspect `gitConnectionState` and compare repository/branch/directory with the intended target. Require full, not selective, synchronization. |
| Connect if `NotConnected` | `POST /v1/workspaces/{workspaceId}/git/connect` | Use the Terraform `github_connect_request` JSON unchanged; it contains the configured connection ID, not the PAT. |
| Check caller's Git credentials | `GET /v1/workspaces/{workspaceId}/git/myGitCredentials` | If missing, use the documented PATCH on the same endpoint with `source: "ConfiguredConnection"` and the approved `connectionId`. Do not disconnect the workspace. |
| Initialize if `Connected` | `POST /v1/workspaces/{workspaceId}/git/initializeConnection` | Use `{"initializationStrategy":"PreferRemote"}` only after confirming the intended incoming content; poll if asynchronous. |
| Inspect synchronized workspace | `GET /v1/workspaces/{workspaceId}/git/status` | Read the current head, remote commit and changes, polling if asynchronous. Stop for conflicts/deletions or concurrent authoring. |
| Update when required | `POST /v1/workspaces/{workspaceId}/git/updateFromGit?beta=true` | Attach the plan in the full request below, after reviewing the exact incoming revision. |

For Initialize Connection, handle every `requiredAction`: `UpdateFromGit` continues; `None` needs no sync; `CommitToGit` or an unknown value stops for review. Initialize Connection itself cannot accept a plan.

After explicit approval of the incoming definitions, construct the following body with `jq`/JSON serialization, using the actual returned head and reviewed remote SHA:

```json
{
  "workspaceHead": "<returned-workspace-head>",
  "remoteCommitHash": "<reviewed-full-remote-commit-sha>",
  "options": {
    "allowOverrideItems": true,
    "deploymentPlan": {
      "logicalId": "<Bootstrap-logical-id-from-reviewed-platform-file>",
      "referenceType": "ByLogicalId"
    }
  }
}
```

`workspaceHead` may be JSON `null` only immediately after initialization when that is what Fabric returned. Do not use the string `"null"` or omit a required current head on later updates. Refuse an unexpected remote SHA. Omit `conflictResolution` rather than silently choosing `PreferRemote`; `allowOverrideItems` is explicit consent to apply reviewed incoming definitions, not permission to discard conflicts or delete data.

If already `ConnectedAndInitialized`, reuse the connection. If the desired SHA is already synchronized and only expected generated binding changes remain, inspect/reuse the live binding instead of overwriting it. On a no-op Git update, notebook actions are not guaranteed to run.

References: [Connect](https://learn.microsoft.com/rest/api/fabric/core/git/connect), [Get connection](https://learn.microsoft.com/rest/api/fabric/core/git/get-connection), [Update my Git credentials](https://learn.microsoft.com/rest/api/fabric/core/git/update-my-git-credentials), [Initialize](https://learn.microsoft.com/rest/api/fabric/core/git/initialize-connection), [Status](https://learn.microsoft.com/rest/api/fabric/core/git/get-status), [Update](https://learn.microsoft.com/rest/api/fabric/core/git/update-from-git), [plan extension](https://learn.microsoft.com/fabric/cicd/deployment-plan/deployment-plan-automation).

## Operation and notebook completion

For a Fabric operation's `202`, retain `x-ms-operation-id`, validated `Location` and `Retry-After`. Poll `GET /v1/operations/{operationId}` until `Succeeded`; continue only for `NotStarted`/`Running`, honor polling delays and fail on unsuccessful/unknown states. Read `/result` when the operation provides a result, including Initialize Connection and Get Status. Do not turn a missing/non-JSON result for a no-body operation into an empty JSON object.

Some receipts advertise a regional service hostname. Do not forward credentials
to that hostname automatically. Use the documented canonical
`https://api.fabric.microsoft.com/v1/operations/{operationId}` endpoint, retaining
the returned operation ID. A receipt-parsing failure is not permission to
resubmit the accepted mutation; recover and observe that same operation.

A deployment plan failure can leave earlier groups completed. Inspect the operation result and notebook runs before resuming. Never submit a duplicate while another operation/job is active.

When an already-deployed notebook genuinely needs running outside the plan, use its **physical item ID** with:

```http
POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/notebooks/{notebookId}/jobs/execute/instances?jobType=RunNotebook
```

Use the notebook-specific route and `RunNotebook` job type from the notebook API guide; the deployment plan's `job.type: Execute` is not a scheduler job type to copy blindly. Send the plan's parameters for that notebook as typed `executionData.parameters` (`{"<name>": {"value": ..., "type": "string|int|float|bool"}}`), not an assumed top-level `parameters` array. Run the seeder to completion, then the binder. Do not invent a default lakehouse attachment.

This job API returns a **job-instance Location**, not a generic `/operations/{id}` receipt. Validate its workspace/item, honor `Retry-After`, and poll that instance until `Completed`. `Failed`, `Cancelled`, a timeout or an unknown status does not satisfy readiness. Inspect non-secret failure details and preserve the receipt. A phase observation timeout does not cancel the running cloud operation.

Use the canonical Job Scheduler read:
`GET /v1/workspaces/{workspaceId}/items/{notebookId}/jobs/instances/{jobInstanceId}`.
It reports the normalized `RunNotebook` job type. The notebook-specific
`.../notebooks/{notebookId}/jobs/execute/instances/{jobInstanceId}?beta=true`
projection can instead report `jobType: "execute"` and nested compute/exit
properties; that representation is not evidence that the submitted job changed.
Never rerun a notebook because the two response projections differ.

References: [LROs](https://learn.microsoft.com/rest/api/fabric/articles/long-running-operation), [run item job](https://learn.microsoft.com/rest/api/fabric/core/job-scheduler/run-on-demand-item-job), [get job instance](https://learn.microsoft.com/rest/api/fabric/core/job-scheduler/get-item-job-instance), [notebook execution](https://learn.microsoft.com/fabric/data-engineering/notebook-public-api).

## Resolve and verify target bindings

Enumerate `GET /v1/workspaces/{devWorkspaceId}/items`, following pagination. Use `.platform` identities and current display names/types to resolve the intended items; require unique matches, then read their type-specific metadata. Do not select the first name match in another workspace.

Read the lakehouse metadata and the new-experience ontology metadata/definition. Get Definition can itself be asynchronous. Check the current workspace, expected ontology logical ID/generation and source bindings against `docs/ontology-knowledge.md`. Decode definition parts locally; do not rewrite TMDL from this deployment skill.

Require the successful binder's scoped SQL refresh, the shared `Sql.Database` expression on this workspace lakehouse's SQL analytics endpoint, and the current-workspace lakehouse pins on every partition. Graph materialization identifies the source by those pins, and Fabric strips them from an `AzureStorage.DataLake` (OneLake) expression. Get Definition returns the SQL expression as ALM tokens (`ONT_ALM_SQL_ENDPOINT`, `ONT_ALM_SQL_DATABASE-Lakehouse:<workspace>:<lakehouse>`); those must name this DEV lakehouse. Git-normalized zero GUIDs, logical IDs and placeholder workspace names are not runtime source IDs. Discover the physical lakehouse and ontology GUIDs only from this DEV target before calling `scripts/configure_knowledge.py`.

## Serverless Search identity

Preflight reads `POST https://management.azure.com/providers/Microsoft.Search/fetchOfferings?api-version=2026-09-01-preview` on every run and, when it is readable, requires the chosen `search_sku` in the Search region's `skus`; an unreadable response is reported as unverified. Serverless also needs `GET /subscriptions/{subscriptionId}/providers/Microsoft.Features/providers/Microsoft.Search/features/Serverless?api-version=2021-07-01` to report `Registered`.

The serverless preview ends in `Failed` when a system- or user-assigned identity is requested at creation, so Terraform creates it without one. After the platform apply, and again before the knowledge plan, the runner reads the `search_management_url` output with an ARM token. It waits for `Succeeded`/`running`, refuses a `Failed` service even when it shows a principal, and sends `PATCH {"identity":{"type":"SystemAssigned"}}` once when there is no `identity.principalId`. The preview answers HTTP 500 while applying the identity, so the runner accepts only a later GET that shows the principal on a healthy service. Any other error, or no principal within its own three-minute window, blocks. Never recreate the service to clear this.

A full knowledge plan that creates or replaces serverless Search is never applied as is: the new service would have no principal for its grants. The runner discards it, applies a fresh plan with `-var=search_identity_ready=false`, enables and verifies the identity, then applies a third, normal plan. Each saved plan keeps its own variables and approval.

## Knowledge readiness and retrieval

Use Terraform's `knowledge_source_status_urls`, `search_endpoint`, `knowledge_base_name`, `ontology_check_knowledge_base_name`, `knowledge_source_names` and `ontology_knowledge_sources` outputs. Use the API version from the deployed configuration, not a guessed newer preview version. Wait for every folder source's ingestion, whether or not the agents' knowledge base attaches it.

The deployer receives `Search Index Data Reader`; Azure resource ownership by itself would not grant query data access. Acquire the signed-in user's token for `https://search.azure.com` in memory. The same user token can authenticate the direct Search call and supply `x-ms-query-source-authorization`, provided the user has Search and Fabric access and the required delegated consent. Do not use a service-principal token as that end-user identity.

Pass both headers using the same stdin-config technique, only to the verified Search endpoint:

```bash
printf 'header = "Authorization: Bearer %s"\nheader = "x-ms-query-source-authorization: %s"\n' \
  "$search_user_token" "$search_user_token" |
  curl --config - --silent --show-error --fail-with-body \
    --connect-timeout 15 --max-time 120 \
    --request POST --header 'Content-Type: application/json' \
    --data-binary "@$retrieval_request_file" \
    --output "$retrieval_response_file" "$verified_retrieve_url"
```

Send the ontology question to the **check-only** knowledge base's `retrieve` URL (`ontology_check_knowledge_base_name`), never to the agents' knowledge base (`knowledge_base_name`), which holds no ontology. Quote the values in the question, as in `which Asset rows have the Status value "Maintenance"`: Fabric can read a bare value as an empty one. Merge the following source parameters into the `messages` request in `docs/ontology-knowledge.md`, using the actual ontology knowledge-source name:

```json
{
  "knowledgeSourceParams": [
    {
      "kind": "fabricOntology",
      "knowledgeSourceName": "<actual-ontology-knowledge-source-name>",
      "includeReferences": true,
      "includeReferenceSourceData": true
    }
  ]
}
```

Require a successful `activity` entry of type `fabricOntology`, a `references` entry of the same type with matching DEV `workspaceId`/`ontologyId`, and `sourceData.fabricRawData` grounded in the bound tables. A document-only answer is a failed ontology check.

Check the indexed folder sources separately, through the agents' knowledge base's `retrieve` URL, with the starter-document question and the `documents` source name from `knowledge_source_names`. That knowledge base runs at minimal retrieval effort, which refuses `messages`: send the question as a search intent, `{"intents": [{"type": "semantic", "search": "<question>"}]}`. Its parameter discriminator is `kind: "indexedOneLake"`, not `searchIndex`; request references/source data there too. Require successful ingestion and a reference from that source. Preserve denied/failed-source errors instead of accepting partial answers as fully ready. Never persist either token in config, state, request JSON or logs.

Check the Web IQ public-context knowledge base once, through its own `retrieve` URL from the `public_context_knowledge_base_name` output and the same API version. Use `kind: "mcpServer"` with the `public_context_knowledge_source_name` output, and a public question about the asset type, such as pump maintenance guidance, with no site name, asset name or other private detail. Require an `mcpServer` activity entry without an error and at least one `mcpServer` reference. Every retrieval is a billable Web IQ search, so the runner makes one attempt and never retries it automatically.

References: [Fabric ontology retrieval and response fields](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-fabric-ontology#query-a-knowledge-base), [ontology runtime parameters](https://learn.microsoft.com/javascript/api/@azure/search-documents/fabricontologyknowledgesourceparams?view=azure-node-preview), [OneLake runtime parameters](https://learn.microsoft.com/javascript/api/@azure/search-documents/indexedonelakeknowledgesourceparams?view=azure-node-preview).

## Foundry agents and the app

The `agent` phase calls Foundry Agent Service's data plane with the deployer's token for `https://ai.azure.com/.default`, on the `foundry_project_endpoint` output only, with `api-version=v1`:

- `GET {project}/agents/{name}`: the agent with `versions.latest`, whose `metadata.definition_sha256` the runner compares with the rendered body's; 404 means missing.
- `POST {project}/agents` with `{name, description, definition, metadata}` creates an agent; `POST {project}/agents/{name}/versions` with `{description, definition, metadata}` publishes a new version. One `agent-publish` approval names every agent to create or publish. The runner records the attempt first and reads the agent back afterward; it retries only throttling and transient reads.

Each `foundry_agents` entry is rendered from its reviewed `agents/` directory with values from the `agent_settings`, `foundry_tool_connections` and `fabric_iq_connections` outputs. The phase first refuses uncommitted or untracked changes under `agents/` (`git status --porcelain --untracked-files=all -- agents`), so a published version always matches committed definitions. While the `work_iq` output's `enabled` is false, the with-IQ agent's optional `WorkIQMail` tool is left out of the body. The digest covers the body, so turning `work_iq_enabled` on or off publishes a new with-IQ version on the next agent phase. The apply that flips it adds or deletes the `work-iq` connection at once, so run `deploy.py up` through the agent phase after such a change; the knowledge phase reminds you until then.

The `app` phase first grants the app registration's single delegated scope tenant-wide, as [Tenant-wide consent](#tenant-wide-consent) describes. It then uses Azure CLI against the `container_registry` output:

- The image tag is the first 12 hex characters of the sha256 of `git ls-tree -r HEAD -- app`, once `git status --porcelain --untracked-files=all -- app` is empty: only committed files under `app/` are built.
- `az acr repository show-tags --name {registry} --repository app` finds an existing image with that tag.
- Otherwise, after an `app-build` approval, `az acr build --registry {registry} --image app:{tag} --file app/Dockerfile --platform linux/amd64 app` runs from the repository root. For a local context, Azure CLI reads `--file` from the working directory and uploads it beside the context, which `app/.dockerignore` filters.
- The reviewed `app` plan carries `-var=app_image_tag={tag}`. The runner writes that tag into the one `app_image_tag` line of `infra/terraform.tfvars` only after the plan's approval, then applies it. The container app itself is Terraform's.

Readiness reads the `app` output's URL: `GET {url}/healthz` must return `200`, polled every 10 seconds while a revision starts, and `GET {url}/api/config` must return the `app` output's `client_id` as `clientId`. That proves the deployed image and its sign-in settings, not a user's consent to the agents' tools or an answer.

## Teardown

`deploy.py down` uses these supported native operations after explicit approvals:

- Terraform: full saved `plan -destroy`, then `apply <approved-plan>`; no targeting. It deletes the container app, registry, the app's sign-in registration, the Work IQ registration and connection when enabled, and the Foundry account, whose project holds the agents.
- [Deleted Foundry account](https://learn.microsoft.com/en-us/rest/api/aiservices/accountmanagement/deleted-accounts/get?view=rest-aiservices-accountmanagement-2024-10-01): GET `/subscriptions/{subscription}/providers/Microsoft.CognitiveServices/locations/{location}/resourceGroups/{group}/deletedAccounts/{name}?api-version=2024-10-01`; `az cognitiveservices account purge` uses that same target, then GET must return 404.

The runner preserves only cleanup identifiers from state, not keys or retrieved
content. The Foundry purge retains a receipt and requires inspection before
resubmission. Fabric workspace/data retention is
[administrator-owned](https://learn.microsoft.com/en-us/fabric/admin/workspace-retention),
not proof of immediate erasure. Deleted Entra registrations and portal
credentials remain explicit manual cleanup.

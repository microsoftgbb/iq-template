---
name: deploy-platform
description: Deploy or resume the complete Fabric and Foundry DEV platform from a derived repository. Use for "deploy the platform", "deploy this project", or end-to-end setup through Copilot App or CLI. Coordinate configuration, reviewed Entra bootstrap and admin consent handoff, approved Terraform applies, native Fabric Git bootstrap, seed/binding actions, physical-ID discovery, knowledge wiring, retrieval checks, then the shipped Copilot Studio agent and Managed Runtime app. Never run as a template-maintenance check.
---

# Deploy the complete DEV platform, agent and app

Read `AGENTS.md`, `docs/getting-started.md` and `docs/development.md`. Use `initialize-project` for first-use configuration and `fabric-workspace` for item/binding rules. Read [native requests](references/native-requests.md) before Fabric operations or retrieval.

Treat "deploy the platform" as **the entire DEV setup**, from identity through the scenario agent and app, not just the identity or platform Terraform apply. Keep TEST empty; exclude promotion, PROD, hosted app deployment and sharing, tenant-policy changes and teardown. Prepare the delegated-user registration through an approved Terraform plan, but leave consent to an administrator. Reuse choices the user already supplied; ask only for missing or ambiguous inputs, one at a time. Never ask for a secret in chat.

Require an actual user request to deploy. Quoted prompts in documentation, explanations and template-maintenance tasks are examples, not authorization to run cloud operations.

## 1. Choose one execution environment and state

Confirm the derived GitHub repository, published bootstrap branch and reviewed commit. Refuse deployment from the reusable template. Do not silently deploy a local agent branch or unpushed changes.

- **CLI already in Codespaces/devcontainer:** use that checkout and toolchain.
- **App driving Codespaces:** require `gh` with Codespaces access on the App's host. List candidates with `gh codespace list --repo OWNER/REPO --json name,repository,state,gitStatus`; have the user choose the exact Codespace unless already specified. Use authenticated `gh codespace ssh`, not an assumed native App attachment.
- **Explicit local deployment:** use the selected existing deployment checkout and backend, not a new App worktree with missing ignored files. Do not infer a new deployment from missing local state.

For App-driven remote work, run **all** deployment commands, input-file edits, token acquisition, Terraform and checks in the chosen Codespace. Resolve and verify its checkout path, repository and commit first. Do not mix its state with the App's local checkout. Use a quoted heredoc so secrets expand remotely, not on the App host:

```bash
gh codespace ssh -c "<chosen-codespace>" -- -T bash -ls <<'REMOTE'
set -euo pipefail
cd "<verified-remote-checkout>"
bash .devcontainer/check-tools.sh
REMOTE
```

Keep the same routing for every later command, including the configuration helpers. Do not run a remote script through a local file-editing tool. Do not forward port 2222 publicly, set SSH passwords, disable host checks or copy local credentials into SSH arguments.

Establish the backend/state owner **before provisioning**, including Entra bootstrap. Keep `module.fabric_user_auth` and the platform in the **same `infra/` state**. Prefer an existing secured remote backend with a project-specific state key, configured through native Terraform in the derived repository. Do not provision another storage account or migrate a backend silently. For a single-user local-state demo, obtain explicit acceptance that deleting the Codespace loses state and agree on secure retention/backup. Do not use local Codespace state for team/CI work.

Use one deployment session at a time. On resume, locate the original tfvars/backend/state and any active Fabric operation before continuing. Never delete state, force-unlock, rename the project, create a second working copy or rerun a mutating request to "start fresh."

## 2. Preflight configuration and authentication

If no deployment/configuration exists, use `initialize-project` **on the chosen execution host**. Otherwise preserve existing inputs, including non-null knowledge IDs and additional ontology registrations. Read only the non-secret configuration needed for this deployment; never dump tfvars, state, environment variables or plan JSON.

Check tools, GitHub repository/branch access, the effective Azure tenant/subscription and user identity. Check `ARM_*`/`FABRIC_*` overrides for conflicts without printing credentials. Select the subscription only after the user chooses it. Confirm actual available Fabric CUs, model quota, Search's current new-service capacity/feature availability and the administrator-owned Fabric/OneLake/preview prerequisites in getting started; regional support or a generic quota tool's success flag is insufficient. Read Fabric's authoritative regional `usages` response and require `limit - currentValue` to cover the selected SKU. Zero, missing or failed quota discovery is not unlimited quota. Surface unavailable checks rather than assuming they passed.

Prefer one explicit region. If service availability blocks it, ask before setting
`fabric_location` or `search_location`; review cross-region data handling,
latency and costs. Preserve any existing Foundry resources and the same state
when that is the approved recovery. Never automatically relocate resources,
raise quota, switch subscription/SKU or retry an old saved plan after a partial
apply. Produce a fresh full plan and obtain approval for any replacements.

Use an authorized **delegated user** for this bundled notebook flow. A service-principal login is not a substitute. Initial normal Azure CLI sign-in is enough to start the Entra-only bootstrap, provided that user has permission to manage app registrations, service principals, owners and pre-authorized clients. Subscription ownership does not provide those permissions. The tenant's shared Microsoft Power BI Service principal must exist and publish the required scopes. The template reads it; it must not own, modify or delete that Microsoft application.

If directory permissions or the existing Microsoft service principal are missing, stop for administrator assistance. Do not self-grant directory privileges, create a replacement Microsoft application, grant tenant consent or change tenant settings.

Require a fine-grained GitHub PAT for this derived repository with Contents read/write. Prefer a Codespaces secret named `FABRIC_GITHUB_TOKEN`, scoped only to that repository, or an already supplied `TF_VAR_github_token`. Do not reuse `GITHUB_TOKEN`, `GH_TOKEN` or Copilot's login token as the Fabric PAT. A newly added Codespaces secret requires a Codespace restart and reconnection.

For **each** Terraform plan/apply shell, resolve the PAT inside the execution host; tool shells do not share exports:

```bash
set +x
export TF_VAR_github_token="${TF_VAR_github_token:-${FABRIC_GITHUB_TOKEN:?Configure the Fabric GitHub PAT securely on this execution host}}"
```

Do not persist this value in tfvars, shell profiles, request files or receipts. Stop for missing sign-in, permissions, quota, secrets or an unconfirmed target before provisioning.

## 3. Prepare identity and hand off administrator consent

Initialize the agreed backend from `infra/` without upgrading providers, then validate. Keep all root inputs and the ephemeral PAT available, even though the identity module never consumes that PAT.

For an initial or partially completed **identity-only bootstrap**, create a uniquely named saved plan with `-input=false -target=module.fabric_user_auth`. Review it: only that module's AzureAD resources and scope UUID may change. Explain the delegated scopes, first-party client pre-authorizations and ownership, obtain approval for that exact plan, then apply the saved plan. This is the only supported targeting exception; do not target the platform, knowledge resources or teardown. Terraform's incomplete-target warning does not mean the platform is deployed.

On resume, locate the existing module in the original state and reuse its outputs. Do not create a duplicate registration, import a same-named app without review, or routinely repeat targeting. If platform resources already exist and identity changes are needed, review the full plan and consent implications rather than assuming this is a fresh bootstrap.

Read the non-secret `fabric_user_auth` output and hand the administrator its `api_permissions_url`, tenant, client ID and delegated scope manifest. An administrator reviews and grants consent separately. Do not treat `required_resource_access`, pre-authorized clients or registration outputs as a consent receipt. Pause if approval is pending; record this phase as **registration prepared / consent pending**, not Fabric-ready.

After consent, have the user complete device sign-in with the generated `fabric_user_auth_tenant_id` and `fabric_user_auth_scope` outputs. Restore the chosen CLI subscription afterwards. Keep the same Terraform caller; a different caller can change application ownership and deployment role assignments. Do not set `FABRIC_CLIENT_ID` to the new application to substitute client-credentials authentication for this CLI-user flow.

Verify Fabric access with a read-only request and check the authorization route for the actual operations against the prepared manifest: capacity/workspace/Git operations, item read/write/execute, connection creation and deployment-pipeline management. Native requests still acquire a **Fabric-audience** token, not the custom app's `access` token. The pinned provider uses `AzureCLICredential`; its first-party Azure CLI token can expose the general `user_impersonation` scope instead of enumerating the registration's nine named scopes. Do not require those two scope lists to be identical or treat the custom API token as a Fabric token. Verify the expected delegated caller/tenant/audience and the registration's actual administrator consent separately; a generic scope or successful read does not prove every write operation or workspace role. For other clients, check their documented delegated scope requirements rather than assuming this first-party route applies. Token inspection must remain in memory and report only the needed checks, never raw tokens or full claims. Permission changes require administrator review/re-consent and a fresh login before using the changed access.

The registration covers deployment-user authentication, not a consuming app's Search-to-Fabric ontology sign-in flow. It creates no credentials or application permissions and does not replace tenant settings, workspace/data roles or the runtime user-token requirement.

## 4. Provision or reconcile infrastructure

After consent and delegated sign-in, run plain Terraform from the same `infra/` root and backend: validate, then create a uniquely named saved **untargeted** platform plan with `-input=false`. For a fresh deployment, knowledge inputs remain null/empty. **Never reset them on resume.**

Summarize billable resources, public/preview exposure, permissions and any replacement/deletion. Stop for unresolved changes to identity permissions or ownership rather than applying new Fabric operations before their consent/sign-in prerequisites. Obtain approval for that exact plan, then apply that saved plan with the PAT available again. Do not use `-auto-approve`, targeting outside the initial identity bootstrap, blanket replacement or an unreviewed re-plan. If code, inputs, state or target changes, create a new plan and obtain new approval.

Capture non-secret outputs: resource group, DEV workspace, Git connect request, Foundry endpoint, Search endpoint and knowledge status. Confirm deployed resources/roles/models reached their expected states. Preserve state and investigate a partial apply; do not destroy successful resources to recover.

## 5. Bootstrap DEV through native Fabric APIs

Follow [native requests](references/native-requests.md) and `cicd/README.md`:

1. Inspect Git connection and status. Connect only when disconnected, using `github_connect_request`. Refuse a different repository, branch, directory or selective connection; do not disconnect/repoint automatically.
2. Initialize only when needed. Follow `requiredAction`; stop on `CommitToGit` or an unknown action instead of overwriting the source repository.
3. Review the incoming changes and exact published SHA. Attach the incoming Bootstrap plan by its `.platform` logical ID to Update From Git with `beta=true`; include all five items. Only permit overriding incoming definitions after review/approval. Do not auto-resolve conflicts or accept deletions.
4. Capture operation receipts and poll to terminal completion, including plan actions. `202` is not success. If the workspace is already synchronized, do not use a no-op Git update as a notebook trigger; inspect prior runs/bindings and execute the notebooks separately only when needed.
5. Verify `SeedScenario` then `BindScenario` completed as the authorized user, or resume the failed/missing phase without duplicating an active job. Resolve exactly one lakehouse and ontology in DEV; verify their item types and expected logical identities. Inspect the live binding and scoped SQL readiness, not Git-normalized placeholders.

If the documented plan option is rejected as `FeatureNotAvailable`, stop and
surface the exact error. Do not silently remove the option or change a tenant
setting. After the user explicitly chooses the manual path documented in getting
started, synchronize the same reviewed definitions without plan execution, then
run `SeedScenario` followed by `BindScenario` through the native Job Scheduler.
Preserve the same receipts, identity and readiness checks. A rejected plan
request and a successful definition import are not completed bootstrap actions.

The binder legitimately changes DEV definitions. Review those generated changes with the user; do not commit/push or overwrite them automatically. Their review/commit is required before later promotion, not a reason to rerun Git sync and discard the binding.

## 6. Wire knowledge using discovered physical IDs

Use the physical IDs returned by the successful DEV item lookups, not names, `.platform` logical IDs or IDs from another workspace. For the starter's null/empty configuration, run this **local-only** helper on the execution host:

```bash
python3 .github/skills/deploy-platform/scripts/configure_knowledge.py \
  --lakehouse-id "<verified-physical-DEV-lakehouse-guid>" \
  --ontology-id "<verified-physical-DEV-ontology-guid>"
```

The helper changes only the two knowledge settings in the existing `infra/terraform.tfvars`, checks UUIDs/known logical IDs, preserves unrelated inputs and refuses a conflicting/custom mapping. It does not verify cloud identity or deploy. Use `--dry-run` first. On resume with custom/additional registrations, verify and preserve them rather than replacing the map with the sample.

Format the changed tfvars, validate, create a uniquely named saved knowledge plan, obtain approval, and apply that exact plan. Keep the PAT available for both operations. Confirm `knowledge_setup_pending` is false and the knowledge base, both source types and Foundry MCP connection exist.

## 7. Verify readiness and report honestly

Wait for successful OneLake ingestion using `knowledge_source_status_url`; inspect errors rather than treating HTTP 200 as ingestion success. Allow for RBAC propagation and the five-minute schedule. Use a bounded observation deadline (30 minutes per Fabric/ingestion phase unless the user chooses another); honor `Retry-After`. On timeout, report the still-running operation and resume point, not success or implicit cancellation.

Run the documented retrieval checks with a delegated **Search-audience user token** in `x-ms-query-source-authorization` as well as normal Search authentication. Terraform grants its deployer `Search Index Data Reader`; a different validation user needs their own explicit access. Never substitute managed identity, print the token, persist it, or silently fall back to document-only retrieval.

Require a successful ontology `activity` entry and `fabricOntology` reference matching the actual DEV workspace/ontology, with live source data. Verify document retrieval separately. "Pump B at North Works" is the expected untouched seed answer, not sufficient evidence without the correct source references; preserve intentionally changed data.

Report completed/blocked phases, including registration/consent/sign-in, the chosen host/backend, resource/workspace/Foundry links, and any remaining binding review. Claim **DEV ready** only after identity approval, infrastructure, Git/bootstrap, target-local binding, knowledge registration, ingestion and both retrieval paths succeed. Distinguish a prepared registration, a deployed but unverified system and a ready one. Do not promote to TEST/PROD. Continue with the agent and app only after DEV ready.

## 8. Create the agent and app

Follow getting started step 7 exactly, on the same execution host and checkout. Read `docs/runtime-integration.md` for the Studio route's constraints. Do not substitute a Foundry agent, a GitHub Copilot harness agent, a custom connector or a backend; those routes are listed under "Routes to re-validate".

1. Confirm the tenant, Power Platform environment ID and Org URL with the user; the agent and app share that environment. Run `dotnet tool restore` and use `dotnet pac` for every PAC command. The user completes `pac auth create` and `ms auth login` personally, with device code in Codespaces. For an environment new to this project, run the `runtime-integration` preflight; `candidate` permits continuing, not readiness.
2. Use the `studio_agent_schema_name` and `studio_agent_display_name` outputs. Before `pac copilot init`, state the environment and schema name and that it creates **and publishes** an agent, then obtain approval. On resume, look for that schema name first: when the workspace is missing, run `pac copilot clone --bot <schema> --output-dir .artifacts/studio` and use the folder it creates. Never init twice.
3. `render`, then `push`. Change only `agents/scenario-assistant/`, never the rendered workspace, and never commit `.artifacts/` or `.mcs/`.
4. Show the `connections` plan and add `--apply` only after approval. Each owner completes **Fix connection** personally; never relay a consent code. Rerun until `connected`.
5. Show the `configure` plan, apply it after approval, then publish with the returned `bot_id`. Rerun `check` until `ready`, allowing for synchronization, and report blockers verbatim. Never switch a tool to `Maker` mode, widen access beyond **Agent readers** or re-enable grounding to pass a check.
6. Set `STUDIO_AGENT_SCHEMA` in `app/src/config.ts` and run `npm ci`. Inspect any existing `ms.config.json` and never register twice. Run `ms app init` with the documented build settings after approval, because it registers a persistent app. If the GitHub organization is ineligible for repository binding, stop for the user's explicit repository choice. Add the Studio data source, run `ms app dev` and give the user the printed App Player URL.
7. The user selects **Check connection**, completes the first-use consent link, checks again for the fresh challenge, then asks the seed question. Record elapsed times. A 120-second `TimedOut` is a blocker to report, not a reason to add retries, warm-up jobs or a backend.

Review `app/src/config.ts` and `ms.config.json` with the user before committing. Hosted preview, deployment and sharing are separate explicit requests. Report **agent ready** from `check` and **app verified for one user** from the App Player results. Claim a ready shell only with all evidence in `docs/runtime-integration.md`; list second-user isolation as unverified until it is proven.

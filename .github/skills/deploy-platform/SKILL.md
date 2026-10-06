---
name: deploy-platform
description: Deploy or resume the complete Fabric and Foundry DEV platform from a derived repository. Use for "deploy the platform", "deploy this project", or end-to-end setup through Copilot App or CLI. Coordinate configuration, approved Terraform applies, native Fabric Git bootstrap, seed/binding actions, physical-ID discovery, knowledge wiring and retrieval checks. Never run as a template-maintenance check.
---

# Deploy the complete DEV platform

Read `AGENTS.md`, `docs/getting-started.md` and `docs/development.md`. Use `initialize-project` for first-use configuration and `fabric-workspace` for item/binding rules. Read [native requests](references/native-requests.md) before Fabric operations or retrieval.

Treat "deploy the platform" as **the entire DEV setup**, not just the first Terraform apply. Keep TEST empty; exclude promotion, PROD, consuming agents, tenant-policy changes and teardown. Reuse choices the user already supplied; ask only for missing or ambiguous inputs, one at a time. Never ask for a secret in chat.

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

Establish the backend/state owner **before provisioning**. Prefer an existing secured remote backend with a project-specific state key, configured through native Terraform in the derived repository. Do not provision another storage account or migrate a backend silently. For a single-user local-state demo, obtain explicit acceptance that deleting the Codespace loses state and agree on secure retention/backup. Do not use local Codespace state for team/CI work.

Use one deployment session at a time. On resume, locate the original tfvars/backend/state and any active Fabric operation before continuing. Never delete state, force-unlock, rename the project, create a second working copy or rerun a mutating request to "start fresh."

## 2. Preflight configuration and authentication

If no deployment/configuration exists, use `initialize-project` **on the chosen execution host**. Otherwise preserve existing inputs, including non-null knowledge IDs and additional ontology registrations. Read only the non-secret configuration needed for this deployment; never dump tfvars, state, environment variables or plan JSON.

Check tools, GitHub repository/branch access, the effective Azure tenant/subscription and user identity. Check `ARM_*`/`FABRIC_*` overrides for conflicts without printing credentials. Select the subscription only after the user chooses it. Confirm region/model quota and the administrator-owned Fabric/OneLake/preview prerequisites in getting started; surface unavailable checks rather than assuming they passed.

Use an authorized **delegated user** for this bundled notebook flow. A service-principal login is not a substitute. Complete the pinned provider's [user-context app/consent setup](https://registry.terraform.io/providers/microsoft/fabric/1.14.0/docs/guides/auth_app_reg_user); normal Azure sign-in alone may be insufficient. Have the user complete device sign-in in their browser, using the approved tenant and app scope when required. Do not create an Entra app, grant tenant consent or change tenant settings automatically.

Verify Fabric access with a read-only request and check the required delegated scopes for the actual operations: workspace/Git operations, item read/write/execute, connection creation and deployment-pipeline management. Token inspection must remain in memory and report only missing scope/audience/tenant checks, never raw tokens or full claims.

Require a fine-grained GitHub PAT for this derived repository with Contents read/write. Prefer a Codespaces secret named `FABRIC_GITHUB_TOKEN`, scoped only to that repository, or an already supplied `TF_VAR_github_token`. Do not reuse `GITHUB_TOKEN`, `GH_TOKEN` or Copilot's login token as the Fabric PAT. A newly added Codespaces secret requires a Codespace restart and reconnection.

For **each** Terraform plan/apply shell, resolve the PAT inside the execution host; tool shells do not share exports:

```bash
set +x
export TF_VAR_github_token="${TF_VAR_github_token:-${FABRIC_GITHUB_TOKEN:?Configure the Fabric GitHub PAT securely on this execution host}}"
```

Do not persist this value in tfvars, shell profiles, request files or receipts. Stop for missing sign-in, permissions, quota, secrets or an unconfirmed target before provisioning.

## 3. Provision or reconcile infrastructure

Run plain Terraform from the selected checkout's `infra/`: initialize the agreed backend without upgrading providers, validate, then create a uniquely named saved platform plan with `-input=false`. For a fresh deployment, knowledge inputs remain null/empty. **Never reset them on resume.**

Summarize billable resources, public/preview exposure, permissions and any replacement/deletion. Obtain approval for that exact plan, then apply that saved plan with the PAT available again. Do not use `-auto-approve`, targeted apply, blanket replacement or an unreviewed re-plan. If code, inputs, state or target changes, create a new plan and obtain new approval.

Capture non-secret outputs: resource group, DEV workspace, Git connect request, Foundry endpoint, Search endpoint and knowledge status. Confirm deployed resources/roles/models reached their expected states. Preserve state and investigate a partial apply; do not destroy successful resources to recover.

## 4. Bootstrap DEV through native Fabric APIs

Follow [native requests](references/native-requests.md) and `cicd/README.md`:

1. Inspect Git connection and status. Connect only when disconnected, using `github_connect_request`. Refuse a different repository, branch, directory or selective connection; do not disconnect/repoint automatically.
2. Initialize only when needed. Follow `requiredAction`; stop on `CommitToGit` or an unknown action instead of overwriting the source repository.
3. Review the incoming changes and exact published SHA. Attach the incoming Bootstrap plan by its `.platform` logical ID to Update From Git with `beta=true`; include all five items. Only permit overriding incoming definitions after review/approval. Do not auto-resolve conflicts or accept deletions.
4. Capture operation receipts and poll to terminal completion, including plan actions. `202` is not success. If the workspace is already synchronized, do not use a no-op Git update as a notebook trigger; inspect prior runs/bindings and execute the notebooks separately only when needed.
5. Verify `SeedScenario` then `BindScenario` completed as the authorized user, or resume the failed/missing phase without duplicating an active job. Resolve exactly one lakehouse and ontology in DEV; verify their item types and expected logical identities. Inspect the live binding and scoped SQL readiness, not Git-normalized placeholders.

The binder legitimately changes DEV definitions. Review those generated changes with the user; do not commit/push or overwrite them automatically. Their review/commit is required before later promotion, not a reason to rerun Git sync and discard the binding.

## 5. Wire knowledge using discovered physical IDs

Use the physical IDs returned by the successful DEV item lookups, not names, `.platform` logical IDs or IDs from another workspace. For the starter's null/empty configuration, run this **local-only** helper on the execution host:

```bash
python3 .github/skills/deploy-platform/scripts/configure_knowledge.py \
  --lakehouse-id "<verified-physical-DEV-lakehouse-guid>" \
  --ontology-id "<verified-physical-DEV-ontology-guid>"
```

The helper changes only the two knowledge settings in the existing `infra/terraform.tfvars`, checks UUIDs/known logical IDs, preserves unrelated inputs and refuses a conflicting/custom mapping. It does not verify cloud identity or deploy. Use `--dry-run` first. On resume with custom/additional registrations, verify and preserve them rather than replacing the map with the sample.

Format the changed tfvars, validate, create a uniquely named saved knowledge plan, obtain approval, and apply that exact plan. Keep the PAT available for both operations. Confirm `knowledge_setup_pending` is false and the knowledge base, both source types and Foundry MCP connection exist.

## 6. Verify readiness and report honestly

Wait for successful OneLake ingestion using `knowledge_source_status_url`; inspect errors rather than treating HTTP 200 as ingestion success. Allow for RBAC propagation and the five-minute schedule. Use a bounded observation deadline (30 minutes per Fabric/ingestion phase unless the user chooses another); honor `Retry-After`. On timeout, report the still-running operation and resume point, not success or implicit cancellation.

Run the documented retrieval checks with a delegated **Search-audience user token** in `x-ms-query-source-authorization` as well as normal Search authentication. Terraform grants its deployer `Search Index Data Reader`; a different validation user needs their own explicit access. Never substitute managed identity, print the token, persist it, or silently fall back to document-only retrieval.

Require a successful ontology `activity` entry and `fabricOntology` reference matching the actual DEV workspace/ontology, with live source data. Verify document retrieval separately. "Pump B at North Works" is the expected untouched seed answer, not sufficient evidence without the correct source references; preserve intentionally changed data.

Report completed/blocked phases, the chosen host/backend, resource/workspace/Foundry links, and any remaining binding review. Claim **DEV ready** only after infrastructure, Git/bootstrap, target-local binding, knowledge registration, ingestion and both retrieval paths succeed. Distinguish a deployed but unverified system from a ready one. Do not promote to TEST/PROD or fabricate a consuming agent.

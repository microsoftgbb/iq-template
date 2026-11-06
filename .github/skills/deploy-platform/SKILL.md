---
name: deploy-platform
description: Deploy, resume or explicitly tear down the Fabric and Foundry DEV platform in a derived repository. Use for "deploy the platform", "delete the environment", or end-to-end setup through Copilot App or CLI. Use the deterministic runner and relay its target, approval, sign-in and readiness gates. Never run as a template-maintenance check.
---

# Deploy the complete DEV platform, agent and app

Read `AGENTS.md`, `docs/getting-started.md` and `docs/development.md`. Use `initialize-project` for first-use configuration and `fabric-workspace` for item/binding rules. Read [native requests](references/native-requests.md) to diagnose a blocked Fabric, retrieval, agent or app phase.

Treat "deploy the platform" as **the entire DEV setup**, from identity through the scenario agent and app, not just the identity or platform Terraform apply. The deterministic runner does the work; your job is choosing the host, relaying its gates faithfully and diagnosing stops. Keep TEST empty; exclude promotion, PROD, granting other users access, tenant-policy changes and teardown. Prepare the delegated-user registration through an approved Terraform plan; the runner then grants its consent as the user, who is assumed to administer every target. Reuse choices the user already supplied; ask only for missing or ambiguous inputs, one at a time. Never ask for a secret in chat.

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

## 2. Run the deterministic runner

Run every covered step through [`scripts/deploy.py`](scripts/deploy.py). Do not hand-drive the Terraform plans, native item import, opt-in Git sync, notebook runs, knowledge wiring, retrieval checks, image builds, toolbox and agent versions or Container App updates it owns. Each phase checks live state first and only acts on what is missing, so rerunning it resumes. Read [the runner's section in developer setup](../../../docs/development.md#deploy-deterministically) for its phases, gates and flags.

On the chosen execution host, with shell tracing off:

```bash
set +x
python3 .github/skills/deploy-platform/scripts/deploy.py up --json --non-interactive \
  --subscription "<chosen-subscription>" --local-state
```

Pass `--local-state` only if the user accepted local state in step 1; otherwise use the declared remote backend with `--backend-config <file>`. On first use without `infra/terraform.tfvars`, add `--project-name`, `--location` and `--capacity-admin`, confirmed under the `initialize-project` rules; never infer a region, project name or administrator. The runner remembers subscription, tenant, caller and backend choices, and refuses a different one; never edit its receipts to bypass that. Confirm a `location` with documented Foundry hosted agents before the first run; preflight refuses any other region before provisioning. Add `--through verify` only when the user wants to deploy and verify DEV first and publish the agent and app later.

Default to `fabric_git_enabled = false`: native Fabric import needs no PAT.
Only if the user opts into Git synchronization, require a fine-grained PAT
with Contents read/write for this repository. Supply it through a repository-scoped
`FABRIC_GITHUB_TOKEN` Codespaces secret or `TF_VAR_github_token`; restart an existing
Codespace after adding its secret. Never ask for the PAT in chat or reuse
`GITHUB_TOKEN`, `GH_TOKEN` or Copilot's login token. Keep remote execution inside
the same `gh codespace ssh` session routing shown in step 1.

Run the runner asynchronously with its stderr in a private log, and check that log every few seconds rather than waiting for the process to exit. Its first phase, `signin`, runs every missing sign-in back to back before anything is created: Azure CLI, plus any token audience that needs a fresh sign-in, including Microsoft Foundry when the run includes the agent or app. Each prints a Microsoft device-code prompt that expires in about 15 minutes. When one appears, tell the user which sign-in it is and ask them to read the code from that log themselves; never relay the code. Reports redact one-time codes. Only GitHub CLI cannot sign in headless: if it is signed out, the runner stops first with `gh auth login` for the user's own terminal. Later prompts are rare: the deployment-scope token after consent is normally acquired silently.

## 3. Handle each stop

The `--json` report gives `result`, `phase`, `message`, `next`; for approvals, `approval` with the key, description, saved plan path, sha256 and change summary; and for a refusal for lack of permission, `grants`, naming who must grant what.

| Exit | Meaning | Do |
| --- | --- | --- |
| `0` | Completed through the requested phase | Report honestly (step 5). |
| `3` | A person must act or a cloud operation is still running | Relay `message` and `next` exactly: a missing permission's `grants` (who grants what, where), sign-in commands, PAT setup, or the still-running operation. Never enter device codes, perform MFA or consent for the user, and never relay a code. Rerun when they confirm; nothing is resubmitted. |
| `4` | Approval required | For a plan, show the summary and `terraform -chdir=infra show <plan>`, and explain billable resources, public or preview exposure, permissions and any replacement or deletion. For an action, explain the listed changes (step 6). After the user explicitly approves, rerun with `--approve <key>=<sha12>` for that exact plan, or `--approve <action>`. |
| `2` | Blocked | Read the message and the receipts in `.artifacts/deploy/state.json`, then diagnose with [native requests](references/native-requests.md) and getting started. Fix the cause or ask the user, then rerun. Do not improvise mutating calls the runner owns, delete state or receipts, reset knowledge inputs or create a second working copy. |

The runner assumes the user administers every target. It grants tenant-wide consent for exactly the declared scopes and registers the serverless Search feature as that user, and lets Terraform, Fabric, Foundry and Azure Resource Manager attempt every change. Only when an API refuses for lack of permission does it stop with exit 3 and `grants`; relay those instead of listing every possible prerequisite up front.

- `--yes` approves every non-destructive plan and action in that run. Use it only when the user asks for unattended approval of the run.
- `--allow-replace` permits an approved plan that deletes or replaces resources. Use it only after the user reviews those exact changes; replacements after a partial apply need a fresh full plan, not a retried old one.
- `--manual-grants` stops the runner from granting tenant-wide consent or registering subscription preview features itself; it prints them for an administrator instead. Use it only when the user says they are not an administrator or their organization requires an administrator to make those changes.
- `--consent-confirmed` proceeds when the user's sign-in cannot read consent grants, and so could not grant them either. Use it only after an administrator has confirmed the grant.
- `--bootstrap-fallback notebooks` imports or Git-syncs without the plan and runs `SeedScenario` then `BindScenario` as the user. Use it only after surfacing the exact `FeatureNotAvailable` error and the user chooses that path. Never change a tenant setting instead.
- `--retry-failed` continues after a failed or receipt-less Git operation or notebook run, creates a new hosted agent version in place of a failed one, and resubmits an unconfirmed Foundry purge. Use it only after that operation has been inspected and its cause fixed; the runner never resubmits an accepted request otherwise.

## 4. Keep what the runner cannot verify

- **Prerequisites and permissions:** the runner checks tools, every sign-in, provider overrides, hosted-agent support in `location`, PAT read access only for opt-in Git, the shared Power BI Service principal, Fabric CU quota, model quota and whether the Search region offers the chosen tier, and registers the serverless tier's subscription feature. It assumes administrator access instead of pre-checking Fabric tenant settings, Entra roles or Azure RBAC; a refusal stops with the exact grant (exit 3, `grants`). Dedicated Search new-service capacity stays unverified. If the Microsoft service principal is missing, stop for administrator assistance. Do not self-grant directory privileges, widen scopes or roles, create a replacement Microsoft application or change tenant settings to get past a refusal. A service-principal login is not a substitute for the delegated user.
- **Web IQ:** deploy its global G3 account in the same state. Require explicit `web_iq_terms_accepted` before Terraform registers `Microsoft.WebIQ` and creates the account; do not add an opt-out. Follow [preparation.md](../../../preparation.md#web-iq): the user links the resource/AppID in the portal and supplies its key only via `WEB_IQ_API_KEY`. `verify` requires the account and one cited, fixed public-only search; missing evidence blocks readiness and `--yes` cannot complete portal onboarding. Do not call account creation or a successful search agent/app integration.
- **Serverless Search:** Terraform creates it without a managed identity because the preview fails otherwise; the runner enables the system identity and verifies it, since the preview answers that update with HTTP 500. Its grants arrive with the knowledge plan. Changing serverless settings, tags, tier or region replaces it; after knowledge setup the runner applies that as two reviewed plans, first with the identity grants deferred (`search_identity_ready=false`, which also needs `--allow-replace`), then the grants. Review both like any other plan.
- **Regions and quota:** never automatically relocate resources, raise quota, switch subscription or SKU, or retry after a partial apply. Ask before setting `fabric_location` or `search_location`, review cross-region data handling, latency and cost, and approve a fresh full plan.
- **Identity:** the targeted identity plan is the only targeting exception; the runner refuses one that touches anything else, and blocks later plans that change the registration. The registration, `required_resource_access`, pre-authorized clients and outputs are not a consent receipt. Permission changes need administrator re-consent and a fresh sign-in. Keep the same Terraform caller.
- **Bootstrap:** by default, import tracked `workspace/` definitions through Bulk Import with the `Bootstrap` plan and no name-based pairing. Require matching logical/physical identities, complete per-item success and recorded operation receipts; never overwrite existing definitions. Opt-in Git sync still refuses other targets, conflicts and `CommitToGit`. Both paths require the reviewed, published `HEAD`. Review generated DEV bindings before promotion; commit through Fabric source control only when Git is enabled. Do not commit, push or overwrite them automatically.
- **Readiness:** the `verify` phase waits for ingestion and requires `fabricOntology` activity and references matching the DEV workspace and ontology, with live source data, plus a separate `indexedOneLake` reference. It uses the user's own Search token, never a managed identity, and never accepts a document-only answer as live ontology evidence. Use `--ontology-question` and `--expect` when the user intentionally changed the seed data.

## 5. Report honestly

Report completed and blocked phases, including registration, consent and sign-in, the chosen host and backend, resource, workspace and Foundry links, and any remaining binding review. Claim **DEV ready** only after the `verify` phase succeeded. Distinguish a prepared registration, a deployed but unverified system and a ready one. Do not promote to TEST or PROD.

## 6. Agent and app

The `agent` and `app` phases follow getting started step 7 on the same host and checkout. Read `docs/runtime-integration.md` for the route's identity, access and evidence. Do not substitute another agent runtime or hosting service, a shared or application credential, or a backend.

1. Both phases build only committed source in `agents/scenario-assistant/` and `app/`, from a `HEAD` equal to the published `origin/<github_branch_name>`. If the runner refuses, ask the user to commit and push; never build from a dirty or unpublished tree. For an environment new to this project, run the `runtime-integration` preflight first; `candidate` permits continuing, not readiness.
2. `agent-deploy` covers building the `scenario-assistant` image in the deployment's container registry (skipped when its tag exists), publishing a toolbox version and making it the default, and creating a hosted agent version. State the project, toolbox and agent names before approving. The runner records the version before polling, so a rerun observes it instead of creating another. A `failed` version stops with its error; after the cause is fixed, `--retry-failed` creates a new one. The runner then proves, as the user, a fresh challenge, the expected facts in the `scenario_ontology` tool's output and `starter.txt` in the `knowledge_base` tool's output. Report a miss verbatim; never weaken the instructions, tools or expected facts to pass.
3. `app-deploy` covers tenant-wide consent for the web registration's delegated Foundry scope (granted as the user; with `--manual-grants`, relay the administrator link), building the `web` image and creating or updating the Container App through Azure Resource Manager. The runner waits for the latest revision, then checks `/healthz`, `/config.json` and a relayed fresh challenge as the user.
4. The user then opens the reported app URL, signs in, selects **Check connection**, and asks the seed question and a follow-up. Record elapsed times. A timeout or a 5xx is a blocker to report, not a reason to add retries, warm-up jobs or a backend.

Report **agent ready** from the `agent` phase and **app verified for the deploying user** from the `app` phase and the user's browser check. Granting other users access (Foundry Agent Consumer on the project or agent, and Fabric access) is a separate operator request; never widen roles, connection sharing or the app's identity instead. Claim a ready shell only with all evidence in `docs/runtime-integration.md`; list second-user isolation as unverified until it is proven.

## 7. Explicit teardown

Only for a separate user request to delete the environment, use `deploy.py down`
in the original derived checkout/backend, with its receipts and tfvars. Follow
[teardown](../../../docs/development.md#delete-the-environment). Never run it
while maintaining the template.

Start without approvals to obtain the full destroy plan; like `up`, it signs in
to every CLI teardown needs first. Explain that both
workspaces and all their contents will be deleted. Require `--allow-delete`,
the reviewed `--approve destroy=<hash>`, and separate `runtime-delete` and
`purge-foundry` approvals. `--yes` does not grant them. The runner deletes the
Container App, hosted agent and toolbox before Terraform and purges Foundry
afterward; it preserves the login registration until Fabric teardown finishes.

Relay failures and manual cleanup: retained Fabric workspaces/data and Web IQ
portal credentials. Do not claim immediate data erasure or delete
shared resources, state, local configuration or the repository.

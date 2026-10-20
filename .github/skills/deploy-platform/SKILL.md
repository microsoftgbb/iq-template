---
name: deploy-platform
description: Deploy or resume the complete Fabric and Foundry DEV platform from a derived repository. Use for "deploy the platform", "deploy this project", or end-to-end setup through Copilot App or CLI. Run the deterministic deployment runner on the chosen host and relay its gates: plan approvals by hash, administrator consent, sign-ins, connector OAuth and blocked phases, through DEV readiness and the shipped Copilot Studio agent and Managed Runtime app. Never run as a template-maintenance check.
---

# Deploy the complete DEV platform, agent and app

Read `AGENTS.md`, `docs/getting-started.md` and `docs/development.md`. Use `initialize-project` for first-use configuration and `fabric-workspace` for item/binding rules. Read [native requests](references/native-requests.md) to diagnose a blocked Fabric or retrieval phase.

Treat "deploy the platform" as **the entire DEV setup**, from identity through the scenario agent and app, not just the identity or platform Terraform apply. The deterministic runner does the work; your job is choosing the host, relaying its gates faithfully and diagnosing stops. Keep TEST empty; exclude promotion, PROD, hosted app deployment and sharing, tenant-policy changes and teardown. Prepare the delegated-user registration through an approved Terraform plan, but leave consent to an administrator. Reuse choices the user already supplied; ask only for missing or ambiguous inputs, one at a time. Never ask for a secret in chat.

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

Run every covered step through [`scripts/deploy.py`](scripts/deploy.py). Do not hand-drive the Terraform plans, Fabric Git sync, notebook runs, knowledge wiring, retrieval checks, PAC or `ms` steps it owns. Each phase checks live state first and only acts on what is missing, so rerunning it resumes. Read [the runner's section in developer setup](../../../docs/development.md#deploy-deterministically) for its phases, gates and flags.

On the chosen execution host, with shell tracing off:

```bash
set +x
python3 .github/skills/deploy-platform/scripts/deploy.py up --json --non-interactive \
  --subscription "<chosen-subscription>" --local-state \
  --environment-id "<power-platform-environment-id>" \
  --environment-url "https://<org>.crm.dynamics.com"
```

Pass `--local-state` only if the user accepted local state in step 1; otherwise use the declared remote backend with `--backend-config <file>`. On first use without `infra/terraform.tfvars`, add `--project-name`, `--location` and `--capacity-admin`, confirmed under the `initialize-project` rules; never infer a region, project name or administrator. The runner remembers subscription, tenant, caller, backend and environment choices, and refuses a different one; never edit its receipts to bypass that. Omit the environment flags until the user has confirmed the Power Platform environment and Org URL; the runner then waits at the `agent` phase without creating anything there.

Require a fine-grained GitHub PAT for this derived repository with Contents read/write. Prefer a Codespaces secret named `FABRIC_GITHUB_TOKEN`, scoped only to that repository, or an already supplied `TF_VAR_github_token`; a newly added Codespaces secret requires a Codespace restart and reconnection. The runner reads it only from the execution host's environment, maps it into each plan and apply, and never writes it anywhere. Never ask for it in chat, and do not reuse `GITHUB_TOKEN`, `GH_TOKEN` or Copilot's login token. For App-driven remote work, run the runner inside the same `gh codespace ssh` heredoc shown in step 1.

Run the runner asynchronously with its stderr in a private log, and check that log every few seconds rather than waiting for the process to exit. Native CLIs such as `ms app init` can print a GitHub or Microsoft device-code prompt that expires in about 15 minutes. When one appears, ask the user to read the code from that log themselves and complete the sign-in; never relay the code. Reports redact one-time codes.

## 3. Handle each stop

The `--json` report gives `result`, `phase`, `message`, `next` and, for approvals, `approval` with the key, description, saved plan path, sha256 and change summary.

| Exit | Meaning | Do |
| --- | --- | --- |
| `0` | Completed through the requested phase | Report honestly (step 5). |
| `3` | A person must act or a cloud operation is still running | Relay `message` and `next` exactly: the administrator approval link, device-code sign-in commands, PAT setup, **Fix connection** link, or the still-running operation. Never perform consent, MFA or connector OAuth for the user, and never relay a consent code. Rerun when they confirm; nothing is resubmitted. |
| `4` | Approval required | For a plan, show the summary and `terraform -chdir=infra show <plan>`, and explain billable resources, public or preview exposure, permissions and any replacement or deletion. For an action, explain what it creates. After the user explicitly approves, rerun with `--approve <key>=<sha12>` for that exact plan, or `--approve <action>`. |
| `2` | Blocked | Read the message and the receipts in `.artifacts/deploy/state.json`, then diagnose with [native requests](references/native-requests.md) and getting started. Fix the cause or ask the user, then rerun. Do not improvise mutating calls the runner owns, delete state or receipts, reset knowledge inputs or create a second working copy. |
- `--yes` approves every non-destructive plan and action in that run. Use it only when the user asks for unattended approval of the run.
- `--allow-replace` permits an approved plan that deletes or replaces resources. Use it only after the user reviews those exact changes; replacements after a partial apply need a fresh full plan, not a retried old one.
- `--grant-admin-consent` grants tenant-wide consent for exactly the declared delegated scopes. Use it only when the user states that they are a tenant administrator and want to grant it. Otherwise hand the administrator the printed link and wait.
- `--consent-confirmed` proceeds when the user's sign-in cannot read consent grants. Use it only after the administrator has confirmed the grant.
- `--register-search-serverless` registers the subscription's auto-approved `Microsoft.Search/Serverless` preview feature and re-registers `Microsoft.Search`. Use it only when the user accepts the serverless Search preview and that subscription change; otherwise relay the printed commands or let them choose `search_sku = "basic"`.
- `--bootstrap-fallback notebooks` syncs without the plan and runs `SeedScenario` then `BindScenario` as the user. Use it only after surfacing the exact `FeatureNotAvailable` error and the user chooses that path. Never change a tenant setting instead.
- `--retry-failed` continues after a failed or receipt-less Git operation, notebook run, publish or app registration. Use it only after that operation has been inspected and its cause fixed; the runner never resubmits an accepted request otherwise.
- `--with-m365-demo` adds the optional `m365-demo` phase (section 7). Use it only after the user said yes to Microsoft 365 demo content; plan approvals and `--yes` never imply that choice.
- `--grant-m365-demo-consent` grants tenant-wide consent for exactly the demo app's declared delegated scopes, which include Conditional Access and per-user MFA administration. Use it only when the user states that they are a tenant administrator and want to grant it; `--grant-admin-consent` never covers these scopes.

## 4. Keep what the runner cannot verify

- **Prerequisites:** the runner checks tools, the delegated Azure user, provider overrides, PAT read access, the shared Power BI Service principal, Fabric CU quota, model quota, whether the Search region offers the chosen tier and, for serverless, its subscription feature. It does not verify administrator-owned Fabric tenant settings, dedicated Search new-service capacity or Entra directory permissions; surface them from getting started. If directory permissions or the Microsoft service principal are missing, stop for administrator assistance. Do not self-grant directory privileges, create a replacement Microsoft application or change tenant settings. A service-principal login is not a substitute for the delegated user.
- **Optional Web IQ:** enable only with `web_iq_enabled` and explicit `web_iq_terms_accepted` in the same tfvars/state. Terraform conditionally registers `Microsoft.WebIQ` and creates its global G3 account. Follow [preparation.md](../../../../preparation.md): the user links the resource/AppID in the portal and supplies its key only via `WEB_IQ_API_KEY`. `verify` requires one cited, fixed public-only search; `--yes` cannot complete portal onboarding. Do not call account creation or a successful search native Studio/app integration. The default core has no Web IQ dependency.
- **Serverless Search:** Terraform creates it without a managed identity because the preview fails otherwise; the runner enables the system identity and verifies it, since the preview answers that update with HTTP 500. Its grants arrive with the knowledge plan. Changing serverless settings, tags, tier or region replaces it; after knowledge setup the runner applies that as two reviewed plans, first with the identity grants deferred (`search_identity_ready=false`, which also needs `--allow-replace`), then the grants. Review both like any other plan.
- **Regions and quota:** never automatically relocate resources, raise quota, switch subscription or SKU, or retry after a partial apply. Ask before setting `fabric_location` or `search_location`, review cross-region data handling, latency and cost, and approve a fresh full plan.
- **Identity:** the targeted identity plan is the only targeting exception; the runner refuses one that touches anything else, and blocks later plans that change the registration. The registration, `required_resource_access`, pre-authorized clients and outputs are not a consent receipt. Permission changes need administrator re-consent and a fresh sign-in. Keep the same Terraform caller.
- **Bootstrap:** the runner syncs only additions of items defined in `workspace/`, from the pushed `HEAD` of the configured branch, and refuses other Git targets, conflicts and `CommitToGit`. The binder legitimately changes DEV definitions: review its generated changes with the user and commit them through Fabric source control before any promotion. Do not commit, push or overwrite them automatically.
- **Readiness:** the `verify` phase waits for ingestion and requires `fabricOntology` activity and references matching the DEV workspace and ontology, with live source data, plus a separate `indexedOneLake` reference. It uses the user's own Search token, never a managed identity, and never accepts a document-only answer as live ontology evidence. Use `--ontology-question` and `--expect` when the user intentionally changed the seed data.

## 5. Report honestly

Report completed and blocked phases, including registration, consent and sign-in, the chosen host and backend, resource, workspace and Foundry links, and any remaining binding review. Claim **DEV ready** only after the `verify` phase succeeded. Distinguish a prepared registration, a deployed but unverified system and a ready one. Do not promote to TEST or PROD.

## 6. Agent and app

The `agent` and `app` phases follow getting started step 7 on the same host and checkout. Read `docs/runtime-integration.md` for the Studio route's constraints. Do not substitute a Foundry agent, a GitHub Copilot harness agent, a custom connector or a backend; those routes are listed under "Routes to re-validate".

1. Confirm the tenant, non-Default Power Platform environment GUID and Org URL with the user; the agent and app share that environment. Use `dotnet pac env list` to choose an existing environment rather than creating one implicitly. For an environment new to this project, run the `runtime-integration` preflight first; `candidate` permits continuing, not readiness. The user completes `dotnet pac auth create` and `ms auth login` personally; the runner prints the device-code commands.
2. Agent creation (`agent-create`) runs `pac copilot init`, which creates **and publishes** the agent; state the environment and schema name before approving. On resume, the runner looks up the schema name and clones instead of creating twice. It renders `agents/scenario-assistant/` into ignored `.artifacts/studio/`; never edit or commit the rendered workspace or `.mcs/`.
3. Connection creation (`agent-connections`) is followed by each owner's personal **Fix connection**. Publishing (`agent-publish`) applies **Agent readers** access, end-user (`Invoker`) tools and grounding off, then the runner rechecks until `ready`. Report blockers verbatim. Never switch a tool to `Maker` mode, widen access or re-enable grounding to pass a check.
4. App registration (`app-register`) sets `STUDIO_AGENT_SCHEMA`, runs `ms app init` with the documented build settings and adds the Studio data source. If the Microsoft Managed Apps GitHub App cannot access the repository, relay the administrator installation/configuration link and inspect any partial registration before retrying. If the GitHub organization is ineligible for repository binding, stop for the user's explicit repository choice; never register twice.
5. Before playback, each user authorizes their app-facing **Microsoft Copilot Studio** connection in the selected environment's Power Apps **Connections** page (or reuses their matching **Connected** connection). The user then runs `ms app dev`, selects **Check connection**, follows the separate Studio **user-connections** link for their own Fabric IQ/Foundry IQ consent when prompted, checks again for the fresh challenge and asks the seed question. See [runtime integration](../../../docs/runtime-integration.md#local-player-boundary) for the observed loopback OAuth failure; never publish private Codespaces ports to bypass it. Record elapsed times. A 120-second `TimedOut` is a blocker to report, not a reason to add retries, warm-up jobs or a backend.

Review `app/src/config.ts` and `ms.config.json` with the user before committing. Hosted preview, deployment and sharing are separate explicit requests. Report **agent ready** from the runner's `agent` phase and **app verified for one user** from the App Player results. Claim a ready shell only with all evidence in `docs/runtime-integration.md`; list second-user isolation as unverified until it is proven.

## 7. Optional: Microsoft 365 demo content

When a session reached **DEV ready**, offer Microsoft 365 demo content once, unless the user already declined it or nobody is present to answer. It seeds fictional mail, Teams chats and a channel, meetings and Word, PowerPoint and Excel files into the deployment tenant so Work IQ and Microsoft 365 Copilot have work data to demo. Before asking, explain that it is optional and needs a non-production Microsoft 365 demo tenant with two to eight licensed demo users, an administrator to consent once, and that administrator's sign-in for each seed or cleanup. Each seed or cleanup temporarily relaxes Security Defaults, MFA and Conditional Access for the demo users and restores exactly what it changed. Then ask with `ask_user` (`Yes - set up demo content` / `Not now`). Plan approvals and `--yes` never imply this choice.

On an explicit yes, rerun the runner with `--with-m365-demo`, adding `--through m365-demo` when the agent and app are not part of this run. Its `m365-demo` phase registers a delegated-only app through Terraform (no application permissions, no credentials), relays the saved plan for approval like any other, then administrator consent (`--grant-m365-demo-consent` as in step 3). When the phase is done, load [`m365-demo-seed`](../m365-demo-seed/SKILL.md) on the same host and checkout for the content work; it reads the runner's `m365_demo` receipt from `deploy.py status --json`. Keep the demo content's outcome out of the DEV, agent and app readiness you report.

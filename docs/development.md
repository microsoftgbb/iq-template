# Developer setup and Copilot

## Codespaces or a local dev container

In your **derived repository**, use **Code > Codespaces > Create codespace** on the intended branch. For a local checkout with Docker and VS Code's Dev Containers extension, run **Dev Containers: Reopen in Container**.

The [devcontainer](../.devcontainer/devcontainer.json) includes Azure CLI, Terraform, the standalone GitHub Copilot CLI (`copilot`, not the retired `gh copilot` extension), Git/GitHub CLI, Node.js/npm, Python 3 with venv support, Bash, jq, ripgrep and an SSH server for authenticated Codespaces access. It also includes the .NET 10 SDK for the repository-pinned Power Platform CLI (`dotnet pac`, from [`.config/dotnet-tools.json`](../.config/dotnet-tools.json)), the Copilot Managed Runtime CLI (`ms`), its official `microsoft-managed-apps` Copilot plugin, Git Credential Manager (GCM), native Node build tools, `libsecret` development libraries and `xdg-open`. It configures the Terraform, Azure CLI, Python, YAML and Copilot editor extensions.

Tools are installed during image build. The base image, Terraform and Copilot are pinned; Node uses the 24 release line and must be at least 24.11.0. Copilot comes from its official GitHub release with mandatory SHA-256 verification, without an npm-registry dependency. The local [Managed Runtime feature](../.devcontainer/features/managed-runtime/install.sh) runs after the Node feature: it pins the `@microsoft/managed-apps-cli` npm package, explicitly allows its three native-dependency install scripts, verifies the official GCM release's SHA-256, and installs the plugin for the **`vscode` user**, not root. The marketplace is a local checkout of a pinned Microsoft commit; change the feature's pins and rebuild to update it, rather than tracking the marketplace's moving branch.

The [IQ plugin feature](../.devcontainer/features/iq-plugins/install.sh) also
installs `azure`, `foundry-iq-skills`, `workiq`,
`microsoft-365-agents-toolkit` and `fabric-skills` from reviewed official
marketplace commits. It supplies a pinned Azure MCP binary, retains the
packages' hosted MCP declarations, and uses the image's Python 3.12 for Foundry
IQ helpers. See the [package/MCP map and authorization boundaries](copilot-tooling.md).
These are coding-assistant capabilities, not automatically enabled app or agent
integrations.

The post-create command restores the pinned Power Platform CLI with `dotnet tool restore`, then only checks tool availability, minimum Node/Git/Python versions, native authentication bindings and all six plugin registrations. It does **not** install or update packages, start MCP servers, invoke skills, sign in, accept terms, generate tfvars, initialize Terraform, provision resources, synchronize Fabric, query Microsoft 365, or run notebooks. Python supports the local initializer, plugin hooks and static source checks; Node supports app builds and Node-based developer/MCP tools. Spark, Delta and NotebookUtils still run in Fabric, not locally. No Fabric CLI, fabric-cicd or azd is needed for the template's native deployment flow; the .NET SDK only runs the pinned Power Platform CLI. On a host without the devcontainer, install the .NET 10 SDK and run `dotnet tool restore` at the repository root. Use optional product-workflow dependency checks only when that workflow is explicitly requested.

Authenticate separately when needed:

```sh
# Only if GitHub CLI is not already authenticated:
gh auth login

# Start from the repository root; use /login in Copilot if prompted:
copilot

# Initial user sign-in for the Entra-only bootstrap (device code works in Codespaces):
az login --use-device-code --tenant "<tenant-id>"
az account set --subscription "<subscription-name-or-id>"
```

Follow [identity bootstrap and administrator consent](getting-started.md#3-bootstrap-the-fabric-user-auth-application) before the first Fabric plan. Terraform prepares the app and permissions in the same state as the platform; only that initial plan targets `module.fabric_user_auth`. After admin consent, or when resuming with an already-consented registration, sign in with its generated scope:

```sh
az login --use-device-code \
  --tenant "$(terraform -chdir=infra output -raw fabric_user_auth_tenant_id)" \
  --scope "$(terraform -chdir=infra output -raw fabric_user_auth_scope)"
az account set --subscription "<subscription-name-or-id>"
```

Copilot needs an active subscription and organization permission. A Codespaces-provided GitHub token may be sufficient for repository access but not Copilot requests; follow [Copilot authentication](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli) rather than reusing the Fabric Git PAT. Never put tokens in devcontainer files or paste them into chat.

The Copilot App uses its selected execution host; a devcontainer file does not automatically move App sessions into that container. The deployment skill can instead run commands in your chosen Codespace through `gh codespace ssh`. The App host needs GitHub CLI with access to that Codespace; all Azure tools, credentials, tfvars and state stay in the remote deployment checkout. For an explicitly local App deployment, install the equivalent tools on that host and reuse its original deployment checkout/backend, not a fresh worktree.

## Build and deploy Copilot Managed Runtime apps

The template ships the app in [`app/`](../app/); the repository root is its
native app root. [Getting started step 7](getting-started.md#7-create-the-agent-and-app)
registers and runs it against the shipped Studio agent. For an environment new
to the project, run the [runtime integration preflight](runtime-integration.md)
first. A signed-in account or an allowed connector is not enough; inspect the
pinned CLI's help and keep authentication-cache probes serialized.

The container supplies the prerequisites in Microsoft's [developer guide](https://learn.microsoft.com/en-us/microsoft-365/managed-apps/developer/?view=o365-worldwide) and [GitHub Copilot quickstart](https://learn.microsoft.com/en-us/microsoft-365/managed-apps/developer/quickstart-github-copilot?view=o365-worldwide): Node.js >= 24.11.0, Git >= 2.27.0, GCM, the global `ms` CLI and the Copilot plugin. Managed Runtime is **preview**. You still need a work/school account in an eligible tenant and permission to create apps. Running apps, including local development, requires Power Apps Premium or sufficient Managed Application Copilot Credits; a Copilot subscription alone is not that license.

The plugin is already installed in the container; do not repeat the quickstart's marketplace installation there. Copilot App/CLI on another host needs its own plugin installation; rebuilding the container does not modify that host. Do not scaffold another app with `/microsoft-managed-apps:create-app` or `ms app create`: the shipped `app/` is the shell.

Complete the **separate** Managed Runtime sign-in in your own terminal: use `ms auth login --device-code` in headless Codespaces/SSH sessions, then `ms auth status`. Browser-based `ms auth login` is also available. Azure CLI, Copilot and Fabric Git authentication do not sign you into `ms`. GCM uses Git's ephemeral in-memory cache in this headless container (900 seconds by default), not a plaintext Git credential store or a desktop keyring. Protect all CLI authentication caches; never commit them or put credentials in the image.

The app's React, TypeScript, Vite, `@microsoft/managed-apps` and `@microsoft/managed-apps-vite-plugin` dependencies are locked in the root `package-lock.json` for the `app` npm workspace, not installed as global tools. Use `ms`, **not `npx ms`**, which can resolve an unrelated npm package.

From the repository root, `npm run lint`, `npm run build` and `npm test` check the app, and `ms app dev` starts the local development loop. The container forwards the default app/config ports **3000 and 8080**; keep Codespaces ports private. When the browser is on another machine, use private local port forwarding for these ports. For example, `gh codespace ports forward 3000:3000 8080:8080 --codespace "<name>"` runs on the browser's machine. Device-code sign-in avoids forwarding an authentication callback port.

Before playback, authorize your app-facing **Microsoft Copilot Studio** connection in the selected environment's Power Apps **Connections** page. This is separate from the agent's Fabric IQ/Foundry IQ consent. The App Player's **Check connection** flow can redirect to a loopback OAuth port even in hosted previews; use the [per-user connection steps](runtime-integration.md#local-player-boundary), not public Codespaces ports.

Review and push app commits before opening a hosted preview with `ms app play --mode preview`. Deployment is a separate, explicit action: use `/microsoft-managed-apps:deploy` or `ms app deploy` after approving the preview. The guided deployment registers the app and runs it locally; it does not share or deploy it. Binding the app to this GitHub repository requires an organization on GitHub Enterprise Cloud and the Microsoft Managed Apps GitHub App installed for that repository. A platform-managed or build-only repository is an explicit alternative, not an automatic fallback.

## Deploy deterministically

One command runs the complete DEV flow, agent and app without an AI agent. It is the same runner Copilot uses for "Deploy the platform", so both paths create the same resources and require the same evidence. Run it in the derived repository's Codespace or devcontainer:

```sh
python3 .github/skills/deploy-platform/scripts/deploy.py up \
  --subscription "<subscription-id-or-name>" --local-state \
  --environment-id "<power-platform-environment-id>" \
  --environment-url "https://<org>.crm.dynamics.com"
```

Without `infra/terraform.tfvars`, also pass `--project-name`, `--location` and `--capacity-admin`; the runner calls the [initializer](#what-gets-templated). Use `--local-state` only for a single-user demo whose state you back up. For anything shared, declare a secured remote backend in `infra/` and pass `--backend-config <file>`. Target choices are remembered in ignored `.artifacts/deploy/state.json`, so later runs need only `up`; a different subscription, tenant or caller is refused.

| Phase | What it does | Stops for |
| --- | --- | --- |
| `config`, `preflight` | Repository, branch and tfvars checks; Azure user and subscription; provider overrides; Fabric CU and model quota; Search tier/feature; PAT access only for opt-in Git | Missing inputs, sign-in, quota or feature; PAT only for opt-in Git |
| `identity` | Saved plan targeted only at `module.fabric_user_auth`, then apply | Plan approval |
| `consent` | Reads the tenant-wide grant, then checks the scoped sign-in and Fabric reads | Administrator consent; device-code sign-in |
| `platform` | Saved untargeted plan, then apply; enables the serverless Search identity that Terraform cannot set and verifies it, because the preview answers HTTP 500 | Plan approval |
| `bootstrap` | Imports your published `HEAD` through native Fabric APIs with `Bootstrap`, then verifies target-local binding; Git connect/sync only when enabled | Conflicting identities, changed existing definitions, an unavailable plan attachment |
| `knowledge` | Writes the discovered physical GUIDs with the knowledge helper; saved plan, then apply. A plan that creates or replaces serverless Search is applied as two plans: grants deferred, then granted after the identity exists | Plan approval |
| `verify` | Waits for ingestion; proves live ontology and document retrieval with your own token and an authenticated public Web IQ search | Running ingestion; portal onboarding, credentials or missing evidence |
| `agent` | Creates or clones, renders, pushes, connects, configures, publishes and checks the Studio agent | Creation, connection and publish approvals; your **Fix connection** |
| `app` | Sets the agent schema, runs `npm ci`, `ms app init` and adds the Studio data source; syncs `package-lock.json` (lockfile only) with the CLI pin that `ms app init` adds to `package.json` | Registration approval; `ms` sign-in |

Every deployment includes Web IQ. `preflight` requires explicit Web IQ terms
acceptance and the pinned accounts API in the selected subscription. The normal
platform/knowledge plan creates its global G3 account. In `verify`, the runner
checks provisioning and makes one fixed public-only Web IQ search using
`WEB_IQ_API_KEY` from the environment. Missing portal onboarding/credentials waits;
API errors or missing citations block. Counts and optional freshness metadata
are recorded separately from agent/app readiness; no content or keys are saved.
See the [manual checklist](../preparation.md#web-iq) for required actions
and [runtime details](runtime-integration.md#web-iq) for the still-separate
Studio integration. Missing Web IQ outputs or credentials cannot be skipped.

Fabric GitHub synchronization defaults to **off** (`fabric_git_enabled = false`).
Native import uses the Azure user's Fabric token, preserves `.platform` identities
and needs no PAT. It imports only missing items, verifies per-item results and
resumes accepted operations without overwriting live data or bindings. Set the
flag to `true` and supply a PAT only when Git synchronization is wanted.

Every phase checks live state before acting, so rerunning `up` resumes. It observes an accepted Fabric operation or an active notebook run instead of submitting another, and creates a fresh plan after a failed apply. Exit code `3` means a person must act; the output names the exact link or command. Exit code `4` means approval is required. Approve interactively, or rerun with `--approve <phase>=<sha256-prefix>` for the exact saved plan it printed (`terraform -chdir=infra show <plan>` shows it), or with `--approve agent-create`, `agent-connections`, `agent-publish` or `app-register`. `--yes` approves everything in that run except deletions and replacements, which also need `--allow-replace`. `up --through <phase>` stops after a phase; every run re-verifies the earlier phases, so no gate can be skipped. `status` summarizes receipts without cloud calls, and `--json` prints a machine-readable report.

These choices are explicit flags, never automatic:

- `--grant-admin-consent`: you are a tenant administrator and grant consent yourself for exactly the declared delegated scopes. Otherwise the runner waits and prints the administrator's approval link.
- `--consent-confirmed`: an administrator confirmed consent, but your sign-in cannot read the tenant's consent grants to verify it.
- `--register-search-serverless`: register the subscription's auto-approved `Microsoft.Search/Serverless` preview feature that the default Search tier needs, then re-register `Microsoft.Search`. Otherwise the runner prints both commands and waits, or you set `search_sku = "basic"`.
- `--bootstrap-fallback notebooks`: when the tenant rejects plan attachment (`FeatureNotAvailable`), import or Git-sync the same reviewed definitions without it, then run `SeedScenario` and `BindScenario` as you.
- `--retry-failed`: after you inspected and fixed a failed or unconfirmed operation, continue from live state; without it the runner never resubmits an accepted request.

The runner refuses the reusable template, a different Git target, conflicts, non-additive incoming changes, any commit other than your pushed `HEAD`, ambient Terraform overrides (`TF_CLI_ARGS*`, `TF_WORKSPACE`, extra `TF_VAR_*`) and a Dataverse URL that belongs to another environment. It never prints or stores tokens. It still needs people for consent, MFA sign-ins, connector OAuth and the App Player check, so it is not unattended CI: the bundled binder and IQ connections need a delegated user. `up` does not promote, host, share or delete resources.

Choose an existing non-Default Power Platform environment with a GUID ID (`dotnet pac env list`) and its matching Org URL. The runner diagnoses Dataverse's `Default-<guid>` environment IDs explicitly rather than treating them as malformed GUIDs. If `ms app init` reports that the Microsoft GitHub App cannot access the bound repository, ask the repository/organization administrator to [install or configure Microsoft Managed Apps](https://github.com/marketplace/microsoft-managed-apps) for that repository. Inspect `ms app list` / `ms app info` before retrying; recover a partial registration instead of registering a duplicate.

## Delete the environment

Use the original checkout, tfvars, backend and `.artifacts/deploy/state.json`.
Sign in as the original Azure user and select the recorded subscription. Keep
Fabric consent active until Terraform finishes. App deletion needs its owner;
agent deletion needs permission in the recorded Power Platform environment.

```sh
python3 .github/skills/deploy-platform/scripts/deploy.py down --non-interactive
# Inspect the saved plan path printed above:
terraform -chdir=infra show "<plan-path>"
python3 .github/skills/deploy-platform/scripts/deploy.py down --non-interactive \
  --allow-delete --approve "destroy=<sha256-prefix>" \
  --approve runtime-delete --approve purge-foundry
```

| Order | Deletes |
| --- | --- |
| 1 | Managed Runtime app, including shares; Copilot Studio agent. |
| 2 | Full Terraform state: knowledge base/sources, Search ingestion assets, DEV/TEST workspaces and all data, pipeline, connections, capacity, Foundry, Web IQ, role assignments and deployment identity. |
| 3 | Soft-deleted Foundry account, using its recorded subscription, resource group, name and location. |

`down` checks the repository, caller and targets without deployment quota or
readiness probes. No targeted destroy, tenant-policy changes or local-file
deletion. `--yes` cannot approve deletion. `destroy=<hash>`, `runtime-delete` and
`purge-foundry` are separate approvals. Rerun after sign-in, approval or an
unfinished operation; use `--retry-failed` only after inspecting an unconfirmed
agent deletion or purge. A failed Terraform apply requires a fresh plan/hash.
The runner preserves a nonsecret teardown inventory across partial deletion.
If the app registration was interrupted, recover `ms.config.json` first.

Manual cleanup:

- Permanently delete the reported DEV/TEST workspace IDs in **OneLake catalog > Govern > Workspaces** as a Fabric tenant admin. [Workspace retention](https://learn.microsoft.com/en-us/fabric/admin/workspace-retention) delays data erasure; [OneLake retains data for seven days after permanent item deletion](https://learn.microsoft.com/en-us/fabric/admin/item-recovery#permanently-delete-a-recoverable-item).
- Remove project-only Power Apps connections/references and retained agent transcripts/solution components. Connections may be reused by other apps or users, so the runner does not delete them.
- Revoke Web IQ keys and remove the portal AppID/profile.
- Retain state and receipts until cleanup is confirmed. GitHub, backend storage, local files, tenant settings, licenses, external sources and service-retained backups/audit records remain.

## Deploy from one prompt

After these changes are included in the template's published branch:

1. Create a new repository with **Use this template**, and open its Codespace.
2. Complete the one-time prerequisites below. Start `copilot` in the Codespace terminal, or open the derived repository in Copilot App with GitHub CLI access to the Codespace.
3. Say **"Deploy the platform"**. In App, include **"Use my Codespace `<name>`"**; otherwise Copilot asks which one. Interactive mode is useful for initial sign-in and approvals.

Copilot loads [`deploy-platform`](../.github/skills/deploy-platform/SKILL.md), runs the [deterministic runner](#deploy-deterministically) and relays its gates:

[![The complete guided deployment: choose and authorize a target, build and bootstrap DEV, connect and prove knowledge, then create the Studio agent and register and verify the Managed Runtime app. Three Terraform plan approvals remain distinct from user and administrator actions.](diagrams/deployment-flow.svg)](deployment-flow.md)

Read each row left to right, then continue down. See the
[stage-by-stage guide](deployment-flow.md) for the approval and readiness gates.

This is **one guided request**, not a zero-input or guaranteed unattended deployment. You still need:

- **Target and consent:** choose subscription/tenant, region, project name and capacity admin; approve the actual Terraform plans and reviewed workspace definitions. Quota, preview availability and RBAC propagation can still block a run.
- **Entra setup and approval:** the initial Terraform identity needs directory permissions to create/configure the app, service principal and authorized clients. Copilot can prepare them through the reviewed Entra-only plan and hand the administrator the generated approval link and scope manifest. An administrator grants consent separately, in the portal or by running the runner with `--grant-admin-consent`; subscription Owner alone is not enough, and Terraform does not grant itself consent.
- **Azure/Fabric user sign-in:** after consent, use the generated tenant/scope outputs and the administrator-owned tenant prerequisites from [getting started](getting-started.md). Copilot can guide device login, but cannot perform your MFA or bypass policy. Plain `az login`, app creation or consent alone is not proof that Fabric write/execute scopes and workspace roles are available.
- **Only for opt-in Fabric Git synchronization:** supply `FABRIC_GITHUB_TOKEN` as a repository-scoped [Codespaces secret](https://docs.github.com/en/codespaces/managing-your-codespaces/managing-your-account-specific-secrets-for-github-codespaces), or `TF_VAR_github_token` in the deployment shell. It needs Contents read/write; restart an existing Codespace after adding the secret. Do not reuse Copilot's login or the Codespaces `GITHUB_TOKEN`.
- **Agent and app access:** a Power Platform environment where you can create agents and apps, a Copilot Studio license and Managed Runtime licensing. You sign in to `pac` and `ms` yourself, connect your own IQ connections and approve agent creation, configuration and app registration.
- **State retention:** prefer an existing encrypted, access-controlled remote backend and project-specific state key. A single-user local-state demo needs explicit acceptance and secure retention/backup before deleting the Codespace. Deleting a Codespace does not delete its Azure resources. Team/CI deployment requires remote state.

The App route uses GitHub's [authenticated Codespaces SSH transport](https://docs.github.com/en/codespaces/developing-in-a-codespace/using-github-codespaces-with-github-cli#ssh-into-a-codespace), not a claimed native App-to-Codespaces attachment. Do not publish port 2222, configure an SSH password or disable host-key verification. If the App host cannot access Codespaces, use CLI in the Codespace rather than silently deploying from the laptop.

On a failed/paused run, say **"Resume deployment in the same Codespace and backend."** Copilot reruns the runner, which checks existing state and operation receipts instead of resetting knowledge inputs or submitting duplicate jobs. It reports **DEV ready** only after document retrieval, live ontology retrieval and an authenticated public Web IQ search succeed, then continues with the agent and app; incomplete authentication or ingestion is reported as blocked/unverified. TEST stays empty and PROD, promotion, hosted deployment and sharing remain separate. Review generated DEV binding changes before committing them and before any later promotion.

## Use the repository skills

Open the derived repository in Copilot App, or start `copilot` at its root. [`AGENTS.md`](../AGENTS.md) supplies the small, always-on contract. The task-specific instructions live in [`.github/skills/`](../.github/skills), a [supported skills location for both App and CLI](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills); no global installation or copied App instructions are needed.

| Ask Copilot | What it does |
| --- | --- |
| "Deploy the platform." | Uses `deploy-platform` to run the deterministic runner for the full DEV deployment, agent and app, relaying its approvals and sign-ins. |
| "Use /initialize-project to configure this new repository." | Confirms project/region/admin choices, resolves the intended repository and published branch, and creates one ignored local tfvars file. No deployment. |
| "Use /template-maintenance to check my changes." | Runs scoped static checks without touching deployment state or executing cloud operations. |
| "Use /fabric-workspace to review these bindings." | Checks logical identities, model references and target-local binding rules; live bootstrap/promotion remains an explicit operation. |
| "Use /runtime-integration before building my agent and app." | Performs read-only checks in a chosen derived project and guides the native connector, authorization and app-build proof without claiming shell readiness. |
| "Use /scenario-authoring to add this domain workflow." | Uses the canonical layout and complete native work-package recipes, preserving platform ownership and explicit opt-in/runtime readiness gates. |

In CLI, use `/skills list` to see available skills and `/skills reload` after adding/updating them. In App, use **Customize > Skills**. Skills provide instructions, not permission to deploy or bypass tool approvals.

Keep new learning in one place: stable cross-task guardrails in `AGENTS.md`, a repeatable workflow in its existing skill, and product explanations in the canonical docs. Do not add session diaries, deployment IDs, credentials or copies of entire guides to always-loaded instructions.

## What gets templated

The shipped workspace has **no physical workspace/lakehouse/ontology IDs, SQL hostnames or saved notebook lakehouse attachments**. Its literal GUIDs are logical/lineage identities, and its generic names are a portable sample model.

| Value | How it gets the correct value |
| --- | --- |
| Project branding, region and Fabric admins | Explicit choices in local `infra/terraform.tfvars`; the initializer validates syntax without choosing cloud defaults. |
| GitHub owner/repository and bootstrap branch | The initialization skill resolves the new repository's published branch, not the template's owner or the current agent branch. |
| `.platform` logical IDs, plan references, TMDL lineage tags and binding markers | Preserve them. Regenerating all UUIDs would break item identity and binder checks. |
| `Scenario` / `ScenarioOntology`, `dbo` and demo table names | Portable workspace-local names; no project-name substitution is required. Coordinate any deliberate rename using the workspace skill. |
| Physical workspace/item GUIDs, OneLake URLs and SQL endpoint binding | The notebooks discover these in the **current Fabric workspace** when the explicitly attached Bootstrap plan runs. |
| Terraform knowledge item IDs | After successful DEV bootstrap, put the actual physical GUIDs into the same local tfvars file. Never use `.platform` IDs. |

For a non-Copilot setup, run the [initializer](../.github/skills/initialize-project/scripts/initialize_project.py) directly with `--help`, or follow the manual configuration in [getting started](getting-started.md). The helper uses Python's standard library and Git, offers `--dry-run`, creates the local file with owner-only permissions, and refuses existing tfvars/state. It never rewrites workspace definitions or changes Git remotes.

During deployment, the [knowledge configuration helper](../.github/skills/deploy-platform/scripts/configure_knowledge.py) fills the two knowledge-stage settings using verified physical DEV IDs. It preserves unrelated inputs and rejects conflicting/custom registrations rather than replacing them. Neither helper authenticates or deploys resources; the [deterministic runner](#deploy-deterministically) sequences them with native commands and explicit approvals.

Once a derived DEV workspace is bound, its reviewed Git export legitimately contains environment-specific source details. Keep those out of the pristine template, and rebind every promoted target; see [binding ownership and review](ontology-knowledge.md#review-the-generated-binding). Do not use blanket GUID replacement to reset a deployed repository.

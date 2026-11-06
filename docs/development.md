# Developer setup and Copilot

## Codespaces or a local dev container

In your **derived repository**, use **Code > Codespaces > Create codespace** on the intended branch. For a local checkout with Docker and VS Code's Dev Containers extension, run **Dev Containers: Reopen in Container**.

The [devcontainer](../.devcontainer/devcontainer.json) includes Azure CLI, Terraform, the standalone GitHub Copilot CLI (`copilot`, not the retired `gh copilot` extension), Git/GitHub CLI, Node.js/npm, Python 3 with venv support, Bash, jq, ripgrep and an SSH server for authenticated Codespaces access. It configures the Terraform, Azure CLI, Python, YAML and Copilot editor extensions.

Tools are installed during image build. The base image, Terraform and Copilot are pinned; Node uses the 24 release line and must be at least 24.11.0 for the web app. Copilot comes from its official GitHub release with mandatory SHA-256 verification, without an npm-registry dependency. No Docker is needed inside the container: the runner builds the agent and app images in Azure Container Registry.

The [IQ plugin feature](../.devcontainer/features/iq-plugins/install.sh) also
installs `azure`, `foundry-iq-skills`, `workiq`,
`microsoft-365-agents-toolkit` and `fabric-skills` from reviewed official
marketplace commits. It supplies a pinned Azure MCP binary, retains the
packages' hosted MCP declarations, and uses the image's Python 3.12 for Foundry
IQ helpers. See the [package/MCP map and authorization boundaries](copilot-tooling.md).
These are coding-assistant capabilities, not automatically enabled app or agent
integrations.

The post-create command only checks tool availability, minimum Node/Git/Python versions and all five plugin registrations. It does **not** install or update packages, start MCP servers, invoke skills, sign in, accept terms, generate tfvars, initialize Terraform, provision resources, synchronize Fabric, query Microsoft 365, or run notebooks. Python supports the local initializer, plugin hooks and static source checks; Node supports app builds and Node-based developer/MCP tools. The hosted agent's Python packages are installed only inside its image. Spark, Delta and NotebookUtils still run in Fabric, not locally. No Fabric CLI, fabric-cicd or azd is needed for the template's native deployment flow. Use optional product-workflow dependency checks only when that workflow is explicitly requested.

The [deterministic runner](#deploy-deterministically) signs you in itself: its first phase, `signin`, completes every CLI sign-in the run needs, back to back, before anything is created. Sign in by hand only for work outside the runner:

```sh
# Only if GitHub CLI is not already authenticated:
gh auth login

# Start from the repository root; use /login in Copilot if prompted:
copilot

# Azure CLI user sign-in (device code works in Codespaces):
az login --use-device-code --tenant "<tenant-id>"
az account set --subscription "<subscription-name-or-id>"
```

For a manual deployment, follow [identity bootstrap and consent](getting-started.md#3-bootstrap-the-fabric-user-auth-application) before the first Fabric plan. Terraform prepares the deployment app registration and permissions in the same state as the platform; only that initial plan targets `module.fabric_user_auth`. After consent, or when resuming with an already-consented registration, sign in with its generated scope if Azure CLI cannot get that token silently:

```sh
az login --use-device-code \
  --tenant "$(terraform -chdir=infra output -raw fabric_user_auth_tenant_id)" \
  --scope "$(terraform -chdir=infra output -raw fabric_user_auth_scope)"
az account set --subscription "<subscription-name-or-id>"
```

Copilot needs an active subscription and organization permission. A Codespaces-provided GitHub token may be sufficient for repository access but not Copilot requests; follow [Copilot authentication](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli) rather than reusing the Fabric Git PAT. Never put tokens in devcontainer files or paste them into chat.

The Copilot App uses its selected execution host; a devcontainer file does not automatically move App sessions into that container. The deployment skill can instead run commands in your chosen Codespace through `gh codespace ssh`. The App host needs GitHub CLI with access to that Codespace; all Azure tools, credentials, tfvars and state stay in the remote deployment checkout. For an explicitly local App deployment, install the equivalent tools on that host and reuse its original deployment checkout/backend, not a fresh worktree.

## Build the agent and web app

The template ships the hosted agent in
[`agents/scenario-assistant/`](../agents/scenario-assistant/README.md) and the
web app in [`app/`](../app/). [Getting started step 7](getting-started.md#7-create-the-agent-and-app)
publishes both through the runner. For an environment new to the project, run
the [runtime integration preflight](runtime-integration.md#preflight-is-not-readiness)
first.

The app is a standalone npm project with its own `package-lock.json`. From
`app/`, `npm ci`, `npm run lint`, `npm run build` and `npm test` check it, as CI
does. `npm run dev` serves the browser app with Vite on port **5173** and
proxies `/api` and `/config.json` to `npm start`, the app's dependency-free Node
server on port **8080**. [Local development](runtime-integration.md#local-development)
lists the server's settings and the sign-in redirect boundary. The container
forwards both ports; keep Codespaces ports private. When the browser is on
another machine, forward them privately from that machine, for example
`gh codespace ports forward 5173:5173 8080:8080 --codespace "<name>"`.

The hosted agent runs Python 3.13 in its image. `requirements.in` pins its
direct dependencies, and `requirements.txt` is their hash-pinned closure, which
the image installs with `--require-hashes`; regenerate it with the command in
`requirements.in` after any change. The [`hosted-agent.json`](../agents/scenario-assistant/hosted-agent.json)
definition sets its size, protocol, settings and toolbox; the runner fills its
`{{placeholders}}` from Terraform outputs and never writes the rendered result
to the repository.

Neither image is built locally, and releases are deliberate: commit and push,
then rerun `up`. The runner builds both images with `az acr build` from the
published commit and never deploys uncommitted or unpublished source.

## Deploy deterministically

One command runs the complete DEV flow, agent and app without an AI agent. It is the same runner Copilot uses for "Deploy the platform", so both paths create the same resources and require the same evidence. Run it in the derived repository's Codespace or devcontainer:

```sh
python3 .github/skills/deploy-platform/scripts/deploy.py up \
  --subscription "<subscription-id-or-name>" --local-state
```

Without `infra/terraform.tfvars`, also pass `--project-name`, `--location` and `--capacity-admin`; the runner calls the [initializer](#what-gets-templated). Use `--local-state` only for a single-user demo whose state you back up. For anything shared, declare a secured remote backend in `infra/` and pass `--backend-config <file>`. Target choices are remembered in ignored `.artifacts/deploy/state.json`, so later runs need only `up`; a different subscription, tenant or caller is refused.

**Sign in once, up front.** The first phase, `signin`, completes every sign-in the run needs before anything is created: GitHub CLI, the Azure CLI user and each token audience its phases request (Resource Manager, Graph, Fabric, the Fabric deployment scope after consent, Azure AI Search, and Microsoft Foundry when the run includes the agent or app). Missing ones run back to back, and each prints its device code in your terminal or, without one, on stderr; reports never contain codes. Only GitHub CLI's browser flow needs your own terminal (`gh auth login`); Codespaces are already signed in. Add `--through verify` to deploy and verify DEV first and publish the agent and app in a later run. `up --through signin` completes only the GitHub and Azure CLI sign-ins; every later run still checks its token audiences before it changes anything. Afterwards a sign-in prompt is rare: the deployment-scope token after consent is normally acquired silently.

**Administrator access is assumed.** The runner grants tenant-wide consent for exactly the declared delegated scopes of both registrations and registers the serverless Search preview feature as you, and lets Terraform, Fabric, Foundry and Azure Resource Manager attempt every change. Only if an API refuses for lack of permission does it stop with exit code `3`, naming who must grant what (`grants` in the `--json` report), for example the administrator consent link, Role Based Access Control Administrator on the subscription, or Foundry User on the project when Foundry refuses a toolbox or agent request. After that grant, rerun; completed work is not repeated. With `--manual-grants`, it never attempts consent or feature registration and prints them for an administrator instead.

| Phase | What it does | Stops for |
| --- | --- | --- |
| `signin` | Checks GitHub CLI and refuses the template; signs in to Azure CLI and any token audience that needs a fresh sign-in, including Microsoft Foundry for the agent and app | Your device codes; `gh auth login` in your own terminal |
| `config`, `preflight` | Repository, branch and tfvars checks; Terraform and Azure CLI 2.80+ versions; Azure user and subscription; provider overrides; a `location` with documented hosted agents; Fabric CU and model quota; Search tier; registers the serverless Search feature as you; PAT access only for opt-in Git | Missing inputs, region support or quota; a refused registration; PAT only for opt-in Git |
| `identity` | Saved plan targeted only at `module.fabric_user_auth`, then apply | Plan approval |
| `consent` | Grants tenant-wide consent for exactly the declared scopes as you when it is missing or partial, then checks the scoped token and Fabric reads, giving a fresh grant a few minutes to propagate | An administrator only if Entra refuses your grant |
| `platform` | Saved untargeted plan, then apply; enables the serverless Search identity that Terraform cannot set and verifies it, because the preview answers HTTP 500 | Plan approval |
| `bootstrap` | Imports your published `HEAD` through native Fabric APIs with `Bootstrap`, then verifies target-local binding; Git connect/sync only when enabled | Conflicting identities, changed existing definitions, an unavailable plan attachment |
| `knowledge` | Writes the discovered physical GUIDs with the knowledge helper; saved plan, then apply. A plan that creates or replaces serverless Search is applied as two plans: grants deferred, then granted after the identity exists | Plan approval |
| `verify` | Waits for ingestion; proves live ontology and document retrieval with your own token and an authenticated public Web IQ search | Running ingestion; portal onboarding, credentials or missing evidence |
| `agent` | Requires a clean, published `agents/scenario-assistant/`; builds its image in the registry, publishes a toolbox version and makes it the default, creates a hosted agent version and waits until it is active, then proves a fresh challenge, live ontology facts and the starter document through the agent as you | `agent-deploy` approval; a failed version |
| `app` | Requires a clean, published `app/`; builds its image, grants tenant-wide consent for the web registration's Foundry scope as you, creates or updates the Container App through Azure Resource Manager and waits for its revision, then checks `/healthz`, `/config.json` and a relayed fresh challenge as you | `app-deploy` approval; an administrator only if Entra refuses consent; a failed revision |

Every deployment includes Web IQ. `preflight` requires explicit Web IQ terms
acceptance and the pinned accounts API in the selected subscription. The normal
platform/knowledge plan creates its global G3 account. In `verify`, the runner
checks provisioning and makes one fixed public-only Web IQ search using
`WEB_IQ_API_KEY` from the environment. Missing portal onboarding/credentials waits;
API errors or missing citations block. Counts and optional freshness metadata
are recorded separately from agent/app readiness; no content or keys are saved.
See the [manual checklist](../preparation.md#web-iq) for required actions
and [runtime details](runtime-integration.md#web-iq) for the still-separate
agent integration. Missing Web IQ outputs or credentials cannot be skipped.

Fabric GitHub synchronization defaults to **off** (`fabric_git_enabled = false`).
Native import uses the Azure user's Fabric token, preserves `.platform` identities
and needs no PAT. It imports only missing items, verifies per-item results and
resumes accepted operations without overwriting live data or bindings. Set the
flag to `true` and supply a PAT only when Git synchronization is wanted.

Every phase checks live state before acting, so rerunning `up` resumes. It observes an accepted Fabric operation or an active notebook run instead of submitting another, and creates a fresh plan after a failed apply. Exit code `3` means a person must act; the output names the exact grant, link or command. Exit code `4` means approval is required. Approve interactively, or rerun with `--approve <phase>=<sha256-prefix>` for the exact saved plan it printed (`terraform -chdir=infra show <plan>` shows it), or with `--approve agent-deploy` or `--approve app-deploy` for the agent and app releases it listed. `--yes` approves everything in that run except deletions and replacements, which also need `--allow-replace`. `up --through <phase>` stops after a phase; every run re-verifies the earlier phases, so no gate can be skipped. `status` summarizes receipts without cloud calls, and `--json` prints a machine-readable report.

These choices are explicit flags, never automatic:

- `--manual-grants`: you are not an administrator, or your organization requires one for these changes. The runner prints the consent link and the feature-registration commands for an administrator instead of trying them as you.
- `--consent-confirmed`: an administrator confirmed consent for the deployment app or the web sign-in, but your sign-in cannot read the tenant's consent grants to verify it (so it could not grant them either).
- `--bootstrap-fallback notebooks`: when the tenant rejects plan attachment (`FeatureNotAvailable`), import or Git-sync the same reviewed definitions without it, then run `SeedScenario` and `BindScenario` as you.
- `--retry-failed`: after you inspected and fixed a failed or unconfirmed operation, continue from live state, and create a new hosted agent version in place of a failed one; without it the runner never resubmits an accepted request.

The runner refuses the reusable template, a different Git target, conflicts, non-additive incoming changes, any commit other than your pushed `HEAD`, uncommitted changes under `agents/scenario-assistant/` or `app/`, agent or app targets outside the deployment's project and subscription, and ambient Terraform overrides (`TF_CLI_ARGS*`, `TF_WORKSPACE`, extra `TF_VAR_*`). It never prints or stores tokens. It still needs you for device codes and MFA (up front) and the browser check of the web app, and an administrator only when your own permissions are refused, so it is not unattended CI: the bundled binder and the IQ proofs need a delegated user. `up` does not promote, grant other users access or delete resources.

## Delete the environment

Use the original checkout, tfvars, backend and `.artifacts/deploy/state.json`.
`down` also starts with `signin`, for only the token audiences teardown uses;
sign in as the original Azure user with the recorded subscription. Keep
Fabric consent active until Terraform finishes. Deleting the hosted agent and
toolbox uses the deployer's Foundry User role, which Terraform removes only
afterwards.

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
| 1 | Container App, then the hosted agent with its sessions (`force=true`) and the toolbox. |
| 2 | Full Terraform state: knowledge base/sources, Search ingestion assets, DEV/TEST workspaces and all data, pipeline, connections, capacity, Foundry, container registry and its images, Container Apps environment, monitoring, Web IQ, role assignments, and the deployment and web sign-in registrations. |
| 3 | Soft-deleted Foundry account, using its recorded subscription, resource group, name and location. |

`down` checks the repository, caller and targets without deployment quota or
readiness probes. No targeted destroy, tenant-policy changes or local-file
deletion. `--yes` cannot approve deletion. `destroy=<hash>`, `runtime-delete` and
`purge-foundry` are separate approvals. Rerun after sign-in, approval or an
unfinished operation; use `--retry-failed` only after inspecting an unconfirmed
purge. A failed Terraform apply requires a fresh plan/hash.
The runner preserves a nonsecret teardown inventory across partial deletion.

Manual cleanup:

- Permanently delete the reported DEV/TEST workspace IDs in **OneLake catalog > Govern > Workspaces** as a Fabric tenant admin. [Workspace retention](https://learn.microsoft.com/en-us/fabric/admin/workspace-retention) delays data erasure; [OneLake retains data for seven days after permanent item deletion](https://learn.microsoft.com/en-us/fabric/admin/item-recovery#permanently-delete-a-recoverable-item).
- Revoke Web IQ keys and remove the portal AppID/profile.
- Retain state and receipts until cleanup is confirmed. GitHub, backend storage, local files, tenant settings, licenses, external sources and service-retained backups/audit records remain.

## Deploy from one prompt

After these changes are included in the template's published branch:

1. Create a new repository with **Use this template**, and open its Codespace.
2. Complete the one-time prerequisites below. Start `copilot` in the Codespace terminal, or open the derived repository in Copilot App with GitHub CLI access to the Codespace.
3. Say **"Deploy the platform"**. In App, include **"Use my Codespace `<name>`"**; otherwise Copilot asks which one. Interactive mode is useful for initial sign-in and approvals.

Copilot loads [`deploy-platform`](../.github/skills/deploy-platform/SKILL.md), runs the [deterministic runner](#deploy-deterministically) and relays its gates:

[![The complete guided deployment: choose and authorize a target, build and bootstrap DEV, connect and prove knowledge, then publish and prove the Foundry hosted agent and deploy and verify the Container Apps web app. Three Terraform plan approvals remain distinct from release approvals and user and administrator actions.](diagrams/deployment-flow.svg)](deployment-flow.md)

Read each row left to right, then continue down. See the
[stage-by-stage guide](deployment-flow.md) for the approval and readiness gates.

This is **one guided request**, not a zero-input or guaranteed unattended deployment. You still need:

- **Target and consent:** choose subscription/tenant, a region with Foundry hosted agents, project name and capacity admin; approve the actual Terraform plans, reviewed workspace definitions and the agent and app releases. Quota, preview availability and RBAC propagation can still block a run.
- **Sign-ins, first:** the runner signs you in to Azure CLI, with every token audience the run needs, and checks GitHub CLI before it creates anything. You enter each device code and complete MFA yourself; Copilot never relays a code or bypasses policy. The deployment-scope token after consent is normally acquired silently.
- **Administrator access, assumed:** creating the deployment and web sign-in registrations needs Entra rights such as Application Developer, tenant-wide consent needs a role such as Cloud Application Administrator, and Terraform's role assignments need Owner or Role Based Access Control Administrator; subscription Owner alone does not cover the Entra parts. The runner assumes you have them: after the reviewed Entra-only plan it grants consent for exactly the declared scopes as you, and the `app` phase does the same for the web registration's Foundry scope. Only when an API refuses does Copilot relay the exact grant, such as the administrator approval link and scope manifest. Terraform never grants itself consent, and a sign-in, app creation or consent alone is not proof that Fabric write/execute scopes, tenant settings and workspace roles are available.
- **Only for opt-in Fabric Git synchronization:** supply `FABRIC_GITHUB_TOKEN` as a repository-scoped [Codespaces secret](https://docs.github.com/en/codespaces/managing-your-codespaces/managing-your-account-specific-secrets-for-github-codespaces), or `TF_VAR_github_token` in the deployment shell. It needs Contents read/write; restart an existing Codespace after adding the secret. Do not reuse Copilot's login or the Codespaces `GITHUB_TOKEN`.
- **Agent and app access:** nothing extra for the deployer, who receives Foundry User on the Foundry account through Terraform. You approve the agent and app releases and check the web app in your browser. Other users need an operator to grant them access; see the [access model](runtime-integration.md#access-model).
- **State retention:** prefer an existing encrypted, access-controlled remote backend and project-specific state key. A single-user local-state demo needs explicit acceptance and secure retention/backup before deleting the Codespace. Deleting a Codespace does not delete its Azure resources. Team/CI deployment requires remote state.

The App route uses GitHub's [authenticated Codespaces SSH transport](https://docs.github.com/en/codespaces/developing-in-a-codespace/using-github-codespaces-with-github-cli#ssh-into-a-codespace), not a claimed native App-to-Codespaces attachment. Do not publish port 2222, configure an SSH password or disable host-key verification. If the App host cannot access Codespaces, use CLI in the Codespace rather than silently deploying from the laptop.

On a failed/paused run, say **"Resume deployment in the same Codespace and backend."** Copilot reruns the runner, which checks existing state and operation receipts instead of resetting knowledge inputs or submitting duplicate jobs. It reports **DEV ready** only after document retrieval, live ontology retrieval and an authenticated public Web IQ search succeed, then continues with the agent and app; incomplete authentication or ingestion is reported as blocked/unverified. TEST stays empty and PROD, promotion and granting other users access remain separate. Review generated DEV binding changes before committing them and before any later promotion.

## Use the repository skills

Open the derived repository in Copilot App, or start `copilot` at its root. [`AGENTS.md`](../AGENTS.md) supplies the small, always-on contract. The task-specific instructions live in [`.github/skills/`](../.github/skills), a [supported skills location for both App and CLI](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills); no global installation or copied App instructions are needed.

| Ask Copilot | What it does |
| --- | --- |
| "Deploy the platform." | Uses `deploy-platform` to run the deterministic runner for the full DEV deployment, agent and app, relaying its approvals and sign-ins. |
| "Use /initialize-project to configure this new repository." | Confirms project/region/admin choices, resolves the intended repository and published branch, and creates one ignored local tfvars file. No deployment. |
| "Use /template-maintenance to check my changes." | Runs scoped static checks without touching deployment state or executing cloud operations. |
| "Use /fabric-workspace to review these bindings." | Checks logical identities, model references and target-local binding rules; live bootstrap/promotion remains an explicit operation. |
| "Use /runtime-integration before building my agent and app." | Performs read-only checks in a chosen derived project and guides the hosted agent and web app evidence, access and troubleshooting without claiming shell readiness. |
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
| `.github/CODEOWNERS`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `SUPPORT.md` | Template-owned. Replace them with your project's own owners and policies. CI (`.github/workflows/ci.yml`) and Dependabot work unchanged. |
| `LICENSE` | MIT terms for the template's code. Keep its notice: the MIT license requires it in copies of the code. |

For a non-Copilot setup, run the [initializer](../.github/skills/initialize-project/scripts/initialize_project.py) directly with `--help`, or follow the manual configuration in [getting started](getting-started.md). The helper uses Python's standard library and Git, offers `--dry-run`, creates the local file with owner-only permissions, and refuses existing tfvars/state. It never rewrites workspace definitions or changes Git remotes.

During deployment, the [knowledge configuration helper](../.github/skills/deploy-platform/scripts/configure_knowledge.py) fills the two knowledge-stage settings using verified physical DEV IDs. It preserves unrelated inputs and rejects conflicting/custom registrations rather than replacing them. Neither helper authenticates or deploys resources; the [deterministic runner](#deploy-deterministically) sequences them with native commands and explicit approvals.

Once a derived DEV workspace is bound, its reviewed Git export legitimately contains environment-specific source details. Keep those out of the pristine template, and rebind every promoted target; see [binding ownership and review](ontology-knowledge.md#review-the-generated-binding). Do not use blanket GUID replacement to reset a deployed repository.

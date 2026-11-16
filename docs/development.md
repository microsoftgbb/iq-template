# Developer setup and Copilot

## Codespaces or a local dev container

In your **derived repository**, use **Code > Codespaces > Create codespace** on the intended branch. For a local checkout with Docker and VS Code's Dev Containers extension, run **Dev Containers: Reopen in Container**.

The [devcontainer](../.devcontainer/devcontainer.json) includes Azure CLI, Terraform, the standalone GitHub Copilot CLI (`copilot`, not the retired `gh copilot` extension), Git/GitHub CLI, Node.js/npm, Python 3 with venv support, Bash, jq, ripgrep and an SSH server for authenticated Codespaces access. It configures the Terraform, Azure CLI, Python, YAML and Copilot editor extensions.

Tools are installed during image build. The base image, Terraform and Copilot are pinned; Node uses the 24 release line and must be at least 24.11.0 for the web app. Copilot comes from its official GitHub release with mandatory SHA-256 verification, without an npm-registry dependency. No Docker is needed inside the container: the runner builds the app's image with ACR Tasks.

The [IQ plugin feature](../.devcontainer/features/iq-plugins/install.sh) also
installs `azure`, `foundry-iq-skills`, `workiq`,
`microsoft-365-agents-toolkit` and `fabric-skills` from reviewed official
marketplace commits. It supplies a pinned Azure MCP binary, retains the
packages' hosted MCP declarations, and uses the image's Python 3.12 for Foundry
IQ helpers. See the [package/MCP map and authorization boundaries](copilot-tooling.md).
These are coding-assistant capabilities, not automatically enabled app or agent
integrations.

The post-create command only checks tool availability, minimum Node/Git/Python versions and all five plugin registrations. It does **not** install or update packages, start MCP servers, invoke skills, sign in, accept terms, generate tfvars, initialize Terraform, provision resources, synchronize Fabric, query Microsoft 365, or run notebooks. Python supports the runner, the local initializer, plugin hooks and static source checks; Node supports the app and Node-based developer/MCP tools. Spark, Delta and NotebookUtils still run in Fabric, not locally. No Fabric CLI, fabric-cicd or azd is needed for the template's native deployment flow. Use optional product-workflow dependency checks only when that workflow is explicitly requested.

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

## Agents and app development

The template ships two Foundry prompt agents in [`agents/`](../agents/README.md)
and the web app in [`app/`](../app/README.md). [Getting started step 7](getting-started.md#7-create-the-agents-and-app)
publishes both through the runner. For an environment new to the project, run the
[runtime integration preflight](runtime-integration.md#preflight-is-not-readiness)
first.

The agents need no build: each variant directory holds JSON and Markdown that the
runner fills from Terraform outputs and publishes. Check them offline, as CI does:

```sh
python3 .github/skills/deploy-platform/scripts/foundry_agent.py validate
```

It loads every variant, including the optional Work IQ tool, refuses unknown
placeholders, inlined instructions or tools, a tool with other keys, headers, an
approval step or an empty allow-list, and requires the shared `agent.json` and
`instructions.md` to be byte-identical in all variants. `render` previews the
exact body the runner would send ([agent route](runtime-integration.md#publish)).

The app is a standalone npm project in `app/` with its own `package-lock.json`. From
`app/`:

| Command | Runs |
| --- | --- |
| `npm run dev:mock` | The page at `http://localhost:3000/` with canned answers for the sample questions: no sign-in and no Azure |
| `npm run dev` | The real server with Vite's middleware at `http://localhost:3000/`, against the deployment that the ignored `app/.env.local` names |
| `npm run build` | The type check, then the page into `dist/` and the server into one module, `dist-server/main.mjs` |
| `npm start` | The built server on port 8080, with the same settings |
| `npm run lint`, `npm test` | ESLint; the unit tests, with `node --test` and type stripping |

Write `app/.env.local` from `terraform -chdir=infra output -json app_settings`, and
register `app_local_redirect_uris = ["http://localhost:3000/redirect.html"]` before
signing in locally ([local development](runtime-integration.md#local-development)).
The container forwards ports 3000 and 8080; keep Codespaces ports private. When the
browser is on another machine, forward privately from that machine, for example
`gh codespace ports forward 3000:3000 --codespace "<name>"`.

Keep these boundaries:

- **Agent layer.** The page calls only [`app/src/live/`](../app/src/live/): the wire
  protocol (`protocol.ts`), the stream reader, one turn, the reply parser and safe
  renderer (`reply.ts`), the MSAL sign-in (`auth.ts`) and the client. Only
  `auth.ts` and the redirect bridge, `src/redirect.ts`, import MSAL.
- **Server.** [`app/server/`](../app/server/) validates its settings (`config.ts`),
  verifies tokens (`auth.ts`), handles a turn (`turn.ts`), streams it from Foundry
  (`foundry.ts`), serves the built files with a strict content security policy
  (`static.ts`, `http.ts`) and traces (`telemetry.ts`). It accepts only
  `{ cid, variant, question, previousResponseId? }` and builds no prompt of its own.
- **Mock mode.** The app loads [`app/src/mock/client.ts`](../app/src/mock/client.ts)
  only behind the literal `import.meta.env.MODE === 'mock'` comparison, so production
  builds drop it. Keep that comparison literal.
- **Reply format.** Change the agents' shared instructions, the parser and its tests,
  and the mock answers together ([reply format](architecture.md#reply-format)).
- **Tests.** `app/tests/*.test.ts` import the pure modules and the server directly,
  with fake Foundry streams and signing keys, so keep those modules free of React and
  Vite-only APIs, import them with explicit `.ts` paths and keep all TypeScript
  erasable.

The runner builds the image with ACR Tasks from the committed files under `app/`,
so you need no Docker; [`app/Dockerfile`](../app/Dockerfile) builds a distroless,
non-root Node.js 24 image without `node_modules`. CI builds it too, starts it with
placeholder settings and checks its health endpoint, its refusal of an
unauthenticated question and of a missing configuration, and the redirect bridge's
framing header. The agent phase publishes the definitions in this checkout, and the
app phase builds only committed app files: commit before you rerun `up`.

## Deploy deterministically

One command runs the complete DEV flow, agents and app without an AI agent. It is the same runner Copilot uses for "Deploy the platform", so both paths create the same resources and require the same evidence. The runner is recommended, not mandatory: another deployment path must preserve the same explicit target selection, approvals, credential handling and readiness checks. Run it in the derived repository's Codespace or devcontainer:

```sh
python3 .github/skills/deploy-platform/scripts/deploy.py up \
  --subscription "<subscription-id-or-name>" --local-state
```

Without `infra/terraform.tfvars`, also pass `--project-name`, `--location` and `--capacity-admin`, and optionally `--github-branch`; the runner calls the [initializer](#what-gets-templated). Use `--local-state` only for a single-user deployment whose state you back up. For anything shared, declare a secured remote backend in `infra/` and pass `--backend-config <file>`. Target choices are remembered in ignored `.artifacts/deploy/state.json`, so later runs need only `up`; a different subscription, tenant or caller is refused.

**Sign in once, up front.** The first phase, `signin`, completes every sign-in the run needs before anything is created: GitHub CLI, the Azure CLI user and each token audience its phases request (Resource Manager, Graph, Fabric, the Fabric deployment scope after consent, Azure AI Search, and Microsoft Foundry when the run includes the agent phase). Missing ones run back to back, and each prints its device code in your terminal or, without one, on stderr; reports never contain codes. Only GitHub CLI's browser flow needs your own terminal (`gh auth login`); Codespaces are already signed in. Add `--through verify` to deploy and verify DEV first and publish the agents and app in a later run. `up --through signin` completes only the GitHub and Azure CLI sign-ins; every later run still checks its token audiences before it changes anything. Afterwards a sign-in prompt is rare: the deployment-scope token after consent is normally acquired silently.

**Administrator access is assumed.** The runner grants tenant-wide consent for exactly the declared delegated scopes and registers the serverless Search preview feature as you, and lets Terraform, Fabric, Foundry and Azure Resource Manager attempt every change. It grants the deployment registration's Fabric scopes in `consent`; the app registration's one Foundry scope in `consent` once the registration exists, or at the start of `app`; and, only with `work_iq_enabled`, Work IQ's `WorkIQAgent.Ask` in `verify`. Every user still consents once to each tool that acts as them, through the link the app shows, and, with Work IQ, to `offline_access` unless an administrator granted it. Only if an API refuses for lack of permission does the runner stop with exit code `3`, naming who must grant what (`grants` in the `--json` report), for example the administrator consent link, Role Based Access Control Administrator on the subscription, or Foundry User when Foundry refuses an agent request. After that grant, rerun; completed work is not repeated. With `--manual-grants`, it never attempts consent or feature registration and prints them for an administrator instead.

| Phase | What it does | Stops for |
| --- | --- | --- |
| `signin` | Checks GitHub CLI and refuses the template and its forks; signs in to Azure CLI and any token audience that needs a fresh sign-in, including Microsoft Foundry for the agent phase | Your device codes; `gh auth login` in your own terminal |
| `config`, `preflight` | Repository, branch, backend and tfvars checks; the tools and Terraform version; your Azure user and subscription; provider overrides; the Web IQ terms and API; the Power BI, Foundry API and, with Work IQ, Work IQ service principals; Fabric CU quota, counting only the CUs a larger SKU adds to an existing capacity; model quota summed per quota for the deployments still to create; the Search tier; registers the serverless Search feature as you; PAT access only for opt-in Git | Missing inputs, quota or a principal; a refused registration; PAT only for opt-in Git |
| `identity` | Saved plan targeted only at `module.fabric_user_auth`, then apply | Plan approval |
| `consent` | Grants tenant-wide consent for exactly the declared scopes as you when it is missing or partial, checks the scoped token and Fabric reads, giving a fresh grant a few minutes to propagate, and grants the app's sign-in scope once its registration exists | An administrator only if Entra refuses your grant |
| `platform` | Saved untargeted plan, then apply; enables the serverless Search identity that Terraform cannot set and verifies it, because the preview answers HTTP 500 | Plan approval |
| `bootstrap` | Imports your published `HEAD` through native Fabric APIs with `Bootstrap`; while the binding is unverified, runs `SeedScenario` (only until it first completes) and `BindScenario`, and any updated notebook again; then verifies the SQL-endpoint binding and its pins. Git connect/sync only when enabled | Conflicting identities; changed definitions without an approved `--update-definitions`; an unavailable plan attachment |
| `knowledge` | Writes the discovered physical GUIDs with the knowledge helper; saved plan, then apply. A plan that creates or replaces serverless Search is applied as two plans: grants deferred, then granted after the identity exists. Reminds you to publish the agents again after `work_iq_enabled` changed | `knowledge-registration` and plan approvals; a missing `WEB_IQ_API_KEY` |
| `verify` | Waits for every folder source's ingestion; proves live ontology retrieval through the check-only knowledge base and document retrieval through the agents' knowledge base with your own token, one public-only Web IQ search and one public-context retrieval; with Work IQ, grants `WorkIQAgent.Ask` and checks the `work-iq` connection, without a Work IQ call | Running ingestion; Web IQ portal steps or credentials; Work IQ consent; missing evidence |
| `agent` | Renders both definitions from Terraform's outputs with [`foundry_agent.py`](../.github/skills/deploy-platform/scripts/foundry_agent.py), leaving out the Work IQ tool unless enabled; compares each digest with the agent's latest version; creates or publishes only what changed; reads them back | `agent-publish` approval; a Foundry role for the deployer |
| `app` | Grants the app's sign-in scope if missing; computes the image tag from the committed `app/` files, builds a missing image with ACR Tasks, saves an untargeted plan with that tag, writes it as `app_image_tag` into tfvars once the plan is approved, applies it, then checks `/healthz` and that `/api/config` serves this deployment's client ID | Uncommitted app files; `app-build` and plan approvals; a missing `WEB_IQ_API_KEY` |

| Command | Does |
| --- | --- |
| `up [--through <phase>]` | Runs the phases in order, re-verifying earlier ones, until done, the given phase or a gate. No gate can be skipped. |
| `status [--json]` | Summarizes the receipts without cloud calls |
| `down` | Signs in, then deletes the environment; see [delete the environment](#delete-the-environment) |

| Exit | Meaning |
| --- | --- |
| `0` | Done through the requested phase |
| `1` | Usage error |
| `2` | Blocked: fix the cause from the message and the receipts, then rerun |
| `3` | A person must act (a sign-in, a missing permission's grant), or a cloud operation is still running: follow the printed link or command, then rerun |
| `4` | Approval required: approve interactively, or rerun with `--approve` |

| `--approve` | Approves |
| --- | --- |
| `identity`, `platform`, `knowledge` or `app` `=<sha256-prefix>` | That exact saved plan; inspect it with `terraform -chdir=infra show <plan>` |
| `item-update=<sha256-prefix>` | Replacing the changed definitions that `--update-definitions` lists |
| `knowledge-registration` | Replacing a knowledge registration the helper wrote earlier; the knowledge plan then replaces its source, which also needs `--allow-replace` |
| `agent-publish` | Creating every missing agent and publishing a new version of every changed one |
| `app-build` | Building the app's image for the printed tag with ACR Tasks |
| `destroy=<sha256-prefix>`, `purge-foundry` | The teardown steps |
| `shared-action-group` | Teardown deleting this resource group's Smart Detection action group while alert rules outside the deployment still notify it |

`--yes` approves this run's plans, publications and builds. It never approves `destroy`, `purge-foundry`, `item-update` or `shared-action-group`, and a plan that deletes or replaces resources also needs `--allow-replace`. These choices are explicit flags, never automatic:

| Flag | Use when |
| --- | --- |
| `--manual-grants` | You are not an administrator, or your organization requires one for these changes. The runner prints the consent links and the feature-registration commands for an administrator instead of trying them as you. |
| `--consent-confirmed` | An administrator confirmed consent, but your sign-in cannot read the tenant's grants to verify it (so it could not grant them either). |
| `--bootstrap-fallback notebooks` | The tenant rejects plan attachment (`FeatureNotAvailable`): import or Git-sync the same reviewed definitions without it, then run `SeedScenario` and `BindScenario` as you. |
| `--update-definitions` | You want deployed notebooks, the ontology or the plan replaced with the reviewed commit's. Lakehouses are never replaced. |
| `--retry-failed` | You inspected and fixed a failed or unconfirmed operation. Without it, the runner never resubmits an accepted request. |
| `--allow-replace` | You reviewed a plan that deletes or replaces resources. |

Other options: `--non-interactive` stops with exit 3 or 4 instead of prompting; `--json` prints a machine-readable report; `--timeout-minutes` sets each phase's observation window (default 30); `--tenant` names the expected Entra tenant; `--ontology-question`, `--document-question` and the repeatable `--expect` change the readiness evidence.

Every phase checks live state before acting, so rerunning `up` resumes. It observes an accepted Fabric operation or an active notebook run instead of submitting another, and plans afresh after a failed apply. An apply discards every other phase's saved plan, which Terraform would refuse as stale; the next run plans those again. `.artifacts/deploy/state.json` holds the target, each phase's result, pending plans, operation receipts, each agent's receipts (its name, version, definition digest and Work IQ setting) and the app's (its image builds, URL and tag). Never edit or delete receipts to get past a check. A read that fails in transit or with HTTP 429 or 5xx is retried, at most three attempts after `Retry-After` (up to 60 seconds) or a short backoff, and so is a read-only Azure CLI lookup that fails transiently; a write, a refusal or another 4xx is never retried. The configuration check evaluates inputs with `terraform console` and ignores the state-lock notices a remote backend prints.

**Web IQ.** Every deployment includes Web IQ. `preflight` requires `web_iq_terms_accepted = true` and the pinned accounts API. From the `knowledge` phase on, every plan and apply, the `app` phase's included, also manages the public-context knowledge source, which stores the Web IQ key write-only: the runner passes `WEB_IQ_API_KEY` to exactly those plans and applies, never to destroy plans, and waits without it. Rotate the key by incrementing `web_iq_api_key_version`. `verify` makes one public-only Web IQ search and one cited retrieval through the public-context knowledge base; both are billable and never retried automatically. Missing portal onboarding waits; API errors or missing citations block. See [Web IQ setup](getting-started.md#web-iq) and the [public-context route](runtime-integration.md#web-iq).

**Fabric items.** Fabric GitHub synchronization defaults to **off** (`fabric_git_enabled = false`). Native import uses your Fabric token and no PAT, keeps `.platform` identities, imports only missing items, checks each item's result and never overwrites live data or bindings. A changed notebook, ontology or plan stops the run until you approve its update with `--update-definitions`; a lakehouse definition is never replaced. Items removed from `workspace/` are reported and left in DEV. Set the flag to `true` and supply a PAT only when Git synchronization is wanted. Review the generated DEV binding before any promotion; see [review the generated binding](ontology-knowledge.md#review-the-generated-binding).

**Agents.** The `agent` phase fills each definition from Terraform's `foundry_agents`, `agent_settings`, `foundry_tool_connections` and `fabric_iq_connections` outputs, records the rendered body's sha256 in the agent's metadata and compares it with the latest version's, so a rerun publishes nothing that is unchanged. It records an attempted publication before sending it, so a rerun observes it instead of repeating it blindly, and reads every agent back before reporting it ready. It publishes the definitions in this checkout: commit agent changes first.

**App.** The `app` phase builds only committed files: it refuses uncommitted or untracked changes under `app/`. Their content digest at `HEAD` is the image tag, so an unchanged app builds nothing and plans no new revision. It plans with the tag as a variable and writes it into exactly one `app_image_tag` line of `infra/terraform.tfvars` only after you approve that plan, so an earlier phase's full plan never deploys an image you haven't approved; it refuses a tfvars file without that line before it builds anything.

**Refusals.** The runner refuses the reusable template, a different Git target, conflicts, non-additive incoming changes, any bootstrap commit other than your pushed `HEAD`, a service-principal sign-in, Terraform debug logging (`TF_LOG*`) and ambient Terraform overrides (`TF_CLI_ARGS*`, `TF_WORKSPACE`, `TF_DATA_DIR` and any `TF_VAR_*` but `TF_VAR_github_token`, so supply the Web IQ key only as `WEB_IQ_API_KEY`). It never prints or stores tokens. It still needs you for device codes and MFA (up front), each user's tool consent in the app and the browser check, and an administrator only when your own permissions are refused, so it is not unattended CI: the bundled binder and the IQ proofs need a delegated user. `up` does not promote, grant other users access or delete resources.

## Delete the environment

Use the original checkout, tfvars, backend and `.artifacts/deploy/state.json`.
`down` also starts with `signin`, for only the token audiences teardown uses;
sign in as the original Azure user with the recorded subscription. Keep
Fabric consent active until Terraform finishes.

```sh
python3 .github/skills/deploy-platform/scripts/deploy.py down --non-interactive
# Inspect the saved plan path printed above:
terraform -chdir=infra show "<plan-path>"
python3 .github/skills/deploy-platform/scripts/deploy.py down --non-interactive \
  --allow-delete --approve "destroy=<sha256-prefix>" --approve purge-foundry
```

| Order | Deletes |
| --- | --- |
| 1 | Everything in Terraform state: the container app, its environment, registry with its images, identity and sign-in registration; knowledge bases and sources and their Search ingestion assets; the DEV and TEST workspaces with all their data; the pipeline, connections and capacity; Foundry, with both agents in its project; Web IQ; Log Analytics and Application Insights with their traces; with Work IQ, its registration and connection; role assignments and the deployment identity. Two destroy-time actions also delete the Failure Anomalies alert rule that Azure creates beside Application Insights and, if Azure created it in this resource group, the Smart Detection action group, which would otherwise keep the resource group from being deleted. Azure creates that group beside a subscription's first Application Insights component and reuses it for later ones elsewhere, so before planning, `down` lists the subscription's alert rules outside this resource group that still notify it. If any do, it stops until you give them another action group or approve the loss with `--approve shared-action-group`. |
| 2 | The soft-deleted Foundry account, by its recorded subscription, resource group, name and location. |

`down` checks the repository, caller and targets without deployment quota or
readiness probes. No targeted destroy, tenant-policy changes or local-file
deletion. `--yes` cannot approve deletion: `destroy=<hash>` and `purge-foundry` are
separate approvals. Rerun after sign-in, approval or an unfinished operation; use
`--retry-failed` only after inspecting an unconfirmed purge. A failed Terraform
apply requires a fresh plan/hash. The runner preserves a nonsecret teardown
inventory across partial deletion.

Manual cleanup:

- Permanently delete the reported DEV/TEST workspace IDs in **OneLake catalog > Govern > Workspaces** as a Fabric tenant admin. [Workspace retention](https://learn.microsoft.com/en-us/fabric/admin/workspace-retention) delays data erasure; [OneLake retains data for seven days after permanent item deletion](https://learn.microsoft.com/en-us/fabric/admin/item-recovery#permanently-delete-a-recoverable-item).
- Microsoft Entra keeps the deleted app registrations under **Deleted applications** for 30 days; restore or permanently delete them there.
- Revoke Web IQ keys and remove the portal AppID/profile.
- Retain state and receipts until cleanup is confirmed. GitHub, backend storage, local files, tenant settings, licenses, Copilot Credits billing, external sources and service-retained backups/audit records remain.

## Deploy from one prompt

After these changes are included in the template's published branch:

1. Create a new repository with **Use this template**, and open its Codespace.
2. Complete the one-time prerequisites below. Start `copilot` in the Codespace terminal, or open the derived repository in Copilot App with GitHub CLI access to the Codespace.
3. Say **"Deploy the platform"**. In App, include **"Use my Codespace `<name>`"**; otherwise Copilot asks which one. Interactive mode is useful for initial sign-in and approvals.

Copilot loads [`deploy-platform`](../.github/skills/deploy-platform/SKILL.md), runs the [deterministic runner](#deploy-deterministically) and relays its gates:

[![The complete guided deployment: choose and authorize a target, build and bootstrap DEV, connect and prove knowledge, then publish the Foundry prompt agents and build and deploy the Container Apps web app. Four saved-plan approvals remain distinct from the publish and build approvals and from user and administrator actions.](diagrams/deployment-flow.svg)](deployment-flow.md)

Read each row left to right, then continue down. See the
[stage-by-stage guide](deployment-flow.md) for the approval and readiness gates.

This is **one guided request**, not a zero-input or guaranteed unattended deployment. You still need:

- **Target and consent:** choose subscription/tenant, one region that offers Foundry with the configured models, Container Apps and Container Registry, project name and capacity admin; approve the actual Terraform plans, reviewed workspace definitions, the agent publication and the image build. Quota, preview availability and RBAC propagation can still block a run.
- **Sign-ins, first:** the runner signs you in to Azure CLI, with every token audience the run needs, and checks GitHub CLI before it creates anything. You enter each device code and complete MFA yourself; Copilot never relays a code or bypasses policy. The deployment-scope token after consent is normally acquired silently.
- **Administrator access, assumed:** creating the deployment and app sign-in registrations needs Entra rights such as Application Developer, tenant-wide consent needs a role such as Cloud Application Administrator, and Terraform's role assignments need Owner or Role Based Access Control Administrator; subscription Owner alone does not cover the Entra parts. The runner assumes you have them: after the reviewed Entra-only plan it grants consent for exactly the declared scopes as you, and does the same for the app registration's Foundry scope and, with Work IQ, `WorkIQAgent.Ask`. Only when an API refuses does Copilot relay the exact grant, such as the administrator approval link and scope manifest. Terraform never grants itself consent, and a sign-in, app creation or consent alone is not proof that Fabric write/execute scopes, tenant settings and workspace roles are available.
- **Only for opt-in Fabric Git synchronization:** supply `FABRIC_GITHUB_TOKEN` as a repository-scoped [Codespaces secret](https://docs.github.com/en/codespaces/managing-your-codespaces/managing-your-account-specific-secrets-for-github-codespaces), or `TF_VAR_github_token` in the deployment shell. It needs Contents read/write; restart an existing Codespace after adding the secret. Do not reuse Copilot's login or the Codespaces `GITHUB_TOKEN`.
- **Web IQ key:** after linking the Web IQ resource in its portal, supply `WEB_IQ_API_KEY` in the deployment shell, without echo or shell history, before knowledge setup and in every later run ([Web IQ](getting-started.md#web-iq)). Never paste it into chat.
- **Agent and app access:** nothing extra for the deployer, who receives Foundry User on the Foundry account through Terraform. You approve the publication and the build, consent to each tool that acts as you, and check the web app in your browser. Other users need an operator to grant them access; see the [access model](runtime-integration.md#access-model).
- **State retention:** prefer an existing encrypted, access-controlled remote backend and project-specific state key. A single-user local-state demo needs explicit acceptance and secure retention/backup before deleting the Codespace. Deleting a Codespace does not delete its Azure resources. Team/CI deployment requires remote state.

The App route uses GitHub's [authenticated Codespaces SSH transport](https://docs.github.com/en/codespaces/developing-in-a-codespace/using-github-codespaces-with-github-cli#ssh-into-a-codespace), not a claimed native App-to-Codespaces attachment. Do not publish port 2222, configure an SSH password or disable host-key verification. If the App host cannot access Codespaces, use CLI in the Codespace rather than silently deploying from the laptop.

On a failed/paused run, say **"Resume deployment in the same Codespace and backend."** Copilot reruns the runner, which checks existing state and operation receipts instead of resetting knowledge inputs or submitting duplicate jobs. It reports **DEV ready** only after document retrieval, live ontology retrieval, an authenticated public Web IQ search and a public-context retrieval succeed, then continues with the agents and app; incomplete authentication or ingestion is reported as blocked/unverified. TEST stays empty and PROD, promotion and granting other users access remain separate. Review generated DEV binding changes before committing them and before any later promotion.

## Use the repository skills

Open the derived repository in Copilot App, or start `copilot` at its root. [`AGENTS.md`](../AGENTS.md) supplies the small, always-on contract. The task-specific instructions live in [`.github/skills/`](../.github/skills), a [supported skills location for both App and CLI](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills); no global installation or copied App instructions are needed.

| Ask Copilot | What it does |
| --- | --- |
| "Deploy the platform." | Uses `deploy-platform` to run the deterministic runner for the full DEV deployment, agents and app, relaying its approvals and sign-ins. |
| "Use /initialize-project to configure this new repository." | Confirms project/region/admin choices, resolves the intended repository and published branch, and creates one ignored local tfvars file. No deployment. |
| "Use /template-maintenance to check my changes." | Runs scoped static checks without touching deployment state or executing cloud operations. |
| "Use /fabric-workspace to review these bindings." | Checks logical identities, model references and target-local binding rules; live bootstrap/promotion remains an explicit operation. |
| "Use /runtime-integration before publishing my agents and app." | Performs read-only checks in a chosen derived project and guides the prompt agents' and web app's evidence, access and troubleshooting without claiming shell readiness. |
| "Use /scenario-authoring to add this domain workflow." | Uses the canonical layout and complete native work-package recipes, including agent variants, preserving platform ownership and explicit opt-in/runtime readiness gates. |

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

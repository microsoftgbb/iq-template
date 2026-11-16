---
name: template-maintenance
description: Maintain and statically validate the Fabric and Foundry repository template, including Terraform, the devcontainer, onboarding scripts and Copilot guidance. Use for template changes or local checks without live Azure/Fabric deployment or notebook execution.
---

# Maintain the template

Read `AGENTS.md` and inspect the current diff before editing. Preserve unrelated changes and staging. Treat ignored configuration/state as user-owned; inspect filenames, not contents.

## Locate the relevant contract

| Change | Read before editing |
| --- | --- |
| Resource naming, tags or inputs | `infra/main.tf`, `infra/variables.tf`, `infra/terraform.tfvars.example` |
| Capacity, Git credentials or deployment pipeline | `infra/fabric.tf`, `infra/providers.tf`, `cicd/README.md` |
| Delegated deployment identity or consent handoff | `infra/identity.tf`, `infra/modules/fabric-user-auth/`, `infra/fabric.tf`, `docs/getting-started.md` and the `deploy-platform` skill |
| Foundry or knowledge retrieval | `infra/foundry.tf`, `infra/knowledge.tf`, `infra/outputs.tf`, `docs/ontology-knowledge.md` |
| Web IQ | `infra/web_iq.tf`, `preparation.md`, and the existing deployment runner/probe tests |
| Work IQ | `infra/work_iq.tf`, the `work-iq` connection in `infra/foundry.tf`, `agents/scenario-with-iq/tools/WorkIQMail.json`, the runner's `verify` and `agent` phases, `docs/getting-started.md#work-iq` |
| Monitoring and traces | `infra/monitoring.tf`, `app/server/telemetry.ts`, `docs/runtime-integration.md#traces-and-telemetry` |
| Workspace definitions or notebooks | The `fabric-workspace` skill and its linked guides |
| Developer tools or initialization | `.devcontainer/`, the `initialize-project` skill and `docs/development.md` |
| Guided deployment | The `deploy-platform` skill, its runner (`scripts/deploy.py`), its native-request reference and `cicd/README.md` |
| Prompt agents or their publication | `agents/` and its README, `scripts/foundry_agent.py`, the agent outputs in `infra/app.tf` and `infra/outputs.tf`, the runner's `agent` phase, `docs/runtime-integration.md` |
| Web app or its hosting | `app/` (page, `src/live/`, `server/`, `Dockerfile`) and its README, `infra/app.tf`, the runner's `app` phase, `docs/runtime-integration.md` |

Preserve the non-obvious infrastructure constraints:

- Create capacity before looking up its Fabric GUID; workspaces cannot use its Azure resource ID.
- Keep the user-auth module in the platform's state. Its one-time targeted bootstrap is the only targeting exception; never run it during maintenance. Keep delegated scopes and pre-authorized clients separate from administrator-owned consent, and retain Fabric's dependency on the registration for safe teardown. Read shared Microsoft service principals rather than managing their lifecycle.
- Default to native Fabric item import without a PAT; keep GitHub synchronization opt-in with `fabric_git_enabled`. Both paths attach the reviewed plan outside Terraform. Preserve logical IDs, per-item result checks, resumable receipts, empty TEST and administrator-owned tenant settings.
- Require Web IQ terms acceptance and public-search verification. Do not add an enable/disable switch or skip missing account evidence. The pinned AzAPI provider lacks its embedded schema; retain the resource-scoped validation exception, not a provider-wide bypass. Keep the key ephemeral and write-only, passed by the runner from `WEB_IQ_API_KEY` to every plan and apply after knowledge setup and never to destroy plans. Portal AppID linking and a public-only API probe do not prove the agents' public-context tool; use `preparation.md` for the manual gates.
- Keep Work IQ opt-in (`work_iq_enabled = false`): without it, no Work IQ registration, connection or tool exists, and the runner omits `WorkIQMail` from the rendered agent. Its registration stays a public client without a secret, and the tool stays a read-only `fetch`.
- Use `Microsoft.CognitiveServices/accounts` projects, not ML hubs. Keep model versions/SKUs/quota configurable.
- Manage Search sources/bases through data-plane `restful_resource`, not unsupported ARM types. Retain CRUD and drift detection, source rebuild triggers, and knowledge-base-before-source deletion ordering. The OneLake source owns its generated search assets.
- Create a serverless Search service without a managed identity: its preview ends in `Failed` when one is requested and answers HTTP 500 to identity writes and to any change once an identity exists. Keep the runner's verified identity step, `ignore_changes = [identity]`, the identity grants gated until it exists (including `search_identity_ready` for the staged replacement plan), and replacement rather than update for serverless setting or tag changes. Read the admin key through the ephemeral action, never into state.
- Look up Git-owned items rather than creating duplicate lakehouses/notebooks in Terraform. Keep DEV-only Search permissions and conditional knowledge resources when the lakehouse ID is null.
- Keep deployment-pipeline replacement semantics visible when changing stages. Do not hide drift or paper over unsupported provider behavior.
- Keep the app's hosting, its container app (created once `app_image_tag` is set) and every project connection in Terraform, and the agent versions and the image build in the runner. Keep the registry's admin user disabled; the app identity gets only `AcrPull` and Monitoring Metrics Publisher. Keep the sign-in registration a secretless single-page client whose only API permission is the delegated `https://ai.azure.com/user_impersonation` scope and whose redirect URIs are only `/redirect.html` bridges. Keep Application Insights Entra-only, `AppGenAIContent` protected with Privileged Monitoring Data Reader for the deployer only, and the destroy-time Smart Detection cleanup. Match observed service behavior so plans stay clean: Foundry stores project connections with `isSharedToAll = false`; azurerm 5.x accepts the Container Apps environment's workspace only with `logs_destination = "log-analytics"`; and the service gives every Container Apps environment a `Consumption` workload profile, which Terraform declares and the container app targets.

## Run static checks only

For an unconfigured checkout, run from `infra/`:

```sh
terraform init -backend=false -input=false -lockfile=readonly
terraform fmt -check -recursive
terraform validate
```

If local deployment files exist, make an isolated temporary configuration directory. Copy **only reviewed Terraform source under `infra/` (including local modules, preserving relative paths) and `infra/.terraform.lock.hcl`**, using current working-tree contents. Start from tracked source paths and explicitly include new source files from the current change. Do not copy tfvars, state, overrides, saved plans, `.terraform/`, credentials or logs. Run the same commands there. Do not use `git archive HEAD` to validate uncommitted edits. Clean up only the temporary files you created.

Do not upgrade providers during unrelated maintenance; retain the lock file. An intentional provider update requires updating constraints and reviewing the lock change separately. Record its checksums for every supported platform with `terraform providers lock -platform=linux_amd64 -platform=linux_arm64 -platform=darwin_amd64 -platform=darwin_arm64 -platform=windows_amd64`; CI and Codespaces run on Linux. A static-check failure is not permission to use `plan`, `apply`, `destroy` or a live Fabric operation.

For changed Python, parse/compile source without executing notebook cells; Fabric supplies NotebookUtils, Spark and Delta. Exercise initializer behavior in a **temporary fixture repository**, not by creating deployment config in this template: valid inputs, a non-main branch, repeated execution, existing tfvars/state, invalid inputs and literal HCL escaping. Check the generated shape and owner-only permissions.

For the knowledge configuration helper, verify the two-field change, repeatability, preservation of unrelated settings, rejection of zero/logical/conflicting IDs and custom registrations, and no cloud calls. Validate deployment-skill request shapes against the documented APIs; do not execute its live command examples as a template test.

For the runtime preflight helper, run its standard-library tests with mocked native commands:
`python3 -B -m unittest discover -s .github/skills/runtime-integration/tests`.
Check template refusal, target/tenant mismatches, provider credential overrides,
the Foundry scope and regional resource-type checks, the Node, Git and Azure CLI
minimums, Fabric quota parsing (including a resize), the Work IQ prerequisite list
and credential-output redaction. Do not run
the helper's live probes as a template-maintenance check; those require a chosen
derived checkout and explicit permission.

For the deployment runner, its knowledge helper and its Foundry agent helper (`scripts/foundry_agent.py`), run their standard-library tests:
`python3 -B -m unittest discover -s .github/skills/deploy-platform/tests`, then
`python3 -B .github/skills/deploy-platform/scripts/foundry_agent.py validate`.
They use mocked native commands and HTTP. They cover saved-plan approval by hash,
identity-target and destructive-change guards, stale saved plans after an apply,
PAT-free native import and its identity/partial-result/resume guards, approved
definition updates, opt-in Git sync refusals, the explicit plan-attachment
fallback, notebook resume without duplicates, the SQL-endpoint binding check,
per-folder ingestion and retrieval evidence, the Web IQ key's plans, up-front
sign-in, admin-first consent (including the app's scope and Work IQ's), feature
registration, permission refusals mapped to grants, bounded retries, the agent
publication and its digests, the optional Work IQ tool, and the app's build, tag
and plan gates. Teardown tests must cover exact destroy-plan approval, purge
ordering, target isolation, partial failure/resume and retention reporting
without cloud calls. Do not run the runner's `up` or `down`
command as a template check; the runner refuses the template and its forks anyway. When changing a phase, keep it
check-then-act, resumable and gated: persist a receipt before polling any
accepted mutation, and update `docs/development.md`.

For the Fabric notebooks, run the offline binder and seeder tests:
`python3 -B -m unittest discover -s .github/skills/fabric-workspace/tests`.

For the app, run `npm ci`, `npm run lint`, `npm run build` and `npm test` from
`app/`. Where Docker is available, `docker build --tag app:check app` and a local
run with placeholder settings are fine; do not push images or start the app
against a deployed agent as a template check: those need a chosen target.

[CI](../../workflows/ci.yml) runs the Terraform, Python (including the agent definitions
and the Fabric notebook tests), app, app image and shell syntax checks on every pull request
with a read-only token and no secrets or cloud access. Contributions
arrive from forks, so keep it that way: never add `pull_request_target`, secrets or write
permissions. Keep its commands in step with this section and its Terraform version with the
devcontainer.
`app/package-lock.json` must resolve from `https://registry.npmjs.org/`; CI rejects private
mirror URLs, and `CONTRIBUTING.md` shows how to normalize them. Dependabot proposes npm
updates for `app/`, Docker base-image updates for `app/Dockerfile`, GitHub Actions and
dev container updates; review them like any dependency change.

For devcontainer changes, validate the configuration against the Dev Container schema and inspect feature options against their published definitions. If Docker is available, build/start an isolated checkout without local credentials/state, run `.devcontainer/check-tools.sh`, then stop/remove only that test container. A container check must not sign in, initialize Terraform, seed or deploy.

For official IQ plugin updates, inspect each pinned marketplace's actual plugin
name, payload and MCP declarations. Keep Python >= 3.12 for Foundry IQ. Verify
the Azure MCP overlay's expected upstream shape and both telemetry launchers;
do not silently leave `@latest` behind when claiming a pinned runtime. Installation
and startup checks must not launch MCP servers, accept terms or query work data.
Check all five required plugin registrations; do not infer a dedicated MCP server
from a skill-only package or duplicate plugin-owned servers in personal/project
MCP configuration.

## Keep the experience small

Keep always-applicable rules in `AGENTS.md`, task procedures in `.github/skills/<name>/SKILL.md`, and explanations in the canonical docs. Use skill metadata with a clear trigger; do not require developers to install the same skill globally or duplicate root instructions in App settings.

Keep base-image, Terraform and Copilot pins compatible with their documented requirements. Install the official Copilot release with mandatory checksum verification during image build; keep startup free of installs and cloud operations. Preserve Node >= 24.11.0 and Git >= 2.27.0. Check plugin registration without invoking an app skill or signing in. Keep the web app's React, Vite, MSAL, jose and OpenTelemetry dependencies in `app/package.json` and `app/package-lock.json`, not in global tools or the devcontainer. The image builds with ACR Tasks, so the devcontainer needs no container engine. Do not add Fabric CLI, fabric-cicd, local Spark or Azure deployment wrappers just to validate the template.

Keep Codespaces SSH private and managed through `gh codespace ssh`; do not publish its port, set passwords or disable host verification. App-driven deployment must route both commands and file changes to the same remote checkout/state.

Update README, getting started and the safe tfvars example when inputs or behavior change. Preserve the initializer's exact placeholder contract when editing the example. Do not bundle project-specific Terraform/deployment test suites or claim that static checks verify cloud permissions, preview availability, quota or ingestion.

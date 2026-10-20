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
| Optional Web IQ | `infra/web_iq.tf`, `preparation.md`, and the existing deployment runner/probe tests |
| Optional Work IQ knowledge | `infra/work_iq.tf`, the runner's Work IQ checks and tests, and getting started's Work IQ section |
| Workspace definitions or notebooks | The `fabric-workspace` skill and its linked guides |
| Developer tools or initialization | `.devcontainer/`, the `initialize-project` skill and `docs/development.md` |
| Guided deployment | The `deploy-platform` skill, its runner (`scripts/deploy.py`), its native-request reference and `cicd/README.md` |
| Studio agent or its helper | `agents/scenario-assistant/`, `.github/skills/deploy-platform/scripts/studio_agent.py`, `docs/runtime-integration.md` |
| App shell | `app/`, root `package.json`/`index.html`, `generated/`, `docs/runtime-integration.md` |
| Optional Microsoft 365 demo seeding skill | `.github/skills/m365-demo-seed/` (`SKILL.md`, `NOTICE.md`, `references/`, `scripts/`, `tests/`); its cloud setup is `infra/m365_demo.tf` (delegated-only app, opt-in `m365_demo_enabled`) and the runner's `m365-demo` phase with `scripts/configure_m365_demo.py` |

Preserve the non-obvious infrastructure constraints:

- Create capacity before looking up its Fabric GUID; workspaces cannot use its Azure resource ID.
- Keep the user-auth module in the platform's state. Its one-time targeted bootstrap is the only targeting exception; never run it during maintenance. Keep delegated scopes and pre-authorized clients separate from administrator-owned consent, and retain Fabric's dependency on the registration for safe teardown. Read shared Microsoft service principals rather than managing their lifecycle.
- Keep Git connection/initialization/sync outside Terraform: the pinned provider cannot attach a deployment plan. Keep GitHub integration, empty TEST, and administrator-owned tenant settings.
- Keep Web IQ disabled by default, with explicit paid-usage/terms acceptance. The pinned AzAPI provider lacks its embedded schema; retain the resource-scoped validation exception, not a provider-wide bypass. Portal AppID linking and a public-only API probe do not prove native Studio/app integration; use `preparation.md` for the manual gates.
- Keep Work IQ disabled by default, with explicit terms acceptance, in its own knowledge base: a retrieve without the user's Work IQ token fails, so the scenario knowledge base and the Studio agent must never depend on it. Keep its app secretless (federated credential for the Search identity), its consent administrator-owned, and its runner proof content-free.
- Use `Microsoft.CognitiveServices/accounts` projects, not ML hubs. Keep model versions/SKUs/quota configurable.
- Manage Search sources/bases through data-plane `restful_resource`, not unsupported ARM types. Retain CRUD and drift detection, source rebuild triggers, and knowledge-base-before-source deletion ordering. The OneLake source owns its generated search assets.
- Create a serverless Search service without a managed identity: its preview ends in `Failed` when one is requested and answers HTTP 500 to identity writes and to any change once an identity exists. Keep the runner's verified identity step, `ignore_changes = [identity]`, the identity grants gated until it exists (including `search_identity_ready` for the staged replacement plan), and replacement rather than update for serverless setting or tag changes. Read the admin key through the ephemeral action, never into state.
- Look up Git-owned items rather than creating duplicate lakehouses/notebooks in Terraform. Keep DEV-only Search permissions and conditional knowledge resources when the lakehouse ID is null.
- Keep deployment-pipeline replacement semantics visible when changing stages. Do not hide drift or paper over unsupported provider behavior.

## Run static checks only

For an unconfigured checkout, run from `infra/`:

```sh
terraform init -backend=false -input=false -lockfile=readonly
terraform fmt -check -recursive
terraform validate
```

If local deployment files exist, make an isolated temporary configuration directory. Copy **only reviewed Terraform source under `infra/` (including local modules, preserving relative paths) and `infra/.terraform.lock.hcl`**, using current working-tree contents. Start from tracked source paths and explicitly include new source files from the current change. Do not copy tfvars, state, overrides, saved plans, `.terraform/`, credentials or logs. Run the same commands there. Do not use `git archive HEAD` to validate uncommitted edits. Clean up only the temporary files you created.

Do not upgrade providers during unrelated maintenance; retain the lock file. An intentional provider update requires updating constraints and reviewing the lock change separately. A static-check failure is not permission to use `plan`, `apply`, `destroy` or a live Fabric operation.

For changed Python, parse/compile source without executing notebook cells; Fabric supplies NotebookUtils, Spark and Delta. Exercise initializer behavior in a **temporary fixture repository**, not by creating deployment config in this template: valid inputs, a non-main branch, repeated execution, existing tfvars/state, invalid inputs and literal HCL escaping. Check the generated shape and owner-only permissions.

For the knowledge configuration helper, verify the two-field change, repeatability, preservation of unrelated settings, rejection of zero/logical/conflicting IDs and custom registrations, and no cloud calls. Validate deployment-skill request shapes against the documented APIs; do not execute its live command examples as a template test.

For the runtime preflight helper, run its standard-library tests with mocked native commands:
`python3 -B -m unittest discover -s .github/skills/runtime-integration/tests`.
Check exact native `items` envelopes, denied/deprecated actions, target/tenant
mismatches, template refusal, CLI pins and credential-output redaction. Do not run
the helper's live probes as a template-maintenance check; those require a chosen
derived checkout and explicit permission.

For the deployment runner and Studio agent helper, run their standard-library tests:
`python3 -B -m unittest discover -s .github/skills/deploy-platform/tests`.
They use mocked native commands and HTTP. They cover saved-plan approval by hash,
identity-target and destructive-change guards, Git sync refusals, the explicit
plan-attachment fallback, notebook resume without duplicates, binding and
retrieval evidence, consent and the agent/app gates, the optional `m365-demo` phase
(plan scope, separate consent flag) and render the shipped agent
into a temporary workspace. Do not run the runner's `up` command or the helper's
`connections`, `configure`, `check` or `find` subcommands as a template check;
the runner refuses the template anyway. When changing a phase, keep it
check-then-act, resumable and gated: persist a receipt before polling any
accepted mutation, and update `docs/development.md`.

For the app, run `npm ci`, `npm run lint`, `npm run build` and `npm test` from the
repository root. Do not regenerate `generated/` or run `ms app init`/`dev` as a
template check; they need a chosen environment.

For the optional Microsoft 365 demo seeding skill, run its standard-library suite:
`python3 -B -m unittest discover -s .github/skills/m365-demo-seed/tests`.
It points the tool at a fake Entra and Microsoft Graph server on 127.0.0.1 (the tool
accepts endpoint overrides only for loopback), isolates its workspace in a temporary
folder and never signs in to a real tenant. Keep every tenant change recorded before it
is made and restored exactly, and keep passwords, tokens and one-time codes out of stdout,
logs and run files; tests assert both. Never run its sign-in, seeding, cleanup, restore or
Work IQ steps against a tenant as a template check.

For devcontainer changes, validate the configuration against the Dev Container schema and inspect feature options against their published definitions. If Docker is available, build/start an isolated checkout without local credentials/state, run `.devcontainer/check-tools.sh`, then stop/remove only that test container. A container check must not sign in, initialize Terraform, seed or deploy.

For official IQ plugin updates, inspect each pinned marketplace's actual plugin
name, payload and MCP declarations. Keep Python >= 3.12 for Foundry IQ. Verify
the Azure MCP overlay's expected upstream shape and both telemetry launchers;
do not silently leave `@latest` behind when claiming a pinned runtime. Installation
and startup checks must not launch MCP servers, accept terms or query work data.
Check all six required plugin registrations; do not infer a dedicated MCP server
from a skill-only package or duplicate plugin-owned servers in personal/project
MCP configuration.

## Keep the experience small

Keep always-applicable rules in `AGENTS.md`, task procedures in `.github/skills/<name>/SKILL.md`, and explanations in the canonical docs. Use skill metadata with a clear trigger; do not require developers to install the same skill globally or duplicate root instructions in App settings.

Keep base-image, Terraform and Copilot pins compatible with their documented requirements. Install the official Copilot release with mandatory checksum verification during image build; keep startup free of installs and cloud operations. The local Managed Runtime feature must run after Node, install its CLI/plugin for the remote user, and verify the official GCM package checksum. Preserve Node >= 24.11.0, Git >= 2.27.0, native build/libsecret support and headless in-memory Git credential caching; do not replace it with plaintext storage. Check plugin registration without invoking an app skill or signing in. Keep React/Vite/SDK dependencies in the `app` workspace manifest and the root lockfile, not in global tools. Keep the .NET 10 feature and the pinned Power Platform CLI tool manifest; startup restores that tool but never signs in to it. Do not add Fabric CLI, fabric-cicd, local Spark or Azure deployment wrappers just to validate the template.

Keep Codespaces SSH private and managed through `gh codespace ssh`; do not publish its port, set passwords or disable host verification. App-driven deployment must route both commands and file changes to the same remote checkout/state.

Update README, getting started and the safe tfvars example when inputs or behavior change. Preserve the initializer's exact placeholder contract when editing the example. Do not bundle project-specific Terraform/deployment test suites or claim that static checks verify cloud permissions, preview availability, quota or ingestion.

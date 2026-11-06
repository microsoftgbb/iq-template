# Repository guidance

This repo is derived from a template located in https://github.com/microsoftgbb/iq-template. It owns its infrastructure, state, tests and CI/CD. Do not add compatibility code for older deployments.

## Start here

- [Getting started](docs/getting-started.md) is the canonical deployment guide; [developer setup](docs/development.md) covers Codespaces and Copilot App/CLI.
- `infra/` owns infrastructure, permissions and knowledge registration. Use plain Terraform and one ignored local tfvars file.
- `workspace/` owns Fabric definitions and bootstrap notebooks, not infrastructure, credentials or data backups.
- `agents/scenario-assistant/` owns the Microsoft Foundry hosted agent (code, instructions, image and toolbox definition) and `app/` the Azure Container Apps web app (browser app and its proxy server). The runner builds and publishes both; Terraform owns their registry, hosting environment, identities and sign-in registration.
- [Official Copilot tooling](docs/copilot-tooling.md) covers the pinned Azure/Foundry IQ, Work IQ/M365 Agents Toolkit and Fabric packages. Product skills supplement this repository's ownership and approval rules; authoring MCP servers are not runtime agent/app connections.
- [Scenario authoring](docs/scenario-authoring.md) fixes domain, knowledge, agent, app and evaluation locations and the complete native work-package inventory. Do not improvise a competing project layout or backend.
- [`cicd/`](cicd/README.md) owns native Git/bootstrap/promotion guidance. [Ontology knowledge](docs/ontology-knowledge.md) explains binding and delegated retrieval.

Load the matching repository skill before its workflow:

| Task | Skill |
| --- | --- |
| "Deploy the platform" or resume complete DEV setup, agent and app | [deploy-platform](.github/skills/deploy-platform/SKILL.md) |
| If this agent is running directly on the template or a fork of it, recommend configuring a new repository from this template and do not proceed with infra deployment, unless the user is developing the template itself | [initialize-project](.github/skills/initialize-project/SKILL.md) |
| Change or statically validate the template/toolchain | [template-maintenance](.github/skills/template-maintenance/SKILL.md) |
| Edit Fabric items, inspect bindings, bootstrap or promote | [fabric-workspace](.github/skills/fabric-workspace/SKILL.md) |
| Preflight the hosted agent and web app route, prove shell readiness or diagnose a blocked route | [runtime-integration](.github/skills/runtime-integration/SKILL.md) |
| Add a domain workflow, ontology/seed work package or explicit IQ opt-in | [scenario-authoring](.github/skills/scenario-authoring/SKILL.md) |

## Always-on guardrails

- Never run live `plan`, `apply`, `destroy`, notebook execution or deployment tests as a template-maintenance check. Live work requires a derived repository and an explicitly chosen target.
- Route "deploy the platform" to the complete DEV workflow, not just infrastructure, through the [deterministic runner](docs/development.md#deploy-deterministically). Use one confirmed execution host/checkout/backend throughout; do not create duplicate state in an App worktree. Its `signin` phase completes every CLI sign-in, including the Microsoft Foundry token the agent and app phases use, before anything is created; `--through verify` defers the agent and app. Require plan and release approvals and distinguish deployed resources from verified readiness.
- Do not delete cloud assets, local state or ignored configuration without explicit permission. Preserve unrelated user changes and staging.
- Never commit or print credentials, real tfvars, `.env`, state, saved plans or debug logs. Keep PATs ephemeral/write-only; rotate with `github_token_version`. Provider-returned secrets can remain in state: require secured remote state before team/CI use.
- Use `project_name` for resource branding; require an explicit region. Discover subscription/authentication through provider-supported CLI/environment conventions. Never bake a repository owner, tenant, subscription or physical Fabric ID into shared definitions.
- Preserve `.platform` logical IDs, TMDL lineage tags and binding markers: these are portable model identities, **not** deployment IDs. Resolve physical bindings in the current Fabric workspace. Keep the template ontology unbound; review generated DEV bindings only in derived repositories.
- Keep setup staged in one state: up-front CLI sign-in -> Entra bootstrap -> tenant-wide consent and the scoped sign-in -> Terraform platform -> native item import with an explicitly attached plan -> Terraform knowledge using physical DEV item GUIDs. Fabric Git sync is opt-in (`fabric_git_enabled = true`); only that path requires a PAT. The hosted agent, its toolbox and the Container App follow DEV readiness, are built only from the published, clean source, and live outside Terraform state. Only the initial identity plan may target `module.fabric_user_auth`; all later platform/knowledge/destroy plans are untargeted. Preserve Fabric's identity-module dependencies so its login registration survives until Fabric teardown finishes.
- Declare delegated permissions and authorized clients. The deployer is assumed to administer every target: the runner grants tenant-wide consent for exactly the declared scopes and registers the subscription features the configuration needs as that user. Only when an API refuses for lack of permission does it stop with the exact grant an administrator must give; `--manual-grants` never attempts them. Never widen scopes or roles, self-assign directory privileges or change tenant settings to get past a refusal. Do not create credentials or grant directory-management permissions to the deployment app. No Terraform Git/seeding provisioners. The deterministic runner (`.github/skills/deploy-platform/scripts/deploy.py`) is the only deployment automation: it orchestrates native tools with the same gates. Extend it instead of adding another wrapper, and never bypass its approvals. TEST starts empty; tenant settings and PROD/approval gates are not template-owned.
- Preserve insert-only seeding, schema checks, current-workspace scope and explicit opt-in outside DEV/TEST. Preserve binding identity checks, unrelated definition parts, bounded polling and failure propagation. Serialize authoring/deployment; rebind every promoted target.
- Keep Search knowledge objects in data-plane `restful_resource` resources, with CRUD/drift detection and source rebuild/deletion ordering. Leave the source/base/MCP connection disabled when `knowledge_lakehouse_id` is null.
- Use native `fabricOntology` sources for live ontology queries. They require an end-user Search-audience token in `x-ms-query-source-authorization`; managed identities do not replace it. OneLake indexing's Search identity needs Contributor on DEV only, not a read-only lakehouse grant or access to TEST/PROD.
- Surface failures and documented preview/cost/public-networking limitations. A successful seed, deployment or registration is not proof of indexing, binding, graph or query readiness.
- The web app calls only the hosted agent's Responses endpoint, through its own transport-only proxy, with the signed-in user's Foundry token. Never give the app or its proxy a credential, shared token, API key or any Foundry, Search or Fabric role (its identity only pulls its image), and never widen the proxy beyond that one endpoint and its narrowed request. Keep the Fabric IQ tool on its `UserEntraToken` connection so ontology queries run as the user, and keep the knowledge-base tool limited to document retrieval. Never widen roles, scopes, connection sharing or app access to pass a check. Keep the agent's message-content capture off (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false`) and app telemetry free of headers, tokens and bodies, so traces never widen who can read user data. Never commit a rendered agent or toolbox definition, or any file with physical IDs, tokens or deployment receipts.
- Prove consuming apps with end-user authorization and isolation. The runner's fresh-challenge and tool evidence covers only the deploying user; healthy containers, cached sign-in and the current Fabric/knowledge DEV-ready report are not application-shell readiness. Granting other users access is an operator step, not a template default. Never silently add a backend, change tenant policy, publish private Codespaces ports or recreate a partially deployed agent or app to bypass a blocked integration; see [runtime troubleshooting](docs/runtime-integration.md#troubleshooting).

## Conventions and learning

Use snake_case Terraform labels, typed/documented inputs and outputs, explicit API versions and configurable model versions/SKUs/quota. Prefer native resources, keep `.terraform.lock.hcl`, and limit drift exclusions to documented service behavior.

Keep reusable learning at the narrowest useful scope: cross-task safety rules here, repeatable procedures in the matching skill, and product explanations in canonical docs. Correct the existing source instead of appending a session diary or duplicating instructions. Do not persist deployment IDs, credentials, personal details or unverified assumptions as guidance.

Keep README, getting started and the sanitized tfvars example aligned. Static Terraform checks are `init -backend=false`, `fmt -check -recursive`, and `validate`; use an isolated configuration copy when local deployment files exist. The maintenance skill explains safe checks for tooling and notebooks. Do not bundle project-specific deployment tests.

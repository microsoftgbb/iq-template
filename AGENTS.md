# Repository guidance

This is a **clean-slate GitHub template**, not a deployed project. Derived repositories own their infrastructure, state, tests and CI/CD. Do not add compatibility code for older deployments.

## Start here

- [Getting started](docs/getting-started.md) is the canonical deployment guide; [developer setup](docs/development.md) covers Codespaces and Copilot App/CLI.
- `infra/` owns infrastructure, permissions and knowledge registration. Use plain Terraform and one ignored local tfvars file.
- `workspace/` owns Fabric definitions and bootstrap notebooks, not infrastructure, credentials or data backups.
- `agents/scenario-assistant/` owns the Copilot Studio agent definition and `app/` the Managed Runtime app. The repository root is the native app root (`package.json`, `index.html`, `ms.config.json`, `generated/`).
- [Official Copilot tooling](docs/copilot-tooling.md) covers the pinned Azure/Foundry IQ, Work IQ/M365 Agents Toolkit, Fabric and Managed Runtime packages. Product skills supplement this repository's ownership and approval rules; authoring MCP servers are not runtime agent/app connections.
- [Scenario authoring](docs/scenario-authoring.md) fixes domain, knowledge, agent, app and evaluation locations and the complete native work-package inventory. Do not improvise a competing project layout or backend.
- [`cicd/`](cicd/README.md) owns native Git/bootstrap/promotion guidance. [Ontology knowledge](docs/ontology-knowledge.md) explains binding and delegated retrieval.

Load the matching repository skill before its workflow:

| Task | Skill |
| --- | --- |
| "Deploy the platform" or resume complete DEV setup, agent and app | [deploy-platform](.github/skills/deploy-platform/SKILL.md) |
| Configure a new repository from this template | [initialize-project](.github/skills/initialize-project/SKILL.md) |
| Change or statically validate the template/toolchain | [template-maintenance](.github/skills/template-maintenance/SKILL.md) |
| Edit Fabric items, inspect bindings, bootstrap or promote | [fabric-workspace](.github/skills/fabric-workspace/SKILL.md) |
| Preflight the agent/app route, prove shell readiness or re-validate a blocked route | [runtime-integration](.github/skills/runtime-integration/SKILL.md) |
| Add a domain workflow, ontology/seed work package or explicit IQ opt-in | [scenario-authoring](.github/skills/scenario-authoring/SKILL.md) |

## Always-on guardrails

- Never run live `plan`, `apply`, `destroy`, notebook execution or deployment tests as a template-maintenance check. Live work requires a derived repository and an explicitly chosen target.
- Route "deploy the platform" to the complete DEV workflow, not just infrastructure, through the [deterministic runner](docs/development.md#deploy-deterministically). Use one confirmed execution host/checkout/backend throughout; do not create duplicate state in an App worktree. Require plan approvals and distinguish deployed resources from verified readiness.
- Do not delete cloud assets, local state or ignored configuration without explicit permission. Preserve unrelated user changes and staging.
- Never commit or print credentials, real tfvars, `.env`, state, saved plans or debug logs. Keep PATs ephemeral/write-only; rotate with `github_token_version`. Provider-returned secrets can remain in state: require secured remote state before team/CI use.
- Use `project_name` for resource branding; require an explicit region. Discover subscription/authentication through provider-supported CLI/environment conventions. Never bake a repository owner, tenant, subscription or physical Fabric ID into shared definitions.
- Preserve `.platform` logical IDs, TMDL lineage tags and binding markers: these are portable model identities, **not** deployment IDs. Resolve physical bindings in the current Fabric workspace. Keep the template ontology unbound; review generated DEV bindings only in derived repositories.
- Keep setup staged in one state: Entra bootstrap -> administrator consent/user sign-in -> Terraform platform -> native Git sync with an explicitly attached plan -> Terraform knowledge using physical DEV item GUIDs. The Studio agent and app follow DEV readiness and live outside Terraform state. Only the initial identity plan may target `module.fabric_user_auth`; all later platform/knowledge/destroy plans are untargeted. Preserve Fabric's identity-module dependencies so its login registration survives until Fabric teardown finishes.
- Declare delegated permissions and authorized clients. Tenant-wide consent is an administrator's explicit action: the portal, or the runner's `--grant-admin-consent` run by that administrator for exactly the declared scopes. Do not create credentials or grant directory-management permissions to the deployment app. No Terraform Git/seeding provisioners. The deterministic runner (`.github/skills/deploy-platform/scripts/deploy.py`) is the only deployment automation: it orchestrates native tools with the same gates. Extend it instead of adding another wrapper, and never bypass its approvals. TEST starts empty; tenant settings and PROD/approval gates are not template-owned.
- Preserve insert-only seeding, schema checks, current-workspace scope and explicit opt-in outside DEV/TEST. Preserve binding identity checks, unrelated definition parts, bounded polling and failure propagation. Serialize authoring/deployment; rebind every promoted target.
- Keep Search knowledge objects in data-plane `restful_resource` resources, with CRUD/drift detection and source rebuild/deletion ordering. Leave the source/base/MCP connection disabled when `knowledge_lakehouse_id` is null.
- Use native `fabricOntology` sources for live ontology queries. They require an end-user Search-audience token in `x-ms-query-source-authorization`; managed identities do not replace it. OneLake indexing's Search identity needs Contributor on DEV only, not a read-only lakehouse grant or access to TEST/PROD.
- Surface failures and documented preview/cost/public-networking limitations. A successful seed, deployment or registration is not proof of indexing, binding, graph or query readiness.
- The app calls only the published standard-harness Studio agent through the generated Copilot Studio service. Keep its IQ tools in `Invoker` mode with **Agent readers** access and grounding off; never use `Maker` mode or widen access to pass a check. Never edit `generated/` or commit rendered PAC workspaces or `.mcs/`.
- Prove consuming apps through native generated connector services with end-user authorization and isolation. A reviewed transport-only adapter may use their generated metadata through the public SDK; it must preserve connections, operations and governance, never mutate generated files or expose credentials. Allowed catalogs, cached sign-in and the current Fabric/knowledge DEV-ready report are not application-shell readiness. Never silently add a backend, change tenant policy or recreate a partially registered app to bypass a blocked integration.

## Conventions and learning

Use snake_case Terraform labels, typed/documented inputs and outputs, explicit API versions and configurable model versions/SKUs/quota. Prefer native resources, keep `.terraform.lock.hcl`, and limit drift exclusions to documented service behavior.

Keep reusable learning at the narrowest useful scope: cross-task safety rules here, repeatable procedures in the matching skill, and product explanations in canonical docs. Correct the existing source instead of appending a session diary or duplicating instructions. Do not persist deployment IDs, credentials, personal details or unverified assumptions as guidance.

Keep README, getting started and the sanitized tfvars example aligned. Static Terraform checks are `init -backend=false`, `fmt -check -recursive`, and `validate`; use an isolated configuration copy when local deployment files exist. The maintenance skill explains safe checks for tooling and notebooks. Do not bundle project-specific deployment tests.

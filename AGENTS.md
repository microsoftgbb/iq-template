# Repository guidance

## Scope and layout

This is a **clean-slate GitHub repository template**, not a deployed project. Derived repositories own their infrastructure, state, tests, and CI/CD. Do not add compatibility code for deployments predating this template.

- `infra/main.tf`: shared naming, tags, resource group.
- `infra/fabric.tf`: new capacity, DEV/TEST workspaces, GitHub credentials connection, native DEV/TEST deployment pipeline.
- `infra/foundry.tf`: Foundry account/project, model deployments, project connections.
- `infra/knowledge.tf`: Git-owned DEV lakehouse lookup, Search, permissions, conditional knowledge-source/base lifecycle.
- `infra/versions.tf`, `providers.tf`, `variables.tf`, `outputs.tf`: provider constraints, authentication, inputs, public outputs.
- `workspace/`: Fabric Git item definitions, including a schema-enabled lakehouse, insert-only synthetic seeder notebook, and deployment plan; no infrastructure or credentials.
- `cicd/`: native Git bootstrap, deployment-plan and deployment-pipeline guidance; no deployment wrapper or fabric-cicd dependency.
- `docs/getting-started.md`: the canonical, concise deployment guide.

## Conventions

- Keep infrastructure setup to plain Terraform and one local tfvars file. Use staged setup: platform apply, native Git sync with a deployment plan, then knowledge apply with the physical DEV lakehouse GUID. Keep promotion guidance in `cicd/`, using native tooling rather than custom wrappers. Do not bundle project-specific tests.
- Use required `project_name` for project branding, resource names, and tags. Never hard-code a template repository's name, owner, region, tenant, or subscription into a derived deployment.
- Use snake_case Terraform labels and typed, documented variables/outputs. Validate input constraints, format with `terraform fmt`, and retain the lock file.
- Discover subscription/authentication through provider-supported CLI/environment conventions. Require an explicit region; keep credentials out of configuration.
- Provision the capacity before looking up its Fabric GUID. Workspaces take the GUID, **not** the Azure resource ID.
- Use GitHub for DEV Git integration; no Azure DevOps integration. Git connection/initialization/sync stays outside Terraform because the pinned provider cannot attach a deployment plan. TEST starts empty. Tenant-wide Fabric settings remain administrator-owned prerequisites.
- Promote the same reviewed `workspace/` revision to TEST then PROD using native deployment pipelines with explicit plan attachment. These copy live workspace definitions, not Git checkouts or data; require synchronized, unchanged source workspaces. PROD provisioning and automated approval gates belong to derived projects.
- Do not automate orphan deletion or place secrets in item definitions/plan parameters. Review Git deletions explicitly. Never run live item deployment as a template check.
- Use native Terraform resources before scripts/provisioners, explicit API versions, and configurable model versions/SKUs/quota. Foundry uses `Microsoft.CognitiveServices/accounts` projects, not ML hubs.
- Search knowledge bases/sources are **data-plane** objects managed by `restful_resource`, not unsupported AzAPI/ARM types. Preserve CRUD and drift detection.
- The indexed OneLake source owns its generated index/indexer/skillset/data source. Preserve source-rebuild behavior and knowledge-base-before-source deletion ordering.
- Do not use Terraform provisioners for seeding or Git operations. Git owns the lakehouse/notebook; Terraform only looks up the supplied DEV lakehouse GUID. Keep the source, base and MCP connection disabled when that input is null.
- The notebook seeds only missing demo keys/files, using current-workspace identity and GUID-based OneLake paths. Preserve existing data, reject incompatible schemas, and surface failures. Require explicit opt-in outside DEV/TEST. This is bootstrap, not file drift management or a generic production ingestion pipeline.
- Deployment plans order item groups and supported actions; attach one per operation and poll for completion. Do not imply SQL discovery, graph materialization or semantic-model refresh completes just because seeding did.
- OneLake indexing currently requires Search's identity to have Contributor on DEV. Do not imply it is a read-only lakehouse grant; do not broaden it to TEST/PROD.
- Surface errors. Limit drift exclusions to documented server behavior; do not hide changes or imply unimplemented features exist.

## Security and execution

- Never run live `plan`, `apply`, `destroy`, or deployment tests as a template-maintenance check. Deployment/testing belongs in a derived repository with an explicitly chosen target.
- Do not delete cloud assets, local state, or ignored configuration without explicit permission. Preserve unrelated user changes and staging.
- Keep PATs ephemeral/write-only and rotate with `github_token_version`. Never commit real tfvars, `.env`, state, saved plans, keys, or debug logs; never print credentials.
- Use the notebook's authorized Fabric runtime for OneLake bootstrap and scoped managed identities for Search/Foundry access. The Search bootstrap key and other provider-returned secrets can remain in state; derived projects need secured remote state before team/CI use.
- Document cost, preview APIs, public networking, and asynchronous ingestion honestly. This is a development baseline, not a production security guarantee.

## Verification and documentation

Template maintenance uses static checks only, from `infra/`:

```sh
terraform init -backend=false
terraform fmt -check -recursive
terraform validate
```

Use an isolated configuration copy if local deployment files are present; do not read or modify their secrets/state. No bundled Terraform test suite is required. Derived projects should add tests and deployment verification appropriate to their workloads.

Keep the README, getting-started guide, and safe tfvars example aligned with inputs, permissions, and behavior. Prefer removing unnecessary steps over adding automation that hides deployment choices.

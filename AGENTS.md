# Repository guidance

## Scope and layout

This is a **clean-slate GitHub repository template**, not a deployed project. Derived repositories own their infrastructure, state, tests, and CI/CD. Do not add compatibility code for deployments predating this template.

- `infra/main.tf`: shared naming, tags, resource group.
- `infra/fabric.tf`: new capacity, DEV/TEST workspaces, DEV GitHub integration.
- `infra/foundry.tf`: Foundry account/project, model deployments, project connections.
- `infra/knowledge.tf`: Blob, Search, scoped RBAC, knowledge-source/base lifecycle.
- `infra/versions.tf`, `providers.tf`, `variables.tf`, `outputs.tf`: provider constraints, authentication, inputs, public outputs.
- `infra/seed/`: generic, non-sensitive starter content uploaded to Blob.
- `workspace/`: Fabric Git item definitions only; no infrastructure or credentials.
- `cicd/`: optional fabric-cicd configuration, pinned CLI dependencies, and item-promotion guidance.
- `docs/getting-started.md`: the canonical, concise deployment guide.

## Conventions

- Keep infrastructure setup to plain Terraform and one local tfvars file. Keep optional item promotion in `cicd/`, using upstream tooling rather than custom wrappers. Do not bundle project-specific tests.
- Use required `project_name` for project branding, resource names, and tags. Never hard-code a template repository's name, owner, region, tenant, or subscription into a derived deployment.
- Use snake_case Terraform labels and typed, documented variables/outputs. Validate input constraints, format with `terraform fmt`, and retain the lock file.
- Discover subscription/authentication through provider-supported CLI/environment conventions. Require an explicit region; keep credentials out of configuration.
- Provision the capacity before looking up its Fabric GUID. Workspaces take the GUID, **not** the Azure resource ID.
- Use GitHub for DEV Git integration; no Azure DevOps integration. TEST starts empty. Tenant-wide Fabric settings remain administrator-owned prerequisites.
- Promote the same reviewed `workspace/` commit to TEST then PROD. fabric-cicd deploys definitions, not underlying data; PROD provisioning and automated approval gates belong to derived projects.
- Preserve `unpublish.skip: true` in `cicd/config.yml` unless explicitly asked to enable deletions. Never run `fab deploy` as a template check or place secrets in promotion/parameter YAML.
- Use native Terraform resources before scripts/provisioners, explicit API versions, and configurable model versions/SKUs/quota. Foundry uses `Microsoft.CognitiveServices/accounts` projects, not ML hubs.
- Search knowledge bases/sources are **data-plane** objects managed by `restful_resource`, not unsupported AzAPI/ARM types. Preserve CRUD and drift detection.
- The Blob source owns its generated index/indexer/skillset/data source. Preserve source-rebuild behavior, write-only connection handling, and knowledge-base-before-source deletion ordering.
- Surface errors. Limit drift exclusions to documented server behavior; do not hide changes or imply unimplemented features exist.

## Security and execution

- Never run live `plan`, `apply`, `destroy`, or deployment tests as a template-maintenance check. Deployment/testing belongs in a derived repository with an explicitly chosen target.
- Do not delete cloud assets, local state, or ignored configuration without explicit permission. Preserve unrelated user changes and staging.
- Keep PATs ephemeral/write-only and rotate with `github_token_version`. Never commit real tfvars, `.env`, state, saved plans, keys, or debug logs; never print credentials.
- Use scoped managed identities at runtime. Bootstrap Search/storage keys remain in state; derived projects need secured remote state before team/CI use.
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

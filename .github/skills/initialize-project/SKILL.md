---
name: initialize-project
description: Configure a new repository created from the Fabric and Foundry template. Use for first-time onboarding, filling Terraform placeholders, choosing the GitHub bootstrap branch, or checking which workspace IDs and bindings need customization. Creates local configuration only; does not deploy.
---

# Initialize a derived project

Read `AGENTS.md`, `docs/getting-started.md` and `infra/terraform.tfvars.example` from the repository root. Use the installed toolchain described in `docs/development.md`.

If the user requested deployment rather than configuration only, let `deploy-platform` coordinate the full flow. Its runner calls this helper on the chosen execution host when given `--project-name`, `--location` and `--capacity-admin`; do not treat generating tfvars as completing deployment. Reuse confirmed inputs instead of asking again.

## Resolve the inputs

1. Confirm this is the user's **new derived repository**, not the template or an existing deployment. Inspect Git status and filenames only for existing tfvars/state; do not read secrets or overwrite local configuration. If already configured, stop first-time initialization and ask what needs changing.
2. Resolve the intended GitHub repository, not the template parent or an upstream remote. Use `gh repo view --json nameWithOwner,defaultBranchRef`, specifying `--repo OWNER/REPO` when the checkout/CLI repository selection is ambiguous. Confirm the result only if the user has not already chosen that target. If authentication/discovery fails, ask for the missing `OWNER/REPO` or existing bootstrap branch; never guess.
3. Use the repository's **published default branch**, unless the user explicitly chooses another published branch. Do not use the current Copilot worktree/feature branch automatically. Verify an explicitly chosen branch on GitHub.
4. Ask only for missing choices: a stable 3-16 character `project_name`, an explicit Azure region, and the Fabric capacity administrator UPN(s) or service-principal object ID(s). Do not silently truncate a repository name into a project name, infer region/quota, or substitute an application/client ID for an object ID.
5. Keep subscription selection in Azure CLI/standard provider environment variables, not tfvars. Do not ask for a PAT or access token in chat. Explain the permission/quota prerequisites and the separate interactive sign-ins in getting started.

## Generate local configuration

Run from the repository root, substituting the confirmed values:

```sh
python3 .github/skills/initialize-project/scripts/initialize_project.py \
  --project-name "<project-name>" \
  --location "<azure-region>" \
  --capacity-admin "<user-upn-or-service-principal-object-id>" \
  --github-repository "<owner>/<new-repository>" \
  --github-branch "<published-bootstrap-branch>"
```

Repeat `--capacity-admin` for additional admins. Use `--dry-run` to validate without writing. The Python standard-library helper validates syntax, fills the sanitized example, and creates only `infra/terraform.tfvars`, with owner-only permissions. It refuses existing local tfvars/state and never overwrites a file. It does not verify cloud access, region availability or quota.

Confirm the generated file is ignored with `git check-ignore infra/terraform.tfvars`; do not print its contents. Leave `fabric_git_enabled = false`, `knowledge_lakehouse_id = null` and `knowledge_ontologies = {}`. Native bootstrap needs no PAT; enable Fabric Git synchronization and request its PAT only after an explicit opt-in. Do not initialize a backend, run a plan/apply, connect Fabric Git, commit or push as a side effect of configuration.

## Preserve portable workspace identities

Do **not** regenerate UUIDs or replace every occurrence of `Scenario`:

- `.platform` logical IDs and deployment-plan references identify the same items across environments; keep them synchronized and stable.
- TMDL lineage tags, `ScenarioLakehouse`, `site-asset-v1`, and the binder's sample logical ID form the sample binding contract.
- `Scenario` / `ScenarioOntology` are workspace-local display names, not the template's project name. Keep the defaults unless the user requests a coordinated model rename; use the `fabric-workspace` skill for that change.
- The template tables have no partitions or physical bindings. `SeedScenario` and `BindScenario` resolve the current workspace/lakehouse/ontology and SQL metadata **at runtime in Fabric**, without a saved default lakehouse attachment.

If a purported fresh template contains exported physical bindings or notebook attachments, stop and review their ownership using `docs/ontology-knowledge.md`. Do not delete unknown partitions or perform global GUID replacement. In a deployed derived repository, reviewed DEV binding exports are expected, and every promoted target must be rebound.

Finish by pointing to the Entra bootstrap, administrator consent and platform stages in `docs/getting-started.md`. Configuration-only initialization does not create the app or grant consent. After a separately authorized Fabric bootstrap, use the actual physical DEV GUIDs printed by the notebooks for the knowledge stage in the **same** tfvars file; never substitute logical IDs or invent placeholders as deployment values.

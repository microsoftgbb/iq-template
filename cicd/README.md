# Native Fabric bootstrap and promotion

Use [Git integration](https://learn.microsoft.com/fabric/cicd/git-integration/intro-to-git-integration) for DEV and [native deployment pipelines](https://learn.microsoft.com/fabric/cicd/deployment-pipelines/intro-to-deployment-pipelines) for promotion. Attach a [deployment plan (preview)](https://learn.microsoft.com/fabric/cicd/deployment-plan/deployment-plan-overview) when item deployment must run seed notebooks or follow an explicit order.

```text
Terraform platform -> Git /workspace + Bootstrap -> seeded DEV -> Terraform knowledge
                                      reviewed DEV -> TEST -> approved TEST -> PROD
```

There is no fabric-cicd dependency, `fab deploy` configuration, or custom deployment wrapper. Start with [getting started](../docs/getting-started.md); infrastructure still uses plain Terraform and one local tfvars file. Derived repositories own unattended automation, tests and approval gates.

## Ownership and included items

Terraform owns capacity, workspaces, permissions, the GitHub credentials connection, the DEV/TEST deployment pipeline, Foundry and Search. Git owns workspace item definitions. The seeder owns only the insertion of missing demo keys and initial starter content. Do not manage the same lakehouse or notebook with both Terraform and Git.

| Git item | Purpose |
| --- | --- |
| [`Scenario.Lakehouse`](../workspace/Scenario.Lakehouse) | Schema-enabled lakehouse with default schema `dbo`; Git does not contain its data. |
| [`SeedScenario.Notebook`](../workspace/SeedScenario.Notebook/notebook-content.py) | Synthetic sites/assets and a starter document, embedded in code so they travel through Git and native promotion. |
| [`Bootstrap.DeploymentPlan`](../workspace/Bootstrap.DeploymentPlan/plan.yml) | Deploy lakehouse, deploy notebook, then run that notebook as a post-action. |

The generic item names are local to each project's workspaces. Preserve each `.platform` logical ID across environments. If you rename `Scenario`, update the notebook parameter and plan action together. The plan uses logical IDs, while Terraform's `knowledge_lakehouse_id` input requires the physical lakehouse GUID in DEV.

The seeder uses the current runtime workspace, never a saved DEV workspace ID or default lakehouse attachment. It writes `Tables/dbo/demo_sites`, `Tables/dbo/demo_assets` and `Files/starter.txt`. Existing rows (including edited demo rows) and the existing file are retained. Incompatible table schemas/non-Delta paths and write failures stop the run; no data is silently replaced. Serialize seed runs per workspace.

## Initial Git synchronization

After the first Terraform apply, use **Connect and sync with deployment plan** in DEV and select the incoming `Bootstrap` plan. Connect to the configured GitHub branch and `/workspace` directory. Include all three starter items. The seeder is itself a deployment group, so it exists before its post-action runs even in an empty workspace.

**Committing a plan doesn't activate it.** Attach it to each initial sync, update, branch operation or pipeline deployment that needs its actions. A Git push alone doesn't synchronize Fabric or run a notebook.

Git connection initialization is intentionally outside Terraform: the pinned Fabric provider's `fabric_workspace_git` resource automatically initializes/syncs but cannot attach a plan. Do not add it alongside the native bootstrap operation.

### Automating the same operation

Use the documented [Git automation APIs](https://learn.microsoft.com/fabric/cicd/git-integration/git-automation) and [deployment-plan API extension](https://learn.microsoft.com/fabric/cicd/deployment-plan/deployment-plan-automation), not another Terraform provisioner:

1. Authenticate to Fabric with Entra. The caller needs workspace permissions, the original API scopes, and `Item.Execute.All` when attaching a plan. Service-principal/managed-identity use also requires tenant settings and support from every selected item type.
2. Read `terraform -chdir=infra output -json github_connect_request` and submit that body to `POST /v1/workspaces/{devWorkspaceId}/git/connect`. It contains the repository, branch, directory and credentials connection ID, not the PAT.
3. Call `POST /v1/workspaces/{devWorkspaceId}/git/initializeConnection` with `{"initializationStrategy":"PreferRemote"}`. Follow the returned required action. Initialize Connection itself does not accept a plan.
4. When an update is required, call `POST /v1/workspaces/{devWorkspaceId}/git/updateFromGit?beta=true` with the returned workspace head, the reviewed remote commit hash and the plan option below. For later updates, read current Git status first. Stop on conflicts rather than unconditionally overriding workspace changes.
5. Poll asynchronous operations to their terminal result, respecting `Retry-After`, and propagate item/action failures. A `202` response is not completion. Stop the workflow on failure; Fabric doesn't roll back earlier successful groups.

The plan option belongs inside the **existing request's `options`** object:

```json
{
  "options": {
    "deploymentPlan": {
      "logicalId": "17fca947-7a9f-4822-91ac-cd6aa8256e75",
      "referenceType": "ByLogicalId"
    }
  }
}
```

This is only the options fragment, not a full Update From Git request. Keep its required commit/head fields. The logical ID comes from [`Bootstrap.DeploymentPlan/.platform`](../workspace/Bootstrap.DeploymentPlan/.platform). Do not replace it with a physical item ID for Git updates.

A no-op Git update is not a reliable reseed trigger. Run the notebook separately when data needs bootstrapping again.

## Running only the seeder

Open `SeedScenario` and run all cells, or invoke it through the [notebook Job Scheduler APIs](https://learn.microsoft.com/fabric/data-engineering/notebook-public-api). Resolve the notebook's physical item ID in the target workspace; poll the job to completion and surface its failure reason. This path doesn't require a new commit or a deployment plan.

The parameter defaults target `Scenario` and allow only workspace names ending in `-dev` or `-test`. This is a demo-data guard, not an authorization boundary. For a deliberate exception, supply the Boolean `allow_other_workspaces=true` for that run; never a truthy string. The included plan explicitly passes `false`.

Seed code is bootstrap, not continuous data synchronization: changing an existing fixture in Git doesn't overwrite the corresponding existing row or file. Add reviewed data migrations/ingestion to your derived project when updates are needed.

## Adding ontologies, graphs and semantic models

The template supplies their starter data and deployment ordering, **not prebuilt ontology, graph or semantic-model definitions**. Author these items in DEV against the seeded lakehouse, then commit their real Fabric-exported definitions and `.platform` files through Git.

The sample relationship is `demo_assets.site_id -> demo_sites.site_id`. It can back Site/Asset ontology entities, graph nodes and located-at edges, or a semantic model counting assets by site/status.

Add each new item as a deployment group in `Bootstrap` (or an appropriately named release plan) and make it depend on the completed `SeedScenario` group. Use its exported logical ID. A group depends on the whole prerequisite group, including its post-actions; file order alone is not ordering. Commit the changed plan with the items it references and select those items in the deployment operation.

| Item | Additional work beyond deploying its definition |
| --- | --- |
| Ontology (preview) | Bind entities/properties/relationships to the target environment's tables or semantic model; review exported source references. If generated from a semantic model, order it after that model. |
| Graph model/query set | Map node/edge sources to target tables and arrange loading/refresh. [API-created graph models currently require a first portal open](https://learn.microsoft.com/fabric/graph/manage-data) to initialize loading infrastructure; do not assume definition import alone makes them queryable. |
| Semantic model | Set target data-source/connection parameters and arrange refresh or Direct Lake framing as applicable. Workspace-specific SQL endpoint URLs and database IDs don't automatically rebind through Git. |

Review [cross-workspace dependency binding](https://learn.microsoft.com/fabric/cicd/cross-workspace-dependency-binding) for every reference. A plan's logical IDs identify items; they do not rewrite every dependency inside those items. Connections, credentials, workspace identities and other workspace settings are configured separately. Keep tokens and passwords out of item files and plan parameters.

Only [supported executable items](https://learn.microsoft.com/fabric/cicd/deployment-plan/deployment-plan-actions#item-types-you-can-run-as-an-action) can be plan actions: notebooks, data pipelines, Dataflow Gen2, copy jobs and user data functions. Ontologies, graphs and semantic models can be deployed as groups; they are not generic `Execute` actions. A refresh step can instead run a supported data pipeline/notebook using the target item's documented API and permissions.

An action finishes when its own run finishes, not when a SQL analytics endpoint, graph or model catches up. The bundled seeder completes Delta writes, but **does not wait for SQL catalog discovery or refresh downstream models**. Before adding a consumer that needs this readiness during deployment, add a bounded readiness check to your action and fail on timeout. Do not substitute an arbitrary sleep.

Prefer small synthetic fixtures embedded in notebook code or an authenticated data source. Notebook `Resources/builtin/` files can be [tracked by Git](https://learn.microsoft.com/fabric/data-engineering/notebook-source-control-deployment#notebooks-resources-folder-support-in-git), but deployment-pipeline/public-API support is currently limited; the starter doesn't depend on this feature.

The Foundry IQ source remains scoped to the selected DEV lakehouse's **Files** area. Creating tables, an ontology or a graph doesn't make those objects part of that knowledge source.

## Promoting through TEST and PROD

Terraform creates a native deployment pipeline with DEV and TEST assigned. Find it using `deployment_pipeline_id` and `deployment_pipeline_stage_ids`. It doesn't deploy items.

1. Synchronize the reviewed Git revision into DEV. Confirm DEV matches that revision with no uncommitted workspace changes; record the commit and freeze source edits during promotion. Native pipelines copy live workspace definitions, not a repository checkout.
2. In the deployment pipeline, select the starter items and any added consumers. For the first TEST deployment, choose the **incoming source `Bootstrap` plan** and include it in the selection; TEST doesn't yet contain a plan. Configure required target connections/settings before actions depend on them.
3. Deploy to TEST, wait for the plan/actions, then verify data, bindings and consumer readiness using your project's checks. TEST gets its own seeded data, not a copy of DEV's OneLake contents. Subsequent deployments may default to the target plan: explicitly choose the reviewed plan version.
4. After approval, promote the same reviewed definitions from TEST to your separately provisioned PROD. Don't promote later DEV changes accidentally. Provision/assign PROD and define its reviewed release plan and approval policy in the derived project before this step.

PROD should normally use ingestion from approved production sources, not synthetic fixtures. The bundled plan fails its seeder action outside `*-dev`/`*-test`. Define a separate production release plan that omits/replaces the demo action, or explicitly authorize demo seeding for a deliberate demo environment; don't silently weaken the default guard. Version and review both plans with the same release.

For automation, use Fabric's [Deploy Stage Content API with a deployment plan](https://learn.microsoft.com/fabric/cicd/deployment-plan/deployment-plan-automation#deploy-content-between-pipeline-stages): add `beta=true` and `options.deploymentPlan` with the plan's physical `itemId` and `referenceType: "ByItemId"`. Retain the normal source/target stage fields and selected items, and poll the operation. This is different from Git's logical-ID reference.

The pinned Terraform provider treats changes to the pipeline's `stages` as replacement. Review the plan when extending it for PROD; don't change Terraform-owned stage assignments only in the portal and expect Terraform to preserve that drift.

Native pipeline promotion leaves items absent from the source in place; this template does not automate orphan deletion. Git synchronization can apply reviewed item deletions, and deleting a lakehouse/workspace can destroy its data. Treat deletions as explicit changes, not cleanup. A failed deployment can leave partial changes; inspect them before rerunning. The insert-only seeder is safe to rerun after resolving the failure.

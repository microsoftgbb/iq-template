# Getting started

Use this template for a **new project**, not an existing deployment. Each derived repository gets its own resources and Terraform state.

## Prerequisites

- **Tools:** Git, Bash, [Terraform 1.11+ (below 2.0)](https://developer.hashicorp.com/terraform/install), and [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli). No local Python, Fabric CLI or fabric-cicd installation is required; the seed notebook runs in Fabric.
- **Azure access:** subscription Owner, or Contributor plus Role Based Access Control Administrator/User Access Administrator. Allow registration of `Microsoft.Fabric`, `Microsoft.CognitiveServices`, and `Microsoft.Search`; Terraform handles registration and resource-scoped role assignments.
- **Fabric access:** an identity allowed to create workspaces, connections and deployment pipelines, listed in `fabric_capacity_admins`. Complete the provider's [CLI user authentication setup](https://registry.terraform.io/providers/microsoft/fabric/latest/docs/guides/auth_app_reg_user), including deployment-pipeline permissions. The identity connecting Git needs workspace Admin; the seeder's execution identity needs notebook execution and lakehouse write access. An administrator must enable Git/GitHub synchronization, applicable cross-geography export, and **Users can create deployment plan (preview) items** ([Git prerequisites](https://learn.microsoft.com/fabric/cicd/git-integration/git-get-started#prerequisites), [deployment-plan prerequisites](https://learn.microsoft.com/fabric/cicd/deployment-plan/how-to-create-deployment-plan)). This template does not change tenant settings.
- **OneLake access:** Search and Fabric must share a tenant. Enable external-app OneLake access and service-principal Fabric APIs. The [OneLake indexer](https://learn.microsoft.com/azure/search/search-how-to-index-onelake-files#grant-permissions) requires **Contributor on DEV** for the Search managed identity; Terraform grants it, not access to TEST/PROD.
- **Region and quota:** choose a region supporting [Fabric](https://learn.microsoft.com/fabric/admin/region-availability), [Search agentic retrieval](https://learn.microsoft.com/azure/search/search-region-support), and the model versions/SKUs in [`variables.tf`](../infra/variables.tf). Confirm quota or override `chat_model`/`embedding_model`.
- **GitHub token:** a fine-grained PAT for the **new repository**, with **Contents: Read and write**, approved/SSO-authorized by your organization where required.

## 1. Create your repository

On GitHub, select **Use this template > Create a new repository**, then clone **that repository** and open its `infra/` directory. Keep the three item folders under `workspace/`, including their `.platform` logical IDs. The Git bootstrap configuration defaults to `main:/workspace`; set `github_branch_name` if your default branch differs. Push any customization to the selected branch before synchronizing Fabric.

## 2. Configure

```sh
az login
az account set --subscription "<subscription-name-or-id>"
cp -n terraform.tfvars.example terraform.tfvars
```

Edit the **five placeholders** in `terraform.tfvars`: `project_name`, `location`, `fabric_capacity_admins`, `github_owner`, and `github_repository_name`. Leave `knowledge_lakehouse_id = null` for the first apply. Use your new repository's details and a distinct, short project name. The capacity admin is your Fabric identity's user UPN or service-principal **object ID**.

Subscription/authentication come from the active Azure CLI account or standard `ARM_*`/`FABRIC_*` provider variables; `ARM_SUBSCRIPTION_ID` takes precedence over the CLI subscription. No subscription ID goes in tfvars. For azd environments, export `ARM_SUBSCRIPTION_ID="$(azd env get-value AZURE_SUBSCRIPTION_ID)"`; azd login alone does not authenticate AzureRM. Service-principal automation also requires the Fabric tenant's service-principal API setting.

Enter the PAT without saving it in shell history (Bash/zsh):

```sh
printf 'GitHub PAT: '
read -rs TF_VAR_github_token
printf '\n'
export TF_VAR_github_token
```

## 3. Provision the platform

```sh
terraform init
terraform validate
terraform plan -out=platform.tfplan
# Review the plan output before continuing:
terraform apply platform.tfplan
terraform output
```

Defaults provision F2 Fabric capacity, empty DEV/TEST workspaces, a GitHub credentials connection, a native DEV/TEST deployment pipeline, Foundry and its models, and Basic AI Search. **Git is not yet connected and knowledge setup is pending.** No separate Blob account or private networking is created. Keep the PAT exported for both plan/apply stages; it is ephemeral and not saved in state/plans. Increment `github_token_version` when rotating it.

## 4. Bootstrap DEV from Git

Open the workspace identified by `dev_workspace_id` / `dev_workspace_name`. In its Git integration settings, use the GitHub repository, branch and directory from `github_connect_request`, and the credentials connection identified by `github_connection_id`.

Choose **Connect and sync with deployment plan**, then select the incoming **Bootstrap** plan. Include `Scenario`, `SeedScenario` and `Bootstrap` in the operation. The plan deploys the lakehouse first, deploys the notebook second, and executes the notebook as its post-deploy action. Wait for the operation and notebook run to succeed; Spark startup can take several minutes.

The notebook creates `dbo.demo_sites` (two rows), `dbo.demo_assets` (three rows), and `Files/starter.txt`. It resolves `Scenario` in the **current workspace**, uses GUID-based OneLake paths, and requires no default lakehouse attachment. Reruns only insert missing demo keys and create the file if absent; they do not overwrite edits or delete other data. Run one seeder per workspace at a time. It refuses workspace names other than `*-dev` / `*-test` unless `allow_other_workspaces` is explicitly enabled.

Copy the **physical lakehouse GUID** printed by the notebook (also available in the lakehouse URL), not the logical ID in `.platform`. Confirm the files/tables exist before continuing. Without an attached plan, Git only deploys definitions: open `SeedScenario` and run all cells separately.

For API automation, independent reruns, and extending the plan with ontologies/graphs/semantic models, see [`cicd/`](../cicd/README.md). The pinned Terraform Fabric provider cannot attach a deployment plan during Git initialization, so this operation deliberately remains outside Terraform.

## 5. Complete knowledge setup

Replace `knowledge_lakehouse_id = null` in the **same** `terraform.tfvars` with the DEV lakehouse GUID, then run a second normal plan/apply:

```sh
terraform plan -out=knowledge.tfplan
# Review the plan output before continuing:
terraform apply knowledge.tfplan
terraform output
```

Terraform looks up the lakehouse in DEV without taking ownership of it, then creates the knowledge source, knowledge base and Foundry MCP connection. An ID from TEST or another workspace fails the lookup. Keep this value set on subsequent runs; setting it back to null plans removal of the knowledge objects, not a pause. No targeted apply is needed.

Ingestion runs asynchronously every five minutes. In your derived project, inspect status/errors with:

```sh
az rest --method get --resource https://search.azure.com \
  --url "$(terraform output -raw knowledge_source_status_url)"
```

Allow time for permission propagation; resolve any ingestion errors before relying on retrieval. Outputs identify the Foundry project, knowledge-base MCP connection, and OneLake lakehouse/Files URL. Upload additional documents into that lakehouse's **Files** area, not Tables. For document URLs in citations, set `includeReferenceSourceData: true` in the retrieval request's OneLake `knowledgeSourceParams`.

Use the native DEV/TEST deployment pipeline and explicitly attach the plan when promoting items. Follow the separate [`cicd/` guide](../cicd/README.md) for reviewed releases and PROD. Promotion does not copy source data or repeat Terraform's DEV-only knowledge setup.

## Keep in mind

- **Isolation:** start each project with new state; never copy another project's tfvars, state, or `.terraform/`. Once deployed, retain state and keep `project_name` stable. Use an encrypted, access-controlled remote backend with a separate state key per project before team/CI use.
- **Secrets:** state/plans can contain provider-returned secrets, including the Search bootstrap key. Keep them, PATs, tfvars, and debug logs out of Git. OneLake uses Entra authentication; runtime access uses managed identities. Commit `.terraform.lock.hcl`.
- **Scope/cost:** resources and notebook execution are billable, endpoints are authenticated but public, and deployment plans/Foundry IQ use preview features. Only the selected DEV lakehouse's Files are indexed, not tables, ontologies, graphs or semantic models. TEST stays empty until promotion; PROD provisioning and agents are not included. Source/embedding configuration changes rebuild the index.
- **Teardown:** review `terraform plan -destroy -out=destroy.tfplan`, then apply that plan. Deleting the Terraform-owned workspaces also deletes their Git-owned items and data; Git is not a data backup.

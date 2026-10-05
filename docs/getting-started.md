# Getting started

Use this template for a **new project**, not an existing deployment. Each derived repository gets its own resources and Terraform state.

## Prerequisites

- **Tools:** Git, Bash, [Terraform 1.11+ (below 2.0)](https://developer.hashicorp.com/terraform/install), and [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli). Terraform invokes the Azure CLI once to upload the OneLake starter file; no Python deployment code is required.
- **Azure access:** subscription Owner, or Contributor plus Role Based Access Control Administrator/User Access Administrator. Allow registration of `Microsoft.Fabric`, `Microsoft.CognitiveServices`, and `Microsoft.Search`; Terraform handles registration and resource-scoped role assignments.
- **Fabric access:** an identity allowed to create workspaces/connections, listed in `fabric_capacity_admins`. Complete the provider's [CLI user authentication setup](https://registry.terraform.io/providers/microsoft/fabric/latest/docs/guides/auth_app_reg_user). An administrator must enable Fabric item/workspace creation, Git/GitHub synchronization, and cross-geography Git export where applicable ([settings](https://learn.microsoft.com/fabric/cicd/git-integration/git-get-started#prerequisites)); this template does not change tenant settings.
- **OneLake access:** Search and Fabric must share a tenant. Enable external-app OneLake access and service-principal Fabric APIs. The [OneLake indexer](https://learn.microsoft.com/azure/search/search-how-to-index-onelake-files#grant-permissions) requires **Contributor on DEV** for the Search managed identity; Terraform grants it. The Azure CLI identity uploading the file must have write access to the DEV lakehouse.
- **Region and quota:** choose a region supporting [Fabric](https://learn.microsoft.com/fabric/admin/region-availability), [Search agentic retrieval](https://learn.microsoft.com/azure/search/search-region-support), and the model versions/SKUs in [`variables.tf`](../infra/variables.tf). Confirm quota or override `chat_model`/`embedding_model`.
- **GitHub token:** a fine-grained PAT for the **new repository**, with **Contents: Read and write**, approved/SSO-authorized by your organization where required.

## 1. Create your repository

On GitHub, select **Use this template > Create a new repository**, then clone **that repository** and open its `infra/` directory. Keep the included `workspace/.gitkeep`; DEV connects to `main:/workspace` by default. Set `github_branch_name` if your default branch differs.

## 2. Configure

```sh
az login
az account set --subscription "<subscription-name-or-id>"
cp -n terraform.tfvars.example terraform.tfvars
```

Edit the **five values** in `terraform.tfvars`: `project_name`, `location`, `fabric_capacity_admins`, `github_owner`, and `github_repository_name`. Use your new repository's details and a distinct, short project name. The capacity admin is your Fabric identity's user UPN or service-principal **object ID**.

Subscription/authentication come from the active Azure CLI account or standard `ARM_*`/`FABRIC_*` provider variables; `ARM_SUBSCRIPTION_ID` takes precedence over the CLI subscription. No subscription ID goes in tfvars. For azd environments, export `ARM_SUBSCRIPTION_ID="$(azd env get-value AZURE_SUBSCRIPTION_ID)"`; azd login alone does not authenticate AzureRM. Service-principal automation also requires the Fabric tenant's service-principal API setting.

The OneLake upload uses the **Azure CLI login**, even when Terraform providers use environment-based credentials. In CI, authenticate Azure CLI as the deployment identity too.

Enter the PAT without saving it in shell history (Bash/zsh):

```sh
printf 'GitHub PAT: '
read -rs TF_VAR_github_token
printf '\n'
export TF_VAR_github_token
```

## 3. Plan and apply

```sh
terraform init
terraform validate
terraform plan -out=deployment.tfplan
# Review the plan output before continuing:
terraform apply deployment.tfplan
terraform output
```

Defaults provision F2 Fabric capacity, DEV/TEST workspaces, a DEV knowledge lakehouse with `Files/starter.txt`, a Foundry account/project and models, and a OneLake-backed knowledge base on Basic AI Search. No separate Blob account or private networking is created. Keep the PAT exported through apply; it is ephemeral and not saved in state/plans. Increment `github_token_version` when rotating it.

Ingestion runs asynchronously every five minutes. In your derived project, inspect status/errors with:

```sh
az rest --method get --resource https://search.azure.com \
  --url "$(terraform output -raw knowledge_source_status_url)"
```

Allow time for permission propagation; resolve any ingestion errors before relying on retrieval. Outputs identify the Foundry project, knowledge-base MCP connection, and OneLake lakehouse/Files URL. Upload additional documents into that lakehouse's **Files** area, not Tables. For document URLs in citations, set `includeReferenceSourceData: true` in the retrieval request's OneLake `knowledgeSourceParams`.

The starter upload reruns when its local content or lakehouse changes, and overwrites only `Files/starter.txt`. It is bootstrap content, not continuous file synchronization. Commit the new lakehouse definition through Fabric Git integration when ready; file contents are not stored in Git.

To promote Git-versioned Fabric items into TEST or an existing PROD workspace, follow the separate [`cicd/` guide](../cicd/README.md). This optional step uses fabric-cicd; it does not copy underlying data or change Terraform setup.

## Keep in mind

- **Isolation:** start each project with new state; never copy another project's tfvars, state, or `.terraform/`. Once deployed, retain state and keep `project_name` stable. Use an encrypted, access-controlled remote backend with a separate state key per project before team/CI use.
- **Secrets:** state/plans can contain provider-returned secrets, including the Search bootstrap key. Keep them, PATs, tfvars, and debug logs out of Git. OneLake uses Entra authentication; runtime access uses managed identities. Commit `.terraform.lock.hcl`.
- **Scope/cost:** resources are billable, endpoints are authenticated but public, and Foundry IQ uses preview APIs. Only the dedicated DEV lakehouse's Files are indexed, not other workspaces, tables, or lakehouses. TEST starts empty; PROD provisioning and agents are not included. Source/embedding configuration changes rebuild the index.
- **Teardown:** review `terraform plan -destroy -out=destroy.tfplan`, then apply that plan. It deletes the project's managed resources and documents.

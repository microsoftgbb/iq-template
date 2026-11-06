# Getting started

Use this template for a **new project**, not an existing deployment. Each derived repository gets its own resources and Terraform state. Create it with **Use this template**, not a fork: forks of this public repository are public and inherit the template setting, so the deployment runner refuses to deploy from them. You use the template at your own risk; read the [disclaimer](../README.md#disclaimer) first.

The default Fabric bootstrap imports definitions through native APIs and needs
**no GitHub PAT**. Fabric GitHub synchronization is opt-in.

The runner assumes you administer the Azure subscription, Entra tenant and
Fabric, and it signs you in to every CLI before it creates anything. It names a
specific grant only when an API refuses for lack of permission. The [manual
preparation checklist](../preparation.md) covers tenant settings, licenses and
approvals, and the grants a non-administrator may need.

For a visual overview, see the [platform architecture](architecture.md) and [deployment flow](deployment-flow.md). The prepared toolchain includes the [official Microsoft IQ Copilot packages](copilot-tooling.md); their MCP authentication and runtime connections remain separate from image installation.

**Guided App/CLI path:** open the derived repository's Codespace and tell Copilot **"Deploy the platform"**. The [deployment skill](../.github/skills/deploy-platform/SKILL.md) carries out all stages below: it completes every sign-in first, asks for missing choices and plan approvals, verifies document and live ontology retrieval, then publishes the scenario agent and deploys its web app. For App, direct it to that same Codespace through GitHub CLI SSH. See [single-prompt setup, credentials and state retention](development.md#deploy-from-one-prompt).

**Deterministic path:** the skill drives the same [runner](development.md#deploy-deterministically) that you can run yourself, with the same gates and no AI agent: `python3 .github/skills/deploy-platform/scripts/deploy.py up`. The steps below remain the canonical manual flow and the runner's contract.

[![Deployment at a glance: configure and authorize, build and bootstrap DEV, connect and prove knowledge, then publish and verify the Foundry hosted agent and the Container Apps web app. Saved-plan approvals, administrator consent and user sign-in are separate gates.](diagrams/deployment-flow.svg)](deployment-flow.md)

Read each row left to right, then continue down. The diagram's ten stages expand
the seven manual sections below; [stage details](deployment-flow.md) distinguish
DEV readiness from the agent/app and later promotion.

## Prerequisites

- **Tools:** open the included [Codespaces/devcontainer](development.md), or install Git, Bash, [Terraform 1.11+ (below 2.0)](https://developer.hashicorp.com/terraform/install), and [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) 2.80 or later locally. The optional initializer also uses Python 3. The container includes Copilot CLI and Node.js 24 for the [web app](#7-create-the-agent-and-app). The runner builds the agent and app images in Azure Container Registry, so no local Docker is needed. No local Spark, Fabric CLI or fabric-cicd installation is required; notebooks run in Fabric.
- **Azure access:** subscription Owner, or Contributor plus Role Based Access Control Administrator/User Access Administrator. Allow registration of `Microsoft.App`, `Microsoft.CognitiveServices`, `Microsoft.ContainerRegistry`, `Microsoft.Fabric`, `Microsoft.Insights`, `Microsoft.ManagedIdentity`, `Microsoft.OperationalInsights`, `Microsoft.Search` and `Microsoft.WebIQ`; Terraform handles registration and resource-scoped role assignments. The default serverless (preview) Search tier also needs the subscription's auto-approved `Microsoft.Search/Serverless` feature; the runner registers it and re-registers `Microsoft.Search` as you (by hand: `az feature register --namespace Microsoft.Search --name Serverless`, then `az provider register --namespace Microsoft.Search`).
- **Web IQ:** enable Web IQ for the Azure subscription. Accept its terms and global public-endpoint usage, complete portal AppID linking after provisioning, and supply `WEB_IQ_API_KEY` securely for the required public-search proof. Follow the [manual checklist](../preparation.md#web-iq).
- **Entra access:** an existing identity allowed to create/configure app registrations, owners, service principals and pre-authorized clients (for example Application Developer), and a role that can grant tenant-wide consent (for example Cloud Application Administrator). Terraform creates two registrations: the Fabric deployment app and the web app's single-page sign-in. The runner grants consent for each registration's declared delegated scopes as you, and hands you the approval link for another administrator only if Entra refuses. Azure subscription ownership does **not** grant these directory permissions. The [AzureAD provider](https://registry.terraform.io/providers/hashicorp/azuread/latest/docs) uses the existing sign-in, not the app it is creating. The tenant must already have the Microsoft **Power BI Service** enterprise application so its published scopes can be read; the template does not take ownership of that shared Microsoft application. Terraform likewise reads, but never owns, the Microsoft Foundry API service principal behind `https://ai.azure.com` (shown as **Azure Machine Learning Services**) for the web registration's delegated scope.
- **Fabric access:** an authorized user allowed to create workspaces and deployment pipelines, listed in `fabric_capacity_admins`. Step 3 prepares the provider's [CLI user authentication setup](https://registry.terraform.io/providers/microsoft/fabric/1.14.0/docs/guides/auth_app_reg_user); consent follows, granted by the runner as you or by an administrator. Bootstrap needs `Item.ReadWrite.All` and `Item.Execute.All`. An administrator must enable **Users can create ontology (preview) items** and **Users can create deployment plan (preview) items** ([ontology settings](https://learn.microsoft.com/fabric/iq/ontology/overview-tenant-settings), [plan prerequisites](https://learn.microsoft.com/fabric/cicd/deployment-plan/how-to-create-deployment-plan)). Opt-in Git synchronization additionally needs connection creation, workspace Admin, Git/GitHub tenant settings and applicable cross-geography export. This template does not change tenant settings.
- **OneLake access:** Search and Fabric must share a tenant. Enable external-app OneLake access and service-principal Fabric APIs. The [OneLake indexer](https://learn.microsoft.com/azure/search/search-how-to-index-onelake-files#grant-permissions) requires **Contributor on DEV** for the Search managed identity; Terraform grants it, not access to TEST/PROD.
- **Region and quota:** choose a region supporting [Fabric](https://learn.microsoft.com/fabric/admin/region-availability), [Search agentic retrieval](https://learn.microsoft.com/azure/search/search-region-support), the [serverless Search tier](https://learn.microsoft.com/azure/search/search-sku-tier#serverless-pricing-model-preview) (or set `search_sku` to a dedicated tier such as `basic`), the model versions/SKUs in [`variables.tf`](../infra/variables.tf), and, for `location`, [Foundry hosted agents](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents#region-availability). The runner refuses a `location` without documented hosted agents before it provisions anything. Check actual available [Fabric CUs](https://learn.microsoft.com/fabric/enterprise/fabric-quotas), model quota, and, for dedicated Search tiers, Search's current new-service capacity restrictions; regional support alone is insufficient. Confirm quota or override `chat_model`/`embedding_model`.
- **Only for opt-in Fabric Git synchronization:** a fine-grained PAT for the **new repository**, with **Contents: Read and write**, approved/SSO-authorized where required.
- **Agent and app:** nothing beyond the access above for the deployer, who receives Foundry User on the Foundry account through Terraform. Each additional app user needs an agent role, such as **Foundry Agent Consumer** on the project or agent, and their own read access to the DEV ontology and its data, granted by an operator; see the [access model](runtime-integration.md#access-model).

## 1. Create your repository

On GitHub, select **Use this template > Create a new repository**, then open **that repository** in Codespaces or clone it locally. Keep the five item folders under `workspace/`, including their `.platform` logical IDs: these are portable identities, not someone else's deployment bindings. The notebooks resolve physical bindings in each target workspace. Select your new repository's published bootstrap branch and `/workspace` directory; do not select an unpublished Copilot worktree branch. Push any customization to the selected branch before synchronizing Fabric. Replace the template's code owners and contribution, conduct, security and support policies with your own, and keep its MIT license notice ([what gets templated](development.md#what-gets-templated)).

## 2. Configure

**With Copilot App/CLI:** from the repository root, ask it to **use /initialize-project**. The skill confirms your project, region and Fabric admin, resolves the new GitHub repository and published branch, then creates only the ignored `infra/terraform.tfvars`. It does not sign in or deploy, and refuses existing local configuration/state.

**Manually:** copy `infra/terraform.tfvars.example` to `infra/terraform.tfvars` without overwriting an existing file. Edit its **six placeholders**: `project_name`, `location`, `fabric_capacity_admins`, `github_owner`, `github_repository_name`, and `github_branch_name`. Use your new repository's details and a distinct, short project name. The capacity admin is your Fabric identity's user UPN or service-principal **object ID**. The initializer also works [directly without Copilot](development.md#what-gets-templated).

For either path, leave `knowledge_lakehouse_id = null` and `knowledge_ontologies = {}` through identity and platform setup. Start with a normal Azure CLI user sign-in in the chosen tenant. This lets Terraform prepare Entra configuration; the Fabric-scoped sign-in comes **after** registration and consent in step 3. Work from `infra/` for the remaining Terraform commands:

`location` is the common region and the Foundry/resource-group region. Keep that
default when available. If quota or regional service capacity blocks a service,
explicitly choose `fabric_location` and/or `search_location` in the same ignored
tfvars file. They default to `location`. Review cross-region data handling,
latency and traffic costs; the services still need the same Entra tenant. Never
change an existing region as an automatic retry: review the full replacement
plan and preserve the original state.

```sh
az login --use-device-code --tenant "<tenant-id>"
az account set --subscription "<subscription-name-or-id>"
cd infra
```

Subscription/authentication come from the active Azure CLI account or standard `ARM_*`/`FABRIC_*` provider variables; `ARM_SUBSCRIPTION_ID` takes precedence over the CLI subscription. No subscription ID goes in tfvars. For azd environments, export `ARM_SUBSCRIPTION_ID="$(azd env get-value AZURE_SUBSCRIPTION_ID)"`; azd login alone does not authenticate AzureRM. Service-principal automation also requires the Fabric tenant's service-principal API setting.

Leave `fabric_git_enabled = false` for native import without a PAT. To opt into
Fabric GitHub synchronization, set it to `true` and supply the PAT for every
plan/apply. In Codespaces, use the repository-scoped `FABRIC_GITHUB_TOKEN` secret
described in [developer setup](development.md#deploy-from-one-prompt):

```sh
set +x
export TF_VAR_github_token="${TF_VAR_github_token:-${FABRIC_GITHUB_TOKEN:?Configure the Fabric GitHub PAT securely}}"
```

Alternatively, enter the PAT in your own terminal without saving it in shell history (Bash/zsh); keep that shell for the following commands:

```sh
printf 'GitHub PAT: '
read -rs TF_VAR_github_token
printf '\n'
export TF_VAR_github_token
```

Complete the [Web IQ checklist](../preparation.md#web-iq) and set
`web_iq_terms_accepted = true` before deployment. Terraform provisions the global
G3 account; agent integration is separate. The runner's `verify` phase requires
portal AppID linking, `WEB_IQ_API_KEY` and a cited public search.

## 3. Bootstrap the Fabric user-auth application

Use the **same `infra/` root, tfvars, execution host and backend/state** for every stage. Configure the agreed secured backend before this first apply; do not create a separate identity state or deploy from another App worktree.

The one-time targeted plan avoids a circular prerequisite: Fabric cannot authenticate until this registration is prepared and consented. This is the **only supported targeting exception**, not the normal deployment workflow. `TF_VAR_github_token` is required only when `fabric_git_enabled = true`; the identity module does not consume or store it.

```sh
terraform init
terraform validate
terraform plan -target=module.fabric_user_auth -out=identity.tfplan
# Review the Entra-only plan before continuing:
terraform apply identity.tfplan
terraform output fabric_user_auth
```

The plan must contain only the `fabric_user_auth` module's AzureAD resources and scope UUID, not capacity, workspace or other platform changes. Terraform's incomplete-target warnings are expected **only here**. Only the `fabric_user_auth*` outputs are ready; a successful identity apply is not a deployed platform.

Terraform creates `${project_name}-fabric-user-auth`, its service principal, the `api://<client-id>` URI and an exposed `access` scope. It pre-authorizes Azure CLI and the two Power BI clients documented by the pinned Fabric provider. The current Terraform identity owns the application and service principal. No client secret, application permission, consent grant or tenant setting is created.

The required **delegated Power BI Service/Fabric** permissions are declared together:

| Operations | Scopes |
| --- | --- |
| Capacity assignment and workspace management | `Capacity.ReadWrite.All`, `Workspace.ReadWrite.All` |
| Opt-in GitHub credentials connection | `Connection.ReadWrite.All` |
| Item access, binding and notebook/plan execution | `Item.ReadWrite.All`, `Item.Execute.All` |
| Opt-in Git synchronization and binding commits | `Workspace.GitUpdate.All`, `Workspace.GitCommit.All` |
| Pipeline management and content promotion | `Pipeline.ReadWrite.All`, `Pipeline.Deploy` |

Scope IDs are looked up on the tenant's existing Power BI Service principal; missing scopes fail rather than being skipped. These cover the template's provisioning, bootstrap and promotion APIs, not every Fabric API. Microsoft Graph is used by the **AzureAD provider's existing identity** to configure Entra; the new deployment app does not need Graph directory-read or application-management permissions.

References: [capacity assignment](https://learn.microsoft.com/rest/api/fabric/core/workspaces/assign-to-capacity), [connection creation](https://learn.microsoft.com/rest/api/fabric/core/connections/create-connection), [pipeline creation](https://learn.microsoft.com/rest/api/fabric/core/deployment-pipelines/create-deployment-pipeline), and [Git/pipeline deployment-plan scopes](https://learn.microsoft.com/fabric/cicd/deployment-plan/deployment-plan-automation).

### Consent and user sign-in

The [runner](development.md#deploy-deterministically) grants this consent as you, for exactly these declared scopes, when your Entra role allows it (for example Cloud Application Administrator). If Entra refuses, or you pass `--manual-grants`, give an administrator the non-secret `fabric_user_auth` output. They open `api_permissions_url` in the indicated tenant, review the declared delegated scopes, and select **Grant admin consent**. Terraform prepares the permission request and client authorizations; it deliberately does **not** grant consent itself.

After consent, the runner acquires the generated scope's token silently when it can. By hand, sign in again using the generated values, not a manually assembled app URI:

```sh
az login --use-device-code \
  --tenant "$(terraform output -raw fabric_user_auth_tenant_id)" \
  --scope "$(terraform output -raw fabric_user_auth_scope)"
az account set --subscription "<subscription-name-or-id>"
```

Use the same Terraform user for subsequent phases. An administrator can grant consent through the portal without changing the Terraform caller. Switching Terraform callers can change application ownership and deployment role assignments; review that explicitly rather than silently replacing the owner.

Verify the user has the actual Fabric roles and delegated scopes before continuing. Creating the registration or successfully signing in does not prove all Fabric operations are authorized. Do not substitute a service principal for the bundled notebook user's identity, and do not set `FABRIC_CLIENT_ID` to this app to turn the CLI flow into client-credentials authentication.

Keep the registration's permission manifest distinct from the bearer token's
scope representation. The pinned provider uses the first-party Azure CLI
credential, whose Fabric token can carry the general `user_impersonation` scope
rather than those nine named scopes. A direct list comparison can incorrectly
block this flow. Verify the expected delegated caller, tenant, Fabric audience
and administrator consent independently, then check actual API/role access.
Neither a generic token scope nor a successful read proves every deployment
operation is allowed.

On resume, retain the registration in the original state and reuse its outputs. A same-named app blocks creation rather than being adopted; recover the original state or review an explicit import instead of creating a duplicate. Do not routinely repeat targeted plans. Changes to requested permissions need administrator review/re-consent and a fresh user sign-in; registration outputs do not track consent or token readiness.

## 4. Provision the platform

After consent and the scoped user sign-in, return to normal, **untargeted** plans:

```sh
terraform plan -out=platform.tfplan
# Review the plan output before continuing:
terraform apply platform.tfplan
terraform output
```

Defaults provision F2 Fabric capacity, empty DEV/TEST workspaces, a native DEV/TEST deployment pipeline, Foundry and its models, a global G3 Web IQ account, serverless (preview) AI Search, and the agent and app hosting: a container registry, Log Analytics and Application Insights, a Container Apps environment, the web app's pull identity and its sign-in registration. **Workspace bootstrap and knowledge setup are still pending**, and step 7 publishes the agent and app. A GitHub credentials connection is created only with `fabric_git_enabled = true`; its PAT stays ephemeral/write-only, and rotation uses `github_token_version`. No separate Blob account or private networking is created.

The serverless preview fails provisioning when a managed identity is requested and answers HTTP 500 to identity updates while applying them. Terraform therefore creates it without one; the runner then enables the system-assigned identity, and the knowledge apply grants it. By hand:

```sh
url="$(terraform output -raw search_management_url)"
az rest --method patch --url "$url" --body '{"identity":{"type":"SystemAssigned"}}' || true  # HTTP 500 is expected
az rest --method get --url "$url" --query "{identity:identity.principalId, state:properties.provisioningState}"
```

Continue once `identity` is set and the state is `Succeeded`. While the preview defect lasts, a serverless service cannot be updated in place: changing its settings, tags, tier or region replaces it. After knowledge setup, that replacement needs two reviewed plans because the new service has no identity during its apply: the runner first applies it with `search_identity_ready=false`, enables the identity, then applies the identity grants. By hand, plan the replacement with `-var search_identity_ready=false`, run the commands above, then plan again.

## 5. Bootstrap DEV

From the repository root, run `python3 .github/skills/deploy-platform/scripts/deploy.py up --through bootstrap`
with the same subscription and backend. By default, the runner imports the
reviewed, published `workspace/` definitions through Fabric's **Bulk Import Item
Definitions** API and attaches `Bootstrap`. It uses the signed-in Azure user,
not a PAT, and does not connect Fabric to GitHub.

All five items are included: `Scenario`, `SeedScenario`, `ScenarioOntology`,
`BindScenario` and `Bootstrap`. The plan deploys the lakehouse, runs the seeder,
deploys the ontology, then runs its binder. The runner resumes accepted
operations, checks every imported item and never overwrites existing definitions.
If plan attachment is unavailable, it stops for explicit
`--bootstrap-fallback notebooks` approval before importing and running the
notebooks separately.

The notebook creates `dbo.demo_sites` (two rows), `dbo.demo_assets` (three rows), and `Files/starter.txt`. It resolves `Scenario` in the **current workspace**, uses GUID-based OneLake paths, and requires no default lakehouse attachment. Reruns only insert missing demo keys and create the file if absent; they do not overwrite edits or delete other data. Run one seeder per workspace at a time. It refuses workspace names other than `*-dev` / `*-test` unless `allow_other_workspaces` is explicitly enabled.

Run `BindScenario` as an **authorized Fabric user**: service-principal NotebookUtils tokens have restricted scopes. It refreshes the two tables' SQL metadata and binds the Site/Asset ontology to this workspace's lakehouse. Review its generated DEV binding before promotion. Copy the **physical lakehouse and ontology GUIDs** printed by the binder, not the logical IDs in `.platform`. Confirm the files/tables and a data-backed ontology query in Fabric before continuing.

### Optional Git synchronization

With `fabric_git_enabled = true`, the runner uses the existing Git bootstrap
instead. For manual setup, open DEV's Git integration settings, use
`github_connect_request` and `github_connection_id`, then choose **Connect and
sync with deployment plan** and the incoming **Bootstrap** plan. Include all
five items. Review and commit generated DEV bindings through Fabric source
control before promotion.

For API automation and promotion, see [`cicd/`](../cicd/README.md). For binding details, see [seeded ontologies](ontology-knowledge.md). Both bootstrap paths run outside Terraform.

## 6. Complete knowledge setup

In the **same** `terraform.tfvars`, set `knowledge_lakehouse_id` to the DEV lakehouse GUID and register the bound ontology:

```hcl
knowledge_ontologies = {
  scenario = {
    ontology_id = "<dev-ScenarioOntology-guid>"
    description = "Current sites, assets, their status, and which site each asset belongs to."
  }
}
```

Leave this map empty only if you want document-only retrieval. Then run a second normal plan/apply:

```sh
terraform plan -out=knowledge.tfplan
# Review the plan output before continuing:
terraform apply knowledge.tfplan
terraform output
```

Terraform looks up the lakehouse and configured ontologies in DEV without owning their definitions, then creates the knowledge sources, knowledge base and Foundry MCP connection. An ID from TEST or another workspace fails the lookup. Keep the values set on subsequent runs; clearing them plans removal of knowledge objects, not a pause. No targeted apply is needed.

Each registered ontology also receives a non-shared native Foundry Fabric IQ
`UserEntraToken` connection to its DEV MCP endpoint, reported by
`fabric_iq_connections`. Step 7's hosted agent uses the `scenario` connection
for its Fabric IQ tool and the knowledge-base MCP connection for documents.
This registration is not a readiness claim. The Search `fabricOntology` route
still requires its separate user header; see
[runtime integration](runtime-integration.md#toolbox-tools).

The **file source** ingests asynchronously every five minutes. In your derived project, inspect status/errors with:

```sh
az rest --method get --resource https://search.azure.com \
  --url "$(terraform output -raw knowledge_source_status_url)"
```

Allow time for permission propagation; resolve any ingestion errors before relying on retrieval. Outputs identify the Foundry project, knowledge-base MCP connection, and OneLake lakehouse/Files URL. Upload additional documents into that lakehouse's **Files** area, not Tables. For document URLs in citations, set `includeReferenceSourceData: true` in the retrieval request's OneLake `knowledgeSourceParams`.

The **ontology sources query Fabric live**, separately from file ingestion. Retrieval must carry a signed-in user's Search-audience token in `x-ms-query-source-authorization`; the existing project managed identity authenticates only to Search. The deployment registration from step 3 is not a consuming application's sign-in implementation and does not complete this runtime flow. Follow the [runtime authentication and retrieval example](ontology-knowledge.md#supply-the-end-users-identity-at-query-time) before exposing the knowledge base to users. Look for `fabricOntology` activity/references, not merely an answer copied from the starter document.

Use the native DEV/TEST deployment pipeline and explicitly attach the plan when promoting items. Follow the separate [`cicd/` guide](../cicd/README.md) for reviewed releases and PROD. Promotion does not copy source data or repeat Terraform's DEV-only knowledge setup.

## 7. Create the agent and app

The template ships the [scenario assistant](../agents/scenario-assistant/README.md), a Foundry hosted agent with two native IQ tools, and the [web app](../app/) that calls it from Azure Container Apps. This stage publishes both against DEV: the Fabric IQ tool queries the ontology as **each signed-in user**, and the knowledge-base tool reads documents with the project's managed identity. Start after step 6 succeeds. [Runtime integration](runtime-integration.md) explains the identity flow, access model and evidence.

Run both phases with the runner, on the same host, checkout and state:

```sh
python3 .github/skills/deploy-platform/scripts/deploy.py up
```

The runner builds only committed source that matches the published `origin/<github_branch_name>`, so commit and push changes under `agents/scenario-assistant/` or `app/` first. It builds each image in the deployment's container registry with `az acr build`, tagged with the first 16 characters of its directory's Git tree hash; no local Docker is needed, and unchanged source is never rebuilt or redeployed.

- **`agent`** builds the `scenario-assistant` image, publishes a toolbox version from [`hosted-agent.json`](../agents/scenario-assistant/hosted-agent.json) and makes it the default, then creates a hosted agent version. Approve the listed actions interactively or with `--approve agent-deploy`. It waits until the version is `active`, then proves, as you, a fresh challenge, the seeded live facts in the `scenario_ontology` tool's output and `starter.txt` in the `knowledge_base` tool's output.
- **`app`** builds the `web` image, grants tenant-wide consent for the web registration's delegated Foundry scope as you, and creates or updates the Container App at the `web_app` output's URL through Azure Resource Manager. Approve with `--approve app-deploy`. It waits until the latest revision is ready, then checks `/healthz`, `/config.json` and a fresh challenge relayed through the app as you.

With `--manual-grants`, the `app` phase leaves that consent to an administrator, who opens the `web_app` output's `api_permissions_url` and grants admin consent for **Azure Machine Learning Services** `user_impersonation`; then rerun. The [native request contract](../.github/skills/deploy-platform/references/native-requests.md#hosted-agent-and-web-app) lists the Foundry and Azure Resource Manager requests behind both phases, for review and diagnosis; do not hand-submit them in place of the runner.

Then open the web app URL that the runner prints and sign in with your work account. Select **Check connection** and expect this check's fresh challenge back from the agent. Ask "Which assets are in Maintenance and at which site?": expect Pump B at North Works from the live ontology. A live answer can take a minute. Follow-up questions continue the same conversation; **New conversation** starts another.

To change the agent or app, edit `agents/scenario-assistant/` or `app/`, run the [app checks](runtime-integration.md#local-development), commit, push and rerun `up`: the runner publishes a new agent version or Container App revision and proves it again. Other users need an operator to grant them an agent role and Fabric access from the [access model](runtime-integration.md#access-model); the template assigns none. Second-user denial and isolation are not yet verified.

## Keep in mind

- **Isolation:** start each project with new state; never copy another project's tfvars, state, or `.terraform/`. Once deployed, retain state and keep `project_name` stable. Use an encrypted, access-controlled remote backend with a separate state key per project before team/CI use.
- **Identity lifecycle:** the deployment app registration shares that project's state with the platform. Full plans still require access to its Entra objects. Fabric resources depend on the identity module so Terraform tears them down before removing the login registration. Do not remove those dependencies, target-destroy the module, or delete the registration manually while Fabric resources still need it.
- **Secrets:** state/plans can contain provider-returned secrets; the Search admin key is read ephemerally on each run and is not stored. Keep state, PATs, tfvars, and debug logs out of Git. OneLake uses Entra authentication; runtime access uses managed identities and each signed-in user's own token, and the web app holds no secret. Commit `.terraform.lock.hcl`.
- **Scope/cost:** resources, notebook execution and retrieval are billable; endpoints, including the web app, are authenticated but public; ontology/deployment-plan/Foundry IQ features, the Fabric IQ agent tool and the serverless Search tier are preview (no SLA, no tier migration). Serverless bills compute per use, including the five-minute file-source ingestion schedule. Semantic ranking and agentic retrieval use their standard plans: monthly free allowances, then pay-as-you-go. Only the selected DEV lakehouse's Files are indexed. Registered ontologies query their bound data live with end-user permissions; arbitrary tables/graphs/models are not automatically exposed. Hosted agent sessions bill CPU and memory while active, and the Container App scales to zero when idle. TEST stays empty until promotion; the agent and app serve DEV only, and PROD provisioning is not included. File-source/embedding configuration changes rebuild the index.
- **Teardown:** use the [runner's `down` command](development.md#delete-the-environment) to delete the Container App, hosted agent, toolbox and Terraform resources, including workspace data and Search ingestion assets. Retained Fabric workspaces and Web IQ portal credentials need manual cleanup.

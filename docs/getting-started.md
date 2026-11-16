# Getting started

Use this template for a **new project**, not an existing deployment. Each derived repository gets its own resources and Terraform state. Create it with **Use this template**, not a fork: forks of this public repository are public and inherit the template setting, so the deployment runner refuses to deploy from them. You use the template at your own risk; read the [disclaimer](../README.md#disclaimer) first.

The default Fabric bootstrap imports definitions through native APIs and needs
**no GitHub PAT**. Fabric GitHub synchronization is opt-in.

The runner assumes you administer the Azure subscription, Entra tenant and
Fabric, and it signs you in to every CLI before it creates anything. It names a
specific grant only when an API refuses for lack of permission. The
[prerequisites](#prerequisites) name who provides what, and the [manual
preparation checklist](../preparation.md) lists the same items as tasks.

For a visual overview, see the [platform architecture](architecture.md) and [deployment flow](deployment-flow.md). The prepared toolchain includes the [official Microsoft IQ Copilot packages](copilot-tooling.md); their MCP authentication and runtime connections remain separate from image installation.

**Guided App/CLI path:** open the derived repository's Codespace and tell Copilot **"Deploy the platform"**. The [deployment skill](../.github/skills/deploy-platform/SKILL.md) carries out all stages below: it completes every sign-in first, asks for missing choices and approvals, verifies document, live ontology and public-context retrieval, then publishes the two Foundry prompt agents and deploys their web app. For App, direct it to that same Codespace through GitHub CLI SSH. See [single-prompt setup, credentials and state retention](development.md#deploy-from-one-prompt).

**Deterministic path:** the skill drives the same [runner](development.md#deploy-deterministically) that you can run yourself, with the same gates and no AI agent: `python3 .github/skills/deploy-platform/scripts/deploy.py up`. The steps below remain the canonical manual flow and the runner's contract.

[![Deployment at a glance: configure and authorize, build and bootstrap DEV, connect and prove knowledge, then publish the Foundry prompt agents and build and deploy the Container Apps web app. Four saved-plan approvals, the publish and build approvals, administrator consent and user sign-in are separate gates.](diagrams/deployment-flow.svg)](deployment-flow.md)

Read each row left to right, then continue down. The diagram's ten stages expand
the seven numbered sections below; [stage details](deployment-flow.md) distinguish
DEV readiness from the agents and app and later promotion.

## Prerequisites

Arrange these before the first run. Some need other people, so start with the
longest lead times; they are typical, not guaranteed.

| Need | Who acts | Lead time | Details |
| --- | --- | --- | --- |
| Azure subscription | Subscription owner | Days, for a new subscription or role | A paid subscription in the Entra tenant you use for Fabric. **Owner**, or **Contributor** with **Role Based Access Control Administrator** or **User Access Administrator**, and permission to register resource providers and preview features: Terraform registers `Microsoft.App`, `Microsoft.CognitiveServices`, `Microsoft.ContainerRegistry`, `Microsoft.Fabric`, `Microsoft.Insights`, `Microsoft.ManagedIdentity`, `Microsoft.OperationalInsights`, `Microsoft.Search` and `Microsoft.WebIQ`. The subscription allows ACR Tasks, which build the app's image. |
| Entra permissions | Entra administrator | Hours to days | You can create and configure app registrations, owners, service principals and pre-authorized clients, for example as Application Developer; subscription ownership does **not** grant this. The tenant has the **Microsoft Power BI Service** and **Azure Machine Learning Services** (`18a66f5f-dbdf-4c17-9dd7-1634712a9cbe`, the resource behind Foundry's `https://ai.azure.com` tokens) enterprise applications; Terraform reads them and never owns them. |
| Administrator consent | An Entra administrator: the runner, as you, when you are one | Minutes as an administrator; otherwise book one | Tenant-wide consent for the deployment registration's declared Fabric scopes ([step 3](#3-bootstrap-the-fabric-user-auth-application)) and, optionally, the app's Foundry sign-in scope; with Work IQ, also `WorkIQAgent.Ask` and `offline_access` ([Work IQ](#work-iq)). A role such as Cloud Application Administrator can grant it. |
| Fabric tenant settings | Fabric administrator | Hours to days | [**Users can create ontology (preview) items**](https://learn.microsoft.com/fabric/iq/ontology/overview-tenant-settings), [**Users can create deployment plan (preview) items**](https://learn.microsoft.com/fabric/cicd/deployment-plan/how-to-create-deployment-plan), external-app OneLake access and service-principal Fabric APIs. The template never changes tenant settings, and the runner does not check them up front. |
| Fabric permissions | Fabric administrator | Hours | You can create workspaces, connections and deployment pipelines, and you are listed in `fabric_capacity_admins`. Bootstrap needs `Item.ReadWrite.All` and `Item.Execute.All`. |
| Web IQ | You; your Microsoft representative | Days or more: enablement is a request | The paid subscription is enabled for Web IQ; Azure credits do not pay for it. You accept its terms before deployment, then link the resource and create an API key after the platform step ([Web IQ](#web-iq)). |
| Region and quota | You; a subscription owner for quota requests | Days, if quota must be raised | One region that offers [Fabric](https://learn.microsoft.com/fabric/admin/region-availability), [Search agentic retrieval](https://learn.microsoft.com/azure/search/search-region-support), the [serverless Search tier](https://learn.microsoft.com/azure/search/search-sku-tier#serverless-pricing-model-preview) (or set `search_sku` to a dedicated tier such as `basic`), Foundry with the models in [`variables.tf`](../infra/variables.tf), Azure Container Apps and Container Registry. Quota: [Fabric CUs](https://learn.microsoft.com/fabric/enterprise/fabric-quotas) for the SKU (F2 by default); `gpt-5.4-mini` GlobalStandard for the `agent` (100 units) and `chat` (10) deployments, summed because they share one quota; and `text-embedding-3-small` (10). |
| Work IQ (optional) | Global administrator; billing administrator; Microsoft 365 administrator | Days: billing and mailboxes | Only with `work_iq_enabled = true`: the tenant's Work IQ service principal, a Copilot Credits spending policy that covers the Work IQ API and every user, a mailbox per user, and consent ([Work IQ](#work-iq)). |
| Fabric Git synchronization (optional) | You; your GitHub organization owner | Hours, for PAT approval or SSO | Only with `fabric_git_enabled = true`: a fine-grained PAT for the **new repository** with **Contents: Read and write**, plus Git/GitHub tenant settings, workspace Admin and any applicable cross-geography export setting. |
| Deployment host | You | Minutes | The derived repository's Codespace or dev container ([developer setup](development.md)). |
| Other app users | An operator | When you share the app | A Foundry role on the project and read access to the DEV ontology and its data, granted per user; with Work IQ, a mailbox and spending-policy coverage ([access model](runtime-integration.md#access-model)). |

The dev container includes every tool. Locally, install Git 2.27+, Bash,
[Terraform 1.11+ (below 2.0)](https://developer.hashicorp.com/terraform/install),
[Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) 2.60+, GitHub
CLI, Python 3 and, for the [web app](#7-create-the-agents-and-app), Node.js 24.11+.
The runner builds the app's image with ACR Tasks, so no local Docker is needed. No
local Spark, Fabric CLI or fabric-cicd installation is required; notebooks run in
Fabric.

Terraform creates the Fabric deployment app, the web app's single-page sign-in
registration and, with Work IQ, a Work IQ registration that is a public client
without a secret. The [AzureAD provider](https://registry.terraform.io/providers/hashicorp/azuread/latest/docs)
uses your existing sign-in, not the app it is creating. Search and Fabric must share
a tenant: the [OneLake indexer](https://learn.microsoft.com/azure/search/search-how-to-index-onelake-files#grant-permissions)
reads DEV as the Search managed identity, which Terraform makes **Contributor on
DEV** only, never TEST or PROD. Regional support alone does not prove capacity: for
dedicated Search tiers, also check Search's current new-service capacity
restrictions.

## Deployment stages

`deploy.py up` runs these stages as phases, in order. Each run re-verifies the
earlier phases and acts only on what is missing, so rerunning `up` resumes.

| # | Stage | Phase | Gate |
| --- | --- | --- | --- |
| 1 | [Create and configure](#2-configure) | `signin`, `config`, `preflight` | Your device-code sign-ins; a missing input, quota or feature |
| 2 | [Prepare identity](#3-bootstrap-the-fabric-user-auth-application) | `identity` | `--approve identity=<sha12>` |
| 3 | [Consent and sign in](#consent-and-user-sign-in) | `consent` | An administrator, only if Entra refuses your grant |
| 4 | [Provision the platform](#4-provision-the-platform) | `platform` | `--approve platform=<sha12>` |
| 5 | [Bootstrap DEV](#5-bootstrap-dev) | `bootstrap` | None by default; `--approve item-update=<sha12>` with `--update-definitions` |
| 6 | [Verify bindings](#5-bootstrap-dev) | `bootstrap` | A missing binding blocks |
| 7 | [Wire knowledge](#6-complete-knowledge-setup) | `knowledge` | `WEB_IQ_API_KEY`; `--approve knowledge=<sha12>` |
| 8 | [Prove retrieval](#6-complete-knowledge-setup) | `verify` | [Web IQ](#web-iq) portal steps; with Work IQ, its consent; missing evidence blocks |
| 9 | Report DEV ready | `verify` | Every check above passed |
| 10 | [Publish the agents, deploy the app](#7-create-the-agents-and-app) | `agent`, `app` | `agent-publish`; `app-build`; `--approve app=<sha12>` |

The runner exits with `0` when done, `1` on a usage error, `2` when blocked, `3`
when a person must act or a cloud operation is still running, and `4` when it needs
approval. For `3`, do exactly what it prints, then rerun. For `4`, answer the
interactive prompt, or inspect the saved plan with
`terraform -chdir=infra show <plan>` and rerun with `--approve <key>=<sha12>`, the
hash it prints for that exact plan or change; actions take `--approve <action>`.
For `2`, read the message and the receipts in `.artifacts/deploy/state.json`, fix
the cause and rerun. [Deploy deterministically](development.md#deploy-deterministically)
lists every phase, approval and flag.

## 1. Create your repository

On GitHub, select **Use this template > Create a new repository**, then open **that repository** in Codespaces or clone it locally. Keep the five item folders under `workspace/`, including their `.platform` logical IDs: these are portable identities, not someone else's deployment bindings. The notebooks resolve physical bindings in each target workspace. Select your new repository's published bootstrap branch and `/workspace` directory; do not select an unpublished Copilot worktree branch. Push any customization to the selected branch before synchronizing Fabric. Replace the template's code owners and contribution, conduct, security and support policies with your own, and keep its MIT license notice ([what gets templated](development.md#what-gets-templated)).

## 2. Configure

**With Copilot App/CLI:** from the repository root, ask it to **use /initialize-project**. The skill confirms your project, region and Fabric admin, resolves the new GitHub repository and published branch, then creates only the ignored `infra/terraform.tfvars`. It does not sign in or deploy, and refuses existing local configuration/state.

**With the runner:** on first use, `python3 .github/skills/deploy-platform/scripts/deploy.py up --through config --local-state --project-name "<project-name>" --location "<azure-region>" --capacity-admin "<user-principal-name>"` calls the same initializer with this checkout's `origin` and the repository's published default branch (or `--github-branch`).

**Manually:** copy `infra/terraform.tfvars.example` to `infra/terraform.tfvars` without overwriting an existing file. Edit its **six placeholders**: `project_name`, `location`, `fabric_capacity_admins`, `github_owner`, `github_repository_name`, and `github_branch_name`. Use your new repository's details and a distinct, short project name. The capacity admin is your Fabric identity's user UPN or service-principal **object ID**. The initializer also works [directly without Copilot](development.md#what-gets-templated).

For every path, leave `knowledge_lakehouse_id = null` and `knowledge_ontologies = {}` through identity and platform setup, and keep the example's top-level `app_image_tag = null` line: the app phase writes the image tag there. Start with a normal Azure CLI user sign-in in the chosen tenant. This lets Terraform prepare Entra configuration; the Fabric-scoped sign-in comes **after** registration and consent in step 3. Work from `infra/` for the remaining Terraform commands:

`location` is the common region and the Foundry, app and resource-group region. Keep that
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

Complete the [Web IQ](#web-iq) steps and set `web_iq_terms_accepted = true`
before deployment. Terraform provisions the global G3 account; the agents'
public-context tool reaches it through Search with the key you supply. The
runner's `verify` phase requires portal AppID linking, `WEB_IQ_API_KEY` and a cited
public search.

### Configuration keys

Everything else keeps its default in [`variables.tf`](../infra/variables.tf). The
[tfvars example](../infra/terraform.tfvars.example) shows the optional settings as
comments. Keep credentials and tenant and subscription IDs out of the file.

| Key | Default | Set it when |
| --- | --- | --- |
| `project_name`, `location`, `fabric_capacity_admins`, `github_owner`, `github_repository_name`, `github_branch_name` | Placeholders | Always: the six placeholders above |
| `fabric_location`, `search_location` | `location` | Only after approving cross-region data handling and cost |
| `fabric_capacity_sku` | `F2` | You want a larger capacity: Fabric IQ's queries run on it |
| `search_sku` | `serverless` | You want a dedicated, SLA-backed tier such as `basic` |
| `fabric_git_enabled` | `false` | You opt into Fabric GitHub synchronization, which needs a PAT |
| `web_iq_terms_accepted` | `false` | Always: set `true` once you accept the [Web IQ](#web-iq) terms |
| `web_iq_api_key_version` | `1` | You rotated the Web IQ key: increment it. The key itself goes only in `WEB_IQ_API_KEY` |
| `work_iq_enabled` | `false` | You opt into the Work IQ mail tool and meet its [prerequisites](#work-iq) |
| `knowledge_lakehouse_id`, `knowledge_ontologies` | `null`, `{}` | Never by hand on the runner path: the knowledge phase writes them after binding |
| `knowledge_folders` | `documents`: `Files/documents`, attached | You index more document folders. An override replaces the whole map; keys can't be `web` or end in `-ontology` ([folders](ontology-knowledge.md#register-the-folders-and-the-ontology)) |
| `knowledge_base_name` | `<project_name>-knowledge` | You want another name for the agents' knowledge base; renaming replaces it |
| `knowledge_editor_principal_ids` | `[]` | People attach a detached folder source live in the Foundry portal: they get Search Service Contributor |
| `chat_model` | `gpt-5.4-mini`, capacity 10 | Web IQ public-context calls or `verify`'s ontology check return HTTP 429: raise the capacity |
| `embedding_model` | `text-embedding-3-small`, capacity 10 | You change the embedding model; that rebuilds the folder sources |
| `agent_model`, `agent_reasoning_effort` | `gpt-5.4-mini`, capacity 100; `low` | You change the agents' model, its capacity or the effort; `null` omits the effort for a model without reasoning |
| `app` | 1 vCPU, `2Gi`, one to three replicas | Between sessions, `app = { min_replicas = 0 }` stops paying for an idle replica |
| `app_image_tag` | `null` | Never by hand: the app phase writes it after you approve the app plan |
| `container_registry_sku` | `Basic` | You want a larger registry tier |
| `app_local_redirect_uris` | `[]` | You run the app locally against this deployment: `["http://localhost:3000/redirect.html"]` |
| `telemetry` | `{ retention_days = 30 }` | You keep logs and traces longer, up to 730 days |

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

The app's sign-in registration, which the platform apply creates, declares one delegated scope: Azure Machine Learning Services' `user_impersonation`, the `https://ai.azure.com` scope. Once the registration exists, the consent phase grants it tenant-wide as you, or the app phase does on the first run, so no user meets Entra's consent screen. That grant is optional: with `--manual-grants`, or a sign-in that cannot read grants, each user consents at first sign-in where tenant policy allows it, or an administrator grants it at the `app` output's `api_permissions_url`.

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

Defaults provision F2 Fabric capacity, empty DEV/TEST workspaces, a native DEV/TEST deployment pipeline, Foundry with its `agent`, `chat` and `embedding` model deployments, a global G3 Web IQ account, serverless (preview) AI Search, Log Analytics and Application Insights with Foundry's tracing connection, and the app's hosting: a container registry, a Container Apps environment, the app's managed identity and its sign-in registration. With `work_iq_enabled = true`, the plan also creates the Work IQ registration and Foundry's `work-iq` connection. **Workspace bootstrap and knowledge setup are still pending**; the container app waits for the app phase's image, and step 7 publishes the agents. A GitHub credentials connection is created only with `fabric_git_enabled = true`; its PAT stays ephemeral/write-only, and rotation uses `github_token_version`. No separate Blob account or private networking is created.

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

The seeder creates `dbo.demo_sites` (two rows), `dbo.demo_assets` (three rows), and `Files/documents/starter.txt`, which the default `documents` knowledge folder indexes. It resolves `Scenario` in the **current workspace**, uses GUID-based OneLake paths, and requires no default lakehouse attachment. Reruns only insert missing demo keys and create the file if absent; they do not overwrite edits, delete other data or touch a legacy `Files/starter.txt`. Run one seeder per workspace at a time. It refuses workspace names other than `*-dev` / `*-test` unless `allow_other_workspaces` is explicitly enabled.

Run `BindScenario` as an **authorized Fabric user**: service-principal NotebookUtils tokens have restricted scopes. It refreshes the two tables' SQL metadata and binds the Site/Asset ontology to this workspace's lakehouse through its SQL analytics endpoint, with every `ONT_*` source pin; Fabric keeps the pins only in that form, and graph materialization needs them ([binding](ontology-knowledge.md#bind-through-the-sql-analytics-endpoint)). The runner then checks the binding and records the **physical lakehouse and ontology GUIDs**, not the logical IDs in `.platform`. Review the generated DEV binding before promotion. By hand, copy the GUIDs the binder prints and confirm the files/tables and a data-backed ontology query in Fabric before continuing.

| Case | Flag |
| --- | --- |
| The tenant rejects plan attachment (`FeatureNotAvailable`) | `--bootstrap-fallback notebooks` imports without the plan, then runs `SeedScenario` and `BindScenario` in order. Never change a tenant setting instead. |
| A deployed notebook, ontology or plan differs from the checkout | `--update-definitions`. Review each printed `git diff` command, then approve with `--approve item-update=<sha12>`; `--yes` cannot. Lakehouses are never replaced, and updated bootstrap notebooks run again before the binding is re-verified. |
| An import or notebook run failed | Inspect and fix the cause, then rerun with `--retry-failed`. |

### Optional Git synchronization

With `fabric_git_enabled = true`, the runner uses the existing Git bootstrap
instead. For manual setup, open DEV's Git integration settings, use
`github_connect_request` and `github_connection_id`, then choose **Connect and
sync with deployment plan** and the incoming **Bootstrap** plan. Include all
five items. Review and commit generated DEV bindings through Fabric source
control before promotion.

For API automation and promotion, see [`cicd/`](../cicd/README.md). For binding details, see [seeded ontologies](ontology-knowledge.md). Both bootstrap paths run outside Terraform.

## Web IQ

Every deployment includes Web IQ. The platform plan creates its global G3 account.
The knowledge plan adds the knowledge source `<project>-web`, which allows only the
Web IQ MCP `web` tool, as the only source of the knowledge base
`<project>-public-context`, which the with-IQ agent's public-context tool reads. It
is separate from the agents' document knowledge base, so a document question never
triggers a web search. Complete these steps before knowledge setup:

1. Before deployment, have Web IQ enabled for the paid subscription; ask your
   Microsoft representative. Azure credits do not work. Accept the
   [Web IQ terms](https://www.microsoft.com/en-us/webiq/webiq-legal), its usage
   charges, its global public endpoint and its public-only query policy, then set
   `web_iq_terms_accepted = true`. Preflight waits until you do.
2. After the platform step, read `terraform -chdir=infra output web_iq`. Sign in to
   [Web IQ Profile Management](https://webiq.microsoft.ai/profiles), select the
   subscription and the deployed resource, finish linking it and select its
   **Web IQ AppID**.
3. Create a Web Search-enabled API key for that AppID, and supply it to the
   deployment shell only as `WEB_IQ_API_KEY`, without echo or shell history:

   ```sh
   set +x
   printf 'Web IQ key: '
   read -rs WEB_IQ_API_KEY
   printf '\n'
   export WEB_IQ_API_KEY
   ```

Keep the key out of chat, files, tfvars, command arguments and the app. The runner
passes it to Terraform as the ephemeral, write-only `web_iq_api_key` on every plan
and apply from knowledge setup on, never on destroy plans or earlier ones. The
public-context source stores it, Search masks it on read, and it never enters
tfvars, state or plans. Export it in every shell that runs `up` from then on:
without it, the knowledge and app phases wait. Never export
`TF_VAR_web_iq_api_key` in a shell that runs the runner, which refuses it. After rotating the key, export the new one and increment
`web_iq_api_key_version`.

The `verify` phase makes one fixed public-only search and one cited retrieval
through the public-context knowledge base, each attempted once because Web IQ
searches are billable. Neither proves the agent's tool: check a cited
public-context line in the app ([runtime integration](runtime-integration.md#web-iq)).

## Work IQ

Work IQ is off by default (`work_iq_enabled = false`). Turned on, the with-IQ
agent gains a mail tool that reads each user's own Outlook mail through Work IQ's
MCP server, as that user and read-only
([Work IQ mail](runtime-integration.md#work-iq-mail)). The platform plan then
creates Foundry's project connection `work-iq`, with OAuth identity passthrough,
and its OAuth client, the registration `<project>-work-iq`: a public client without
a secret, identifier URI or exposed scope, whose only redirect URI is Foundry's
consent-service URL, and which declares Work IQ's `WorkIQAgent.Ask` and Microsoft
Graph's `offline_access`. Before you enable it:

1. Enable the tenant for the Work IQ API once: a Global administrator runs
   `az ad sp create --id fdcc1f02-fc51-4226-8753-f668596af7f7`. A conflict means it
   is already done; preflight checks it.
2. In the [Microsoft 365 admin center](https://admin.microsoft.com), under
   **Copilot** > **Cost management**, set up usage-based billing for Copilot
   Credits: a spending policy, billed to an Azure subscription, that covers the
   **Work IQ API** and every user
   ([Microsoft's guide](https://learn.microsoft.com/copilot/implement/usage-based-billing-copilot-credits-setup)).
   A custom agent's Work IQ calls are always billed this way, even for users with a
   Microsoft 365 Copilot license
   ([Work IQ API costs](https://learn.microsoft.com/microsoft-365/copilot/extensibility/cost-considerations#work-iq-api)).
   Put the billing resource group outside the deployment's, which teardown deletes,
   and set a monthly limit. The runner cannot read spending policies: a user's
   first Work IQ answer shows that the policy covers them.
3. Give every user a Microsoft 365 mailbox.
4. Arrange consent. The `verify` phase grants `WorkIQAgent.Ask` tenant-wide as you,
   the assumed administrator; with `--manual-grants`, an administrator grants it at
   the `api_permissions_url` that `terraform -chdir=infra output work_iq` prints.
   Grant `offline_access` there too, or let each user consent to it where tenant
   policy allows.

Then set `work_iq_enabled = true` and rerun `deploy.py up`: the apply adds the
registration and connection, and the agent phase publishes a with-IQ version with
the mail tool. Turning it off deletes the connection, so rerun `up` through the
agent phase to republish the agent without the tool; the knowledge phase reminds
you until you do.

Each user then consents once, in the app: their first Work IQ question shows a
consent card with Foundry's link. The user signs in and consents; Foundry then
keeps a token per user and connection and refreshes it. Every Work IQ call
consumes Copilot Credits. The `verify` phase makes no Work IQ call: it checks the
`work_iq` output's tenant, connection and redirect URL and the `WorkIQAgent.Ask`
grant. Without a tenant that meets these prerequisites, Work IQ stays unverified.

## 6. Complete knowledge setup

With `WEB_IQ_API_KEY` exported, rerun `up`. The knowledge phase writes `knowledge_lakehouse_id`, the `Scenario` lakehouse's physical GUID, and the `scenario` entry of `knowledge_ontologies`, with `ScenarioOntology`'s physical GUID, into the **same** `terraform.tfvars` with the [knowledge helper](../.github/skills/deploy-platform/scripts/configure_knowledge.py), then saves an untargeted plan; approve it with `--approve knowledge=<sha12>`. By hand, set the same two values:

```hcl
knowledge_lakehouse_id = "<dev-Scenario-lakehouse-guid>"
knowledge_ontologies = {
  scenario = {
    ontology_id = "<dev-ScenarioOntology-guid>"
    description = "Current sites, assets, their status, and which site each asset belongs to."
  }
}
```

Leave this map empty only if you want document-only retrieval. Then run another normal plan/apply. By hand, give both commands the [Web IQ](#web-iq) key, as the runner does: a plan without it would remove the key the public-context source stores.

```sh
TF_VAR_web_iq_api_key="$WEB_IQ_API_KEY" terraform plan -out=knowledge.tfplan
# Review the plan output before continuing:
TF_VAR_web_iq_api_key="$WEB_IQ_API_KEY" terraform apply knowledge.tfplan
terraform output
```

Terraform looks up the lakehouse and configured ontologies in DEV without owning their definitions. It creates one indexed OneLake source per `knowledge_folders` entry (`<project>-documents` by default), the agents' knowledge base `<project>-knowledge` over the attached folder sources, the live ontology source in the check-only knowledge base `<project>-ontology-check`, the [Web IQ](#web-iq) public-context source and knowledge base, the agents' `knowledge-base`, `public-context` and `fabric-scenario` connections, and the Search identity's grants. An ID from TEST or another workspace fails the lookup. Keep the values set on subsequent runs; clearing them plans removal of knowledge objects, not a pause. No targeted apply is needed.

The `fabric-scenario` connection is a non-shared native Foundry Fabric IQ
`UserEntraToken` connection to the ontology's DEV MCP endpoint, reported by
`fabric_iq_connections`; step 7's with-IQ agent queries the ontology through it as
each signed-in user. The agents' knowledge base never holds an ontology: only
`verify` queries the check-only base, with your own token. Registration is not a
readiness claim ([ontology and knowledge](ontology-knowledge.md#two-retrieval-paths)).

Each **folder source** ingests asynchronously, checking its folder every five minutes. In your derived project, inspect status/errors with:

```sh
az rest --method get --resource https://search.azure.com \
  --url "$(terraform output -json knowledge_source_status_urls | jq -r .documents)"
```

Allow time for permission propagation; resolve any ingestion errors before relying on retrieval. Outputs identify the Foundry project, knowledge-base MCP connection, and OneLake lakehouse/Files URL. Upload additional documents into a knowledge folder in that lakehouse's **Files** area, not Tables. For document URLs in citations, set `includeReferenceSourceData: true` in the retrieval request's OneLake `knowledgeSourceParams`.

The `verify` phase then proves **DEV ready** with your own Search token, never a managed identity:

- Every folder source, attached or not, has ingested its folder.
- Live ontology retrieval through the check-only knowledge base returns `fabricOntology` activity and references for DEV's workspace and ontology, with **Pump B** and **North Works** in the live source data (or your `--expect` facts). An answer from a document does not count.
- Document retrieval from the agents' knowledge base cites the `documents` source. That base runs at minimal effort, so `verify` sends the question as a search intent.
- One public-only Web IQ search and one public-context retrieval succeed.
- With Work IQ, the `WorkIQAgent.Ask` grant and the `work-iq` connection exist; no Work IQ call is made.

Report DEV ready only after `verify` succeeds. The [runtime authentication and retrieval example](ontology-knowledge.md#supply-the-end-users-identity-at-query-time) shows the user header the live source needs.

Use the native DEV/TEST deployment pipeline and explicitly attach the plan when promoting items. Follow the separate [`cicd/` guide](../cicd/README.md) for reviewed releases and PROD. Promotion does not copy source data or repeat Terraform's DEV-only knowledge setup.

## 7. Create the agents and app

The template ships two [Foundry prompt agents](../agents/README.md), the variants
of one agent, and the [web app](../app/README.md) that asks them from Azure
Container Apps. Start after step 6 succeeds. [Runtime integration](runtime-integration.md)
explains the identity flow, access model and evidence.

| Variant | Agent name | Definition | Tools |
| --- | --- | --- | --- |
| Scenario assistant – with IQ | `<project>-with-iq` | [`agents/scenario-with-iq/`](../agents/scenario-with-iq/) | Fabric IQ ontology, as the user; Foundry IQ knowledge and Web IQ public context, as the project; Work IQ mail, as the user, only with `work_iq_enabled` |
| Scenario assistant – without IQ | `<project>-without-iq` | [`agents/scenario-without-iq/`](../agents/scenario-without-iq/) | None |

Both share a byte-identical `agent.json` (the model deployment and reasoning
effort) and `instructions.md`; only their tools differ, so the app can compare an
answer with IQ against one without. Rerun `up` on the same host, checkout and state,
with `WEB_IQ_API_KEY` still exported: the app's plan, like every plan after
knowledge setup, carries it.

```sh
set +x
python3 .github/skills/deploy-platform/scripts/deploy.py up
```

The `agent` and `app` phases do this; don't do it by hand:

| What it does | How | Approval |
| --- | --- | --- |
| Render each definition from Terraform's outputs, leaving out the Work IQ tool unless `work_iq_enabled`, and compare its digest with the agent's latest version | [`foundry_agent.py`](../.github/skills/deploy-platform/scripts/foundry_agent.py); `GET <project endpoint>/agents/<name>` | None |
| Create each missing agent, or publish a new version of a changed one, then read it back | `POST /agents`, `POST /agents/<name>/versions` | `agent-publish`, naming every agent it changes |
| Grant the app registration's Foundry sign-in scope tenant-wide, if missing | Microsoft Graph, as you | None; with `--manual-grants`, each user consents at first sign-in instead |
| Build the image from the committed files under `app/`, tagged with their digest, unless the registry has it | `az acr build --image app:<tag> --file app/Dockerfile --platform linux/amd64 app` | `app-build` |
| Plan the container app with the new tag; once you approve, write it as `app_image_tag` in `infra/terraform.tfvars` and apply | `terraform plan -var app_image_tag=<tag>`, `terraform apply` | `--approve app=<sha12>` |
| Check that the app answers `/healthz` and serves this deployment's sign-in registration | `GET /healthz`, `GET /api/config` | None |

A rerun publishes and builds nothing that hasn't changed. Change an agent in its
`agents/` folder or the app in `app/`, commit, and rerun `up`; the app phase refuses
uncommitted or untracked app files. The
[native request contract](../.github/skills/deploy-platform/references/native-requests.md#foundry-agents-and-the-app)
lists the requests behind both phases, for review and diagnosis. Where ACR Tasks is
not available, build and push the tag the runner printed yourself, then rerun:

```sh
az acr login --name "<registry>"
docker build --platform linux/amd64 --tag "<registry>.azurecr.io/app:<tag>" app
docker push "<registry>.azurecr.io/app:<tag>"
```

The app phase reports the app's URL, which `terraform -chdir=infra output app` also
prints. A healthy app is not an answered question. Open the URL and sign in with
your work account:

1. At first sign-in, consent to the app's access to Microsoft Foundry for you,
   unless an administrator granted it for everyone.
2. Ask **With IQ**: "Which assets are in maintenance, and at which site?" Expect
   **Pump B at North Works** from the live ontology, cited `[D1]`. The first Fabric
   IQ call shows a consent card with Microsoft's link: accept, then ask again.
   Fabric IQ and, with Work IQ, Work IQ each ask once per user; Foundry IQ and Web
   IQ use the project's identity and never ask.
3. Select **Ask both** to compare the answer with IQ against the one without, and
   ask a follow-up to continue the same conversation. A live answer can take a
   minute; the server waits at most 225 seconds.

This proves the route for your account only. Other users need an operator to grant
them a Foundry role and Fabric access from the
[access model](runtime-integration.md#access-model); the template assigns none.
Second-user isolation stays unverified until a second account proves it.

### Tracing

Every deployment traces the app and the agents to one Application Insights
resource (`terraform -chdir=infra output application_insights`), which accepts
Entra ID only. The app's server sends a span for each request but the health
probe, and an `invoke_agent` span for each question; Foundry's server-side trace
of the agent run continues it, so one transaction holds the request, the run and
its tool calls. Open them in the Foundry project's **Traces** view, which finds a
run by the response ID the app shows, or in Application Insights' transaction
search. Traces appear within a few minutes. Foundry's prompts, answers and tool
content sit in a protected table that only you, the deployer, can read
([traces and telemetry](runtime-integration.md#traces-and-telemetry)).

## Keep in mind

- **Isolation:** start each project with new state; never copy another project's tfvars, state, or `.terraform/`. Once deployed, retain state and keep `project_name` stable. Use an encrypted, access-controlled remote backend with a separate state key per project before team/CI use.
- **Identity lifecycle:** the deployment app registration shares that project's state with the platform. Full plans still require access to its Entra objects. Fabric resources depend on the identity module so Terraform tears them down before removing the login registration. Do not remove those dependencies, target-destroy the module, or delete the registration manually while Fabric resources still need it.
- **Secrets:** state/plans can contain provider-returned secrets; the Search admin key is read ephemerally on each run and is not stored. Keep state, PATs, tfvars, and debug logs out of Git. OneLake uses Entra authentication; runtime access uses managed identities and each signed-in user's own token. The web app holds no secret, Application Insights accepts no ingestion key, the Work IQ registration is a public client, and the Web IQ key lives only in Search. Commit `.terraform.lock.hcl`.
- **Scope/cost:** resources, notebook execution and retrieval are billable; endpoints, including the web app, are authenticated but public; ontology/deployment-plan/Foundry IQ features, the Fabric IQ agent tool, Web IQ and the serverless Search tier are preview (no SLA, no tier migration). Serverless bills compute per use, including each folder source's five-minute ingestion schedule. Semantic ranking and agentic retrieval use their standard plans: monthly free allowances, then pay-as-you-go. Only the DEV lakehouse's knowledge folders are indexed. Registered ontologies query their bound data live with end-user permissions; arbitrary tables/graphs/models are not automatically exposed. GlobalStandard model deployments bill per token, every Web IQ call is a billable search, and Work IQ calls consume Copilot Credits. TEST stays empty until promotion; the agents and app serve DEV only, and PROD provisioning is not included. Folder-source/embedding configuration changes rebuild the index.
- **Between sessions:** the app keeps one warm replica by default. Pause the Fabric capacity in the Azure portal and set `app = { min_replicas = 0 }` when idle; resume the capacity before use, and expect a cold start on the first question ([troubleshooting](runtime-integration.md#troubleshooting)).
- **Teardown:** use the [runner's `down` command](development.md#delete-the-environment) to delete everything in Terraform state, including the container app, the Foundry project with both agents, workspace data and Search ingestion assets. Retained Fabric workspaces, deleted Entra registrations and Web IQ portal credentials need manual cleanup.

# Manual preparation checklist

The [deployment runner](docs/development.md#deploy-deterministically) assumes
you administer the Azure subscription, Entra tenant and Fabric. It signs you in
to every CLI before it creates anything, grants consent and registers
subscription features as you, and stops with the exact grant only when an API
refuses for lack of permission. Administrators can
skip the permission items below; others can use them to arrange grants ahead
of time. Licenses, eligibility, term acceptance and Fabric tenant settings
still need you: the runner never changes tenant settings, and it points at them
when Fabric refuses. [Getting started](docs/getting-started.md#prerequisites) says
who acts on each item and how long it can take.

## Permissions and licenses

- [ ] Obtain **Owner**, or **Contributor + Role Based Access Control Administrator / User Access Administrator**, on the target Azure subscription.
- [ ] Obtain permission to register Azure resource providers and preview features in that subscription.
- [ ] Obtain Entra permission to create/manage app registrations, service principals, application owners and pre-authorized clients, and read the existing Power BI Service and Microsoft Foundry API (**Azure Machine Learning Services**) enterprise applications.
- [ ] Hold an Entra role that can grant tenant-wide consent for the [declared delegated Fabric permissions](docs/getting-started.md#3-bootstrap-the-fabric-user-auth-application) and the web app's delegated Foundry scope, such as Cloud Application Administrator, so the runner can grant them as you; otherwise arrange an administrator to grant them when the runner hands over the approval links.
- [ ] Obtain Fabric permission to create workspaces, connections and deployment pipelines; include your deployment user in `fabric_capacity_admins`.
- [ ] Decide who may use the app. Each additional user needs a Foundry role on the project, such as **Foundry User**, read access to the DEV ontology and its data and, with Work IQ, a mailbox and spending-policy coverage; Terraform grants Foundry access only to the deployer. See the [access model](docs/runtime-integration.md#access-model).
- [ ] If using Copilot, enable your GitHub Copilot subscription and organization access.
- [ ] Obtain GitHub access to create the derived repository, push its bootstrap branch and open its Codespace.

## Tenant and environment

- [ ] Select the Azure subscription and Entra tenant; use that tenant for Fabric, Search and Foundry.
- [ ] Have a tenant administrator confirm that the **Microsoft Power BI Service** and **Azure Machine Learning Services** (Microsoft Foundry API) enterprise applications exist.
- [ ] Have a Fabric administrator enable **Users can create ontology (preview) items**.
- [ ] Have a Fabric administrator enable **Users can create deployment plan (preview) items**.
- [ ] Have a Fabric administrator enable external-app OneLake access and service-principal Fabric APIs.
- [ ] Choose one Azure region that offers Foundry with the configured models, Azure Container Apps and Container Registry, and confirm Fabric CU quota, model quota (the `agent` and `chat` deployments share one) and Search tier availability/capacity. The [runtime preflight](docs/runtime-integration.md#preflight-is-not-readiness) checks the regional resource types.
- [ ] Confirm the subscription allows ACR Tasks, which build the app's image.
- [ ] For serverless Search, nothing to do as a subscription owner: the runner registers `Microsoft.Search/Serverless` and re-registers `Microsoft.Search` as you. With `--manual-grants`, an owner runs both commands it prints.
- [ ] Accept the platform's billable usage, preview features and authenticated public endpoints, including the web app's public HTTPS address. Plan to pause the Fabric capacity and set `app = { min_replicas = 0 }` between sessions.

## Repository and deployment host

- [ ] Create your repository with **Use this template** and push the selected bootstrap branch.
- [ ] Open its Codespace/devcontainer, or install the [documented local tools](docs/development.md); keep one deployment checkout and execution host.
- [ ] If using Copilot, sign in through its own login flow. GitHub CLI is already signed in inside a Codespace; elsewhere the runner asks you to run `gh auth login` first.
- [ ] Configure an encrypted, access-controlled Terraform backend and project-specific state key; grant the deployment identity backend read/write/locking access. For a single-user local-state demo, explicitly accept local state and arrange secure backup.
- [ ] Configure the six placeholders in the ignored `infra/terraform.tfvars`: `project_name`, `location`, `fabric_capacity_admins`, `github_owner`, `github_repository_name` and `github_branch_name`.
- [ ] Keep `knowledge_lakehouse_id = null` and `knowledge_ontologies = {}` until DEV bootstrap and binding succeed.
- [ ] Choose the deployment subscription before the first run; the runner signs you in up front.

## Optional Fabric GitHub synchronization

- [ ] Set `fabric_git_enabled = true` only if Git synchronization is wanted; it defaults to `false`.
- [ ] Have a Fabric administrator enable Git/GitHub synchronization and applicable cross-geography Git export.
- [ ] Create a fine-grained PAT for the derived repository with **Contents: Read and write**; obtain organization approval/SSO authorization where required.
- [ ] Store it as the repository's Codespaces secret `FABRIC_GITHUB_TOKEN`, or supply `TF_VAR_github_token` securely in the deployment shell; restart an existing Codespace after adding its secret.

## During deployment and first run

- [ ] Start the [deployment runner](docs/development.md#deploy-deterministically) with the selected subscription and backend, and complete its up-front Azure CLI sign-ins by entering each device code and completing MFA.
- [ ] Review and approve the initial identity-only Terraform plan.
- [ ] Consent: the runner grants it as you. Only if Entra refuses, have an administrator open the generated `api_permissions_url`, review the declared scopes and select **Grant admin consent**.
- [ ] If the runner asks, sign in again with the generated deployment scope (it normally acquires that token silently); keep the same deployment user.
- [ ] Review and approve the untargeted platform Terraform plan.
- [ ] Confirm DEV item/notebook permissions, including `Item.ReadWrite.All` and `Item.Execute.All`; Git synchronization also requires workspace **Admin**.
- [ ] If Fabric service-principal access is restricted to security groups, have the administrator include the provisioned Search identity in an allowed group.
- [ ] If plan attachment is unavailable, explicitly approve the runner's `--bootstrap-fallback notebooks` path.
- [ ] Review the generated DEV ontology bindings before promotion; commit through Fabric source control when Git synchronization is enabled.
- [ ] Review and approve the untargeted knowledge Terraform plan.
- [ ] Commit any change under `agents/` or `app/`; the runner publishes the agent definitions in the checkout and builds only committed app files.
- [ ] Approve the agent publication (`agent-publish`) when requested.
- [ ] Approve the image build (`app-build`) and the saved app plan (`--approve app=<sha12>`) when requested. The runner grants the app's Foundry sign-in scope as you; with `--manual-grants`, have an administrator grant it at the reported link, or let each user consent at first sign-in.
- [ ] Open the web app URL, sign in and ask the sample question **With IQ**; accept the Fabric IQ consent card once, ask again and confirm **Pump B at North Works**. Select **Ask both** to compare the answer without IQ.
- [ ] For each additional user, assign a Foundry role on the project and read access to the required DEV Fabric items; have them sign in and repeat the check. Until a second user has, isolation is not demonstrated.
- [ ] Retain and securely back up the deployment state.

## Web IQ

- [ ] Enable Web IQ for the Azure subscription; request enablement from your Microsoft representative if required.
- [ ] Accept the [Web IQ terms](https://www.microsoft.com/en-us/webiq/webiq-legal), usage charges, global public endpoint and public-only query policy.
- [ ] Set `web_iq_terms_accepted = true` in the same ignored tfvars file after accepting the terms.
- [ ] After provisioning, read `terraform -chdir=infra output web_iq` and sign in to [Web IQ Profile Management](https://webiq.microsoft.ai/profiles).
- [ ] Select the deployed subscription/resource, finish linking it, and select its resulting **Web IQ AppID**.
- [ ] Create a Web Search-enabled API key for that AppID and inject it as `WEB_IQ_API_KEY` into the deployment process before knowledge setup, and in every later run; keep it out of chat, files, command history and the browser app.
- [ ] Rerun the runner to verify an authenticated public search and a cited public-context retrieval.
- [ ] In the app, check a cited public-context line and that its query in the tool timeline holds public terms only; review citations, freshness and conflicting-source handling for your scenario ([Web IQ](docs/runtime-integration.md#web-iq)).
- [ ] After rotating the key, export the new one and increment `web_iq_api_key_version`.

## Work IQ (optional)

Only with `work_iq_enabled = true`; see [Work IQ](docs/getting-started.md#work-iq).

- [ ] Have a Global administrator enable the tenant for the Work IQ API once: `az ad sp create --id fdcc1f02-fc51-4226-8753-f668596af7f7`.
- [ ] Have a billing administrator set up a Copilot Credits spending policy that covers the **Work IQ API** and every user; it is required even for users with a Microsoft 365 Copilot license.
- [ ] Give every user a Microsoft 365 mailbox, and put any sample mail into it at least a day before you rely on it.
- [ ] Arrange administrator consent to the Work IQ registration's `WorkIQAgent.Ask` and `offline_access`: the runner grants `WorkIQAgent.Ask` as you; `offline_access` needs an administrator at the `work_iq` output's `api_permissions_url` or each user's consent.
- [ ] Set `work_iq_enabled = true` and rerun the runner through the agent phase; turning it off again needs the same rerun.
- [ ] Have each user consent once, through the link the app shows on their first Work IQ question. Without a tenant that meets these items, Work IQ stays unverified.

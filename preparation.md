# Manual preparation checklist

The [deployment runner](docs/development.md#deploy-deterministically) assumes
you administer the Azure subscription, Entra tenant, Fabric and the Power
Platform environment. It signs you in to every CLI before it creates anything,
grants consent and registers subscription features as you, and stops with the
exact grant only when an API refuses for lack of permission. Administrators can
skip the permission items below; others can use them to arrange grants ahead
of time. Licenses, eligibility, term acceptance and Fabric tenant settings
still need you: the runner never changes tenant settings, and it points at them
when Fabric refuses.

## Permissions and licenses

- [ ] Obtain **Owner**, or **Contributor + Role Based Access Control Administrator / User Access Administrator**, on the target Azure subscription.
- [ ] Obtain permission to register Azure resource providers and preview features in that subscription.
- [ ] Obtain Entra permission to create/manage app registrations, service principals, application owners and pre-authorized clients, and read the existing Power BI Service enterprise application.
- [ ] Hold an Entra role that can grant tenant-wide consent for the [declared delegated Fabric permissions](docs/getting-started.md#3-bootstrap-the-fabric-user-auth-application), such as Cloud Application Administrator, so the runner can grant it as you; otherwise arrange an administrator to grant it when the runner hands over the approval link.
- [ ] Obtain Fabric permission to create workspaces, connections and deployment pipelines; include your deployment user in `fabric_capacity_admins`.
- [ ] Have a Power Platform administrator grant environment access, **Environment Maker** permissions for apps/connections, and the [agent author](https://learn.microsoft.com/en-us/microsoft-copilot-studio/environments-first-run-experience) Dataverse security role for Studio authoring.
- [ ] Have your Microsoft 365 administrator assign a publish-capable [Copilot Studio user license](https://learn.microsoft.com/en-us/microsoft-copilot-studio/requirements-licensing) and provision tenant billing/capacity.
- [ ] Obtain **Power Apps Premium** or sufficient **Managed Application Copilot Credits** for each app user.
- [ ] If using Copilot, enable your GitHub Copilot subscription and organization access.
- [ ] Obtain GitHub access to create the derived repository, push its bootstrap branch and open its Codespace.
- [ ] For repository-bound Managed Runtime apps, use a GitHub Enterprise Cloud organization and have its administrator install/authorize [Microsoft Managed Apps](https://github.com/marketplace/microsoft-managed-apps) for the repository.

## Tenant and environment

- [ ] Select the Azure subscription and Entra tenant; use that tenant for Fabric, Search and Power Platform.
- [ ] Have a tenant administrator confirm that the **Microsoft Power BI Service** enterprise application exists.
- [ ] Have a Fabric administrator enable **Users can create ontology (preview) items**.
- [ ] Have a Fabric administrator enable **Users can create deployment plan (preview) items**.
- [ ] Have a Fabric administrator enable external-app OneLake access and service-principal Fabric APIs.
- [ ] Select a **non-Default Power Platform environment with Dataverse**; record its GUID and Org URL from Power Apps (**Settings > Session details**) or `dotnet pac env list` / `dotnet pac env who`.
- [ ] Have a Power Platform administrator allow the **Microsoft Copilot Studio**, **Fabric IQ** and **Azure AI Search / Foundry IQ** connectors and their required operations.
- [ ] Choose the Azure region and confirm Fabric CU quota, model availability/quota, and Search tier availability/capacity.
- [ ] For serverless Search, nothing to do as a subscription owner: the runner registers `Microsoft.Search/Serverless` and re-registers `Microsoft.Search` as you. With `--manual-grants`, an owner runs both commands it prints.
- [ ] Accept the platform's billable usage, preview features and authenticated public endpoints.

## Repository and deployment host

- [ ] Create your repository with **Use this template** and push the selected bootstrap branch.
- [ ] Open its Codespace/devcontainer, or install the [documented local tools](docs/development.md); keep one deployment checkout and execution host.
- [ ] If using Copilot, sign in through its own login flow. GitHub CLI is already signed in inside a Codespace; elsewhere the runner asks you to run `gh auth login` first.
- [ ] Configure an encrypted, access-controlled Terraform backend and project-specific state key; grant the deployment identity backend read/write/locking access. For a single-user local-state demo, explicitly accept local state and arrange secure backup.
- [ ] Configure the six placeholders in the ignored `infra/terraform.tfvars`: `project_name`, `location`, `fabric_capacity_admins`, `github_owner`, `github_repository_name` and `github_branch_name`.
- [ ] Keep `knowledge_lakehouse_id = null` and `knowledge_ontologies = {}` until DEV bootstrap and binding succeed.
- [ ] Choose the deployment subscription and the Power Platform environment before the first run; the runner signs you in to both up front.

## Optional Fabric GitHub synchronization

- [ ] Set `fabric_git_enabled = true` only if Git synchronization is wanted; it defaults to `false`.
- [ ] Have a Fabric administrator enable Git/GitHub synchronization and applicable cross-geography Git export.
- [ ] Create a fine-grained PAT for the derived repository with **Contents: Read and write**; obtain organization approval/SSO authorization where required.
- [ ] Store it as the repository's Codespaces secret `FABRIC_GITHUB_TOKEN`, or supply `TF_VAR_github_token` securely in the deployment shell; restart an existing Codespace after adding its secret.

## During deployment and first run

- [ ] Start the [deployment runner](docs/development.md#deploy-deterministically) with the selected subscription, backend and Power Platform environment, and complete its up-front sign-ins (Azure CLI, PAC and `ms`) by entering each device code and completing MFA.
- [ ] Review and approve the initial identity-only Terraform plan.
- [ ] Consent: the runner grants it as you. Only if Entra refuses, have an administrator open the generated `api_permissions_url`, review the declared scopes and select **Grant admin consent**.
- [ ] If the runner asks, sign in again with the generated deployment scope (it normally acquires that token silently); keep the same deployment user.
- [ ] Review and approve the untargeted platform Terraform plan.
- [ ] Confirm DEV item/notebook permissions, including `Item.ReadWrite.All` and `Item.Execute.All`; Git synchronization also requires workspace **Admin**.
- [ ] If Fabric service-principal access is restricted to security groups, have the administrator include the provisioned Search identity in an allowed group.
- [ ] If plan attachment is unavailable, explicitly approve the runner's `--bootstrap-fallback notebooks` path.
- [ ] Review the generated DEV ontology bindings before promotion; commit through Fabric source control when Git synchronization is enabled.
- [ ] Review and approve the untargeted knowledge Terraform plan.
- [ ] Approve Studio agent creation, IQ connection creation, publication and Managed Runtime app registration when requested.
- [ ] Open the IQ connection sign-in links and complete **Fix connection** for both Fabric IQ and Foundry IQ.
- [ ] For each additional user, share both the agent and app, grant read access to the required DEV Fabric items, and assign **Search Index Data Reader** on the deployed Search service.
- [ ] For each app user, open **Power Apps > selected environment > Connections** and create or reuse their own **Connected Microsoft Copilot Studio** connection.
- [ ] Run `ms app dev`, open its App Player URL and select **Check connection**.
- [ ] For each app user, follow the agent's Studio **user-connections** link and authorize their own Fabric IQ and Foundry IQ connections.
- [ ] Repeat **Check connection** and confirm the fresh challenge; ask the sample question and confirm **Pump B at North Works**.
- [ ] Review and commit `app/src/config.ts` and `ms.config.json`; retain and securely back up the deployment state.

## Web IQ

- [ ] Enable Web IQ for the Azure subscription; request enablement from your Microsoft representative if required.
- [ ] Accept the [Web IQ terms](https://www.microsoft.com/en-us/webiq/webiq-legal), usage charges, global public endpoint and public-only query policy.
- [ ] Set `web_iq_terms_accepted = true` in the same ignored tfvars file after accepting the terms.
- [ ] After provisioning, read `terraform -chdir=infra output web_iq` and sign in to [Web IQ Profile Management](https://webiq.microsoft.ai/profiles).
- [ ] Select the deployed subscription/resource, finish linking it, and select its resulting **Web IQ AppID**.
- [ ] Create a Web Search-enabled API key for that AppID and inject it as `WEB_IQ_API_KEY` into the deployment process; keep it out of chat, files, command history and the browser app.
- [ ] Rerun the runner to verify an authenticated public search.
- [ ] Before using Web IQ in the agent/app, separately approve and verify its [native Studio integration](docs/runtime-integration.md#web-iq), citations, freshness and conflicting-source handling.

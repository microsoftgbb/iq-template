# Prepare optional Web IQ

Web IQ is an explicit opt-in, not part of the default Fabric/Foundry IQ route.
Terraform provisions its Azure billing account; it does not create the Web IQ
portal application profile or connect the Studio agent.

## Prerequisites and approval

- Use a **paid Azure subscription enabled for Web IQ**. Azure credits do not
  work. If creation reports that Web IQ is not enabled, ask your Microsoft
  representative for access; registering the provider is not sufficient.
- Review and accept the [Web IQ Terms of Use](https://www.microsoft.com/en-us/webiq/webiq-legal)
  and usage charges. The account is `Microsoft.WebIQ/accounts`, kind
  `WebIQ.Grounding`, SKU `G3`, API `2026-06-01-preview`, location `global`.
  The resource group's region does not provide regional Web IQ residency.
- Web IQ runs on a Microsoft-hosted, multi-tenant **public HTTPS endpoint**.
  It does not support Private Link, private endpoints or VNet integration.
  Send only approved public query fields, never private business data.

In the derived project's ignored `infra/terraform.tfvars`, set:

```hcl
web_iq_enabled        = true
web_iq_terms_accepted = true
```

Use the existing deployment runner and the same backend/state. Review its normal
untargeted platform/knowledge plan; do not target Web IQ or create another
deployment wrapper. Terraform registers `Microsoft.WebIQ` only when enabled and
creates the account with statistics disabled. Leave both flags false to retain
the default core without a Web IQ dependency. Disabling an already deployed
account proposes its deletion, which requires a reviewed destructive plan.
The pinned AzAPI 2.13 provider does not contain this resource's embedded schema,
so schema validation is disabled only on the Web IQ resource; the pinned ARM
API still validates its request. Other resources retain their existing checks.

## Manual portal steps

1. After provisioning, read `terraform -chdir=infra output web_iq`. Sign in to
   [Web IQ Profile Management](https://webiq.microsoft.ai/profiles) using the
   account with access to that subscription.
2. Select the deployed subscription and Web IQ resource, and finish linking
   them. Confirm that the resulting **Web IQ AppID** is selected. An Azure
   resource ID, an Entra application/client ID and a Web IQ AppID are different
   identifiers.
3. For the pilot, create an API key for **that resource's AppID** with Web Search
   access. Supply it only as `WEB_IQ_API_KEY` in the deployment process's
   environment, preferably injected by a secret manager. Never put it in chat,
   shell command arguments/history, tfvars, `.env`, source control or the app.
4. Rerun the deployment runner. Its `verify` phase checks the Azure account and
   issues one billable, fixed public query, **Microsoft Web IQ documentation**.
   It requires grounding passages and usable source URLs. It records counts and
   the presence of optional freshness metadata, not keys or retrieved content.
   Missing onboarding is a waiting state; denied, malformed or empty results
   fail explicitly. There is no automatic billable-query retry.

These manual actions cannot be replaced by subscription Owner permissions,
Azure provider registration or `--yes`. No documented provisioning API for
portal AppID linking or key creation was established by the pilot.

For production authentication, the documented alternative is to bind an
approved Entra application's client ID to the Web IQ AppID in the portal and
use app-only tokens for `https://api.microsoft.ai/.default`. Availability can
require Microsoft enablement. Do not create credentials on the Fabric deployment
application or reuse its delegated-user token. The runner's pilot probe uses
only the explicitly supplied API key, not this production authentication route.

## Agent/app integration is a separate gate

A successful public search proves API access with the supplied credential, not
that the credential belongs to the Terraform account; the operator confirms
that association in the portal. It also does not prove source freshness,
conflict resolution, scenario answers, or a Web IQ tool inside the Studio agent.

The documented native MCP endpoint is `https://api.microsoft.ai/v3/mcp`.
Before adding it to the agent, establish an approved native Studio
connection/tool, its account-scoped allowed tools, authentication and governance,
then verify citations, freshness, conflicting sources and the consuming app.
Do not install an authoring MCP server and call it an agent connection, enable
ungoverned web grounding, put an API key in the browser, switch the existing IQ
tools to Maker mode, or add a backend to bypass the integration gate.

The shipped Studio agent and app continue to use their Fabric IQ/Foundry IQ
tools; provisioning this opt-in does **not** silently add a third runtime tool.
Live query evidence is distinct from replay or mocked test evidence.

## Pilot evidence

The isolated `iq-pilot` account was created and independently read back as
`Succeeded`, with global location, G3 and statistics disabled. The subscription
accepted the documented account creation request. The exact new Terraform
resource was then imported and applied in the isolated pilot, and a second full
pilot plan reported no changes. Fabric, Search, Foundry and the template's
deployment state were not deployed or changed for this pilot.

**Still pending:** portal resource/AppID linking, a credential, an authenticated
Web IQ search and native Studio/app integration. The pilot account and resource
group are retained; deleting them is a separate explicit operation.
Their local Terraform state is retained in the isolated pilot checkout outside
this template. Preserve it securely before removing the pilot checkout/session;
losing local state does not delete the Azure resources.

## Product references

- [Deploy with Azure CLI](https://learn.microsoft.com/en-us/web-iq/get-started/deploy-azure-cli)
- [Portal onboarding](https://learn.microsoft.com/en-us/web-iq/get-started/deploy-azure-portal)
- [Authentication](https://learn.microsoft.com/en-us/web-iq/get-started/authentication)
- [Web Search request/response contract](https://learn.microsoft.com/en-us/web-iq/reference/web-search)
- [Native MCP tools](https://learn.microsoft.com/en-us/web-iq/integrations/mcp)
- [Eligibility, networking and identity FAQ](https://learn.microsoft.com/en-us/web-iq/resources/faq)

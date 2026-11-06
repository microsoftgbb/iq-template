# Foundry hosted agent and web app

The shell is **Fabric -> Foundry IQ -> hosted agent -> web app**, with Work IQ
and additional Fabric capabilities as prescribed opt-ins. The platform always
deploys a Web IQ account; connecting it to the agent is a separate approval and
verification gate.

The template ships both runtime pieces:

- The [scenario assistant](../agents/scenario-assistant/README.md): a Python
  Microsoft Agent Framework agent that runs as a
  [Foundry hosted agent](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents).
  Its project toolbox has two tools: native Fabric IQ for live ontology facts,
  queried as the signed-in user, and the Foundry IQ knowledge base for documents.
- The [web app](../app/): a single-page browser app and a small Node server in
  Azure Container Apps. The server serves the app and relays each question to
  the agent's Responses endpoint with the user's own token.

Terraform owns their registry, telemetry, Container Apps environment,
identities and browser sign-in registration. The runner's `agent` and `app`
phases ([getting started step 7](getting-started.md#7-create-the-agent-and-app))
build the images and publish the toolbox, the agent version and the Container
App, outside Terraform state.

Successful `agent` and `app` phases prove the route for the deploying user
only: a fresh challenge through the agent, live ontology facts and the starter
document in its tools' own output, and another fresh challenge relayed by the
deployed app. Browser sign-in, a second user's denial and isolation, and other
users' access remain unproven, so report "verified for one user", not a ready
shell.

Use [runtime-integration](../.github/skills/runtime-integration/SKILL.md) for the
preflight, the remaining evidence and diagnosis. An explicitly chosen derived
project is the right place for live verification, not this reusable template.

## Web IQ

Terraform provisions a global `Microsoft.WebIQ/accounts` resource,
kind `WebIQ.Grounding`, SKU `G3`, API `2026-06-01-preview`, with statistics
disabled. The service is Microsoft-hosted and multi-tenant at a public HTTPS endpoint,
without Private Link or VNet integration. The resource group's region does not
provide regional Web IQ residency. The pinned AzAPI provider lacks its embedded
schema, so only this resource disables local schema validation; ARM still
validates requests.

Follow the [manual checklist](../preparation.md#web-iq) for terms,
portal resource/AppID linking and credentials. Terraform does not complete that
portal onboarding. The runner's `verify` phase uses `WEB_IQ_API_KEY` for one
billable, fixed public-only search, without automatic retries. It requires cited
grounding passages and records result counts and the presence of optional
freshness metadata, not keys or retrieved content. The operator confirms that
the credential belongs to the deployed resource; the probe does not establish
that association or scenario-level freshness/conflict handling. Missing terms
acceptance blocks deployment; missing account outputs or authenticated-search
evidence block verification rather than silently skipping Web IQ.

The native MCP endpoint is `https://api.microsoft.ai/v3/mcp`; exposed tools depend
on the account's allowed services. The shipped hosted agent has no Web IQ tool.
Adding one requires a separately approved toolbox tool and project connection,
authentication, governance and consuming-app proof. An authoring MCP registration
or successful REST search is not that integration. Keep the agent answering only
from its connected tools; do not add a backend or browser credentials to bypass
a blocked connection.

Production Entra authentication requires manually binding an approved
application's client ID to the Web IQ AppID in the portal, then obtaining app-only
tokens for `https://api.microsoft.ai/.default`; availability can require Microsoft
enablement. Do not create credentials on the Fabric deployment application or the
web app's sign-in registration, or reuse a delegated-user token. The runner's probe
supports only the explicitly supplied API key.

## Runtime at a glance

[![A signed-in user's browser gets a Foundry token through the web app's single-page sign-in registration. The Container App's proxy forwards only that token and the question to the hosted agent's Responses endpoint. Foundry authorizes the user and keeps their conversation private; the agent's toolbox queries the live Fabric IQ ontology as that user and retrieves documents from the Foundry IQ knowledge base with the project's managed identity.](diagrams/agent-runtime.svg)](diagrams/agent-runtime.svg)

Arrows show invocation direction; answers return through the same agent and
proxy. This is the intended authorization contract, not evidence that every
user or deployment has passed the checks below. The Search knowledge base's
separate live-source token contract is shown in
[knowledge retrieval](ontology-knowledge.md#two-retrieval-paths).

[Editable Excalidraw source](diagrams/agent-runtime.excalidraw) | [All diagrams](diagrams/README.md) | [Icon sources](diagram-authoring.md#official-icon-sources)

## What runs where

`<prefix>` is `<project_name>-<suffix>`, where Terraform generates the
six-character suffix once per deployment.

| Component | Owner | Contract |
| --- | --- | --- |
| Container registry `cr<project_name without hyphens><suffix>` | Terraform, [`infra/agent.tf`](../infra/agent.tf) | Holds the `scenario-assistant` and `web` images. The admin user is disabled and `container_registry_sku` defaults to `Basic`. The project's managed identity and the web app's identity can only pull. |
| Log Analytics `log-<prefix>`, Application Insights `appi-<prefix>` | Terraform | Container App logs and the end-to-end traces of the web app, Agent Service and the hosted agent; see [observability](#observability). |
| Project connections `container-registry` and `app-insights` | Terraform | Agent Service pulls images with the project's managed identity and exports traces. The Application Insights connection string is sent write-only and never stored in state. |
| Toolbox `<project_name>-tools` | Runner | A version with the two tools in [`hosted-agent.json`](../agents/scenario-assistant/hosted-agent.json), made the default. |
| Hosted agent `<project_name>-assistant` | Runner | Versions of the `scenario-assistant` image with 0.5 CPU, 1 GiB and the Responses protocol 2.0.0. The agent has its own Microsoft Entra agent identity. |
| Container Apps environment `cae-<prefix>` | Terraform, [`infra/app.tf`](../infra/app.tf) | Consumption workload profile; logs go to the Log Analytics workspace. |
| Web identity `id-<prefix>-web` | Terraform | User-assigned identity with `AcrPull` on the registry and nothing else. |
| Sign-in registration `<project_name>-web` | Terraform | Single-tenant public single-page client. Its redirect URI is the app's URL, plus any opt-in `web_app_local_redirect_uris`; its only API permission is the delegated Foundry scope `https://ai.azure.com/user_impersonation`, and it has no secret or certificate. |
| Container App `ca-<prefix>-web` | Runner | The `web` image with external HTTPS ingress to port 8080, health probes on `/healthz`, 0 to 2 replicas and the seven settings below. |

The Container App's settings come only from Terraform outputs:
`AGENT_RESPONSES_URL` and `AGENT_NAME` from `hosted_agent`, and
`ENTRA_CLIENT_ID`, `ENTRA_TENANT_ID` and `AGENT_SCOPE` from `web_app`, and
`APPLICATIONINSIGHTS_CONNECTION_STRING` from the sensitive
`application_insights_connection_string` output, with `OTEL_SERVICE_NAME`
set to `<project_name>-web`. None is a secret: the connection string
identifies the Application Insights resource and is not a credential. The runner builds each image with `az acr build` from the published
commit and tags it with the first 16 characters of its directory's Git tree
hash, so unchanged source is never rebuilt or redeployed.

## Request path

1. **Sign-in.** The browser reads the non-secret `/config.json` and signs the
   user in with MSAL's redirect flow and PKCE, keeping tokens in
   `sessionStorage`. It requests the delegated Foundry scope, for which the
   `app` phase grants tenant-wide consent.
2. **Relay.** The browser posts the question, at most 2,000 characters, to
   `/api/responses` with the user's bearer token. The server forwards only that
   token and the narrowed body `{input, stream: false, previous_response_id?}`
   to the one configured `AGENT_RESPONSES_URL`, refuses redirects and waits up
   to `AGENT_TIMEOUT_SECONDS` (220 by default). It logs only the method, route,
   status and duration, never headers, tokens or questions, and traces the
   request and the agent call the same way (see [observability](#observability)). Its Content
   Security Policy allows connections only to the app itself and Microsoft
   Entra sign-in.
3. **Authorize and isolate.** Foundry authorizes the token on the agent
   endpoint (see the [access model](#access-model)). Its
   [documented isolation](https://learn.microsoft.com/azure/foundry/agents/how-to/isolate-sessions-per-user)
   keeps each user's conversations, sessions and stored data private to that
   identity. The app keeps only the latest response ID in memory, to send a
   follow-up with `previous_response_id`; it stores no conversation.
4. **Run the agent.** Agent Service runs the container, which serves the
   Responses protocol on port 8088, in a per-session sandbox. The agent
   authenticates as its own agent identity: it calls the project's `chat`
   deployment through `FoundryChatClient` and loads the project toolbox
   through `FoundryToolbox`. Foundry injects `FOUNDRY_PROJECT_ENDPOINT`; the
   runner sets `AZURE_AI_MODEL_DEPLOYMENT_NAME` and `TOOLBOX_ENDPOINT` from
   `hosted-agent.json`, which also keeps message content out of traces.

### Toolbox tools

The toolbox receives the platform's call context for the signed-in user. Each
tool reaches its source through its own project connection:

| Tool (`server_label`) | Type and project connection | Identity at the source |
| --- | --- | --- |
| `scenario_ontology` | `fabric_iq_preview` through `fabric-scenario`: `RemoteTool`, `UserEntraToken`, Power BI API audience, the bound ontology's Fabric MCP endpoint. | **The signed-in user.** Fabric applies their workspace and item permissions. |
| `knowledge_base` | `mcp` through `knowledge-base`: `RemoteTool`, `ProjectManagedIdentity`, audience `https://search.azure.com/`, the knowledge base's MCP endpoint, allowing only `knowledge_base_retrieve`. | **The project's managed identity**, which has Search Index Data Reader. |

Both tools set `require_approval: never`, because the app has no approval flow.
The project identity cannot supply the end-user token that the knowledge base's
live `fabricOntology` source needs. The
[instructions](../agents/scenario-assistant/instructions.md) therefore send
every current fact to Fabric IQ and use the knowledge base only for guidance
documents, naming each one inline. Documents are not trimmed per user: anyone
who can invoke the agent can receive content from every indexed document, so
publish only documents that all agent users may read.

A message that is exactly `connection proof <uuid>` returns
`AGENT_PROOF|connection proof <uuid>` from the agent's middleware without
calling the model or a tool. The toolbox still loads first, so a correct reply
also shows that the agent can load its toolbox.

## Access model

| Principal | Needs | Granted by |
| --- | --- | --- |
| Each app user | `Microsoft.CognitiveServices/accounts/AIServices/endpoints/interact/action` on the Foundry project or the agent: **Foundry Agent Consumer** (least privilege) or Foundry User. Read access to the DEV ontology and the lakehouse data it binds, because Fabric IQ queries run as them. | An operator. The template assigns no end-user roles. |
| Deployer | Foundry User to publish the toolbox and agent versions and to invoke the agent, plus the subscription and Entra rights in [getting started](getting-started.md#prerequisites). | Terraform assigns Foundry User on the Foundry account (`deployer_foundry_user` in [`infra/foundry.tf`](../infra/foundry.tf)); the rest is yours. |
| Hosted agent identity | Model inference and session storage in its own project, which it has implicitly; no role assignment for this standard case. | Agent Service. The runner records its principal ID. |
| Project managed identity | `AcrPull` on the registry; Search Index Data Reader for the knowledge base. | Terraform |
| Web app identity | `AcrPull` on the registry only: no Foundry, Search or Fabric access. | Terraform |

Grant a user the least-privilege role at project scope with Azure CLI; the
Azure portal currently offers this role only at account scope. The role
definition ID avoids name differences while Foundry roles are renamed:

```sh
az role assignment create --assignee "<user-principal-name-or-object-id>" \
  --role "eed3b665-ab3a-47b6-8f48-c9382fb1dad6" \
  --scope "$(terraform -chdir=infra output -raw foundry_project_id)"
```

Append `/agents/<hosted_agent.name>` to the scope to grant only this agent, and
allow a few minutes for the assignment to apply. The web app never holds an
application credential or a shared token: Foundry decides who can invoke the
agent, and Fabric decides which ontology data each user can read.

## Evidence required for a ready shell

| Gate | Required evidence | Checked by |
| --- | --- | --- |
| Source and build | Images built from the reviewed, published commit. The toolbox's default version and the agent's latest version match the rendered definition, and that version is `active`. | `agent` and `app` phases |
| Agent | The exact fresh challenge through the agent endpoint as the caller. | `agent` phase |
| Live ontology | The `scenario_ontology` tool call's own output contains the expected seeded facts (`Pump B` and `North Works`, or `--expect`). Answer text alone, or a fact copied from `starter.txt`, is not live evidence. | `agent` phase |
| Documents | The `knowledge_base` tool call's output references `starter.txt`, after successful ingestion. | `agent` phase |
| App | `/healthz`, a `/config.json` that matches this deployment, and the fresh challenge relayed through `/api/responses` with the deployer's token. | `app` phase |
| Browser sign-in | A person signs in at the app URL and gets **Check connection**'s fresh challenge, a live answer and a follow-up in the same conversation. | You |
| End-user authorization | A user without a Foundry role gets HTTP 403; a user without Fabric access gets an explicit unavailable-source answer, never a document substitute. | You, with a second account |
| Isolation | A second user cannot continue or read the first user's conversation. | You, with a second account |
| Repeatability | Rerunning `up` with the same source builds, publishes and redeploys nothing. | A rerun of `up` |

The runner asks each evidence question up to twice, because a model can skip a
tool, and decides from the tool calls' structured output, never from answer
text. It saves the responses as owner-only files in ignored
`.artifacts/deploy/evidence/`. Keep evidence private with the reviewed revision
and exact target, and keep deployment IDs, accounts, tokens and logs out of
reusable definitions and guidance. Do not claim readiness from a healthy
container, a successful registration or a correct-sounding answer.

The [deployment skill](../.github/skills/deploy-platform/SKILL.md) reports
**DEV ready** after `verify`, **agent ready** after `agent` and **app verified
for the deploying user** after `app`. None of these is a ready shell without
the remaining evidence above. Work IQ and Web IQ are not part of this route and
must not be silently enabled.

## Preflight is not readiness

The [read-only preflight helper](../.github/skills/runtime-integration/scripts/preflight_runtime.py)
checks a chosen derived checkout: its origin and that it is neither the
template nor a fork, Git >= 2.27.0, Azure CLI >= 2.80.0 for ACR builds and
hosted agents, the chosen subscription, tenant and delegated user, provider
credential overrides, that `--location` is a region with documented hosted
agents, and the exact Fabric SKU's available regional CUs. Its successful
result is **candidate**. It never signs in or provisions, and it leaves every
runtime claim unverified. The runner's `preflight` phase repeats the region
check before anything is created.

The region list is a dated snapshot of the
[hosted agent regions](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents#region-availability).
If Microsoft adds your region, update `HOSTED_AGENT_REGIONS` in a reviewed
change; the runner refuses an unlisted region. Check Fabric's native
`CapacityQuota` record, not a generic discovery fallback that says "No Limit"
beside a zero. Search's published region and tier restrictions, model quota and
actual service capacity are separate checks. A partial provisioning failure can
leave working resources behind; retain the same state and obtain a fresh plan
approval before changing regions or replacing anything.

Infrastructure creation and native bootstrap still follow [getting
started](getting-started.md), with one state, reviewed plans, administrator
consent and user sign-in. Runtime sign-in is a different flow; do not expand
the deployment registration into a credential broker.

## Local development

Run the app's checks from `app/`, as CI does:

```sh
cd app
npm ci
npm run lint && npm run build && npm test
```

For a development loop against a deployed agent, start the app server with the
Container App's settings, then Vite in a second terminal. Vite serves the app
on port 5173 and proxies `/api` and `/config.json` to the server on port 8080:

`npm start` preloads [`server/telemetry.mjs`](../app/server/telemetry.mjs);
without `APPLICATIONINSIGHTS_CONNECTION_STRING` it loads and exports nothing.

```sh
cd app
hosted="$(terraform -chdir=../infra output -json hosted_agent)"
web="$(terraform -chdir=../infra output -json web_app)"
AGENT_RESPONSES_URL="$(jq -r .responses_url <<< "$hosted")" AGENT_NAME="$(jq -r .name <<< "$hosted")" \
ENTRA_CLIENT_ID="$(jq -r .client_id <<< "$web")" ENTRA_TENANT_ID="$(jq -r .tenant_id <<< "$web")" \
  npm start
# In a second terminal, from app/:
npm run dev
```

The devcontainer forwards both ports; keep them private. By default the sign-in
registration accepts only the Container App's URL as a redirect URI, so browser
sign-in from `localhost` fails with `AADSTS50011`. To sign in locally, set
`web_app_local_redirect_uris = ["http://localhost:5173/"]` in your tfvars and
rerun `up`; the reviewed plan adds that address to the registration. Open the
app at that address, including from Codespaces through a private local port
forward. A forwarded Codespaces URL is never registered. Without the variable,
you can still check layout, configuration loading and the proxy's request
validation locally, then push and rerun the `app` phase to use the app URL.
Change the registration only through Terraform, never in the portal.

To separate an app problem from an agent problem, call the agent as yourself
with a fresh challenge:

```sh
url="$(terraform -chdir=infra output -json hosted_agent | jq -r .responses_url)"
challenge="connection proof $(python3 -c 'import uuid; print(uuid.uuid4())')"
az rest --method post --resource https://ai.azure.com --url "$url" \
  --headers Content-Type=application/json \
  --body "{\"input\": \"$challenge\", \"stream\": false}" \
  --query "output[?type=='message'].content[].text" --output tsv
```

The reply is `AGENT_PROOF|` followed by the challenge. Agent changes are
published the same way as app changes: commit and push, then rerun `up`; the
runner creates a new agent version and proves it.

## Observability

Every component sends telemetry to the deployment's Application Insights
resource, `appi-<prefix>`, and a question asked through the app is one
end-to-end operation from the browser request to each tool call:

| Component (`cloud_RoleName`) | What it records | Configured by |
| --- | --- | --- |
| Web app (`<project_name>-web`) | Each browser request except `/healthz` probes, named by route (`POST /api/responses`), and each call to the agent as an HTTP dependency that sends W3C trace context. Never headers, tokens or bodies. | [`app/server/telemetry.mjs`](../app/server/telemetry.mjs) with the Microsoft OpenTelemetry Distro, preloaded with `node --import`; the runner sets the connection string and `OTEL_SERVICE_NAME`. |
| Agent Service (`agentsv2`) | The `invoke_agent` request with Foundry's latency breakdown (`hosted_agent.latency.*`), and each toolbox `tools/list` and `tools/call`. | Foundry, without configuration. |
| Hosted agent (`<project_name>-assistant`) | `invoke_agent`, `chat` and `execute_tool` spans with token usage, the MCP and toolbox calls, and the agent's logs. | The agent server's OpenTelemetry distro, through the connection string Foundry injects from the project's `app-insights` connection. |

**Message content stays out by default.** The agent server records prompts,
tool results and answers on its spans unless
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` is `false`, which
[`hosted-agent.json`](../agents/scenario-assistant/hosted-agent.json) sets.
Those values were fetched with each user's own access, and anyone who can
read the telemetry would otherwise see them. To debug in DEV only, set it to
`true`, publish, reproduce, then set it back and publish again. Spans still
carry `user.id`, the caller's object ID. The agent also skips the OpenAI
Agents SDK instrumentation that the platform's Agent 365 tracing would load,
which otherwise logs an exception on every container start.

Reading traces needs **Log Analytics Reader** on `appi-<prefix>`, or
**Privileged Monitoring Data Reader** when its tables are protected; grant it
only to operators. Open them in the Foundry portal under **Agents** >
**Traces**, or in Application Insights under **Transaction search** and
**Application map**. This query lists the slowest recent questions with
their agent and tool spans:

```kusto
let slowest = requests
    | where timestamp > ago(1h) and cloud_RoleName endswith "-web" and name == "POST /api/responses"
    | top 5 by duration desc
    | project operation_Id;
union requests, dependencies
| where operation_Id in (slowest)
| project timestamp, operation_Id, cloud_RoleName, name, duration, success
| order by operation_Id asc, timestamp asc
```

Application Insights bills ingestion per GB; content-free spans keep each
question's telemetry small.

## Troubleshooting

Treat authentication, authorization and tool failures as blockers, not empty
answers. Never copy tokens into requests, files or logs, switch to a shared or
application identity, grant more than the access model above, or recreate the
agent or app to hide a failure.

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Sign-in fails with `AADSTS50011` | The page's address is not a registered redirect URI, for example `localhost` without `web_app_local_redirect_uris`, a Codespaces URL or a custom domain. | Use the `web_app` output's `url`, or register `http://localhost:5173/` through `web_app_local_redirect_uris`. Change the registration only through Terraform. |
| Sign-in asks for administrator approval or fails with `AADSTS65001` | Tenant-wide consent for the registration's Foundry scope is missing. | Rerun `up`: the `app` phase grants it as you. Otherwise an administrator opens the `web_app` output's `api_permissions_url` and grants admin consent for **Azure Machine Learning Services** `user_impersonation`, the `https://ai.azure.com` scope. |
| HTTP 401 | No token, an expired token or a token for another audience. | Sign out and in again. |
| HTTP 403 | The user lacks agent-endpoint access. The app's message names Foundry Agent Consumer, the least-privilege role. | An operator assigns a role from the [access model](#access-model); allow a few minutes. |
| HTTP 404 | No agent at the configured endpoint. | Run the `agent` phase, and compare the `hosted_agent` output's `responses_url` with the Container App's `AGENT_RESPONSES_URL`. |
| HTTP 424 or 5xx on a first question | A new session is starting. | Ask again. For persistent errors, follow the question's operation in Application Insights; see [observability](#observability). |
| HTTP 502 | The agent endpoint was unreachable, or the connection dropped while the agent answered. | Ask again. If it persists, check the agent's traces in Application Insights and the Container App's logs. |
| HTTP 504 after about 220 seconds | The agent did not answer within `AGENT_TIMEOUT_SECONDS`. | Ask again. A cold live ontology query can be slow on small capacities; do not add retry loops or another backend. |
| The answer says the ontology is unavailable | The user lacks Fabric access, the ontology is unbound, or Fabric IQ needs the user's consent. | Grant Fabric access, check the [binding](ontology-knowledge.md#review-the-generated-binding), or open the consent link the app shows and ask again. |
| The answer says documents are unavailable | Ingestion has not finished, or the knowledge-base connection or the project identity's Search role is missing. | Check `knowledge_source_status_url` and the knowledge phase's outputs. |
| Agent version `failed` | The image did not pull or start. | Inspect the version's error, the `container-registry` connection and the project identity's `AcrPull`; after fixing the cause, rerun with `--retry-failed`. |
| Container App revision failed | The server did not start, for example because a setting is missing. | Read the revision's console and system logs in the `log-<prefix>` Log Analytics workspace; fix, push and rerun. |
| The runner refuses to build | The source directory has uncommitted changes, or the checkout is not the published `origin/<branch>`. | Commit and push, then rerun `up`. |

## Limits and open evidence

- **Other users:** per-user isolation and denial are documented platform
  behavior that this template has not verified. Prove them before sharing the
  app.
- **Browser sign-in:** the runner's app check uses your Azure CLI token, not
  the app's registration, so it does not prove the browser sign-in path.
- **Documents:** retrieved with the project identity and not trimmed per user;
  see [toolbox tools](#toolbox-tools).
- **Live ontology through Search:** the agent does not use the knowledge base's
  `fabricOntology` source, which needs a per-request end-user header that the
  toolbox connection does not send.
- **Public endpoints:** the web app's ingress, the agent endpoint and the
  registry are authenticated public endpoints; there is no private networking.
- **Cold starts:** the Container App scales to zero, and hosted-agent sessions
  release their compute after an idle timeout, so a first request is slower.
- **Previews:** the Fabric IQ tool (`fabric_iq_preview`) is a preview, the
  toolbox endpoint is called with the `Toolboxes=V1Preview` feature, and the
  Agent Framework hosting package is a pre-release (see
  [`requirements.in`](../agents/scenario-assistant/requirements.in)).
- **Web IQ:** deployed and probed, but not connected to the agent.

## Product contracts

- [Hosted agents](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents) and [their permissions](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agent-permissions)
- [Agent endpoint authorization](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-endpoint-authorization) and [Foundry roles](https://learn.microsoft.com/azure/foundry/concepts/rbac-foundry)
- [Per-user session isolation](https://learn.microsoft.com/azure/foundry/agents/how-to/isolate-sessions-per-user)
- [Toolboxes](https://learn.microsoft.com/azure/foundry/agents/concepts/toolbox-overview) and the [Fabric IQ tool](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/fabric-iq)
- [Foundry IQ agent integration and per-request authorization](https://learn.microsoft.com/azure/foundry/agents/how-to/foundry-iq-connect)
- [Single-page app sign-in](https://learn.microsoft.com/entra/identity-platform/scenario-spa-overview)
- [Azure Container Apps](https://learn.microsoft.com/azure/container-apps/overview) and [its logs](https://learn.microsoft.com/azure/container-apps/log-monitoring)
- [Live ontology runtime identity](ontology-knowledge.md#supply-the-end-users-identity-at-query-time)

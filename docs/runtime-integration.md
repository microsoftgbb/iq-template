# Runtime integration

The [web app](../app/README.md), a React page with a small Node.js server on Azure
Container Apps, signs each user in and forwards their own Foundry token to two
Foundry prompt agents: the [variants of one agent](../agents/README.md), with IQ
and without. The with-IQ agent reaches the four IQs through one MCP tool each:
Fabric IQ for live ontology facts, Foundry IQ for approved documents, Web IQ for
public context through a Foundry IQ knowledge base of its own and, opt-in, Work IQ
for the user's own mail. This page holds the route's contract, its identities, its
failures and the evidence for a ready shell.
[Getting started step 7](getting-started.md#7-create-the-agents-and-app) creates it
and the [runtime-integration skill](../.github/skills/runtime-integration/SKILL.md)
proves it.

Terraform owns the registry, the Container Apps environment, the container app
(once the app phase sets `app_image_tag`), the identities, the sign-in and Work IQ
registrations and every project connection. The runner's `agent` phase publishes
the agents as Foundry project objects, outside Terraform state, and its `app` phase
builds the image with ACR Tasks and applies the reviewed app plan.

Successful `agent` and `app` phases prove that each agent runs its reviewed
definition and that the deployed app answers its health check with this
deployment's sign-in settings. They don't prove an answer, a user's consent to the
agents' tools or another user's isolation: ask through the app as yourself, then
with a second account ([evidence](#evidence-required-for-a-ready-shell)). Live
verification belongs in an explicitly chosen derived project, not this reusable
template.

## Runtime at a glance

[![The Container Apps web app's server verifies the signed-in user's token and forwards it to two Foundry prompt agents. Only the with-IQ agent has tools: Fabric IQ and the optional Work IQ mail tool run as that user; Foundry IQ documents and Web IQ public context use the project's managed identity. Application Insights holds the app's and the agents' traces.](diagrams/agent-runtime.svg)](diagrams/agent-runtime.svg)

Arrows show invocation; answers return the same way. Every agent run is the
signed-in user's, so Foundry's role-based access applies to them. Fabric IQ
exchanges the user's token for its own audience, so Fabric workspace roles apply to
that user. With Work IQ, Foundry's `work-iq` connection calls Work IQ with the token
it keeps for that user since their one-time consent, so mailbox permissions apply.
The agents read both knowledge bases as the project's managed identity, and Search
calls Web IQ with the API key it holds. This is the intended authorization contract,
not evidence that every user or deployment has passed the checks below.

[Editable Excalidraw source](diagrams/agent-runtime.excalidraw) | [All diagrams](diagrams/README.md) | [Icon sources](diagram-authoring.md#official-icon-sources)

## What runs where

`<prefix>` is `<project_name>-<suffix>`, where Terraform generates the
six-character suffix once per deployment.

| Component | Owner | Contract |
| --- | --- | --- |
| Agents `<project_name>-with-iq` and `<project_name>-without-iq` | Runner, `agent` phase | Versions of the reviewed definitions in [`agents/`](../agents/README.md) on the `agent` model deployment; each latest version's metadata holds the rendered body's `definition_sha256`. |
| Project connections | Terraform, [`infra/foundry.tf`](../infra/foundry.tf) | `fabric-scenario` (`UserEntraToken`), `knowledge-base` and `public-context` (`ProjectManagedIdentity`), `work-iq` (`OAuth2`, only with Work IQ), `app-insights` for Foundry's tracing and `knowledge-search`. Each is declared with `isSharedToAll = false`, as Foundry stores them. |
| Knowledge bases | Terraform, [`infra/knowledge.tf`](../infra/knowledge.tf), [`infra/web_iq.tf`](../infra/web_iq.tf) | `<project_name>-knowledge` for documents, `<project_name>-public-context` for Web IQ, and the check-only `<project_name>-ontology-check` that only `verify` queries ([ontology and knowledge](ontology-knowledge.md#two-retrieval-paths)). |
| Container registry `cr<project_name without hyphens><suffix>` | Terraform, [`infra/app.tf`](../infra/app.tf) | Holds the `app` image. The admin user is disabled, and `container_registry_sku` defaults to `Basic`. |
| Log Analytics `log-<prefix>`, Application Insights `appi-<prefix>` | Terraform, [`infra/monitoring.tf`](../infra/monitoring.tf) | The container's logs and the traces of the app and the agents. Application Insights accepts Entra ID only; see [traces and telemetry](#traces-and-telemetry). |
| Container Apps environment `cae-<prefix>` | Terraform | Consumption workload profile; logs go to the Log Analytics workspace. |
| App identity `id-<prefix>-app` | Terraform | User-assigned identity with `AcrPull` on the registry and Monitoring Metrics Publisher on Application Insights, and nothing else. |
| Sign-in registration `<project_name>-app` | Terraform | Single-tenant single-page client without a secret or certificate. Its redirect URIs are exactly `https://<app>/redirect.html` and any `app_local_redirect_uris`; its only API permission is the delegated `https://ai.azure.com/user_impersonation`. |
| Work IQ registration `<project_name>-work-iq` | Terraform, [`infra/work_iq.tf`](../infra/work_iq.tf), only with Work IQ | The `work-iq` connection's OAuth client: a public client without a secret, whose only redirect URI is Foundry's consent-service URL. |
| Container app `ca-<prefix>` | Terraform, once `app_image_tag` is set | The `app:<tag>` image with external HTTPS ingress to port 8080, `/healthz` startup, readiness and liveness probes, `app.min_replicas` to `app.max_replicas` replicas (one to three by default) with an HTTP scale rule, and the settings below. |

The container app's settings come only from Terraform: `PORT` (8080),
`FOUNDRY_PROJECT_ENDPOINT`, `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID` (the sign-in
registration), `AGENT_WITH_IQ`, `AGENT_WITHOUT_IQ`,
`APPLICATIONINSIGHTS_CONNECTION_STRING`, `AZURE_CLIENT_ID` (the app identity, which
sends the traces) and `APP_VERSION` (the image tag). None is a secret: with local
authentication disabled, the connection string only addresses the Application
Insights resource. [`app/server/config.ts`](../app/server/config.ts) validates them
at start, and an invalid or incomplete configuration stops the container.

## The app's contract

| Hop | Interface | Identity |
| --- | --- | --- |
| Browser -> app server | `GET /api/config` returns `{ tenantId, clientId, scopes }`. `POST /api/turns` with `{ cid, variant, question, previousResponseId? }` answers with newline-delimited JSON: a `tool` event as each call starts and ends, a `heartbeat` every 15 seconds and one `result` | The user's token for `https://ai.azure.com/user_impersonation`, as `Authorization: Bearer` |
| App server -> Foundry | `POST <project endpoint>/openai/v1/responses` with `agent_reference` to the variant's agent, the question as `input`, `previous_response_id` for a follow-up, the metadata `cid` and `variant`, and `stream: true` | The same token, forwarded |
| With IQ -> Fabric IQ | MCP `ask_ontology` on `ScenarioOntology`'s endpoint, connection `fabric-scenario` | `UserEntraToken`: the user's token, exchanged for Fabric |
| With IQ -> Foundry IQ | MCP `knowledge_base_retrieve` on `<project_name>-knowledge`, connection `knowledge-base` | `ProjectManagedIdentity`, with Search Index Data Reader |
| With IQ -> Web IQ public context | MCP `knowledge_base_retrieve` on `<project_name>-public-context`, connection `public-context` | `ProjectManagedIdentity`; Search calls Web IQ with its stored key |
| With IQ -> Work IQ mail, only with Work IQ | MCP `fetch` on `https://workiq.svc.cloud.microsoft/mcp`, connection `work-iq` | `OAuth2` identity passthrough: the Work IQ token Foundry keeps for each user after their consent |

The protocol lives in [`app/src/live/protocol.ts`](../app/src/live/protocol.ts), the
server in [`app/server/`](../app/server/).

1. **Sign-in.** The page reads `/api/config` and signs the user in with MSAL
   (`@azure/msal-browser` 5), a redirect flow with PKCE, keeping tokens in session
   storage. It requests only the Foundry scope. Entra returns every response to
   `/redirect.html`, MSAL's redirect bridge and the registration's only redirect
   URI. MSAL renews a token silently with the refresh token and, once that expires
   a day after sign-in, in a hidden iframe that Entra returns to the bridge. So the
   app may frame the bridge (`frame-ancestors 'self'`), and nothing else may be
   framed.
2. **Verify.** Before it reads the request, the server
   ([`app/server/auth.ts`](../app/server/auth.ts)) verifies the bearer token
   against the tenant's signing keys: a v1 or v2 issuer for the configured tenant,
   the Foundry audience (`https://ai.azure.com` or its application ID), `RS256`,
   the tenant ID, the app's own client ID as `appid` or `azp`, the
   `user_impersonation` scope and an object ID. Anything else is a 401. It then
   accepts only the four request fields: a lowercase UUID, `with_iq` or
   `without_iq`, a question of 1 to 2,000 characters after trimming and, for a
   follow-up, a Foundry response ID. Anything else is a 400. The browser can't
   name an endpoint or an agent: the server calls only the configured project
   endpoint and the two agent names in its settings, and builds no prompt of its
   own.
3. **Ask.** The server forwards the user's token to the project's Responses API,
   streaming, with the `cid` as `x-ms-client-request-id` and the W3C trace context
   of its `invoke_agent` span. It never calls Foundry as itself and keeps no token.
   A follow-up sends the previous response's ID; Foundry holds the conversation
   and decides who may continue it.
4. **Stream.** The server reads Foundry's event stream and passes each MCP call on
   as it starts and ends, with its server, tool, state, time and shortened
   arguments. A consent request (`oauth_consent_request`, or a tool error that
   carries a consent link, as Fabric IQ's `CONSENT_REQUIRED` can) ends the turn
   with that link. The `result` is `answered` with the Markdown text,
   `needs-consent` with the link, or `failed` with a message, each with Foundry's
   response ID when there is one. A server whose tool list holds none of the
   allowed tools is reported as a failed `tools/list`.
5. **Log.** The server writes one value-free `app.turn` line per turn: the `cid`,
   variant, follow-up flag, outcome, failure code, elapsed time, response ID and
   each tool's IQ, state and time. It logs no question, answer, tool argument,
   token or user identifier.

A turn may take 225 seconds (`TURN_TIMEOUT_MS`), inside Azure Container Apps'
240-second request limit; the heartbeat keeps the connection open while a slow
tool runs. Fabric IQ dominates: `ask_ontology` takes tens of seconds, and a cold
first query takes longer. The defaults keep turns short: the agents run on their
own `agent` deployment at low reasoning effort, the documents' knowledge base
answers at minimal effort, and the app keeps one warm replica. A longer turn needs
an approved design, not a retry loop.

## Foundry agent route

| Variant | Agent name | Definition | Tools |
| --- | --- | --- | --- |
| Scenario assistant – with IQ | `<project_name>-with-iq` | [`agents/scenario-with-iq/`](../agents/scenario-with-iq/) | Fabric IQ, Foundry IQ, Web IQ and, with Work IQ, Work IQ mail |
| Scenario assistant – without IQ | `<project_name>-without-iq` | [`agents/scenario-without-iq/`](../agents/scenario-without-iq/) | None |

The variants share a byte-identical `agent.json` (the model deployment, reasoning
effort and description) and `instructions.md`; only their `tools/` differ.
Terraform names the agents in its `foundry_agents` output and supplies every value
a definition's `{{placeholders}}` need: the agent deployment and reasoning effort
(`agent_settings`), and each tool's server URL and connection
(`foundry_tool_connections` and `fabric_iq_connections`). A tool holds exactly the
fixed MCP keys, never headers, so no definition can carry a credential: each tool
authenticates through its project connection, and the app supplies no per-request
input. The [agents' README](../agents/README.md) lists the files and placeholders
and explains how to add a `scenario-no-<iq>` variant.

### Publish

The runner's `agent` phase renders each definition with
[`foundry_agent.py`](../.github/skills/deploy-platform/scripts/foundry_agent.py)
into the exact body it sends: `definition` (`kind: prompt`, the model, reasoning
effort, instructions and tools), `description` (the display name) and `metadata`,
which records the body's sha256 as `definition_sha256`. Unless the `work_iq` output
is enabled, it leaves the optional `WorkIQMail` tool out; the digest covers the
body, so turning Work IQ on or off publishes a new with-IQ version. It reads each
agent's latest version with the deployer's token for `https://ai.azure.com/.default`,
creates a missing agent (`POST /agents`) or publishes a new version of a changed one
(`POST /agents/<name>/versions`) after one `agent-publish` approval that names them,
and reads them back. A rerun with unchanged definitions publishes nothing. To check
and preview a body offline:

```sh
python3 .github/skills/deploy-platform/scripts/foundry_agent.py validate
python3 .github/skills/deploy-platform/scripts/foundry_agent.py render \
  --source agents/scenario-with-iq --variant with_iq --values <placeholder-values.json> \
  --omit WorkIQMail
```

- Keep the shared instructions within 8,000 characters and free of braces: Foundry
  can read `{{name}}` in instructions as a template variable. They hold no document
  content or sample facts, because the without-IQ variant shares them.
- Each tool allows only its reviewed tools (`allowed_tools`) with
  `require_approval: never`, because the app has no approval step. An empty
  `allowed_tools` would allow everything a server offers, so the runner refuses one.
- The without-IQ variant has no tools and nothing to connect. A prompt agent has no
  switch for the model's general knowledge, so the shared rules keep every variant
  to the sources it received and make it say what it can't confirm.

## IQ tools

| Tool file | Server label | Allowed tool | Target | Connection |
| --- | --- | --- | --- | --- |
| `FabricIQOntology.json` | `fabric_iq_ontology` | `ask_ontology` | `ScenarioOntology` | `fabric-scenario`, as the user |
| `FoundryIQKnowledge.json` | `foundry_iq_knowledge` | `knowledge_base_retrieve` | `<project_name>-knowledge` | `knowledge-base`, as the project |
| `WebIQPublicContext.json` | `web_iq_public_context` | `knowledge_base_retrieve` | `<project_name>-public-context` | `public-context`, as the project |
| `WorkIQMail.json` | `work_iq_mail` | `fetch` | Work IQ's MCP server: the user's own mail | `work-iq`, as the user; only with `work_iq_enabled` |

Each tool's `server_description`, at most 1,024 characters, carries what is
specific to that tool, so the shared instructions stay generic. The app's tool
timeline shows each call's shortened arguments, so you can check what a tool was
asked.

### Fabric IQ ontology

`ask_ontology` answers in prose with a table of the properties a question names.
A question across several entities, or a widened one such as everything related to
an asset, can fail after 60–80 seconds with "Invalid JSON response from remote MCP
server" and an empty reply; a larger derived ontology showed this even on a warm
capacity. So the tool description asks one question about the rows of one entity,
naming the properties it needs and quoting each value: unquoted, Fabric can read a
bare value as an empty one. The sample's question asks for `Asset` rows with each
asset's site through `LocatedAt`; if it fails in your tenant, ask about one entity
only. Fabric returns only the rows the user may read.

### Foundry IQ knowledge

`<project_name>-knowledge` holds only the attached
[knowledge folders](ontology-knowledge.md#register-the-folders-and-the-ontology) and
runs at `minimal` retrieval effort with extractive output: Search runs the agent's
question against every attached source and returns the matching passages, each with
its file's `doc_url`, in about a second. No model runs inside Search, so the agent
writes the answer and cites each document's title and section from the passages.
Minimal effort takes the question as a search intent, not a chat message, which is
how `verify` asks it. The agents read it as the project's managed identity, so
documents are not trimmed per user: publish only documents that every agent user
may read.

### Work IQ mail

Work IQ is opt-in (`work_iq_enabled = false` by default); its prerequisites are in
[getting started](getting-started.md#work-iq). The mail tool calls Work IQ's MCP
server, `https://workiq.svc.cloud.microsoft/mcp`, through the project connection
`work-iq`.

- **As the user.** The connection uses OAuth identity passthrough (`OAuth2`), so
  Work IQ answers only from what the user may read. Its OAuth client is the
  registration `<project_name>-work-iq`, which has no other role: a public client
  without a secret, identifier URI or exposed scope, whose only redirect URI is
  Foundry's consent-service URL (`terraform output work_iq` prints it as
  `oauth_redirect_url`), so the connection holds no secret. The registration
  declares Work IQ's `WorkIQAgent.Ask` and Microsoft Graph's `offline_access`;
  declaring isn't consent. A user's first Work IQ question returns Foundry's
  consent link ([consent for a tool](#consent-for-a-tool)); after the user consents,
  Foundry keeps a token per user and connection and refreshes it with
  `offline_access`. The app neither requests nor forwards it.
- **Read-only.** Work IQ's MCP server also offers tools that create, change or act.
  The tool allows only `fetch`, and its description allows one fixed path:
  `/me/messages?$search="<asset name>"&$select=subject,from,receivedDateTime,webLink,body&$top=10`.
- **Mail must be indexed.** Work IQ finds only mail in the user's own mailbox, after
  Microsoft 365 has indexed it. Put sample mail into that mailbox at least a day
  before you rely on it.
- **Billing.** Each call consumes Copilot Credits under a spending policy that
  covers the Work IQ API and the user, even for users with a Microsoft 365 Copilot
  license.

### The reply format

Foundry IQ and Web IQ both offer `knowledge_base_retrieve`, so their server labels,
descriptions and the instructions keep document questions off the web: Web IQ is
called last, in public terms, for at most one labelled line. Foundry's runtime
hands knowledge-base results to the model as documents and invites its own
`【1:2†source】` citations; the instructions allow only the
[reply format's](architecture.md#reply-format) markers. A large tool result can
make the model drop the format; the app's parser then shows the whole reply as the
answer.

## Web IQ

Terraform ([`infra/web_iq.tf`](../infra/web_iq.tf)) provisions a global
`Microsoft.WebIQ/accounts` resource, kind `WebIQ.Grounding`, SKU `G3`, API
`2026-06-01-preview`, with statistics disabled, once `web_iq_terms_accepted = true`.
The service is Microsoft-hosted and multi-tenant at a public HTTPS endpoint, without
Private Link, VNet integration or regional residency. The pinned AzAPI provider
lacks its embedded schema, so only this resource disables local schema validation;
ARM still validates requests. Portal onboarding, AppID linking and the key are
manual ([getting started](getting-started.md#web-iq)).

After knowledge setup, Terraform adds the knowledge source `<project_name>-web`
(kind `mcpServer` on `https://api.microsoft.ai/v3/mcp`, only the `web` tool, the key
in a write-only `storedHeaders` `x-apikey` header) as the only source of the
knowledge base `<project_name>-public-context`. The `WebIQPublicContext` tool
queries that knowledge base, so no document question can trigger a web search. The
base's retrieval instructions request passage content, the only format the key
allows.

- **Public-only queries.** The key is a project credential, not the user's
  identity, so only instructions keep private data out: the tool description and the
  base's retrieval instructions allow public, general context, such as a
  manufacturer's guidance about a type of equipment, and never a site, asset or
  person's name. The agent labels the line "Public context, not company guidance".
  Check the query in the app's tool timeline when you test.
- **The key.** It enters only as the sensitive, ephemeral input `web_iq_api_key`,
  through the source's write-only body, so it never reaches tfvars, state or plans;
  rotate it with `web_iq_api_key_version`. The runner supplies it from
  `WEB_IQ_API_KEY` to every plan and apply from knowledge setup on, never to
  destroy plans, and refuses an ambient `TF_VAR_web_iq_api_key`. A plan without the
  key would remove the stored key.
- **Verification.** `verify` makes one billable, fixed public-only search with
  `WEB_IQ_API_KEY`, and one cited retrieval through the public-context knowledge
  base, each attempted once. It records result counts and freshness metadata, not
  keys or content. Neither shows that the key belongs to the deployed account:
  confirm that yourself. Missing terms block deployment; missing account outputs or
  search evidence block `verify`.

Production Entra authentication for Web IQ requires manually binding an approved
application's client ID to the Web IQ AppID in the portal, then obtaining app-only
tokens for `https://api.microsoft.ai/.default`; availability can require Microsoft
enablement. Do not create credentials on the Fabric deployment application or the
app's sign-in registration, reuse a delegated-user token, or add a Web IQ tool with
the key, a backend or browser credentials.

## Traces and telemetry

The app's tool timeline is the first record: each tool call with its server, state,
time and shortened arguments, and the run's response ID. Every deployment also
traces both halves of a turn to the Application Insights resource `appi-<prefix>`
(`terraform output application_insights`), in the Log Analytics workspace
`log-<prefix>` that holds the container's log lines too:

- **The app.** The server ([`app/server/telemetry.ts`](../app/server/telemetry.ts),
  service name `scenario-assistant`, version the image tag) records a request span
  per request but the health probe, named by route, such as `POST /api/turns`. For
  each question it records an `invoke_agent <agent>` client span with
  `gen_ai.agent.name`, `app.variant`, `app.cid`, `app.follow_up`, `app.outcome`,
  `app.outcome.code`, `app.tool_calls`, `app.tool_failures` and Foundry's
  `gen_ai.response.id`, and each finished tool call as an `app.tool_call` event
  with `gen_ai.tool.name`, `app.tool.server`, `app.tool.iq`, `app.tool.state` and
  `app.tool.duration_ms`. Like the log line, these spans hold no question, answer,
  tool argument, token or user identifier.
- **The agents.** The project's `app-insights` connection turns on Foundry's
  server-side tracing of every run: an `invoke_agent` span with `chat` spans for the
  model calls and `execute_tool` spans for the tool calls, with their inputs and
  outputs. Its spans record the asking user's object ID as `user.id`.
- **One trace.** The server sends the `invoke_agent` span's W3C trace context with
  the request, and Foundry's trace of the run continues it. Application Insights'
  transaction view shows the request, the agent run and its tool calls together; the
  Foundry portal's **Traces** view lists the runs and finds one by the response ID
  the app shows. Traces appear within a few minutes.

To find a turn's trace by the response ID the app shows, query
`AppDependencies | where Properties["gen_ai.response.id"] == "<response ID>"` and
open its `OperationId`.

Nothing holds an ingestion key: Application Insights accepts only Entra ID, and the
app's managed identity and the Foundry project's identity publish with **Monitoring
Metrics Publisher**. Foundry's prompts, answers, tool definitions and tool
arguments and results go to the `AppGenAIContent` table, which Terraform protects.
Only **Privileged Monitoring Data Reader** holders can read it, and Terraform grants
that role to the deployer alone; the rest of each trace stays readable to the
workspace's readers. Never grant the role more widely, and never paste trace
content, which can hold ontology rows, document passages and mail, into issues,
logs or commits. `telemetry.retention_days` sets how long the workspace keeps it.
Application Insights bills ingestion per GB. Azure adds a Failure Anomalies alert
rule beside Application Insights and, in a subscription without one, a Smart
Detection action group; the reviewed destroy plan deletes both from the resource
group, so teardown isn't blocked.

## Authorization and consent

### Access model

| Principal | Needs | Granted by |
| --- | --- | --- |
| Each app user | A Foundry role on the project that lets them run its agents through the Responses API, such as **Foundry User**. Read access to the DEV ontology and its bound data, because Fabric IQ queries run as them. With Work IQ, a mailbox and spending-policy coverage. | An operator. The template assigns no end-user roles. |
| Deployer | Foundry User on the Foundry account, to publish and invoke the agents; Search Index Data Reader, for `verify`; Privileged Monitoring Data Reader, for trace content; and the subscription and Entra rights in [getting started](getting-started.md#prerequisites). | Terraform (`deployer_foundry_user`, `deployer_search_reader`, `deployer_telemetry_content_reader`); the rest is yours. |
| Project managed identity | Search Index Data Reader, for both knowledge bases; Monitoring Metrics Publisher, for Foundry's traces. | Terraform |
| App identity | `AcrPull` on the registry and Monitoring Metrics Publisher on Application Insights only: no Foundry, Search or Fabric access. | Terraform |
| Search managed identity | Contributor on DEV, for OneLake indexing; Cognitive Services User on the Foundry account, for the models. | Terraform, through the knowledge plan |

The app's 403 message names Foundry User. Foundry Agent Consumer, the least-privilege
role for agent endpoints, is unverified for this route, which calls the project's
Responses API: confirm it with a second account before relying on it. Grant Foundry
User at project scope; its role definition ID avoids name differences while Foundry
roles are renamed:

```sh
az role assignment create --assignee "<user-principal-name-or-object-id>" \
  --role "53ca6127-db72-4b80-b1b0-d745d6d5456d" \
  --scope "$(terraform -chdir=infra output -raw foundry_project_id)"
```

Allow a few minutes for the assignment to apply. The app never holds an
application credential or a shared token: Foundry decides who can run the agents,
Fabric decides which ontology data each user can read, and Microsoft 365 decides
whose mail Work IQ reads.

### Sign-in

The app's sign-in registration requests one delegated permission: Azure Machine
Learning Services' `user_impersonation`, the resource behind `https://ai.azure.com`.
The runner grants it tenant-wide as the assumed administrator, in the `consent`
phase once the registration exists or at the start of the `app` phase. Otherwise
each user consents at first sign-in where tenant policy allows it, or an
administrator grants it at the `api_permissions_url` that `terraform output app`
prints.

### Consent for a tool

The first time Fabric IQ or Work IQ runs for a user, Foundry needs that user's
consent to call the service as them. The stream carries an `oauth_consent_request`,
or the tool's error names a consent link, and the app shows a consent card with
that link. The user accepts in the new tab and asks again. Each user does this once
per tool; Foundry IQ and Web IQ never ask. A consent lasts until the user or an
administrator revokes it. Foundry can offer the link even where tenant policy
doesn't let users consent; accepting then fails, and an administrator grants the
consent for the organization instead
([Microsoft's guide](https://learn.microsoft.com/azure/foundry/agents/how-to/configure-user-delegation#complete-downstream-consent)):
for Work IQ, `WorkIQAgent.Ask` and `offline_access` at the `api_permissions_url`
that `terraform output work_iq` prints. The `verify` phase grants
`WorkIQAgent.Ask` tenant-wide as you; no tenant-wide grant replaces each user's own
tool consent.

## Evidence required for a ready shell

| Gate | Required evidence | Checked by |
| --- | --- | --- |
| DEV | Ingestion of every folder source; a `fabricOntology` reference with live data (`Pump B`, `North Works`, or `--expect`) through the check-only knowledge base; a cited `indexedOneLake` reference from `<project_name>-knowledge`; an authenticated Web IQ search and a cited public-context retrieval; with Work IQ, the `WorkIQAgent.Ask` grant and the `work-iq` connection with its redirect URL, checked without a Work IQ call | `verify` phase |
| Agents | Each agent's latest version carries the reviewed definition's digest | `agent` phase |
| App | `/healthz` answers 200, and `/api/config` serves this deployment's client ID | `app` phase |
| Browser sign-in | A person signs in at the app URL, asks with IQ and without, and continues with a follow-up | You |
| Sources | Through the app, as the tool timeline shows: Fabric IQ rows as the user (Pump B at North Works), a cited starter-document passage, a public-only Web IQ query and, with Work IQ, `work_iq_mail`'s `fetch` returning the user's own mail after their consent. A document is never a live fact. | You |
| Replies | Each answer carries a marker after each fact, then *Sources* and *Steps*, and shows the run's response ID | You |
| Authorization | Missing consent, an expired sign-in, a missing Foundry role (HTTP 403) or denied Fabric access is an explicit failure, never an answer from another source | You, with a second account |
| Isolation | A second user can't continue or read the first user's conversation through its response ID, or reach their mail | You, with a second account |
| Repeatability | Rerunning `up` publishes, builds and changes nothing | A rerun of `up` |

The runner saves `verify`'s retrieval responses as owner-only files in the ignored
`.artifacts/deploy/evidence/` and decides from their structured references, never
from answer text. Keep evidence private with the reviewed revision and target, and
keep deployment IDs, accounts, tokens and logs out of reusable definitions and
guidance.

A deployed resource is not readiness. The runner reports **DEV ready** after
`verify`, **agents ready** after `agent` and a **healthy app** after `app`; an answer
from each variant through the app gives **verified for one user**. None is a ready
shell. A single-account run is verified for that user, with isolation not
demonstrated. Work IQ stays unverified without a tenant with mailboxes and a
spending policy that covers the Work IQ API. A catalog entry, cached sign-in,
plausible answer or the preflight's `candidate` result is not evidence.

## Preflight is not readiness

The [read-only preflight helper](../.github/skills/runtime-integration/scripts/preflight_runtime.py)
checks a chosen derived checkout and reports what it found:

- the checkout's origin, and that it is neither the template nor a fork;
- `tools`: Node.js >= 24.11.0, Git >= 2.27.0 and Azure CLI >= 2.60.0;
- the chosen subscription, tenant and delegated user, and provider credential
  overrides such as `ARM_CLIENT_ID`;
- `foundry_api`: the tenant's Microsoft Foundry API principal (Azure Machine
  Learning Services) publishes an enabled `user_impersonation` scope;
- `regions`: Foundry accounts, Container Apps environments and container registries
  are offered in `--location`;
- `fabric_quota`: Fabric's native `CapacityQuota` record in `--fabric-location` has
  the CUs the exact SKU needs. With `--current-fabric-capacity-sku`, the SKU of this
  deployment's existing capacity, it counts only the CUs a resize adds and reports
  that SKU as `fabric_quota.current_sku`;
- `work_iq`: with `--work-iq`, Work IQ's manual prerequisites, which it can't check;
  otherwise a note that Work IQ is optional.

Its successful result is `candidate`, and its `unverified` list names what still
needs live evidence. It never signs in, writes configuration, reads state or
provisions, and it never prints native authentication output. Check Fabric's
`CapacityQuota` record, not a generic discovery fallback that says "No Limit" beside
a zero. The runner's own `preflight` phase repeats the checks it needs before it
creates anything: tools, your user and target, provider overrides, Web IQ, the
Power BI, Foundry API and (with Work IQ) Work IQ principals, Fabric CU quota, model
quota summed per quota and the Search tier. Search's new-service capacity and
actual service capacity are separate. A partial provisioning failure can leave
working resources behind; retain the same state and obtain a fresh plan approval
before changing regions or replacing anything.

## Local development

From `app/`, run the checks as CI does: `npm ci`, `npm run lint`, `npm run build`
and `npm test`. `npm run dev:mock` serves the page at `http://localhost:3000/` with
canned answers for the sample questions: no sign-in and no Azure. The mock module
loads only behind a literal `import.meta.env.MODE === 'mock'`, so production builds
leave it out.

`npm run dev` runs the real server with Vite's middleware at
`http://localhost:3000/`, against the deployment that the ignored `app/.env.local`
names ([app README](../app/README.md#run-it-against-a-deployment)):

```sh
terraform -chdir=infra output -json app_settings \
  | jq -r 'to_entries[] | "\(.key)=\(.value)"' > app/.env.local
```

Sign-in works only on a registered redirect URI. Add
`app_local_redirect_uris = ["http://localhost:3000/redirect.html"]` to your tfvars
and rerun `up`; the reviewed plan adds that bridge to the registration. In a
Codespace, open port 3000 through VS Code's local forwarding, so the browser shows
`localhost:3000`; a forwarded `*.app.github.dev` address isn't registered. Keep
Codespaces ports private, never register a public tunnel's address, and change the
registration only through Terraform.

To separate an app problem from an agent problem, ask an agent directly as
yourself, with your Azure CLI token:

```sh
endpoint="$(terraform -chdir=infra output -raw foundry_project_endpoint)"
agent="$(terraform -chdir=infra output -json foundry_agents | jq -r .without_iq.name)"
az rest --method post --resource https://ai.azure.com \
  --url "$endpoint/openai/v1/responses" \
  --headers Content-Type=application/json \
  --body "{\"agent_reference\": {\"type\": \"agent_reference\", \"name\": \"$agent\"}, \"input\": \"Which assets are in maintenance?\"}" \
  --query "output[?type=='message'].content[].text" --output tsv
```

If that answers but the app fails, look at the app, its settings or the sign-in
registration. Agent and app changes are published the same way: commit, then rerun
`up`.

## Troubleshooting

Treat authentication, authorization and tool failures as blockers, not empty
answers. To locate one, compare the without-IQ agent, which calls no tool, with
the with-IQ agent's turn and its tool timeline. Never copy tokens into requests,
files or logs, switch to a shared, key-based or application identity, grant more
than the access model above, widen a tool's `allowed_tools`, relax the server's
token checks or recreate an agent to hide a failure.

| Symptom | Cause | Fix |
| --- | --- | --- |
| Bootstrap stops with `CapacityNotActive`; in the app, every with-IQ turn fails within seconds with "A user error occurred while executing a tool call" (`tool_user_error`) while the without-IQ agent answers | The Fabric capacity is paused | Resume the capacity in the Azure portal, then ask again. Pause it again when idle. |
| Retrieval fails with HTTP 500 "exceeded the maximum runtime" right after a resume | A cold start | Retry; warm up with one question before you rely on the app. |
| Graph materialization reports *Unsupported Data Source* for every entity | The ontology is bound through a OneLake (`AzureStorage.DataLake`) expression, which loses the `ONT_*` pins | Rerun `BindScenario` (`deploy.py up` reruns it while the binding is unverified): the binding must use the [SQL endpoint form](ontology-knowledge.md#bind-through-the-sql-analytics-endpoint). |
| Web IQ search or public-context retrieval fails with HTTP 401 or 403 | The key is wrong or rotated, or lacks Web Search | Create a new key in the portal, export it as `WEB_IQ_API_KEY`, increment `web_iq_api_key_version` and rerun `up`. |
| Web IQ returns HTTP 429 | Web IQ quota | Wait, or raise the Web IQ quota; the runner never retries a billable search. |
| Sign-in fails with `AADSTS50011` | The page's address isn't a registered redirect URI, such as `localhost` without `app_local_redirect_uris`, a Codespaces URL or a custom domain | Use the `app` output's `url`, or register `http://localhost:3000/redirect.html` through `app_local_redirect_uris`. |
| Sign-in fails with msal.js `timed_out`, or silent renewal fails after a day | The redirect bridge isn't the registered redirect URI, or the bridge can't be framed | Register exactly `https://<app>/redirect.html` (Terraform does) and keep the server's `frame-ancestors 'self'` on `/redirect.html`. |
| Sign-in asks for administrator approval, or fails with `AADSTS65001` | Consent to the app's Foundry scope is missing and users may not consent | Rerun `up`: the `consent` or `app` phase grants it as you. Otherwise an administrator grants it at the `app` output's `api_permissions_url`. |
| The app says to sign in again (HTTP 401 from the app server) | The token expired, or isn't a Foundry token for this app | Reload the page; MSAL signs in again. |
| Foundry refused the question (HTTP 403) | The user has no Foundry role on the project | An operator grants one from the [access model](#access-model); allow a few minutes. |
| Foundry has no agent (HTTP 404) | The agent phase hasn't run, or the settings name another project | Rerun `deploy.py up`; compare `terraform output app_settings` with the container app's settings. |
| HTTP 429 from the agent model | The `agent` deployment's quota is exhausted | Raise `agent_model.capacity` within your quota; for Web IQ calls or `verify`'s ontology check, `chat_model.capacity`. |
| A tool shows `tools/list` failed | The definition's `allowed_tools` names a tool the server doesn't offer | Fix the definition to the server's tool names and republish; never widen `allowed_tools`. |
| Fabric IQ returns an empty reply, or "Invalid JSON response from remote MCP server", after 60–80 seconds | The question spans several entities or is too wide | Ask about one entity, name the properties you need and quote each value. |
| The app shows a consent card on every question | The user didn't finish consenting, or tenant policy blocks user consent | Open the link, consent as yourself and ask again; otherwise an administrator grants it ([consent for a tool](#consent-for-a-tool)). Never consent for another user. |
| Work IQ asks for consent that fails, or its tool fails after consent | No administrator consent to `WorkIQAgent.Ask` and `offline_access`, or no Copilot Credits spending policy covers the Work IQ API and the user | Have an administrator grant consent at the `work_iq` output's `api_permissions_url`, and check the spending policy ([Work IQ](getting-started.md#work-iq)). Never switch to a shared mailbox or identity. |
| Work IQ finds no mail | The mail isn't in the signed-in user's own mailbox, or Microsoft 365 hasn't indexed it yet | Put it in that user's mailbox at least a day before you rely on it. |
| The answer says documents are unavailable | Ingestion hasn't finished, or the connection or the project identity's Search role is missing | Check `knowledge_source_status_urls` and the knowledge phase's outputs. |
| No answer within 225 seconds | A slow or cold tool call | Ask again once; a cold Fabric IQ query can be slow. Don't add retry loops or another backend. |
| Tool progress appears only when the answer is complete | Something between the browser and the app buffers the stream, such as a corporate proxy | Let `/api/turns` pass unbuffered; the app streams newline-delimited JSON. |
| The container doesn't start | A setting is missing or invalid; the server logs `app.server.config` with the problems | Read the revision's console log in the `log-<prefix>` workspace, fix the Terraform setting and rerun `up`. |
| The runner refuses to build | The app's files under `app/` have uncommitted or untracked changes | Commit them, then rerun `up`. |

**Cost hygiene.** Between sessions, pause the Fabric capacity and set
`app = { min_replicas = 0 }`, then rerun `up` to apply it. Resume the capacity
before the next session and warm up with one question: the first one waits for the
capacity and a cold start.

## Routes to re-validate

`main` uses only the route above. Re-test another only in an explicitly chosen
environment, and keep this one until a replacement passes every gate.

| Route | Status | Re-test when |
| --- | --- | --- |
| Foundry IQ and Web IQ knowledge bases as the user | Not available: Foundry's MCP connection to a knowledge base uses the project identity | Foundry documents a per-user connection for knowledge-base MCP endpoints |
| The agents' knowledge base with the live `fabricOntology` source | Not chosen: the agents' MCP connection can't send the per-request user header the source needs, so live facts come from native Fabric IQ, and only `verify` queries the check-only base | The connection can pass each user's Search-audience token |
| Microsoft 365 Mail MCP (`mcp_MailTools`) with `UserEntraToken` | Blocked: Foundry's token exchange for it failed with "ARA OBO token request failed with status BadRequest" | Foundry's exchange works for that server |
| The Work IQ MCP server with `UserEntraToken` or `AgentOnBehalfOfUser` | Not chosen: `UserEntraToken` isn't supported for this server, and `AgentOnBehalfOfUser`, in preview, needs agent-blueprint authorization that would change the app's sign-in | Microsoft supports `UserEntraToken` for it, or a reviewed sign-in change |
| Web IQ as an agent tool with its key, or as a source of the agents' document knowledge base | Not chosen: the key would act for every user, and any document question could trigger a web search | Web IQ offers a per-user connection |

## Product contracts

- [MCP tools for Foundry agents](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/model-context-protocol) and the [Fabric IQ tool](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/fabric-iq)
- [Foundry roles](https://learn.microsoft.com/azure/foundry/concepts/rbac-foundry) and [downstream consent for user delegation](https://learn.microsoft.com/azure/foundry/agents/how-to/configure-user-delegation#complete-downstream-consent)
- [Foundry IQ agent integration and per-request authorization](https://learn.microsoft.com/azure/foundry/agents/how-to/foundry-iq-connect)
- [Work IQ API costs](https://learn.microsoft.com/microsoft-365/copilot/extensibility/cost-considerations#work-iq-api) and [Copilot Credits billing](https://learn.microsoft.com/copilot/implement/usage-based-billing-copilot-credits-setup)
- [Single-page app sign-in](https://learn.microsoft.com/entra/identity-platform/scenario-spa-overview)
- [Azure Container Apps](https://learn.microsoft.com/azure/container-apps/overview) and [its logs](https://learn.microsoft.com/azure/container-apps/log-monitoring)
- [Live ontology runtime identity](ontology-knowledge.md#supply-the-end-users-identity-at-query-time)

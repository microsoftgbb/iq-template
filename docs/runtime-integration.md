# Foundry agents and Managed Runtime

The shell is **Fabric -> Foundry IQ -> agent -> Copilot Managed Runtime app**,
with Work IQ and additional Fabric capabilities as prescribed opt-ins.
The platform always deploys a Web IQ account; connecting it to the agent is a
separate approval and verification gate.
The template ships it: a standard-harness Copilot Studio agent
([`agents/scenario-assistant/`](../agents/scenario-assistant/README.md)) with
native [Fabric IQ and Foundry IQ tools](#studio-iq-tools), and the Managed
Runtime app ([`app/`](../app/)) that calls it through the Studio connector with
the end user's identity. [Getting started step 7](getting-started.md#7-create-the-agent-and-app)
creates both.

A disposable derived project proved this route end to end for one signed-in
user: per-user connection consent, the exact fresh challenge, a live ontology
answer and a follow-up in the same conversation. A second user's denial and
isolation, hosted preview and repeatability are still unproven, so report
"verified for one user", not a ready shell.

The Foundry Agent Service connector is blocked by a service-side token exchange
failure (see [native connection failures](#native-connection-failures)), and the
Studio connector refuses GitHub Copilot harness agents. Both are listed under
[routes to re-validate](#routes-to-re-validate).

Use [runtime-integration](../.github/skills/runtime-integration/SKILL.md) for the
preflight, the remaining evidence and re-validation. An explicitly chosen
disposable derived project is the right place for live verification, not this
reusable template.

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
on the account's allowed services. The shipped Studio agent has no Web IQ tool.
Adding one requires a separately approved native Studio connection/tool,
authentication, governance and consuming-app proof. An authoring MCP registration
or successful REST search is not that integration. Keep existing IQ tools in
`Invoker` mode and built-in web grounding off; do not add a backend or browser
credentials to bypass a blocked connection.

Production Entra authentication requires manually binding an approved
application's client ID to the Web IQ AppID in the portal, then obtaining app-only
tokens for `https://api.microsoft.ai/.default`; availability can require Microsoft
enablement. Do not create credentials on the Fabric deployment application or
reuse its delegated-user token. The runner's probe supports only the explicitly
supplied API key.

## Runtime at a glance

[![The signed-in Managed Runtime user invokes a published standard-harness Copilot Studio agent through ExecuteCopilotAsyncV2. Invoker-mode Fabric IQ and Foundry IQ tools use that user's own connections for live facts and document evidence. Access, grounding, conversation isolation and readiness gates remain explicit.](diagrams/agent-runtime.svg)](diagrams/agent-runtime.svg)

Arrows show invocation direction; answers return through the same agent and
conversation. This is the intended authorization contract, not evidence that
every user or deployment has passed the checks below. The Search knowledge
base's separate live-source token contract is shown in
[knowledge retrieval](ontology-knowledge.md#two-retrieval-paths).

[Editable Excalidraw source](diagrams/agent-runtime.excalidraw) | [All diagrams](diagrams/README.md) | [Icon sources](diagram-authoring.md#official-icon-sources)

## Native connector contract

| Responsibility | Connector / operation | End-user connection |
| --- | --- | --- |
| Invoke a versioned Foundry prompt agent | `shared_azureagentservice` / `InvokeAgent` | `entraIDUserLogin`; non-shareable |
| Invoke a published Copilot Studio agent | `shared_microsoftcopilotstudio` / `ExecuteCopilotAsyncV2` | `oauthDefault`; non-shareable |
| Native knowledge-base retrieval | `shared_azureaisearchfoundryiq` / `AzureAISearchKBRetrieveMcp` | `oauth`; non-shareable |

Check the selected environment's catalog and action governance, then inspect the
actual generated SDK schema. These IDs are public product identities, not
tenant-specific connections. A connector's presence in a catalog does not prove
its runtime compatibility, licensing, permissions or authorization propagation.
Do not substitute the Inference connector's chat completions for a Foundry agent.

Managed Runtime apps use generated connector services. Built-in app sign-in does
not automatically supply Foundry access or the ontology's end-user token.
Shareable API-key, certificate and managed-identity connections cannot stand in
for the required end-user ontology identity. A service-principal connection can
also be non-shareable; verify the selected auth type rather than that flag alone.

### MCP transport and retrieval inputs

The native IQ action is an MCP transport, not a conventional REST retrieve
operation. Its current generated metadata omits the MCP `Accept` parameter and
describes the response as `void`. The MCP server requires
`Accept: application/json, text/event-stream`. A JSON-only request returns a
`406`/JSON-RPC error before retrieval.

Use a narrow, checked adapter over the generated definition with the public
Managed Runtime `getClient` API when that wire contract is missing. Add only the
required `Accept` header and preserve opaque JSON/SSE responses for a strict
JSON-RPC parser. Keep the connector/action IDs, request path, method, connection
and authentication unchanged. Do not edit `generated/`, add raw HTTP, mutate the
SDK or loosen governance. Refuse a changed generated contract until reviewed.

Discover `tools/list` before constructing the tool invocation. With the
template's pinned Search API version (`search_api_version`, currently
`2026-08-01-preview`), `knowledge_base_retrieve` requires exactly:

```json
{
  "query_variants": ["One complete, unchanged user question or request."]
}
```

The connector's default `2026-05-01-Preview` names the same array `queries`, so
always pass the pinned `api-version` explicitly, in app calls and Studio tool
inputs alike.

The array contains one string of at most 400 Unicode characters. A `query`
property, multiple variants or extra source/filter arguments do not match this
tool's schema. Preserve the user's entities, identifiers, dates, filters and
language; let the native backend expand the query. JSON-RPC errors, a mismatched
request ID, malformed SSE and tool `isError` responses must remain failures.
Successful protocol negotiation still does not prove live-source authorization.

### Native Foundry Fabric IQ route

When the app cannot supply Search's separate source-user header, do not expose a
token field or silently fall back to the starter snapshot. After an explicit
design approval, prove the native Foundry `fabric_iq_preview` tool against the
bound ontology, with a delegated `RemoteTool` connection. The documented
`UserEntraToken` connection uses the Fabric audience; no client secret or
directory-management permission is needed for that passthrough configuration.
Keep the Search `fabricOntology` registration for callers that implement its
separate authorization contract.

In this route the agent uses Fabric IQ for current business facts and Foundry
IQ for indexed guidance/background. The app still invokes the agent through
its native non-shared Foundry connection. Source routing is a deliberate
contract, not a fallback selected differently in each project.

A native SDK smoke can prove the tool target and returned facts, but does not
prove the Managed Runtime caller's token exchange. Inspect the actual
`scenario-facts` tool receipt and matching ontology document URL. Surface
`raiPostToolAnnotations` errors as unresolved safety-service warnings, even
when the response is completed and contains correct data.

### Copilot Studio agent route

The agent's source lives in `agents/scenario-assistant/`. The deployment helper
renders it into a PAC workspace under ignored `.artifacts/studio/`, created with
`pac copilot init` or `clone`. PAC writes connection metadata (tenant,
environment and agent IDs) to `.mcs/`; never commit it. Observed with PAC CLI
2.13.1:

- Create with `pac copilot init --environment`; update with `push`, then
  `pac copilot publish --bot <physical-bot-id>`. Publishing by schema name and
  `pac copilot status` failed, and the publish message can repeat the previous
  timestamp. Confirm the bot's `publishedon` and a `Synchronized`
  `synchronizationstatus` through Dataverse instead; synchronization finished
  about 30 seconds after publishing.
- `pack` omits `accessControlPolicy` and `push` did not apply it. Init and
  import left **Group membership** with no allowed groups. Set the documented
  writable `bot.accesscontrolpolicy` column (`1`, **Agent readers**; YAML
  `ChatbotReaders`), then publish and read it back. Never leave group membership
  without reviewed groups.
- A `SendActivity` message is a template: use `{System.Activity.Text}`. A
  `="..." & ...` string is sent literally rather than evaluated.

Invoke `ExecuteCopilotAsyncV2` ("Execute Agent and wait") with the agent's schema
name and `{ message, locale, notificationUrl: "https://notificationurlplaceholder" }`,
as [Microsoft's code-app guidance](https://learn.microsoft.com/power-apps/developer/code-apps/how-to/connect-to-copilot-studio)
documents. The placeholder is required but unused. `ExecuteCopilot` is
fire-and-forget: its conversation ID (observed as `conversationID`) is a receipt,
not an answer. `ExecuteCopilotAsync` can fail with 502.

The generated service types the result as `void`; validate it as unknown data.
The observed result arrived in under a second as `responses`, `conversationId`,
`completed: false` and `isExpectingInput: false`, with no `lastResponse`, even
after the topic ended with `EndConversation`. Therefore read the turn from
`responses`. Do not gate on `completed` or `lastResponse` as Microsoft's example
does. Accept the three documented conversation-ID casings only when they agree.
Treat empty responses as no answer and a malformed flag as a failure.

Prove the caller with a fresh challenge that the agent must return exactly.
Studio writes `conversationtranscripts` about 30 minutes after a session; the
user activity's `from.aadObjectId` must equal the signed-in app user. These
transcripts use the `pva-autonomous` channel. The same continuation-ownership
rule applies to `x-ms-conversation-id` as to Foundry conversations.

### Studio IQ tools

Give a Studio agent live facts and documents through two native MCP tools. Each
tool is an `actions/<Tool>.mcs.yml` `TaskDialog` using
`InvokeExternalAgentTaskAction` with `ModelContextProtocolMetadata`, and is listed
in `connectionreferences.mcs.yml`. Set `connectionProperties.mode: Invoker` for
end-user credentials. `Maker` mode runs every caller as the author, so it is
never acceptable here.

| Tool | Connector / MCP operation | Connection parameter set |
| --- | --- | --- |
| Live ontology facts | `shared_fabriciqmcpserver` / `mcp_FabricIQOntology` | `certOauth` with `workspace_id` and `ontology_id` |
| Indexed documents | `shared_azureaisearchfoundryiq` / `AzureAISearchKBRetrieveMcp` | `oauth` (Entra ID integrated) with `targetUri` |

Set the Foundry IQ path parameter with a `ManualTaskInput` named
`"'knowledgeBaseName'"`, and pin `"'api-version'"` to `search_api_version`
likewise. Generative orchestration must be on. Turn off model knowledge, web
browsing, file analysis and semantic search so answers come only from these
tools.

The live Fabric IQ server exposed `ask_ontology`, `list_ontology_entities` and
`list_ontology_rules`, not the preview names in Microsoft's documentation.
Discover them instead of hard-coding names. Through the user's own connections,
one `ask_ontology` question took about 30 seconds on F2 and one document
retrieval about 6 seconds. The knowledge base's answer synthesis intermittently
returned 408 to Studio while direct retrievals succeeded in about 5 seconds; the
agent then reports the document source as unavailable.

The connector returns plain text only. A document answer arrived with its
citation marker removed, ending in a stray ` .`, so the app could not show the
source. Instruct the agent to name each document inline, for example
`(Source: starter.txt)`, and confirm the tool's reference in the transcript.
Quote that instruction text or use a YAML block scalar, because an unquoted
`Source: starter.txt` is invalid YAML.

All of this is scriptable; only each owner's delegated sign-in is interactive.
The deployment skill's [`studio_agent.py`](../.github/skills/deploy-platform/scripts/studio_agent.py)
implements these steps. Observed with PAC CLI 2.13.1:

- `pac copilot init --environment` creates **and publishes** the agent, with
  group-membership access and model knowledge, web browsing, file analysis and
  semantic search enabled.
- `push` updates topics, the agent component and tool actions. It creates the
  connection references unbound, and ignores `settings.mcs.yml`. Bind each
  reference through the documented writable `connectionreference.connectionid`
  column. Change `bot.configuration` grounding switches and
  `accesscontrolpolicy` through Dataverse, then publish and clone to read back.
- Create each owner's connection with the connector's parameter set through the
  Power Apps connections API, as Terraform's `powerplatform_connection` does.
  Its OAuth target remains `Unauthenticated` until that owner completes **Fix
  connection** in Power Apps. Never relay a consent code through chat or logs.
  Azure CLI tokens are forbidden on the Power Platform connectivity API that
  `ms` uses, so do not impersonate another tool's client to bypass that.

The Studio Test pane's **Allow** consent applies only to design-mode testing.
Through the published connector channel, each user must also choose their
connections in Studio's per-conversation connection manager. Its card is not
text, so the connector drops it and returns empty `responses` with
`isExpectingInput: true`. Studio's card linked to
`https://copilotstudio.microsoft.com/c2/tenants/{tenant}/environments/{environment}/bots/{schema-name}/channels/pva-autonomous/conversations/{conversation-id}/user-connections`.
This pattern was observed, not documented: the app may offer it as a
convenience, but must report the turn as needing user action rather than as
an answer. On a new agent the card arrives before any topic runs, so even the
connection check needs it. One consent per user and agent covered later
conversations. The Microsoft 365 Copilot license listed for the ontology MCP preview
did not block this proof.

The synchronous connector call is capped at Power Platform's documented
120-second limit for outbound requests. Once the user's connections were
chosen, a warm app -> Studio -> `ask_ontology` turn returned the live answer in
67.4 seconds. In a cold run, Studio's first `ask_ontology` call stalled for 100
seconds before Studio retried it successfully in 24 seconds. That turn finished
after 137 seconds, but the app had already received
`TimedOut: Invocation of API timed out.` at 120.1 seconds, with no
conversation ID. Direct ontology calls varied from 30 to 68 seconds, so treat
the 120-second limit as a real demo risk. Keep live-IQ turns well below it. A
longer turn needs an explicitly approved asynchronous design; do not add a
callback backend, warm-up job or retry loop by default.

### GitHub Copilot harness agents

PAC 2.13.1 creates a GitHub Copilot harness agent with
`pac copilot init --authoring-mode cli-copilot --environment <env>`. Unlike the
classic template, it is not published and starts with group-membership access.
Its workspace keeps instructions and model in `settings.mcs.yml`, tools in
`capabilities/tools/*.mcs.yml` and connection references in
`infrastructure/connections/<logical-name>.sync.yaml`. A native MCP tool is
`kind: McpTool` with `connectorId`, `connectionReference` and `operationId`.

Microsoft has not yet revalidated live push for this layout. Here, every `push`
tried to create the same connection reference twice and failed on its unique
key. Creating the tool `botcomponent` (type 9) through Dataverse worked: link it
through `botcomponent_connectionreference`, bind the reference, and set access
and `agentSettings.web.enableWebSearch`. Publishing by physical ID also worked,
and `clone` read the agent back unchanged. In Studio's preview, the agent
answered live ontology facts.

It cannot be the app-facing agent through the Studio connector. That connector
replies within a second with an ordinary text response: "This action doesn't
support agents built with the GitHub Copilot harness". Apps must treat that
response as a failure. The `McpTool` format has no `Invoker` mode field, so prove
end-user credentials with a second user before relying on it.

The MCP and A2A client channels are a preview available only in early-release
environments; a standard environment offers only the demo website, web app and
Teams/Microsoft 365 channels. An existing environment cannot be switched. The
Power Platform Terraform provider creates a new one with
`powerplatform_environment.release_cycle = "Early"`. Tenants that provision by
macro region need `macro_region`, for example `north-america`, instead of
`location`. Opening the MCP client channel provisioned Microsoft's
"Copilot Studio Agent MCP and A2A channels" API (`6f46e6ed-…`) in the tenant.
Its `MCS.InvokeAsMCP` and `MCS.InvokeAsA2A` scopes allow user consent.

A Terraform-managed, single-tenant public client with only `MCS.InvokeAsMCP`
and an `http://localhost` redirect worked without a secret or tenant-wide
consent. The endpoint is
`https://api.powerplatform.com/copilotstudio-mcp/tenants/{tenant}/environments/{environment}/agents/{schema-name}`
and exposes one `send-message` tool with `message` and an optional
`conversationId`. Signed in as the user, it answered live ontology facts in 38
seconds and kept the citation marker. An app still needs a native connector for
this endpoint, such as a custom MCP connector whose Entra authentication uses a
managed identity instead of a secret. That connector and second-user identity
remain unproven, so keep the standard-harness agent for the app route.

## Native connection failures

Treat OBO, consent and decryption failures as authentication blockers, not empty
successful answers. Compare a read-only agent-list request with invocation to
locate a connection-wide failure. Never enable PII/token logging, copy access
tokens into requests or state, change to a shared/API-key identity, grant more
roles without a reviewed need, or recreate the app/agent to hide the failure.
Use supported reauthentication of the exact existing connection only after
confirming its tenant, endpoint and auth type. A persistent native-service
failure remains blocked and needs service support, not a custom backend added
without approval.

The Foundry Agent Service connector currently fails before reaching Foundry with
`Failed to get onbehalfof access token ... IDX10609: Decryption failed`, for both
agent listing and invocation. This was observed in the local player and hosted
preview with Managed Runtime CLI 0.27 and 0.28 and matches public issue
microsoft/PowerAppsCodeApps#318. Refreshing the existing binding did not change
it. Use the proven Studio route for the app-facing hop instead of
working around this failure.

For invocation, use the Responses-based `InvokeAgent` operation with an explicit
agent name/version. Its `user` field is telemetry, not authorization. Verify
conversation ownership before supporting persisted history or continuation;
client-side possession of a conversation ID is not an authorization policy.

## Preflight is not readiness

The [read-only preflight helper](../.github/skills/runtime-integration/scripts/preflight_runtime.py)
checks a chosen derived checkout, tools, Azure/Managed Runtime tenant alignment,
the exact requested Fabric SKU's available regional CUs, and the two
connector/action catalogs. Its successful result is **candidate**.
It intentionally leaves all runtime claims unverified and never provisions.

CLI authentication has separate stages: an account can appear signed in while
maker operations still require interactive authentication. Catalog access is
not evidence that app registration or downstream queries will work. Keep native
CLI calls serialized because authentication-cache writes can race even on reads.

Check Fabric's native `CapacityQuota` record, not a generic discovery fallback
that says "No Limit" beside a zero. Search's published region/tier restrictions
and actual service capacity are separate from that quota. A partial provisioning
failure can leave working resources behind; retain the same state and obtain a
fresh plan approval before changing regions or replacing anything.

Infrastructure creation and native bootstrap still follow [getting
started](getting-started.md), with one state, reviewed plans, administrator
consent and user sign-in. A PAT is needed only for opt-in Fabric Git synchronization. Runtime sign-in is a
different flow; do not expand the deployment registration into a credential
broker.

## Evidence required for a ready shell

| Gate | Required evidence |
| --- | --- |
| Git and build | The reviewed source produces a working local App Player experience and hosted preview through the chosen native Git/build arrangement. No nested repository or governance bypass. |
| Agent | The app invokes the intended Foundry prompt-agent version or published Studio agent through its generated service, returns a fresh challenge exactly and surfaces native failures. A direct SDK-only smoke or conversation receipt is insufficient. |
| Documents | Successful ingestion plus a citation/reference from the intended knowledge corpus, not an uncited answer. |
| Live ontology | Successful `fabricOntology` activity, matching physical target IDs and raw source data. A starter-document snapshot is not a live fact. |
| End-user authorization | The real app/connector/agent path preserves the calling user's permissions. Missing identity, expired authorization and denied access remain explicit failures. |
| Isolation | A second user cannot reuse the first user's conversation, retrieved evidence or broader connection permissions. |
| Repeatability | Reusing the same reviewed configuration does not create another app, registration, state or active bootstrap job. |

Capture evidence privately with the reviewed revision and exact target. Keep
deployment IDs, account information, tokens, state and logs out of reusable
definitions and guidance. Do not claim readiness from a Boolean config flag,
successful registration, allowed catalog action or correct-sounding answer.

The [deployment skill](../.github/skills/deploy-platform/SKILL.md) reports
**DEV ready**, then **agent ready** from `studio_agent.py check` and **app
verified for one user** from the App Player. None of these is a ready shell
without the remaining evidence above. Work IQ/Web IQ are not part of this preflight and must not be
silently enabled.

## Local player boundary

Run the app through `ms app dev`, then use the exact App Player URL printed by
the CLI or official Vite plugin. A direct localhost page is not a native
connector/authorization proof.

An embedded browser can fail to fetch HTTP localhost even when the native app
and config servers return `200` with valid CORS headers. Verify those endpoints
and test the printed URL in an existing supported external browser on the same
host. Do not disable TLS, browser security or publish private Codespaces ports.
If local playback remains blocked, use an explicitly approved native hosted
preview of the reviewed pushed commit, not an invented proxy or backend.

With Managed Runtime CLI 0.27, the App Player's **Check connection** flow was
observed redirecting to `http://127.0.0.1:<port>/...` when creating the
app-facing `shared_microsoftcopilotstudio` connection, including in a hosted
preview. Hosted playback is therefore not a guaranteed fix for this OAuth
failure. Do not publish Codespaces ports or change this repository's Entra
registration to work around the player's redirect.

Before playback, each app user can authorize the connection through
`https://make.powerapps.com/environments/<environment-id>/connections`:
select **New connection** -> **Microsoft Copilot Studio**, then sign in.
Use the same tenant, environment and user as the app; reuse an existing matching
**Connected** connection instead of creating duplicates. Return to the App
Player and select **Check connection** again. This avoided the loopback
creation flow in the observed deployment; if the player still requests a
connection or fails, report the native blocker rather than claiming readiness.

That app-facing connection is separate from the agent's delegated Fabric IQ
and Foundry IQ connections. Follow the Studio **user-connections** link surfaced
by the app when the agent requests consent, authorize both sources as the
calling user, then repeat the fresh-challenge check. Neither the maker's CLI
sign-in nor another user's connection replaces these per-user authorizations;
complete them again if authorization expires or is revoked.

## Routes to re-validate

These routes were blocked or unproven in October 2026. Keep the
standard-harness Studio route until a disposable derived project proves the
replacement end to end. Re-test after the listed change:

| Route | Blocked by | Re-test when |
| --- | --- | --- |
| App -> Foundry Agent Service connector (`InvokeAgent`) | `IDX10609` on-behalf-of decryption failure in Managed Runtime (CLI 0.27-0.28, SDK 0.5.20), still present on 14 October 2026. The fix referenced in microsoft/PowerAppsCodeApps#318 targeted the Code Apps package. | A Managed Runtime release or that issue reports a fix. Run the app's read-only agent-list check first. |
| App -> standard Studio agent -> connected Foundry agent (preview) | Untested. Requires the Foundry agent's Activity protocol, which only the REST API or SDK can enable. | The direct Foundry connector stays blocked and a Foundry-hosted agent is required. |
| App -> GitHub Copilot harness agent | The Studio connector refuses this harness. MCP/A2A client channels exist only in early-release environments. Managed Runtime's default connector policy blocks custom connectors, because advanced connector policies cover certified connectors only. | The Studio connector supports the harness, MCP/A2A channels reach standard environments, or advanced connector policies support custom connectors. |
| PAC CLI for GitHub Copilot harness workspaces | 2.13.1 `push` creates connection references twice, and `publish` fails to read status in early-release environments. | A later PAC CLI release. |
| App -> Studio agent -> Foundry IQ knowledge base with the optional Work IQ source | The Foundry IQ connector's only action, `AzureAISearchKBRetrieveMcp`, takes a knowledge base name and API version and signs in for Search; it cannot send the user's token for the Work IQ app in `x-ms-query-work-iq-source-authorization`. The runner proves that knowledge base directly with the deploying user's token. | The connector can pass a user assertion for a Work IQ knowledge source. |
| App -> Studio agent with a Work IQ tool | Work IQ (preview) in Copilot Studio needs the GitHub Copilot harness, which the Studio connector refuses (above). The standard harness only offers the application-specific Microsoft 365 MCP tools, which Microsoft keeps for backward compatibility, not new projects. | The Studio connector supports the GitHub Copilot harness. |

## Product contracts

- [Foundry Agent Service connector](https://learn.microsoft.com/connectors/azureagentservice/)
- [Copilot Studio connector](https://learn.microsoft.com/connectors/microsoftcopilotstudio/)
- [Connect a code app to Copilot Studio agents](https://learn.microsoft.com/power-apps/developer/code-apps/how-to/connect-to-copilot-studio)
- [Agent (bot) Dataverse table](https://learn.microsoft.com/power-apps/developer/data-platform/reference/entities/bot)
- [Foundry IQ connector](https://learn.microsoft.com/connectors/azureaisearchfoundryiq/)
- [Managed Runtime connector services](https://learn.microsoft.com/microsoft-365/managed-apps/developer/connect-to-data?view=o365-worldwide)
- [Foundry IQ agent integration and per-request authorization](https://learn.microsoft.com/azure/foundry/agents/how-to/foundry-iq-connect)
- [Live ontology runtime identity](ontology-knowledge.md#supply-the-end-users-identity-at-query-time)

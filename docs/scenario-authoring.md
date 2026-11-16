# Scenario authoring contract

Derived projects change the domain and workflow, not the platform architecture.
Use one **Fabric -> knowledge -> prompt agents -> web app** core.
The Web IQ account, public-search probe and public-context tool are standard
platform components. Work IQ mail is an opt-in tool (`work_iq_enabled`); graphs,
semantic models and other runtime integrations are explicit opt-ins.

The template ships this core end to end: the Fabric/knowledge baseline, the
[scenario assistant](../agents/README.md) as two Foundry prompt agents, with IQ and
without, and the Azure Container Apps [web app](../app/README.md) that asks them. A
derived project adds its scenario in the homes below; it does not replace the shell.

Use the [runtime diagram](runtime-integration.md#runtime-at-a-glance) for the fixed
agent/app route, the [retrieval diagram](ontology-knowledge.md#two-retrieval-paths)
for source and identity boundaries, and the [Fabric lifecycle](../cicd/README.md)
for the native work-package order.

## One home per responsibility

| Home | Put here | Do not put here |
| --- | --- | --- |
| `infra/` | Terraform infrastructure, permissions, knowledge registration, the agents' connections and the app's hosting. | Business logic, seed provisioners, agent versions or image builds, another deployment state or an azd/Bicep replacement platform. |
| `workspace/` | Native Fabric item folders, seed/transform/publish/bind/readiness notebooks and deployment plans. | Credentials, saved attachments, raw backups or another project's physical bindings. |
| `scenario/data/` | Versioned domain/table contracts, deterministic synthetic generation and small fictional fixtures. | Real tenant/customer data or evaluation answers destined for indexing. |
| `scenario/knowledge/` | Reviewed authoritative document sources, with IDs, versions, scope and effective dates. | Raw fixture bundles, temporary exports, golden answers or mirrored mailboxes. |
| `scenario/workflows/` | Independently testable scenario-specific rules, transformations and approved tool logic. | An undeclared backend or a second source of authoritative facts. |
| `scenario/evals/` | Expected outcomes, source/citation requirements, negative cases and scenario checks. | Content published to a lakehouse Files corpus. |
| `agents/<variant>/` | Each variant's `agent.json` and `instructions.md`, byte-identical across variants, and its `tools/*.json` MCP tools, whose `{{placeholders}}` the runner fills from Terraform outputs. | Rendered definitions, physical IDs, access tokens, tenant IDs, deployment receipts, headers or credentials, tools that need approval or an empty allow-list. |
| `app/` | The web app: the page in `src/`, its agent layer in `src/live/`, the token-verifying server in `server/`, its tests and `Dockerfile`. Sign-in settings come from `/api/config`. | Credentials or app-only tokens, raw browser calls to Azure/Fabric, server-built prompts, or calls to anything but the configured project and agents. |
| `docs/`, `.github/skills/` | Canonical product explanations and repeatable procedures, respectively. | Session diaries or competing setup guides. |

Use these locations rather than adding competing root `tools/`, `services/`,
`evals/` or `apps/` trees. A genuinely necessary deviation requires an explicit
design decision. Never infer permission to provision a backend from a Python
workflow module.

`app/` is a self-contained build context, with its own `Dockerfile`, dependency
lockfile and `.dockerignore` allow-list; the runner builds the image from it only.
The agents need no build. Do not nest a second Git repository or move their files to
the repository root.

## Fabric ontology work package

A different domain gets new item identities, not a rewritten Site/Asset model
under its old logical ID. Publish all of the following together:

```text
workspace/
  <Domain>.Lakehouse/
    .platform
    lakehouse.metadata.json
  Seed<Domain>.Notebook/
    .platform
    notebook-content.py
  <Domain>Ontology.Ontology/
    .platform
    database.tmdl
    model.tmdl
    entityRelationships.tmdl
    relationships.tmdl
    namespaces/default.tmdl
    entities/<Entity>.tmdl
    tables/<BackingTable>.tmdl
  Bind<Domain>.Notebook/
    .platform
    notebook-content.py
  <Domain>.DeploymentPlan/
    .platform
    plan.yml
```

The [included ontology](../workspace/ScenarioOntology.Ontology) is the exact
native TMDL example. `database.tmdl` declares compatibility; `model.tmdl`
references tables, entities and namespace. Each entity declares its backing
table, key and typed property/column mappings. `relationships.tmdl` declares
source-table joins and cardinalities; `entityRelationships.tmdl` maps those
joins to directed business relationships. Preserve native service-exported
parts needed by the chosen item generation.

Before authoring, specify stable business keys, table grain, columns/types,
null behavior, entity/property names and relationship cardinalities. Keep
identities/lineage tags stable once assigned. The pristine template has no
physical partitions or saved source attachments. A derived binder supplies a
target-local SQL-endpoint expression and every `ONT_*` pin
([why](ontology-knowledge.md#bind-through-the-sql-analytics-endpoint)); review the
service-normalized export.
Do not perform global GUID or text replacement.

### Seed and deploy in a fixed order

1. Create the schema-enabled lakehouse definition.
2. Deploy and execute the seeder: validate schemas/keys and insert missing
   fictional rows only. Embed the reviewed seed payload in the native notebook
   or use an approved supported data source; a repository-side Python package
   is not automatically available in Fabric.
3. Deploy the ontology definition, then execute its explicit schema-specific
   binder. Preserve identity checks, unrelated definition parts, bounded SQL
   readiness, failure propagation and unchanged-rerun behavior.
4. Publish only reviewed document bytes into a `knowledge_folders` folder of the
   knowledge lakehouse's Files area, insert-only. Keep the data/knowledge roles
   distinct when the scenario needs separate lakehouses.
5. Resolve physical DEV item IDs and review the normal Terraform knowledge plan.
6. Verify actual source data and retrieval, not only import or notebook success.

Encode prerequisites as native plan group dependencies, not file order.
Deploy each executable notebook before its action. Attach the reviewed plan
explicitly; if the preview option is unavailable, stop for a user-approved
native sync plus explicit notebook execution path. Never silently omit actions.

The current [seeder](../workspace/SeedScenario.Notebook/notebook-content.py),
[binder](../workspace/BindScenario.Notebook/notebook-content.py) and
[plan](../workspace/Bootstrap.DeploymentPlan/plan.yml) demonstrate the safety
contract. Their Site/Asset-specific identity/mapping checks are not a generic
rewriter for another domain.

## Knowledge and indexing work package

Use the agents' knowledge base for documents, with one source per folder in
`knowledge_folders`; attach only the folders the agents may cite. `infra/knowledge.tf`
owns Search data-plane sources/base lifecycle; do not hand-manage its generated
indexes or create duplicate registrations from an authoring plugin.

Store authoritative text in `scenario/knowledge/`. A native
`Publish<Domain>Knowledge.Notebook` publishes only its reviewed bytes, verifies
readback and rejects conflicting content rather than overwriting it. Keep
document IDs, versions, effective periods and authority policy explicit.

OneLake indexing consumes **Files**, not Delta Tables. Register the physical DEV
ontology in the existing `knowledge_ontologies` map only after binding: its live
`fabricOntology` source joins the check-only knowledge base, and the with-IQ agent
reaches it through its Fabric IQ connection, as the user. An ontology, graph or
semantic model does not become an agent source merely by existing.

Distinguish these three mechanisms:

| Mechanism | Ownership and required work |
| --- | --- |
| Search document indexing/vectorization/semantic ranking | Search/Foundry IQ; controlled corpus, compatible model, ingestion status and citation checks. |
| Microsoft 365 semantic indexing | Microsoft 365/Work IQ; tenant-owned permissions, connectors and eligibility. Not another Terraform Search index. |
| Fabric semantic model | A native `workspace/<Name>.SemanticModel/` item; source binding plus refresh/Direct Lake readiness and exact measure parity. |

## Agent and app work package

Start from the shipped read-only agents and app; [getting started](getting-started.md#7-create-the-agents-and-app)
publishes them. For a scenario, change:

- **The shared instructions**, [`instructions.md`](../agents/scenario-with-iq/instructions.md),
  byte for byte in every variant. They define what the assistant does, which tool
  each part of a question needs, source authority, unavailable or denied-source
  behavior, the read-only rule and the reply format. They hold no document content
  or facts, because the without-IQ variant shares them; they stay within 8,000
  characters and contain no braces. They don't invent deterministic metrics or
  authorize actions.
- **The tool descriptions** in `agents/scenario-with-iq/tools/`, at most 1,024
  characters each, which carry what is specific to a tool: Fabric IQ asks one
  question about one entity with quoted values, Foundry IQ cites title and section
  from the passage, Web IQ queries only public terms, and Work IQ fetches one fixed
  path. Keep each tool's `server_label`, its connection placeholder, its read-only
  `allowed_tools` and `require_approval: never`.
- **The reply format**, if at all, together with the app's parser
  [`app/src/live/reply.ts`](../app/src/live/reply.ts), its tests and the mock answers
  in [`app/src/mock/client.ts`](../app/src/mock/client.ts)
  ([reply format](architecture.md#reply-format)).
- **The sample questions** in [`app/src/samples.ts`](../app/src/samples.ts), and the
  runner's readiness questions and expected facts in its `SCENARIO` descriptor
  when the seed data changes.

A definition may use only the runner's placeholders: `agent_display_name`,
`agent_model_deployment`, `agent_reasoning_effort`, `knowledge_base_mcp_endpoint`,
`knowledge_base_connection`, `public_context_mcp_endpoint`,
`public_context_connection`, `fabric_iq_mcp_endpoint`, `fabric_iq_connection`,
`work_iq_mail_mcp_endpoint` and `work_iq_mail_connection`. `foundry_agent.py validate`
refuses any other, an inlined instruction or tool, a tool with other keys or
headers, an approval step, an empty allow-list and shared files that differ between
variants. A new tool needs its own reviewed project connection and identity decision
in Terraform, a placeholder, and an IQ for its server label in the app
(`SERVER_IQ` in [`app/src/live/protocol.ts`](../app/src/live/protocol.ts)), never a
shared key.

To show what one IQ adds, [add a variant](../agents/README.md#add-a-variant) such as
`scenario-no-fabric`: byte-identical shared files, the with-IQ tools without that
IQ's, a `VARIANTS` entry in `foundry_agent.py`, its agent in Terraform's
`foundry_agents` output and the container app's settings, and the app's variants.

The app asks the agents only through its own server, `POST /api/turns`, which
verifies the signed-in user's token and forwards it to the configured project's
Responses API for the variant's agent. Keep conversations isolated per user: the app
holds only the latest response ID, and Foundry owns the conversation history and who
may continue it.

Build scenario views on the shell's sign-in, variant switch, tool timeline and
failure states. Missing sign-in, missing agent access (HTTP 403), a pending tool
consent link, the 225-second turn limit and source errors are explicit
blocked/incomplete states. Do not return synthetic success or indexed snapshot
facts as live data. Releases follow the runner's `agent-publish` and `app-build`
approvals and the reviewed app plan; granting other users access is a separate
operator step.

## Additional runtime integrations

| Capability | Required addition and exit evidence |
| --- | --- |
| Work IQ | Shipped as the opt-in mail tool (`work_iq_enabled`): one read-only `fetch` of the user's own mail through OAuth identity passthrough, with the [prerequisites in getting started](getting-started.md#work-iq). Extend it only within read-only, user-delegated access and cited accessible work context. No mailbox clone or fixture masquerading as real work data. |
| Web IQ | Standard: the Terraform account, the runner's probe and the agents' public-context tool ([preparation](../preparation.md#web-iq)). Portal AppID linking is a manual gate; require public-only queries, citations and scenario freshness/conflict checks. |
| Fabric graph | An approved native ontology projection/model, target-local bindings, materialization/refresh and bounded path-result checks. No second hand-authored relationship vocabulary. |
| Semantic model | Native `.platform`, `definition.pbism` and exported TMDL definition parts, explicit binding/refresh action and exact governed calculation checks. |
| Fabric data agent | Native definition/published endpoint, allowed sources, reader authorization and actual query receipts. Not a replacement for deterministic scenario rules. |
| Other channel or IQ surface | A published native format/connection contract, explicit ownership, required permissions and a bounded readiness proof before declaring support. |

The [official product packages](copilot-tooling.md) supplement these recipes.
Installing them never enables an opt-in runtime capability or authorizes a
mutation. Disabling an optional capability must leave the mandatory core usable
and display that capability's unavailable status.

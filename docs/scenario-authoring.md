# Scenario authoring contract

Derived projects change the domain and workflow, not the platform architecture.
Use one **Fabric -> knowledge -> agent -> Managed Runtime app** core.
Work IQ, Web IQ, graphs, semantic models and other channels are explicit opt-ins.

The template ships this core end to end: the Fabric/knowledge baseline, the
[scenario assistant](../agents/scenario-assistant/README.md) Copilot Studio agent
and the [Managed Runtime app](../app/) that calls it. A derived project adds its
scenario in the homes below; it does not replace the shell.

## One home per responsibility

| Home | Put here | Do not put here |
| --- | --- | --- |
| `infra/` | Terraform infrastructure, permissions and knowledge/runtime connection registration. | Business logic, seed provisioners, another deployment state or an azd/Bicep replacement platform. |
| `workspace/` | Native Fabric item folders, seed/transform/publish/bind/readiness notebooks and deployment plans. | Credentials, saved attachments, raw backups or another project's physical bindings. |
| `scenario/data/` | Versioned domain/table contracts, deterministic synthetic generation and small fictional fixtures. | Real tenant/customer data or evaluation answers destined for indexing. |
| `scenario/knowledge/` | Reviewed authoritative document sources, with IDs, versions, scope and effective dates. | Raw fixture bundles, temporary exports, golden answers or mirrored mailboxes. |
| `scenario/workflows/` | Independently testable scenario-specific rules, transformations and approved tool logic. | An undeclared backend or a second source of authoritative facts. |
| `scenario/evals/` | Expected outcomes, source/citation requirements, negative cases and scenario checks. | Content published to a lakehouse Files corpus. |
| `agents/scenario-assistant/` | The Copilot Studio agent's instructions, topics and IQ tool actions, with placeholders that `studio_agent.py render` fills. | Rendered PAC workspaces, `.mcs/` metadata, access tokens, tenant IDs, deployment receipts or `Maker`-mode tools. |
| `app/` | Managed Runtime application source. `app/src/config.ts` names the agent; scenario views call it through the generated service. | Raw browser calls to Azure/Fabric, edits to `generated/` or credentials copied from a developer's sign-in. |
| `docs/`, `.github/skills/` | Canonical product explanations and repeatable procedures, respectively. | Session diaries or competing setup guides. |

Use these locations rather than adding competing root `tools/`, `services/`,
`evals/` or `apps/` trees. A genuinely necessary deviation requires an explicit
design decision. Never infer permission to provision a backend from a Python
workflow module.

The repository root is Managed Runtime's verified native app root:
`package.json`, `index.html`, CLI-managed `ms.config.json` and `generated/`.
Application implementation stays in `app/`. Do not nest a second Git
repository or force every tool's native configuration into `app/`.

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
physical partitions or saved source attachments. A derived binder supplies
target-local OneLake and `ONT_*` pins; review the service-normalized export.
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
4. Publish only reviewed document bytes into the selected knowledge lakehouse's
   Files area. Keep the data/knowledge roles distinct when the scenario needs
   separate lakehouses.
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

Use one knowledge base by default. `infra/knowledge.tf` owns Search data-plane
sources/base lifecycle; do not hand-manage its generated index or create
duplicate registrations from an authoring plugin.

Store authoritative text in `scenario/knowledge/`. A native
`Publish<Domain>Knowledge.Notebook` publishes only its reviewed bytes, verifies
readback and rejects conflicting content rather than overwriting it. Keep
document IDs, versions, effective periods and authority policy explicit.

OneLake indexing consumes **Files**, not Delta Tables. Native `fabricOntology`
sources retrieve live bound data separately. Register the physical DEV ontology
in the existing `knowledge_ontologies` map only after binding. An ontology,
graph or semantic model does not become an agent source merely by existing.

Distinguish these three mechanisms:

| Mechanism | Ownership and required work |
| --- | --- |
| Search document indexing/vectorization/semantic ranking | Search/Foundry IQ; controlled corpus, compatible model, ingestion status and citation checks. |
| Microsoft 365 semantic indexing | Microsoft 365/Work IQ; tenant-owned permissions, connectors and eligibility. Not another Terraform Search index. |
| Fabric semantic model | A native `workspace/<Name>.SemanticModel/` item; source binding plus refresh/Direct Lake readiness and exact measure parity. |

## Agent and app work package

Start from the shipped read-only agent and app; [getting started](getting-started.md#7-create-the-agent-and-app)
creates them. The agent is a published standard-harness Copilot Studio agent,
because the Foundry Agent Service connector is blocked and the Studio connector
refuses GitHub Copilot harness agents; see
[routes to re-validate](runtime-integration.md#routes-to-re-validate).
For a scenario, change the agent's instructions and tool descriptions in
`agents/scenario-assistant/`. Instructions define source authority, routing,
unavailable/denied-source behavior and inline source names; they do not invent
deterministic metrics or authorize actions.

The Fabric IQ tool supplies delegated live facts and the Foundry IQ tool supplies
document evidence, each through the user's own connection (`Invoker` mode).
Keep the pinned knowledge `api-version`. A Search `fabricOntology` query still
needs its separate Search-audience user header; managed identity or plugin
installation cannot replace it.

The app calls the agent only through the generated Copilot Studio service and
`app/src/studio.ts`, which rejects receipts without answers, the harness refusal
and malformed results. Keep conversations isolated per user and never accept
client-side possession of a conversation ID as authorization.

Build scenario views on the shell's conversation, connection check and failure
states. Missing identity, pending connection consent, the 120-second connector
timeout and source errors are explicit blocked/incomplete states. Do not return
synthetic success or indexed snapshot facts as live data. Hosted builds,
deployment and sharing are separate approval gates.

## Opt-in capabilities

| Capability | Required addition and exit evidence |
| --- | --- |
| Work IQ | A native approved user-delegated connection/tool, scoped read-only first, tenant consent/licensing and cited accessible work context. No mailbox clone or fixture masquerading as real work data. |
| Web IQ | Approved service availability/terms, public-only query fields, citations and freshness/conflict checks. Replay and live modes are explicitly different. |
| Fabric graph | An approved native ontology projection/model, target-local bindings, materialization/refresh and bounded path-result checks. No second hand-authored relationship vocabulary. |
| Semantic model | Native `.platform`, `definition.pbism` and exported TMDL definition parts, explicit binding/refresh action and exact governed calculation checks. |
| Fabric data agent | Native definition/published endpoint, allowed sources, reader authorization and actual query receipts. Not a replacement for deterministic scenario rules. |
| Other channel or IQ surface | A published native format/connection contract, explicit ownership, required permissions and a bounded readiness proof before declaring support. |

The [official product packages](copilot-tooling.md) supplement these recipes.
Installing them never enables an opt-in runtime capability or authorizes a
mutation. Disabling an optional capability must leave the mandatory core usable
and display that capability's unavailable status.

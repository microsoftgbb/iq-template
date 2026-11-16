# Seeded ontologies in Foundry IQ

The template includes a Git-owned **Site/Asset ontology**, seeded lakehouse data, a starter document and a deployment-plan action that binds them in the target workspace. Terraform registers the lakehouse's document folders as indexed knowledge sources of the agents' knowledge base, and this and additional DEV ontologies as live sources of a separate, **check-only** knowledge base.

This uses the native [`fabricOntology` knowledge source (preview)](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-fabric-ontology), not document export or a custom MCP proxy. The source queries Fabric at retrieval time; it doesn't create an index, copy table rows to Search, or use the folder sources' five-minute schedule.

## Two retrieval paths

[![Indexed OneLake document folders feed the agents' knowledge base; the optional live ontology source sits in a check-only knowledge base that only the runner's verify queries, and Web IQ feeds its own public-context knowledge base, with distinct indexing and end-user authorization.](diagrams/knowledge-retrieval.svg)](diagrams/knowledge-retrieval.svg)

One-way arrows show indexed content flowing toward a knowledge base; paired
arrowheads show live request/response interactions, not data replication. Each
[knowledge folder](#knowledge-folders) becomes its own `indexedOneLake` source,
checked every five minutes. The agents' knowledge base, `<project>-knowledge`, holds
only the attached folders, never an ontology. The live `fabricOntology` sources sit
in `<project>-ontology-check`, which only the runner's `verify` queries, with the
deployer's own token. Web IQ feeds its own `<project>-public-context` base. The
with-IQ agent reads live facts through **native Fabric IQ** as the signed-in user
instead, as the [runtime diagram](runtime-integration.md#runtime-at-a-glance) shows.
Registering the Search source doesn't remove its end-user token requirement.

[Editable Excalidraw source](diagrams/knowledge-retrieval.excalidraw) | [All diagrams](diagrams/README.md) | [Icon sources](diagram-authoring.md#official-icon-sources)

## Included scenario

| Ontology definition | Backing data |
| --- | --- |
| `Site`: SiteId, SiteName, Region | `Scenario.dbo.demo_sites` |
| `Asset`: AssetId, AssetSiteId, AssetName, Status | `Scenario.dbo.demo_assets` |
| `LocatedAt`: Asset -> Site | `demo_assets.site_id -> demo_sites.site_id` |

[`ScenarioOntology.Ontology`](../workspace/ScenarioOntology.Ontology) uses the [new-experience TMDL definition](https://learn.microsoft.com/rest/api/fabric/articles/item-management/definitions/ontology-definition), with stable logical and lineage IDs. The initial backing tables deliberately have **no partitions**: they define the shape without embedding someone else's workspace/SQL endpoint. The ontology is not data-query-ready until binding completes.

The [`Bootstrap` plan](../workspace/Bootstrap.DeploymentPlan/plan.yml) runs this sequence:

1. Deploy `Scenario`, then deploy and execute `SeedScenario` to insert missing synthetic rows and `Files/documents/starter.txt`, the document the default `documents` folder source indexes. It leaves a legacy `Files/starter.txt` untouched.
2. Deploy `ScenarioOntology` with its entities, properties and relationship.
3. Deploy and execute `BindScenario`. It resolves both items in the **current workspace**, waits for the lakehouse's SQL analytics endpoint, refreshes SQL metadata for only `dbo.demo_sites` and `dbo.demo_assets`, and checks both per-table results.
4. The binder adds the sample's Direct Lake partitions and the `ScenarioLakehouse` expression, which reads the lakehouse's SQL analytics endpoint, and pins every partition's `ONT_*` annotations. It then reads the definition back, checks its partitions, expression and pins, and prints the lakehouse/ontology GUIDs.

### Bind through the SQL analytics endpoint

The `ScenarioLakehouse` expression reads the lakehouse's SQL analytics endpoint:
`Sql.Database("<endpoint host>", "<endpoint GUID>")` in an M `let ... in ...` block.
Every partition carries eight `ONT_*` pins for that lakehouse:

| Pin | Value |
| --- | --- |
| `ONT_WorkspaceId`, `ONT_WorkspaceName` | The current workspace |
| `ONT_ItemId`, `ONT_ItemKind`, `ONT_ItemName` | The physical **lakehouse** GUID, `Lakehouse` and the lakehouse's display name |
| `ONT_SqlEndpoint` | The SQL endpoint host |
| `ONT_SqlDatabase` | The lakehouse's **display name**, not the SQL endpoint GUID, which appears only in the expression and the metadata refresh |
| `ONT_PinnedAtUtc` | When the pins were written; kept on an unchanged rerun |

Fabric keeps these pins only with the `Sql.Database` form: it strips all but
`ONT_PinnedAtUtc` from an `AzureStorage.DataLake` (OneLake) expression. Queries still
work without the pins, but Fabric then can't identify the source's item kind, and
graph materialization reports every entity as *Unsupported Data Source*. So the
binder writes the SQL form, requires every pin on read-back and replaces a OneLake
expression that an earlier binder wrote. Fabric can store the expression with ALM
tokens, `Sql.Database("ONT_ALM_SQL_ENDPOINT", "ONT_ALM_SQL_DATABASE-Lakehouse:<workspace>:<lakehouse>")`,
which it resolves from the pins. The binder treats that form as bound when its tokens
name this workspace's lakehouse and every pin matches. The runner's binding check
requires the workspace, lakehouse and item-kind pins on every partition and an
expression, in either form, that reads this DEV lakehouse.

Fabric Git normalizes `ONT_WorkspaceId` to a zero GUID, `ONT_ItemId` to the lakehouse's logical ID, and `ONT_WorkspaceName` to a placeholder. These are Git representations, not runtime source values. Rebind each target and inspect its live definition and data-backed query; do not treat the exported placeholders or an unchanged expression as proof of target readiness.

[`BindScenario`](../workspace/BindScenario.Notebook/notebook-content.py) has a 15-minute default deadline, honors throttling/polling delays and surfaces failures. It uses the Fabric runtime's `notebookutils` credential; no token is in Git or Terraform. Its identity needs workspace Contributor or higher and the API permissions to list/read items, refresh SQL metadata, and read/write ontology definitions.

**Run the bundled binding action as an authorized Fabric user.** [NotebookUtils `pbi` tokens have restricted scopes under service principals](https://learn.microsoft.com/fabric/data-engineering/notebookutils/notebookutils-credentials#considerations), omitting the full Fabric scopes needed here. Unattended service-principal execution needs an explicitly designed authentication flow in the derived project; the notebook does not switch identities or fall back to stored credentials on a permission failure.

The binder is intentionally **sample-specific**, not a generic TMDL rewriter. It checks the logical ID and generation in ontology item metadata, the `BootstrapBinding` annotations on both entities, and the source expression's lineage tag. It preserves unrelated definition parts and business rules, and refuses custom partitions or source expressions rather than guessing how to overwrite them. Use the default definition endpoint and verify its TMDL parts: an explicit `format=TMDL` can be rejected, exported `.platform` can contain a placeholder logical ID, and model-level annotations can be omitted. Entity-level annotations survive the round trip. A correctly bound rerun does not update the ontology again. It neither seeds data nor materializes graphs.

### Review the generated binding

Review the generated partitions, expression and service-normalized TMDL before promotion. With native bootstrap, use Get Definition to inspect or export the live binding; with opt-in Git synchronization, review and commit it through Fabric source control. Confirm one Direct Lake partition per table on schema `dbo` with expression source `ScenarioLakehouse`, an expression or ALM tokens that name this workspace's `Scenario` lakehouse, every pin, both entities' `BootstrapBinding` markers and a data-backed answer in Fabric. Endpoint IDs are configuration, not credentials. Preserve the binding markers in any reviewed export.

Native promotion copies those definitions, including their source references. Always attach the plan so `BindScenario` rebinds the target to its **own** lakehouse before it is used. Target-specific binding differences are expected; don't mistake them for unreviewed business-model changes or overwrite them with DEV values.

Serialize deployment and ontology authoring. The public `updateDefinition` API replaces the definition and has no conditional-update header; the notebook checks for an intervening change before updating, but cannot eliminate a concurrent-edit race. If an operation fails or times out, inspect its status before rerunning. Don't expose a partially deployed/unbound ontology to consumers.

## Register the folders and the ontology

After bootstrap, the runner's `knowledge` phase writes the **physical DEV item GUIDs** it discovered into the same local tfvars file used for platform setup, with its [knowledge helper](../.github/skills/deploy-platform/scripts/configure_knowledge.py), then runs a reviewed plan and apply. By hand, check the entities and a data-backed question in Fabric, then set:

```hcl
knowledge_lakehouse_id = "<dev-Scenario-lakehouse-guid>"
knowledge_ontologies = {
  scenario = {
    ontology_id = "<dev-ScenarioOntology-guid>"
    description = "Current sites, assets, their status, and which site each asset belongs to."
  }
}
```

Run the normal knowledge plan/apply from [getting started](getting-started.md#6-complete-knowledge-setup), with the Web IQ key it needs. No targeted apply is needed.

### Knowledge folders

`knowledge_folders` maps a stable short key to `{ description, target_path, attach }`.
Each entry becomes the `indexedOneLake` source `<project>-<key>`, which indexes
`target_path` under the lakehouse's **Files** area, the only area the OneLake indexer
reads, and checks it for changes every five minutes with the embedding model. Give
`target_path` without a `Files/` prefix or surrounding slashes: `documents` indexes
`Files/documents`. The default is one attached folder, `documents`, described as
"Starter documents", which holds `starter.txt`.

- `attach = true` adds the source to the agents' knowledge base. A detached source is
  still created and indexed, so someone in `knowledge_editor_principal_ids`, who gets
  Search Service Contributor, can attach it live in the Foundry portal without
  waiting; re-applying the knowledge plan detaches it again. At least one folder must
  be attached.
- An override replaces the whole default map, so copy the `documents` entry you keep.
  Keys can't be `web` or end in `-ontology`: those source names belong to the Web IQ
  and ontology sources.
- Removing a key rebuilds the knowledge base before deleting the source, because
  Search refuses to delete a source that a base references.
- `knowledge_base_name` optionally renames the agents' knowledge base (default
  `<project>-knowledge`). It must differ from the check-only and public-context bases;
  renaming replaces the base, and the agents follow the `knowledge_base_name` output.

The agents' knowledge base runs at `minimal` retrieval effort with extractive output:
Search returns the matching passages, each with its file's URL, and runs no model, so
the agent writes and cites the answer. Upload documents into an attached folder, not
Tables; folder-source or embedding changes rebuild the index. The
`knowledge_source_names` and `knowledge_source_status_urls` outputs, keyed by folder,
identify each source and its ingestion status.

### Ontologies

Terraform looks up each ontology in DEV without owning its definition, creates a native `fabricOntology` source named `<project>-<key>-ontology`, and puts it in the check-only knowledge base `<project>-ontology-check`, never in the agents' one. It also creates the Fabric IQ `UserEntraToken` connection `fabric-<key>`; the with-IQ agent's Fabric IQ tool uses `fabric-scenario`. The description helps the retrieval model select the right source.

Use a separate stable map key for each additional ontology. Export its real definition to `workspace/`, supply its seed/binding actions and plan dependencies, then add its physical DEV GUID and domain description to `knowledge_ontologies`. Don't duplicate the sample logical ID for a different ontology. The reusable Terraform registration is not limited to the Site/Asset schema.

Leave the map empty to use only indexed documents; the agent phase then has no Fabric IQ connection for the with-IQ agent. Nonempty registrations require `knowledge_lakehouse_id`; they belong to knowledge setup after Fabric bootstrap. Removing an entry rebuilds the check-only base and removes its Search knowledge-source registration and Fabric IQ connection, **not** the Fabric ontology or data. Ontology parameter changes update the live source in place; no index rebuild is needed.

Outputs `ontology_knowledge_sources`, `ontology_check_knowledge_base_name` and `knowledge_base_requires_user_token` identify the sources and the runtime authorization requirement. Terraform registration is not evidence that a user can successfully query the ontology.

## Supply the end user's identity at query time

There are two separate authentication layers:

| Layer | Requirement |
| --- | --- |
| Caller -> Azure AI Search knowledge base | Normal Search authentication and `Search Index Data Reader` for an Entra caller. The agents' Foundry IQ and Web IQ connections use the project's managed identity for this hop; the runner's `verify` uses the deployer's own token. |
| Knowledge base -> Fabric ontology | An **end-user** token in `x-ms-query-source-authorization`, scoped to `https://search.azure.com/.default`. Search exchanges it for a Fabric-scoped token on the user's behalf. The user must be allowed to read the ontology and its bound data. |

The Search and Fabric resources must share an Entra tenant. Enable the Fabric ontology tenant settings and complete any tenant consent required for the documented delegated flow. **Do not use the Search or Foundry managed identity token as a substitute for the end-user token.** Ontology retrieval does not inherit access from the seeding identity or Search's workspace Contributor grant.

The Terraform `fabric_user_auth` module prepares **deployment-user** access for the Fabric provider, Git/bootstrap and promotion. Its registration, exposed `access` scope and admin approval do not implement a consuming application's sign-in or the Search-to-Fabric runtime delegation. Keep those identities and consent requirements distinct; never store an end-user token in Terraform.

For a direct retrieval request against the check-only knowledge base, the header arrangement is:

```http
POST <search-endpoint>/knowledgebases/<project>-ontology-check/retrieve?api-version=2026-08-01-preview
Authorization: Bearer <search-caller-access-token>
x-ms-query-source-authorization: <end-user-search-audience-access-token>
Content-Type: application/json

{
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Using the live ScenarioOntology, which Asset rows have the Status value \"Maintenance\", and at which site is each asset located? Return asset names and site names."
        }
      ]
    }
  ]
}
```

Quote the values in a question: unquoted, Fabric can read a bare value as an empty one. Tokens are placeholders, **not configuration values to store in tfvars or source control**. Acquire the user's token in your application's approved sign-in/OBO flow, not from a chat prompt. Forward it per request, protect logs, and refuse an ontology query if the required identity is missing.

For a consuming Foundry agent, a knowledge-base MCP connection would need this per-request header on its MCP tool, through [structured inputs or supported OAuth identity passthrough](https://learn.microsoft.com/azure/foundry/agents/how-to/foundry-iq-connect#optional-enforce-permissions-with-per-request-headers); creating the managed-identity connection alone does not complete ontology authentication. The shipped agents therefore never query the check-only base: the with-IQ agent reads live facts through its [Fabric IQ tool](runtime-integration.md#iq-tools) as the signed-in user, and its knowledge-base tools read only documents and public context with the project's managed identity.

For an untouched seed, the example question should identify **Pump B at North Works**. Inspect the retrieval response for an `activity` entry and `references` of type `fabricOntology`, with the expected workspace and ontology IDs. A correct-sounding answer from `starter.txt` alone is **not** proof of ontology integration. Include a user without Fabric access in your derived project's authorization checks and surface denied/failed sources rather than silently substituting the document snapshot.

## Extending and promoting

Put consumers that need bound data after the completed **BindScenario** group, not merely after the lakehouse/seed group. For a different schema, adapt a separate binder with explicit source mappings and preserve the public definition format; don't turn the sample into an unrestricted find/replace script.

`BindScenario` waits for the two tables' SQL metadata, but does not perform a graph load or semantic-model refresh. Those consumers still need their own readiness/refresh steps. PROD should normally omit demo seeding and use approved data ingestion; binding to a target-local lakehouse remains necessary.

Both ontology and Foundry IQ source integration are preview features. Review Fabric/Foundry availability, costs and compliance boundaries for the deployment region. No live deployment or query is performed as a template-maintenance check.

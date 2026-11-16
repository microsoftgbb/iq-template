---
name: fabric-workspace
description: Edit and review Fabric Git items, notebooks, Site/Asset ontology bindings and deployment plans. Use when checking hardcoded workspace references, renaming sample items, bootstrapping DEV, troubleshooting binding, or promoting a reviewed release through native Fabric pipelines.
---

# Work with portable Fabric items

Read `AGENTS.md`, `cicd/README.md` and `docs/ontology-knowledge.md`. Separate repository edits/static checks from live operations. Never deploy or execute notebooks to validate template maintenance.

## Review or customize definitions

1. Inspect `.platform` metadata, deployment-plan group/action references, notebook parameter cells and TMDL together. Preserve logical IDs across environments; do not confuse them with physical workspace/item/SQL endpoint GUIDs.
2. Classify literal values before changing them. Generic display names (`Scenario`, `ScenarioOntology`), `dbo`, demo table/column names, lineage tags, `ScenarioLakehouse` and `BootstrapBinding = site-asset-v1` are the sample contract, not leaked deployment configuration. Service/schema URLs are public API contracts, not a deployment target.
3. Keep a fresh template's notebook dependencies empty and its ontology tables without partitions/physical source expressions. Resolve runtime workspace identity, lakehouse IDs and the SQL analytics endpoint in Fabric. Never copy exported DEV attachments, SQL hostnames, ALM tokens or OneLake GUID URLs into the reusable template.
4. If renaming sample items, update `.platform` display names, both notebooks' parameter defaults, plan parameters/group dependencies, relevant messages/starter text and docs together. Keep folder names understandable and preserve each item's logical ID. Do not rename entity/table mappings or replace lineage tags as a branding operation.
5. For a genuinely different ontology, use its own logical ID, explicit source mappings and binding action. The sample binder deliberately refuses unknown partitions/expressions; do not weaken that check into a general find/replace engine.
6. Preserve executable `# PARAMETERS CELL` delimiters. A Markdown heading or language-metadata tag is not a Fabric parameter cell. Keep secrets out of notebook metadata and plan parameters.
7. Preserve insert-only seeding, schema/non-Delta checks, current-workspace GUID paths, one seeder per workspace and the explicit Boolean opt-in outside `*-dev`/`*-test`. The starter document lives at `Files/documents/starter.txt`, the folder the default `knowledge_folders` entry `documents` indexes; an existing file is never overwritten, and a legacy `Files/starter.txt` is left untouched. Do not turn bootstrap into drift reconciliation or delete existing content.
8. Preserve the binder's item-generation/logical-ID/entity-marker checks, source lineage tag, scoped SQL refresh results, bounded polling, throttling handling, business rules, unrelated definition parts and unchanged-rerun behavior. Keep the SQL-endpoint expression, every `ONT_*` pin and the read-back that requires them; the binder replaces a OneLake expression and accepts Fabric's ALM-token form. Authoring and deployment must be serialized because `updateDefinition` has no conditional-write header.

Parse changed Python and JSON locally, and check that every plan logical ID resolves to the intended `.platform` item. Check group dependencies and both notebook parameter names, not merely YAML syntax. Run the offline binder and seeder tests, which load the notebooks with fake NotebookUtils, Spark and Fabric responses: `python3 -B -m unittest discover -s .github/skills/fabric-workspace/tests`. Keep the runner's `SCENARIO` descriptor in `deploy.py` in step with renamed items, plan parameters, tables and readiness facts. Do not execute notebooks outside the authorized Fabric runtime.

## Bootstrap or promote only when explicitly requested

Confirm the derived repository, reviewed commit, target workspace and caller identity. The runner's `bootstrap` phase imports DEV definitions through native Fabric APIs by default, without a PAT. Git sync is opt-in with `fabric_git_enabled = true`; follow `cicd/README.md`. This skill is not an unattended deployment wrapper.

- Run the bundled binder as an authorized Fabric **user**. Service-principal NotebookUtils tokens lack the required scopes; unattended authentication needs a separately designed flow, not a token fallback.
- Attach the reviewed plan explicitly for every operation. Native imports and Git updates use the plan's logical ID (`ByLogicalId`); pipeline deployments use its physical item ID (`ByItemId`). Keep required API fields, poll to completion and surface failed actions.
- Review generated DEV bindings before promotion; export reviewed definitions or commit through source control when Git is enabled. Native pipelines copy live definitions, not a Git checkout or data; freeze the source. Rebind each target to its own lakehouse and verify readiness before exposing it.
- Bind through the lakehouse's SQL analytics endpoint, `Sql.Database("<sql endpoint host>", "<sql endpoint id>")`, with all eight `ONT_*` pins on every partition, as the [ontology guide](../../../docs/ontology-knowledge.md#bind-through-the-sql-analytics-endpoint) describes: the SQL database pin is the lakehouse display name, not the SQL endpoint GUID. Fabric keeps the pins only in this form and may store it as ALM tokens (`ONT_ALM_SQL_ENDPOINT`, `ONT_ALM_SQL_DATABASE-Lakehouse:<workspace>:<lakehouse>`). A OneLake (`AzureStorage.DataLake`) expression loses the pins, and graph materialization then reports *Unsupported Data Source*; rebind it. Git-normalized zero/logical IDs and workspace-name placeholders are not runtime readiness evidence.
- Treat target-binding differences separately from business-model revisions. Do not overwrite them with DEV values. Do not automatically delete orphans or resolve Git deletions/conflicts in favor of the incoming source.
- Keep PROD infrastructure/approvals and its non-demo release plan in the derived project. Do not weaken the sample seeding guard.

After successful DEV binding, use physical DEV lakehouse/ontology GUIDs for Terraform's knowledge stage. Native `fabricOntology` retrieval, in the check-only knowledge base, needs the end user's Search-audience token; Search/Foundry managed identities do not substitute for it. Validate `fabricOntology` activity/references and denied access, not just an answer reproduced from `starter.txt`.

Keep SQL metadata, indexing, graph materialization and semantic-model refresh distinct. The binder refreshes only the two sample tables; add explicit bounded readiness actions for additional consumers instead of assuming seed completion makes everything ready.

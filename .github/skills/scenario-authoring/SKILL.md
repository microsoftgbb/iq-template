---
name: scenario-authoring
description: Add or adapt a domain workflow using the template's fixed Fabric, knowledge, agent and Managed Runtime artifact contract. Use for a new PoC scenario, ontology/seed work package, governed documents, agent instructions or explicit IQ opt-ins. Do not redesign the platform, agent route or app shell.
---

# Author a scenario without redesigning the shell

Read `AGENTS.md`, `docs/scenario-authoring.md`, `docs/runtime-integration.md` and
the current project diff. Use `fabric-workspace` for native item/identity rules,
`initialize-project` for first-use local configuration and `deploy-platform`
only for an explicitly requested deployment.

## Freeze the small domain contract

Resolve the question/workflow, stable business keys, table grain, field types,
calculation ownership, relationships, authoritative document versions and
expected source-backed result. Reuse choices the user supplied. Ask one focused
question for a genuine missing behavioral/design decision, not branding or
folder preferences already prescribed by the template.

Keep the canonical homes: `infra/`, `workspace/`, `scenario/data`,
`scenario/knowledge`, `scenario/workflows`, `scenario/evals`,
`agents/scenario-assistant` and `app/`. Do not create competing root tool/evaluation/service trees or
silently add a backend/channel. A different ontology receives new identities;
do not turn the sample binder into unrestricted text substitution.

## Produce one complete work package

1. Author the lakehouse, all native ontology TMDL parts, seeder, schema-specific
   binder and deployment plan together. Preserve native `.platform`/lineage
   identities, executable parameter cells and target-local discovery.
2. Make fictional fixtures reproducible. Put deterministic business calculations
   in governed data/workflow logic, not model instructions. Notebook deployment
   must carry its reviewed payload; repository modules are not a Fabric runtime.
3. Add reviewed document source plus a target-local publisher when needed.
   Publish only the permitted corpus; never index golden answers or raw bundles.
4. Reuse existing Search sources/base lifecycle and register bound physical
   DEV items through the same approved knowledge stage. Do not create duplicate
   resources from a product plugin.
5. Adapt the shipped agent's instructions and tool descriptions in
   `agents/scenario-assistant/`, keeping both tools in `Invoker` mode. Add
   scenario views in `app/` on the shell's Studio service and failure states;
   preserve source authority and per-user isolation.
6. Put independent expected outcomes and negative/source/authorization cases
   in `scenario/evals/`. A static fixture is not a live model/retrieval proof.

Review the whole dependency graph, not only YAML syntax. Additional graph,
semantic-model and data-agent consumers need their own explicit binding,
refresh/materialization and bounded readiness actions.

## Keep operations and claims bounded

Repository edits/static checks require no live cloud execution. Live work
requires the chosen derived target, reviewed revision, one host/state, exact
plan approvals and the native runtime identity. Never execute a notebook or
deployment test as template maintenance.

Prove the native agent/app route before saying the scenario shell is ready.
An allowed catalog, healthy connection record, green build or SDK-only smoke
is insufficient. Native OBO/decryption, denied-source and safety-service errors
remain blockers; never switch identities, expose tokens or recreate a partially
registered app to get a plausible answer.

Use product skills from `docs/copilot-tooling.md` for opted-in capabilities, but
retain repository ownership/approval rules. Unsupported capabilities stop for
a design decision rather than becoming scenario-specific improvisation.

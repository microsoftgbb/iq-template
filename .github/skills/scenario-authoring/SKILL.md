---
name: scenario-authoring
description: Add or adapt a domain workflow using the template's fixed Fabric, knowledge, prompt agent and web app artifact contract. Use for a new PoC scenario, ontology/seed work package, governed documents, agent instructions or variants, or explicit IQ opt-ins. Do not redesign the platform, agent route or app shell.
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
`scenario/knowledge`, `scenario/workflows`, `scenario/evals`, `agents/<variant>/`
and `app/`. Do not create competing root tool/evaluation/service trees or
silently add a backend/channel. A different ontology receives new identities;
do not turn the sample binder into unrestricted text substitution.

## Produce one complete work package

1. Author the lakehouse, all native ontology TMDL parts, seeder, schema-specific
   binder and deployment plan together. Preserve native `.platform`/lineage
   identities, executable parameter cells and target-local discovery.
2. Make fictional fixtures reproducible. Put deterministic business calculations
   in governed data/workflow logic, not model instructions. Notebook deployment
   must carry its reviewed payload; repository modules are not a Fabric runtime.
3. Add reviewed document source plus a target-local, insert-only publisher when
   needed, into a `knowledge_folders` folder. Publish only the permitted corpus;
   never index golden answers or raw bundles.
4. Reuse existing Search sources/base lifecycle and register bound physical
   DEV items through the same approved knowledge stage. Do not create duplicate
   resources from a product plugin.
5. Adapt the shared `instructions.md`, byte for byte in every variant, within
   8,000 characters, without braces or document content, and the tool
   descriptions in `agents/scenario-with-iq/tools/`. Keep each tool's server label,
   connection placeholder, read-only `allowed_tools` and `require_approval: never`.
   Change the reply format only together with `app/src/live/reply.ts`, its tests
   and the mock answers. Update the app's sample questions and the runner's
   `SCENARIO` readiness questions and facts with the seed data. To show what one IQ
   adds, add a `scenario-no-<iq>` variant as `agents/README.md` describes. Add
   scenario views in `app/src/` on the shell's sign-in, tool timeline and failure
   states; preserve source authority and per-user isolation, and keep the server
   limited to the configured project and agents.
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

Prove the prompt agents and web app route before saying the scenario shell is
ready. A healthy container, a published version, a green build or a
correct-sounding answer is insufficient. Authorization, denied-source and
safety-service errors remain blockers; never switch identities, expose tokens,
widen roles or recreate a partially deployed agent or app to get a plausible
answer.

Run `python3 .github/skills/deploy-platform/scripts/foundry_agent.py validate` and the
deploy-platform and app tests after changing agents or the app. Use product skills
from `docs/copilot-tooling.md` for opted-in capabilities, but retain repository
ownership/approval rules. Unsupported capabilities stop for
a design decision rather than becoming scenario-specific improvisation.

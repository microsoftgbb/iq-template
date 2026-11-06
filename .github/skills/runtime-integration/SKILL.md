---
name: runtime-integration
description: Preflight the shipped Foundry hosted agent and Container Apps web app route, prove its remaining readiness evidence, or diagnose a blocked agent or app in an explicitly chosen derived project. Use before promising an end-to-end IQ shell. Distinguish a deployed, healthy route from authenticated, data-backed, per-user readiness; never provision as template maintenance.
---

# Prove the runtime path first

Read `AGENTS.md`, `docs/runtime-integration.md`, `docs/development.md` and
`docs/ontology-knowledge.md`. This is a bounded integration procedure, not a
deployment wrapper. The `deploy-platform` runner's `agent` and `app` phases
publish the shipped hosted agent and web app; use this skill for the preflight,
the remaining shell evidence, access questions and diagnosis.

## 1. Choose the proof target

Require an explicitly chosen derived repository, reviewed revision, execution
host, Azure subscription/tenant and region. A disposable proof still creates
billable resources, Entra registrations and role assignments. Do not borrow
another project's resources, connections or state without approval.

Keep commands, configuration edits and state on that host. Use `deploy-platform`
for infrastructure, Fabric bootstrap and the agent and app releases, preserving
its consent, saved-plan, release-approval and state gates. A local single-user
proof needs explicit state-retention acceptance; team/CI use requires secured
remote state.

## 2. Run the read-only preflight

Run from the selected checkout, supplying the chosen values:

```sh
python3 .github/skills/runtime-integration/scripts/preflight_runtime.py \
  --repository "<owner>/<derived-repository>" \
  --subscription-id "<chosen-subscription-guid>" \
  --tenant-id "<chosen-tenant-guid>" \
  --location "<chosen-region>" \
  --fabric-location "<chosen-fabric-region>"
```

The helper only reads native metadata. It checks that the checkout's origin is
the chosen derived repository and neither the template nor a fork, Git >= 2.27.0
and Azure CLI >= 2.80.0, the selected Azure user, subscription and tenant,
provider credential overrides, that `--location` is a region with documented
hosted agents, and Fabric's authoritative regional CU quota for the exact SKU.
Supply `--fabric-capacity-sku` for a SKU other than F2; `--fabric-location`
defaults to `--location`. The helper does not sign in, write configuration, read
state, retrieve a secret or provision, and it never prints native
authentication output. Investigate a failed probe; zero or unknown quota is not
"unlimited".

Treat `status: "candidate"` as permission to continue, **not** readiness. Its
`unverified` list names what still needs live evidence.
`fabric_git_token_available` reports only environment-variable presence, never
permission or write access. A region missing from `HOSTED_AGENT_REGIONS` needs a
check of the current hosted-agent region list and a reviewed code change, not a
bypass. Search's new-service capacity and model quota still require separate
checks. Keep one common region by default; service-specific overrides require
approval and a fresh full plan, especially after a partial deployment.

## 3. Prove identity, retrieval and isolation

Start from the runner's evidence: its `agent` phase saves the fresh-challenge,
ontology and document responses in `.artifacts/deploy/evidence/`, and its `app`
phase checks the deployed app with the deployer's token. Together they prove
the route for one user. For the remaining gates in `docs/runtime-integration.md`:

- **Browser sign-in.** The user signs in at the `web_app` URL, selects **Check
  connection**, then asks the seed question and a follow-up. The runner's CLI
  token does not prove the web registration's sign-in or consent.
- **End-user authorization.** With an explicitly approved second account,
  confirm that a user without a Foundry agent role receives HTTP 403, and that a
  user with the role but without Fabric access gets an explicit
  unavailable-ontology answer, never a document substitute. Grant nothing beyond
  the access model without the user's approval.
- **Isolation.** The second user must not be able to continue or read the first
  user's conversation through `previous_response_id`.
- **Tool evidence.** Decide from the Responses output's tool calls: the
  `scenario_ontology` output must carry live facts, and the `knowledge_base`
  output the expected document. Never accept answer text or a `starter.txt`
  fact as live ontology evidence.

Keep the identity contract intact while proving it. The app forwards only the
signed-in user's Foundry token; Fabric IQ uses its `UserEntraToken` connection;
the knowledge base is called with the project's managed identity for documents
only. Never expose tokens in prompts, tool arguments, configuration, evidence
or logs. Never switch to a shared, application or managed identity, widen a
role, share a connection or add a backend to get a plausible answer.

## 4. Diagnose a blocked route

Follow the troubleshooting table in `docs/runtime-integration.md`. Separate the
layers before changing anything:

1. Call the agent directly as the user with a fresh challenge (the documented
   `az rest` check). If that succeeds but the app fails, look at the app, its
   settings or the sign-in registration.
2. Read the agent version's status and error, and the toolbox's default version,
   through the Foundry data plane; the hosted agent's traces are in Application
   Insights. Read the Container App's revision status and its console and system
   logs in Log Analytics.
3. Map the HTTP status: 401 is sign-in, 403 the agent role, 404 the endpoint,
   424 or 5xx a starting session or agent error, and 504 the proxy's timeout.

Fix the cause in source or with the documented grant, then let the runner
publish a new version or revision from the pushed commit. Never edit a live
agent, toolbox or Container App by hand, recreate one to clear a failure, or
change the registration's redirect URIs in the portal. If a native service
cannot preserve the required identity, report the blocked capability and
request an explicit design decision.

Require all evidence in `docs/runtime-integration.md` before saying **shell
ready**.

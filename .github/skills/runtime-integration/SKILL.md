---
name: runtime-integration
description: Preflight the shipped Foundry prompt agents and Container Apps web app route, prove its remaining readiness evidence, or diagnose a blocked agent or app in an explicitly chosen derived project. Use before promising an end-to-end IQ shell. Distinguish a deployed, healthy route from authenticated, data-backed, per-user readiness; never provision as template maintenance.
---

# Prove the runtime path first

Read `AGENTS.md`, `docs/runtime-integration.md`, `docs/development.md` and
`docs/ontology-knowledge.md`. This is a bounded integration procedure, not a
deployment wrapper. The `deploy-platform` runner's `agent` and `app` phases
publish the shipped prompt agents and build and deploy the web app; use this skill
for the preflight, the remaining shell evidence, access questions and diagnosis.

## 1. Choose the proof target

Require an explicitly chosen derived repository, reviewed revision, execution
host, Azure subscription/tenant and region. A disposable proof still creates
billable resources, Entra registrations and role assignments. Do not borrow
another project's resources, connections or state without approval.

Keep commands, configuration edits and state on that host. Use `deploy-platform`
for infrastructure, Fabric bootstrap, the agents and the app, preserving its
consent, saved-plan, action-approval and state gates. A local single-user proof
needs explicit state-retention acceptance; team/CI use requires secured remote
state.

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
the chosen derived repository and neither the template nor a fork; the tool
minimums Node.js >= 24.11.0, Git >= 2.27.0 and Azure CLI >= 2.60.0 (`tools`); the
selected Azure user, subscription and tenant; provider credential overrides; that
the tenant's Microsoft Foundry API principal publishes an enabled
`user_impersonation` scope (`foundry_api`); that Foundry accounts, Container Apps
environments and container registries are offered in `--location` (`regions`); and
Fabric's authoritative regional CU quota for the exact SKU (`fabric_quota`).
Supply `--fabric-capacity-sku` for a SKU other than F2; `--fabric-location`
defaults to `--location`. For an existing deployment's resize, pass its current SKU
as `--current-fabric-capacity-sku`: only the CUs the larger SKU adds need quota, and
the report names it as `fabric_quota.current_sku`. Before the user enables Work IQ,
add `--work-iq`: `work_iq.manual` then lists its prerequisites, which the helper
can't check (the tenant's Work IQ principal, administrator consent to
`WorkIQAgent.Ask` and `offline_access`, a Copilot Credits spending policy covering
the Work IQ API even for licensed users, a mailbox per user and each user's
consent). The helper does not sign in, write configuration, read state, retrieve a
secret, call Microsoft 365 or provision, and it never prints native authentication
output. Investigate a failed probe; zero or unknown quota is not "unlimited".

Treat `status: "candidate"` as permission to continue, **not** readiness. Its
`unverified` list names what still needs live evidence.
`fabric_git_token_available` reports only environment-variable presence, never
permission or write access. A region that doesn't offer a resource type needs
another explicitly chosen region, not a bypass. Search's new-service capacity and
model quota still require separate checks; the runner's own preflight checks model
quota. Keep one common region by default; service-specific overrides require
approval and a fresh full plan, especially after a partial deployment.

## 3. Prove identity, retrieval and isolation

Start from the runner's evidence: its `verify` phase saves the ontology, document
and public-context retrieval responses in `.artifacts/deploy/evidence/`, its
`agent` phase reads every agent back with its reviewed digest, and its `app` phase
checks the deployed app's health and sign-in settings. None of them asks the agents
a question. For the remaining gates in `docs/runtime-integration.md`:

- **Browser sign-in.** The user signs in at the `app` output's URL, asks the sample
  question with IQ, accepts the Fabric IQ consent card once, asks again and gets
  Pump B at North Works with a `[D1]` marker; then compares with **Ask both** and
  asks a follow-up. The runner's CLI token does not prove the app's registration,
  its consent or the redirect bridge.
- **Sources.** Decide from the tool timeline, not answer text: the Fabric IQ call
  must return live rows as the user, the Foundry IQ call a cited starter-document
  passage, the Web IQ query public terms only and, with Work IQ, the mail `fetch`
  the user's own mail. Never accept a `starter.txt` fact as live ontology evidence.
- **End-user authorization.** With an explicitly approved second account, confirm
  that a user without a Foundry role receives HTTP 403, and that a user with the
  role but without Fabric access gets an explicit failure or unavailable-source
  answer, never a document substitute. Grant nothing beyond the access model
  without the user's approval.
- **Isolation.** The second user must not be able to continue or read the first
  user's conversation through its response ID, or reach their mail. Until a second
  user has tried, report the shell as verified for one user, isolation not
  demonstrated.

Keep the identity contract intact while proving it. The app's server verifies and
forwards only the signed-in user's Foundry token; Fabric IQ uses its
`UserEntraToken` connection and Work IQ its OAuth identity passthrough; the
knowledge-base tools read documents and public context with the project's managed
identity. Never expose tokens in prompts, tool arguments, configuration, evidence
or logs. Never switch to a shared, application or managed identity, widen a role,
an allow-list or connection sharing, or add a backend to get a plausible answer.

## 4. Diagnose a blocked route

Follow the troubleshooting table in `docs/runtime-integration.md`. Separate the
layers before changing anything:

1. Compare the without-IQ agent, which calls no tool, with the with-IQ agent's turn
   and its tool timeline. A without-IQ answer next to with-IQ tool failures within
   seconds points at a source, such as a paused Fabric capacity.
2. Ask an agent directly as the user (the documented `az rest` call to the
   project's Responses API). If that succeeds but the app fails, look at the app,
   its settings or the sign-in registration and its redirect bridge.
3. Read the agent's latest version and its digest through the Foundry data plane,
   and the run's trace in Application Insights by the response ID the app shows.
   Read the container app's revision status and its console logs in Log Analytics.
4. Map the status: 401 is sign-in or token verification, 403 the user's Foundry
   role, 404 an unpublished agent or wrong project, 429 model quota, a failed
   `tools/list` an allow-list that names a tool the server doesn't offer, and a
   225-second timeout a slow tool.

Fix the cause in source, Terraform or with the documented grant, then let the
runner publish a new agent version or build and apply a new app revision. Never
edit a live agent or container app by hand, recreate one to clear a failure,
change the registration's redirect URIs in the portal or relax the server's token
checks. If a native service cannot preserve the required identity, report the
blocked capability and request an explicit design decision.

Require all evidence in `docs/runtime-integration.md` before saying **shell
ready**. Work IQ stays unverified without a tenant that meets its prerequisites.

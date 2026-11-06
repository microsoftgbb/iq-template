# Deployment flow

From a new derived repository to **verified DEV readiness** (steps 1-9 in the diagram), then the scenario agent and app (step 10). Read each row **left to right**, then continue down through the four phases. [Getting started](getting-started.md) is the canonical guide for prerequisites and commands.

[![Ten-stage deployment in four phases: choose and authorize; build and bootstrap DEV; connect and prove knowledge; then publish and prove the Foundry hosted agent and deploy and verify the Container Apps web app. Identity, platform and knowledge plans each require approval, separate from consent and the agent and app release approvals.](diagrams/deployment-flow.svg)](diagrams/deployment-flow.svg)

Select the image for the full-size SVG. For the resulting components, see the [platform architecture](architecture.md).

The diagram's Git-sync step is opt-in; the default uses native Fabric import with the same plan.

## Stages and gates

| Step | Stage | Required gate or result |
| --- | --- | --- |
| 1 | Create and configure | Use a **derived repository** and published bootstrap branch. Choose the target, a region with Foundry hosted agents, the execution host and a secured backend; configure one local tfvars file and complete every CLI sign-in up front. No PAT is needed unless `fabric_git_enabled = true`. |
| 2 | Prepare deployment identity | Review and approve the saved **Entra-only** plan. This initial bootstrap is the only plan allowed to target `module.fabric_user_auth`. Registration alone is not consent or Fabric readiness. |
| 3 | Consent and sign in | Consent covers exactly the declared delegated scopes. The runner grants it as you, assuming you are an administrator; another administrator grants it only if Entra refuses. The deployment-scope token is acquired silently where possible; verify the delegated scopes and actual Fabric roles. Terraform does not grant consent. |
| 4 | Provision the platform | Review and approve the saved **full, untargeted** plan. It creates capacity, empty DEV/TEST workspaces, deployment pipeline, Foundry/models, Search, Web IQ and the agent and app hosting (registry, monitoring, Container Apps environment, web identity and sign-in registration); the Git connection is opt-in. Search's system identity is enabled after creation and granted by the knowledge plan. Bootstrap and knowledge setup are still pending. |
| 5 | Import DEV and run Bootstrap | Import the reviewed definitions through native Fabric APIs with `Bootstrap` attached. Git sync is the opt-in alternative. Include all five starter items and wait for the operation and notebook actions. |
| 6 | Check bindings and discover IDs | Confirm files, tables, target-local binding and a data-backed Fabric query. Discover the physical DEV lakehouse and ontology GUIDs, not `.platform` logical IDs. Review generated bindings before promotion; commit through source control when Git is enabled. |
| 7 | Wire knowledge | Put the verified physical IDs into the **same** tfvars file. Review and approve the saved **full, untargeted** knowledge plan for sources, knowledge base and MCP connection. |
| 8 | Verify both retrieval paths | Wait for successful OneLake ingestion. Verify document retrieval separately from live ontology retrieval, including `fabricOntology` activity/references and the required end-user token. |
| 9 | Report DEV ready | Claim readiness only after the required checks pass. If a phase fails or times out, report the blocker and resume point instead. TEST promotion and PROD are separate workflows. |
| 10 | Create the agent and app | Build the agent image from the published commit, publish its toolbox and a hosted agent version, and prove a fresh challenge, live ontology facts and the starter document as you (`agent-deploy` approval). Then build the web image, consent to its Foundry sign-in scope, deploy the Container App and check its health, settings and a relayed challenge (`app-deploy` approval). Finally, sign in at the app URL yourself. |

The three highlighted approvals in the diagram are **Terraform plan approvals**, not a replacement for administrator consent, user sign-in, the agent and app release approvals or review of incoming Git changes. Every phase uses the same execution host, checkout, tfvars file and state/backend.

The [deterministic runner](development.md#deploy-deterministically) executes these stages as phases `signin` through `app` with the same gates: it completes every sign-in first, approves each saved plan by its hash and each release by name, names the exact grant when an API refuses for lack of permission, and resumes from live state when rerun. Both bootstrap paths deploy only additions from the reviewed, published `workspace/` revision, and the agent and app phases build only the published, committed source.

## What Bootstrap does

The [`Bootstrap` deployment plan](../workspace/Bootstrap.DeploymentPlan/plan.yml) orders four groups:

1. Deploy the `Scenario` lakehouse.
2. Deploy and execute `SeedScenario` to insert missing demo rows and create `Files/starter.txt` if absent.
3. Deploy `ScenarioOntology` with Site/Asset entities and their relationship.
4. Deploy and execute `BindScenario` to refresh scoped SQL metadata, bind to the target lakehouse, and expose the physical IDs needed for knowledge setup.

The plan itself is the fifth selected Git item. **A Git push does not run the plan**; attach it to each operation that needs its actions. Existing demo rows and files are preserved. The [Fabric lifecycle diagram](diagrams/fabric-lifecycle.svg) expands the four groups and promotion boundaries. For API request shapes, failure handling and target rebinding, use the [native bootstrap and promotion guide](../cicd/README.md).

## Where this flow stops

A successful Terraform apply, notebook run or source registration is not proof of query readiness. For the sample flow, verify both documents and live ontology data; an answer copied from `starter.txt` is not evidence of live ontology retrieval.

The guided flow leaves TEST empty. It does not create PROD, grant other users access to the agent and app, or verify a second user's isolation. Those are separate, explicit tasks. See [runtime integration](runtime-integration.md) and [promotion to TEST and PROD](../cicd/README.md#promoting-through-test-and-prod).

[Editable Excalidraw source](diagrams/deployment-flow.excalidraw) | [Authoring and icon sources](diagram-authoring.md)

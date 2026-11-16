# Deployment flow

From a new derived repository to **verified DEV readiness** (steps 1-9 in the diagram), then the prompt agents and app (step 10). Read each row **left to right**, then continue down through the four phases. [Getting started](getting-started.md) is the canonical guide for prerequisites and commands.

[![Ten stages from a derived repository through verified DEV readiness to publishing the Foundry prompt agents and building and deploying the Container Apps web app. Identity, platform, knowledge and app plans each require approval, separate from consent and the approvals to publish the agents and build the image.](diagrams/deployment-flow.svg)](diagrams/deployment-flow.svg)

Select the image for the full-size SVG. For the resulting components, see the [platform architecture](architecture.md).

The diagram's Git-sync step is opt-in; the default uses native Fabric import with the same plan.

## Stages and gates

| Step | Stage | Required gate or result |
| --- | --- | --- |
| 1 | Create and configure | Use a **derived repository** and published bootstrap branch. Choose the target, one region that offers Foundry with the configured models, Container Apps and Container Registry, the execution host and a secured backend; configure one local tfvars file and complete every CLI sign-in up front. No PAT is needed unless `fabric_git_enabled = true`. |
| 2 | Prepare deployment identity | Review and approve the saved **Entra-only** plan. This initial bootstrap is the only plan allowed to target `module.fabric_user_auth`. Registration alone is not consent or Fabric readiness. |
| 3 | Consent and sign in | Consent covers exactly the declared delegated scopes. The runner grants it as you, assuming you are an administrator; another administrator grants it only if Entra refuses. The deployment-scope token is acquired silently where possible; verify the delegated scopes and actual Fabric roles. Terraform does not grant consent. |
| 4 | Provision the platform | Review and approve the saved **full, untargeted** plan. It creates capacity, empty DEV/TEST workspaces, deployment pipeline, Foundry with its `agent`, `chat` and `embedding` models, Search, Web IQ, monitoring and the app's hosting (registry, Container Apps environment, app identity and sign-in registration); the Git connection and, with `work_iq_enabled`, the Work IQ registration and connection are opt-in. Search's system identity is enabled after creation and granted by the knowledge plan. Bootstrap and knowledge setup are still pending. |
| 5 | Import DEV and run Bootstrap | Import the reviewed definitions through native Fabric APIs with `Bootstrap` attached. Git sync is the opt-in alternative. Include all five starter items and wait for the operation and notebook actions. Replacing a changed notebook, ontology or plan needs `--update-definitions` and an `item-update` approval. |
| 6 | Check bindings and discover IDs | Confirm files, tables, the target-local SQL-endpoint binding with its `ONT_*` pins and a data-backed Fabric query. Discover the physical DEV lakehouse and ontology GUIDs, not `.platform` logical IDs. Review generated bindings before promotion; commit through source control when Git is enabled. |
| 7 | Wire knowledge | Put the verified physical IDs into the **same** tfvars file and export `WEB_IQ_API_KEY`. Review and approve the saved **full, untargeted** knowledge plan: one source per knowledge folder, the agents' knowledge base, the check-only ontology base, the Web IQ public-context base and the agents' connections. |
| 8 | Prove retrieval | Wait for every folder's ingestion. Verify document retrieval separately from live ontology retrieval, including `fabricOntology` activity/references and the required end-user token, then one public-only Web IQ search and one public-context retrieval. With Work IQ, the runner grants `WorkIQAgent.Ask` and checks the connection, without a Work IQ call. |
| 9 | Report DEV ready | Claim readiness only after the required checks pass. If a phase fails or times out, report the blocker and resume point instead. TEST promotion and PROD are separate workflows. |
| 10 | Publish the agents, deploy the app | Publish the changed prompt agent definitions (`agent-publish` approval); the runner reads each one back and compares its digest. Build the app's image from the committed `app/` files with ACR Tasks (`app-build` approval), then review and approve the saved app plan, which sets `app_image_tag` and creates or updates the container app. The runner checks `/healthz` and the served sign-in settings. Finally, sign in at the app URL and ask with IQ yourself. |

The diagram's four **saved-plan approvals**, for the identity, platform, knowledge and app plans, are not a replacement for administrator consent, user sign-in, the publish and build approvals or review of incoming Git changes. Every phase uses the same execution host, checkout, tfvars file and state/backend.

The [deterministic runner](development.md#deploy-deterministically) executes these stages as phases `signin` through `app` with the same gates: it completes every sign-in first, approves each saved plan by its hash and each action by name, names the exact grant when an API refuses for lack of permission, and resumes from live state when rerun. Both bootstrap paths deploy only additions from the reviewed, published `workspace/` revision, and the app phase builds only committed `app/` files.

## What Bootstrap does

The [`Bootstrap` deployment plan](../workspace/Bootstrap.DeploymentPlan/plan.yml) orders four groups:

1. Deploy the `Scenario` lakehouse.
2. Deploy and execute `SeedScenario` to insert missing demo rows and create `Files/documents/starter.txt` if absent.
3. Deploy `ScenarioOntology` with Site/Asset entities and their relationship.
4. Deploy and execute `BindScenario` to refresh scoped SQL metadata, bind to the target lakehouse through its SQL analytics endpoint, and expose the physical IDs needed for knowledge setup.

The plan itself is the fifth selected Git item. **A Git push does not run the plan**; attach it to each operation that needs its actions. Existing demo rows and files are preserved. The [Fabric lifecycle diagram](diagrams/fabric-lifecycle.svg) expands the four groups and promotion boundaries. For API request shapes, failure handling and target rebinding, use the [native bootstrap and promotion guide](../cicd/README.md).

## Where this flow stops

A successful Terraform apply, notebook run, source registration or agent publication is not proof of query readiness. For the sample flow, verify both documents and live ontology data; an answer copied from `starter.txt` is not evidence of live ontology retrieval.

The guided flow leaves TEST empty. It does not create PROD, grant other users access to the agents and app, or verify a second user's isolation. Those are separate, explicit tasks. See [runtime integration](runtime-integration.md) and [promotion to TEST and PROD](../cicd/README.md#promoting-through-test-and-prod).

[Editable Excalidraw source](diagrams/deployment-flow.excalidraw) | [Authoring and icon sources](diagram-authoring.md)

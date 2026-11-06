# Platform architecture

The intended **derived-project DEV baseline after bootstrap, knowledge wiring and the agent and app stage**, not a report of deployed resources. Copilot helps author and deploy all of it; the scenario workflow is the derived project's work.

[![Platform overview separating authoring, Fabric, Azure resources and the user experience. OneLake files feed Search; the Foundry hosted agent receives document evidence through Foundry IQ and live ontology facts through native Fabric IQ as the signed-in user. The Container Apps web app calls only the agent.](diagrams/platform-architecture.svg)](diagrams/platform-architecture.svg)

Select the image for the full-size SVG. The [deployment flow](deployment-flow.md) shows how these components become ready.

## Focused views

| View | What it makes explicit |
| --- | --- |
| [Agent runtime and identity](runtime-integration.md#runtime-at-a-glance) | Browser sign-in, the web app's proxy, the hosted agent and its toolbox, and which identity each IQ tool uses. |
| [Knowledge retrieval](ontology-knowledge.md#two-retrieval-paths) | Indexed OneLake files versus live ontology queries, model dependencies and the two authorization layers. |
| [Fabric bootstrap and promotion](../cicd/README.md) | Five portable Git items, seed/bind ordering and target-local data across DEV, TEST and separately provisioned PROD. |

## Components and ownership

| Component | What it contributes | Source of truth |
| --- | --- | --- |
| GitHub repository | Infrastructure configuration, five portable Fabric item definitions, the agent definition, the app and native release guidance. | [`infra/`](../infra/), [`workspace/`](../workspace/), [`agents/`](../agents/scenario-assistant/README.md), [`app/`](../app/), [`cicd/`](../cicd/README.md) |
| GitHub Copilot App / CLI | Guided configuration and deployment through repository skills, with a Codespaces/devcontainer toolchain for Terraform, Azure CLI, the web app and the IQ Copilot packages. | [Developer setup](development.md) |
| Microsoft Entra | Deployment-user app registration, delegated scopes and authorized clients, and the web app's single-page sign-in registration for the delegated Foundry scope. The deploying administrator grants consent for both (the runner does it as them; another administrator only if Entra refuses); the user's scoped sign-in is separate. | [`infra/identity.tf`](../infra/identity.tf), [`infra/app.tf`](../infra/app.tf), [identity bootstrap](getting-started.md#3-bootstrap-the-fabric-user-auth-application) |
| Microsoft Fabric | One capacity shared by DEV and TEST, workspace roles and native deployment pipeline. Native bootstrap needs no PAT; Git sync and its credentials connection are opt-in. | [`infra/fabric.tf`](../infra/fabric.tf) |
| Fabric DEV items | `Scenario` lakehouse, `SeedScenario`, `ScenarioOntology`, `BindScenario` and the `Bootstrap` deployment plan. Notebooks insert missing demo content and bind to the current workspace. | [`workspace/`](../workspace/), [bootstrap and promotion](../cicd/README.md) |
| Azure AI Search / Foundry IQ | Search service (serverless preview by default), indexed OneLake file source, optional live ontology sources and the knowledge base. Sources and knowledge base use Search data-plane resources. | [`infra/knowledge.tf`](../infra/knowledge.tf) |
| Microsoft Foundry | Account, project, chat and embedding deployments, Search AAD connection, knowledge-base MCP connection using the project managed identity, Fabric IQ `UserEntraToken` connections, and the container registry and Application Insights connections for hosted agents. | [`infra/foundry.tf`](../infra/foundry.tf), [`infra/agent.tf`](../infra/agent.tf) |
| Web IQ | Global G3 account, portal AppID linking and public-search verification. Agent integration is separate. | [`infra/web_iq.tf`](../infra/web_iq.tf), [runtime details](runtime-integration.md#web-iq) |
| Foundry hosted agent | The scenario assistant: a container image in the deployment's container registry, a project toolbox whose Fabric IQ tool runs as the signed-in user and whose knowledge-base tool retrieves documents, and the published agent version. Built and published by the runner, not Terraform. | [`agents/scenario-assistant/`](../agents/scenario-assistant/README.md), [step 7](getting-started.md#7-create-the-agent-and-app) |
| Azure Container Apps web app | The browser app and its proxy, which forwards each question with the signed-in user's own token to the agent's Responses endpoint. Terraform owns its environment, pull identity and sign-in registration; the runner deploys the app. | [`app/`](../app/), [`infra/app.tf`](../infra/app.tf), [runtime integration](runtime-integration.md) |

Terraform owns infrastructure, registrations and role assignments, not the Git-owned lakehouse or ontology definitions, the toolbox, the agent versions or the Container App. The Azure resource group contains the capacity, Foundry, Search, Web IQ, container registry, monitoring and Container Apps resources; Fabric workspaces/items and Entra objects have their own service boundaries.

One explicitly selected region is the default. Approved `fabric_location` and
`search_location` overrides allow a service-specific region without moving
existing Foundry resources; `service_locations` reports the effective choices.
Overrides require a cross-region data-handling and cost review, not an automatic
capacity workaround.

## Two knowledge paths

| Path | Data and behavior | Identity |
| --- | --- | --- |
| **Indexed documents** | The DEV lakehouse's **Files** area, including `starter.txt`, feeds an `indexedOneLake` source. Ingestion runs asynchronously every five minutes and uses the embedding model. It does not index the lakehouse's Tables area. | Search's managed identity has **Contributor on DEV only** and access to the Foundry models. |
| **Live ontology** | A registered `fabricOntology` source queries the bound Site/Asset ontology at request time. Its tables and relationships are not copied into the document index. | In addition to normal Search authentication, each request supplies an **end-user Search-audience token** through `x-ms-query-source-authorization`. |

The knowledge base uses the chat model for retrieval planning and answer synthesis. The Foundry project has a Search AAD connection and a separate knowledge-base MCP connection. Its managed identity authenticates to Search; it does **not** replace the end-user token required for live ontology queries.

Registered ontologies also get native Foundry Fabric IQ MCP connections using
`UserEntraToken` and the Power BI API audience. The hosted agent's Fabric IQ
tool uses them, so live facts are queried as the signed-in user; its
knowledge-base tool uses the managed-identity connection for documents only.
Search's native ontology sources retain their own user-header contract.

Knowledge sources, the knowledge base and its MCP connection remain disabled while `knowledge_lakehouse_id` is null. After DEV bootstrap, Terraform uses physical DEV item GUIDs to look up the Git-owned items. An empty `knowledge_ontologies` map selects document-only retrieval. See [ontology knowledge and runtime authentication](ontology-knowledge.md).

## Reading the boundaries

In this overview, arrows point toward the consuming component: Files feed Search, models support retrieval, and document evidence and live facts reach the hosted agent. The green live-facts path bypasses Search through the native Fabric IQ tool; the separate [knowledge retrieval view](ontology-knowledge.md#two-retrieval-paths) explains Search's optional live ontology source. The [runtime view](runtime-integration.md#runtime-at-a-glance) shows invocation direction instead.

The hosted agent and web app are published after DEV readiness, outside Terraform state. The agent reads live ontology facts with the signed-in user's identity and documents with the project's managed identity. The diagram's Fabric group is a service boundary, not an Azure resource group: the capacity is an Azure resource, while Fabric workspaces and items live in Fabric.

TEST is provisioned but stays empty until a reviewed native promotion. Imports, Git sync and deployment pipelines carry definitions, not OneLake data; every promoted target needs its own binding. The agent and app serve DEV only. PROD, production ingestion, the scenario workflow, other users' access and production hardening belong to the derived project. The baseline is billable, uses authenticated public endpoints and includes preview features; see [getting started](getting-started.md#keep-in-mind).

## Reference architectures

The template builds on Microsoft's [Fabric capacity quickstart](https://github.com/microsoft/fabric-terraform-quickstart/tree/main/quickstarts/102-fabric-capacity), [Foundry Terraform guidance](https://learn.microsoft.com/azure/foundry/how-to/create-resource-terraform?tabs=azapi), and [OneLake knowledge-source documentation](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-onelake).

[Editable Excalidraw source](diagrams/platform-architecture.excalidraw) | [Authoring and icon sources](diagram-authoring.md)

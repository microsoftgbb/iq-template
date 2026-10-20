# Platform architecture

The intended **derived-project DEV baseline after bootstrap, knowledge wiring and the agent and app stage**, not a report of deployed resources. Copilot helps author and deploy all of it; the scenario workflow is the derived project's work.

[![Platform overview separating authoring, Fabric, Azure resources and the Power Platform experience. OneLake files feed Search; the Studio agent receives document evidence through Foundry IQ and live ontology facts through native Fabric IQ. The Managed Runtime app calls only the agent.](diagrams/platform-architecture.svg)](diagrams/platform-architecture.svg)

Select the image for the full-size SVG. The [deployment flow](deployment-flow.md) shows how these components become ready.

## Focused views

| View | What it makes explicit |
| --- | --- |
| [Agent runtime and identity](runtime-integration.md#runtime-at-a-glance) | The generated app connector, published Studio agent and per-user IQ tools. |
| [Knowledge retrieval](ontology-knowledge.md#two-retrieval-paths) | Indexed OneLake files versus live ontology queries, model dependencies and the two authorization layers. |
| [Fabric bootstrap and promotion](../cicd/README.md) | Five portable Git items, seed/bind ordering and target-local data across DEV, TEST and separately provisioned PROD. |

## Components and ownership

| Component | What it contributes | Source of truth |
| --- | --- | --- |
| GitHub repository | Infrastructure configuration, five portable Fabric item definitions, the agent definition, the app and native release guidance. | [`infra/`](../infra/), [`workspace/`](../workspace/), [`agents/`](../agents/scenario-assistant/README.md), [`app/`](../app/), [`cicd/`](../cicd/README.md) |
| GitHub Copilot App / CLI | Guided configuration and deployment through repository skills, with a Codespaces/devcontainer toolchain that includes the Power Platform and Managed Runtime CLIs. | [Developer setup](development.md) |
| Microsoft Entra | Deployment-user app registration, delegated scopes and authorized clients. An administrator grants consent; the user signs in separately. | [`infra/identity.tf`](../infra/identity.tf), [identity bootstrap](getting-started.md#3-bootstrap-the-fabric-user-auth-application) |
| Microsoft Fabric | One capacity shared by DEV and TEST; workspace roles, GitHub credentials connection and native deployment pipeline. Git sync is a separate native operation. | [`infra/fabric.tf`](../infra/fabric.tf) |
| Fabric DEV items | `Scenario` lakehouse, `SeedScenario`, `ScenarioOntology`, `BindScenario` and the `Bootstrap` deployment plan. Notebooks insert missing demo content and bind to the current workspace. | [`workspace/`](../workspace/), [bootstrap and promotion](../cicd/README.md) |
| Azure AI Search / Foundry IQ | Search service (serverless preview by default), indexed OneLake file source, optional live ontology sources and the knowledge base. Sources and knowledge base use Search data-plane resources. | [`infra/knowledge.tf`](../infra/knowledge.tf) |
| Microsoft Foundry | Account, project, chat and embedding deployments, Search AAD connection, and knowledge-base MCP connection using the project managed identity. | [`infra/foundry.tf`](../infra/foundry.tf) |
| Web IQ | Global G3 account, portal AppID linking and public-search verification. Studio integration is separate. | [`infra/web_iq.tf`](../infra/web_iq.tf), [runtime details](runtime-integration.md#web-iq) |
| Copilot Studio agent | The published scenario assistant. Its Fabric IQ and Foundry IQ MCP tools run with each user's own connections (`Invoker` mode). Created with PAC, not Terraform. | [`agents/scenario-assistant/`](../agents/scenario-assistant/README.md), [step 7](getting-started.md#7-create-the-agent-and-app) |
| Managed Runtime app | Calls the agent through the generated Copilot Studio connector service as the signed-in user, in the agent's environment. | [`app/`](../app/), [runtime integration](runtime-integration.md) |

Terraform owns infrastructure, registrations and role assignments, not the Git-owned lakehouse or ontology definitions. The Azure resource group contains the capacity, Foundry, Search and Web IQ resources; Fabric workspaces/items and Entra objects have their own service boundaries.

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
`UserEntraToken` and the Fabric audience. They serve the Foundry-agent route,
which is blocked in Managed Runtime today; the Studio agent uses its own Fabric
IQ and Foundry IQ connector connections instead. Search's native ontology
sources retain their own user-header contract.

Knowledge sources, the knowledge base and its MCP connection remain disabled while `knowledge_lakehouse_id` is null. After DEV bootstrap, Terraform uses physical DEV item GUIDs to look up the Git-owned items. An empty `knowledge_ontologies` map selects document-only retrieval. See [ontology knowledge and runtime authentication](ontology-knowledge.md).

## Reading the boundaries

In this overview, arrows point toward the consuming component: Files feed Search, models support retrieval, and document evidence and live facts reach the Studio agent. The green live-facts path bypasses Search through the native Fabric IQ tool; the separate [knowledge retrieval view](ontology-knowledge.md#two-retrieval-paths) explains Search's optional live ontology source. The [runtime view](runtime-integration.md#runtime-at-a-glance) shows invocation direction instead.

The Studio agent and app are created after DEV readiness, outside Terraform, and reach the knowledge base and ontology with each user's credentials. The diagram's Fabric group is a service boundary, not an Azure resource group: the capacity is an Azure resource, while Fabric workspaces and items live in Fabric.

TEST is provisioned but stays empty until a reviewed native promotion. Git and deployment pipelines carry definitions, not OneLake data; every promoted target needs its own binding. PROD, production ingestion, the scenario workflow, hosted app deployment and production hardening belong to the derived project. The baseline is billable, uses authenticated public endpoints and includes preview features; see [getting started](getting-started.md#keep-in-mind).

## Reference architectures

The template builds on Microsoft's [Fabric capacity quickstart](https://github.com/microsoft/fabric-terraform-quickstart/tree/main/quickstarts/102-fabric-capacity), [Foundry Terraform guidance](https://learn.microsoft.com/azure/foundry/how-to/create-resource-terraform?tabs=azapi), and [OneLake knowledge-source documentation](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-onelake).

[Editable Excalidraw source](diagrams/platform-architecture.excalidraw) | [Authoring and icon sources](diagram-authoring.md)

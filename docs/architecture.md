# Platform architecture

The intended **derived-project DEV baseline after bootstrap and knowledge wiring**, not a report of deployed resources. Copilot helps author and deploy the platform; a consuming agent or application is separate work.

[![Fabric + Foundry IQ architecture: GitHub and Copilot feed a Terraform-managed platform; Fabric's OneLake files are indexed into Search, while its Site/Asset ontology is queried live. Foundry supplies models, a project and a managed-identity MCP connection. TEST is initially empty.](diagrams/platform-architecture.svg)](diagrams/platform-architecture.svg)

Select the image for the full-size SVG. The [deployment flow](deployment-flow.md) shows how these components become ready.

## Components and ownership

| Component | What it contributes | Source of truth |
| --- | --- | --- |
| GitHub repository | Infrastructure configuration, five portable Fabric item definitions and native release guidance. | [`infra/`](../infra/), [`workspace/`](../workspace/), [`cicd/`](../cicd/README.md) |
| GitHub Copilot App / CLI | Guided configuration and deployment through repository skills, with a Codespaces/devcontainer toolchain. Managed Runtime tooling supports separate app development; it does not deploy a consuming app here. | [Developer setup](development.md) |
| Microsoft Entra | Deployment-user app registration, delegated scopes and authorized clients. An administrator grants consent; the user signs in separately. | [`infra/identity.tf`](../infra/identity.tf), [identity bootstrap](getting-started.md#3-bootstrap-the-fabric-user-auth-application) |
| Microsoft Fabric | One capacity shared by DEV and TEST; workspace roles, GitHub credentials connection and native deployment pipeline. Git sync is a separate native operation. | [`infra/fabric.tf`](../infra/fabric.tf) |
| Fabric DEV items | `Scenario` lakehouse, `SeedScenario`, `ScenarioOntology`, `BindScenario` and the `Bootstrap` deployment plan. Notebooks insert missing demo content and bind to the current workspace. | [`workspace/`](../workspace/), [bootstrap and promotion](../cicd/README.md) |
| Azure AI Search / Foundry IQ | Search service, indexed OneLake file source, optional live ontology sources and the knowledge base. Sources and knowledge base use Search data-plane resources. | [`infra/knowledge.tf`](../infra/knowledge.tf) |
| Microsoft Foundry | Account, project, chat and embedding deployments, Search AAD connection, and knowledge-base MCP connection using the project managed identity. | [`infra/foundry.tf`](../infra/foundry.tf) |

Terraform owns infrastructure, registrations and role assignments, not the Git-owned lakehouse or ontology definitions. The Azure resource group contains the capacity, Foundry and Search resources; Fabric workspaces/items and Entra objects have their own service boundaries.

## Two knowledge paths

| Path | Data and behavior | Identity |
| --- | --- | --- |
| **Indexed documents** | The DEV lakehouse's **Files** area, including `starter.txt`, feeds an `indexedOneLake` source. Ingestion runs asynchronously every five minutes and uses the embedding model. It does not index the lakehouse's Tables area. | Search's managed identity has **Contributor on DEV only** and access to the Foundry models. |
| **Live ontology** | A registered `fabricOntology` source queries the bound Site/Asset ontology at request time. Its tables and relationships are not copied into the document index. | In addition to normal Search authentication, each request supplies an **end-user Search-audience token** through `x-ms-query-source-authorization`. |

The knowledge base uses the chat model for retrieval planning and answer synthesis. The Foundry project has a Search AAD connection and a separate knowledge-base MCP connection. Its managed identity authenticates to Search; it does **not** replace the end-user token required for live ontology queries.

Knowledge sources, the knowledge base and its MCP connection remain disabled while `knowledge_lakehouse_id` is null. After DEV bootstrap, Terraform uses physical DEV item GUIDs to look up the Git-owned items. An empty `knowledge_ontologies` map selects document-only retrieval. See [ontology knowledge and runtime authentication](ontology-knowledge.md).

## Reading the boundaries

Arrows show data, query and model dependencies; paired arrowheads denote request/response interactions, not data replication. The dashed consumer box is an extension point, **not a provisioned agent or app**.

TEST is provisioned but stays empty until a reviewed native promotion. Git and deployment pipelines carry definitions, not OneLake data; every promoted target needs its own binding. PROD, production ingestion, consuming applications and production hardening belong to the derived project. The baseline is billable, uses authenticated public endpoints and includes preview features; see [getting started](getting-started.md#keep-in-mind).

## Reference architectures

The template builds on Microsoft's [Fabric capacity quickstart](https://github.com/microsoft/fabric-terraform-quickstart/tree/main/quickstarts/102-fabric-capacity), [Foundry Terraform guidance](https://learn.microsoft.com/azure/foundry/how-to/create-resource-terraform?tabs=azapi), and [OneLake knowledge-source documentation](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-onelake).

[Editable Excalidraw source](diagrams/platform-architecture.excalidraw) | [Editing and icon attribution](diagrams/README.md)

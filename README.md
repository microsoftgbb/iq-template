# Fabric + Foundry IQ template

A GitHub repository template for starting independent Fabric and Foundry IQ projects. Create a new repository from it, choose your project name, and provision your own infrastructure with Terraform.

- A new Fabric capacity shared by DEV and TEST workspaces, a GitHub credentials connection, and a native DEV/TEST deployment pipeline.
- Git-versioned lakehouse, seeder notebook and deployment plan: initial Git sync can populate synthetic sites/assets and starter knowledge in DEV.
- A Microsoft Foundry account/project with chat and embedding models.
- After workspace bootstrap, a OneLake-backed Foundry IQ knowledge base, Azure AI Search ingestion, and a managed-identity MCP connection.

Start with [getting started](docs/getting-started.md). Repository conventions are in [AGENTS.md](AGENTS.md).

Setup is **Terraform platform -> Git sync with the Bootstrap plan -> Terraform knowledge wiring**, using one local tfvars file. Terraform does not initialize Git or run notebooks. A deployment plan must be explicitly attached to each sync/deployment; pushing a commit alone does not execute it.

`infra/` owns infrastructure; `workspace/` owns Fabric item definitions and seed code; [`cicd/`](cicd/README.md) explains native bootstrap, promotion, and adding ontologies, graphs and semantic models. There is no fabric-cicd dependency or custom deployment wrapper. Git copies definitions, not OneLake data; the notebook inserts missing sample rows and preserves existing content. Each derived project owns its configuration, state, tests, and automated workflows.

This is a **billable development baseline**, with authenticated public endpoints and preview deployment-plan/Foundry IQ features. It needs no separate Azure Storage account or private networking. Ontology/graph/semantic-model definitions, PROD infrastructure, production hardening, real data ingestion, and consuming agents belong in derived projects. The bundled seeder refuses non-DEV/TEST workspace names unless explicitly allowed.

**Template maintainer:** enable **Template repository** in GitHub **Settings > General** to expose the **Use this template** button.

Based on Microsoft's [Fabric capacity quickstart](https://github.com/microsoft/fabric-terraform-quickstart/tree/main/quickstarts/102-fabric-capacity), [Foundry Terraform guidance](https://learn.microsoft.com/azure/foundry/how-to/create-resource-terraform?tabs=azapi), and [OneLake knowledge-source documentation](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-onelake).

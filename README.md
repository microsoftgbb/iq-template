# Fabric + Foundry IQ template

A GitHub repository template for starting independent Fabric and Foundry IQ projects. Create a new repository from it, choose your project name, and provision your own infrastructure with Terraform.

- A new Fabric capacity shared by DEV and TEST workspaces; DEV connects to GitHub.
- A Microsoft Foundry account/project with chat and embedding models.
- A Blob-backed Foundry IQ knowledge base, Azure AI Search ingestion, and a managed-identity MCP connection.

Start with [getting started](docs/getting-started.md). Repository conventions are in [AGENTS.md](AGENTS.md).

`infra/` owns infrastructure; `workspace/` holds Fabric items; [`cicd/`](cicd/README.md) provides optional TEST/PROD item promotion using fabric-cicd. `infra/seed/starter.txt` provides initial knowledge content. Each derived project owns its configuration, state, tests, and automated workflows; none deploy automatically from this template.

This is a **billable development baseline**, with authenticated public endpoints and preview Foundry IQ features. PROD infrastructure, production hardening, data movement, OneLake ingestion, and a consuming agent belong in derived projects.

**Template maintainer:** enable **Template repository** in GitHub **Settings > General** to expose the **Use this template** button.

Based on Microsoft's [Fabric capacity quickstart](https://github.com/microsoft/fabric-terraform-quickstart/tree/main/quickstarts/102-fabric-capacity), [Foundry Terraform guidance](https://learn.microsoft.com/azure/foundry/how-to/create-resource-terraform?tabs=azapi), and [Blob knowledge-source documentation](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-blob).

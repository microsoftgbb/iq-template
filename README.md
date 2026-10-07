# Fabric + Foundry IQ template

A GitHub repository template for starting independent Fabric and Foundry IQ projects. Create a new repository from it, choose your project name, and provision your own infrastructure with Terraform.

- A prepared Entra app registration for delegated Fabric deployment, with API scopes and authorized clients; an administrator grants consent.
- A new Fabric capacity shared by DEV and TEST workspaces, a GitHub credentials connection, and a native DEV/TEST deployment pipeline.
- Git-versioned lakehouse, Site/Asset ontology, seed/binding notebooks and deployment plan: initial Git sync can populate synthetic data and bind the ontology in DEV.
- A Microsoft Foundry account/project with chat and embedding models.
- After workspace bootstrap, a Foundry IQ knowledge base with indexed OneLake documents and optional live Fabric ontology sources, plus a managed-identity MCP connection to Search.

Start with [getting started](docs/getting-started.md). Use the included **Codespaces/devcontainer** for Azure CLI, Terraform, Copilot CLI, GitHub CLI, Python, Node.js 24, and the **Copilot Managed Runtime plugin and CLI (`ms`)**, including Git Credential Manager and native build dependencies. [Developer setup](docs/development.md) covers Copilot App/CLI, [Managed Runtime app development](docs/development.md#build-and-deploy-copilot-managed-runtime-apps), and the repository skills for deployment, initialization, static maintenance and Fabric workspace work; [AGENTS.md](AGENTS.md) keeps the always-on guardrails concise.

Setup is **Terraform identity bootstrap -> admin consent and user sign-in -> Terraform platform -> Git sync with the Bootstrap plan -> Terraform knowledge wiring**, using one local tfvars file and one state. Only the initial Entra bootstrap targets `module.fabric_user_auth`; subsequent plans are untargeted. Terraform does not grant tenant consent, initialize Git or run notebooks. A deployment plan must be explicitly attached to each sync/deployment; pushing a commit alone does not execute it.

`infra/` owns infrastructure; `workspace/` owns Fabric definitions and bootstrap code; [`cicd/`](cicd/README.md) explains native promotion. [Seeded ontologies in Foundry IQ](docs/ontology-knowledge.md) covers the sample and registering additional ontologies. There is no fabric-cicd dependency or local deployment wrapper. Git copies definitions, not OneLake data; seeding preserves existing content. Each derived project owns its configuration, state, tests, and automated workflows.

In your new repository, tell Copilot **"Deploy the platform"**. The `deploy-platform` skill guides the complete DEV flow: configuration and prerequisite checks, an approved identity bootstrap and administrator consent handoff, approved platform applies, native Fabric Git sync with seed/binding actions, physical-ID discovery, knowledge wiring and retrieval verification. You still choose the target, sign in, arrange admin consent, supply the PAT securely and approve the plans. Copilot CLI runs inside Codespaces; Copilot App can drive that same Codespace through authenticated GitHub CLI SSH. See [the single-prompt setup](docs/development.md#deploy-from-one-prompt).

Use **/initialize-project** when you want configuration only. Workspace logical IDs stay stable; physical bindings are discovered by the notebooks in each target workspace. Neither container startup nor configuration-only initialization deploys anything.

This is a **billable development baseline**, with authenticated public endpoints and preview ontology/deployment-plan/Foundry IQ features. Ontology retrieval requires a delegated end-user token in addition to Search authentication; the project managed identity alone is insufficient. It needs no separate Azure Storage account or private networking. Additional domain models, graphs/semantic models, PROD infrastructure, production hardening, real ingestion and consuming agents belong in derived projects. The bundled seeder refuses non-DEV/TEST workspace names unless explicitly allowed.

**Template maintainer:** enable **Template repository** in GitHub **Settings > General** to expose the **Use this template** button.

Based on Microsoft's [Fabric capacity quickstart](https://github.com/microsoft/fabric-terraform-quickstart/tree/main/quickstarts/102-fabric-capacity), [Foundry Terraform guidance](https://learn.microsoft.com/azure/foundry/how-to/create-resource-terraform?tabs=azapi), and [OneLake knowledge-source documentation](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-onelake).

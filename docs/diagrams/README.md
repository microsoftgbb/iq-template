# Architecture diagrams

Five focused views keep the platform overview readable without hiding runtime,
identity or promotion boundaries. Select an SVG for the full-size diagram; each
guide embeds the image beside its text equivalent.

The self-contained SVGs stay sharp at any zoom. Editable Excalidraw sources live
beside each image.

## Previews

### Platform architecture

[![Platform overview showing authoring, Fabric, Azure resources with Web IQ, and the two Foundry prompt agents with their Container Apps web app, with separate document, public-context, live-fact and optional Work IQ mail routes.](platform-architecture.svg)](platform-architecture.svg)

### Deployment flow

[![Ten stages from a derived repository through verified DEV readiness to publishing the Foundry prompt agents and building and deploying the Container Apps web app, with explicit approvals.](deployment-flow.svg)](deployment-flow.svg)

### Agent runtime

[![The Container Apps web app's server verifies the signed-in user's token and forwards it to two Foundry prompt agents. Only the with-IQ agent has tools: Fabric IQ and the optional Work IQ mail tool run as that user; Foundry IQ documents and Web IQ public context use the project's managed identity. Application Insights holds the app's and the agents' traces.](agent-runtime.svg)](agent-runtime.svg)

### Knowledge retrieval

[![Indexed OneLake document folders feed the agents' knowledge base; the optional live ontology source sits in a check-only knowledge base that only the runner's verify queries, and Web IQ feeds its own public-context knowledge base, with distinct indexing and end-user authorization.](knowledge-retrieval.svg)](knowledge-retrieval.svg)

### Fabric lifecycle

[![Git bootstrap and native DEV/TEST promotion, with four ordered Bootstrap groups, target-local data and a separately owned PROD release.](fabric-lifecycle.svg)](fabric-lifecycle.svg)

[Authoring guide](../diagram-authoring.md) | [Icon licenses](LICENSE)

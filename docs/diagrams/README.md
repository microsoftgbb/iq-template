# Architecture diagrams

Five focused views keep the platform overview readable without hiding runtime,
identity or promotion boundaries. Select an SVG for the full-size diagram; each
guide embeds the image beside its text equivalent.

The self-contained SVGs stay sharp at any zoom. Editable Excalidraw sources live
beside each image.

## Previews

### Platform architecture

[![Platform overview showing authoring, Fabric, Azure resources and the Foundry hosted agent and Container Apps web app, with separate document and live-fact routes.](platform-architecture.svg)](platform-architecture.svg)

### Deployment flow

[![Ten stages from a derived repository through verified DEV readiness to the Foundry hosted agent and Container Apps web app, with explicit approvals.](deployment-flow.svg)](deployment-flow.svg)

### Agent runtime

[![The Container Apps web app forwards the signed-in user's token to the Foundry hosted agent; its Fabric IQ tool runs as that user and its knowledge-base tool uses the project's managed identity.](agent-runtime.svg)](agent-runtime.svg)

### Knowledge retrieval

[![Indexed OneLake documents and optional live ontology queries feed one knowledge base, with distinct indexing and end-user authorization.](knowledge-retrieval.svg)](knowledge-retrieval.svg)

### Fabric lifecycle

[![Git bootstrap and native DEV/TEST promotion, with four ordered Bootstrap groups, target-local data and a separately owned PROD release.](fabric-lifecycle.svg)](fabric-lifecycle.svg)

[Authoring guide](../diagram-authoring.md) | [Icon licenses](LICENSE)

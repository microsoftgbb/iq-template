# Official Copilot tooling

The devcontainer installs the official Microsoft capability packages below for
its `vscode` user. Repository skills remain the project workflow contract;
vendor skills supply product-specific expertise and tools, not permission to
deploy, change tenant policy, access work data or replace Terraform ownership.

| Official source | Installed Copilot package | Included capability |
| --- | --- | --- |
| [microsoft/azure-skills](https://github.com/microsoft/azure-skills) | `azure@azure-skills` | Azure skills, including `microsoft-foundry`, and Azure MCP's Azure/Foundry tool routing. |
| [microsoft/azure-skills](https://github.com/microsoft/azure-skills) | `foundry-iq-skills@azure-skills` | The `foundry-iq` skill. It uses the Azure/native knowledge interfaces; it declares no separate MCP server. |
| [microsoft/work-iq](https://github.com/microsoft/work-iq) | `workiq@work-iq` | Work IQ skill and the hosted Work IQ MCP server. |
| [microsoft/work-iq](https://github.com/microsoft/work-iq) | `microsoft-365-agents-toolkit@work-iq` | Declarative-agent development, UI widgets and evaluation skills. Toolkit installation is an explicit task, not container startup. |
| [microsoft/skills-for-fabric](https://github.com/microsoft/skills-for-fabric) | `fabric-skills@fabric-collection` | Fabric/Fabric IQ skills and agents, plus hosted Fabric IQ, semantic-model authoring and SQL-endpoint MCP declarations. |
| [microsoft/Managed-Apps](https://github.com/microsoft/Managed-Apps) | `microsoft-managed-apps@Managed-Apps` | Managed Runtime app skills/architect and the global `ms` CLI. This bundle does not declare an MCP server. |

## Installation and pins

Installation happens during image build, not login or post-create. The
[IQ plugin feature](../.devcontainer/features/iq-plugins/install.sh) owns the
Azure, Work IQ and Fabric repository commit pins and Azure MCP version. The
[Managed Runtime feature](../.devcontainer/features/managed-runtime/install.sh)
owns its CLI/plugin and Git Credential Manager pins. The repository's
[.NET tool manifest](../.config/dotnet-tools.json) pins the Power Platform CLI
(`dotnet pac`) that creates and publishes the Copilot Studio agent; the
post-create command restores it without signing in. Local marketplace
checkouts use explicit Git commit fetches: a Copilot marketplace `#ref` shortcut
does not reliably accept an immutable commit.

Azure MCP is installed at a reviewed version as `azmcp`. The image's small,
checked overlay points the Azure server and its telemetry launchers at that
binary instead of downloading `@latest` during a session. It fails if the
expected upstream declarations change. The upstream MCP server URLs, OAuth
configuration and skill payloads are otherwise retained; no tokens or tenant
IDs are added to them.

Rebuild to update a reviewed pin. Do not enable marketplace auto-update as a
template side effect. The post-create [tool check](../.devcontainer/check-tools.sh)
checks package registration and Python >= 3.12 without starting MCP servers,
invoking an app skill, signing in or accepting terms.

On a separate App/CLI host, install the same packages there; container plugins
do not configure the desktop host. The upstream manual marketplace commands are:

```text
/plugin marketplace add microsoft/azure-skills
/plugin install azure@azure-skills
/plugin install foundry-iq-skills@azure-skills
/plugin marketplace add microsoft/work-iq
/plugin install workiq@work-iq
/plugin install microsoft-365-agents-toolkit@work-iq
/plugin marketplace add microsoft/skills-for-fabric
/plugin install fabric-skills@fabric-collection
/plugin marketplace add microsoft/Managed-Apps
/plugin install microsoft-managed-apps@Managed-Apps
```

Those upstream commands follow the marketplace's current revision. Use the
feature's reviewed commit-fetch procedure when reproducing the pinned
toolchain outside a container. Do not duplicate MCP entries already owned by a
plugin or copy a personal MCP config into the repository.

## Authentication and project boundaries

- Azure/Fabric use the explicitly selected Azure user/tenant and the deployment
  registration's separate administrator-consent workflow. Product skills must
  reuse this project's Terraform resources and state, not create a parallel
  azd/Bicep platform.
- Work IQ MCP requires its own user authorization and tenant consent/licensing.
  Installing the plugin does not enable Work IQ in the app or grant access to
  Microsoft 365. Keep it opt-in; never query mail, meetings or documents during
  toolchain checks. Writes, sends and permission changes require explicit user
  approval. To give Work IQ something to find in a demo, the optional
  [`m365-demo-seed`](../.github/skills/m365-demo-seed/SKILL.md) skill (inspired by
  [DemoIQ](https://aka.ms/demoiq)) seeds fictional content into a non-production
  demo tenant. It is separate from the Work IQ plugin and needs its own explicit
  request and confirmations.
- Fabric MCP access is not evidence that an ontology is bound, a graph is
  materialized or a semantic model is refreshed. Preserve the native
  `workspace/` ownership and target-local binding/readiness gates. Do not install
  extra Fabric tooling merely to perform template maintenance.
- Managed Runtime still needs separate `ms` sign-in, eligible app hosting/Git
  access, permitted user connections and the [runtime proof](runtime-integration.md).
  Its shell-command attribution is handled by the official host plugin; do not
  overwrite a user's `MS_CLI_ORIGIN`.

The coding assistant's MCP servers are authoring tools. They are not automatically
Foundry agent tools or Managed Runtime data sources. Runtime wiring follows its
own reviewed connection, identity, schema and readiness contract.

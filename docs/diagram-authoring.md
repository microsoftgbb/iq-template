# Diagram authoring

Browse the previews in the [diagram gallery](diagrams/README.md). Each SVG and
its matching editable Excalidraw source live together in `docs/diagrams/`.
Required third-party notices and license texts are in
[`diagrams/LICENSE`](diagrams/LICENSE).

## Editing and exporting

1. Open the matching `.excalidraw` source in [Microsoft Excalidraw](https://aka.ms/excalidraw). The source embeds its icons and groups related labels; connectors are bound to their nodes.
2. Edit against the linked guide and save the source. Keep straight connectors, zero roughness, transparent group containers, black Helvetica labels and restrained product-color borders. Keep official icons at their original aspect ratio, orientation and colors.
3. Export the **entire scene** to SVG with a white background and light appearance. Keep the SVG beside its source, with the same filename stem. Do not substitute a PNG or external image URL.
4. Preserve the exported SVG's `title`, `desc`, `role="img"` and matching `aria-labelledby` IDs; retain `Helvetica, Arial, sans-serif` as the text fallback. Keep these descriptions consistent with the figure's meaning and the Markdown alt text. A fresh UI export may need these accessibility attributes reapplied.
5. Inspect the result at full size and at README width. Labels must fit their nodes, arrowheads must remain distinct, and the image must work without external assets. Update the source and SVG together, including any affected guide captions.

The checked-in scenes were exported with `@excalidraw/excalidraw` 0.18.1's
`exportToSvg`, then given accessible titles/descriptions and system-font
fallbacks. Diagram authoring does not add a dependency to the application.

The SVGs are native exports, not screenshots. Original vector icons are
embedded, text stays selectable, and no remote images, scripts or rendering
service are required. The white background keeps the figures readable in
both light and dark GitHub themes.

Check content against the canonical guides and Terraform, especially the
separation between deployment and runtime identities, the two retrieval paths,
initial TEST emptiness, and explicit plan attachment. Diagrams describe the
intended architecture, not observed deployment status.

## Official icon sources

Only the icons used by the diagrams are embedded; the full archives are not
redistributed. Each image element's `customData` records its original filename
and source, and its `fileId` identifies the embedded SVG bytes.

| Publisher / set | Icons used | Official source |
| --- | --- | --- |
| Microsoft Azure, public service icons V24 | Microsoft Foundry, Foundry IQ, Foundry Models, Foundry Project, Azure AI Search, App Registrations, Managed Identities, Resource Groups and the generic Browser glyph | [Azure architecture icons](https://learn.microsoft.com/azure/architecture/icons/), [V24 SVG archive](https://arch-center.azureedge.net/icons/Azure_Public_Service_Icons_V24.zip) |
| Microsoft Fabric, `@fabric-msft/svg-icons` 11.0.0 | Fabric, OneLake, Lakehouse, Notebook, Ontology and Group Workspace | [Fabric icons](https://learn.microsoft.com/fabric/fundamentals/icons), [official versioned package](https://www.npmjs.com/package/@fabric-msft/svg-icons/v/11.0.0) |
| Microsoft Power Platform SVG collection | Copilot Studio and Power Platform | [Power Platform icons](https://learn.microsoft.com/power-platform/guidance/icons), [official SVG archive](https://download.microsoft.com/download/498606aa-6d27-4f13-aa5c-1401078c153b/Power-Platform-icons-scalable.zip) |
| Microsoft Entra, October 2023 | Microsoft Entra ID color icon | [Entra architecture icons](https://learn.microsoft.com/entra/architecture/architecture-icons) |
| GitHub Octicons | `mark-github-24.svg`, `copilot-24.svg`, `workflow-24.svg` and `check-circle-24.svg` | [Pinned official sources](https://github.com/primer/octicons/tree/ab05d0e8bbf64ecd6b08375044b016ecdac9ef6d/icons) |
| HashiCorp Flight icons | `terraform-color-24.svg` | [Pinned official source](https://github.com/hashicorp/design-system/blob/c4a55d0e56ace0ab303ef65cfca9aa96c6d80b02/packages/flight-icons/svg/terraform-color-24.svg) |

The Foundry IQ icon identifies a knowledge base hosted by **Azure AI Search**.
The Fabric ontology icon identifies the bound ontology and its native Fabric IQ
tool. The GitHub Copilot icon identifies the authoring/deployment assistant,
not the app-facing Studio agent.

Managed Runtime is represented by Azure's official **generic Browser** glyph,
not by the Azure Managed Applications logo: these are different products.
The workflow and check glyphs are neutral process symbols, not additional
deployed services or a GitHub Actions pipeline.

# Diagram sources and icon notices

| Diagram | README-ready image | Editable source |
| --- | --- | --- |
| Platform architecture | [SVG](platform-architecture.svg) | [Excalidraw](platform-architecture.excalidraw) |
| Deployment flow | [SVG](deployment-flow.svg) | [Excalidraw](deployment-flow.excalidraw) |

The SVGs are self-contained: vector icons are embedded, fonts use a system fallback, and no remote images, scripts or rendering service are required. A white background keeps the diagrams readable in both light and dark GitHub themes. Each image includes a title and description; the companion [architecture](../architecture.md) and [deployment](../deployment-flow.md) pages provide a text equivalent.

## Editing

Open the `.excalidraw` source in [Microsoft Excalidraw](https://aka.ms/excalidraw). Keep the exported SVG and editable source together when making changes. Export with a white background, keep labels readable at README width, and preserve the SVG's accessible title/description and the icon notices below.

Use straight connectors, transparent group containers, black labels and restrained product-color borders. Keep official icons at their original aspect ratio, orientation and colors. The sources embed the icons for offline editing; there is no need to install a diagramming dependency in the repository.

Check content against the canonical guides and Terraform, especially the separation between deployment and runtime identities, the two retrieval paths, initial TEST emptiness, and explicit plan attachment. Diagrams describe the intended architecture, not observed deployment status.

## Official icon sources

These are product icons used to identify their respective products, not branding for this template. Only the icons used by the diagrams are embedded; the full icon archives are not redistributed.

| Publisher / set | Icons used | Official source and terms |
| --- | --- | --- |
| Microsoft Azure, public service icons V24 | Microsoft Foundry, Foundry IQ, Foundry Models, Foundry Project, Azure AI Search and App Registrations | [Azure architecture icons and terms](https://learn.microsoft.com/azure/architecture/icons/), [V24 SVG archive](https://arch-center.azureedge.net/icons/Azure_Public_Service_Icons_V24.zip) |
| Microsoft Fabric, v6.1.0 | Fabric, OneLake, Lakehouse and Notebook | [Fabric icons and terms](https://learn.microsoft.com/fabric/fundamentals/icons), [official icon archive](https://github.com/microsoft/fabric-samples/blob/main/docs-samples/Icons.zip) |
| Microsoft Entra, October 2023 | Microsoft Entra ID color icon | [Entra architecture icons, download and terms](https://learn.microsoft.com/entra/architecture/architecture-icons) |
| GitHub Octicons | `mark-github-24.svg` and `copilot-24.svg` | [Official Octicons sources](https://github.com/primer/octicons/tree/main/icons), [MIT license](https://github.com/primer/octicons/blob/main/LICENSE) |

Microsoft permits its architecture icons in architectural diagrams, training materials and documentation. All other rights are reserved by Microsoft. Do not crop, rotate, distort or repurpose the icons as your own product identity. Refer to each linked Microsoft page for its usage conditions; the repository's own licensing does not relicense these product icons.

The Foundry IQ icon identifies a knowledge base hosted by **Azure AI Search**. The GitHub Copilot icon identifies the authoring/deployment assistant, not a consuming agent provisioned by this template.

### GitHub Octicons license

```text
MIT License

Copyright (c) 2026 GitHub Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

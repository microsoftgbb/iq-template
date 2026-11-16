# Scenario assistant (Foundry prompt agents)

> [!IMPORTANT]
> **Fictional sample.** The sites, assets, documents and mail in this scenario are invented to
> demonstrate Microsoft IQ. They are not operational guidance.

One Foundry prompt agent definition in two variants: the same model, the same instructions and the
same question, with only the tools changing, so the app can compare an answer with IQ against one
without. Each tool is an MCP tool limited to its read-only `allowed_tools`, without approval steps.
The agents run with the signed-in user's token, which the app forwards: Fabric IQ and Work IQ mail
act as that user, and the agents read the Foundry IQ and Web IQ knowledge bases as the Foundry
project.

| Variant | Directory | Runner key | Agent name | Display name | Tools |
| --- | --- | --- | --- | --- | --- |
| With IQ | `scenario-with-iq/` | `with_iq` | `<project>-with-iq` | Scenario assistant – with IQ | Fabric IQ ontology, Foundry IQ knowledge, Web IQ public context and, when Work IQ is enabled, Work IQ mail |
| Without IQ | `scenario-without-iq/` | `without_iq` | `<project>-without-iq` | Scenario assistant – without IQ | None |

Terraform names each variant in its `foundry_agents` output and gives the names to the container
app as `AGENT_WITH_IQ` and `AGENT_WITHOUT_IQ`.

## Files

| File | Variants | Purpose |
| --- | --- | --- |
| `agent.json` | All, byte-identical | The agent version's `description` (`{{agent_display_name}}`) and `definition`: `kind: prompt`, the model deployment (`{{agent_model_deployment}}`) and the reasoning effort (`{{agent_reasoning_effort}}`). |
| `instructions.md` | All, byte-identical | The shared instructions, sent as the definition's `instructions`. |
| `tools/FabricIQOntology.json` | With IQ | `fabric_iq_ontology`: `ScenarioOntology`'s ontology endpoint, allowing only `ask_ontology`, as the user. |
| `tools/FoundryIQKnowledge.json` | With IQ | `foundry_iq_knowledge`: the agents' knowledge base of approved documents, such as `starter.txt`, allowing only `knowledge_base_retrieve`, as the project. |
| `tools/WebIQPublicContext.json` | With IQ | `web_iq_public_context`: the `<project>-public-context` knowledge base, whose only source is Web IQ, allowing only `knowledge_base_retrieve`, as the project. |
| `tools/WorkIQMail.json` | With IQ, when Work IQ is enabled | `work_iq_mail`: Work IQ's MCP server, allowing only `fetch`, on the `work-iq` connection, as the user. |

Each tool file holds exactly `type: mcp`, its `server_label`, `server_description`, `server_url`,
`project_connection_id`, `allowed_tools` and `require_approval: never`, and no headers: every tool
authenticates through its project connection, so no definition holds a credential. The runner fills
the placeholders from Terraform outputs and refuses any other placeholder:

| Placeholders | Terraform output |
| --- | --- |
| `agent_display_name` | `foundry_agents.<variant>.display_name` |
| `agent_model_deployment`, `agent_reasoning_effort` | `agent_settings` (`model_deployment`, `reasoning_effort`; a null effort drops the setting) |
| `fabric_iq_mcp_endpoint`, `fabric_iq_connection` | `fabric_iq_connections.scenario` (`server_url`, `name`) |
| `knowledge_base_mcp_endpoint`, `knowledge_base_connection` | `foundry_tool_connections.knowledge_base` |
| `public_context_mcp_endpoint`, `public_context_connection` | `foundry_tool_connections.public_context` |
| `work_iq_mail_mcp_endpoint`, `work_iq_mail_connection` | `foundry_tool_connections.work_iq_mail`, null when Work IQ is disabled |

## Tools and instructions

The tool descriptions, at most 1,024 characters each, carry what is specific to a tool, so the
shared instructions stay generic:

- **Fabric IQ ontology** asks one question about the rows of one entity, with values in quotes, such
  as which `Asset` rows have a given `Status`, with each asset's site. Its rows are the only current
  facts.
- **Foundry IQ knowledge** retrieves passages from the approved documents. The agent takes each
  document's title and section from the passage text, because a result can omit citation markers.
- **Web IQ public context** is for public, general context only, such as a manufacturer's guidance
  about a type of equipment. A query never holds a site, asset or person's name, and the agent uses at
  most one line, labelled "Public context, not company guidance".
- **Work IQ mail** makes one `fetch` of exactly
  `/me/messages?$search="<asset name>"&$select=subject,from,receivedDateTime,webLink,body&$top=10`.
  Work IQ's server also offers tools that act, so the tool allows only `fetch`.

The instructions open with which tools each kind of question calls, then the rules (cite only
sources received in the conversation, never fill a gap from memory, read-only), then the reply
format: a Markdown answer with a marker after each fact (`[D1]` ontology data, `[F1]` or `[F1 §2]` an
approved document, `[Web1]` public context, `[W1]` a mail message), a *Sources* line per marker and
a numbered *Steps* list of the tools called. The app shows the markers as source chips. Because the
without-IQ variant shares the instructions, they hold no document content or sample facts: those
reach an agent only through its tools. They stay within 8,000 characters and contain no braces.

## Work IQ is optional

`WorkIQMail` is the only optional tool (`OPTIONAL_TOOLS` in `foundry_agent.py`). With Terraform's
`work_iq_enabled = false`, the default, there is no Work IQ registration, connection or tool: the
runner renders `scenario-with-iq` without `WorkIQMail`, and that tool's placeholders need no values.
Enabling Work IQ changes the rendered definition, so the runner publishes a new version. It needs a
Microsoft 365 mailbox per user, a Copilot Credits spending policy that covers the Work IQ API, which
bills custom agents' Work IQ calls even for users with a Microsoft 365 Copilot licence, administrator
consent to `WorkIQAgent.Ask` and `offline_access`, and each user's one-time consent
([Work IQ](../docs/getting-started.md#work-iq)). After switching `work_iq_enabled`, rerun the
deployment through the agent phase. Without a tenant that meets these prerequisites, Work IQ stays
unverified.

## Add a variant

To show what one IQ adds, add a `scenario-no-<iq>` variant, such as `scenario-no-fabric`:

1. Create `agents/scenario-no-<iq>/` with byte-identical copies of `agent.json` and
   `instructions.md`, and `tools/` with byte-identical copies of the with-IQ tools except that IQ's.
   The instructions need no change: an agent skips the tools it doesn't have.
2. Add the variant's key and directory to `VARIANTS` in
   [`foundry_agent.py`](../.github/skills/deploy-platform/scripts/foundry_agent.py).
3. Name its agent in Terraform's `foundry_agents` output and the container app's settings, and add
   it to the app's variants.

## Deploy and check

The deployment runner's `agent` phase publishes the agents after DEV readiness, outside Terraform
state ([deployment skill](../.github/skills/deploy-platform/SKILL.md)):

1. `foundry_agent.py` renders each variant with Terraform's outputs, leaving out `WorkIQMail` unless
   Work IQ is enabled, and records the rendered body's sha256 in the agent's metadata.
2. The runner compares that digest with each agent's latest version and, after one `agent-publish`
   approval, creates a missing agent or publishes a new version of a changed one. An unchanged digest
   publishes nothing.
3. It reads every agent back and reports it ready only when its latest version carries the reviewed
   digest. A published agent isn't a ready app: each user still consents to the tools that act as
   them ([runtime integration](../docs/runtime-integration.md)).

Check and preview the definitions offline, without Azure:

```sh
python3 .github/skills/deploy-platform/scripts/foundry_agent.py validate
python3 .github/skills/deploy-platform/scripts/foundry_agent.py render \
  --source agents/scenario-with-iq --variant with_iq --values <placeholder-values.json> \
  --omit WorkIQMail
```

`validate` loads every variant, including the optional tool, and checks that the shared files are
byte-identical. Leave out `--omit WorkIQMail` to render the definition with Work IQ. When you change
a definition, keep every tool read-only with its exact allow-list and no approval step, then run
`python3 -B -m unittest discover -s .github/skills/deploy-platform/tests`. Never add a tool, widen an
allow-list or add a credential to pass a check.

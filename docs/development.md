# Developer setup and Copilot

## Codespaces or a local dev container

In your **derived repository**, use **Code > Codespaces > Create codespace** on the intended branch. For a local checkout with Docker and VS Code's Dev Containers extension, run **Dev Containers: Reopen in Container**.

The [devcontainer](../.devcontainer/devcontainer.json) includes Azure CLI, Terraform, the standalone GitHub Copilot CLI (`copilot`, not the retired `gh copilot` extension), Git/GitHub CLI, Node.js/npm, Python 3 with venv support, Bash, jq, ripgrep and an SSH server for authenticated Codespaces access. It also configures the Terraform, Azure CLI, Python, YAML and Copilot editor extensions. The base image, Terraform and Copilot are pinned; Node uses the 24 release line. Rebuild the container after changing tool versions.

Tools are installed during image build; Copilot comes from its official GitHub release with mandatory SHA-256 verification, without an npm-registry dependency. The post-create hook only checks tool availability/versions. It does **not** sign in, generate tfvars, initialize Terraform, provision resources, synchronize Fabric, or run notebooks. Python supports the local initializer and static source checks; Node supports Node-based developer/MCP tools. Spark, Delta and NotebookUtils still run in Fabric, not locally. No Fabric CLI, fabric-cicd or azd is needed.

Authenticate separately when needed:

```sh
# Only if GitHub CLI is not already authenticated:
gh auth login

# Start from the repository root; use /login in Copilot if prompted:
copilot

# After the Fabric provider app/consent prerequisite (device code works in Codespaces):
az login --use-device-code --tenant "<tenant-id>" \
  --scope "<approved-Fabric-application-ID-URI>/.default"
az account set --subscription "<subscription-name-or-id>"
```

Copilot needs an active subscription and organization permission. A Codespaces-provided GitHub token may be sufficient for repository access but not Copilot requests; follow [Copilot authentication](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli) rather than reusing the Fabric Git PAT. Never put tokens in devcontainer files or paste them into chat.

The Copilot App uses its selected execution host; a devcontainer file does not automatically move App sessions into that container. The deployment skill can instead run commands in your chosen Codespace through `gh codespace ssh`. The App host needs GitHub CLI with access to that Codespace; all Azure tools, credentials, tfvars and state stay in the remote deployment checkout. For an explicitly local App deployment, install the equivalent tools on that host and reuse its original deployment checkout/backend, not a fresh worktree.

## Deploy from one prompt

After these changes are included in the template's published branch:

1. Create a new repository with **Use this template**, and open its Codespace.
2. Complete the one-time prerequisites below. Start `copilot` in the Codespace terminal, or open the derived repository in Copilot App with GitHub CLI access to the Codespace.
3. Say **"Deploy the platform"**. In App, include **"Use my Codespace `<name>`"**; otherwise Copilot asks which one. Interactive mode is useful for initial sign-in and approvals.

Copilot loads [`deploy-platform`](../.github/skills/deploy-platform/SKILL.md) and coordinates:

```text
choose host/state + preflight -> configure -> review/apply platform plan
  -> native DEV Git sync + Bootstrap -> verify seed/binding + discover physical IDs
  -> review/apply knowledge plan -> verify ingestion and live ontology retrieval
```

This is **one guided request**, not a zero-input or guaranteed unattended deployment. You still need:

- **Target and consent:** choose subscription/tenant, region, project name and capacity admin; approve the actual Terraform plans and reviewed Git content. Quota, preview availability and RBAC propagation can still block a run.
- **Azure/Fabric user sign-in:** complete the [Fabric provider's user-context app/consent setup](https://registry.terraform.io/providers/microsoft/fabric/1.14.0/docs/guides/auth_app_reg_user) and administrator-owned tenant prerequisites from [getting started](getting-started.md). Copilot can guide device login, but cannot perform your MFA, grant its own consent or bypass policy. Plain `az login` alone is not proof that Fabric write/execute scopes are available.
- **A secure Fabric Git PAT:** add a [Codespaces secret](https://docs.github.com/en/codespaces/managing-your-codespaces/managing-your-account-specific-secrets-for-github-codespaces) named `FABRIC_GITHUB_TOKEN`, restricted to the derived repository, with Contents read/write. Restart an already-running Codespace after adding it. The skill maps it to Terraform's ephemeral `TF_VAR_github_token` inside each plan/apply shell; it never copies it into files or chat. An existing `TF_VAR_github_token` is also supported. Copilot's login and the default Codespaces `GITHUB_TOKEN` are not this PAT.
- **State retention:** prefer an existing encrypted, access-controlled remote backend and project-specific state key. A single-user local-state demo needs explicit acceptance and secure retention/backup before deleting the Codespace. Deleting a Codespace does not delete its Azure resources. Team/CI deployment requires remote state.

The App route uses GitHub's [authenticated Codespaces SSH transport](https://docs.github.com/en/codespaces/developing-in-a-codespace/using-github-codespaces-with-github-cli#ssh-into-a-codespace), not a claimed native App-to-Codespaces attachment. Do not publish port 2222, configure an SSH password or disable host-key verification. If the App host cannot access Codespaces, use CLI in the Codespace rather than silently deploying from the laptop.

On a failed/paused run, say **"Resume deployment in the same Codespace and backend."** Copilot checks existing state and operation receipts instead of resetting knowledge inputs or submitting duplicate jobs. It reports **DEV ready** only after both document and live ontology retrieval succeed; incomplete authentication or ingestion is reported as blocked/unverified. TEST stays empty and PROD/promotion/consuming agents remain separate. Review generated DEV binding changes before committing them and before any later promotion.

## Use the repository skills

Open the derived repository in Copilot App, or start `copilot` at its root. [`AGENTS.md`](../AGENTS.md) supplies the small, always-on contract. The task-specific instructions live in [`.github/skills/`](../.github/skills), a [supported skills location for both App and CLI](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills); no global installation or copied App instructions are needed.

| Ask Copilot | What it does |
| --- | --- |
| "Deploy the platform." | Uses `deploy-platform` for the full guided DEV deployment, including bootstrap, knowledge wiring and readiness checks. |
| "Use /initialize-project to configure this new repository." | Confirms project/region/admin choices, resolves the intended repository and published branch, and creates one ignored local tfvars file. No deployment. |
| "Use /template-maintenance to check my changes." | Runs scoped static checks without touching deployment state or executing cloud operations. |
| "Use /fabric-workspace to review these bindings." | Checks logical identities, model references and target-local binding rules; live bootstrap/promotion remains an explicit operation. |

In CLI, use `/skills list` to see available skills and `/skills reload` after adding/updating them. In App, use **Customize > Skills**. Skills provide instructions, not permission to deploy or bypass tool approvals.

Keep new learning in one place: stable cross-task guardrails in `AGENTS.md`, a repeatable workflow in its existing skill, and product explanations in the canonical docs. Do not add session diaries, deployment IDs, credentials or copies of entire guides to always-loaded instructions.

## What gets templated

The shipped workspace has **no physical workspace/lakehouse/ontology IDs, SQL hostnames or saved notebook lakehouse attachments**. Its literal GUIDs are logical/lineage identities, and its generic names are a portable sample model.

| Value | How it gets the correct value |
| --- | --- |
| Project branding, region and Fabric admins | Explicit choices in local `infra/terraform.tfvars`; the initializer validates syntax without choosing cloud defaults. |
| GitHub owner/repository and bootstrap branch | The initialization skill resolves the new repository's published branch, not the template's owner or the current agent branch. |
| `.platform` logical IDs, plan references, TMDL lineage tags and binding markers | Preserve them. Regenerating all UUIDs would break item identity and binder checks. |
| `Scenario` / `ScenarioOntology`, `dbo` and demo table names | Portable workspace-local names; no project-name substitution is required. Coordinate any deliberate rename using the workspace skill. |
| Physical workspace/item GUIDs, OneLake URLs and SQL endpoint binding | The notebooks discover these in the **current Fabric workspace** when the explicitly attached Bootstrap plan runs. |
| Terraform knowledge item IDs | After successful DEV bootstrap, put the actual physical GUIDs into the same local tfvars file. Never use `.platform` IDs. |

For a non-Copilot setup, run the [initializer](../.github/skills/initialize-project/scripts/initialize_project.py) directly with `--help`, or follow the manual configuration in [getting started](getting-started.md). The helper uses Python's standard library and Git, offers `--dry-run`, creates the local file with owner-only permissions, and refuses existing tfvars/state. It never rewrites workspace definitions or changes Git remotes.

During guided deployment, the [knowledge configuration helper](../.github/skills/deploy-platform/scripts/configure_knowledge.py) fills the two second-stage settings using verified physical DEV IDs. It preserves unrelated inputs and rejects conflicting/custom registrations rather than replacing them. Neither helper authenticates or deploys resources; the skill coordinates ordinary native commands with explicit approvals.

Once a derived DEV workspace is bound, its reviewed Git export legitimately contains environment-specific source details. Keep those out of the pristine template, and rebind every promoted target; see [binding ownership and review](ontology-knowledge.md#review-the-generated-binding). Do not use blanket GUID replacement to reset a deployed repository.

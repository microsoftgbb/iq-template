# Contributing

<!-- Template-owned: a derived repository should replace this file with its own contribution guide. -->

This guide covers contributions to the **template itself**, through pull requests from forks.
To build a project from it, select **Use this template** and follow
[getting started](docs/getting-started.md) in your new repository. Don't fork it for a project:
forks of this public repository are public, and the deployment runner refuses to deploy from them.

This project welcomes contributions and suggestions. Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](CODE_OF_CONDUCT.md).
Report security issues as described in [SECURITY.md](SECURITY.md), never in a public issue.

## Before you start

- Open an issue before a large change, so the approach can be agreed first.
- Read [AGENTS.md](AGENTS.md). Its guardrails apply to people and Copilot alike. The
  [repository skills](.github/skills/) encode each workflow; extend them instead of
  adding parallel scripts or wrappers.
- Open a Codespace on your fork, or use the dev container, for the pinned toolchain; see
  [developer setup](docs/development.md#codespaces-or-a-local-dev-container).

## Ground rules

- **No sensitive data.** Never commit or paste credentials, tokens, tfvars, `.env` files,
  Terraform state, saved plans, logs or tenant, subscription or physical Fabric IDs.
- **Static checks only.** Never run a live `plan`, `apply`, `destroy`, deployment or notebook
  as a test of a template change. Live work belongs in a derived repository with an explicitly
  chosen target.
- **Portable identities.** Keep `.platform` logical IDs, TMDL lineage tags and binding markers
  stable; they are not deployment IDs.
- **Generated code.** Never edit `generated/`; regenerate it with `ms`.
- **Pins.** Do not upgrade providers, tools or dependencies in an unrelated change.
- **Docs in step.** Keep the README, [getting started](docs/getting-started.md) and
  `infra/terraform.tfvars.example` aligned with any input or behavior change.

## Contribute through a fork

1. Fork [microsoftgbb/iq-template](https://github.com/microsoftgbb/iq-template) and clone your
   fork, or open a Codespace on it. Keep your fork's `main` current with **Sync fork** on GitHub
   or `gh repo sync <your-account>/iq-template`, then pull it.
2. Create a branch from `main` and keep the change focused.
3. Run the checks below, then push the branch to your fork.
4. Open a pull request against `main` in `microsoftgbb/iq-template`, fill in the template and
   leave **Allow edits by maintainers** on.
5. CI starts once a maintainer approves workflows for first-time contributors. Fix any failures.
   A static check doesn't prove cloud permissions, preview availability, quota or ingestion, so
   say what you couldn't verify.
6. A [code owner](.github/CODEOWNERS) reviews changes to agent guidance, skills, CI, the dev
   container, infrastructure, Fabric items, the Studio agent and dependencies. Maintainers
   squash-merge approved pull requests.

## Run the checks

[CI](.github/workflows/ci.yml) runs these on every pull request. Run them locally first:

```sh
# Terraform, from infra/
terraform init -backend=false -input=false -lockfile=readonly
terraform fmt -check -recursive
terraform validate

# Python helpers, from the repository root
python3 -B -m unittest discover -s .github/skills/deploy-platform/tests
python3 -B -m unittest discover -s .github/skills/runtime-integration/tests

# App, from the repository root
npm ci && npm run lint && npm run build && npm test
```

If `infra/` holds local deployment files, validate an isolated copy as described in the
[template-maintenance skill](.github/skills/template-maintenance/SKILL.md#run-static-checks-only),
which also lists the checks for notebooks, the initializer and the dev container. In Copilot,
ask it to statically validate the template.

### npm lockfile

`package-lock.json` must resolve every package from `https://registry.npmjs.org/`; CI rejects
other hosts. If your npm configuration uses an Azure Artifacts mirror, normalize the lockfile
after changing dependencies. Integrity hashes are unchanged, and npm still routes these URLs
through your configured registry.

```sh
node -e 'const fs=require("fs"),f="package-lock.json";fs.writeFileSync(f,fs.readFileSync(f,"utf8").replace(/("resolved": ")https:\/\/[^"]+?\/_packaging\/[^"\/]+\/npm\/registry\//g,"$1https://registry.npmjs.org/"))'
```

## Test a change live

Static checks can't prove deployment behavior. To try a change end to end, deploy it from a
separate repository with your own subscription and tenant:

1. Create a repository with **Use this template**; it can be private.
2. Apply your branch's changes to it, for example:

   ```sh
   # In your fork, with main synced:
   git diff --binary main...my-change > ../my-change.patch
   # In a clone of the new repository:
   git apply ../my-change.patch
   ```

3. Commit, push and deploy it by following [getting started](docs/getting-started.md).

Share only redacted evidence in your pull request. Never bring deployment output back into the
template, such as tfvars, state, `ms.config.json`, agent or app settings, or bound workspace
definitions.

## Review pull requests from forks

For maintainers:

- Approve workflow runs only after reading the diff. CI runs a fork's code with a read-only
  token and no secrets; keep it that way, and never add `pull_request_target`.
- Don't run a fork's code, build its dev container or open it in Codespaces or Copilot where your
  cloud, GitHub or Codespaces credentials are available. Rely on CI or an isolated environment
  without credentials.
- Treat changes to `AGENTS.md`, `.github/`, `.devcontainer/` and dependency manifests as code that
  runs with deployers' credentials or steers their agents.

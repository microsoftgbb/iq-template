# Contributing

<!-- Template-owned: a derived repository should replace this file with its own contribution guide. -->

This guide covers contributions to the **template itself**. To build a project from it,
select **Use this template** and follow [getting started](docs/getting-started.md) in your
new repository instead.

This project follows the [Microsoft Open Source Code of Conduct](CODE_OF_CONDUCT.md).
Report security issues as described in [SECURITY.md](SECURITY.md), never in a public issue.

## Before you start

- Open an issue before a large change, so the approach can be agreed first.
- Read [AGENTS.md](AGENTS.md). Its guardrails apply to people and Copilot alike. The
  [repository skills](.github/skills/) encode each workflow; extend them instead of
  adding parallel scripts or wrappers.
- Use the Codespace or dev container for the pinned toolchain; see
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

## Pull requests

1. Branch from `main`, keep the change focused and fill in the pull request template.
2. Make sure CI passes. A static check does not prove cloud permissions, preview availability,
   quota or ingestion; say what you could not verify.
3. A [code owner](.github/CODEOWNERS) must review changes to agent guidance, skills, CI, the
   dev container, infrastructure, Fabric items, the Studio agent and dependencies.

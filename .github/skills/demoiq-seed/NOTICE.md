# Notice - DemoIQ for GitHub Copilot

## Attribution

This skill adapts **DemoIQ** and its **DemoIQ-Scout** skill, release `2026-10-02.1`, created
by Microsoft to seed realistic, fictional content into Microsoft 365 demo tenants. All
credit for the DemoIQ engine, the Config Builder, the content model, the seeding pipeline
and the original playbooks belongs to the DemoIQ authors. To learn more about DemoIQ, visit
**[aka.ms/demoiq](https://aka.ms/demoiq)**.

This adaptation targets **GitHub Copilot App and GitHub Copilot CLI only**, on Windows,
macOS and Linux, on top of this repository's deployment. It is not an official DemoIQ
release. Report problems with the adaptation in this repository; for DemoIQ itself, start at
[aka.ms/demoiq](https://aka.ms/demoiq).

The DemoIQ-Scout package carried no license file. Keep this skill in repositories
restricted to your organization unless the DemoIQ owners confirm it may be shared more
widely.

## Unchanged from DemoIQ-Scout

- `engine/DemoIQ.html` is byte-identical. It remains the source of truth for seeder
  generation; `scripts/generate.js` runs its `generatePs1()` in a Node sandbox.
- The content model, prompts, industries, scenarios, personas and languages, the seeding
  steps, the tenant security-settings preparation and restore (restore, never strip) and
  the AoP runbook.
- The flows, gates and operating rules, adapted only where the harness, the platform or the
  identity model differs.
- The workspace `~/.copilot/DemoIQ-Output-Scout` and its layout, so existing runs and history
  keep working.

## What changed

| Area | Change |
| --- | --- |
| Harness | GitHub Copilot tools and conventions replace Microsoft Scout's: the `ask_user` gate, terminal async mode for the administrator's sign-in, the repository-relative path `.github/skills/demoiq-seed`, one-line commands that run in bash, zsh and PowerShell, and frontmatter that follows the GitHub Copilot skill schema. The Scout-specific system-walkthrough asset was dropped. |
| Identity | The deploy-platform runner's optional `demoiq` phase registers a **delegated-only, public-client** DemoIQ app through Terraform (`infra/demoiq.tf`, `demoiq_enabled`), relays administrator consent and checks meeting audio. No application permissions, certificate or secret exist. Upstream's certificate app registration, permission-grant and Speech provisioning scripts and the Flow 2 playbook were removed, as was local app deletion. |
| Administrator sign-in | An administrator signs in with a device code for each seed or cleanup run (`scripts/admin_signin.ps1`), with their own MFA. The refresh token is kept for one run only (`scripts/session_store.ps1`); the one-time code goes only to an owner-only file. This replaces the stored administrator password and the app-only certificate tokens. |
| Target tenant | Always the deploying user's: the tenant, app and Speech resource come from the runner's receipt (`deploy.py status --json` -> `demoiq`). The skill confirms it as a non-production demo tenant (Gate 0), and the sign-in refuses another tenant, app or account. |
| Seeder patch | `scripts/platform_patch.js` (marker `demoiq-seed: delegated patch v2`) applies anchored patches to each generated seeder: plain configs; the run's administrator session with early token renewal; a Microsoft Graph REST shim instead of the Graph PowerShell modules; demo users through the public client (no client assertion); consent checked, never granted; the administrator never excluded from Conditional Access; each per-user MFA change recorded with the original state, so a resumed run (or a person) can restore it; a cleanup mode; and removal of the usage-telemetry block. A post-check refuses any certificate, client-credentials, Graph-module or config-password construct. `run_seeder.ps1` refuses unmarked seeders and `generate.js` exits 10 when a required anchor no longer matches the engine. |
| Configs | Plain, owner-only JSON in the config library `<ws>/configs`; there is no config password. The Config Builder saves straight into the library through the local server. `scripts/convert_config.ps1` converts an encrypted upstream config once, in the user's own terminal; `scripts/repoint_config.ps1` points a config at this deployment's app. |
| Story | `scripts/draft_config.ps1` drafts a config from the project's scenario or the template's sample story (`scripts/presets/sample-asset-operations.json`) for review in the Builder. Seeded content never flows back into Foundry IQ knowledge. |
| Tenant cleanup | A seeder run in cleanup mode prepares the tenant, deletes what the run manifests list with delegated tokens (each owner's own items, the team and chats as the administrator) and restores the tenant (`scripts/cleanup_engine.ps1`). `scripts/cleanup_tenant.ps1` is a read-only planner. |
| Readiness | `scripts/status.ps1` reports honest states - seeded is never ready - and `scripts/workiq_check.js` records an optional Work IQ check as a demo user. Upstream's live post-run Graph verification and publishing to the administrator's OneDrive needed app-only access and were removed. |
| Speech | Meeting audio uses the platform's Foundry account with the Azure CLI's Entra token; no separate Speech resource is created. |
| Cross-platform layer | `scripts/platform.ps1` (OS detection, owner-only files, browser opening, detached processes, process listing, per-OS install hints, deployment receipts), `scripts/session_store.ps1` and `scripts/detach.js`. Every script was ported for macOS and Linux. |
| Telemetry | Removed entirely: no telemetry scripts, events, salts, opt-out files or engagement IDs (ROSS, Opportunity, TPID), and the seeder's telemetry block is stripped. The Config Builder's hidden legacy engagement fields are never asked for or prefilled. |
| Node helpers | Blocking stdout so JSON isn't truncated on POSIX pipes; `--out-file` for large `generate.js` output. |
| Config Builder | No administrator password, certificate thumbprint, encryption, decryption or download: **Save config** posts plain JSON to the local server; the App ID is read-only (from the receipt); a drafted config opens prefilled. Also edited: the version-stamp title, the per-OS FFmpeg install hint, the repository `stream_rtmp.ps1` path and the comment on the hidden legacy fields. Line endings are preserved. |
| Tests | New suites for the platform layer, seeder patch, seeder bootstrap (seed and cleanup), administrator sign-in, Builder save, credential preflight, cleanup engine, status and the Work IQ check; the other suites were adapted. `run_tests.ps1` isolates `HOME`, `TMPDIR` and the session folder. |

## Local state and its protection

- **Configs** hold the demo users' passwords in plain text, in owner-only files (0600, folder
  0700 on macOS and Linux; the user's profile on Windows). Anyone who can act as your
  operating-system account can read them: use dedicated demo accounts whose passwords
  protect nothing else, and remove the library with local cleanup depth 2.
- **The administrator's sign-in** (a refresh token for the DemoIQ app) is protected with
  DPAPI on Windows, or an AES-256-GCM envelope in an owner-only file with its key in a
  separate owner-only file under `<ws>/.session/` on macOS and Linux. It lasts at most 12
  hours and the next run consumes it; `admin_signin.ps1 -Clear` or local cleanup removes it.
  Like DPAPI, this protects against other users and accidental disclosure, not against code
  running as you.
- **No certificate, application secret, administrator password or config password** exists
  anywhere in this adaptation.

## Validation

The offline suite (`tests/run_tests.ps1`) exercises the platform layer, the session store,
the seeder patch and a stubbed seed and cleanup run on macOS and Linux. Not yet validated
for this adaptation: a real-tenant run (consent, the delegated preparation, restore and
cleanup, the public-client demo-user sign-in, meeting audio on Foundry and the Work IQ
check) and a complete Windows run. Report results through this repository.

## Updating from a new DemoIQ release

1. Replace `engine/DemoIQ.html` with the new release's file, unchanged (it stays CRLF;
   `.gitattributes` keeps it byte-identical).
2. Replace `DemoIQ-Config-Builder.html` and re-apply the Config Builder changes above
   without changing its line endings; `tests/test_builder_save.ps1` and section 4 of
   `tests/test_engagement_fields.js` must pass.
3. Port upstream script and playbook changes, keeping this adaptation's identity model,
   platform helpers, tenant pinning and the absence of telemetry.
4. Run `pwsh -NoProfile -File .github/skills/demoiq-seed/tests/run_tests.ps1`.
   `test_platform_patch.js` fails when a required patch anchor drifted or a forbidden
   construct reappeared; update `scripts/platform_patch.js` until it passes.
5. Set the new release in `VERSION`, the SKILL.md version line and the Config Builder's
   version meta tag and stamp; `scripts/version.ps1 -Check` must report no problems.

---
name: demoiq-seed
description: Optional - seed fictional Microsoft 365 demo content (mail, Teams chats and channels, meetings, Word/PowerPoint/Excel files and meeting audio) into the deploying user's non-production demo tenant so Work IQ and Microsoft 365 Copilot have realistic work data to demo. Powered by DemoIQ (aka.ms/demoiq) and adapted for GitHub Copilot App and CLI on Windows, macOS and Linux. Use only when the user explicitly asks to seed demo content or a demo tenant, run DemoIQ or /demoiq-seed, use a DemoIQ_config.json, re-seed a previous run, check DemoIQ status, show past DemoIQ runs, check Work IQ against seeded content, stream demo meeting audio into a Teams meeting or clean up seeded demo content. Attended sessions only. Never use for production tenants, general Microsoft 365 administration, platform deployment or template-maintenance checks.
---

# DemoIQ for GitHub Copilot

> **Powered by DemoIQ.** DemoIQ generates and seeds realistic, fictional Microsoft 365
> work content - emails, Teams chats and channels, meetings with transcripts and audio,
> Word, PowerPoint and Excel files - plus an "Art of the Possible" (AoP) presenter runbook,
> so a demo tenant shows Work IQ and Microsoft 365 Copilot at their best. To learn more
> about DemoIQ, visit **[aka.ms/demoiq](https://aka.ms/demoiq)**.
>
> This folder adapts DemoIQ for **GitHub Copilot App and GitHub Copilot CLI only**, on
> Windows, macOS and Linux, on top of this repository's deployment. It is not the official
> DemoIQ distribution. The engine (`engine/DemoIQ.html`) ships unmodified;
> [NOTICE.md](NOTICE.md) gives the attribution and lists every change. This adaptation
> sends no telemetry.

- **Version: 2026-10-02.1** - the DemoIQ release this adaptation is based on. It lives in
  `VERSION`, is mirrored here and in the Config Builder stamp, is recorded in every run's
  `run.json`, and `scripts/version.ps1` and `scripts/status.ps1` print it.
  State this version when the user asks which version they are running.

A plain, owner-only **config** in the workspace's config library is the single input to a
run. It starts from the project's own story (`scripts/draft_config.ps1`) or the visual
**Config Builder** (`DemoIQ-Config-Builder.html`), and the user types the demo users'
passwords only in the Builder. You regenerate the matching `DemoIQ.ps1` seeder from it with
`scripts/generate.js` (the engine's own `generatePs1()` in a Node sandbox, then this
adaptation's delegated patch), author the demo content yourself, have the administrator sign
in for the run, and launch the seeder detached.

## Where it fits in this repository

DemoIQ is an **optional** step that gives Work IQ (and the Work IQ MCP server in Copilot)
realistic work data in the tenant the platform was deployed to. It is not part of the
platform, and seeding is not readiness:

- It runs only on an explicit request: directly (`/demoiq-seed`, "seed my demo tenant") or
  when the user answers yes to the offer at the end of
  [`deploy-platform`](../deploy-platform/SKILL.md).
- **The deploy-platform runner owns its identity.** `deploy.py up --with-demoiq` (add
  `--through demoiq` when the agent and app are not part of the run) registers a
  delegated-only, public-client DemoIQ app through Terraform (`infra/demoiq.tf`, behind
  `demoiq_enabled`): no application permissions, no certificate, no secret. An
  administrator grants consent through the runner, and it checks meeting audio on the
  platform's Foundry account. The result is the `demoiq` receipt in
  `deploy.py status --json`; this skill only reads it. Never register, change or delete the
  app from here.
- **The deploying user's tenant only.** The tenant, app and Speech resource come from that
  receipt, so DemoIQ runs on the same host and checkout as the deployment.
- It never runs inside `deploy.py`, Terraform, Fabric bootstrap, CI or a
  template-maintenance check. It does not connect Work IQ to the Studio agent or the app;
  that remains the explicit [Work IQ opt-in](../../../docs/scenario-authoring.md#opt-in-capabilities).
- **One-way.** The project's scenario may shape the story DemoIQ seeds; nothing seeded (and
  no AoP runbook) is ever fed back into Foundry IQ knowledge, `scenario/knowledge/`, a
  lakehouse or an index.
- What it creates lives outside Terraform state: the seeded content in Microsoft 365 and
  the local workspace on this machine.

## Hard rules

1. **Explicit request, attended session.** Never start DemoIQ on your own initiative, from a
   quoted example or without a person present (see [Gates](#gates-and-attended-sessions)).
2. **One tenant: the deploying user's, confirmed non-production.** Take it from the
   receipt and pass Gate 0 before anything signs in to or changes a tenant. Never seed or
   clean up another tenant; the administrator's sign-in refuses one (`wrong-tenant`).
3. **Disclose tenant changes before they happen.** Gate 0, the content preview and the
   cleanup confirmation restate them.
4. **Secrets never touch chat or the terminal.** There is no administrator password: the
   administrator signs in in their own browser with a one-time code that `admin_signin.ps1`
   writes only to an owner-only file - never read, print or relay that code. Demo-user
   passwords are typed only into the Config Builder. Configs are plain files: never open
   one with `view`, `cat` or a search tool, never echo a `u<N>pwd` field, and use the
   summaries from `find_config.ps1`, `preflight_credentials.ps1` and `status.ps1` instead.
5. **Nothing in the repository.** Configs, runs, logs and deliverables live in `<ws>` on
   this machine. Never write them into the checkout, commit them or move a config between
   machines through Git.
6. **Never touch the platform from this skill.** No edits to `infra/`, Terraform state,
   Fabric items, Foundry, Search, the Studio agent or the app. DemoIQ's own infrastructure
   changes only through the deploy-platform runner and its approvals.
7. **The user signs in and configures in their own browser.** Never drive the Config
   Builder, a sign-in page or Teams with browser automation; share links instead.
8. **One DemoIQ run at a time.** Seed and cleanup runs relax and restore the same tenant
   settings; `launch_seeder.ps1` refuses a second run.
9. **Never self-modify this skill mid-run.** If something here looks wrong, tell the user
   and work around it within the run; fixes ship through a pull request.

## Paths and commands

- **Skill folder:** `.github/skills/demoiq-seed`, relative to the repository root (the
  normal working directory). If your working directory differs, use the absolute path of
  the folder containing this file. The scripts resolve their own files from `$PSScriptRoot`.
- **Workspace `<ws>`:** `~/.copilot/DemoIQ-Output-Scout` - the single home for everything
  DemoIQ writes, outside the repository (the name is kept from upstream DemoIQ-Scout).
  Inside it: `configs/` (the config library, owner-only), `runs/<runId>/` (one folder per
  seed or cleanup run, with `run.json`, `DemoIQ.ps1`, `DemoIQ-Output/` and `logs/`),
  `scratch/` (your working files), `serve/` (local server state), `archive/` and `.session/`
  (the session key and a pending sign-in code). Scripts print absolute paths; use them.
- **Scratch `<scratch>`:** the `scratchDir` that `scripts/find_config.ps1` returns. Write
  files you author (prefs, story text, the `--pasted` content map) there with your
  file-creation tool, as UTF-8.
- **Command form:** one line that runs unchanged in bash, zsh and PowerShell, with forward
  slashes and double-quoted paths:
  - `pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/<name>.ps1 <args>`
    (`-ExecutionPolicy Bypass` is mandatory: the scripts are unsigned)
  - `node .github/skills/demoiq-seed/scripts/<name>.js <args>`
  - Use `--out-file <path>` for `generate.js` JSON dumps instead of shell redirection.
- **Outputs:** helpers print one compact JSON line; parse it instead of grepping output.

## Gates and attended sessions

DemoIQ needs a person present: it asks blocking questions, and the administrator signs in
for every run. Ask every gate with the **`ask_user`** tool. Put the context and your
recommendation in a short message before the call, put only the question and its canonical
choices in the call, then act on the answer the tool returns. Never answer a gate yourself,
never proceed on an assumed or default answer, and never re-type the choices as a list.
Each gate uses the exact question and choices from its section or playbook, every run.

The gates are: **(0)** target tenant (below), **(1)** config choice (Flow 1a), **(2)**
content-preview approval (Flow 1d) - its "Looks good - seed it now" answer **is** the seed
consent, with no second confirmation - and **(3)** the cleanup confirmation (Flow 6).

If `ask_user` is unavailable or nobody is present (non-interactive `copilot -p` prompts,
scheduled automations, CI, or any mode that blocks questions), do not start. At the first
point that needs a gate or a sign-in, stop with one message: *"DemoIQ is attended-only: it
needs you to confirm the tenant, choose a config, approve the content preview and sign in
for the run. Run `/demoiq-seed` in an interactive GitHub Copilot App or CLI session."*

## Step 0 - status and the target tenant

Run this at the start of every DemoIQ session, before the menu.

1. **Tools:** PowerShell 7.4+ and Node.js 18+ (the devcontainer has both; the runner checks
   them when DemoIQ is enabled). If `pwsh` is missing: Windows
   `winget install --id Microsoft.PowerShell -e`, macOS `brew install --cask powershell`,
   Linux see
   [Install PowerShell on Linux](https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-linux).
   No Microsoft Graph PowerShell modules are needed.
2. **Status:**
   `pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/status.ps1`
   It reads the receipt, the config library, the stored sign-in (account only) and the
   newest runs, and returns `state`, `summary`, `attention[]` and `next[]`. Relay
   `summary` and every `attention[]` line as written. Then:
   - `not-set-up`: DemoIQ has no app in this deployment. With the user's yes, load
     [`deploy-platform`](../deploy-platform/SKILL.md) and run its section 7 (the runner's
     `demoiq` phase, with its own plan approval and consent). Come back here when
     `status.ps1` no longer says `not-set-up`. If `deploymentError` says the runner could
     not be read, you are probably not on the deployment's host and checkout: say so and
     stop.
   - `tenant-prepared`: an earlier run left MFA and Conditional Access changes in place.
     Resolve it first (Flow 6 "Tenant left prepared"), before any new run.
   - `running`: a run is in progress; offer to monitor it (Flow 1h step 3 or Flow 6).
3. **Gate 0 - confirm a non-production demo tenant.** Required before any flow that signs
   in to or changes the tenant: Build, Re-seed and tenant cleanup. Show past runs, status,
   How DemoIQ works, the Work IQ check, meeting audio and streaming, and local cleanup do
   not need it. Ask once per session. In the message before the call, name the tenant
   (`deployment.tenantId` from `status.ps1`, from this checkout's deployment receipts) and
   state what DemoIQ does to it:
   - uses the delegated-only **DemoIQ** app this deployment registered; an administrator
     signs in to it for each seed or cleanup run, and it can act only within its consented
     scopes and that person's own roles;
   - for each seed or cleanup run, temporarily turns off Security Defaults (when on) and
     per-user MFA for the demo users and excludes the demo users from enabled Conditional
     Access policies (never the administrator), then restores exactly those changes at the
     end of the run;
   - signs in as the five demo users with the passwords in the config (an owner-only file
     on this machine) to create, and later delete, their mail, chats, meetings and files.

   Then call `ask_user` with the question *"Is this a non-production demo tenant that DemoIQ
   may change as described?"* and the choices `Yes - it is a non-production demo tenant` /
   `No - stop`. On anything but the first choice, stop and return to the caller.

## Where to run DemoIQ

Run DemoIQ where the deployment ran: the receipt, and so the tenant and app, live in that
checkout's `.artifacts/deploy/state.json`. Keep using that host for the run's life.

- **Your own computer with GitHub Copilot App or CLI:** the Config Builder opens in your
  default browser and the sign-in code file opens in your editor or terminal.
- **A Codespace (Copilot CLI in its terminal, or Copilot App driving it):** the Config
  Builder is reached through VS Code's automatic private port forwarding or
  `gh codespace ports forward <port>:<port>`; never make a port public. The user reads the
  sign-in code from the code file in their own Codespace terminal
  (`gh codespace ssh -c "<codespace>" -- cat "<codeFile>"` from their machine works too).
  The Codespace must keep running until the run ends - stopping it stops the seeder - so
  raise its idle timeout (30 minutes by default) before a long run.

## The menu

Unless the request already names an action, present the menu with `ask_user` after Step 0
(direct requests - build, re-seed or "run it again", cleanup, audio or stream, history or
"past runs", status, "check Work IQ" - skip it and go straight to that flow, whose gates
still apply). After every flow, completed, cancelled or failed, rerun `status.ps1`, say the
new `summary` in one line and show the menu again until the user chooses Done.

**Readiness line (before the menu).** Say the `status.ps1` summary. Never call seeded
content "ready": a seed run proves the content was written, not that Work IQ answers from
it. Then add these short notes, each on its own line:

> *Content: industry and scenario (by default a story drafted from this project's scenario,
> or the template's North Works / South Works sample), content types and counts (emails,
> chats, channels, meetings, Word/PowerPoint/Excel), language, the five demo users and AoP
> surfaces - you review all of it in the Config Builder.*
>
> *Model: this skill authors all the demo content itself, so quality tracks your session's
> model. Pick the most capable model available in Copilot's model picker (`/model` in the
> CLI) before a build.*
>
> *Timing: a focused run (a few content types at default counts) takes about 20-40 minutes
> (10-15 authoring, 7-25 seeding); all content types can take about 3.5 hours. Teams channel
> propagation dominates seeding. Re-seed skips authoring (7-25 minutes).*

Then call `ask_user` with *"What would you like to do with DemoIQ?"* and these choices, in
this order (omit Re-seed unless Flow 1R's eligibility check finds a re-seedable run, and
omit the Work IQ check unless `status.ps1` reports a seed run):

- `🚀 Build a demo tenant seed (Recommended)` - Flow 1
- `🔁 Re-seed the last run` - Flow 1R
- `🔎 Check Work IQ against the seeded content` - Flow 9
- `📜 Show past runs` - Flow 7
- `🎙️ Generate / stream meeting audio` - Flow 3
- `🧹 Clean up seeded content` - Flow 6
- `✅ Done for now`

Also available on request (not menu choices): **Check DemoIQ status** (Flow 8), **How DemoIQ
works** (Flow 4, below) and **Clean up local footprint** (Flow 10).

## Flow playbooks

Each flow's steps live in `references/`. **Before running a flow, read its playbook fully
with the `view` tool, then follow it exactly.** Never run a flow from memory. Everything in
this file keeps applying while a playbook runs.

| Flow | When | Playbook (under `.github/skills/demoiq-seed/`) |
| --- | --- | --- |
| 1 - Build a demo tenant seed | Menu; the primary action | `references/flow-1-build.md` (the spine), then each stage in full as you enter it: `flow-1a-configure.md` (story, config, pre-seed checks) -> `flow-1d-author.md` (author, preview, regenerate) -> `flow-1h-seed.md` (sign in, launch, monitor, wrap-up) |
| 1R - Re-seed the last run | Menu, only when eligible; "run it again" | `references/flow-1r-reseed.md` |
| 3 - Generate / stream meeting audio | Menu | `references/flow-3-audio.md` |
| 6 - Clean up seeded content (tenant) | Menu; "Tenant left prepared" | `references/flow-6-cleanup.md` |
| 7 - Show past runs | Menu | `references/flow-7-history.md` |
| 8 - Check DemoIQ status | On request ("am I set up?", "where are we?") | `references/flow-8-status.md` |
| 9 - Check Work IQ against the seeded content | Menu after a seed run; offered at the end of Flow 1h | `references/flow-9-workiq-check.md` |
| 10 - Clean up local footprint | On request | `references/flow-10-local-cleanup.md` |

Always use the deterministic helpers instead of hand-written equivalents: `status.ps1`
(where things stand), `find_config.ps1` (config library and deployment identity),
`draft_config.ps1` (a story into a config), `repoint_config.ps1` (a config onto this
deployment's app), `admin_signin.ps1` (the administrator's per-run sign-in), `new_run.ps1`
(per-run folder and `run.json`), `launch_seeder.ps1` (detached seed or cleanup run; returns
`launchedAt` and a ready-made `monitorCommand`), `monitor_seeding.ps1` (bounded-wait
progress) and `workiq_check.js` (the Work IQ check).

## The administrator's sign-in

Every seed and cleanup run starts with a fresh administrator sign-in to the DemoIQ app:

`pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/admin_signin.ps1 -ExpectedUpn "<config adminUpn>"`

Run it with the terminal tool in **async** mode: it prints one `{"status":"waiting",...}`
line, then waits up to 15 minutes for the sign-in. Post the action in the usual shape - `🔐
**ACTION NEEDED** - open the sign-in code file <codeFile> (it holds the code and the page
to enter it on) and sign in as <adminUpn>` - never the code itself. Read the shell again
until the final line: `ok:true` stores the sign-in for one run (12 hours at most;
`launch_seeder.ps1` hands it to the next run, which removes it from disk); `ok:false`
carries a `reason` and `message` to relay (`consent-missing` sends the user back to the
runner's `--grant-demoiq-consent`; `wrong-account`, `wrong-tenant`, `declined` and `expired`
need the person to retry). People running it in their own terminal may add
`-ShowCodeInTerminal`. `-Status` reports whether a sign-in is stored; `-Clear` forgets it.

## Keep the run moving

- **Bounded, file-based tool calls.** Never `cat`, `Get-Content` or `Select-String` the
  large files - a generated `DemoIQ.ps1` (about 1 MB), `engine/DemoIQ.html` (about 850 KB),
  the Config Builder or any `aop_*.html`. Use the grep tool with a tight pattern or `view`
  with a line range (never on a config, see Hard rule 4). Long work runs detached with its
  output in log files (the seeder, the local server) or in the terminal tool's async mode
  (the administrator's sign-in); read the logs rather than holding a live console. If a
  shell stops responding, stop that shell session and continue in a new one instead of
  re-issuing the command; detached work keeps running.
- **Never pause mid-pipeline.** Once the preview is approved, chain regenerate -> sign-in
  check (the administrator signed in at the end of Flow 1a) -> launch -> monitor -> wrap-up
  without asking for permission. Every turn carries a status
  line and a tool call until the next gate or the end of the flow.
- **Heartbeat every wait.** While anything long-running is in progress (the seeder, a
  sub-agent, a sign-in, propagation), post one concrete line - what, how long, the latest
  progress signal - at least every 60-90 seconds, and issue the next check in the same
  turn. Never end your turn while the work is unfinished: nothing re-invokes you. Let the
  helpers pace the wait: run `monitor_seeding.ps1 -WaitSeconds 60` as a sync call with the
  tool's wait set above 60 seconds (for example 90), so one call is one heartbeat. Attribute
  sub-agent progress yourself ("the authoring agent is on key 6 of 9").
- **Scope-locked recovery.** When a step fails, either diagnose it per its playbook, retry
  that step (or its targeted top-up), or report it honestly and return to the menu. Never
  widen scope on your own: no re-seed, full re-run, cleanup, infrastructure changes or
  config edits as "recovery". Re-seed duplicates content and exists only for an explicit
  "run it again".
- **Unmissable user actions.** Put every action the user must take on its own line, in this
  shape: `🔐 **ACTION NEEDED** - <what to open and do>`. Acknowledge the completed action in
  your next status line ("Sign-in detected for `<upn>` - launching the run.").
- Use plain hyphens instead of em dashes in any text written to PowerShell or files.

## Platform notes

| | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Administrator sign-in (12-hour maximum, one run) | DPAPI-protected file in the user's temp folder | AES-256-GCM file (0600) in `$TMPDIR`; key in `<ws>/.session/` (0600) | as macOS, under `$XDG_RUNTIME_DIR` when set, else `<ws>/.session/` |
| Sign-in code file | `<ws>/.session/admin-signin-code.txt` (deleted after the sign-in) | same, 0600 | same, 0600 |
| Configs | `<ws>/configs/` in the user's profile | `<ws>/configs/`, files 0600, folder 0700 | same as macOS |
| Detached seeder and local server | hidden `Start-Process` | new session via `scripts/detach.js` | same as macOS |
| Opening links | default browser | `open` | `$BROWSER`, else `xdg-open` on a desktop; otherwise forward the port privately |

The seeder runs detached and survives the end of a Copilot tool call; the machine must stay
on and online until it finishes. On macOS and Linux it also survives closing Copilot (its
own session, output in `<run folder>/logs/launcher.log`); on Windows, keep the Copilot
session open for the run, as upstream advises. Re-attach by running `monitor_seeding.ps1`
again.

## How DemoIQ works (Flow 4)

Answer "how does DemoIQ work" from this overview and the playbooks:

- **Identity** (deploy-platform runner): a delegated-only, public-client DemoIQ app from
  Terraform, consented by an administrator. No certificate, secret or stored administrator
  password exists anywhere.
- **Story and config:** `scripts/draft_config.ps1` writes a story - drafted from this
  project's scenario, or the template's North Works / South Works sample - into a plain
  config; the **Config Builder** (`DemoIQ-Config-Builder.html`, served on loopback by
  `scripts/serve_local.ps1`, never `file://`) opens it for review, the user types the demo
  users' passwords and **Save config** writes it straight into `<ws>/configs/`.
- **`scripts/generate.js`** runs the engine's own `generatePs1()` in a Node sandbox, so the
  seeder matches what the DemoIQ page itself would produce for that scenario, language and
  content mix. `--emit prompts` / `--emit aop-context` export what you need to author the
  content; `--pasted <map.json>` bakes your content in. `scripts/platform_patch.js` then
  applies the delegated patch, marks the seeder and fails loudly (exit 10) if the engine no
  longer matches.
- **You author everything**: emails, chats, channel posts, meetings and transcripts, and
  the Office files, validated by `scripts/validate_pasted.js` and previewed by
  `scripts/preview_content.js` before the user approves.
- **The seeder** (`DemoIQ.ps1`, run by `scripts/run_seeder.ps1`): uses the administrator's
  sign-in for the run, signs in as each demo user (a public-client password grant, which is
  why MFA is relaxed for them during the run), creates the content through Microsoft Graph
  REST in Outlook, Teams, OneDrive and Calendar, synthesizes meeting audio on the
  platform's Foundry account with the Azure CLI's Entra token, writes the AoP runbook,
  restores the tenant's security settings and reports them in `tenant_state.json`.
- **Cleanup** is a run of the same seeder in cleanup mode: prepare, delete what the run
  manifests list (as each owner, and the team and chats as the administrator), restore.
- Five personas, about 29 industries with four scenarios each and 48 content languages
  (live counts: `generate.js --list`). All content is fictional; internal demo tenants
  only.

## Prerequisites

- The deploy-platform runner's `demoiq` phase is complete (`status.ps1` is not
  `not-set-up`), on this host and checkout.
- PowerShell 7.4+ and Node.js 18+ on that host (the devcontainer includes both).
- The deploying user's tenant is a **Microsoft 365 demo tenant**: an administrator who can
  change Conditional Access, authentication methods and Security Defaults and delete Teams
  chats (a Global Administrator), and five existing demo users with Exchange Online, Teams
  and OneDrive (and Microsoft 365 Copilot licenses to demo Copilot and Work IQ). If the
  tenant has no such users, DemoIQ cannot seed it; do not switch tenants.
- For meeting audio: the Azure CLI signed in to the deployment tenant with access to the
  platform's Foundry account (`preflight_azure_cli.ps1` checks it). Meetings and
  transcripts seed without it.
- For the Work IQ check: the `workiq` plugin (see
  [Copilot tooling](../../../docs/copilot-tooling.md)), Work IQ tenant consent and a
  licensed demo user to sign in to it as.
- For live streaming: FFmpeg and an RTMP-In-capable Teams meeting (often Teams Premium).

## Notes and limits

- **Tenant security settings round-trip per run.** Tenant preparation turns off, for the
  run only: Security Defaults (when on), per-user MFA on demo users that had it (original
  state recorded), and Conditional Access for the demo users, by adding the
  `Demo-Seeder-MFA-Exclusion` group to every enabled policy's exclusions. The administrator
  is never added. A policy that already excluded that group is deliberate configuration:
  recorded (`caExclusion.preExistingExclusions`), reported and never modified. The restore
  step undoes exactly the run's own changes - the exclusion is removed from the policies
  this run patched (`caExclusion.policiesTouched`), MFA restored, Security Defaults
  re-enabled - then writes `DemoIQ-Output/tenant_state.json`, which Flow 1h and Flow 6
  relay on every terminal status. The group itself is kept for reuse. A run that dies
  before its restore leaves the tenant prepared (`phase:"prepared"`, naming the touched
  policies and each per-user MFA change with its original state), and a restore that fails
  partway leaves `restored:false`; either way `status.ps1` reports `tenant-prepared`.
  Re-running from the same run folder restores them, while a run in a different folder
  deliberately leaves them alone (restore,
  never strip: a blanket sweep once removed a deliberate exclusion and locked a tenant's
  administrators out). Demo users getting MFA prompts again after a completed run is the
  restore working.
- **Plain configs are a deliberate trade-off.** A config holds the demo users' passwords in
  plain text in an owner-only file on this machine, so anyone who can act as this
  operating-system account can read them. Use dedicated demo accounts whose passwords
  protect nothing else. An encrypted config from upstream DemoIQ is converted once, by the
  user in their own terminal (`scripts/convert_config.ps1`).
- **Seeders come only from this skill.** `run_seeder.ps1` refuses a seeder without the
  `demoiq-seed: delegated patch v2` marker, including the DemoIQ page's own "Download
  Script" output and seeders from the upstream DemoIQ-Scout skill; regenerate them here.
- **No post-run reads of other users' files.** The administrator's sign-in cannot open the
  demo users' OneDrives, and demo users can sign in only while a run relaxes their MFA, so
  there is no live re-verification after a run. The offline file check (Flow 1h) and the
  optional Work IQ check (Flow 9) are the evidence.
- **Presenter copies in the administrator's OneDrive.** Besides the run folder, the seeder
  uploads the AoP runbook, the meeting MP3 and its transcript to the administrator's
  OneDrive folder `DemoIQ-Output` (beside the `cleanup.config` manifest), as upstream
  DemoIQ does. Tenant cleanup (Flow 6) removes the seeded content, not these presenter
  copies: after the last cleanup, the administrator can delete that folder by hand.
- **Meeting audio is billable** on the platform's Foundry account (text to speech, usage
  based). Without audio, meetings and their transcript text still seed.
- **Collision-resilient uploads.** When an Office file name already exists in the owner's
  OneDrive, the seeder uploads a new timestamped file instead of overwriting, so repeat
  runs stay additive.
- **Copilot Code and Copilot Autopilot AoP surfaces** (`code`, `autopilot`) add runbook
  sections and supporting content; DemoIQ cannot verify those tabs are enabled in the
  tenant, so both sections carry a check-before-you-demo note.
- **Not automated:** the Teams meeting policy "Allow streaming media input" (RTMP-In) for
  live streaming; it is a manual Teams admin center setting.
- **Removing DemoIQ:** clean up the tenant content first (Flow 6), then the local footprint
  (Flow 10), then set `demoiq_enabled = false` and rerun the runner (or destroy the
  platform) to remove the app.

## Maintaining this adaptation

- This copy lives in the repository and GitHub Copilot App and CLI discover it
  automatically (CLI `/skills list`; App **Customize > Skills**). Don't install a second
  copy under `~/.copilot/skills`. If the upstream DemoIQ-Scout skill is also installed,
  both share `<ws>`; this adaptation never reads upstream's certificate or app files.
- Run the offline tests (no tenant, no network sign-in):
  `pwsh -NoProfile -File .github/skills/demoiq-seed/tests/run_tests.ps1`.
- To take a new DemoIQ release, follow [NOTICE.md](NOTICE.md#updating-from-a-new-demoiq-release).

# DemoIQ for GitHub Copilot - Flow 1: Build a demo tenant seed (spine)

> Part of the **demoiq-seed** skill (DemoIQ adapted for GitHub Copilot). The skill folder is
> `.github/skills/demoiq-seed` (relative to the repository root, the normal working
> directory; elsewhere use the skill's base directory) and `<ws>` (the single canonical
> output home) is `~/.copilot/DemoIQ-Output-Scout` - see SKILL.md "Paths and commands".
> Commands are single lines that run unchanged in bash, zsh and PowerShell: pass the
> absolute paths the scripts print, double-quoted. SKILL.md's hard rules, gates and "Keep
> the run moving" rules apply throughout.

## How Flow 1 is organized (read the STAGE file before running its stage)

The build is split into **stage playbooks** so that a (re-)read - including the re-reads a
session checkpoint forces - only costs the stage you are in. **Read the current stage's file
FULLY before executing that stage; never run a stage from memory.** This spine holds only
the rules that bind the whole pipeline.

| Stage | Covers | Playbook |
|---|---|---|
| **1a-1c.5 Story, config and checks** | the config library and this deployment's DemoIQ identity (step 0); Gate 1 - draft a config from this project's story (or the template's sample), use a saved config, configure from scratch in the Config Builder, or continue the previous run; serving the Builder and picking up the saved config; the run mode; the pre-seed checks (demo-user sign-in and licenses, the Azure CLI for meeting audio) and the administrator's sign-in for the run | `references/flow-1a-configure.md` |
| **1d Author, preview, regenerate** | per-key prompt dump, run folder, chunked authoring around a story bible (parallel sub-agents when available), validation, the MANDATORY content preview (Gate 2), seeder regeneration | `references/flow-1d-author.md` |
| **1h-1i Launch, monitor, wrap up** | the sign-in check (`admin_signin.ps1 -Status`; a new sign-in only when none is stored), the detached launch (the preview approval is the seed consent - no second confirmation), monitoring with heartbeats, the tenant-state report, the content summary and the AoP runbook, then the offer of the optional Work IQ check | `references/flow-1h-seed.md` |
| **1R Re-seed** | a prior run again without re-authoring (its stored content bundle) - its own menu choice (`🔁 Re-seed the last run`) when a reusable run exists | `references/flow-1r-reseed.md` |
| **Flow 9 Work IQ check** | optional, offered at the end of 1h-1i: asks Work IQ, as a demo user, a few runbook prompts | `references/flow-9-workiq-check.md` |

A normal build runs 1a -> 1d -> 1h, then offers Flow 9. A re-seed runs 1R, which borrows 1c
and 1c.5 from the configure stage and all of 1h. The stage numbering (1a, 1b, 1c, 1c.5, 1d,
1h, 1i) is stable and cross-referenced from the other flows - do not renumber.

## Rules that bind the WHOLE pipeline

> **Keep moving / never pause after the preview approval (hard rule).** The only stops are
> the `ask_user` gates: (0) the target tenant (SKILL.md Step 0 - answered before Flow 1
> starts; the tenant is `deployment.tenantId` from this deployment's receipts), (1) the
> config choice (1a), (2) the content-preview approval (1d; a re-seed holds it too), whose
> "Looks good - seed it now" IS the seed consent, and (3) the cleanup confirmation (Flow 6).
> Questions inside a stage (the story, which saved config, the run mode) use `ask_user`
> too. A `🔐 ACTION NEEDED` hand-off (Save config in the Builder, converting an upstream
> config in the user's own terminal, an `az login`, the administrator's sign-in) waits for
> the user's action - it is not an extra gate. The administrator signs in at the end of
> 1c.5, so once the preview is approved, chain regenerate -> sign-in check -> launch ->
> monitor -> wrap-up without asking for permission or ending a turn to "think". Every turn
> carries a one-line status and a tool call until the next gate or the end of the flow.

- **The config is the single input.** Industry, scenario, language, content types, counts,
  AoP choices and the five demo users come from one plain config in `<ws>/configs`, never
  gathered piecemeal in chat: the Story path writes its story into a config before the
  Builder opens it, and the generated seeder reads the config at run time.
- **Security.** No password in chat, on a command line or in a file you write: demo-user
  passwords are typed only in the Config Builder, and the administrator has no password at
  all - they sign in in their own browser with a one-time code that lives only in its
  owner-only code file, which you never read, print or relay. Never open a config with
  `view`, `cat` or a search tool; use the summaries from `find_config.ps1`,
  `preflight_credentials.ps1` and `status.ps1`. Configs, runs, logs and deliverables stay in
  `<ws>`, never in the repository; the seeder embeds no credentials.
- **Scope-locked recovery.** When a step fails, diagnose it per its playbook, retry that step
  (or its targeted top-up), or report honestly and return to the menu. Never widen scope on
  your own: no re-seed, full re-run, cleanup, infrastructure change or config edit as
  "recovery".
- **One run at a time.** Seed and cleanup runs relax and restore the same tenant settings;
  `launch_seeder.ps1` refuses a second run, and you never start one while `status.ps1` says
  `running`.
- **One-way and honest.** The project's scenario may shape the story; nothing seeded (and
  no AoP runbook) goes back into Foundry IQ knowledge, `scenario/knowledge/`, a lakehouse or
  an index. A finished run is `seeded`, never "ready": only the optional Work IQ check
  (Flow 9) adds evidence that Work IQ answers from it.

---

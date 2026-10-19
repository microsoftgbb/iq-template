# DemoIQ for GitHub Copilot - Flow 1R: Re-seed a prior run (skip authoring; keep the preview)

> Part of the **demoiq-seed** skill (DemoIQ adapted for GitHub Copilot) - the re-seed stage of
> Flow 1 (see `references/flow-1-build.md`, the spine, for the whole-pipeline rules). Read
> this file FULLY before executing the flow - do not run it from memory. The skill folder is
> `.github/skills/demoiq-seed` (relative to the repository root, the normal working
> directory; elsewhere use the skill's base directory) and `<ws>` (the single canonical
> output home) is `~/.copilot/DemoIQ-Output-Scout` - see SKILL.md "Paths and commands".
> Commands are single lines that run unchanged in bash, zsh and PowerShell: pass the
> absolute paths the scripts print, double-quoted. SKILL.md's hard rules, gates and "Keep
> the run moving" rules apply throughout.

## Flow 1R - Re-seed the last run (menu choice `🔁 Re-seed the last run`, shown only when eligible; also on "run it again" / "re-seed")

Re-seed repeats a prior run's CONTENT into the tenant without re-authoring it: the authored
`--pasted` map was persisted into the run folder at generation time
(`<run folder>/demoiq-pasted.json`, written automatically by `generate.js`), so the entire
~10-15 min authoring stage is skipped. Everything else is the normal pipeline - validate ->
**full content preview** (whose approval IS the seed go-ahead) -> the administrator's sign-in
-> launch -> monitor -> summary - so
the user still sees exactly what will land before anything touches the tenant. Total time
**~7-25 min** (seeding only). Dates are computed at SEED time (meetings, timestamps), so
re-seeded content is freshly dated - no stale-date fixup is needed.

**Eligibility (check BEFORE offering the menu choice or accepting the path).** A prior run is
re-seedable when ALL of:
1. `<ws>/runs/<run>/demoiq-pasted.json` exists (older runs generated before this file shipped
   have no stored map and are NOT re-seedable; offer a normal Build, or the
   Continuation path in `flow-1a-configure.md`, which authors fresh content instead);
2. the run's config still exists in `<ws>/configs` as a plain config (resolve it via the run's
   `run.json` `configFile` path, falling back to a name match in `find_config.ps1`'s
   `configs[]`; an `encrypted:true` entry is usable only after the user converts it once in
   their own terminal with `scripts/convert_config.ps1`) - the map holds content only; the
   demo users' passwords always come from the config, which you never open;
3. that config names this deployment's DemoIQ app (`find_config.ps1` -> its
   `appMatchesDeployment` is true; otherwise offer `repoint_config.ps1 -ConfigPath "<config>"`
   first, as Flow 1a does);
4. the run targeted this deployment's tenant - the one Gate 0 confirmed (its `run.json`
   `tenant`): a re-seed only ever repeats a run into that SAME tenant, and the
   administrator's sign-in refuses any other (`wrong-tenant`).
When several runs qualify, list them newest-first (scenario title + date + tenant from each
`run.json`) and let the user pick; when exactly one qualifies, confirm that one.

**A re-seed is NEVER a failure remedy.** This flow exists for explicit user intent ("run it
again" / `🔁 Re-seed the last run`) only. When a sub-step of a run failed or came up partial
(a missing MP3 is the classic case), do NOT offer or propose a re-seed as the fix - it
re-creates all the content that DID land (duplicate-creating by design) to chase one missing
piece. The correct offer is the targeted top-up - resuming that run folder, whose ledger
re-attempts only what is missing - in `flow-1h-seed.md` ("Recover a partial or failed run"),
or a diagnose-and-report per the scope-locked recovery rule in SKILL.md ("Keep the run
moving"). That includes arriving here from `status.ps1`'s `seeded-partial` state (its
`next[]` reads "Top up only the missing content types (Flow 1r)"): follow that Flow 1h
section, not the steps below.

Steps:

1. **Pick the run** (above) and restate what it seeds - scenario title, content types (the
   stored map's top-level keys), tenant - so the user is confirming something concrete.
2. **Run mode, pre-seed checks and the sign-in - same as 1c / 1c.5**
   (`references/flow-1a-configure.md`): the run mode (default 1 = Fast), then Check B
   (`preflight_credentials.ps1 -ConfigPath "<the run's config>"` - the demo users' sign-ins
   and licenses) and Check C (`preflight_azure_cli.ps1 -ConfigPath "<the run's config>"` -
   silently not applicable unless the run seeds meeting audio), reported as ONE outcome, then
   the administrator's sign-in that ends 1c.5. Check B's `reason:"app-mismatch"` means the
   config names another app than this deployment's: repoint it first (eligibility 3).
3. **Create a NEW run folder** with `new_run.ps1` (same scenario title + tenant, and the run's
   config as `-ConfigPath`). A re-seed
   is a NEW run with its own manifest, logs, and cleanup scope - NEVER re-launch the old
   folder's `DemoIQ.ps1` in place (its outputs/logs would interleave with the prior run's
   and cleanup could no longer address the two runs separately; its seed ledger would also
   skip everything already seeded - re-launching that folder is Flow 1h's resume, which only
   completes the original run).
4. **Validate the stored map** (schemas can drift between skill versions). First pick the
   prefs: `<prior run folder>/demoiq-prefs.json` - the prefs that run was generated from,
   which `generate.js` keeps beside its seeder - when it exists; for an older run without
   it, `<scratch>/demoiq-prefs.json` (written as `{}` with your file-creation tool if it does
   not exist yet, as in 1d step 1). That is `<the run's prefs>` below. Re-dump the prompts
   for the run's config with it (`generate.js --emit prompts --out-file
   "<scratch>/demoiq-prompts.json"`, exactly as in `flow-1d-author.md` step 2 - same config,
   seconds, no authoring) so the validator gets the
   `officeFiles` metadata and can enforce the config's Word/PPT/Excel file counts and primary
   names against the stored bundle:
   ```text
   node .github/skills/demoiq-seed/scripts/validate_pasted.js --pasted "<prior run folder>/demoiq-pasted.json" --prompts "<scratch>/demoiq-prompts.json" --keys "<the map's own top-level keys, comma-separated>"
   ```
   Exit 0 = proceed. Exit 7 = the engine's schema moved since this map was authored: do NOT
   hand-patch the stored map - tell the user and fall back to a normal Build (fresh
   authoring), reusing the same config. Also confirm the re-dump's scenario (skim one
   prompt) is the prior run's (`scenarioTitle` in its `run.json`). With the run's own
   `demoiq-prefs.json` it matches. For an older run, when it differs and the scratch prefs
   file holds another run's story, rewrite it as `{}` and re-dump once; when it still
   differs (the original story came from a prefs file - Flow 1a's Describe-it or Continuation
   path - that no longer holds it), stop and offer a normal Build instead.
5. **Preview - the gate is NOT skipped.** Render the stored map with `preview_content.js`
   (`--html-out "<NEW run folder>/DemoIQ-Output/content_preview.html"`) and serve it
   detached in the user's browser, exactly as in 1d step 5b:
   ```text
   pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/serve_local.ps1 -Detach -OpenBrowser -Root "<NEW run folder>/DemoIQ-Output" -Path "content_preview.html"
   ```
   Link the printed `[SERVE-URL]` in chat, give the same pre-gate context as 1d step 5b (the
   target tenant, and what seeding does to it as Gate 0 said: the delegated-only DemoIQ app
   with the administrator's sign-in for the run; Security Defaults (when on) and per-user MFA
   turned off and Conditional Access exclusions added for the five demo users only - never
   the administrator - for the run, and restored at its end; the demo users signing in with
   the config's passwords), and hold the SAME hard gate (Gate 2): one `ask_user` with the
   question *"Does this content look right to seed into tenant `<tenantId>`?"* and the choices
   **"Looks good - seed it now"** / **"Re-author (tell me what to change)"** /
   **"Cancel this run"**. "Looks good - seed it now" IS the seed consent - steps 6-7 then run
   with NO further question. Re-seed skips the WRITING, never the user's look at what will
   land. If the user wants changes ("make the emails longer"), re-author only the affected
   key(s) per `flow-1d-author.md` step 4 into a COPY of the map for this new run (in
   `<scratch>`, then pass that copy to step 6 instead) - leave the prior run's stored original
   untouched.
6. **Generate into the NEW run folder** (bakes the stored content; the administrator, app,
   demo users and scenario come from the config; no authoring):
   ```text
   node .github/skills/demoiq-seed/scripts/generate.js --html .github/skills/demoiq-seed/engine/DemoIQ.html --prefs "<the run's prefs>" --from-config "<the run's config path>" --pasted "<prior run folder>/demoiq-pasted.json" --out-dir "<NEW runFolder>" --emit ps1 --script-name DemoIQ.ps1
   ```
   (The generator re-copies the map, the prefs and `run_identity.json` into the new folder,
   so the new run is itself re-seedable.) Exit 2 with "encrypted DemoIQ config" = the config must be
   converted once by the user (eligibility 2); exit 5 = the config lacks `adminUpn` or
   `existingAppId` (re-save it in the Config Builder, or `repoint_config.ps1` for the app);
   exit 9 = a stored key's content type is not enabled in the config (the config changed
   since that run) - surface which, and offer to proceed without it or fall back to a normal
   Build; exit 3, 4 or 10 = the generator no longer matches the DemoIQ engine (exit 10: the
   delegated seeder patch, `scripts/platform_patch.js`) - stop, do not seed, and report it
   (the engine changed and the adaptation must be updated through a pull request to this
   repository - never patch the skill mid-run).
7. **Sign-in check + launch + monitor + summarize** exactly per `references/flow-1h-seed.md`
   (no separate confirm - step 5's approval carried it): `admin_signin.ps1 -Status` (sign in
   again only when nothing usable is stored), `launch_seeder.ps1`, then chain
   `monitor_seeding.ps1 -WaitSeconds 60` calls (one-line report + next call in the SAME
   turn until terminal), then the Demo Tenant Content Summary + tenant state + deliverables,
   the optional Work IQ check offer and the `status.ps1` summary.

Caveats (tell the user up front):
- **Same scenario = same Office file names.** If the prior run's content is still IN the
  tenant, recommend running **Flow 6 cleanup of that run first** - the natural re-seed
  rhythm is demo -> clean up -> re-seed (the cleanup is its own run, with its own
  administrator sign-in; one DemoIQ run at a time). Within ~15-30 min of a prior upload,
  OneDrive can still hold locks on those names: HTTP 423 is FATAL mid-run and leaves the
  tenant prepared (see the re-run caveat in `flow-1h-seed.md`) - wait the lock out or clean
  up first. When base names merely already exist (no lock), the engine falls back to
  collision-safe timestamped names - expected behavior, say so, not an error.
- The tenant, users, language, and counts all come from the SAME config as the original
  run. A different tenant is out of scope (this skill seeds only the confirmed target
  tenant); different users or counts mean a new Build with a different config, not a
  re-seed.

---

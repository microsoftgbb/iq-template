# DemoIQ for GitHub Copilot - Flow 1, stage 1h-1i: Sign in, run the seeder, monitor it, wrap up

> Part of the **demoiq-seed** skill (DemoIQ adapted for GitHub Copilot) - one STAGE of Flow 1
> (see `references/flow-1-build.md`, the spine, for the whole-pipeline rules: never-pause, the
> gates, security). Read this file FULLY before executing this stage - do not run it from
> memory. The skill folder is `.github/skills/demoiq-seed` (relative to the repository root,
> the normal working directory; elsewhere use the skill's base directory) and `<ws>` (the
> single canonical output home) is `~/.copilot/DemoIQ-Output-Scout` - see SKILL.md "Paths and
> commands". Commands are single lines that run unchanged in bash, zsh and PowerShell: pass
> the absolute paths the scripts print, double-quoted. SKILL.md's hard rules, gates and "Keep
> the run moving" rules apply throughout.
> **Previous stage:** `references/flow-1d-author.md` (author + preview + regenerate).

## 1h. Sign in, then run the seeder in the background (unattended)

The seeder (`DemoIQ.ps1`) asks `Read-Host` prompts - the run mode and, only after a component
failure, the end-of-run `RETRY OPTIONS` menu - and `Read-Host` **only reads from a real
console (it ignores piped stdin)**. To run it truly hands-off we use the launcher
`scripts/run_seeder.ps1`, which **overrides `Read-Host` and runs the seeder inside its own
PowerShell session**, answering every prompt programmatically. It also **consumes the
administrator's sign-in**: it reads it from the protected session store (SKILL.md "Platform
notes"), deletes it from disk
and hands it to the seeder, which clears it from its environment as soon as it reads it - so
every seed or cleanup run needs its own sign-in. Beyond the delegated patch `generate.js`
applied (1d step 6), the seeder file is never modified - and `run_seeder.ps1` refuses any
seeder without that patch's marker (status `FAILED not-a-demoiq-seed-seeder`).

Prompts the launcher answers, in order:
1. **Run mode** - the `-RunMode` parameter. **1 = Fast** (~10-15 min, fresh
   content) or **2 = Full** (~15-20 min, continues a prior run). The value was
   chosen at 1c (default **1**) - pass it through; never re-ask here.
2. **Any later prompt** (e.g. the end-of-run `RETRY OPTIONS` menu, only on a
   component failure) - answered **`0`** = exit keeping what succeeded (with `-RetryOnFail`,
   the first such prompt gets `1` once - see step 2).

Procedure:
1. **No separate seed confirm - the 1d 5b preview gate already carried it; the one thing
   left before the launch is the administrator's sign-in (an ACTION NEEDED, not a gate).**
   The user's "Looks good - seed it now" at the content preview IS the go-ahead: do NOT
   present another "seed now / cancel" menu here (the double gate is one step too many). The
   run mode was chosen at 1c - restate it in the one-line launch status (e.g. *"Launching the
   seeder - Fast mode, ~7-25 min"*) rather than re-asking. The configure stage ended with the
   administrator's sign-in (1c.5); a sign-in is kept for at most 12 hours and the next run
   consumes it, so confirm it is still there before launching:
   ```text
   pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/admin_signin.ps1 -Status
   ```
   It prints `{ ok, signedIn, user, tenantId, clientId, expiresAt }` (never a token or a
   code). Go straight to step 2 when `signedIn` is true, `user` is the config's `adminUpn`
   and `clientId` is this deployment's DemoIQ app (both from `find_config.ps1`: the config's
   `adminUpn` and `deployment.clientId` - never open the config). Otherwise - nothing stored,
   or a sign-in for another account or app - sign in again exactly as SKILL.md "The
   administrator's sign-in" says:
   ```text
   pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/admin_signin.ps1 -ExpectedUpn "<config adminUpn>"
   ```
   Run it in the terminal tool's **async** mode. From its first line
   (`{"status":"waiting","codeFile":...,"verificationUri":...,"expiresAt":...}`) post one
   `🔐 **ACTION NEEDED** - open the sign-in code file <codeFile> (it holds the code and the page to enter it on) and sign in as <adminUpn>`
   line - never the code itself - then read the shell again about every 60 seconds, with a
   heartbeat line each time (*"Waiting for the administrator's sign-in - 2 min in; the code
   is valid until <expiresAt>"*), until the final line:
   - `ok:true` -> acknowledge it (*"Sign-in detected for `<user>` - launching the run."*),
     relay each `warnings[]` entry in one plain line (`not-global-administrator`: preparing
     the tenant normally needs a Global Administrator; `consent-unverified`: the runner could
     not confirm tenant-wide consent), then continue with step 2.
   - `ok:false` -> relay `message` as written and act on `reason` as SKILL.md says
     (`consent-missing`: an administrator runs the deploy-platform runner with
     `--grant-demoiq-consent`; `wrong-account`, `wrong-tenant`, `declined`, `expired`: the
     person retries). Also: `no-receipts` means DemoIQ is not set up for this deployment
     (SKILL.md Step 0); `wrong-app`, `sign-in-failed`, `network` or `store-failed`: retry
     once, then report it and return to the menu (scope-locked recovery).
2. **Launch the seeder with the deterministic launch helper, from a single SYNC terminal
   call.** The helper wraps the one launch pattern that works reliably - it starts the
   seeder in its own process (Windows: a hidden `Start-Process`; macOS/Linux: a new session
   via `scripts/detach.js`, output in `<run folder>/logs/launcher.log`), with spaced paths
   quoted correctly (the quoting lives in the script, not re-typed per run):
   ```text
   pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/launch_seeder.ps1 -SeederScript "<run folder>/DemoIQ.ps1" -ConfigFile "<path to the config>" -RunMode 1 -RetryOnFail
   ```
   (`-RunMode 2` only when the user chose Full at 1c.)
   **Always pass `-RetryOnFail`**: it answers the seeder's end-of-run RETRY menu with `1`
   exactly once, so any component that still failed after the in-run retries (a Graph
   blip on a chat/channel/email send) gets ONE automatic second pass before the run
   settles - a direct "place all requested content" win at zero interaction cost. A
   second retry prompt is always answered `0`, so it can never loop.
   Note: a user's delegated token timing out mid-seed is now handled
   automatically and idempotently BEFORE this menu - the seeder re-acquires tokens for
   transiently-failed users and replays only their skipped chat/channel/meeting-chat
   items (permanent failures like a bad password are not retried). When content is still
   missing at the end, the seeder prints a **Content completion** table (seeded/requested/
   missing by component and user) and a **PARTIAL** banner instead of a clean "Done",
   and `monitor_seeding.ps1` reports `status: partial`. On a `partial` run, do NOT
   present the seed as fully complete: report the missing items from that table (see
   Flow 1i) alongside the deliverables that did land.
   It prints one JSON line: `{ ok, kind, launchedAt, processId, runFolder, monitorCommand }`
   (`kind` is `seed`; it also stamps the launch - `launchedAt`, `runMode`, `processId`, plus
   the app identity from `run_identity.json` - into the run folder's `run.json` manifest). It
   **fails fast** (exit 2, `{ ok:false, error }`, the `error` naming the next action) rather
   than letting a detached run die where nobody sees it - fix the cause, never retry blind:
   - a seeder or config path that does not exist -> use the absolute paths `new_run.ps1` and
     `find_config.ps1` printed;
   - a seeder without the `demoiq-seed: delegated patch v2` marker -> regenerate it (1d
     step 6); never run a seeder downloaded from the DemoIQ web page;
   - no stored sign-in, or one that cannot be read -> step 1;
   - a sign-in for another account than the config's `adminUpn`, or a config naming another
     app than this deployment's -> the launcher has already cleared that sign-in: sign in
     again as the config's administrator (step 1); for another app, first point the config at
     this deployment (`repoint_config.ps1 -ConfigPath "<config>"`, as in Flow 1a);
   - a config that is not plain (an encrypted upstream config) -> the user converts it once
     in their own terminal (`scripts/convert_config.ps1`); then use the converted config;
   - another DemoIQ run (seed or cleanup) in progress -> one run at a time (SKILL.md Hard
     rule 8): monitor that run instead and launch only after it has ended.
   - Run this as a **sync** terminal call (NOT the terminal tool's async/detach mode). It
     returns in seconds; the seeder keeps running in its own process, which survives the end
     of the tool call on every OS (on macOS/Linux it also survives closing GitHub Copilot; on
     Windows, keep the GitHub Copilot session open for the run - see the caveats below). The
     launcher (`run_seeder.ps1`) writes a `STARTING` sentinel immediately, then consumes the
     sign-in and writes `STARTED`.
   - **Save the `launchedAt` value** from the JSON - pass it to the monitor
     as `-SinceTime` so the monitor anchors to THIS launch and ignores any stale prior run
     (the ready-made `monitorCommand` in the JSON already includes it, plus `-WaitSeconds 60`).
   - **Verify the launch took:** on your NEXT step, run the monitor (step 3) with
     `-SinceTime` + `-WaitSeconds 60` - the wait window returns the moment the launch
     produces its transcript. `status:"not-started"` with a hint that the launch "likely did
     not take" (a `STARTING` heartbeat that went quiet without a transcript) means exactly
     that: re-launch with the same helper in a single sync call
     (after step 1 again if `admin_signin.ps1 -Status` now shows no sign-in). A run that stops
     straight away reports `status:"failed"`; its reason is in `statusRaw`:
     - `not-a-demoiq-seed-seeder` -> the seeder did not come from this skill's
       `generate.js`: regenerate it (1d step 6).
     - `no-admin-session`, or `admin-session:` (no sign-in reached the run, or it does not
       match this config) -> step 1, then re-launch. Nothing was seeded and the tenant was not
       touched.
     - `config-encrypted`, `config-unreadable` or `no-demo-users` -> fix the config (convert
       it, or re-save it in the Config Builder), then step 1 and re-launch. Nothing was seeded
       and the tenant was not touched.
     - `admin-session-failed` (the administrator's token could not be renewed) or
       `az-tenant-mismatch` (a meeting-audio run whose Azure CLI is signed in to another
       tenant, or not at all) -> these stop AFTER tenant preparation and before any content:
       nothing was seeded, but the tenant is prepared. Report the tenant-state block (1i), fix
       the cause (a fresh sign-in; for `az-tenant-mismatch`, step 1 of the MP3 ladder in
       1i), then resume this run folder ("Recover a partial or failed run" below) - it
       restores the tenant at its end.
   - **Never type into the seeder's process** - the launcher answers every prompt, so there
     is nothing to send it.
3. **Monitor with the bounded-wait progress helper** - run it as a **sync** terminal call
   with `-WaitSeconds 60` and the tool's wait time set ABOVE 60 seconds (e.g. 90), so one
   call = one heartbeat (it is read-only and does NOT disturb the running seeder).
   One call = one watch window: it BLOCKS up to 60s and returns EARLY the moment progress
   moves (a new [TAG] line, phase/percent/activity/sub-detail/seeded-count change, or a
   terminal status) - the SCRIPT does the pacing, so a call never sits longer than ~60s
   and no timing judgment is needed. It correlates the heartbeat + transcript into one
   friendly snapshot:
   ```text
   pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/monitor_seeding.ps1 -OutputFolder "<run folder>" -SinceTime "<the launched-at timestamp from step 2>" -WaitSeconds 60
   ```
   (the launcher's ready-made `monitorCommand` already includes both `-SinceTime` and
   `-WaitSeconds 60`). Passing `-SinceTime` anchors the monitor to the run you just
   launched: it ignores any heartbeat/transcript OLDER than that timestamp, so a stale
   prior run (hours or days old) is never reported as `done` or `running`. Without it, a
   leftover status file from a previous seed can be misread as the current run.
   It prints one JSON line: `{ ok, kind, status, statusRaw, mode, phaseStep, phaseTotal,
   phaseLabel, percent, currentActivity, subDetail, missingItems, missingByComponent, elapsed,
   startedAt, finishedAt, logFile, logAgeSec, seeded{ emails, chats, channels, word, ppt,
   excel, meetings, audio, aop }, lastLines[], hint, cleanupResult, waitedSec }` (`kind` is
   `seed` here; `statusRaw` is the launcher's raw heartbeat, e.g. `FAILED <reason>`;
   `cleanupResult` is for cleanup runs and stays null). `status` is one of
   **not-started | running | stalled | done | partial | failed | unknown**. `partial`
   means the run FINISHED but `missingItems` requested item(s) could not be seeded
   (detected from the seeder's `seed_result.json` / the heartbeat's `DONE missing=<n>`
   payload, with the transcript's `PARTIAL:` banner as fallback) - treat it as terminal,
   never as a clean done. `missingByComponent` (partial only) names WHICH components
   are short, e.g. `{"audio":1}` - scope any recovery to exactly those components (an
   audio-only miss follows the MP3 ladder in 1i, never a wider action). `waitedSec` is how long the call actually blocked (0 = it
   returned instantly on a terminal status).
   **THE LOOP RULE - after reporting a status line, IMMEDIATELY issue the next monitor
   call IN THE SAME TURN. Never end the turn while `status` is non-terminal.** Ending a
   turn does NOT schedule a re-check: nothing re-invokes you, so a turn that ends on a
   status line with no follow-up tool call IS the silent stall this rule exists to
   prevent. The chain is mechanical: call -> one-line report -> call -> ... until
   `status` is terminal: `done` or `partial` (then output the summary, step 1i - for
   `partial` ALSO report the missing-items table) or `failed` (then report the `hint` +
   failing lines). Never ask the user for permission to keep checking (no "want me to
   check now or wait 10 minutes?"), never insert a "thinking" pause between calls, and
   never stop because a phase "will take a while" - the propagation-heavy phases are
   exactly where the user otherwise stares at silence. Report each return in plain terms
   (e.g. *"Step 7/14: Seeding Teams channels (~50%), running ~7 min, 3 emails + 1
   channel seeded so far"* using `phaseStep`/`phaseLabel`/`percent`/`elapsed` + the
   `seeded` counts). The seeder runs ~15-25 min total (channel-heavy runs sit longest in
   Teams roster propagation).
   - **Cadence contract (hard rule): every monitor return = one fresh status line + the
     next monitor call, same turn.** Because each call blocks at most ~60s and returns
     early on movement, chaining call -> report -> call gives the user a new line at
     least every ~60-90 seconds for the WHOLE run. Never batch several returns behind
     one summary. If a call comes back with unchanged numbers (it waited the full
     window), still post the line (with the new elapsed time) - "no change, still
     posting channel 2/4, 11 min in" IS the heartbeat. From launch to `done`/`failed`
     there is no monitor return without a status line, and no status line without a
     next monitor call.
   - **Narrate from `currentActivity` + `subDetail`, not just `phaseStep`.** `phaseStep`
     is the FURTHEST step reached and can freeze for 10+ minutes during channel posting
     (files upload before channels post, so the label alone reads "Uploading Office files"
     while channels are the real activity). `currentActivity` says what is happening NOW
     and `subDetail` carries channel N/M + posts-landed counts - use them so every poll
     shows visible movement (e.g. *"posting channel 2/4 ('site-engineering', 6 msgs) - 14
     posts landed"*). Vary the narration with the fresh numbers; ten near-identical
     "still waiting on propagation" lines in a row reads like a stall even when it isn't.
   - **`status:"stalled"`** = the transcript stopped advancing while still `STARTED`
     (likely a killed seeder). Corroborate with a plain INSTANT call (omit
     `-WaitSeconds`) a few seconds later; if `logAgeSec` keeps growing, treat it as
     dead and report the last `lastLines` from the transcript.
   - **`status:"running"`** with a low, unchanging `phaseStep` across several returns
     and a large `logAgeSec` = same stalled signal - corroborate before declaring
     progress.
   - The monitor derives the phase from the transcript's `[TAG]` lines (the seeder's
     `Step N/14` text goes to the progress stream, which a transcript does not capture),
     so it reflects exactly what landed in the log.
   > Manual fallback (if you ever need it without the helper): a NEW
   > `<run folder>/logs/DemoIQ_<timestamp>.log` whose last-write time keeps advancing
   > (sample twice ~15s apart; a frozen log = dead seeder; on macOS/Linux the launcher's own
   > output is in `<run folder>/logs/launcher.log`); the status file
   > `<run folder>/DemoIQ-Output/.run_seeder_status` (`STARTED mode=1` -> `DONE` /
   > `FAILED <msg>`; stays `STARTED` if the process is killed, so corroborate); and a live
   > process - on macOS/Linux `ps -axo pid,command | grep run_seeder`, on Windows
   > `Get-CimInstance Win32_Process` filtered to a `CommandLine` containing `run_seeder`.
   > Tail the
   > `[CONFIG|AUTH|COPILOT|EMAIL|TEAMS|CHANNEL|WORD|PPT|EXCEL|AOP|MEETING|AUDIO|SEED|FILES|CLEANUP]`
   > lines for raw progress (the grep tool with that pattern, or `view` with a line range -
   > never dump the whole log).
4. **On completion (`status:"done"` OR `status:"partial"`), ALWAYS output the Demo Tenant
   Content Summary** (see step 1i). For `partial`, ADDITIONALLY report the transcript's
   `Content completion` table (which items are missing, for which user) and say plainly
   that the run is partial - never present it as fully complete. On `status:"failed"`,
   report the `hint`/message + the failing component lines from the transcript - AND the
   post-run TENANT STATE block (1i): a failed/stalled run usually died BEFORE its restore
   step, and the user must be told the tenant is still in its prepared (MFA-off-for-demo-
   users) state, not left to assume it healed. For `partial`, `failed` and `stalled` runs,
   the remedy is "Recover a partial or failed run" below - never a re-seed.

Caveats to tell the user:
- The seeder runs on this machine in its own detached process: it survives the end of the
  GitHub Copilot tool call on every OS. On macOS/Linux it also survives closing GitHub
  Copilot (its own session via `scripts/detach.js`, output in `<run folder>/logs/launcher.log`).
  On Windows this adaptation has not verified that the hidden child outlives the Copilot
  session, so keep the GitHub Copilot session open for the run. In all cases the machine
  must stay on and online until the seeder finishes (a sleeping laptop or a stopped
  Codespace stops it); re-attach any time by running `monitor_seeding.ps1` again.
- It mutates a real demo tenant - only the confirmed target tenant; never point it at
  production.
- Each run consumes the administrator's sign-in, so a resume, re-seed or cleanup starts with
  a new sign-in, and only one DemoIQ run (seed or cleanup) runs at a time.
- **Re-running the SAME scenario soon after a prior run can fail with HTTP 423
  "resourceLocked"** on the OneDrive file uploads (Word/PPT/Excel share the same
  deterministic file names, and OneDrive holds a short lock after upload). The
  seeder treats a 423 on file upload as FATAL, so the run halts BEFORE later
  phases - and its restore step does NOT run: the tenant stays prepared (`status.ps1`
  reports `tenant-prepared`) until that run folder is resumed or the tenant is restored by
  hand (Flow 6 "Tenant left prepared"); a new run in another folder deliberately leaves
  those changes alone. Wait for the lock to clear (often 15-30+ min), then resume the same
  run folder (below): what already landed is skipped and the run restores the tenant at its
  end. To avoid the collision next time, clean up the prior run first (Flow 6) or use a
  different scenario/industry so the file names differ.

### Recover a partial or failed run (the targeted top-up)

A `partial` run, a `failed` or `stalled` one and an audio-only miss share ONE remedy that never
duplicates the content that landed: **resume the same run folder**. Its
`DemoIQ-Output/seed_ledger.json` records every seeded email, chat, channel and meeting-chat
message and every meeting; launching the SAME run folder's `DemoIQ.ps1` again skips those
and re-attempts only what is missing (`missingByComponent` names it) - meetings are never
re-created. Office files are not in the ledger: they upload again under timestamped names
(additive, never overwritten - see the in-tenant files note in 1i). The resumed run prepares
and restores the tenant again and inherits what the earlier pass left changed
(`caExclusion.policiesTouched` and the users in `perUserMfa.users`), so it is also how a run
that died before its restore, or whose restore was incomplete, puts the tenant back.

1. **Fix the cause first** - the monitor's `hint` and failing lines, the launch failures in
   step 2, the MP3 ladder in 1i or the HTTP 423 caveat above. Resuming into an unfixed cause
   only repeats the failure.
2. **Offer it in one sentence** (*"Say 'resume' and I'll have you sign in again and re-run
   this run folder - only the missing items are re-attempted."*) and wait for the user's yes:
   it relaxes and restores the tenant's settings again.
3. **Run it:** sign in again (step 1 - the previous run consumed the sign-in), launch with the
   step 2 command against the SAME run folder's `DemoIQ.ps1` and its config (`configFile` in
   its `run.json`), then monitor (step 3) and wrap up (1i) as usual.

A cleanup run (Flow 6) clears the seed ledgers of the runs it purges, so a run folder
re-launched after its cleanup seeds from scratch instead of skipping items that are no longer
there. Never widen scope (SKILL.md "Keep the run moving"): a re-seed (Flow 1R) re-creates
everything that DID land and exists only for an explicit "run it again", and neither a new
Build nor a cleanup is a recovery step.

## 1i. Output the Demo Tenant Content Summary (always, at end of a run)

The **Demo Tenant Content Summary** is, by design, the first section of the run's
Art-of-the-Possible HTML (`<run folder>/DemoIQ-Output/aop_demo_runbook_<timestamp>.html`) -
that runbook is where the seeder records what was seeded, who owns it, and where it lives,
so YES, the summary is sourced from the AoP file (this is expected, not a bug). When a run
finishes, extract and present it in chat:
```text
node .github/skills/demoiq-seed/scripts/summarize_aop.js --dir "<run folder>/DemoIQ-Output"
```
It prints the summary as markdown (newest `aop_demo_*.html` - the engine writes
`aop_demo_runbook_<timestamp>.html` - is picked automatically) plus a final JSON line
(`{ok,file,generated}`). Render the markdown for the user; the AoP runbook itself (`file`)
is handed over in the Deliverables section below.

**Then ALWAYS run the post-seed file check (#6)** and fold its result into the summary, so the
user gets confirmation EVERY expected Office file actually landed correctly - the full configured
set (`options.wordDocs`/`pptDecks`/`excelWorkbooks` files per type), each with its proper
`.docx`/`.pptx`/`.xlsx` extension AND a browser-open `webUrl` (a SharePoint `Doc.aspx` rendering
link, or a `?web=1`/`&web=1` view link) so they open in Office on the web rather than downloading:
```text
node .github/skills/demoiq-seed/scripts/verify_seeded_files.js --dir "<run folder>/DemoIQ-Output"
```
It reads the run's AoP HTML (the same artifact above) for the real in-tenant file links,
cross-checks the `cleanup.config` manifest, and reads the run's EXPECTED Office file counts from
`run_identity.json` (`expectedOfficeFiles`, written beside the seeder by the generator - found
automatically in the run folder; `--identity <path>` overrides). It prints a small markdown table
(File / Type / Extension / Browser-open link) plus an expected-vs-seeded line, and a final JSON
line `{ ok, allGood, source, file, files[], expected, actual, expectedByType, actualByType,
missingByType, missingExpected[], warnings[] }` (`file` is the AoP runbook it read, when
`source` is `aop`).
Render that table as part of the summary. If `allGood:true`, state the files are verified
(full expected count + extensions + open-in-browser links OK). If `allGood:false`, surface each
`warnings[]` entry and the offending rows: a non-zero `missingByType` means the tenant holds FEWER
files than the config requested (e.g. expected 6, seeded 3 - exactly the multi-file merge defect
this check exists to catch; `missingExpected[]` names what is identifiable), a `no-web-link` row
means the file uploaded but its link won't open in the web viewer, and a `missing-link` row means
a seeded file in the manifest has no AoP entry (confirm it uploaded - possibly an HTTP 423
file-lock halt, see the re-run caveat in 1h). When no AoP exists (e.g. `aop` wasn't selected), the
check falls back to the manifest and verifies extensions + expected counts only (reporting webUrl
as "unknown") - still include it. Runs generated before `expectedOfficeFiles` existed report
`expected:null` and verify exactly as before.

**When that check cannot confirm the live links** - `source` is not `aop` (no AoP runbook this
run) or a row came back `no-link`, `missing-link` or `no-web-link` - say exactly that, per
file, and stop there: do not hand-roll Graph queries to fill the gap. This adaptation has no
live re-verification after a run (the administrator's sign-in cannot read the demo users'
OneDrives, and demo users can sign in only while a run relaxes their MFA). The evidence is
the seeder's own upload results - the run log's `[WORD|PPT|EXCEL] OK: Uploaded` lines (the
monitor's `seeded{ word, ppt, excel }` counts) and the files the AoP runbook and
`cleanup.config` recorded - and, end to end, the optional Work IQ check (Flow 9). A re-seed or
a top-up is never a verification step: it only adds content.

> **If `ok:false` (no AoP file - e.g. `aop` was not a selected content type, or the AoP
> step failed), still give the user a summary - do NOT just say "no summary available".**
> Build one yourself from the LAST monitor poll's `seeded{}` counts (emails, chats,
> channels, word, ppt, excel, meetings, audio) plus the `[... OK]` transcript lines, and
> present it in the same shape (what was seeded, the demo users, the scenario title). The
> AoP runbook is the preferred source, but the seeded-counts fallback means a missing AoP
> never leaves the user without a wrap-up. (If `aop` simply wasn't selected, note that the
> presenter runbook wasn't generated this run and offer to add it on a future run.)

**ALWAYS report the post-run TENANT STATE (deterministic - same shape every run, every
terminal status: `done`, `partial`, AND `failed`).** The user must see,
without asking, that the security changes the run made are back in place - especially MFA.
The seeder's restore step (14) undoes what tenant prep changed (CA exclusions, per-user MFA,
Security Defaults) and writes the machine-readable record
`<run folder>/DemoIQ-Output/tenant_state.json`:
`{ schema, phase: "prepared"|"restored", restored, mfaReinstated, securityDefaults{ wasEnabled,
reEnabled, error }, caExclusion{ groupName, policiesTouched[{id,displayName}],
preExistingExclusions[{id,displayName}], exclusionsRemoved, failures[], groupKept },
perUserMfa{ disabledForRun, restored, failures[], users[{ upn, uid, state, restored? }] },
note }` (`perUserMfa.users`: each demo user whose per-user MFA the run turned off, with the
`state` it had before; after the restore each carries `restored`).
`policiesTouched` = the policies THIS run added the exclusion group to (the only ones its
restore removes it from); `preExistingExclusions` = policies that already excluded the group
before the run - deliberate configuration the seeder never modifies (restore-not-strip).
The exclusion group and the per-user MFA changes cover the demo users only - the administrator
is never added or changed; Security Defaults (when it was on) is the one tenant-wide switch.
Read that file (one small JSON object - the `view` tool is fine) and render this block in the
summary, filled ONLY from the file - never from assumption:

> **Tenant state after this run**
> - **MFA: reinstated.** Every MFA-related change this run made has been undone - CA
>   exclusions removed from `<exclusionsRemoved>` policy(ies) this run changed, per-user MFA
>   restored for `<restored>` of `<disabledForRun>` changed demo user(s), Security Defaults
>   `<re-enabled (was on before the run) / was already off - untouched>`. When
>   `preExistingExclusions` is non-empty, add: "left in place (excluded the group before
>   this run - deliberate): `<displayNames>`". *(Use this exact opening only when
>   `mfaReinstated:true`. When every count is 0 and Security Defaults was already off, word
>   it "MFA: unchanged - this run made no MFA-related changes (none were needed on this
>   tenant)" - do not claim a reinstatement that had nothing to reinstate.)*
> - **Kept on purpose:** the `Demo-Seeder-MFA-Exclusion` group (reused by future runs) and
>   the DemoIQ app (managed by this deployment; remove it through the deploy-platform runner).
> - **Seeded demo content:** stays in the tenant until you run `🧹 Clean up seeded content`
>   (Flow 6).

Honesty rules (hard):
- **`mfaReinstated:false` or any `failures[]` non-empty** -> open with **"MFA NOT fully
  reinstated"**, name what failed from the failure entries, and give the manual fix (remove
  the exclusion group from exactly the policies in `caExclusion.policiesTouched` - never
  from the `preExistingExclusions` ones; re-enable per-user MFA for the named users
  in Entra ID > Users > Per-user MFA; re-enable Security Defaults in Entra ID > Properties).
  Never soften or omit this.
- **`phase:"prepared"` / `restored:false`** (the run died before its restore step - the
  usual state after `failed`/`stalled`/killed runs) -> report that the tenant is **still
  prepared for seeding**: demo users excluded from the CA policies named in
  `caExclusion.policiesTouched`, per-user MFA off for `<disabledForRun>` demo user(s),
  Security Defaults still off if `wasEnabled:true`. `status.ps1` now reports
  `tenant-prepared`, and no new run should start until it is resolved: resuming the SAME run
  folder ("Recover a partial or failed run" in 1h - a fresh sign-in, then the same launch
  command) restores all of it automatically at its step 14 (it inherits `policiesTouched`
  and `perUserMfa.users` from this artifact); a NEW run in a different folder deliberately does NOT remove those
  exclusions (it cannot tell them from pre-existing, deliberate ones - restore-not-strip).
  Offer the same-folder resume, or the restore by hand (remove the exclusion group from
  exactly the `policiesTouched` policies, and the rest) if the user wants it back now - both
  as Flow 6 "Tenant left prepared" describes.
- **File missing** (run predates it, or wrong output folder) -> fall back to the
  transcript's `[CLEANUP] TENANT STATE (post-run):` lines; if neither exists, say the
  tenant state could not be determined - do not guess and do not skip the section. (A run
  that stopped before tenant preparation - `no-admin-session`, `admin-session:`, a `config-`
  reason or `no-demo-users` - has neither and changed nothing: say exactly that.)

**Always end the summary with a "Deliverables" section that hands over the AoP runbook two
ways** (this step is MANDATORY, not optional - a prior run skipped it and handed the user
dead links):
1. its **ABSOLUTE local path** - the `file` value `summarize_aop.js` printed (e.g.
   `<ws>/runs/<runId>/DemoIQ-Output/aop_demo_runbook_<timestamp>.html`, written out in full),
   and
2. a **served link that opens it in the user's own browser** - serve the run's output folder
   (one sync call, detached; the server auto-exits after ~2 hours):
   ```text
   pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/serve_local.ps1 -Detach -OpenBrowser -Root "<run folder>/DemoIQ-Output" -Path "<aop file name>"
   ```
   Link the printed `[SERVE-URL]` (same no-browser / private port-forward rule as
   `flow-1a-configure.md` 1b step 2; re-probe it before re-sharing after any wait, 1b step 6).
   Never hand out a `file://` link and never open the page with browser-automation tools.

For example:
```text
**Deliverables:** [AoP runbook](http://127.0.0.1:<port>/aop_demo_runbook_20260630_100857.html) - local file: <absolute path from summarize_aop.js>
```
Keep the run folder as the working home - nothing is copied by default.

**The AoP runbook is the ONLY end-of-run deliverable.** It already opens
with the Demo Tenant Content Summary and carries the full inventory of what was produced, who
owns it, and where it lives in the tenant - everything the user needs, in one file. Do NOT add
links for the meeting audio MP3, the meeting transcript, or the content preview. Instead,
MENTION the audio/transcript in one prose line (they stay in the run folder), e.g.:
*"The run folder also holds the meeting audio MP3 and its text transcript - say the word if you
want their paths, or pick `🎙️ Generate / stream meeting audio` from the menu to stream the MP3
into a live meeting."* Only when the user explicitly asks, give that file's absolute path.

**Copies only on request.** Only when the user asks for copies of the deliverables in a
folder THEY name, run the staging helper:
```text
pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/stage_deliverables.ps1 -SourceDir "<run folder>/DemoIQ-Output" -WorkspacePath "<the folder the user named>"
```
By default it copies ONLY the newest `aop_demo_*.html` into
`<that folder>/DemoIQ-Deliverables/<runstamp>/`; add `-IncludeAudio`, `-IncludeTranscript`
or `-IncludePreview` only for files the user asked for. It prints `{ ok, runStamp, dest,
destUrl, files:[{ kind, name, path, url }], errors[] }` - report `dest` and each copied
file's `path`. It REFUSES a destination inside a Git work tree (seeded tenant content must
never be committed - e.g. into this repository); pass `-AllowGitWorkTree` only if the user
explicitly insists after you have said why.

> **Do NOT link the content preview at the end of the run.** `content_preview.html` belongs to
> the pre-seed approval gate (1d step 5b) - it is shown ONCE, before seeding, and its job ends
> when the user approves. After a successful seed, the **AoP runbook is the single record** of
> what was produced, who owns it, and where it lives in the tenant; an end-of-run preview link
> reads as a second, competing summary of the same content.

> **The transcript still exists even when audio was skipped - say so.** The meeting transcript
> text is authored and saved on EVERY meetings run - with or without meeting audio (text to
> speech on the platform's Foundry account gates only the MP3 synthesis). When audio was
> skipped, the prose mention above becomes: *"meeting audio MP3 skipped (<the one-line cause
> from the check below>) - the transcript text was still generated and sits in the run
> folder"*. Never imply the transcript was lost with the audio. (#13)

> **When meetings/audio WAS selected but no MP3 landed, diagnose and say WHY - never leave it
> silent (the MP3 ladder).** A silent, unsurfaced MP3 miss can cost the whole session. Text to
> speech runs on the platform's Foundry account (the config's `speechResourceId` - the
> deployment's `speech_resource_id`) and authenticates with a live **Azure CLI (`az`) Entra
> token** (`az account get-access-token --resource https://cognitiveservices.azure.com`); there
> is NO stored Speech key anywhere in this system, so a cached wrong-tenant `az` session is the
> most common cause. Check, in order:
> 1. **The az context** - Check C (`flow-1a-configure.md` 1c.5):
>    `pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/preflight_azure_cli.ps1 -ConfigPath "<config>"`.
>    On `wrong-tenant`, `not-logged-in` or `no-cli`, surface its `fixCommands` (the USER runs
>    `az logout` / `az login --tenant <id>` in their own terminal; only a
>    `cachedMatch`/`wrong-subscription` `az account set` may be run for them), re-check until
>    `ok`, then offer the audio-only top-up: resume this run folder ("Recover a partial or
>    failed run" in 1h) - its ledger skips the meetings that landed and the audio is
>    re-attempted.
> 2. **The Speech resource** - `[AUDIO] SKIP: No Speech Resource ID found` in the run log means
>    the seeder was generated from a config without `speechResourceId` (generation bakes it
>    in): `repoint_config.ps1 -ConfigPath "<config>"` sets the deployment's Foundry account,
>    then regenerate this run folder's seeder (the 1d step 6 command, with
>    `--pasted "<run folder>/demoiq-pasted.json"` and `--out-dir "<run folder>"`) before
>    resuming. Check C's `note` flags a config naming another account than the deployment's
>    (the same repoint and regenerate). Its `warning` - or `status.ps1`'s `deployment.audio`
>    with `status` `unavailable` - relays the runner's own text-to-speech check: an
>    administrator reruns `deploy.py up --with-demoiq --through demoiq` to re-check it. Never
>    create or provision a Speech resource from this skill.
> 3. **The run log's `[AUDIO]` lines** - HTTP 401/403 with the RIGHT az tenant means the
>    signed-in az account lacks data-plane access on the platform's Foundry account (the
>    deployment gives the deploying user Foundry User, which can take a few minutes to apply;
>    any other az account gets that access from the platform's owners, never from this skill).
> 4. Only with none of those signals call it an engine failure and show the failing lines.
>
> Report the one-line cause plus the next action in the summary. **A missing MP3 is never
> grounds for a re-seed or a full re-run** - the remedy is always the audio-only path above
> (scope-locked recovery rule, SKILL.md "Keep the run moving").

> **In-tenant files open in the browser, not as downloads - and say WHERE they live, precisely.**
> The seeded Word/PPT/Excel files are linked inside the AoP summary and inside the seeded
> emails/chats/meetings with a `?web=1` flag so they open in Office on the web (the engine
> handles this - see #12). When you mention these files to the user:
> - **Name the location, never just "in the tenant"**: they live in the owner's OneDrive - say
>   whose, e.g. *"in `DakotaS@<tenant>`'s OneDrive"* (the owner UPN is in the AoP runbook /
>   `verify_seeded_files.js` output). Describe them as "opens in Office on the web", not a
>   download.
> - **No bare pronouns**: write "the N Office files uploaded as ..." (N = the run's expected
>   total, `verify_seeded_files.js`'s `expected`, from the config's
>   `options.wordDocs`/`pptDecks`/`excelWorkbooks` - e.g. "the six Office files" on a
>   2/2/2 run, "the three" on a classic 1/1/1 run), never "they uploaded as ..." - the
>   natural reader questions are "which they?" and "where in the tenant?". When
>   collision-safe timestamped names were used, say it in that precise shape:
>   *"the <N> Office files uploaded under timestamped names (e.g. `..._20260715_1008.docx`)
>   because base-named copies already existed in `<owner UPN>`'s OneDrive from an earlier run -
>   that is the intended collision-safe behavior, not an error."*
> The served AoP link and its local path in the Deliverables section point to a LOCAL file on
> this machine (plus the MP3/TXT paths only if the user asked for them) - separate from the
> in-tenant SharePoint/OneDrive documents.

### Close the wrap-up: the optional Work IQ check, then status

End the wrap-up of a `done` or `partial` run - after the summary and its Deliverables - by
OFFERING the optional Work IQ check in one prose sentence (an offer, not a gate), e.g.
*"Optional: I can check whether Work IQ answers from this content - signed in as one of the
demo users, with two or three of the runbook's own prompts (Flow 9). Microsoft 365 indexes new
mail, chats, meetings and files over minutes to hours, so a check later on usually says more
than one right now."* Seeded is not ready: only that check shows Work IQ answering from the
content, and only for the prompts it asks. Run it only when the user takes up the offer, by
reading `references/flow-9-workiq-check.md` in full.

Then rerun `status.ps1` and say its `summary` in one line (`seeded` or `seeded-partial` after
this stage - never "ready"; `seed-failed` or `tenant-prepared` after a failed run):
```text
pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/status.ps1
```

---

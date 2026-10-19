# DemoIQ for GitHub Copilot - Flow 6: Clean up seeded content (tenant)

> Part of the **demoiq-seed** skill (DemoIQ adapted for GitHub Copilot). Read this file FULLY
> before executing the flow - do not run it from memory. The skill folder is
> `.github/skills/demoiq-seed` (relative to the repository root, the normal working
> directory; elsewhere use the skill's base directory) and `<ws>` (the single canonical
> output home) is `~/.copilot/DemoIQ-Output-Scout` - see SKILL.md "Paths and commands".
> Commands are single lines that run unchanged in bash, zsh and PowerShell: pass the
> absolute paths the scripts print, double-quoted. SKILL.md's hard rules, gates and "Keep
> the run moving" rules apply throughout. Gate 0 (SKILL.md Step 0) has already confirmed the
> tenant.

## Flow 6 - Clean up seeded content (remove demo data from the tenant)

Remove the demo content DemoIQ seeded - the **last run** or **all runs**. The plan comes from
the seeder's own manifests (`cleanup.config` in each seed run folder, plus the
administrator's OneDrive copy), with a fallback to the run logs, so it deletes what was
seeded and nothing else. This flow is **destructive**: always preview first and get explicit
confirmation before deleting.

**How it deletes.** There is no app-only access, so cleanup is a **cleanup run** of a
generated seeder, the same machinery as seeding: the administrator signs in for the run,
the run relaxes MFA, Conditional Access and Security Defaults for the five demo users exactly
as a seed does, deletes with delegated tokens - the administrator's for the Teams team and
the chats, each demo user's own for their meetings, mail and files - and then restores the
tenant. One DemoIQ run at a time: never while a seed runs.

What it removes, and how completely (be honest with the user):
- **The Teams team** with its channels and channel messages - the administrator deletes the
  team's group (Microsoft 365 keeps a deleted group restorable by an administrator for a
  limited time).
- **Calendar meetings** - deleted by each organizer, including the cancellation residue
  (canceled-event copies and notification emails) in attendee mailboxes.
- **OneDrive files** (Word / PowerPoint / Excel) - deleted by each owner.
- **Outlook emails** - best effort: each demo user deletes the messages matching the seeded
  subjects in their own mailbox; a few thread replies may remain.
- **Group and meeting chats** - soft-deleted by the administrator (a Teams administrator can
  restore them for a limited time). A chat that cannot be matched is REPORTED
  (`chatsNotFound` plus an `errors[]` entry), never silently skipped.
- **The seed ledger (always)** - `DemoIQ-Output/seed_ledger.json` is cleared in the run
  folders whose own runs were deleted (and only those - a newer folder that was never
  launched is left alone): it is the seeder's resume state, and after a cleanup a re-run of the same seed
  folder would otherwise skip every "already seeded" item and report DONE into an empty
  tenant.
- **Local deliverables (optional)** - with the archive choice, the targeted runs' local
  `DemoIQ-Output` (AoP runbook, preview, MP3/transcript) is zipped to `<ws>/archive/` FIRST,
  then removed; `run.json` and logs stay so run history keeps working.

A demo user who cannot sign in (wrong password, disabled, locked) leaves their own items in
place - the run reports them in `usersWithoutToken`. The team and chats need only the
administrator.

What it does not remove: the presenter copies the seeder put in the administrator's
OneDrive folder `DemoIQ-Output` (AoP runbooks, meeting MP3s and transcripts, beside the
`cleanup.config` manifest). Say so in the report; after the last cleanup the administrator
can delete that folder by hand.

### 6a. Preconditions

1. `pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/status.ps1`
   - `running`: a run is in progress - say so and stop (offer to monitor it). Cleanup never
     runs mid-seed; `launch_seeder.ps1` refuses a second run anyway.
   - `tenant-prepared`: resolve [Tenant left prepared](#tenant-left-prepared) first - a new
     run treats the old exclusions as deliberate and would not remove them.
   - `not-set-up`: SKILL.md Step 0.
2. **Pick the config** whose administrator and demo users seeded the content (the cleanup run
   signs in as those demo users). The newest seed run's `run.json` names it (`configFile`);
   when that file is in the library, use it. Otherwise list the library with
   `find_config.ps1` and ask the user to pick one with `ask_user`, exactly as Flow 1a does.
   Use the config only through helpers - never open it (SKILL.md Hard rule 4). If its
   `appMatchesDeployment` is false, offer `repoint_config.ps1` first (Flow 1a).
3. **Check the demo users' sign-ins** (read-only, Flow 1a step 1c.5 Check B):
   `pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/preflight_credentials.ps1 -ConfigPath "<config>"`
   A failed user's items would stay in the tenant; route each failure per 1c.5 before
   continuing, or tell the user which items will remain and let them choose to continue.

### 6b. Choose the scope and preview (Gate 3, part 1)

Remind the user, in the message before the question, to close any seeded Word/PowerPoint/
Excel files - including browser tabs viewing them: an open file holds a OneDrive lock that
blocks its deletion. Then ask with `ask_user`, using this exact shape (improvised phrasing
confuses users - keep it identical run to run):
> *"Clean up seeded demo content from tenant `<tenantId>`. Which scope?"*
> 1. **Last run** - remove only the most recent run's content
> 2. **All runs** - remove every tracked run's content

Then preview - a dry run that reads only this machine's manifests and logs, needs no sign-in
and deletes nothing:
```text
pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/cleanup_tenant.ps1 -ConfigPath "<config>" -Scope <LastRun|AllRuns>
```
It prints a readable preview: a per-run table plus a TOTAL line (Teams, meetings, files,
email threads, chats). Add `-Json` for the raw object (`{ ok, mode:"dry-run", scope, tenant,
manifestSource, planSource, runsTargeted, totalRunsKnown, plan{...}, byRun[], note }`) - you
need its `manifestSource` and `planSource` for 6d, so run it with `-Json` too. Show the
readable preview to the user. Say plainly what the preview cannot see: the cleanup run also
reads the administrator's OneDrive copy of the manifest, which can add runs seeded with this
administrator from another machine (All runs); the run's own result names exactly what it
targeted. When `runsTargeted` is 0, say there is nothing to clean up and return to the menu.

### 6c. Confirm (Gate 3, part 2)

In the message before the question, restate what the run does to the tenant: the
administrator signs in for the run; MFA, Conditional Access and Security Defaults are relaxed
for the five demo users while it runs and restored at the end; deleted content cannot be
brought back by DemoIQ. Then ask with `ask_user`, canonical wording:
> *"Delete this content from tenant `<tenantId>`? This cannot be undone."*
> 1. **Delete** - remove the previewed content
> 2. **Delete + archive local copies** - same, PLUS back up the targeted runs' local
>    deliverables (AoP runbook, preview, MP3/transcript) to `<ws>/archive/` and remove them
>    from the run folders (backup taken FIRST, run history stays intact)
> 3. **Cancel**

Only on an explicit choice 1 or 2, continue - without further questions - to 6d.

### 6d. Sign in, generate the cleanup seeder, launch

1. **Administrator sign-in.** Check for a usable stored sign-in:
   `pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/admin_signin.ps1 -Status`
   If none is stored for the config's administrator, sign in exactly as SKILL.md "The
   administrator's sign-in" describes (async mode; the ACTION NEEDED line names the code
   FILE, never the code).
2. **A cleanup run folder:**
   `pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/new_run.ps1 -Kind cleanup -ScenarioTitle "Cleanup <LastRun|AllRuns>" -Tenant "<tenantId>" -ConfigPath "<config>"`
   It returns `{ ok, runId, runFolder, runJson }`; the folder name ends in `-cleanup`, so
   later previews and cleanups never mistake it for a seed run.
3. **The seeder for it** (from the config; no content is authored - a cleanup run skips the
   content steps). Write `{}` to `<scratch>/cleanup-prefs.json` with your file tool, then:
   `node .github/skills/demoiq-seed/scripts/generate.js --html .github/skills/demoiq-seed/engine/DemoIQ.html --prefs "<scratch>/cleanup-prefs.json" --from-config "<config>" --emit ps1 --out-dir "<run folder>"`
   Exit 10 means the engine drifted from this adaptation's patch: stop and report it (SKILL.md
   Hard rule 9); never hand-edit a seeder.
4. **Launch** (a sync call; it returns in seconds):
   `pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/launch_seeder.ps1 -SeederScript "<run folder>/DemoIQ.ps1" -ConfigFile "<config>" -Cleanup -Scope <LastRun|AllRuns>`
   - For **Last run**, when the preview's `planSource` is `manifest` and its
     `manifestSource` is a local file, add `-ManifestPath "<manifestSource>"` so the run
     deletes exactly the previewed run.
   - For choice 2, add `-ArchiveLocalDeliverables`.
   It prints `{ ok, kind:"cleanup", launchedAt, processId, runFolder, monitorCommand }`. On
   exit 2 it names the problem and the next action (no sign-in, a sign-in for another
   account, another run active, a seeder without the patch marker): act on it and launch
   again.

### 6e. Monitor

Run the printed `monitorCommand` as a sync call with the tool's wait above 60 seconds, one
heartbeat line per call (SKILL.md "Keep the run moving"), until it reports a terminal status.
The phases are tenant preparation, "Deleting the seeded content" and the restore. A cleanup
typically takes a few minutes; Teams chat deletion is paced at about one per second.

### 6f. Report - from the files only

Read `<run folder>/DemoIQ-Output/cleanup_result.json` and
`<run folder>/DemoIQ-Output/tenant_state.json` (small JSON files without secrets; the `view`
tool is fine). `cleanup_result.json` is `{ ok, scope, manifestSource, planSource,
runsTargeted, plan{...}, results{ groupsDeleted, groupsFailed, meetingsDeleted,
meetingsFailed, meetingResidueRemoved, meetingEmailsRemoved, filesDeleted, filesFailed,
emailsDeleted, emailsFailed, notificationEmailsDeleted, chatsDeleted, chatsFailed,
meetingChatsDeleted, chatsNotFound, usersWithoutToken[], errors[] }, manifestPruned,
localState{ ledgersCleared[], localArchives[], notFoundLocally[], errors[] }, failedTotal,
tenantRestored, error }`.

Report business-level, in this order:
1. **What was removed**, per kind, and which runs (`runsTargeted`, `manifestSource`).
2. **What was not removed** - every `*Failed` count, `chatsNotFound` (name each chat from
   `errors[]` and ask the user to check or hide it in Teams), and every user in
   `usersWithoutToken` (their meetings, mail and files remain: fix the sign-in per Flow 1a
   1c.5, then run the same cleanup again). **Never call the cleanup complete while
   `failedTotal` is above 0.** Running the same scope again is safe: already-deleted items
   count as deleted.
3. **Tenant state** - the same block and honesty rules as Flow 1h's tenant-state report,
   filled only from `tenant_state.json` (MFA reinstated or not, the policies the run
   touched, pre-existing exclusions left in place, the kept exclusion group).
4. **Local state** - `manifestPruned` (the deleted runs were removed from the manifests, so
   history and later cleanups stay accurate), `localState.ledgersCleared` and, for choice 2,
   each archive in `localState.localArchives`. `localState.notFoundLocally` names deleted
   runs that no run folder on this machine holds (for example runs seeded from another
   machine, known only from the administrator's OneDrive copy): nothing local was cleared
   for them, which is expected.

When `ok` is false, the run failed before or during deletion: say `error` as written, then
report the tenant state - a failed cleanup still runs its restore. Then rerun `status.ps1`
and say its `summary`.

> **Email counts fan out across mailboxes - a high "deleted" number is expected.** Each
> seeded email exists as a separate copy in the sender's Sent Items and every recipient's
> Inbox, and each demo user deletes their own copies. A 25-email seed can report about 100
> emails deleted - that is correct, not a double delete.

## Tenant left prepared

`status.ps1` reports `tenant-prepared` when a seed or cleanup run left tenant changes in
place. Either it stopped before its restore step - its `tenant_state.json` still says
`phase:"prepared"`, so the demo users may still be excluded from the Conditional Access
policies in `caExclusion.policiesTouched`, without per-user MFA, and with Security Defaults
off when `securityDefaults.wasEnabled` is true - or its restore step ran but could not undo
everything (`phase:"restored"` with `restored:false`): then exactly the entries in
`caExclusion.failures`, `perUserMfa.failures` and `securityDefaults.error` remain.
A new run in another folder deliberately does not undo them: it cannot tell them from
deliberate configuration (restore, never strip). Show the user the facts from that file,
then offer:

1. **Resume the same run folder (recommended).** A run started again from the same folder
   inherits what is still changed from its `tenant_state.json` (`caExclusion.policiesTouched`
   and the users in `perUserMfa.users` not marked `restored`) and restores them at its end.
   Sign in (6d step 1), then launch the SAME seeder with the same config: for a seed run as
   in Flow 1h (its seed ledger resumes the remaining content instead of duplicating it), for
   a cleanup run with the same `-Cleanup -Scope` (and `-ManifestPath`) as before.
2. **Restore by hand** in the Microsoft Entra admin center, exactly from the file: remove the
   `Demo-Seeder-MFA-Exclusion` group from the exclusions of each policy in
   `caExclusion.policiesTouched` only - never from `caExclusion.preExistingExclusions`;
   set per-user MFA back to its recorded `state` (Entra admin center > Users > Per-user
   MFA) for each user in `perUserMfa.users` not marked `restored` - a file from an older
   version holds only a count, and then the run log's `[MFA] OK: Disabled per-user MFA for
   <upn> (was: <state>...)` lines name them; and turn
   Security Defaults back on when `securityDefaults.wasEnabled` is true. For an incomplete
   restore, only what its failure entries name is left to do. Once the user
   confirms it is done, record it (the file itself stays untouched as evidence):
   `pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/status.ps1 -MarkRestoredByHand "<run folder>"`
   Never record it on the user's behalf before they confirm the restore is complete.

---

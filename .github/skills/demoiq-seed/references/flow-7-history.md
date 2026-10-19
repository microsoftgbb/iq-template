# DemoIQ for GitHub Copilot - Flow 7: Show past runs

> Part of the **demoiq-seed** skill (DemoIQ adapted for GitHub Copilot). Read this file FULLY
> before executing the flow - do not run it from memory. The skill folder is
> `.github/skills/demoiq-seed` (relative to the repository root, the normal working
> directory; elsewhere use the skill's base directory) and `<ws>` (the single canonical
> output home) is `~/.copilot/DemoIQ-Output-Scout` - see SKILL.md "Paths and commands".
> Commands are single lines that run unchanged in bash, zsh and PowerShell: pass the
> absolute paths the scripts print, double-quoted. SKILL.md's hard rules, gates and "Keep
> the run moving" rules apply throughout.

## Flow 7 - Show past runs (menu choice `📜 Show past runs`; also on request)

List previous DemoIQ runs - **seed runs and cleanup runs** - so the user can see, at a
glance, **when** each run happened, **which tenant** it targeted, **what content** it seeded
(or which scope it cleaned up), and **whether it succeeded**. It reads only existing local
files: no tenant call, no sign-in and no changes, so it needs no Gate 0. It scans BOTH layouts:
the per-run folders (`<ws>/runs/<runId>/` - each holds its own `run.json` manifest,
`logs/`, and `DemoIQ-Output/`) and the legacy flat `<ws>/logs/` from runs made before
the per-run layout existed.

### 7a. Read the history
```text
pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/run_history.ps1
```
By default it scans `~/.copilot/DemoIQ-Output-Scout` (both `runs/*/logs` and the legacy
`logs/`). Pass `-LogsFolder "<folder>"`
for a different legacy location (its parent is treated as the workspace root), `-Mp3SearchRoot
"<folder>"` to point MP3 discovery elsewhere, and
`-Limit <n>` to cap the list (default 50). It prints `{ ok, logsFolder, count, runs[] }`, newest
first (`ok:false` with an `error` when there is no history at all - say there are no past
runs yet). Each run carries `kind` (`seed`, or `cleanup` for a cleanup run), `when`, `runId` /
`scenario` / `runFolder` / `configFile` (from the run's `run.json` manifest; null for legacy
flat-layout runs), `tenant`, `tenantId`, `admin`, `language`, `mode`, `elapsed`,
`outcome` (Succeeded / Partial / Incomplete / Failed), `contentDeclared[]`, `seeded` counts (emails,
meetings, word, ppt, excel, channels, audio, aop), `meetingSubjects[]`, `log` (the transcript
file name), and - for streaming -
`streamableMp3` (local path to this run's meeting-audio MP3, or null) and `streamableMeeting`
(the first meeting's subject, whose audio can be streamed into a live Teams meeting via Flow 3 /
RTMP-In). Cleanup runs also carry `cleanupScope` (`LastRun` or `AllRuns`) and `itemsNotRemoved`
(items the cleanup could not delete); both come from the run's
`DemoIQ-Output/cleanup_result.json` and are null when that file is missing. A cleanup run's
`outcome` comes from that file too - Succeeded (everything in scope removed), Partial (it
finished with `itemsNotRemoved` above 0), Failed (it failed), Incomplete (no result yet: it is
still running or stopped early) - and its `seeded` counts are zero.

### 7b. Render it for the user
Show a tidy table, **grouped by tenant**, newest first - e.g. When | Scenario | Tenant |
Outcome | Content seeded | Meetings (audio) | Duration. Lead with `scenario` when the manifest
carries it (it names the run far better than the tenant does); fall back to the log-derived
fields for legacy runs. Summarise the `seeded` counts in the Content column; in the
Meetings column list `meetingSubjects[]` and mark the `streamableMeeting` with a 🎙️ when
`streamableMp3` is set (its audio can be streamed into a live meeting). `outcome=Succeeded` means
the run reached its summary; `outcome=Partial` means it finished but some requested items could
not be seeded (the transcript's Content completion table has the breakdown - say so, do not
present it as fully complete); the per-artifact counts show what actually landed. Keep it
business-level - do not show log internals unless the user asks.

Render **cleanup runs** (`kind: "cleanup"`) as their own rows, distinct from seed runs: in the
Scenario column write "Cleanup - `<cleanupScope>`" plus, for a Partial one, "- `<itemsNotRemoved>`
items not removed" (e.g. "Cleanup - LastRun - 2 items not removed"; just "Cleanup" when
`cleanupScope` is null); leave the content and meetings cells empty instead of showing zero
counts. A Partial cleanup left items in the tenant (Flow 6 again with the same scope retries
them); never present it as complete.

History reads the transcripts only, so keep two limits in mind and say them when they matter:
it does not show whether a run restored the tenant's MFA and Conditional Access settings
(`status.ps1` does - its `preparedRuns[]`, Flow 8), and it lists only runs that wrote a
transcript - a launch that stopped before the seeder started (for example without an
administrator sign-in) leaves no row. A resumed run folder shows one row per attempt, with the
same `runId`; an Incomplete row may be a run still in progress (`status.ps1` reports
`running`).

---

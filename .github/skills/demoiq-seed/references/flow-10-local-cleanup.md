# DemoIQ for GitHub Copilot - Flow 10: Clean up local footprint (this machine)

> Part of the **demoiq-seed** skill (DemoIQ adapted for GitHub Copilot). Read this file FULLY
> before executing the flow - do not run it from memory. The skill folder is
> `.github/skills/demoiq-seed` (relative to the repository root, the normal working
> directory; elsewhere use the skill's base directory) and `<ws>` (the single canonical
> output home) is `~/.copilot/DemoIQ-Output-Scout` - see SKILL.md "Paths and commands".
> Commands are single lines that run unchanged in bash, zsh and PowerShell: pass the
> absolute paths the scripts print, double-quoted. SKILL.md's hard rules, gates and "Keep
> the run moving" rules apply throughout.

## Flow 10 - Clean up local footprint (remove DemoIQ from THIS machine)

The local-machine counterpart to Flow 6 (which removes seeded content from the **tenant**).
Flow 10 removes what DemoIQ wrote to **this computer**; it never contacts a tenant, so it
needs no Gate 0. It is driven by `scripts/cleanup_local.ps1` and has **two depths**:

1. **Runs** - the per-run `runs/` folders (seeders, outputs, logs, AoP runbooks, meeting
   audio and transcripts), the `archive/` zips from Flow 6's archive option, `scratch/`,
   the local server state (`serve/`), legacy loose files from older layouts, and the
   session store: the administrator's stored sign-in, a pending sign-in code file and the
   session key (`.session/`). **Keeps** the config library (`configs/`) for future runs.
2. **Full local** - everything in (1) **plus** the config library (`configs/`, which holds
   the demo users' passwords), then the workspace folder itself once it is empty.

What Flow 10 never removes:

- **The DemoIQ app.** It belongs to this deployment's Terraform state. `-Depth 3` is
  refused with that pointer: clean up the tenant content first (Flow 6), then set
  `demoiq_enabled = false` in `infra/terraform.tfvars` and rerun the deploy-platform runner
  (or destroy the platform) - with that runner's own plan approval.
- **Files from other tools** sharing `<ws>`: an upstream DemoIQ-Scout or older
  certificate-based setup may have left `app_registration.json`, `speech_resource.json`,
  `certs/` or `telemetry/`. They are reported under `foreign[]` and left in place; mention
  them, and say they belong to that other tool (remove them with it, or by hand once
  nothing uses them).

**Clean up the tenant first.** A run folder holds that run's `cleanup.config` manifest, which
Flow 6 uses to find what to delete. Before executing either depth, check whether any seed
run's content is still in the tenant (`status.ps1`: a `lastRun` that is a seed run, or
`preparedRuns[]`), and if so say that removing the run folders loses the local manifests -
afterwards Flow 6 can only fall back to the administrator's OneDrive copy, which covers the
most recent run only - so run Flow 6 first if the user wants the tenant clean.

**Always inventory first and require an explicit confirm.** Dry-run/inventory is the
DEFAULT - nothing is deleted unless `-Execute` AND `-Confirm <token>` are both passed (the
token is `CLEANUP-<depth>`, printed in every dry-run). Always show the inventory, then
confirm with `ask_user`: *"Remove these DemoIQ files from this machine?"* with the choices
`Yes - remove them` / `No - keep everything`.

**On Windows, before executing, remind the user to close seeded Office files** - one line in
the confirm context: *"Close any generated Word/PowerPoint/Excel files - and browser tabs
viewing them - first; an open file can't be deleted."* On macOS/Linux an open file never
blocks deletion, so the reminder is not needed there.

Inventory (dry-run - nothing deleted):
```text
pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/cleanup_local.ps1 -Depth <1|2>
```
Execute (only after the explicit confirm):
```text
pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/cleanup_local.ps1 -Depth <1|2> -Execute -Confirm CLEANUP-<depth>
```
(Both default to the canonical `<ws>`; pass `-OutputFolder "<folder>"` only for a
non-default workspace.)

It prints `{ ok, depth, mode, confirmToken, outputFolder, inventory[], foreign[], removed[],
kept[], lockedFiles[], errors[] }` and exits **0 when clean (or a dry-run); 3 = completed
but PARTIAL** (something survived); 2 = depth 3 refused. Surface the inventory, `kept[]`
(depth 1 keeps the config library), `foreign[]` and any `errors[]` plainly.

**On a partial result (exit 3 / `lockedFiles[]` non-empty): report it honestly, per file.**
`lockedFiles[]` (a Windows-only outcome) names exactly which files survived because they are
OPEN (probed with a real lock test) - everything else was still removed. Tell the user:
*"Everything was removed except N file(s) still open: `<the paths>`. Close them (Office
windows, browser tabs viewing them) and say 'run it again' - the re-run finishes the job."*
Then re-run the SAME execute command on their go-ahead (no new inventory gate for the
immediate re-run of an already-confirmed cleanup). **Never report a cleanup as complete
while `lockedFiles[]` or `errors[]` is non-empty.**

---

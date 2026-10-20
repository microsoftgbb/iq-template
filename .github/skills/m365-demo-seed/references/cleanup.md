# Cleaning up

Two different cleanups, both explicit and confirmed by the user:

- **Tenant cleanup** deletes the content a seed run created in Microsoft 365.
- **Local cleanup** deletes this skill's files on this computer. It never touches the tenant
  or the demo app.

Do the tenant cleanup first: a seed run's folder holds the manifest that tenant cleanup works
from, so removing it locally loses that record.

## Tenant cleanup

Cleanup works only from the run's own manifest (the recorded ids of every item it created). It
never deletes by searching the tenant, except for best-effort sweeps of notification mail (see
below). It needs the same attended session as a seed: Gate 0, the stored demo-user passwords and
the administrator's sign-in. It relaxes and restores the tenant exactly as a seed run does,
because the demo users must sign in to delete their own items.

| Content | Deleted by | How |
| --- | --- | --- |
| Team, channels and posts | Administrator | The team's Microsoft 365 group is deleted (restorable for 30 days) |
| Group chats, meeting chats | Administrator | Chat deletion: a soft delete that an administrator can undo; one per second |
| One-on-one chats | Each sender | Only the seeded messages; the chat stays, because Teams keeps one chat per pair and it may hold earlier messages |
| Meetings | Organizer, then attendees | The organizer deletes the event (attendees get a cancellation), then attendees remove their cancelled copies |
| Files | Owner | Moved to the owner's OneDrive recycle bin |
| Mail | Every participant | Each thread is deleted from every participant's mailbox, by conversation |
| Notifications | Recipients | Best effort: meeting invitations, cancellations and file share notices matching the seeded subject or file name in the recipients' 300 most recent messages |

**Re-seeded demos.** Seeding the same demo again overwrites its files in place. A file that
another seed run of the same tenant also recorded, and that run is not cleaned up yet, is
**kept** and left to that run. Notices that would match it are kept too. The plan shows these
as `keep` with `keptFor`.

### Steps

1. Run `status`, then `history` to find the seed run. A run must not be active, and no run
   may need a restore (`needs-restore`): restore first.
2. Plan it (local, no tenant calls):
   `python3 .github/skills/m365-demo-seed/scripts/m365demo.py cleanup --run RUN_ID`.
   Summarize `summary`, the per-kind `counts` and any `keep` rows.
3. Gate 0 applies if not already passed this session. Then **Gate 2 - confirm the cleanup**.
   Ask with `ask_user`: *"Delete this content from tenant TENANT as planned? Deleted items go
   to recycle bins or soft delete, but treat it as permanent."* with the choices
   `Yes - delete it` / `No - keep it`. Only a yes counts, for that plan's `sha256` only.
4. Start it: `cleanup --run RUN_ID --tenant TENANT --approve SHA256`.
   - The administrator signs in exactly as for a seed: relay `codeFile`, never read it.
   - If the plan changed in the meantime (another run was cleaned, or something else
     changed), the run stops with `plan-changed`. Plan and confirm again.
5. Monitor with `monitor --run CLEANUP_RUN_ID --wait 600`, as for a seed. On `finished`,
   report `result.outcome`, `result.counts` (deleted, kept, failed per kind), `result.kept`,
   `result.swept` and `result.remaining`. Confirm that `tenant.phase` is `restored`.
6. `cleaned-with-failures`: relay each `failures[].error`. A demo user who could not sign in
   keeps their items. Fix the cause, then plan again: the new plan lists only what remains.
7. Rerun `status`. Once nothing remains, the demo can be seeded again without `--again`.

## Local cleanup

Removes this skill's workspace on this computer (`~/.copilot/m365-demo-seed`). It never
contacts the tenant and needs no Gate 0.

| Depth | Removes | Keeps |
| --- | --- | --- |
| `runs` (default) | Run folders: logs, manifests, presenter guides, Work IQ checks | Demos and the stored demo-user passwords |
| `all` | Run folders, demos, stored passwords, then the workspace folder itself | Anything it does not recognize (`foreign`) |

Whatever the depth, these are always kept:

- Run folders whose tenant settings still need a restore. Restore first.
- Run folders whose seeded content is still in the tenant, unless `--include-uncleaned`. Clean
  the tenant up first, or the user explicitly gives up that cleanup.

The demo app is not part of local cleanup: it belongs to the deployment's Terraform state. To
remove it, clean up the tenant content, set `m365_demo_enabled = false` in
`infra/terraform.tfvars` and rerun the deploy-platform runner, with its own plan approval.

### Steps

1. Inventory: `local-cleanup --depth runs` (or `all`). It removes nothing. Show `remove`,
   `kept` with its reasons, and `foreign`.
2. Ask with `ask_user`: *"Remove these files from this computer?"* with the choices
   `Yes - remove them` / `No - keep everything`. On Windows, first ask the user to close any
   open presenter guides or previews, because Windows cannot remove an open file.
3. Execute: the same command with `--confirm` and the inventory's `confirmToken` (plus
   `--include-uncleaned` only if the user agreed to give up those cleanups).
4. Exit 3 (`partial`) lists `locked` files and `errors`. Everything else was removed. Ask the
   user to close those files, then rerun the same command. Never report a partial cleanup as
   complete.

# DemoIQ for GitHub Copilot - Flow 8: Check DemoIQ status

> Part of the **demoiq-seed** skill (DemoIQ adapted for GitHub Copilot). Read this file FULLY
> before executing the flow - do not run it from memory. The skill folder is
> `.github/skills/demoiq-seed` (relative to the repository root, the normal working
> directory; elsewhere use the skill's base directory) and `<ws>` (the single canonical
> output home) is `~/.copilot/DemoIQ-Output-Scout` - see SKILL.md "Paths and commands".
> Commands are single lines that run unchanged in bash, zsh and PowerShell: pass the
> absolute paths the scripts print, double-quoted. SKILL.md's hard rules, gates and "Keep
> the run moving" rules apply throughout.

## Flow 8 - Check DemoIQ status ("am I set up?", "where are we?")

A run-anytime, read-only answer to "where does DemoIQ stand for this deployment?". It
makes no tenant calls, needs no sign-in and changes nothing, so it needs no Gate 0.

```text
pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/status.ps1
```

It prints one JSON line: `{ ok, state, summary, attention[], next[], skillVersion,
deployment, deploymentError, configs{ count, matching, ready, drafts, encrypted, newestReady,
newestMatching },
signIn{ stored, upn, signedInAt, matchesDeployment }, activeRun, lastRun, lastSeedRun,
preparedRuns[], workIq }`. It never prints a token, a code or a password.

Render it as a short checklist, in this order, from the fields only - never from assumption:

1. **Deployment** - ✅ when `deployment` is set: the DemoIQ app (`clientId`), the tenant,
   consent (`granted`; anything else is ⚠️ and `attention[]` says why) and meeting audio
   (`audio`, when the runner recorded it). ❌ with `deploymentError` otherwise.
2. **Config** - ✅ when `configs.ready` > 0: a config that names this deployment's app, is
   not a draft and has five demo users with passwords (name `newestReady`'s file name, not
   its contents). ⚠️ for configs for this app that are drafts or lack passwords
   (`matching` above `ready`: complete them in the Config Builder, Flow 1a), for encrypted
   configs (convert each once, in the user's own terminal: `scripts/convert_config.ps1`)
   and for configs that name another app (offer `scripts/repoint_config.ps1`).
3. **Administrator sign-in** - "stored for `<upn>` at `<signedInAt>`" or "none stored" -
   both are normal: a sign-in is taken right before each run and consumed by it.
4. **Last run** - `lastRun.kind`, `runId`, `outcome` and, for a cleanup, `itemsNotRemoved`;
   for a seed run also `lastSeedRun` when it differs.
5. **Tenant** - ❌ for every entry in `preparedRuns[]` (a run that stopped before its
   restore step, or whose restore could not undo every MFA, Conditional Access or Security
   Defaults change - `attention[]` says which), with the fix from Flow 6 "Tenant left
   prepared". ✅ "no run
   left the tenant prepared" otherwise. A run the user restored by hand (recorded with
   `status.ps1 -MarkRestoredByHand`) shows as an `attention[]` line instead.
6. **Work IQ** - `workIq.checked` false: "not checked (optional, Flow 9)"; otherwise its
   `verdict`, `withSeededCitations` of `prompts`, `askedAs` and `checkedAt`.

Then say the `summary` and every `attention[]` line as written, and offer the first
`next[]` item. The states, first match wins:

| `state` | Meaning | Next |
| --- | --- | --- |
| `running` | A seed or cleanup run is in progress | Monitor it (Flow 1h step 3, or Flow 6) |
| `tenant-prepared` | A run left MFA / Conditional Access changes in place | Flow 6 "Tenant left prepared", before anything else |
| `not-set-up` | No DemoIQ app in this deployment | deploy-platform section 7 (`deploy.py up --with-demoiq`) |
| `needs-config` | No ready config: none for this app, or only drafts and configs without passwords | Flow 1a |
| `ready-to-seed` | Set up, with a ready config | Flow 1 |
| `cleanup-partial` / `cleanup-failed` | The newest cleanup left items or failed | Flow 6 again, same scope |
| `seed-failed` | The newest seed run failed or stopped | Fix the cause, then resume the same run folder (Flow 1h "Recover a partial or failed run") |
| `seeded-partial` | Seeded, with requested items missing | Resume the same run folder (Flow 1h "Recover a partial or failed run") |
| `seeded` | Seeded; Work IQ unchecked, not citing it yet, or not asked as a demo user | Optional Flow 9 |
| `workiq-verified` | Seeded, and Work IQ - asked as a demo user - cited seeded content | Demo with the AoP runbook |

A run folder that was created but never launched (still authoring, or cancelled at the
content preview) is ignored here: it never touched the tenant.

**Never say "ready" for seeded content.** `seeded` means the content was written;
`workiq-verified` means Work IQ cited it for the prompts that were asked, as the user that
asked - nothing more.

### Deeper checks (on request, or before a run)

- **Demo-user passwords and licenses** (Check B, signs each demo user in once, read-only):
  `pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/preflight_credentials.ps1 -ConfigPath "<config>"`
  - see Flow 1a step 1c.5 for how to read it. It needs the config's passwords, so it is
    meaningful only after the user saved the config in the Builder.
- **Meeting audio** (Check C, local Azure CLI cache only):
  `pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/preflight_azure_cli.ps1 -ConfigPath "<config>"`
- **Stored sign-in only:**
  `pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/admin_signin.ps1 -Status`

---

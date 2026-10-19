# DemoIQ for GitHub Copilot - Flow 1, stage 1a-1c.5: Story and config, run mode, pre-seed checks

> Part of the **demoiq-seed** skill (DemoIQ adapted for GitHub Copilot) - one STAGE of Flow 1
> (see `references/flow-1-build.md`, the spine, for the whole-pipeline rules: never-pause, the
> gates, security). Read this file FULLY before executing this stage - do not run it from
> memory. The skill folder is `.github/skills/demoiq-seed` (relative to the repository root,
> the normal working directory; elsewhere use the skill's base directory) and `<ws>` (the
> single canonical output home) is `~/.copilot/DemoIQ-Output-Scout` - see SKILL.md "Paths and
> commands". Commands are single lines that run unchanged in bash, zsh and PowerShell: pass
> the absolute paths the scripts print, double-quoted. SKILL.md's hard rules, gates and "Keep
> the run moving" rules apply throughout. Gate 0 (SKILL.md Step 0) has already confirmed the
> target tenant: `<tenantId>` below is that tenant, `deployment.tenantId` from this
> deployment's receipts.
> **Next stage after this one:** `references/flow-1d-author.md` (author, preview, regenerate).

This stage ends with one **ready** config in the library (it names this deployment's DemoIQ
app and holds all five demo-user passwords), a run mode, passing pre-seed checks and the
administrator's sign-in for the run. Configs hold passwords: never open one with `view`,
`cat` or a search tool - work only from the helpers' summaries.

## Step 0 - the config library and this deployment (always first)

```text
pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/find_config.ps1
```

One instant call: `<ws>/configs` is the only home for configs, and nothing is swept in from
other folders. It prints `{ ok, ws, configsHome, scratchDir, configs[], count, newConfigs[],
waitedSeconds, deployment, deploymentError }`:

- **`scratchDir`** is `<scratch>` for all of Flow 1: write the story, prefs and authored
  content there with your file-creation tool, as UTF-8 - never into the repository.
- **`deployment`** (`{ tenantId, clientId, consent, delegatedScopes, speechResourceId, audio,
  checkedAt }`, from the runner's receipts): null -> stop, SKILL.md Step 0 handles
  `not-set-up` (relay `deploymentError`); a `tenantId` other than Gate 0's -> ask Gate 0
  again. Cite the source when you state an ID ("App ID `<clientId>` from this deployment's
  receipts"). A `consent` other than `granted` is not a stop: Check B and the sign-in recheck.
  When `speechResourceId` is empty or `audio.status` is `unavailable` (relay `audio.detail`),
  say once: "no meeting audio MP3 this run - meetings and their transcript text still seed".
- **`configs[]`** (newest first) summarizes each config without its passwords: `{ path, name,
  lastWrite, encrypted, adminUpn, existingAppId, appMatchesDeployment, draft, demoUsers,
  demoUsersWithPassword, ready, contentTypes }`. Label each, first match wins:

| Summary | Label | Next |
| --- | --- | --- |
| `error` (not valid JSON, or empty) | unreadable | name it and skip it |
| `encrypted: true` | upstream DemoIQ config | the user converts it once (Saved-config path) |
| `appMatchesDeployment: false` | another app | repoint it (Saved-config path) |
| `draft: true` | draft | complete it in the Config Builder (1b) |
| `ready: false` (fewer than five demo users with passwords) | needs passwords | open it in the Config Builder (1b) |
| `ready: true` | **ready** | usable for a run |

## 1a. Gate 1 - how to configure this run

In a short message before the call, say what the library holds ("Configs in `<configsHome>`:
1 ready - `<name>` (`<adminUpn>`; `<contentTypes>`), 1 draft that needs passwords", or "none
yet"), where a drafted story would come from (this project's `scenario/` folder or, without
one, the template's North Works / South Works sample) and your recommendation. Then call
`ask_user` with *"How should we set up the config for this run?"* and, in this order:

- with a **ready** config: `📂 Use a saved config (Recommended)` /
  `📝 Draft a config from this project's story` /
  `🧩 Configure from scratch in the Config Builder` / `➡️ Continue the previous run`
- otherwise: `📝 Draft a config from this project's story (Recommended)` /
  `📂 Use a saved config` / `🧩 Configure from scratch in the Config Builder` /
  `➡️ Continue the previous run`

Omit `📂 Use a saved config` when the library is empty. Offer `➡️ Continue the previous run`
only when the latest `status.ps1` reports a `lastSeedRun` whose
`<runFolder>/DemoIQ-Output/cleanup.config` exists (check with the glob tool). Re-seeding a
run unchanged is not a choice here: it is Flow 1R, from the menu.

### Story path (draft a config from this project's story)

1. **Find the story.** In a derived project (a `scenario/` folder at the repository root; see
   [scenario authoring](../../../../docs/scenario-authoring.md)), read only what you need: the
   contracts and fictional fixtures in `scenario/data/`, the rules in `scenario/workflows/`,
   the questions in `scenario/evals/`, the titles and scope of `scenario/knowledge/` and the
   instructions in `agents/scenario-assistant/`. Summarize the organization, its sites,
   assets or other entities and their states, the current issue and the roles. Keep it
   fictional: never copy real names, customer data, credentials or tenant and deployment IDs,
   never put `scenario/knowledge/` text verbatim into seeded content, and never read secrets
   (`.env`, tfvars, Terraform state, `.artifacts/`). Without `scenario/` (the template
   itself), use the preset `sample-asset-operations`: North Works (North region) with Pump A
   Operational and Pump B in Maintenance, South Works (South region) with Conveyor A
   Operational - the fictional sample `workspace/SeedScenario.Notebook` seeds. Its story and
   content mix are in `scripts/presets/sample-asset-operations.json` (no secrets; `view` it).
2. **Draft the story** in the `customScenarioText` format into `<scratch>/story-<name>.txt`
   (`<name>`: a short slug of lowercase letters, digits and `-`, reused as `-Name`); skip this
   for the unchanged sample. File-name lines are optional; a `Company: <name>` line brands the
   content and wins over the config's company name.
   ```text
   Title: <one line>
   Description: <one paragraph: the organization, the entities and their state, the central tension, and what the team does about it across email, chats, a Teams channel and meetings; end with "Every name and status in this story is fictional.">
   Excel: <File_Name>.xlsx
   Word: <File_Name>.docx
   PowerPoint: <File_Name>.pptx
   ```
3. **Approve it.** Show the story in chat as a quote block, then call `ask_user` with *"Use
   this story for the demo content?"* and `✅ Use this story` /
   `✏️ Change it - I'll say what to change` /
   `📦 Use the template's North Works / South Works sample instead` (the third only for a
   story drafted from `scenario/`). On ✏️, apply the change (a changed sample story goes to
   `<scratch>/story-<name>.txt`), show it again and ask again.
4. **Name the people** - UPNs are not secrets; passwords never go in chat.
   - **Administrator:** when `az account show --query user.name -o tsv` prints a UPN, call
     `ask_user` with *"Which administrator signs in for each DemoIQ run?"* and
     `<that UPN> (Recommended)` / `Another administrator - I'll type the UPN`; otherwise ask
     it for a typed UPN. It must be a Global Administrator of `<tenantId>`: each run changes
     Conditional Access, per-user MFA and Security Defaults and deletes Teams chats as this
     account, which signs in with a device code and has no password anywhere.
   - **Demo users:** call `ask_user` with *"Which five existing demo users should DemoIQ use?
     Type their UPNs, separated by commas."* - five distinct accounts in the same tenant with
     Exchange Online, Teams and OneDrive (and Microsoft 365 Copilot to demo Copilot and Work
     IQ). No preset accounts, and never the administrator: demo users' MFA is relaxed during
     a run, the administrator's never is.
5. **Draft the config:**
   ```text
   pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/draft_config.ps1 -Name <name> -AdminUpn "<admin upn>" -DemoUsers "<upn1>,<upn2>,<upn3>,<upn4>,<upn5>" -StoryFile "<scratch>/story-<name>.txt"
   ```
   For the sample, pass `-Preset sample-asset-operations` (content mix included), plus
   `-StoryFile` only for a changed story, which replaces the preset's. Optional:
   `-ContentTypes` (what the story needs, for example
   `emails,chats,meetings,word,aop`; without it and without a preset, the sample's mix is
   used: emails, chats, a channel, meetings, Word, PowerPoint, Excel and the AoP runbook),
   `-Industry` (the closest of the Builder's 29; it shapes the personas),
   `-CompanyName`, `-Language`. It writes the passwordless draft
   `<ws>/configs/DemoIQ_config_<name>.json` and prints `{ ok, name, configPath, prefsPath,
   appId, story{ title, industry, companyName }, demoUsers[], passwordsNeeded, warnings[],
   builderCommand }`. Relay every `warnings[]` line. Exit 2 (`ok:false`): relay `error` and
   fix the input - a taken name (another `-Name`; `-Force` only replaces your own
   passwordless draft), not five distinct UPNs, or the administrator among them.
6. **Review it in the Builder:** stop any Builder server still running from earlier (its
   `[SERVE-PID]`), then run `builderCommand` exactly as printed (one sync call; the server
   starts detached). A server replaces only the draft it was started with, so every draft
   gets a fresh server. Follow 1b and post
   `🔐 **ACTION NEEDED** - in the Config Builder (<SERVE-URL>), check the drafted story and settings, type the five demo users' passwords in the Demo users card and click **Save config**.`
   with one more line: the page shows "Loaded the drafted config" and the green "App ID from
   your deployment" badge, and fewer content types make a shorter run. Then 1b step 5.

### Saved-config path

1. List `configs[]`, naming the folder once ("Configs in `<configsHome>`:"): file name,
   `adminUpn`, `contentTypes` and label. With several, call `ask_user` with *"Which saved
   config should this run use?"* and the file names (ready first, then newest first); never
   pick one silently. With one, name it and use it.
2. Bring it to **ready**:
   - **Upstream DemoIQ config** (encrypted; also one kept elsewhere on this machine - ask for
     its full path). Only the user converts it, in their own terminal on this host (in a
     Codespace, a terminal there):
     `🔐 **ACTION NEEDED** - in your own terminal, run pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/convert_config.ps1 -Path "<file>" and type the password that config was saved with at the masked prompt, then tell me "done".`
     It writes a plain, owner-only `<name>-plain.json` into the library, pointed at this
     deployment's app, and leaves the original for the user to delete once the copy works
     (three wrong passwords: exit 3, nothing written). Never run it yourself: it refuses
     without an interactive console (`no-console`), and that password never reaches the chat.
     After "done", rerun `find_config.ps1` and use the new copy.
   - **Another app** (`appMatchesDeployment: false`, for example after a redeployment): call
     `ask_user` with *"Point `<file name>` at this deployment's DemoIQ app?"* and
     `Yes - repoint it` / `No - choose another way`; on yes:
     ```text
     pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/repoint_config.ps1 -ConfigPath "<file>"
     ```
     It sets `existingAppId` and `speechResourceId` from the receipts (and clears two unused
     upstream fields) and prints `{ ok, configPath, changed{}, before{}, after{} }`; state
     the App ID before -> after.
   - **Needs passwords:** open it with the 1b command plus `-PrefillConfig "<file name>"` and
     post `🔐 **ACTION NEEDED** - in the Config Builder (<SERVE-URL>), add the missing demo-user UPNs and passwords and click **Save config**.`
     The save is a new file (1b step 5); continue with it.

### Builder path (configure from scratch)

Open the Builder with the 1b command (no `-PrefillConfig`) and tell the user, in one message,
what it needs: the **Admin UPN** of a Global Administrator of `<tenantId>` (no password - it
signs in with a device code when each run starts); **five existing demo users** of that
tenant, UPN and password each (no presets - every tenant provisions different users - and no
save without all five UPNs); a **scenario** - a preconfigured one (29 industries, four
scenarios each) or **Describe in your own words**: type the story to bake it in, or leave the
box blank to describe it in chat at authoring time (`describeInChat: true`, the Describe-it
path); optional company name (blank keeps the Contoso defaults) and language (48); **content
types** (fewer make a shorter run), **AoP surfaces** and agents when the AoP runbook is on
(Copilot Code and Copilot Autopilot are preview surfaces DemoIQ cannot verify in the
tenant), and **counts**, where `wordDocs`, `pptDecks` and `excelWorkbooks` set how many
separate files to seed (default 1, at most 5). Meeting audio and RTMP streaming are optional.
Never ask for or fill the page's hidden upstream engagement fields.

`🔐 **ACTION NEEDED** - build the run in the Config Builder (<SERVE-URL>) and click **Save config** under the Config summary.`

### Continuation path (the next chapter of an already-seeded run)

A natural continuation of what the previous run seeded, run as a normal Fast seed: the
seeder threads new content into existing conversations by name (an email subject continues
its thread, a group-chat topic its chat, a channel name its channel).

1. **Digest the prior run** (`lastSeedRun.runFolder` from `status.ps1`; for an older run, a
   `runFolder` from Flow 7's `run_history.ps1`):
   ```text
   node .github/skills/demoiq-seed/scripts/continuation_digest.js --dir "<prior run folder>/DemoIQ-Output" --scope last
   ```
   It prints `{ ok, today, reuse{ emailSubjects[], chatTopics[], channelNames[] },
   priorFiles[], meetings[{ subject, startDate, dayOfWeek, relative }], notes, ... }`
   (`--scope all`: every run that `cleanup.config` records). On `ok:false` there is nothing
   to continue: say so and ask Gate 1 again without this choice.
2. **Reuse the prior run's config:** `configFile` in `<prior run folder>/run.json` (a manifest
   without secrets; `view` it), brought to **ready** as in the Saved-config path if needed.
   Same administrator, demo users, industry, language and tenant.
3. **Plan it for Flow 1d.** Before Flow 1d's prompt dump, merge a `customScenarioText` for the
   next chapter (Title, Description, the new `Excel:`, `Word:` and `PowerPoint:` file names)
   into `<scratch>/demoiq-prefs.json`, as the Describe-it path does. The content then:
   - reuses `emailSubjects`, `chatTopics` and `channelNames` **verbatim** (emails as `RE:`
     replies), which threads it into the existing conversations;
   - uses **new** file names that continue `priorFiles` (for example `..._Executive_Brief.docx`
     -> `..._GoLive_Readiness_Review.docx`) and references them - nothing is overwritten;
   - dates past events correctly from `meetings[].relative` and `dayOfWeek` ("last Tuesday"
     only when it was a Tuesday) and dates new content after them;
   - advances the story (decisions since, new developments) instead of repeating it.
4. Read the plan back in one message (threads continued, new file names, key date
   references); the content preview in Flow 1d is the approval.

### Describe-it path (at authoring time, when the config says `describeInChat: true`)

Not a Gate 1 choice: it runs when Flow 1d's prompt dump reports `describeInChat: true` (the
user chose **Describe in your own words** in the Builder and left the box blank). Ask them to
describe the demo - or offer to draft it from this project's story as in the Story path -
for example *"a German banking demo where the exec team debates going live with a new
payments platform while a competitor circles; include a tense email thread and a risk review
meeting"*. Merge ONLY these keys into `<scratch>/demoiq-prefs.json` (Flow 1d step 1 starts it
as `{}`; the schema is in the header of `scripts/generate.js`); Flow 1d then dumps again:

- `customScenarioText` - Title, Description and optional Excel/Word/PowerPoint file names;
- `companyName` - only when the user names one (a `Company:` line in the story wins);
- `demoLanguage` - only when the description states or clearly implies a language.

Everything else - content types, counts (file counts included), AoP surfaces and agents,
industry, the five demo users - comes from the config, which the seeder reads at run time;
copies in prefs would only make the authored content diverge from what seeds.

> **Honor exact content the user describes - within the config's content types.** Named
> documents, decks, workbooks, channels, threads or meetings are authoritative: author exactly
> those, with those names. When the description needs a content type or counts the config
> does not have, say plainly that it would not seed and offer to re-save the config in the
> Builder (1b, `-PrefillConfig`) before authoring - never add it to prefs. Selected types the
> description skips still get sensible content. Read the plan back before authoring; the
> content preview is the approval.

## 1b. Serve the Config Builder and pick up the saved config

The Builder is a local page the user fills in in their own browser, opened only through the
serve helper:

```text
pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/serve_local.ps1 -Detach -AllowSave -OpenBrowser -Root ".github/skills/demoiq-seed" -Path "DemoIQ-Config-Builder.html" -PrefillAppId "<deployment.clientId>" -PrefillSpeechId "<deployment.speechResourceId>"
```

Omit `-PrefillSpeechId` when the receipts name no Speech resource; add
`-PrefillConfig "<file name>"` to open a library config prefilled (the Story path's
`builderCommand` already does). The helper builds the query itself - never hand-build an
`?appId=` URL - and no UPN or password ever goes in a URL.

1. **Start it detached** (one sync call that returns at once). It listens on loopback only,
   survives session checkpoints, exits after about 2 hours and prints `[SERVE] <base URL>`,
   `[SERVE-URL] <page URL>`, `[SERVE-PID]`, `[SERVE-LOG]` and, when the user's default browser
   opened it, `[SERVE-OPENED]`. Never drive the page with browser automation, improvise
   another server or pass a `file://` URL (it loses the query and cannot save).
2. **Share the `[SERVE-URL]` in chat exactly as printed**, as the clickable fallback. Its
   `#fragment` carries the server's save token (fragments never leave the browser): never
   strip or rebuild it, never post it anywhere public. When the helper printed
   `[SERVE] WARN: this host has no browser` instead of `[SERVE-OPENED]` (a Codespace or SSH
   host), the user first forwards the port **privately** - VS Code's automatic forwarding, or
   `gh codespace ports forward <port>:<port> --codespace "<codespace name>"` on their own
   machine. Never make a port public.
3. **Check the prefill through the user**: the App registration card shows the green **App
   ID from your deployment** badge. The App ID is read-only, so when it is empty or an orange
   invalid-value notice appears, re-mint: stop the old server (`[SERVE-PID]`, freeing the
   stable port 53713), rerun the same command, probe it (step 6) and share the new link.
4. **Save config writes straight into the library** - plain, owner-only, no download, never
   overwriting (a name clash gets a `_1` suffix) - and the page confirms "Saved `<name>`". A
   **Not saved** notice means the server stopped or restarted: re-mint as in step 3; in the
   new page the user clicks **Restore that draft** (a drafted config reloads from the link
   itself), re-enters the demo-user passwords (drafts never keep them) and saves again.
5. **Watch for the save** while the user works - a sync call with the tool's wait above 90
   seconds (for example 120), which returns as soon as a config is saved:
   ```text
   pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/find_config.ps1 -WaitSeconds 90
   ```
   Between slices, post one line ("Waiting for Save config - nothing saved yet, about 4
   minutes") and start the next slice in the same turn. The save is in `newConfigs[]` or, if
   it landed between slices, the newest `configs[]` entry with a `lastWrite` after your
   hand-off: a new file named after the scenario and language (spaces and parentheses - keep
   it double-quoted). Saving a draft replaces it: the server deletes the draft it opened
   once the completed config is written, and the page says so. If the page instead says the
   draft could not be removed, it stays in the library marked as a draft and never counts as
   ready - mention it, nothing else to do. Use the saved config only when it is **ready**;
   otherwise say what is missing and re-open it with `-PrefillConfig`. Several new configs
   (the user saved twice): ask which one. After about 15
   minutes without a save, probe (step 6) and call `ask_user` with *"Still working in the
   Config Builder?"* and `⏳ Still working - keep waiting` / `🔗 Send me a fresh link` /
   `🛑 Stop for now`.
6. **Probe before re-sharing after any wait** (a pause, a session checkpoint, the user
   returning later) - a dead link reads as a broken tool:
   ```text
   pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/serve_local.ps1 -Probe "<the [SERVE] base URL>"
   ```
   Exit 0: alive - share the same `[SERVE-URL]`. Exit 4: dead - rerun step 1 and share the
   new URL; never re-show a link you have not just probed or printed (the same rule covers
   the content preview in Flow 1d). A later session finds a running server in
   `<ws>/serve/<port>.json`; probe it before reusing it.

Once the config is captured, stop the server with its `[SERVE-PID]` (`kill <pid>` on macOS
and Linux, `Stop-Process -Id <pid>` on Windows) so the next one gets port 53713 again, where
the Builder keeps its in-browser draft.

## 1c. Run mode

The seeder asks one question, answered by `launch_seeder.ps1 -RunMode`: **Fast** (`1`, the
default; about 10-15 minutes by the seeder's estimate) creates fresh content; **Full** (`2`,
about 15-20 minutes) also loads the previous run's history - the `cleanup.config` it left in
the administrator's OneDrive - as context. An unattended run cannot choose again when that
history is missing, so:

- with no earlier seed run (no `lastSeedRun` with `outcome` `done` or `partial` in
  `status.ps1`), and on the Continuation path, use Fast and say so in one line;
- otherwise call `ask_user` with *"Which run mode should the seeder use?"* and
  `⚡ Fast - fresh content (Recommended)` / `🧵 Full - also load the previous run's history`.

Carry the answer to Flow 1h; never ask it again there.

## 1c.5. Pre-seed checks and the administrator's sign-in

Prove the run can work before authoring. Run Check B and Check C back to back - read-only,
no tenant changes, nothing seeded - and report ONE combined outcome in plain words
("checking that the demo users can sign in and hold the licenses this run needs"). Then the
administrator signs in for the run. `<config>` is the chosen config's `path`.

### Check B - demo users: passwords and licenses

```text
pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/preflight_credentials.ps1 -ConfigPath "<config>"
```

Wrong demo-user passwords are the most common silent failure: the team and channels appear,
but no mail, files, chats or posts. This signs each demo user in once, as the seeder will (a
public-client password sign-in to this deployment's DemoIQ app), reads their own licenses
and prints `{ ok, source, allReady, passed, failed, users[{ upn, role, derived, status,
hint, licenses{ status, missing[], pending[], detail } }], licenses{ requiredFamilies[],
issues, pending, unchecked }, note, ... }`. No administrator row: the administrator has no
password and signs in below. Use each row's `hint`; never echo a password.

- `source` must be `receipts`; `config` (with a `note`) means the receipts could not be read
  here - probably not the deployment's host and checkout: relay `note` and stop.
- `ok:false` (exit 2) - the check could not run: `reason: app-mismatch` -> repoint the config
  (Saved-config path) and rerun; `reason: encrypted-config` -> convert it; else relay `error`
  and `hint`.
- Per user, `status` (the pass is `allReady: true` - every user `ok` or `ok-pending-mfa`):

| `status` | Meaning | Next |
| --- | --- | --- |
| `ok` | signed in | - |
| `ok-pending-mfa` | password valid; MFA or Conditional Access blocks the sign-in now | ready: the run relaxes MFA for the demo users |
| `bad-password`, `no-password` | wrong or missing password (or a wrong UPN) | re-open the config in the Builder (`-PrefillConfig`), fix it, Save config, rerun |
| `not-found` | no such UPN in the tenant; `derived: true` means the config never named it (an older config) | the five real UPNs in the Builder, then rerun |
| `disabled`, `locked`, `password-expired` | the account needs fixing in the tenant | fix it (and the password in the Builder if it changed), rerun |
| `consent-needed` | password valid; the DemoIQ app has no administrator consent | consent through the runner (below), rerun |
| `app-not-found`, `app-not-public-client` | the DemoIQ app is missing or no longer allows password sign-in | rerun `deploy.py up --with-demoiq --through demoiq` (below), rerun |
| `error` | another failure | relay `hint` |

The runner owns the app; never change it from this skill. With the user's yes, load
[deploy-platform](../../deploy-platform/SKILL.md) and follow its section 7 with its own
approvals; for consent, a tenant administrator adds `--grant-demoiq-consent`.

**Licenses** gate the run without changing `allReady`. For each signed-in user the check
grades the families the config's content types need (`requiredFamilies`): `teams` (chats,
channel posts, meetings), `exchange` (email, meeting invites) and `sharepoint` (Office files,
the AoP runbook), each `ok`, `pending`, `disabled` or `missing`:

- `licenses.issues` above 0 (a required family `missing` or `disabled`): stop. Name each user
  and family from `licenses.detail` (a demo user without Teams leaves channels with no
  conversations). The fix is a license in the tenant, then rerun; proceed anyway only when
  the user explicitly says so (for example they dropped that content type), restating what
  will not seed for whom.
- `licenses.pending` above 0: warn - assigned but still provisioning (common on new tenants;
  minutes to a few hours). Offer to wait and rerun, or to proceed accepting the risk.
- `licenses.status: unchecked` rows (no token, or the read failed): "licenses could not be
  verified for <upns>" - unknown, not a pass, but not a stop on its own.

### Check C - Azure CLI for meeting audio

```text
pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/preflight_azure_cli.ps1 -ConfigPath "<config>"
```

Meeting audio uses the Azure CLI's live token on the platform's Foundry account, so a CLI
signed in to another tenant fails the MP3 step after everything else has seeded. The check
reads only the local CLI cache and never changes it; it prints `{ ok, applicable, status,
azUser, azTenantId, expectedTenantId, cachedMatch, hint, fixCommands[], note, warning, ... }`.
By `status`:

- `not-applicable` (meetings not selected, or no Speech resource): say nothing about it.
- `ok`: ready; relay a `note` in one line (for example a CLI account other than the
  administrator, which then needs a data-plane role on the Foundry account).
- `wrong-subscription`: run the single `az account set --subscription <id>` from
  `fixCommands` yourself, rerun Check C and fold "fixed the Azure subscription" into the
  outcome.
- `wrong-tenant`: when `cachedMatch` is true, run the one `az account set` in `fixCommands`
  yourself and rerun. Otherwise only the user can sign in (`az login` is interactive; never
  run it yourself):
  `🔐 **ACTION NEEDED** - in your own terminal on this machine, run <fixCommands, verbatim, in order> and sign in with an account of the demo tenant, then tell me "done".`
  Rerun Check C and proceed only on `ok`.
- `not-logged-in`, `no-cli`: the same ACTION NEEDED with that status's `fixCommands`.
- `no-expected-tenant`: the receipts could not be read here - relay `hint` and stop.
- A `warning` relays the deployment's own audio check (unavailable): say it - meetings and
  transcripts still seed, the MP3 may not.

Instead of fixing the CLI, the user may drop meetings from this run: deselect **meetings** in
the Builder (meetings and transcripts then do not seed at all), save, and run 1c.5 again.

**Combined outcome.** Pass = `allReady: true`, `licenses.issues` 0 and Check C `ok` or
`not-applicable`: say it once ("all five demo users can sign in - `<n>` behind MFA that the
run relaxes - and hold the licenses this run needs; the Azure CLI is ready for meeting
audio"), with any pending or unverified licenses, and continue with the sign-in. Anything
else: stop before authoring, name the failing account or check and its next action, and after
the fix run 1c.5 again from Check B.

### The administrator's sign-in for the run

Taken now, so that everything after the content-preview approval runs unattended.
`<adminUpn>` is the chosen config's `adminUpn` from `configs[]`.

1. **Reuse a fresh sign-in:**
   ```text
   pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/admin_signin.ps1 -Status
   ```
   It prints `{ ok, signedIn, user, tenantId, clientId, expiresAt }`. When `signedIn` is true
   for `<adminUpn>`, with this deployment's `tenantId` and `clientId` and an `expiresAt` more
   than an hour away, keep it and skip step 2.
2. **Sign in** - with the terminal tool in **async** mode:
   ```text
   pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/admin_signin.ps1 -ExpectedUpn "<adminUpn>"
   ```
   It prints `{"status":"waiting","codeFile":...,"verificationUri":...,"expiresAt":...}` and
   waits up to 15 minutes. Post
   `🔐 **ACTION NEEDED** - open the sign-in code file <codeFile> (it holds the code and the page to enter it on) and sign in as <adminUpn>.`
   Never read, print or relay the code; in a Codespace the user opens the file in their own
   terminal there (`cat "<codeFile>"`). Read the shell about every 60 seconds with a
   heartbeat ("waiting for `<adminUpn>`'s sign-in; the code is valid until `<expiresAt>`")
   until its final line.
3. **Result.** `ok:true`: "Sign-in detected for `<user>` - stored for this run (until
   `<expiresAt>`)." Relay `warnings[]`: `not-global-administrator` means the run may fail to
   prepare the tenant - confirm the account is a Global Administrator or sign in again as
   one; `consent-unverified` is informational. `ok:false` carries `reason` and `message` -
   relay the message, then:

| `reason` | Next |
| --- | --- |
| `consent-missing` | administrator consent through the runner (deploy-platform section 7, `--grant-demoiq-consent`), then step 2 again |
| `wrong-account` | step 2 again, choosing `<adminUpn>` (or correct the Admin UPN in the Builder and run 1c.5 again) |
| `declined`, `expired` | step 2 again |
| `wrong-tenant`, `wrong-app` | not this deployment's tenant and app: step 2 again with an account of `<tenantId>`; never seed another tenant |
| `no-receipts` | the receipts could not be read here: back to SKILL.md Step 0 |
| `sign-in-failed`, `network`, `store-failed` | retry once; for `store-failed`, check that `<ws>` and the temp folder are writable |

A sign-in serves one run and lasts 12 hours at most: the launch hands it to the run, which
removes it from disk, and a new sign-in replaces an older one. Flow 1h checks
`admin_signin.ps1 -Status` right before the launch and signs in again only when nothing
valid is stored.

---

**Stage complete.** A ready config, a run mode, passing pre-seed checks and the
administrator's sign-in -> read `references/flow-1d-author.md` and continue (author ->
validate -> preview -> regenerate).

---

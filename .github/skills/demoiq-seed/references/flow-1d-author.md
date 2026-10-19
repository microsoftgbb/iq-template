# DemoIQ for GitHub Copilot - Flow 1, stage 1d: Author the content, preview it, regenerate the seeder

> Part of the **demoiq-seed** skill (DemoIQ adapted for GitHub Copilot) - one STAGE of Flow 1
> (see `references/flow-1-build.md`, the spine, for the whole-pipeline rules: never-pause, the
> gates, security). Read this file FULLY before executing this stage - do not run it from
> memory. The skill folder is `.github/skills/demoiq-seed` (relative to the repository root,
> the normal working directory; elsewhere use the skill's base directory) and `<ws>` (the
> single canonical output home) is `~/.copilot/DemoIQ-Output-Scout` - see SKILL.md "Paths and
> commands". Commands are single lines that run unchanged in bash, zsh and PowerShell: pass
> the absolute paths the scripts print, double-quoted. `<scratch>` is the `scratchDir` that
> `find_config.ps1` returned (`<ws>/scratch`) - write every file you author there with your
> own file-creation tool, as UTF-8, and NEVER into the repository checkout. SKILL.md's hard
> rules, gates and "Keep the run moving" rules apply throughout.
> **Previous stage:** `references/flow-1a-configure.md` (story, config, run mode, pre-seed
> checks and the administrator's sign-in).
> **Next stage after this one:** `references/flow-1h-seed.md` (sign-in check, launch, monitor,
> wrap-up).

## 1d. Author the content yourself, then regenerate the seeder with it baked in

DemoIQ content (emails, chats, channel posts, Word/PPT/Excel narratives, meetings,
the meeting transcript, and the AoP runbook) is authored by **you (the GitHub Copilot
agent)** and injected into the seeder via `generate.js --pasted`. This is how DemoIQ
authors content - the agent writes every piece, then bakes it into the seeder, which
seeds it as-is. There is no external content service to depend on or wait on, so what
you author is exactly what lands in the tenant.

> **Design decision (deliberate - do not reintroduce content packs):**
> shipping pre-baked content-writer scripts / stock content bundles in the skill was
> evaluated and REJECTED. Four reasons: (1) authored content embeds the config's exact
> demo-user UPNs and tenant references, so a canned pack only fits the tenant it was
> written for; (2) a stored pack fossilizes against the engine's per-key `cdx_seed_json`
> schemas (schema drift is exactly what `validate_pasted.js` exit 7 detects); (3) scenario
> variety - ~29 industries x 4 scenarios x 48 languages x personas - is the product, and
> canned content collapses it; (4) the repeat-run cost is already solved without canned
> content: Flow 1R re-seeds a prior run's stored bundle with zero re-authoring. Fresh
> authoring happens once per NEW scenario (~10-15 min) and the preview gate means the user
> shapes it; that is the intended trade.

> **If you delegate the authoring to a background/sub-agent, you STILL owe the user a
> heartbeat every turn (hard rule - same discipline as the seeder monitor in 1h).**
> Authoring takes ~10-15 minutes; a "the agent is working, sit tight" followed by silence
> is indistinguishable from a hang and WILL make the user ask "what's going on". Each turn
> while the authoring agent runs, post one concrete status line - what it is doing, how
> long it has been running, and any progress signal you can read (keys drafted so far,
> tool calls made) - then keep waiting. Attribute any sub-agent narration that surfaces
> ("the authoring agent is now validating the JSON") instead of letting unlabelled text
> stand alone in the conversation. Authoring inline (no delegation) with a one-line note
> as you finish each content key satisfies the same rule.
> **Shape every authoring status line as a PROGRESS COUNTER** so the user can see the bar
> move: "Authored `comm_email` (12 emails across 3 threads) - **4 of 9 keys done**, ~6 min
> in, `word`/`ppt`/`excel` next." The user must never go more than ~60-90 seconds without
> a fresh, numbered line (SKILL.md heartbeat cadence) anywhere between preview approval
> and seeding-done.

> **Author in CHUNKS around a story bible - and fan the chunks out to PARALLEL sub-agents
> when the environment supports them.** First write a short **story bible**: the company
> (ONE precedence rule, the same one the engine applies everywhere: a custom brief's own
> `Company:` line > the config's optional `companyName` field > the Contoso default - the
> step-2 prompts embed the resolved company and a mandatory branding line, so the bible's
> company MUST match what those prompts name; the company the user typed must show up in
> the authored content on the FIRST run, never only after a rewrite), the people (the 5
> demo users cast into their personas), the project/file names, the timeline, and the
> central tension - a paragraph plus a name table is enough. Then author
> each content key as its own CHUNK (`comm_email`, `comm_chat`, `comm_channel`, `meetings`,
> the `word`/`ppt`/`excel` prose, `audiotranscript`, `aop`), each grounded in that bible so
> names, threads, and file references stay consistent across types.
> - If your environment can run **sub-agents concurrently** (GitHub Copilot's task tool), give
>   each chunk to one (embed the full bible + that key's prompt in each sub-agent's
>   instructions - sub-agents are stateless; the heartbeat rule above still applies while they
>   run). This is the biggest authoring speed-up available - chunks author in parallel instead
>   of end-to-end.
> - If only sequential delegation (or none) is available, author the chunks back-to-back
>   yourself in bible order.
> Chunking pays either way: a finished key survives a session checkpoint (never re-author
> completed chunks - keep the partial `--pasted` map as you go), each chunk validates
> incrementally (step 5 accepts any subset of keys - run those interim checks WITHOUT
> `--prompts`, because with the metadata present the validator requires every selected
> Office key to exist, which a partial map deliberately fails), and no single generation
> grows unwieldy. Merge all chunks into ONE complete `--pasted` map before step 5's final
> validation, which ALWAYS passes `--prompts`.

> How it works: the engine's generated seeder supports a per-key "pasted response"
> path (in `DemoIQ.html` see `getPastedResponseB64` / `_pastedResponses` and the
> generated `$PastedResponse_<key>` decode-and-merge into `$Global:SeedData` - grep
> those names rather than trusting line numbers): for each content key, it decodes the
> embedded response, extracts the `cdx_seed_json` fence, parses it into
> `$Global:SeedData`, and seeds it directly. **Author EVERY selected key** so the
> whole run is your content. `generate.js --pasted <map.json>` ({ key: responseText })
> embeds them, and special-cases `aop` via post-processing.

Steps:

1. Write the run's prefs file `<scratch>/demoiq-prefs.json` with your own file-creation tool,
   as UTF-8 (`generate.js` requires `--prefs`; `--from-config` supplies the administrator,
   the app, the demo users and the scenario). If Flow 1a's Continuation path already wrote
   this file, keep it as it is; otherwise its content is just:
   ```text
   {}
   ```
   Only the Describe-it and Continuation paths (Flow 1a) merge scenario keys into it.
   Everywhere below, `<path to the config>` is the chosen config's absolute `path` from
   `find_config.ps1`'s `configs[]` (Flow 1a) - never open the file itself.
2. **Dump the per-key prompts for THIS config** (each prompt already carries that
   key's exact `cdx_seed_json` schema and the demo users/scenario/language):
   ```text
   node .github/skills/demoiq-seed/scripts/generate.js --html .github/skills/demoiq-seed/engine/DemoIQ.html --prefs "<scratch>/demoiq-prefs.json" --from-config "<path to the config>" --emit prompts --out-file "<scratch>/demoiq-prompts.json"
   ```
   (Always `--out-file`, never shell redirection: Windows PowerShell 5.1 redirection writes
   UTF-16.) **No password anywhere:** the config is a plain, owner-only file, so
   `--from-config` needs nothing else - and you still never open it yourself (SKILL.md Hard
   rule 4). If it exits 2 with "encrypted DemoIQ config", an upstream encrypted config slipped
   through: the user converts it once in their own terminal (`scripts/convert_config.ps1`),
   then run this command with the converted config. This applies to EVERY `--from-config`
   call in this flow (steps 2, 3, and 6).
   It writes `{ ok, language, describeInChat, aop, officeFiles, keys[], prompts{ key: text } }`
   to the out-file as UTF-8 and prints a one-line summary `{ ok, outFile, bytes, keys, aop,
   describeInChat, language }` - take `keys` / `aop` / `describeInChat` from that line and read
   the prompts from the file with your file-viewing tool. The `keys[]` are the
   content keys this config selected (subset of: `comm_email`, `comm_chat`,
   `comm_channel`, `word`, `ppt`, `excel`, `meetings`, `audiotranscript`).
   Each prompt carries a `Date anchor (MANDATORY): today is <weekday> <date>` line -
   honor it for every `sentDateTime`/`startDateTime` you author (never copy an example
   date as-is). The seeder still rebases authored dates onto the actual run day
   (`Normalize-SeedDataDates`, whole-week shifts) as the deterministic backstop, so
   content authored days before the run cannot land months off in the tenant.
   `officeFiles` is the machine-readable Office expectation set - per type
   (`word`/`ppt`/`excel`): `{ selected, expected, primaryName }` (expected is 0 when
   the type is unselected), plus `excelTables` - derived from the SAME engine state
   the seeder bakes in. **Keep the saved `demoiq-prompts.json` around for step 5**:
   the validator reads `officeFiles` from it to check the authored multi-file
   content against this exact config. The AoP runbook
   is built at PS1 runtime, so it NEVER appears in `keys[]` - the boolean **`aop` field is
   the authoritative "is AoP on?" signal**: when `aop:true`, also run the aop-context dump
   (step 3) and author the `aop` runbook (steps 4-5). Exit
   code **5** = the config lacks its `adminUpn` or `existingAppId` (have the user re-save it
   in the Config Builder; for a missing app ID, `repoint_config.ps1` sets this deployment's).
   - **If the dump reports `describeInChat: true`** (in the Config Builder the user chose "Describe in your
     own words" and left it blank, rather than picking a preset or typing the scenario), the baked-in
     scenario is empty,
     so do NOT author from this dump yet. First run the **Describe-it path** (Flow 1a): ask the user
     to describe the scenario + the seed content they want in plain language (or, if they want, base
     it on this project's scenario, as Flow 1a's Story path does), synthesize a `customScenarioText` (Title +
     Description + Excel/Word/PowerPoint file names) and MERGE it into the prefs file (only
     `customScenarioText` / `companyName` / `demoLanguage` - content types and counts always come
     from the config), then **re-run this
     dump with that prefs file** so the prompts carry the real scenario. The administrator, the
     app and the 5 demo users still come from the same config via `--from-config`. Author from
     the re-dump - it still reports `describeInChat: true` (that flag is read from the config),
     which is expected once the prefs carry the `customScenarioText`: skim one prompt to confirm
     the story, then author. When `describeInChat` is false/absent, author directly from this
     dump as usual.

   > **Report the scenario from the SCENARIO, not the `industry` field (avoid mislabeling).**
   > When you describe a config's run to the user, say what the run will actually seed by reading
   > the **scenario** the prompts carry (never the config file itself) - i.e. the
   > `customScenarioText` baked into the config (a
   > custom/typed scenario) or the selected preset. The config's `industry` field is just a
   > coarse category that can still hold a leftover default (e.g. "Financial Services") even when
   > the real scenario is something entirely different (e.g. a custom "Modern Agentic AI" story).
   > **Never announce the run by the `industry` value alone** - that is exactly how a custom
   > "Fabrikam Robotics - The Agent That Got Too Helpful" run gets mis-described as a "Financial
   > Services" demo. If a `customScenarioText` is present, lead with ITS title/story; mention the
   > industry only if it genuinely matches. The per-key prompts in this dump already embed the real
   > scenario - skim one to confirm the company/story before you summarize.
   > **`describeInChat: false` with a non-empty `customScenarioText` is CORRECT and expected** when
   > the user TYPED their scenario into the Config Builder box (typed text = a self-contained custom
   > scenario baked into the config; only a BLANK custom box sets `describeInChat: true`). Do not
   > treat `describeInChat: false` as a problem or imply the scenario is missing - just author from
   > the typed `customScenarioText`.

   **Then create THIS run's folder (now that the scenario title is known).** Every run
   gets its own folder under `<ws>/runs/`; the content preview (5b), the regenerated
   seeder (step 6), and all of the run's outputs live there:
   ```text
   pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/new_run.ps1 -ScenarioTitle "<the run's scenario title>" -Tenant "<tenant domain, e.g. contoso.onmicrosoft.com>" -ConfigPath "<path to the config>"
   ```
   (Strip double quotes, `$` and backticks from the title first - each shell reads them
   differently.) It prints `{ ok, runId, runFolder, runJson }` (run.json is the run's
   manifest; the launcher stamps the launch into it, and Flow 7 reads it for history).
   Everywhere the steps below say **"the run folder"**, they mean this absolute `runFolder`.
3. **Dump the AoP authoring context** (format rules + Microsoft article catalog +
   whitelisted surface headings) so you can author a parseable AoP runbook:
   ```text
   node .github/skills/demoiq-seed/scripts/generate.js --html .github/skills/demoiq-seed/engine/DemoIQ.html --prefs "<scratch>/demoiq-prefs.json" --from-config "<path to the config>" --emit aop-context --out-file "<scratch>/demoiq-aop-context.json"
   ```
   (Only needed when the step-2 dump reported `aop:true`; read the file with your
   file-viewing tool.) Exit code **8** = the AoP format rules could not be extracted (engine
   layout changed) - this is NOT a config problem: do not ask the user to change anything;
   stop and report it as engine drift, like exit 10 (step 6).
4. **Author the content.** Read `demoiq-prompts.json` and, for EACH key in
   `keys[]`, write the content that prompt asks for - in the prompt's required
   output format: the readable text, then a single fenced ` ```cdx_seed_json ` block
   containing strict valid JSON per that key's schema. Top-level arrays by key:
   `comm_email` -> `emails[]`; `comm_chat` -> `chats[]`; `comm_channel` ->
   `channels[]`; `meetings` -> `meetings[]`. `word`/`ppt`/`excel`/`audiotranscript`
   are prose (the doc bodies / English transcript) - no JSON needed. For `aop`,
   author the runbook per the aop-context format rules. **Channel names (`comm_channel`):
   give each channel a SHORT, meaningful, scenario-grounded `channelName`** (e.g.
   `incident-gus-autonomy`, `series-b-warroom`, `product-safety`) - NEVER a generic
   placeholder like "Custom scenario", "Channel", or the literal scenario-mode label.
   Reuse the SAME exact `channelName` on every message that belongs to that channel so
   they group correctly - that grouping is what the `channelName` field is FOR.
   **Note on final display names:** the engine remaps your authored `channelName`(s) to
   the scenario-derived title(s) by position (the Nth distinct channel you author maps to
   the Nth scenario), and it derives the Teams **team name** from that scenario title, NOT
   from your authored string. So your `channelName` controls grouping, while the engine
   controls the final, stable display name (a quoted phrase in the scenario title, e.g.
   "The Agent That Got Too Helpful", becomes the channel/team name). Author clean grouping
   names anyway, but do not expect them verbatim on the surface. Honour every rule in the
   prompt (exact demo-user UPNs, exact file names, RE: threading, ASCII-safe
   punctuation, `\n`-escaped line breaks).
   **FILE-NAME MENTIONS - make the links land where the story is:** the seeder uploads the
   Word/PPT/Excel files FIRST, then rewrites any mention of their EXACT file names (with or
   without extension) in emails, chat/channel messages, and meeting bodies into real
   OneDrive links. So author at least one email AND at least one chat or channel message
   that mentions each file by its exact scenario file name ("I've put the numbers in
   `Project_Phoenix_Program_Tracker.xlsx` - see the Risk tab"). Files nobody mentions still
   get linked via an automatic "Related documents" footer on the last email/message, but an
   inline mention reads like a real team - the footer is the fallback, not the goal.
   **MULTIPLE FILES PER TYPE (config `options.wordDocs` / `pptDecks` / `excelWorkbooks` > 1):**
   when a prompt asks for more than one document/deck/workbook, it also specifies the delimiter
   contract - start EACH document (including the first) with its own line, exactly
   `===== FILE: <FileName>.<ext> =====`. The FIRST segment's delimiter must carry the exact
   scenario file name (the prompt states it); extras get distinct scenario-grounded names. The
   seeder splits on those lines and uploads each segment as its own OneDrive file. Without
   delimiters the whole response seeds as ONE file (the classic behavior), so when the count
   is 1 author exactly as before - no delimiter line at all.
   **EXCEL - table format is a seeding contract, not a style choice:** author the `excel`
   key's worksheets as PLAIN TABLES - one header line then 5-10 data rows per sheet,
   columns separated by TABs, 2+ spaces, or a markdown pipe table (`| col | col |`),
   tables separated by a blank line. Comma- or single-space-separated lines are DROPPED by
   the engine's parser; if no line survives, the workbook seeds EMPTY (step 5's validator
   now fails exactly this case up front). **LANGUAGE - native Unicode, never
   transliterations:** when the content language is not English, author EVERY field
   (subjects, bodies, titles, document/slide/cell text, the AoP runbook) in that
   language using its **native Unicode characters** - e.g. German `ä ö ü ß`
   ("nötig", "Rücklaufverfahren", "für", "müssen", "Maßnahmen"), accents for
   French/Spanish, native scripts for Japanese/Chinese/Korean. NEVER substitute ASCII
   transliterations like "noetig"/"fuer"/"Massnahmen". The pipeline preserves UTF-8
   (base64-embedded), so write the authored content as UTF-8 directly with your
   file-creation tool and never pipe it through a shell (Windows PowerShell can mangle
   accents). The `audiotranscript` is the ONLY
   exception - it is always English by design. **PERSONAS - honor explicit choices:** when
   the user set personas for the demo users (the Config Builder's persona dropdowns, or
   named roles in a describe-it description), CAST the users accordingly in everything you
   author - who organizes, who challenges, who reports - and keep each user's voice
   consistent with that persona across emails/chats/meetings. The step-2 prompts embed the
   demo users; if the persona choices are not visible there, they are advisory-only - say
   so rather than silently ignoring them (users expect saved personas to shape the
   content and read the silence as a bug). Write a `--pasted` map keyed by each
   content key (plus `aop`) to `<scratch>/demoiq-pasted.json` with your own file-creation
   tool, as UTF-8 - one JSON object whose values are the full response texts you authored
   (author every value yourself - this is the content that gets seeded verbatim):
   ```text
   { "comm_email": "<response text>", "comm_chat": "<response text>", ..., "aop": "<runbook text>" }
   ```
   Every value is a JSON string: escape `"` and `\` and write line breaks as `\n`. Step 5
   rejects a map that is not valid JSON.
5. **Validate the authored content** before seeding (catches malformed JSON up
   front). Pass the selected keys (from step 2's `keys[]`, plus `aop` when the step-2
   dump reported `aop:true`) AND the step-2 prompts dump - its `officeFiles` metadata
   is what lets the validator check the Word/PPT/Excel content against THIS config's
   exact file counts and primary names:
   ```text
   node .github/skills/demoiq-seed/scripts/validate_pasted.js --pasted "<scratch>/demoiq-pasted.json" --prompts "<scratch>/demoiq-prompts.json" --keys "<comma-separated selected keys, e.g. comm_email,comm_chat,comm_channel,word,ppt,excel,meetings,audiotranscript,aop>"
   ```
   It prints `{ ok, checked[], missing[], errors[], warnings[], summary{...} }`. **Exit 0 =**
   proceed. **Exit 7 =** fix the listed `errors`/`missing` (re-author the offending
   key) and re-validate. Do NOT seed until it returns `ok:true`.
   **ALWAYS pass `--prompts`** - without it the multi-file checks degrade to
   structure-only (no expected-count or primary-name enforcement), and a config that
   asked for 2 Word docs with content that only parses as 1 would slip through to the
   seeder (which now fails fast on the mismatch, but that is 15 minutes later - catch
   it here). The Office checks it runs: exact segment count per type (delimiter
   contract, CRLF and LF both valid), unique file names, correct extension per type,
   the first delimiter carrying the scenario primary file name, a non-empty body per
   segment, and Excel table parseability PER WORKBOOK (a second workbook with no
   parseable table fails even when the first has plenty).
5b. **Preview the content for the user before seeding (MANDATORY - never skip).** Seeding mutates
   a real tenant and takes ~15-20 min, so give the user a chance to sanity-check tone,
   industry fit, and (especially for non-English runs) language quality FIRST. This is
   read-only - it renders the SAME authored `--pasted` map that will be seeded (no tenant
   writes, no drift):
   ```text
   node .github/skills/demoiq-seed/scripts/preview_content.js --pasted "<scratch>/demoiq-pasted.json" --keys "<comma-separated selected keys>" --html-out "<run folder>/DemoIQ-Output/content_preview.html"
   ```
   It prints a friendly markdown preview (emails grouped into threads, chat/channel
   messages, meetings with agenda + meeting-chat, prose snippets) then a final JSON line
   `{ ok, rendered[], counts{}, htmlOut, warnings[] }`. Render the markdown for the user
   and, if you wrote `--html-out`, link the served HTML (below) for a formatted view. Do NOT
   improvise a `python -m http.server`, do NOT open the preview with browser-automation tools
   (Playwright etc.), and never hand out a `file://` link (the user reviews this page, so it
   must land in THEIR browser - automation profiles hit session conflicts and render where
   the user never looks). Serve the output folder
   with the skill's built-in helper (detached, so a checkpoint can't kill it mid-review)
   with `-OpenBrowser`, which opens the printed URL in the user's default browser:
   ```text
   pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/serve_local.ps1 -Detach -OpenBrowser -Root "<run folder>/DemoIQ-Output" -Path "content_preview.html"
   ```
   One sync call; it prints `[SERVE-URL] http://127.0.0.1:<port>/content_preview.html`,
   `[SERVE-PID]`, and `[SERVE-OPENED]` (the user's browser has it - or the no-browser WARN:
   then share the URL with the private port-forward instruction from Flow 1a's 1b step 2) -
   paste the same URL in chat as the clickable fallback, and stop the process with the
   `[SERVE-PID]` (`kill <pid>` on macOS/Linux, `Stop-Process -Id <pid>` on Windows)
   when the user has approved (an orphaned server auto-exits after ~2 h). Re-probe the URL
   before re-sharing it after any wait (Flow 1a's 1b step 6 rule). The HTML preview is
   **ALWAYS full-fidelity, unconditionally**: every email body, chat message, post, and
   document is rendered IN FULL (never truncated), and each section header carries a
   character count - so when the user asks for a change (e.g. "make the emails longer")
   and you re-author + re-preview, the difference is actually visible. `--max <n>` cannot
   reduce it: the flag trims ONLY the markdown skim printed to stdout (fewer items,
   truncated text - useful to keep your chat rendering bounded on big runs); the HTML the
   user reviews shows everything regardless. Then ask the user to confirm the content
   looks right before you regenerate/seed; if not, re-author the weak key(s) and
   re-preview.
   > **The content preview lives HERE and only here.** This pre-seed gate is the one place
   > the preview is generated, served, and linked. After the run completes, do NOT link or
   > re-surface `content_preview.html` - the AoP runbook is the single end-of-run record of
   > what was produced and where it sits in the tenant (see 1i).
   > **HARD GATE (do not bypass) - and the ONLY gate between here and a running seeder.**
   > Approving the *plan* earlier is NOT the same as reviewing the *authored content*. You MUST
   > render this preview and get an explicit go-ahead from the user BEFORE you regenerate the
   > seeder: a single `ask_user` (Gate 2) with EXACTLY this question - *"Does this content look
   > right to seed into tenant `<tenantId>`?"* - and EXACTLY these choices: **"Looks good -
   > seed it now"** / **"Re-author (tell me what to change)"** / **"Cancel this run"**.
   > **Context BEFORE the call (never inside the question):** besides the preview link, restate
   > the target tenant (`<tenant name>` / `<tenantId>`, confirmed at Gate 0) and what seeding
   > does to it, as Gate 0 said: it uses the delegated-only DemoIQ app this deployment
   > registered, with the administrator's sign-in for this run; for the run only, it turns off
   > Security Defaults (when on) and per-user MFA for the five demo users and excludes them from
   > enabled Conditional Access policies (never the administrator), and restores exactly those
   > changes at the end of the run - `tenant_state.json` reports the result (1i); and the demo
   > users sign in with the passwords in the config to create their mail, chats, meetings and
   > files.
   > **"Looks good - seed it now" IS the seed consent.** On that answer, regenerate and launch
   > (flow-1h) with NO further question: there is no separate "seed now?" confirm after this gate
   > (the old preview-approval-then-seed-confirm pair reads as two menus for one
   > decision). The run mode was chosen at 1c and the administrator signed in at 1c.5, so nothing
   > is left to ask - restate the run mode in the launch status line instead (if the sign-in has
   > lapsed, 1h step 1 renews it as an ACTION NEEDED, not a question). Never jump from
   > plan-approval straight to seeding - the user always sees the real emails/chats/files first.
   > The only exception is an explicit user instruction to skip the preview - then this same
   > gate question is still asked, exactly once, just without the rendered preview.
   > "Re-author" -> ask what to change, re-author only the affected key(s) (step 4), re-validate
   > (step 5), re-preview and ask this gate again. "Cancel this run" -> stop and return to the
   > menu: nothing touched the tenant, and the run folder holds only the preview (if
   > `status.ps1` then counts that never-launched folder as a seed run that stopped, say it
   > never ran).
   > **Faster taste test (optional):** to validate the DIRECTION before authoring the FULL
   > set, author just a SMALL sample first (e.g. 1 email thread + 1 chat + the `aop` outline),
   > preview THAT, get a thumbs-up, then author the rest. Saves the ~10-15 min of full
   > authoring if the industry/scenario/tone is off.
6. **Regenerate the seeder INTO the run folder created in step 2.** Use that
   `runFolder` as the generator's `--out-dir` - the seeder writes `DemoIQ-Output/` and
   `logs/` beside itself, so the whole run lands in this one folder and runs never
   overwrite each other. Regenerate (the `--pasted` map makes
   the seeder seed your authored content directly; `--from-config` supplies the
   administrator, the app, the demo users and the scenario, and the seeder embeds no
   password - it reads the demo users' passwords from the config at run time):
   ```text
   node .github/skills/demoiq-seed/scripts/generate.js --html .github/skills/demoiq-seed/engine/DemoIQ.html --prefs "<scratch>/demoiq-prefs.json" --from-config "<path to the config>" --pasted "<scratch>/demoiq-pasted.json" --out-dir "<runFolder from new_run.ps1>" --emit ps1 --script-name DemoIQ.ps1
   ```
   It prints `{ ok, outputs: { ps1, ..., platformPatch{ applied, skipped } } }` and (to
   stderr) `[pasted] injected keys: ...` / `[pasted] aop applied: ...` /
   `[platform] patches applied: ...`. Exit codes: **2** with "encrypted DemoIQ config" = an
   upstream encrypted config (the user converts it once, step 2); **5** = the config lacks
   `adminUpn` or `existingAppId` (step 2); **9** = an authored key's content type is not
   enabled in this config (fix the map or the config) or the pasted AoP could not be applied
   (engine drift - report it like exit 10); never seed around either; **3** or **4** = the
   generator could not run the engine or its output failed the sanity checks (engine layout
   changed) - never run that output, report it like exit 10; **10** = the delegated seeder
   patch (`scripts/platform_patch.js`) no longer matches the DemoIQ engine - stop, do not
   seed, and report it (the engine changed and the adaptation must be updated through a pull
   request to this repository - never patch the skill mid-run, SKILL.md hard rule 9). Every
   generated seeder carries the `demoiq-seed: delegated patch v2` marker on its first line,
   and both `launch_seeder.ps1` and `run_seeder.ps1` refuse seeders without it (status
   `FAILED not-a-demoiq-seed-seeder`) - so seeders must always come from this `generate.js`,
   never from the DemoIQ web page's Download Script.
   Everywhere later steps say "the run folder", they mean this `runFolder` - it replaces the
   old flat "<output folder>" as the home of `DemoIQ.ps1`, `DemoIQ-Output/`, and `logs/` for
   THIS run.
   - **The generator also persists the run's reusable inputs beside the seeder** (both
     non-secret, both automatic - no extra step): `demoiq-pasted.json` (a copy of the
     authored content map - it powers the Re-seed flow, `references/flow-1r-reseed.md`,
     which repeats this run later WITHOUT the 10-15 min authoring stage) and
     `run_identity.json` (non-secret IDs - `appId`, `speechResourceId`, `adminUpn`,
     `tenant` - which the launcher merges into `run.json`; it also carries
     `expectedOfficeFiles` - the per-type Office file counts + primary
     names this seeder was generated for - which the post-seed check in 1h reads to
     require the FULL expected file set). **Leave both files in place.**
7. State the regenerated `DemoIQ.ps1` path, then proceed to run it
   (`references/flow-1h-seed.md`). The 5b approval already carried the seed consent - do not
   ask again.

> Security: the only credentials are the demo users' passwords, which live only in the
> plain, owner-only config in `<ws>/configs` (never open it, SKILL.md Hard rule 4); there is
> no administrator password - the administrator signs in for each run. The regenerated
> `DemoIQ.ps1` embeds NO credentials - it reads the passwords from the config at run time.
> Never echo the passwords back or write them to other files. Delete the transient
> scratch copies `<scratch>/demoiq-pasted.json`, `<scratch>/demoiq-prompts.json` and
> `<scratch>/demoiq-aop-context.json` after the seeder has started (one `rm "<file>"` per
> file works in bash, zsh and PowerShell alike) - but KEEP the run folder's
> `demoiq-pasted.json` + `run_identity.json` (written by the generator in step 6):
> they contain demo content and non-secret IDs only, and they are what makes the run
> re-seedable later. Nothing from this stage is ever written into the repository checkout.

> **Author EVERY selected content key** - the run is meant to be 100% your authored
> content. Always pass a complete `--pasted` map covering every key in `keys[]`
> (plus `aop` when selected); do not leave keys un-authored.

> Not offered in this adaptation: writing a config by hand with `generate.js --emit config`
> or `--emit both` from a prefs file - that would put the demo users' passwords into a prefs
> file. Every run starts from a config in `<ws>/configs` that `draft_config.ps1` drafted or the
> Config Builder saved, with the passwords typed only in the Builder (Flow 1a).

---

**Stage complete.** Content authored, validated, previewed, approved, and baked into
`<run folder>/DemoIQ.ps1` -> read `references/flow-1h-seed.md` and continue (sign-in check ->
launch -> monitor -> summarize).

---

## Helper - list industries / scenarios / personas / languages

This is no longer a user-facing menu option (the Config Builder does visual
browsing now), but the **list helper** still answers "which industries / scenarios /
personas / languages are there?" live from the source HTML (no stale copies):
```text
node .github/skills/demoiq-seed/scripts/generate.js --html .github/skills/demoiq-seed/engine/DemoIQ.html --list --out-file "<scratch>/demoiq-list.json"
```
It writes JSON with `industries[]` (each: `industry`, `scenarios[{index,id,title,
desc,excelFile,wordFile,pptFile}]`, `personas[]`), `universalPersonas[]`,
`aopSurfaces[]`, `aopAgents[]`, and `languages[]` to that file (read it with your
file-viewing tool). Render a tidy table or list for the user when they ask.

---

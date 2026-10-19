# DemoIQ for GitHub Copilot - Flow 9: Check Work IQ against the seeded content

> Part of the **demoiq-seed** skill (DemoIQ adapted for GitHub Copilot). Read this file FULLY
> before executing the flow - do not run it from memory. The skill folder is
> `.github/skills/demoiq-seed` (relative to the repository root, the normal working
> directory; elsewhere use the skill's base directory) and `<ws>` (the single canonical
> output home) is `~/.copilot/DemoIQ-Output-Scout` - see SKILL.md "Paths and commands".
> Commands are single lines that run unchanged in bash, zsh and PowerShell: pass the
> absolute paths the scripts print, double-quoted. SKILL.md's hard rules, gates and "Keep
> the run moving" rules apply throughout.

## Flow 9 - Check Work IQ against the seeded content (optional)

A seed run proves the content was written; it does not prove Work IQ can answer from it.
This optional check asks Work IQ, **as one of the seeded demo users**, two or three of the
run's own runbook prompts and records whether the answers cite seeded items. It is the only
evidence behind `status.ps1`'s `workiq-verified` state, and only for the prompts asked.

It is read-only and makes no tenant changes, so it needs no Gate 0. It is never a gate for
anything else, and it never feeds the seeded content anywhere (not into Foundry IQ
knowledge, the scenario folders or the Studio agent).

**Timing.** Microsoft 365 indexes new mail, chats, meetings and files over minutes to
hours. Right after a run, say so and offer the check later ("indexing usually takes a while;
I can check now and again later"). A miss shortly after seeding is not a defect.

### 1. Preconditions

- `status.ps1` reports a `lastSeedRun` whose `outcome` is `done` or `partial`; use its
  `runFolder` as `<run folder>`.
- The Work IQ MCP server from the `workiq` plugin is available in this session (its tools
  start with `workiq`, for example `workiq-ask`). If it is not, say the check needs the
  plugin ([Copilot tooling](../../../../docs/copilot-tooling.md)), Work IQ tenant consent and
  a licensed demo user, and stop: the state stays `seeded`.

### 2. Pick the prompts

```text
node .github/skills/demoiq-seed/scripts/workiq_check.js prompts --dir "<run folder>/DemoIQ-Output" --count 3
```

It returns `{ ok, file, demoUsers[], prompts:[{ surface, scenario, prompt }] }`: the "Start
Here" prompt of up to three runbook scenarios (Microsoft 365 Copilot app scenarios first;
in-app Word, PowerPoint, Excel, Code and Autopilot scenarios last, since they need their app
open), and the run's demo users. Exit 2 with "no AoP runbook" means the run did not include
`aop`: write two or three questions about the run's story yourself from the content summary
(for example "What is the latest on <subject of a seeded thread>?") and say they are not
runbook prompts.

### 3. Sign in to Work IQ as a demo user

Work IQ answers as whoever authorized its MCP server, so the check means something only as
one of `demoUsers[]` - never as the administrator, who received none of the content. Tell
the user which account to use and why, in one ACTION NEEDED line:

`🔐 **ACTION NEEDED** - when Work IQ asks you to sign in, use the demo user <upn> (not your administrator account); if Work IQ is already signed in as someone else, sign it out in Copilot's MCP server settings first.`

The user signs in in their own browser, with that demo user's normal MFA (the run restored
it). Never ask for, type or relay the demo user's password.

### 4. Ask

Ask each prompt with the Work IQ MCP server's ask tool, verbatim and one at a time. Use
only read tools: never a Work IQ tool that creates, updates, sends or deletes anything.
For each prompt, note:

- `answered`: `true` when Work IQ answered from work content; `false` for "I couldn't
  find...", a refusal, an authorization error or a timeout.
- `citations`: the sources Work IQ returned, each `{ "title": "...", "url": "..." }`
  exactly as given. Never invent or reconstruct a citation; record none when it gave none.

Note the signed-in account Work IQ reports (or the account the user confirmed) as `askedAs`.

### 5. Record and report

Write the results with your file-creation tool to `<scratch>/workiq-results.json`:

```json
{ "askedAs": "<upn>", "answers": [ { "prompt": "<prompt>", "answered": true, "citations": [ { "title": "<title>", "url": "<url>" } ] } ] }
```

```text
node .github/skills/demoiq-seed/scripts/workiq_check.js record --dir "<run folder>/DemoIQ-Output" --results "<scratch>/workiq-results.json"
```

It matches each citation title against the run's manifest (email subjects, file names,
meeting subjects, chat topics and channel names), writes `workiq_check.json` beside the
run's outputs and prints `{ ok, path, verdict, prompts, answered, withSeededCitations,
askedAsIsDemoUser, manifestFound }`. Report from it, with a small table (prompt / answered /
seeded citations):

- `found-seeded-content`: "Work IQ cited seeded content for `<withSeededCitations>` of
  `<prompts>` runbook prompts when asked as `<askedAs>`."
- `answered-without-seeded-citations`: Work IQ answered, but its citations did not match
  seeded items - indexing may still be catching up, or it found other content. Offer to
  check again later.
- `not-found`: no answers yet; most often indexing. Offer to check again later.
- `askedAsIsDemoUser: false`: say the check was not run as a seeded demo user, so it says
  little about the seeded content.
- `manifestFound: false`: the run has no `cleanup.config`, so no citation could be matched;
  say so rather than reporting "not found".

Then rerun `status.ps1` and say its new `summary`. Never describe Work IQ, the tenant or the
platform as "ready" from this check.

---

# Presenter runbook

How to prepare, present, recover and reset a demo of the template's fictional sample: one
assistant in two versions, with IQ and without, over the Site/Asset sample, where **Pump B is
in maintenance at North Works**. Everything in it is fictional.

One file drives the demo: [`app/src/demo/script.json`](../app/src/demo/script.json). Its steps
are presenter mode's rail and notes, mock mode's replies and the rehearsal's checks, so this
runbook doesn't repeat them. Run every command here in your derived project, against the
deployment you present: `npm run rehearse`, `deploy.py check` and `deploy.py trace` call live
services, and `deploy.py links` reads that deployment's state.

## Screens

One account runs every step: yours. The agent with IQ reads the live data and your mail as you,
so sign in with the account you rehearsed with.

| Screen | Where | Used for |
| --- | --- | --- |
| The app, in presenter mode | The app's URL with `?presenter=1`, full screen | Every step and question |
| Foundry portal, optional | The Foundry project's **Agents** and **Traces** | Behind the scenes: the two agents' tools and a run's trace, found by the response ID the app shows |
| Outlook on the web, with Work IQ | Your own mailbox | Sending yourself the sample mail; opening a cited message |
| Azure portal | The Fabric capacity | Resuming it before and pausing it after |

`deploy.py links` prints each of these for your deployment from Terraform's outputs, without
calling Azure, Fabric or Foundry. Its output holds deployment IDs: keep it out of the repository.

```sh
python3 .github/skills/deploy-platform/scripts/deploy.py links
```

## Before

### The day before

- [ ] **With Work IQ, send yourself the sample mail first**, from Outlook on the web, to yourself
  and nobody else, as the `work-context` step's setup describes. Microsoft 365 needs time to
  index it: send it at least a day before you rehearse or present that step. Without Work IQ,
  skip that step on stage.
- [ ] **Rehearse.** From `app/`, with the deployment's settings in `app/.env.local`
  ([app README](../app/README.md#run-it-against-a-deployment)) and Azure CLI signed in as
  you:

  ```sh
  npm run rehearse                    # every step with a question, twice
  npm run rehearse -- --with-work-iq  # also the Work IQ step, once its mail is indexed
  ```

  It asks each step's question of each agent the step names, one question at a time, and checks
  each answer's key facts, forbidden phrases, source markers, tools and time against the script.
  It prints a table and saves the replies in the ignored `.artifacts/rehearsal/`; they can hold
  your mail, so never commit them. Decide each step from the result:
  [when to use the scripted answer](#when-to-use-the-scripted-answer).
- [ ] Note the time of the slowest turns. `--compare` shows two results files side by side,
  such as yesterday's and today's.

### An hour before

- [ ] **Resume the Fabric capacity** if you paused it. `deploy.py check` prints the command.
- [ ] **Check readiness**, read-only:

  ```sh
  python3 .github/skills/deploy-platform/scripts/deploy.py check
  ```

  It reports the capacity's state, the app's health and sign-in registration, whether each agent
  runs its reviewed definition and each knowledge source's last ingestion, each with a fix. It
  changes nothing. Exit code `0` is ready, `1` not ready. It can't check your consents or your
  mail: those stay on your list.
- [ ] **Sign in and give your consents.** Open the app, sign in, and ask the live facts question
  with IQ. Accept Fabric IQ's consent card once, in the new tab, then ask again. With Work IQ,
  ask the mail question and accept its card too. Foundry IQ and Web IQ never ask.
- [ ] With `app = { min_replicas = 0 }`, the first request starts a replica: open the app now,
  not on stage.

### Fifteen minutes before

- [ ] Open the app with `?presenter=1`, or select **Presenter mode** in the header, and go full
  screen. Presenter mode stays on in this tab, through a sign-in, until you turn it off.
- [ ] **Warm up** with one question, such as the live facts step: the first Fabric IQ query
  after a resume is slow.
- [ ] Press **Page Down** and **Page Up** on your clicker; the rail follows. Select **Restart**
  to go back to the first step and reset the timers.

## Presenter mode

The rail lists the script's steps; the current one is highlighted. The stage shows the step's
title, its IQs and its question, with a timer for the step and one for the session. The notes
panel under it shows what to **Say**, **Do** and **Land**, the step's **Setup** and what to
**Expect** from each agent's answer: the script's key facts and checks, as a hint.

| Key | Does |
| --- | --- |
| **→** or **Page Down** | Next step |
| **←** or **Page Up** | Previous step |
| **Enter** | Ask the step's question, of each agent it names, side by side |
| **N** | Show or hide the notes |
| **B** | Blank the notes, and show them again |

No key acts while a text field has focus, so you can still type your own question in the
composer below the answers. A step that needs Work IQ is marked **needs Work IQ**; **Skip**
moves past it. Talk-only steps ask nothing. Presenter mode works in mock mode too, which makes
mock mode an offline rehearsal of the whole flow.

## Run of show

The steps come from `script.json`, in order. The timings are targets; replace them with your
own from the rehearsal.

| # | Step | IQ | Asks | Target |
| --- | --- | --- | --- | --- |
| 1 | One assistant, four IQs (`welcome`) | All four | Nothing: talk only | 0:00–2:00 |
| 2 | Live facts with Fabric IQ (`live-facts`) | Fabric IQ | Without IQ, then with IQ, side by side | 2:00–6:00 |
| 3 | Approved documents with Foundry IQ (`documents`) | Foundry IQ | With IQ | 6:00–9:00 |
| 4 | Public context with Web IQ (`public-context`) | Web IQ | With IQ | 9:00–12:00 |
| 5 | Your own mail with Work IQ (`work-context`) | Work IQ, optional | With IQ | 12:00–15:00 |
| 6 | What changed, and what didn't (`wrap-up`) | All four | Nothing: talk only | 15:00–17:00 |

About 17 minutes with Work IQ and 14 without, before questions. Each answer shows its response
ID: say that it finds the turn's trace.

## Plan B

### Scripted answers

When a live turn fails, times out, waits for a consent or you stop it, its answer card offers
**Show scripted answer**: the step's scripted reply from `script.json`, hatched and labelled
**Scripted answer, not live**. Say so out loud. The card still reads *Not answered*, and
**Ask again** remains for a later attempt.

### Mock mode

`npm run dev:mock` serves the app with the scripted answers instead of the agents: no sign-in
and no Azure, with one Work IQ consent card as an example. Use it to rehearse the flow without a
deployment, or when the deployment is down. Its header says **Mock mode**; never present it as
live.

### When to use the scripted answer

Decide per step from the two rehearsal runs:

- **Both runs pass:** run the step live. If it fails on stage, ask again once, then show the
  scripted answer.
- **One run fails:** rehearse that step again (`--step <id>`); if it fails again, plan the
  scripted answer.
- **Both runs fail, or time out:** use the scripted answer.
- **The answer without IQ names an asset:** the comparison no longer shows anything; use the
  scripted answers for that step and investigate afterwards.

## On-stage recovery

| What you see | Likely cause | Do now | Afterwards |
| --- | --- | --- | --- |
| Every with-IQ turn fails within seconds with "A user error occurred while executing a tool call", while the agent without IQ answers | The Fabric capacity is paused | Show the scripted answer, and resume the capacity during a talk step | Run `deploy.py check` an hour before |
| A consent card | You haven't consented to that tool as this account | **Authorize** in the new tab, then **Ask again**; if that fails, show the scripted answer | Give your consents an hour before |
| "The agent didn't finish within 225 seconds", or a turn you can't wait for | A cold or slow tool call | **Stop**, show the scripted answer and go on; ask again later if there's time | Warm up 15 minutes before; trace the turn |
| Foundry is throttling the agent model (HTTP 429) | The model deployment's quota | Wait a moment and ask again once, then show the scripted answer | Raise `agent_model.capacity` within your quota |
| Sign-in fails with msal.js `timed_out`, or the app asks you to sign in again | The redirect bridge was blocked, or the token expired | Reload and sign in again; presenter mode keeps your step | [Troubleshooting](runtime-integration.md#troubleshooting) |
| Work IQ finds no mail | The mail isn't indexed yet, or isn't in your own mailbox | Show the scripted answer | Send it at least a day before |

To see where a turn's time went, copy the response ID under its answer and, afterwards:

```sh
python3 .github/skills/deploy-platform/scripts/deploy.py trace "<response ID>"
```

It prints the turn's spans from Application Insights, with their durations, outcomes and tool
names, and never their content ([traces and telemetry](runtime-integration.md#traces-and-telemetry)).

## After

- **Pause the Fabric capacity** in the Azure portal: it bills while active.
- **Between sessions,** set `app = { min_replicas = 0 }` in `infra/terraform.tfvars` and rerun
  `deploy.py up` to stop paying for an idle replica. Remove it, or set `1`, and rerun `up` before
  the next session.
- **Keep what you collected private:** the rehearsal results and the links stay under the
  ignored `.artifacts/` or outside the repository; delete them once you no longer need them.
- **On a shared computer,** sign out of the app.

## Change the story

The script follows the sample. When your project changes the scenario, change `script.json`
with the agents' instructions and tool descriptions, the reply parser and the seed data, run the
app's tests, which validate the script, and rehearse again. The
[tuning playbook](scenario-authoring.md#tune-with-rehearsals) explains how to improve a step that
fails.

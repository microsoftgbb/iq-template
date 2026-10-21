---
name: m365-demo-seed
description: Optional - seed fictional Microsoft 365 demo content (mail, Teams chats and a channel, meetings, Word/PowerPoint/Excel files and a presenter guide) into the deploying user's non-production demo tenant, so Work IQ and Microsoft 365 Copilot have realistic work data to demo. Runs from GitHub Copilot App or CLI on Windows, macOS and Linux with Python 3.10+, standard library only. Inspired by DemoIQ (aka.ms/demoiq). Use only when the user explicitly asks to seed demo content or a demo tenant, set up or check demo users, check demo seeding status, check Work IQ against seeded content, restore tenant settings after a demo run, or clean up seeded demo content or this skill's local files. Attended sessions only. Never use for production tenants, general Microsoft 365 administration, platform deployment or template-maintenance checks.
---

# Microsoft 365 demo content for Work IQ

> **Inspired by DemoIQ.** DemoIQ seeds realistic, fictional Microsoft 365 work content into
> demo tenants so Work IQ and Microsoft 365 Copilot can be shown at their best. To learn
> more about DemoIQ, visit **[aka.ms/demoiq](https://aka.ms/demoiq)**.
>
> This skill is a separate implementation for **GitHub Copilot App and GitHub Copilot CLI**
> on top of this repository's deployment: a small Python tool that calls Microsoft Graph
> directly. It shares no code with DemoIQ and is not an official DemoIQ release; see
> [NOTICE.md](NOTICE.md).

**Validation status:** tested offline against a fake tenant on macOS and Linux; not yet run
against a real tenant or on Windows (see [NOTICE.md](NOTICE.md)). Say so when the user runs it
for the first time, and report unexpected Graph errors exactly as the tool gives them.

## Where it fits

- **Optional, on explicit request only:** "seed demo content for Work IQ", or a yes to the
  offer at the end of [`deploy-platform`](../deploy-platform/SKILL.md). It is not part of the
  platform, and seeded content is never readiness evidence for it.
- **The deploy-platform runner owns the identity.** `deploy.py up --with-m365-demo` (add
  `--through m365-demo` when the agent and app are not part of the run) registers a
  delegated-only, public-client app through Terraform (`infra/m365_demo.tf`, behind
  `m365_demo_enabled`): no application permissions, no certificate, no secret. An
  administrator grants consent through the runner (`--grant-m365-demo-consent`) or the
  portal. The result is the `m365_demo` receipt in `deploy.py status --json`; this skill only
  reads it. Never register, change or delete the app from here.
- **The deploying user's tenant only.** Tenant and app come from that receipt, so run this
  skill on the same host and checkout as the deployment (in a Codespace, inside it, and keep
  the Codespace running until a run ends).
- **One-way.** The project's scenario may shape the story; nothing seeded ever flows back
  into Foundry IQ knowledge, `scenario/knowledge/`, a lakehouse or an index. Connecting
  Work IQ to the Studio agent or the app remains the explicit
  [Work IQ opt-in](../../../docs/scenario-authoring.md#opt-in-capabilities).

## Hard rules

1. **Explicit request, attended session.** Never start on your own initiative, from a quoted
   example, or without a person present to answer `ask_user` and to sign in. In
   non-interactive prompts, automations or CI, stop and say that this skill is attended-only.
2. **One tenant: the deploying user's, confirmed non-production** (Gate 0) before anything
   changes it. Pass that tenant id as `--tenant`; the tool refuses any other.
3. **Seed and delete only what the user approved.** `seed --approve` takes the hash from
   `demo preview` after the user approved that preview (Gate 1); `cleanup --approve` takes the
   hash of the cleanup plan the user confirmed (Gate 2). If either changes, ask again.
4. **Secrets never touch chat.** Never ask for, type, read, print or relay a password or a
   one-time sign-in code:
   - The user types the demo users' passwords themselves, masked, with `users set` in a
     terminal they control. Never run `users set` yourself and never open, `cat`, `view` or
     search `demo-users.json`.
   - The administrator signs in in their own browser with a one-time code that the tool
     writes only to an owner-only file (`codeFile`). Give the user the file's path; never open
     or read it yourself.
5. **Nothing in the repository.** Demos, passwords and runs live in the workspace
   `~/.copilot/m365-demo-seed` on this machine (`M365_DEMO_HOME` overrides it). Never copy
   them into the checkout or commit them.
6. **Never touch the platform from here:** no edits to `infra/`, Terraform state, Fabric,
   Foundry, Search, the Studio agent or the app.
7. **Use the tool, not hand-written equivalents**, and one run at a time (the tool enforces
   it). If a command reports something wrong with this skill, tell the user; fixes ship
   through a pull request, never mid-session edits.

## Commands

Run from the repository root (on Windows use `py -3` instead of `python3`):

```bash
python3 .github/skills/m365-demo-seed/scripts/m365demo.py <command>
```

| Command | What it does |
| --- | --- |
| `status [--demo NAME]` | Reads the receipt and the workspace (no tenant calls); returns `state`, `summary` and `next`. |
| `demo new NAME --admin UPN --person UPN[=NAME] ...` | Starts a demo from the template's sample story for 2 to 8 demo users. |
| `demo validate [--demo NAME]` | Checks a demo; returns `errors` by path, `warnings`, counts and `sha256`. |
| `demo preview [--demo NAME]` | Writes `preview.html` (all content and the tenant changes) and returns its approval `sha256`. |
| `demo list` | The demos in the workspace. |
| `users set [--demo NAME \| --upn UPN ...] [--missing-only]` | **The user runs this**: prompts for the demo users' passwords, masked, and stores them owner-only. |
| `users status` / `users check` / `users clear` | Which passwords are stored; sign each demo user in and read their licenses; forget passwords. |
| `seed --demo NAME --tenant ID --approve SHA256 [--again]` | Starts a detached seed run of the approved demo. |
| `monitor --run ID [--wait SECONDS]` | A run's state and progress; with `--wait` (up to 600), returns when the state or step changes. |
| `cancel --run ID` | Stops a run after its current item; the tenant is restored. |
| `resume --run ID --tenant ID` | Continues an interrupted or partly failed seed run without repeating finished items. Refused once a cleanup has started deleting that run's content. |
| `restore --run ID --tenant ID` | Restores tenant settings a run left changed (a new run with its own sign-in). |
| `cleanup --run ID` | Plans deleting what a seed run created (local, no tenant calls); returns the plan's `sha256`. |
| `cleanup --run ID --tenant ID --approve SHA256` | Starts a detached cleanup run of the approved plan. |
| `workiq prompts --run ID` / `workiq record --run ID --results FILE` | The optional Work IQ check: what to ask, and scoring what Work IQ cited. |
| `local-cleanup [--depth runs\|all] [--confirm TOKEN]` | Lists, or with the inventory's token removes, this skill's files on this computer. |
| `history` | Past runs and their outcomes. |

Every command prints **one JSON object** on stdout; parse it rather than scraping text.
Relay `message`, `summary` and each `next` line as written. Exit codes match the deploy
runner: `0` done, `1` usage error, `2` blocked (investigate `reason`), `3` waiting for a
person (relay `next` and wait for them).

## Step 0 - status and the target tenant

Run `status` first in every session and act on `state`:

- `not-set-up`: the deployment has no demo app yet. With the user's yes, load
  [`deploy-platform`](../deploy-platform/SKILL.md) and follow its section 7, then rerun
  `status`. If the runner could not be read, you are probably not on the deployment's host
  and checkout: say so and stop.
- `consent-pending`: relay `next`; an administrator grants consent once.
- `running`: a run is active; monitor it, do not start another.
- `needs-restore`: a run ended without restoring the tenant. Restore first (Gate 0 applies).
- `needs-demo`: author or fix a demo (below).
- `needs-passwords`: the user sets passwords (below).
- `ready-to-seed`: check the users, preview and seed (below).
- `partly-seeded`: the last seed run stopped after creating content. Offer `resume` (Gate 0
  applies); seeding again would create a second copy, so `seed` refuses without `--again`.
- `seeded`: relay the summary (seeded is not ready) and the guide's path; offer the Work IQ
  check later.
- `workiq-verified`: Work IQ cited seeded content for the prompts asked, as a demo user.
- `cleanup-incomplete`: a cleanup left items in the tenant; plan the cleanup again.

**Gate 0 - confirm a non-production demo tenant**, once per session, before seed, resume,
cleanup or restore. In the message before the question, name the tenant (`setup.tenantId`) and
say what a run does:

- the administrator signs in to this deployment's demo app for each run;
- for the run's length it turns Security Defaults off (when on), puts the demo users - never
  the administrator - in a new security group that it excludes from enabled Conditional Access
  policies, and turns their per-user MFA off; it then restores exactly those changes and
  deletes the group;
- the demo users sign in with their stored passwords to create (or, in a cleanup, delete)
  their own content; the administrator creates (or deletes) the team and the group chats.

Ask with `ask_user`: *"Is this a non-production demo tenant that this skill may change as
described?"* with the choices `Yes - it is a non-production demo tenant` / `No - stop`.
Anything else stops.

## Seeding a demo

1. **Story and people.** Ask for the demo users (existing, licensed accounts) and the
   administrator if you do not know them. Run `demo new` with them, then adapt the story to
   the user's scenario following [references/content.md](references/content.md). Edit the
   demo file with your file tools (it holds no secrets) and run `demo validate` until it
   reports no errors. The content quality follows your model; prefer a capable one.
2. **Passwords.** If `status` says `needs-passwords`, ask the user to run the `users set`
   command from `next` in a terminal they control - a second terminal on this machine or the
   Codespace's own terminal. In GitHub Copilot App you may open a Terminal canvas for them,
   but the user types the command and the passwords. Wait for them, then run `users check`
   and relay its `next` lines until it is ready (exit 0). `ok-pending-mfa` passes: the run
   relaxes MFA for the demo users.
3. **Gate 1 - approve the content.** Run `demo preview`, ask the user to open `preview` in
   their browser, then ask with `ask_user`: *"Seed this demo into tenant TENANT as previewed?"*
   with `Yes - seed it` / `Not yet`. Only a yes counts, for that `sha256` only.
4. **Seed.** Run `seed --demo NAME --tenant TENANT --approve SHA256`. It returns
   `waiting-for-sign-in` (exit 3) with `codeFile`, `verificationUri` and `account`: tell the
   user to open `codeFile` themselves (`cat <path>` in their terminal, or open it in their
   editor) and to sign in at `verificationUri` as `account` within the time limit. Never read
   the file yourself.
5. **Monitor.** Run `monitor --run ID --wait 600` repeatedly and summarize `step` and
   `progress` briefly. Team channel posts can wait several minutes for Teams to add members,
   and Teams or Exchange calls up to about twenty minutes in total for the run's Conditional
   Access changes to reach them (the log says so); that is expected. On `failed`, relay
   `result.message` and `next`; on `interrupted` or `needs-restore`, restore before anything
   else.
6. **Report honestly.** On `finished`, report `result.outcome`, any `failures` and
   `signInFailures`, confirm `tenant.phase` is `restored`, and give the presenter guide's
   path (`result.guide`). Say that Microsoft 365 indexes new content over minutes to hours,
   so seeded is not ready; suggest trying the guide's prompts later. If items failed, offer
   `resume` (with Gate 0 already passed).

## Work IQ check and cleanup

Read the playbook fully before starting either; never run them from memory.

- **Check Work IQ** (optional, read-only, after indexing has had time):
  [references/workiq-check.md](references/workiq-check.md).
- **Clean up the tenant** (Gate 0, then **Gate 2**: the user confirms the cleanup plan by its
  `sha256`), and **clean up this computer** (an inventory first, then the user's confirmation):
  [references/cleanup.md](references/cleanup.md).

## Demo users

Demo users are existing, licensed accounts in the demo tenant (two to eight per demo). The
tool signs them in through the demo app as a public client - user name and password, no
client secret - and only to create their own demo content.

`users check` reports, per user, `status` and `passwordValid`:

| `status` | Meaning and next step |
| --- | --- |
| `ok` | Signed in; `licenses` shows the Exchange, SharePoint and Teams plans. |
| `ok-pending-mfa` | The password is valid; MFA or Conditional Access blocks password sign-in now, including Entra asking the user to register for MFA and blocking that registration as risky (AADSTS53004). A run relaxes both for the demo users only, so this passes then. |
| `bad-password`, `password-expired`, `no-password` | The user reruns `users set` for that account in their own terminal (or resets the password in the tenant first). |
| `not-found`, `disabled`, `locked` | Fix the account in the tenant, or correct the demo's people. |
| `consent-needed` | An administrator grants (or completes) the demo app's consent: relay `next`. |
| `app-not-found`, `app-not-public-client` | The demo app is missing or misconfigured: rerun the runner's `m365-demo` phase. |

A user with a missing or disabled plan cannot receive content for that workload: assign a
license with the plan enabled. Pending plans usually finish provisioning within hours.

## Local state

Each run keeps `run.json`, `status.json`, `log.txt`, `manifest.json` (every created item),
`tenant_state.json` (the temporary tenant changes and their restore), `result.json` and
`guide.html` under `runs/<run id>/`. Read the log for diagnosis; it never contains passwords,
tokens or sign-in codes.

`demo-users.json` holds the demo users' passwords in plain text in an owner-only file (0600
in a 0700 folder on macOS and Linux; the user profile's permissions on Windows). Anyone who
can act as this operating-system account can read it, so use dedicated demo accounts whose
passwords protect nothing else, and remove them with `users clear` when done. The
administrator's tokens are never stored: each run signs in and keeps them in memory only.

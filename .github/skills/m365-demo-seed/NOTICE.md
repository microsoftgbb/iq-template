# Notice

## Attribution

This skill is inspired by **DemoIQ**, created by Microsoft to seed realistic, fictional
content into Microsoft 365 demo tenants, and by its DemoIQ-Scout skill. Credit for the idea
and its operating model belongs to the DemoIQ authors: a scenario-driven story told through
mail, Teams, meetings and Office files; demo users signing in to create their own content;
temporarily relaxing MFA and Conditional Access for the demo users and restoring the
original settings afterwards, never stripping them; an honest distinction between seeded
and ready; and a presenter guide. To learn more about DemoIQ, visit
**[aka.ms/demoiq](https://aka.ms/demoiq)**.

## What this is

A separate implementation for **GitHub Copilot App and GitHub Copilot CLI only**, on
Windows, macOS and Linux, on top of this repository's deployment. It is not an official
DemoIQ release and contains no DemoIQ code, engine, Config Builder or generated script:

| Area | This skill |
| --- | --- |
| Runtime | One Python 3.10+ tool using only the standard library, calling Microsoft Entra and Microsoft Graph directly. No PowerShell, Node.js, Graph SDK or generated seeder. |
| Identity | The deploy-platform runner's optional `m365-demo` phase registers a delegated-only, public-client app through Terraform (`infra/m365_demo.tf`). No application permissions, certificate or secret exist. |
| Administrator | Signs in with a device code for each run, in the run's own process; tokens stay in memory and the one-time code goes only to an owner-only file. |
| Demo users | The user types their passwords, masked, in their own terminal; the tool stores them in one owner-only file. |
| Cleanup | Deletes exactly what a run's manifest recorded, each item as its owner (the team and group chats as the administrator); one-on-one chats keep their earlier messages. |
| Work IQ check | The agent asks Work IQ through its MCP server as a demo user; the tool picks the demo's prompts and scores the citations. |
| Telemetry | None. |

Report problems with this skill in this repository; for DemoIQ itself, start at
[aka.ms/demoiq](https://aka.ms/demoiq).

## Validation

The offline suite runs the tool against a fake Entra and Microsoft 365 (Graph) server on
127.0.0.1 that enforces Security Defaults, per-user MFA and Conditional Access on password
sign-in, delivers mail between mailboxes, and lets groups, teams, chats, channel rosters and
OneDrive lag the way a new tenant does, and can drop a connection after processing a request.
It covers sign-ins, tenant preparation and its exact restore (including interrupted and refused
steps), every content type, resume without duplicate mail or groups, cancellation, cleanup
around pre-existing content and re-seeded demos, the Work IQ scoring, local cleanup and
detached runs end to end:

```bash
python3 -B -m unittest discover -s .github/skills/m365-demo-seed/tests
```

The generated Word, Excel and PowerPoint files were also checked with the Open XML SDK
validator (Office 2019 schema) during development; that check needs .NET and is not part of
the suite.

Not yet validated: a real tenant (administrator consent and sign-in, the demo users'
public-client sign-in, the temporary tenant changes and their restore, every Graph content and
cleanup call, and opening the generated files in Office) and a complete run on Windows (its
file-sharing retries are tested with mocks only).

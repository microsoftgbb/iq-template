# Checking Work IQ against the seeded content

A seed run proves the content was written. It does not prove that Work IQ answers from it.
This optional check asks Work IQ a few of the demo's own prompts, as one of the seeded demo
users, and records whether the answers cite seeded items. It is the only evidence behind the
`workiq-verified` state, and only for the prompts asked.

The check is read-only: it makes no tenant changes and needs no Gate 0. It is never a gate for
anything else, and the answers never feed anything (not Foundry IQ knowledge, the scenario
folders or the Studio agent).

**Timing.** Microsoft 365 indexes new mail, chats, meetings and files over minutes to hours.
Right after a seed, say so and offer the check for later. A miss shortly after seeding is not
a defect.

## Steps

1. **Preconditions.** `status` reports `seeded`, with `lastRun.runId` as the run. The Work IQ
   MCP server must be available in this session (its tools start with `workiq`, for example
   the ask tool). If it is not, say the check needs the Work IQ MCP server
   ([Copilot tooling](../../../../docs/copilot-tooling.md)), Work IQ consent in the tenant and
   a licensed demo user, and stop.
2. **Pick the prompts:**
   `python3 .github/skills/m365-demo-seed/scripts/m365demo.py workiq prompts --run RUN_ID`.
   It returns up to three of the demo's prompts, Copilot Chat first, each with the demo user
   to ask as (`as`) and the expected citations (`expect`).
3. **Sign in to Work IQ as a demo user.** Work IQ answers as whoever authorized its MCP
   server, so the check means something only as one of `demoUsers`. It never means anything
   as the administrator, who received none of the content. Tell the user which account to
   use: when Work IQ asks them to sign in, use that demo user, not their administrator
   account; if it is already signed in as someone else, sign it out in Copilot's MCP server
   settings first. The user signs in in their own browser, with that user's normal MFA (the
   run restored it). Never ask for, type or relay the demo user's password.
4. **Ask** each prompt with Work IQ's ask tool, verbatim, one at a time. Use read tools only:
   never a Work IQ tool that creates, updates, sends or deletes anything. For each prompt,
   note:
   - `answered`: true when Work IQ answered from work content; false for "I couldn't find",
     a refusal, an authorization error or a timeout.
   - `citations`: the sources Work IQ returned, `{ "title", "url" }` exactly as given. Never
     invent or reconstruct a citation.

   Also note the account Work IQ reports, or the one the user confirmed, as `askedAs`.
5. **Record.** Write the results with your file tool to a scratch file outside the
   repository, for example in the session folder:

   ```json
   { "askedAs": "<upn>", "answers": [ { "prompt": "<prompt>", "answered": true,
     "citations": [ { "title": "<title>", "url": "<url>" } ] } ] }
   ```

   Then run `workiq record --run RUN_ID --results <that file>`. It matches the citation
   titles against what the run created (mail subjects, file names, meeting subjects, chat
   topics, team and channel names) and keeps every check in the run's `workiq.json`.
6. **Report** a small table (prompt, answered, seeded citations), worded by `verdict`:
   - `found-seeded-content`: Work IQ cited seeded content for `withSeededCitations` of
     `prompts` prompts, asked as `askedAs`.
   - `answered-without-seeded-citations`: Work IQ answered, but did not cite seeded items yet.
     Indexing may still be catching up; offer to check again later.
   - `not-found`: no answers yet, most often because of indexing; offer to check again later.
   - `askedAsIsDemoUser: false`: the check was not run as a seeded demo user, so it says
     little about the seeded content.

   Rerun `status` and say its summary. Never describe Work IQ, the tenant or the platform as
   ready from this check.

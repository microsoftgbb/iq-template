# Authoring a demo (`demo.json`)

A demo is one JSON file, `~/.copilot/m365-demo-seed/demos/<name>/demo.json`, outside the
repository. It holds the story and every piece of content; it never holds a password.
`demo validate` checks it and names each problem by path (for example
`mail[0].messages[1].from: unknown person 'bob'`); fix the file until it reports no errors.

Start from the template's sample (North Works / South Works) mapped onto the real demo users,
then adapt the story:

```bash
python3 .github/skills/m365-demo-seed/scripts/m365demo.py demo new <name> --admin <admin UPN> \
  --person "<upn>=<Display Name>" --person "<upn>=<Display Name>" ...
```

## Writing good demo content

- **One story, told across surfaces.** Each thread, chat, post, meeting and file should move the
  same storyline forward, so Work IQ can connect them ("what did we decide about Pump B?").
- **Specific facts.** Names, numbers, dates and decisions make answers checkable: "15% of
  volume for three shifts", not "some extra capacity".
- **Cross-reference.** Link files from mail, chats, posts and agendas with `{{file:KEY}}`.
- **Write each prompt's expectations.** `guide.prompts[].expect` lists the file names, mail
  subjects or meeting titles a good answer cites; the presenter guide and Work IQ check use them.
- **Fictional only.** Invent companies, sites, people's roles and figures. Never paste real
  customer data, real people's details or anything confidential. The project's scenario may
  shape the story, but nothing seeded flows back into Foundry IQ knowledge.

## Text

Bodies, chat lines, posts and agendas are plain text. A blank line starts a paragraph; lines
starting with `- ` make a bullet list; `**bold**` is bold; `{{file:KEY}}` becomes a link to
that seeded file (its name, when the file was not created). No other `{{...}}` is allowed.

## Format

All keys are lowercase letters, digits and `-`, starting with a letter. "Person" fields hold a
`people[].key`.

| Field | Rules |
| --- | --- |
| `version` | `1` |
| `title` | Up to 120 characters |
| `summary` | The story in a paragraph, up to 2000 characters; shown in the preview and guide |
| `adminUpn` | The tenant administrator who signs in for each run; never one of the people |
| `timezone` | Optional, default `UTC`; a Windows or IANA name for meeting times |
| `people[]` | 2 to 8 existing, licensed demo users: `key`, `upn`, `name`, optional `role` |
| `files[]` | Up to 20 (see below) |
| `mail[]` | Up to 30 threads (see below) |
| `chats[]` | Up to 20 (see below) |
| `channel` | Optional: one team (see below) |
| `meetings[]` | Up to 20 (see below) |
| `guide.prompts[]` | 1 to 20 demo prompts (see below) |

At least one of files, mail, chats, channel or meetings is required.

### `files[]`

`key`, `type` (`docx`, `xlsx` or `pptx`), `name` (ends with `.<type>`; no `" * : < > ? / \ | # %`),
`owner` (uploads it to their OneDrive), optional `folder` (up to three names separated by `/`),
optional `sharedWith` (people who get edit access and an email invitation). Then by type:

- **docx:** optional `title`; `sections[]` (1 to 30) of optional `heading`, `paragraphs[]` and
  `bullets[]` (at least one paragraph or bullet overall).
- **xlsx:** `sheets[]` (1 to 10) of `name` (up to 31 characters; no `: \ / ? * [ ]`) and `rows[]`
  (1 to 500 rows of up to 30 cells: text, numbers, `true`/`false` or `null`). The first row is the
  header (bold, frozen).
- **pptx:** `slides[]` (1 to 30) of `title` and up to 8 `bullets`.

### `mail[]`

`key`, `subject`, `messages[]` (1 to 10). The first message has `from`, `to` (at least one) and
optional `cc`, all distinct. Later messages are replies to everyone on the thread: they have only
`from` (someone on the thread) and `body`.

### `chats[]`

`key`, `members` (2 to 8), `messages[]` (1 to 50) of `from` (a member) and `text`. Three or more
members make a group chat and need a `topic`; two members make the pair's one-on-one chat, so
each pair appears at most once.

### `channel`

`team` (its name), optional `description`, `owner`, `members` (the others on the team), and
`channels[]` (1 to 5) of `name` (up to 50 characters; `General` is the team's default channel),
optional `description`, and `posts[]` (1 to 30) of `from`, optional `subject`, `text` and up to 20
`replies[]` of `from` and `text`. Everyone posting must be the owner or a member.

### `meetings[]`

`key`, `subject`, `organizer`, `attendees` (1 or more, not the organizer), `day` (business days
from the seed day, -20 to 20; negative is in the past), `start` (`HH:MM`, 24-hour), `minutes`
(15 to 240), `agenda`, and an optional `chat[]` of `from` (an invitee) and `text`, posted to the
Teams meeting chat on a best-effort basis.

### `guide.prompts[]`

`surface` (for example `Microsoft 365 Copilot Chat`, `Outlook`, `Teams`, `Word`, `Excel`), `as`
(the person to sign in as), `prompt`, and `expect[]` (up to 10 names a good answer cites).

## What a seed run creates, and as whom

| Content | Created by | Notes |
| --- | --- | --- |
| Files | Their owner | OneDrive upload, then an edit invitation to `sharedWith` |
| Mail | Each message's author | Real send and reply-all; a reply waits for the earlier message to arrive |
| Chats | Each message's author | The first author creates the chat |
| Team and channels | The administrator | A private Microsoft 365 group turned into a team; the administrator then leaves it |
| Channel posts and replies | Their authors | Waits while Teams adds new members |
| Meetings | The organizer | Teams meetings with invitations; meeting chat lines are best effort |

Everything is created at seeding time: messages and posts carry the time they were sent, while
meetings are scheduled on their relative days.

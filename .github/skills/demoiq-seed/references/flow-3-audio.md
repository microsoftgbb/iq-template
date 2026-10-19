# DemoIQ for GitHub Copilot - Flow 3: Generate / stream meeting audio

> Part of the **demoiq-seed** skill (DemoIQ adapted for GitHub Copilot). Read this file FULLY
> before executing the flow - do not run it from memory. The skill folder is
> `.github/skills/demoiq-seed` (relative to the repository root, the normal working
> directory; elsewhere use the skill's base directory) and `<ws>` (the single canonical
> output home) is `~/.copilot/DemoIQ-Output-Scout` - see SKILL.md "Paths and commands".
> Commands are single lines that run unchanged in bash, zsh and PowerShell: pass the
> absolute paths the scripts print, double-quoted. SKILL.md's hard rules, gates and "Keep
> the run moving" rules apply throughout.

## Flow 3 - Generate / stream meeting audio (menu choice `🎙️ Generate / stream meeting audio`)

Meeting audio - an MP3 of the meeting transcript, synthesized with text to speech on the
platform's Foundry account - is produced by the seeder itself when `meetings` is selected.
There is no separate Speech resource or setup step: the config's `speechResourceId` is that
Foundry account (the deployment's `speech_resource_id`), and the seeder authenticates with
the Azure CLI's Entra token
(`az account get-access-token --resource https://cognitiveservices.azure.com`), so the `az`
account must be signed in to the deployment tenant (`preflight_azure_cli.ps1` checks it before
a run; a meetings run whose `az` session points elsewhere stops before any content - the
`az-tenant-mismatch` failure in Flow 1h) and needs data-plane access on that account (the
deploying user has it). Meeting audio is billable (usage based).
A run that selected meetings but has no MP3 is diagnosed with Flow 1h's MP3 ladder
(`references/flow-1h-seed.md`, 1i); the remedy is resuming that run folder (Flow 1h "Recover
a partial or failed run"), never a re-seed.

> **Transcript vs audio - keep the distinction straight when talking to the user.** The
> meeting TRANSCRIPT text needs NO text to speech: it is agent-authored content that
> the seeder parses and saves to `<run folder>/DemoIQ-Output/meeting_transcript_*.txt` on
> every meetings run. Text to speech gates ONLY the MP3 synthesis (multi-voice neural voices
> reading that transcript). So a meetings run whose audio step fails (a config without
> `speechResourceId`, or an `az` account without data-plane access on the Foundry account)
> still gets meetings + the transcript file - it just gets no narrated MP3. If a user asks "can I get the
> transcript without the audio?", the answer is YES, it already happened - give them the
> transcript file's absolute path. Also
> note: the transcript lands locally only; Graph has no API to attach a transcript to a
> Teams meeting, so it can never appear as an in-meeting Teams transcript.

You can adjust `speechSpeedRate` (0.5-2.0, default 1.2) in the Config Builder, or in the
prefs file before generating.

## Stream the MP3 into a live Teams meeting (RTMP-In)

The streamer is **bundled with this skill**:
`.github/skills/demoiq-seed/scripts/stream_rtmp.ps1` (adopted from the
DemoIQ-CoworkGuide skill, where it was validated end-to-end - having that skill installed
is NOT required). It pushes the MP3 with the exact FFmpeg command the DemoIQ engine
generates. There is **no Graph API for RTMP-In ingest credentials** - the ingest URL +
stream key only exist in the live meeting UI (Production tools), so the **user** reads those
two values from the **Teams web client** in their own browser. Never drive Teams with
browser-automation tools (SKILL.md hard rule 7) - guide the user instead.

Prerequisites (state these up front):
- A completed Build run with a `meeting_transcript_*.mp3` in the run's `DemoIQ-Output`
  (Flow 7's history helper flags runs with a `streamableMp3`).
- **FFmpeg** on PATH (Windows `winget install --id Gyan.FFmpeg -e --source winget`, macOS
  `brew install ffmpeg`, Linux your distribution's `ffmpeg` package - `stream_rtmp.ps1`
  prints the command for this OS when FFmpeg is missing).
- **RTMP-In available** in the tenant: the meeting **ORGANIZER needs a Teams Premium
  license**, and the Teams meeting policy "Allow streaming media input" (RTMP-In) must be on
  (a manual Teams admin center setting DemoIQ does not automate). The license is CONFIRMED
  up front in step 3.1.5 - never send the user through the Teams sign-in dance only to
  discover Production tools is missing. If the license is absent, fall back to playing the
  MP3 via screen-share with audio, or hand the user the FFmpeg command to run manually.

### 3.1. Identify the MP3, the target meeting, AND its organizer (FIRST, before any sign-in)
Resolve all three up front and confirm them with the user BEFORE they open Teams. RTMP-In /
Production tools is **organizer (or co-organizer) only**, so resolving the organizer now
avoids signing in as the wrong demo user and finding no RTMP-In.
1. **Pick the MP3.** List `meeting_transcript_*.mp3` in `<run folder>/DemoIQ-Output`
   and choose the one from the intended run (match by timestamp to the run's other outputs).
2. **Resolve the target meeting.** The audio transcript is always generated about the FIRST
   scenario meeting (the seeder's `scenarioIndex=0` meeting, placed first in the meetings
   array) - that meeting is the canonical target.
3. **Resolve that meeting's ORGANIZER UPN** (from the run's AoP runbook, the authored
   meetings content, or the run log's `[MEETING] OK: '<subject>' by <organizer>` line - the
   grep tool with that pattern on `<run folder>/logs/DemoIQ_*.log`). Read the MP3 name, the
   meeting subject, and the organizer UPN back together and confirm all three. Steps 3.3-3.6
   must run signed in as this exact organizer.

### 3.1.5. Confirm the organizer's Teams Premium license (BEFORE any sign-in)
RTMP-In only appears for organizers holding **Teams Premium** - this pre-check
exists because the gap otherwise surfaces inside the live meeting. This adaptation has no
automated license read for it (the administrator's sign-in is consumed by each seed or
cleanup run, and demo users can sign in only while a run relaxes their MFA), so ask the user
to check it in their own browser: Microsoft 365 admin center > Users > Active users >
`<organizer UPN>` > Licenses and apps (Teams Premium, or a license that includes it).
- **Confirmed** -> proceed to 3.2.
- **Not licensed** -> tell the user plainly: RTMP-In will not appear in this organizer's
  meeting. Offer: (a) pick a meeting organized by a demo user who has Teams Premium,
  (b) assign Teams Premium to the organizer first, or (c) fall back to
  screen-share-with-audio playback of the MP3. Do not continue to 3.3 without an explicit
  choice.
- **Cannot check** -> say the license could not be verified and continue ONLY with the
  caveat that Production tools may be missing in 3.5.

### 3.2. Ensure FFmpeg
Run `ffmpeg -version`. If it is missing, offer this OS's install command (Windows
`winget install --id Gyan.FFmpeg -e --source winget`, macOS `brew install ffmpeg`, Linux your
distribution's `ffmpeg` package, e.g. `sudo apt-get install -y ffmpeg`) and run it once the
user agrees (or let them run it - a `sudo` install is theirs to type); a new terminal process
may be needed before PATH picks it up. (You can preview the exact stream command without
FFmpeg or a live meeting via `stream_rtmp.ps1 ... -WhatIfOnly`.)

### 3.3. The user opens Teams web and signs in as the 3.1 organizer (interactive, MFA)
Ask the user to open `https://teams.microsoft.com` in **their own browser** and sign in as
the **organizer UPN resolved in 3.1** (not the transcript's spoken chair), completing that
demo user's normal MFA (the seed run restored it) - interactive login is expected here; do
NOT attempt to inject a ROPC/Graph token (Teams web needs a real cookie session), never ask
for, type or relay the demo user's password, and never drive this page with
browser-automation tools. After
Teams loads, have them VERIFY the account: open the meeting's **Participants** pane and confirm
the signed-in user shows as **Organizer** (or that **More (...) -> Production tools /
RTMP-In** is available). If not, they switch accounts first.
Account-switch gotcha: navigating to `teams.microsoft.com` after a plain logout often
silently re-signs the SAME cached account via SSO. To actually switch, use the in-app
profile menu -> **Sign out**, then on the account picker choose the correct tile or
**Use another account** and enter the 3.1 organizer's credentials.

### 3.4. Start the meeting (recording + transcription ON)
The user navigates to the meeting in the Teams calendar and starts/joins it, turns **on
recording and transcription** (so Copilot can recap the streamed audio afterwards), and gets
past the lobby and any device-permission prompts.

### 3.5. Enable RTMP-In and read the ingest URL + stream key
In the live meeting the user opens **More (...) -> Production tools (or Custom/RTMP) ->
RTMP-In**, enables it, and pastes the **Ingest/Server URL**
(`rtmp(s)://...rtmpingest.../meeting/<id>`) and the **Stream key** from the panel into chat.
Treat the stream key as a secret - never echo it in full and never write it to a file; if the
user would rather keep it out of chat, hand them the 3.6 command with placeholders to run in
their own terminal instead. The live meeting is the only source: keys rotate per meeting, and
a `streamUrl`/`streamKey` saved in a config is never read (never open a config - SKILL.md Hard
rule 4).

### 3.6. Stream the MP3 into the meeting
```text
pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/stream_rtmp.ps1 -Mp3 "<run folder>/DemoIQ-Output/meeting_transcript_<ts>.mp3" -StreamUrl "<ingest url>" -StreamKey "<stream key>"
```
It runs `ffmpeg -re -i <mp3> -b:a 128k -ar 44100 -acodec aac -threads 4 -f flv <url>/<key>`
(DemoIQ engine parity), playing the audio into the meeting in real time for the MP3's full
length. Run it in the terminal tool's **ASYNC mode** (attached) so it can stream while you
poll its output and post a heartbeat line; it ends when the MP3 finishes (stop that async
command to end early - `Q` or `Ctrl+C` when the user runs it in their own terminal). It
prints the destination with the key masked. It stops with an error that names the problem -
a missing MP3, a URL that does not start with `rtmp://` or `rtmps://`, an empty key, or
FFmpeg not on PATH (then with this OS's install command - see 3.2); any other non-zero exit
is FFmpeg's own.

### 3.7. Stop and verify
When FFmpeg exits, the user stops the recording. The meeting recording/transcript now carries
the streamed audio - have them open Copilot in that meeting and ask for a recap to confirm.
Caveats: the audio enters as a media feed, so recording/transcription MUST be on for Copilot
to use it; if the RTMP-In panel is absent the tenant lacks the entitlement; if the Teams web
UI shifts, help the user find Production tools by description - any URL/key they paste still
works with 3.6.

---

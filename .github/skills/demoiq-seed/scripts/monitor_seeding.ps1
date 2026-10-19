<#
  monitor_seeding.ps1 - LIVE progress snapshot for a running (or just-finished)
  DemoIQ seed, so the agent can surface friendly progress in chat instead of
  leaving the user staring at a silent ~15-20 min wait.

  It is READ-ONLY and NON-INTRUSIVE: it only reads the seeder's own heartbeat
  file and its transcript log - it never attaches to, signals, or disturbs the
  running seeder, makes no tenant calls, and changes nothing.

  Two calling modes:
    - Instant (default, -WaitSeconds 0): one snapshot, returns immediately -
      the original behavior, unchanged.
    - Bounded wait (-WaitSeconds N, recommended N=60): the call BLOCKS up to N
      seconds and does the pacing itself - it re-reads every -PollIntervalSeconds
      and returns EARLY the moment any progress signal changes (new [TAG] line,
      phase/percent/activity/sub-detail/seeded-count movement, status flip) or a
      terminal status (done/partial/failed) appears; else it returns the final
      snapshot at the ceiling. This makes the agent's monitor loop mechanical:
      chain "call -> report one line -> call again in the SAME turn" until the
      status is terminal - the wait IS the next tool call, so polling can never
      silently stop, and no timing judgment is needed.

  Three signals it correlates (all written by the normal background run):
    1. Heartbeat: <OutputFolder>\DemoIQ-Output\.run_seeder_status
       lines are "<ISO-timestamp>`t<STARTED mode=N | STARTED cleanup scope=S | DONE [missing=<n> | failed=<n>] | FAILED <msg>>".
    2. Completion result: <OutputFolder>\DemoIQ-Output\seed_result.json (missingTotal +
       per-component counts, written by the seeder after its retry menu) - the PRIMARY
       partial/done signal; the transcript's PARTIAL banner stays as the regex fallback.
    3. Transcript: the newest <OutputFolder>\logs\DemoIQ_*.log. The seeder's
       phase/step text (Update-Progress) goes to the PowerShell *progress stream*
       which a transcript does NOT capture - so progress is derived from the
       Write-Host "[TAG] ..." lines that DO land in the transcript (the same
       tags the run summary / run_history.ps1 parse). The current phase is the
       furthest-along tag seen (monotonic), mapped to the engine's 14-step plan.

  Liveness: the transcript's last-write age is the truth signal. A STARTED status
  with a log whose age keeps growing past -StalledSeconds is reported status
  "stalled" (likely a killed seeder), because the heartbeat stays "STARTED" if the
  process is killed externally. (Corroborate across two polls a few seconds apart.)

  Inputs:
    -OutputFolder    the folder that holds DemoIQ.ps1 + DemoIQ-Output\ + logs\.
                     Defaults to ~/.copilot/DemoIQ-Output-Scout.
    -LogFile         (optional) explicit transcript path (else newest in logs\).
    -StatusFile      (optional) explicit heartbeat path (else the default above).
    -Lines           (optional) how many recent [TAG] log lines to return (default 8).
    -StalledSeconds  (optional) log-age past which a STARTED run is "stalled" (default 300).
    -SinceTime       (optional) ISO timestamp of THIS launch - ignore anything older.
    -WaitSeconds     (optional) bounded-wait ceiling in seconds (default 0 = instant).
    -PollIntervalSeconds (optional) re-read interval inside the wait (default 5).

  CLEANUP RUNS (launch_seeder.ps1 -Cleanup; run.json kind "cleanup"): the same signals,
  with DemoIQ-Output\cleanup_result.json instead of seed_result.json. kind is "cleanup",
  missingItems counts items NOT removed, cleanupResult summarizes the result file, and
  [PURGE] lines are the deletion step.

  Output: a single compact JSON line:
    { ok, kind, status, statusRaw, mode, phaseStep, phaseTotal, phaseLabel, percent,
      currentActivity, subDetail, missingItems, missingByComponent, elapsed,
      startedAt, finishedAt, logFile, logAgeSec, seeded{...}, lastLines[], hint,
      cleanupResult, waitedSec }
  waitedSec = seconds the call actually blocked (0 in instant mode and on the
  terminal fast-path).
  where status is one of: not-started | running | stalled | done | partial | failed |
  unknown ('partial' = the run finished but missingItems requested items could not be
  seeded - report the Content completion table, never a clean DONE).
  missingByComponent (partial only, else null) = {component: missingCount} for the
  short components from seed_result.json's byComponent - so "only audio is missing"
  is machine-readable and the recovery stays scoped to that component.
  phaseStep/phaseLabel are the FURTHEST step reached (monotonic); currentActivity is
  what the seeder is doing RIGHT NOW (they differ when e.g. files upload before
  channels post), and subDetail carries channel N/M sub-progress so the agent can
  narrate visible movement during the propagation-dominated channel phase.
  Exit code is always 0 on a completed snapshot; non-zero only on a usage error.
#>
param(
  [string]$OutputFolder,
  [string]$LogFile,
  [string]$StatusFile,
  [int]$Lines = 8,
  [int]$StalledSeconds = 300,
  [string]$SinceTime,
  [int]$WaitSeconds = 0,
  [int]$PollIntervalSeconds = 5
)

$ErrorActionPreference = 'Stop'
function Write-Result($obj) { $obj | ConvertTo-Json -Compress -Depth 6 }

# Anchor to the launch the caller just initiated: when -SinceTime is supplied (the ISO
# timestamp of THIS launch), ignore any heartbeat/transcript OLDER than it, so a stale
# prior run (hours/days old) is never reported as the current one - and never as "done".
$sinceDt = $null
if ($SinceTime) { try { $sinceDt = [datetime]::Parse($SinceTime) } catch { $sinceDt = $null } }
$sinceFloor = if ($sinceDt) { $sinceDt.AddSeconds(-10) } else { $null }

if (-not $OutputFolder) { $OutputFolder = Join-Path (Join-Path $HOME '.copilot') 'DemoIQ-Output-Scout' }
if (-not $StatusFile) { $StatusFile = Join-Path $OutputFolder 'DemoIQ-Output\.run_seeder_status' }
$logsFolder = Join-Path $OutputFolder 'logs'

# ---- Snapshot builder ------------------------------------------------------
# The whole one-shot snapshot lives in this function so the bounded-wait loop at
# the bottom can re-take it. The body keeps top-level indentation deliberately
# (it WAS the script's main flow - minimal diff, useful blame). $LogFile and the
# heartbeat/result variables become function-locals on first assignment, so every
# call re-resolves the newest transcript and re-reads everything fresh.
function Get-Snapshot {
# ---- Resolve the transcript: explicit, else newest DemoIQ_*.log -----------
if (-not $LogFile) {
  if (Test-Path $logsFolder) {
    $newest = Get-ChildItem $logsFolder -Filter 'DemoIQ_*.log' -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($newest) { $LogFile = $newest.FullName }
  }
}
# Ignore a transcript that predates THIS launch (a stale prior run's log): the current
# run has not produced its transcript yet, so do not let an old one stand in for it.
if ($LogFile -and $sinceFloor -and (Test-Path $LogFile)) {
  if ((Get-Item $LogFile).LastWriteTime -lt $sinceFloor) { $LogFile = $null }
}

# ---- Read the heartbeat ----------------------------------------------------
$statusRaw = $null; $statusStamp = $null; $mode = $null
$hbState = $null   # STARTING | STARTED | DONE | FAILED
$failMsg = $null
$hbMissing = $null   # run_seeder writes "DONE missing=<n>" (seed) or "DONE failed=<n>" (cleanup)
# A cleanup run (launch_seeder.ps1 -Cleanup) is recorded in run.json and the heartbeat.
$runKind = 'seed'
try { if ((Get-Content -LiteralPath (Join-Path $OutputFolder 'run.json') -Raw | ConvertFrom-Json).kind -eq 'cleanup') { $runKind = 'cleanup' } } catch {}
if (Test-Path $StatusFile) {
  try {
    $hb = (Get-Content $StatusFile -Raw -ErrorAction Stop).Trim()
    if ($hb) {
      $parts = $hb -split "`t", 2
      if ($parts.Count -eq 2) { $statusStamp = $parts[0].Trim(); $statusRaw = $parts[1].Trim() } else { $statusRaw = $hb }
      if     ($statusRaw -match '^STARTING') { $hbState = 'STARTING' }
      elseif ($statusRaw -match '^STARTED(?:\s+mode=(\d+))?') { $hbState = 'STARTED'; if ($Matches[1]) { $mode = $Matches[1] }; if ($statusRaw -match '^STARTED cleanup') { $runKind = 'cleanup' } }
      elseif ($statusRaw -match '^DONE(?:\s+(?:missing|failed)=(\d+))?') { $hbState = 'DONE'; if ($Matches[1]) { $hbMissing = [int]$Matches[1] } }
      elseif ($statusRaw -match '^FAILED\s*(.*)$') { $hbState = 'FAILED'; $failMsg = $Matches[1].Trim() }
    }
  } catch {}
}
# Ignore a heartbeat that predates THIS launch (a stale prior run's status file): never
# let an old STARTED/DONE/FAILED masquerade as the run the caller just launched.
if ($hbState -and $sinceFloor -and $statusStamp) {
  try { if ([datetime]::Parse($statusStamp) -lt $sinceFloor) { $hbState = $null; $statusRaw = $null; $statusStamp = $null; $mode = $null; $hbMissing = $null } } catch {}
}

# ---- Read the seeder's machine-readable completion result ------------------
# The seeder persists DemoIQ-Output\seed_result.json (missingTotal + per-component
# counts, no UPNs) AFTER its retry menu - the PRIMARY partial/done signal. The
# heartbeat's "DONE missing=<n>" payload is the second signal; the transcript's
# PARTIAL banner line stays as the regex fallback for older seeders.
$srMissing = $null
$srByComponent = $null
$srFile = Join-Path $OutputFolder 'DemoIQ-Output\seed_result.json'
if (Test-Path -LiteralPath $srFile) {
  $srStale = $false
  if ($sinceFloor) { try { if ((Get-Item -LiteralPath $srFile).LastWriteTime -lt $sinceFloor) { $srStale = $true } } catch {} }
  if (-not $srStale) {
    try {
      $srObj = Get-Content -LiteralPath $srFile -Raw -ErrorAction Stop | ConvertFrom-Json
      $srMissing = [int]$srObj.missingTotal
      # Surface WHICH components are short ({name: missingCount}, non-zero only):
      # "only audio is missing" must be machine-readable from this JSON, not a
      # transcript dig - an over-broad reading of a partial is how a wrong
      # recovery (e.g. a re-seed) gets proposed.
      if ($srObj.byComponent) {
        $bc = [ordered]@{}
        foreach ($p in $srObj.byComponent.PSObject.Properties) {
          $m = 0; try { $m = [int]$p.Value.missing } catch {}
          if ($m -gt 0) { $bc[$p.Name] = $m }
        }
        if ($bc.Count) { $srByComponent = $bc }
      }
    } catch { $srMissing = $null; $srByComponent = $null }
  }
}

# ---- Cleanup runs: cleanup_result.json is the completion result ---------------
$cuSummary = $null
if ($runKind -eq 'cleanup') {
  $cuFile = Join-Path $OutputFolder 'DemoIQ-Output\cleanup_result.json'
  if (Test-Path -LiteralPath $cuFile) {
    $cuStale = $false
    if ($sinceFloor) { try { if ((Get-Item -LiteralPath $cuFile).LastWriteTime -lt $sinceFloor) { $cuStale = $true } } catch {} }
    if (-not $cuStale) {
      try {
        $cu = Get-Content -LiteralPath $cuFile -Raw -ErrorAction Stop | ConvertFrom-Json
        $cuSummary = [ordered]@{ ok = [bool]$cu.ok; scope = $cu.scope; runsTargeted = $cu.runsTargeted; failedTotal = [int]$cu.failedTotal
          tenantRestored = [bool]$cu.tenantRestored; plan = $cu.plan; error = $cu.error; resultFile = $cuFile }
        if ([int]$cu.failedTotal -gt 0) { $srMissing = [int]$cu.failedTotal }
      } catch { $cuSummary = $null }
    }
  }
}

# ---- No log yet: report from the heartbeat alone ---------------------------
if (-not $LogFile -or -not (Test-Path $LogFile)) {
  # Age of the heartbeat itself (how long since the launcher last wrote status).
  $hbAgeSec = $null
  if ($statusStamp) { try { $hbAgeSec = [int]((Get-Date) - [datetime]::Parse($statusStamp)).TotalSeconds } catch {} }
  $st = switch ($hbState) {
    'STARTING' { if ($null -ne $hbAgeSec -and $hbAgeSec -gt $StalledSeconds) { 'not-started' } else { 'running' } }
    'STARTED'  { 'running' }
    'DONE'     { if (($null -ne $srMissing -and $srMissing -gt 0) -or ($null -ne $hbMissing -and $hbMissing -gt 0)) { 'partial' } else { 'done' } }
    'FAILED'   { 'failed' }
    default    { 'not-started' }
  }
  $hint = switch ($st) {
    'not-started' { if ($hbState -eq 'STARTING') { 'A launch began but produced no transcript and has gone quiet - it likely did not take (e.g. the turn was interrupted mid-launch). Re-launch the seeder (Flow 1h) in a SINGLE sync Start-Process call.' } else { 'No run detected yet. Launch the seeder (Flow 1h), then poll again.' } }
    'running'     { 'Seeder just started - the transcript has not appeared yet. Poll again in ~15-30s.' }
    'done'        { 'Seeder reported DONE but no transcript was found in this output folder. Confirm the output folder path.' }
    'partial'     { 'Seeder finished PARTIAL (requested items missing) but no transcript was found in this output folder. Confirm the output folder path and report the missing items from seed_result.json.' }
    'failed'      { "Seeder reported FAILED: $failMsg" }
    default       { 'No transcript and no heartbeat found. Confirm the output folder path.' }
  }
  return @{ ok = $true; kind = $runKind; status = $st; statusRaw = $statusRaw; mode = $mode; cleanupResult = $cuSummary
    phaseStep = 0; phaseTotal = 14; phaseLabel = $null; percent = 0
    missingByComponent = $(if ($st -eq 'partial') { $srByComponent } else { $null })
    elapsed = $null; startedAt = $statusStamp; finishedAt = $null
    logFile = $null; logAgeSec = $null; seeded = @{}; lastLines = @(); hint = $hint }
}

$logItem = Get-Item $LogFile
$logAgeSec = [int]((Get-Date) - $logItem.LastWriteTime).TotalSeconds
$logBody = Get-Content $LogFile -ErrorAction SilentlyContinue
if (-not $logBody) { $logBody = @() }

# ---- Phase map: bracket tag -> { step, label } (engine's 14-step plan) -----
# Several tags map to the same step; current phase = furthest step seen (monotonic).
# NOTE: a plain hashtable (not [ordered]) so integer access is a KEY lookup, not
# a positional index.
$phaseLabels = @{
  1  = 'Connecting to Microsoft Graph'
  2  = 'Preparing tenant (CA/MFA/licensing)'
  3  = 'Granting delegated consent'
  4  = 'Generating/loading content'
  5  = 'Seeding Outlook emails'
  6  = 'Seeding Teams group chat'
  7  = 'Seeding Teams channels'
  8  = 'Uploading Office files'
  9  = 'Seeding Teams meetings'
  10 = 'Generating AoP talk track'
  11 = 'Saving AoP + transcript'
  12 = 'Synthesising meeting audio'
  13 = 'Uploading audio + transcript'
  14 = 'Restoring tenant + cleanup.config'
}
$tagStep = @{
  'SETUP' = 1; 'GRAPH' = 1; 'CONNECT' = 1; 'AUTH' = 1; 'CONFIG' = 1
  'CA' = 2; 'MFA' = 2; 'LICENSE' = 2; 'GROUP' = 2; 'PREP' = 2
  'CONSENT' = 3
  'COPILOT' = 4; 'PURGE' = 4
  'MAIL' = 5; 'ROPC' = 5; 'EMAIL' = 5
  'TEAMS' = 6
  'CHANNEL' = 7
  'WORD' = 8; 'PPT' = 8; 'EXCEL' = 8; 'FILES' = 8
  'MEETING' = 9; 'MEETINGS' = 9; 'MEETING-CHAT' = 9
  'AOP' = 10
  'AUDIO' = 12; 'UPLOAD' = 13
  'CLEANUP' = 14; 'RESTORE' = 14; 'SUMMARY' = 14
}

if ($runKind -eq 'cleanup') { $phaseLabels[4] = 'Deleting the seeded content' }

$maxStep = 0
$lastTag = $null
$chanPosting = $null   # last "(channel N/M)" posting marker seen (sub-step progress)
$lastTagLines = New-Object System.Collections.Generic.List[string]
foreach ($l in $logBody) {
  $m = [regex]::Match($l, '\[([A-Z][A-Z-]*)\]')
  if ($m.Success) {
    $tag = $m.Groups[1].Value
    if ($tagStep.ContainsKey($tag)) { $s = $tagStep[$tag]; if ($s -gt $maxStep) { $maxStep = $s }; $lastTag = $tag }
    # Sub-step marker emitted by the seeder's channel loop: "Posting <n> messages to
    # '<name>' (channel <i>/<total>)". Keeps progress moving during the long Teams
    # roster-propagation phase, where the coarse 14-step percent otherwise freezes.
    $pm = [regex]::Match($l, "Posting (\d+) messages to '([^']+)' \(channel (\d+)/(\d+)\)")
    if ($pm.Success) {
      $chanPosting = @{ msgs = [int]$pm.Groups[1].Value; name = $pm.Groups[2].Value
                        num  = [int]$pm.Groups[3].Value; total = [int]$pm.Groups[4].Value }
    }
    $t = $l.Trim()
    if ($t) { $lastTagLines.Add($t) }
  }
}
$lastLines = @($lastTagLines | Select-Object -Last ([math]::Max(1, $Lines)))

# ---- Terminal-state detection from the transcript --------------------------
$hasSummary = [bool]($logBody | Where-Object { $_ -match 'Run Summary' })
$hasDone    = [bool]($logBody | Where-Object { $_ -match 'Done\. Check Outlook' })
# A PARTIAL run finished normally (no [FATAL]) but left requested content unseeded because
# one or more users' delegated tokens failed and targeted recovery could not fill every item.
# It must NOT be reported as a clean 'done'. Capture the missing count for the hint.
$hasPartial = [bool]($logBody | Where-Object { $_ -match 'PARTIAL: \d+ requested item' })
$partialMiss = $null
$partialM = $logBody | ForEach-Object { $mm = [regex]::Match($_, 'PARTIAL: (\d+) requested item'); if ($mm.Success) { $mm.Groups[1].Value } }
if ($partialM) { $partialMiss = @($partialM)[-1] }
# [CONFIG] FAIL covers the seeder's early-exit paths (config not found, unreadable or
# still encrypted), which end the run without a [FATAL].
$hasFatal   = [bool]($logBody | Where-Object { $_ -match '\[FATAL\]|SEEDING FAILED|Unhandled exception|\[CONFIG\] FAIL' })

# ---- Seeded counts so far (same patterns as run_history.ps1) ---------------
function CountMatch($pattern) { @($logBody | Where-Object { $_ -match $pattern }).Count }
$audio = $null
$audioM = $logBody | ForEach-Object { $mm = [regex]::Match($_, '\[AUDIO\] Synthesized (\d+/\d+) turns'); if ($mm.Success) { $mm.Groups[1].Value } }
if ($audioM) { $audio = @($audioM)[-1] }
$seeded = [ordered]@{
  emails   = CountMatch '\[EMAIL\] OK'
  # Count ONLY per-message chat posts ("[TEAMS] OK: <sender> posted") so authored N messages
  # report N. The broader '[TEAMS] OK' also matches the chat-create/reuse line and the
  # "[TEAMS] OK: Teams chat seeded." summary line, which would inflate the count - exclude them.
  chats    = CountMatch '\[TEAMS\] OK:.*posted'
  channels = CountMatch "\[CHANNEL\] OK: Channel '"
  word     = CountMatch '\[WORD\] OK: Uploaded'
  ppt      = CountMatch '\[PPT\] OK: Uploaded'
  excel    = CountMatch '\[EXCEL\] OK: Uploaded'
  meetings = CountMatch '\[MEETING(S)?\] OK'
  audio    = $audio
  aop      = if ($logBody | Where-Object { $_ -match '\[AOP\] OK|aop_demo_runbook' }) { 'yes' } else { 'no' }
}

# ---- Resolve overall status + percent --------------------------------------
# Partial signals, most deterministic first: seed_result.json (primary), the DONE
# heartbeat's missing=<n> payload, then the transcript's PARTIAL banner regex
# (fallback for seeders that predate seed_result.json). The missing count shown in
# the hint follows the same priority.
$isPartial = ($null -ne $srMissing -and $srMissing -gt 0) -or ($null -ne $hbMissing -and $hbMissing -gt 0) -or $hasPartial
if ($null -ne $srMissing -and $srMissing -gt 0) { $partialMiss = [string]$srMissing }
elseif ($null -ne $hbMissing -and $hbMissing -gt 0) { $partialMiss = [string]$hbMissing }
$status = 'unknown'
if ($hbState -eq 'FAILED' -or $hasFatal) { $status = 'failed' }
elseif ($isPartial -and ($hbState -eq 'DONE' -or $hasSummary)) { $status = 'partial' }
elseif ($hbState -eq 'DONE' -or ($hasSummary -and $hasDone)) { $status = 'done' }
elseif ($hbState -eq 'STARTED' -or $hbState -eq 'STARTING') { $status = if ($logAgeSec -gt $StalledSeconds) { 'stalled' } else { 'running' } }
elseif ($maxStep -gt 0) { $status = if ($logAgeSec -gt $StalledSeconds) { 'stalled' } else { 'running' } }

$_finished = ($status -eq 'done' -or $status -eq 'partial')
$phaseStep = if ($_finished) { 14 } else { $maxStep }
$phaseLabel = if ($phaseStep -ge 1) { $phaseLabels[$phaseStep] } else { $null }
$percent = if ($_finished) { 100 } else { [int][math]::Floor(($phaseStep / 14) * 100) }

# ---- Sub-step detail (what is happening INSIDE the current phase) ----------
# phaseStep is the FURTHEST step seen (monotonic), which can differ from the current
# activity (e.g. Office files [step 8] upload before channels [step 7] post, so the
# label reads "Uploading Office files" while channels are actually posting for many
# minutes). currentActivity reflects the LAST tag; subDetail adds channel N/M progress.
$currentActivity = if ($lastTag -and $tagStep.ContainsKey($lastTag)) { $phaseLabels[$tagStep[$lastTag]] } else { $null }
$chanPosted  = CountMatch '\[CHANNEL\] OK: Posted as'
$chanAllDone = [bool]($logBody | Where-Object { $_ -match '\[CHANNEL\] OK: All channels seeded' })
$subDetail = $null
if ($chanPosting -and -not $chanAllDone) {
  $subDetail = "posting channel $($chanPosting.num)/$($chanPosting.total) ('$($chanPosting.name)', $($chanPosting.msgs) msgs) - $chanPosted channel post(s) landed so far"
} elseif ($chanAllDone -and $lastTag -eq 'CHANNEL') {
  $subDetail = "all channels posted ($chanPosted post(s))"
}
# Let the percent creep across the channel phase (one step is ~7 of the 100) instead of
# freezing for the whole propagation-dominated stretch.
if ($status -ne 'done' -and $chanPosting -and $chanPosting.total -gt 0 -and -not $chanAllDone) {
  $stepSpan = [int][math]::Floor(100 / 14)
  $percent = [math]::Min(99, $percent + [int][math]::Floor((($chanPosting.num - 1) / $chanPosting.total) * $stepSpan))
}

# ---- Elapsed (transcript creation -> now / finish) -------------------------
# The heartbeat is overwritten each phase (STARTED -> DONE/FAILED), so once a run
# finishes its stamp is the FINISH time, not the start. Derive the start from the
# transcript's creation time (the seeder creates the log when it starts).
$running = ($status -eq 'running' -or $status -eq 'stalled')
$startedAt = $null; $finishedAt = $null; $elapsed = $null
try {
  # While running, the heartbeat STARTED stamp is the true start; once finished the
  # heartbeat holds the FINISH time, so fall back to the transcript's creation time.
  $startDt = $logItem.CreationTime
  if ($running -and $statusStamp) { try { $startDt = [datetime]::Parse($statusStamp) } catch {} }
  $startedAt = $startDt.ToString('s')
  $endDt = if ($running) { Get-Date } else { $logItem.LastWriteTime }
  if (-not $running) { if ($statusStamp) { $finishedAt = $statusStamp } else { $finishedAt = $endDt.ToString('s') } }
  $sec = [int]($endDt - $startDt).TotalSeconds
  if ($sec -lt 0) { $sec = 0 }
  $elapsed = '{0:D2}:{1:D2}' -f [int][math]::Floor($sec / 60), ($sec % 60)
} catch {}

# ---- Friendly hint ---------------------------------------------------------
# On a partial, name the short components right in the hint - and when ONLY the
# meeting audio is short, say so and point at the audio-only remedy (the classic
# az-tenant miss must never be answered with a re-seed).
$missText = ''
if ($srByComponent) {
  $missText = ' Missing by component: ' + (@($srByComponent.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ', ') + '.'
  if (@(@($srByComponent.Keys) | Where-Object { $_ -notin @('meetings', 'audio') }).Count -eq 0) {
    $missText += " Only meeting audio is short: diagnose per the flow-1h MP3 ladder (az CLI tenant first, preflight_azure_cli.ps1); the remedy is an audio-only targeted top-up, NEVER a re-seed."
  }
}
$hint = if ($runKind -eq 'cleanup' -and $status -in @('done', 'partial', 'failed')) {
  switch ($status) {
    'done'    { 'Cleanup complete: report what was removed from DemoIQ-Output/cleanup_result.json and the TENANT STATE (post-run) block.' }
    'partial' { "Cleanup finished but $(if($partialMiss){"$partialMiss "})item(s) were not removed. Report them from cleanup_result.json (results.errors, usersWithoutToken, chatsNotFound) and the TENANT STATE (post-run) block - never present it as a complete cleanup." }
    'failed'  { "Cleanup failed$(if($failMsg){": $failMsg"}). Report the [PURGE] and [CLEANUP] lines from the transcript and tenant_state.json: $LogFile" }
  }
} else { switch ($status) {
  'running' {
    $actText = ''
    if ($currentActivity -and $currentActivity -ne $phaseLabel) { $actText = " Currently: $currentActivity." }
    if ($subDetail) { $actText = "$actText Detail: $subDetail." }
    "Step $phaseStep/14: $phaseLabel.$actText Running ~$elapsed so far (typical total ~15-25 min; multi-channel runs spend longest on Teams roster propagation). Last log activity ${logAgeSec}s ago. Report one line, then immediately call the monitor again in the SAME turn (-WaitSeconds 60 does the pacing)."
  }
  'stalled' { "No log activity for ${logAgeSec}s while still STARTED - the seeder may have been killed. Re-check once more a few seconds apart; if the log age keeps growing, treat it as dead and inspect the transcript: $LogFile" }
  'done'    { 'Seed complete. Output the Demo Tenant Content Summary (Flow 1i) and link the deliverables.' }
  'partial' { "Seed finished but $(if($partialMiss){"$partialMiss "})requested item(s) could not be seeded.$missText Output the Demo Tenant Content Summary (Flow 1i) AND report the 'Content completion' table from the transcript with the missing items - do NOT present the run as fully complete: $LogFile" }
  'failed'  { "Seed failed$(if($failMsg){": $failMsg"}). Report the failing [TAG] lines from the transcript: $LogFile" }
  'not-started' { 'No run detected yet.' }
  default   { 'Could not determine run state; inspect the transcript.' }
} }

return @{ ok = $true; kind = $runKind; status = $status; statusRaw = $statusRaw; mode = $mode; cleanupResult = $cuSummary
  phaseStep = $phaseStep; phaseTotal = 14; phaseLabel = $phaseLabel; percent = $percent
  currentActivity = $currentActivity; subDetail = $subDetail
  missingItems = $(if ($status -eq 'partial' -and $partialMiss) { [int]$partialMiss } elseif ($status -eq 'done') { 0 } else { $null })
  missingByComponent = $(if ($status -eq 'partial') { $srByComponent } else { $null })
  elapsed = $elapsed; startedAt = $startedAt; finishedAt = $finishedAt
  logFile = $LogFile; logAgeSec = $logAgeSec; seeded = $seeded; lastLines = $lastLines; hint = $hint }
}

# ---- Bounded-wait loop (the pacing lives HERE, not in the agent) -----------
# Terminal snapshots return immediately regardless of -WaitSeconds. Otherwise:
# fingerprint the first snapshot's PROGRESS signals only (elapsed / logAgeSec /
# startedAt tick on every read and would defeat the early return; the newest
# lastLines entry catches ANY new tag line), then re-snapshot every
# -PollIntervalSeconds until something moves or the ceiling passes. waitedSec
# reports the actual blocked time so the caller can narrate honestly.
function Test-Terminal($s) { return ($s.status -in @('done', 'partial', 'failed')) }
function Get-Fingerprint($s) {
  $lastLine = $null
  if ($s.lastLines) { $arr = @($s.lastLines); if ($arr.Count) { $lastLine = $arr[-1] } }
  return (@{ status = $s.status; phaseStep = $s.phaseStep; percent = $s.percent
             currentActivity = $s.currentActivity; subDetail = $s.subDetail
             missingItems = $s.missingItems; seeded = $s.seeded; lastLine = $lastLine } |
          ConvertTo-Json -Compress -Depth 4)
}

$snap = Get-Snapshot
$waitSw = [System.Diagnostics.Stopwatch]::StartNew()
if ($WaitSeconds -gt 0 -and -not (Test-Terminal $snap)) {
  if ($PollIntervalSeconds -lt 1) { $PollIntervalSeconds = 1 }
  $baseline = Get-Fingerprint $snap
  while ($waitSw.Elapsed.TotalSeconds -lt $WaitSeconds) {
    $left = $WaitSeconds - $waitSw.Elapsed.TotalSeconds
    Start-Sleep -Seconds ([int][math]::Max(1, [math]::Min($PollIntervalSeconds, [math]::Ceiling($left))))
    $snap = Get-Snapshot
    if ((Test-Terminal $snap) -or ((Get-Fingerprint $snap) -ne $baseline)) { break }
  }
}
$snap['waitedSec'] = [int][math]::Round($waitSw.Elapsed.TotalSeconds)
Write-Result $snap
exit 0

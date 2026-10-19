<#
  run_history.ps1 - read-only past-run history for DemoIQ.

  Scans the workspace for DemoIQ run transcripts and reports, for each run: when
  it ran, which tenant it targeted, what content it seeded, and whether it
  succeeded. It reads ONLY existing local files - it makes no tenant calls,
  changes nothing, and needs no credentials.

  Two layouts are scanned (newest first across both):
    1. Per-run folders: <ws>\runs\<runId>\logs\DemoIQ_*.log, each with a
       run.json manifest (runId, scenarioTitle, tenant, configFile) written by
       new_run.ps1/launch_seeder.ps1. Manifest metadata is merged into the run
       record; outcome/counts still come from the transcript (the durable truth
       even for killed runs).
    2. Legacy flat layout: <ws>\logs\DemoIQ_*.log (pre-runs-folder runs).

  Inputs:
    -LogsFolder    (optional) the legacy flat logs folder; its PARENT is treated
                   as the workspace root that holds runs\. Defaults to
                   "$HOME\.copilot\DemoIQ-Output-Scout\logs".
    -Mp3SearchRoot (optional) folder to search for meeting-audio MP3s. Defaults to
                   the parent of the logs folder (the workspace).
    -Limit         (optional) max number of most-recent runs to return (default 50).

  Output: a single JSON object { ok, logsFolder, count, runs[] } where each run
  carries: kind ('seed', or 'cleanup' for a cleanup run that deleted seeded content -
  its outcome comes from DemoIQ-Output/cleanup_result.json and its seeded{} counts are
  zero), when, runId, scenario, runFolder, configFile (null for legacy runs),
  tenant, tenantId, admin, language, mode, elapsed, outcome,
  contentDeclared[], seeded{} counts (emails, meetings, word, ppt, excel,
  channels, audio, aop), meetingSubjects[] (the meetings this run seeded),
  streamableMp3 (local path to this run's meeting-audio MP3, or null) and
  streamableMeeting (the first meeting's subject when an MP3 is available - that
  is the meeting whose audio can be streamed into a Teams meeting - Flow 3).
  Render runs newest-first, grouped by tenant.
#>
param(
  [string]$LogsFolder,
  [string]$Mp3SearchRoot,
  [int]$Limit = 50
)

$ErrorActionPreference = 'Stop'

if (-not $LogsFolder) {
  $LogsFolder = Join-Path $HOME '.copilot\DemoIQ-Output-Scout\logs'
}
# MP3s are searched under the workspace (parent of the logs folder by default), so
# a run can be matched to its locally-available meeting_transcript_*.mp3 for streaming.
if (-not $Mp3SearchRoot) {
  $Mp3SearchRoot = Split-Path -Parent $LogsFolder
  if (-not $Mp3SearchRoot) { $Mp3SearchRoot = $LogsFolder }
}

# The workspace root: runs live under <root>\runs\<runId>\ (each with its own
# run.json manifest + logs\), while the pre-runs-folder layout kept one flat
# <root>\logs. Both are scanned so old runs stay visible.
$wsRoot = Split-Path -Parent $LogsFolder
if (-not $wsRoot) { $wsRoot = $LogsFolder }
$runsRoot = Join-Path $wsRoot 'runs'

if (-not (Test-Path $LogsFolder) -and -not (Test-Path $runsRoot)) {
  @{ ok = $false; error = "No run history found: neither $LogsFolder nor $runsRoot exists."; runs = @() } | ConvertTo-Json -Compress
  exit 0
}

function Get-First($lines, $pattern) {
  foreach ($l in $lines) { $m = [regex]::Match($l, $pattern); if ($m.Success) { return $m.Groups[1].Value.Trim() } }
  return $null
}
function Count-Match($lines, $pattern) {
  ($lines | Where-Object { $_ -match $pattern }).Count
}

# Pre-index meeting-audio MP3s once: path + timestamp parsed from the file name
# (meeting_transcript_YYYYMMDD_HHMMSS.mp3). Used to match each run to its audio.
$mp3Index = @()
if (Test-Path $Mp3SearchRoot) {
  foreach ($m in Get-ChildItem $Mp3SearchRoot -Recurse -Filter 'meeting_transcript_*.mp3' -ErrorAction SilentlyContinue) {
    $mm = [regex]::Match($m.Name, 'meeting_transcript_(\d{8})_(\d{6})')
    $ts = $null
    if ($mm.Success) { try { $ts = [datetime]::ParseExact($mm.Groups[1].Value + $mm.Groups[2].Value, 'yyyyMMddHHmmss', $null) } catch { $ts = $m.LastWriteTime } }
    else { $ts = $m.LastWriteTime }
    $mp3Index += [pscustomobject]@{ path = $m.FullName; ts = $ts }
  }
}

$runs = @()
# Per-run folders first (runs\<runId>\logs\DemoIQ_*.log), then the legacy flat logs
# folder. Log names embed the timestamp, so one global name sort keeps them ordered.
$files = @()
if (Test-Path $runsRoot) {
  foreach ($rf in (Get-ChildItem $runsRoot -Directory -ErrorAction SilentlyContinue)) {
    $ld = Join-Path $rf.FullName 'logs'
    if (Test-Path $ld) { $files += Get-ChildItem $ld -Filter 'DemoIQ_*.log' -File -ErrorAction SilentlyContinue }
  }
}
if (Test-Path $LogsFolder) {
  $files += Get-ChildItem $LogsFolder -Filter 'DemoIQ_*.log' -File -ErrorAction SilentlyContinue
}
$files = $files | Sort-Object Name -Descending
foreach ($f in $files) {
  # A run-folder log lives at runs\<runId>\logs\x.log with the run.json manifest two
  # levels up. Detect by path SHAPE (leaf names), not string equality - 8.3 short
  # paths vs long paths make absolute-path comparisons unreliable on Windows.
  $manifest = $null; $runFolder = $null
  $logDir = Split-Path -Parent $f.FullName
  $maybeRunFolder = Split-Path -Parent $logDir
  if ($maybeRunFolder -and
      (Split-Path -Leaf $logDir) -eq 'logs' -and
      (Split-Path -Leaf (Split-Path -Parent $maybeRunFolder)) -eq 'runs') {
    $runFolder = $maybeRunFolder
    $mfPath = Join-Path $runFolder 'run.json'
    if (Test-Path $mfPath) {
      try { $manifest = Get-Content $mfPath -Raw | ConvertFrom-Json } catch { $manifest = $null }
    }
  }
  $lines = Get-Content $f.FullName -ErrorAction SilentlyContinue
  if (-not $lines) { continue }

  # When: parse the timestamp encoded in the filename (DemoIQ_YYYYMMDD_HHMMSS.log).
  $when = $null
  $tm = [regex]::Match($f.Name, 'DemoIQ_(\d{8})_(\d{6})')
  if ($tm.Success) {
    try {
      $dt = [datetime]::ParseExact($tm.Groups[1].Value + $tm.Groups[2].Value, 'yyyyMMddHHmmss', $null)
      $when = $dt.ToString('yyyy-MM-dd HH:mm')
    } catch { $when = $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm') }
  } else {
    $when = $f.LastWriteTime.ToString('yyyy-MM-dd HH:mm')
  }

  $admin   = Get-First $lines 'Admin=([^,]+)'
  $tenant  = if ($admin -and $admin -match '@') { $admin.Split('@')[1].ToLowerInvariant() } else { $null }
  $tenId   = Get-First $lines 'Tenant:\s*([0-9a-fA-F-]{36})'
  $lang    = Get-First $lines 'Target language:\s*(.+)$'
  $content = Get-First $lines '\[CONFIG\] Content:\s*(.+)$'
  $contentArr = if ($content) { @($content -split ',\s*' | Where-Object { $_ }) } else { @() }
  $mode    = Get-First $lines 'Mode\s*:\s*(.+?)\s*$'
  $elapsed = Get-First $lines 'Elapsed\s*:\s*(.+?)\s*$'

  # Outcome: a completed run writes the Run Summary block and either the closing
  # "Done. Check Outlook..." line or - when requested content is missing - the
  # "PARTIAL: N requested item(s)..." banner. A partial run finished normally (no
  # crash), so classify it 'Partial', not 'Incomplete'. Anything short of a summary
  # is incomplete/failed.
  $hasSummary = [bool]($lines | Where-Object { $_ -match 'Run Summary' })
  $hasDone    = [bool]($lines | Where-Object { $_ -match 'Done\. Check Outlook' })
  $hasPartial = [bool]($lines | Where-Object { $_ -match 'PARTIAL: \d+ requested item' })
  $hasFatal   = [bool]($lines | Where-Object { $_ -match '\[FATAL\]|SEEDING FAILED|Unhandled exception' })
  $outcome = if ($hasSummary -and $hasPartial) { 'Partial' }
             elseif ($hasSummary -and $hasDone) { 'Succeeded' }
             elseif ($hasFatal) { 'Failed' }
             else { 'Incomplete' }
  # A cleanup run deletes instead of seeding; its result file is the outcome.
  $kind = if ($manifest -and $manifest.kind -eq 'cleanup') { 'cleanup' } else { 'seed' }
  $cleanup = $null
  if ($kind -eq 'cleanup') {
    try { $cleanup = Get-Content -LiteralPath (Join-Path $runFolder 'DemoIQ-Output/cleanup_result.json') -Raw | ConvertFrom-Json } catch {}
    $outcome = if ($cleanup -and $cleanup.ok -and [int]$cleanup.failedTotal -eq 0) { 'Succeeded' }
               elseif ($cleanup -and $cleanup.ok) { 'Partial' }
               elseif ($cleanup -or $hasFatal) { 'Failed' }
               else { 'Incomplete' }
  }

  # Audio is reported as "Synthesized N/M turns" - take the last (final tally).
  $audio = $null
  $audioMatches = $lines | ForEach-Object { $m = [regex]::Match($_, '\[AUDIO\] Synthesized (\d+/\d+) turns'); if ($m.Success) { $m.Groups[1].Value } }
  if ($audioMatches) { $audio = @($audioMatches)[-1] }

  # Meeting subjects seeded by this run (from [MEETING] OK: '<subject>' by <organizer>).
  $meetingList = @()
  foreach ($l in $lines) {
    $mm = [regex]::Match($l, "\[MEETING(?:S)?\] OK:\s*'([^']+)'\s*by\s*(\S+)")
    if ($mm.Success) { $meetingList += [pscustomobject]@{ subject = $mm.Groups[1].Value.Trim(); organizer = $mm.Groups[2].Value.Trim() } }
  }

  # Run time window: start from the filename timestamp; end from the Run Summary
  # "End : ..." line, else the file's last-write time. Used to match the run's MP3.
  $runStart = $null
  if ($tm.Success) { try { $runStart = [datetime]::ParseExact($tm.Groups[1].Value + $tm.Groups[2].Value, 'yyyyMMddHHmmss', $null) } catch { $runStart = $f.LastWriteTime } }
  else { $runStart = $f.LastWriteTime }
  $endStr = Get-First $lines 'End\s*:\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'
  $runEnd = $null
  if ($endStr) { try { $runEnd = [datetime]::ParseExact($endStr, 'yyyy-MM-dd HH:mm:ss', $null) } catch {} }
  if (-not $runEnd) { $runEnd = $f.LastWriteTime }
  # Cap the matching window so an unrelated later MP3 can't be misattributed.
  $windowEnd = $runEnd.AddMinutes(10)
  $maxEnd = $runStart.AddHours(2)
  if ($windowEnd -gt $maxEnd) { $windowEnd = $maxEnd }

  # The meeting audio transcript is always about the FIRST seeded meeting, so a
  # matched MP3 is the streamable audio for that first meeting.
  $streamMp3 = $null
  # A per-run folder owns its outputs, so its MP3 is an exact match - no
  # timestamp-window guessing needed. Legacy flat-layout runs fall back to the
  # windowed index match below.
  if ($runFolder) {
    $ownMp3 = Get-ChildItem $runFolder -Recurse -Filter 'meeting_transcript_*.mp3' -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime | Select-Object -First 1
    if ($ownMp3) { $streamMp3 = $ownMp3.FullName }
  }
  if (-not $streamMp3 -and $mp3Index.Count -gt 0) {
    $hit = $mp3Index | Where-Object { $_.ts -ge $runStart -and $_.ts -le $windowEnd } | Sort-Object ts | Select-Object -First 1
    if ($hit) { $streamMp3 = $hit.path }
  }
  $streamMeeting = if ($meetingList.Count -gt 0) { $meetingList[0].subject } else { $null }

  $seeded = [ordered]@{
    emails   = Count-Match $lines '\[EMAIL\] OK'
    meetings = Count-Match $lines '\[MEETING(S)?\] OK'
    word     = Count-Match $lines '\[WORD\] OK: Uploaded'
    ppt      = Count-Match $lines '\[PPT\] OK: Uploaded'
    excel    = Count-Match $lines '\[EXCEL\] OK: Uploaded'
    channels = Count-Match $lines "\[CHANNEL\] OK: Channel '"
    audio    = $audio
    aop      = if ($lines | Where-Object { $_ -match '\[AOP\] OK|aop_demo_runbook' }) { 'yes' } else { 'no' }
  }

  $runs += [ordered]@{
    kind             = $kind
    cleanupScope     = $(if ($cleanup) { $cleanup.scope } else { $null })
    itemsNotRemoved  = $(if ($cleanup) { [int]$cleanup.failedTotal } else { $null })
    when             = $when
    runId            = $(if ($manifest -and $manifest.runId) { $manifest.runId } else { $null })
    scenario         = $(if ($manifest -and $manifest.scenarioTitle) { $manifest.scenarioTitle } else { $null })
    runFolder        = $runFolder
    configFile       = $(if ($manifest -and $manifest.configFile) { $manifest.configFile } else { $null })
    tenant           = $tenant
    tenantId         = $tenId
    admin            = $admin
    language         = $lang
    mode             = $mode
    elapsed          = $elapsed
    outcome          = $outcome
    contentDeclared  = $contentArr
    seeded           = $seeded
    meetingSubjects  = @($meetingList | ForEach-Object { $_.subject })
    streamableMp3    = $streamMp3
    streamableMeeting = $(if ($streamMp3) { $streamMeeting } else { $null })
    log              = $f.Name
  }
}

# Newest-first (filenames already sorted descending = chronological desc).
if ($runs.Count -gt $Limit) { $runs = $runs[0..($Limit - 1)] }

@{ ok = $true; logsFolder = $LogsFolder; count = $runs.Count; runs = $runs } |
  ConvertTo-Json -Compress -Depth 6
exit 0

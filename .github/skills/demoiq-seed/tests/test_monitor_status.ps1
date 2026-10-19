# test_monitor_status.ps1 - tests for the PARTIAL-state plumbing outside
# the seeder: monitor_seeding.ps1's terminal-state detection (done / partial / failed,
# with seed_result.json and the heartbeat "DONE missing=<n>" payload as the primary
# partial signals and the transcript banner as fallback), its bounded-wait mode
# (-WaitSeconds: terminal fast-path, ceiling hold, early return on new activity),
# and run_history.ps1's outcome classification (Succeeded / Partial / Incomplete /
# Failed).
#
# Builds synthetic run folders (heartbeat + transcript + optional seed_result.json)
# in a temp dir and asserts the scripts' emitted JSON. Both scripts are read-only;
# no tenant is contacted and no engine generation is needed. Run via run_tests.ps1.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$monitor = Join-Path $repo 'scripts\monitor_seeding.ps1'
$history = Join-Path $repo 'scripts\run_history.ps1'
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("demoiq-monitor-test-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null

$script:pass = 0; $script:fail = 0
function Assert-True([bool]$cond, [string]$name) {
  if ($cond) { $script:pass++; Write-Host "  PASS $name" -ForegroundColor Green }
  else { $script:fail++; Write-Host "  FAIL $name" -ForegroundColor Red }
}

function New-RunFolder([string]$name) {
  $rf = Join-Path $work $name
  New-Item -ItemType Directory -Force (Join-Path $rf 'logs') | Out-Null
  New-Item -ItemType Directory -Force (Join-Path $rf 'DemoIQ-Output') | Out-Null
  return $rf
}
function Write-Heartbeat([string]$rf, [string]$state) {
  Set-Content -Path (Join-Path $rf 'DemoIQ-Output\.run_seeder_status') -Value ("{0}`t{1}" -f (Get-Date -Format 's'), $state) -Encoding UTF8
}
function Write-Transcript([string]$rf, [string[]]$lines, [string]$stamp = $null) {
  if (-not $stamp) { $stamp = Get-Date -Format 'yyyyMMdd_HHmmss' }
  $log = Join-Path $rf ('logs\DemoIQ_' + $stamp + '.log')
  Set-Content -Path $log -Value ($lines -join "`r`n") -Encoding UTF8
  return $log
}
function Get-MonitorSnapshot([string]$rf) {
  $json = & pwsh -NoProfile -ExecutionPolicy Bypass -File $monitor -OutputFolder $rf
  return ($json | ConvertFrom-Json)
}

try {
  # ============================================================
  # 1. DONE: Run Summary + Done banner + plain DONE heartbeat -> status 'done'
  # ============================================================
  $rf = New-RunFolder 'run-done'
  $null = Write-Transcript $rf @(
    '[EMAIL] OK: Sent from a@x',
    ' DemoIQ - Run Summary',
    ' Done. Check Outlook, Teams, and OneDrive for seeded content.')
  Write-Heartbeat $rf 'DONE'
  $m = Get-MonitorSnapshot $rf
  Assert-True ($m.status -eq 'done') 'done: clean run reports status done'
  Assert-True ($m.missingItems -eq 0) 'done: missingItems is 0'
  Assert-True ($m.percent -eq 100) 'done: percent 100'

  # ============================================================
  # 2. PARTIAL via the transcript banner (fallback path: no seed_result.json,
  #    no heartbeat payload - e.g. an older seeder).
  # ============================================================
  $rf = New-RunFolder 'run-partial-banner'
  $null = Write-Transcript $rf @(
    '[TEAMS] WARN: No token for b@x - skipped.',
    ' DemoIQ - Run Summary',
    ' PARTIAL: 3 requested item(s) could not be seeded.')
  Write-Heartbeat $rf 'DONE'
  $m = Get-MonitorSnapshot $rf
  Assert-True ($m.status -eq 'partial') 'partial(banner): reported partial, not done'
  Assert-True ($m.missingItems -eq 3) 'partial(banner): missingItems from the banner line'
  Assert-True ($m.hint -match '3 requested item') 'partial(banner): hint carries the missing count'

  # ============================================================
  # 3. PARTIAL via seed_result.json + heartbeat payload (PRIMARY path): the
  #    transcript deliberately has NO PARTIAL line (and even a Done banner) -
  #    the deterministic signals must win without the regex.
  # ============================================================
  $rf = New-RunFolder 'run-partial-result'
  $null = Write-Transcript $rf @(
    ' DemoIQ - Run Summary',
    ' Done. Check Outlook, Teams, and OneDrive for seeded content.')
  Set-Content -Path (Join-Path $rf 'DemoIQ-Output\seed_result.json') -Encoding UTF8 -Value (
    (@{ missingTotal = 2; byComponent = @{ chatMessage = @{ requested = 4; seeded = 2; missing = 2 } } } | ConvertTo-Json -Compress -Depth 4))
  Write-Heartbeat $rf 'DONE missing=2'
  $m = Get-MonitorSnapshot $rf
  Assert-True ($m.status -eq 'partial') 'partial(seed_result): reported partial with no banner line in the transcript'
  Assert-True ($m.missingItems -eq 2) 'partial(seed_result): missingItems from seed_result.json'
  Assert-True ($m.missingByComponent.chatMessage -eq 2) 'partial(seed_result): missingByComponent surfaces the short component'

  # ============================================================
  # 3b. PARTIAL where ONLY audio is short: missingByComponent names it and the
  #     hint prescribes the audio-only remedy (never a re-seed).
  # ============================================================
  $rf = New-RunFolder 'run-partial-audio'
  $null = Write-Transcript $rf @(
    ' DemoIQ - Run Summary',
    ' Done. Check Outlook, Teams, and OneDrive for seeded content.')
  Set-Content -Path (Join-Path $rf 'DemoIQ-Output\seed_result.json') -Encoding UTF8 -Value (
    (@{ missingTotal = 1; byComponent = @{ audio = @{ requested = 1; seeded = 0; missing = 1 }; word = @{ requested = 2; seeded = 2; missing = 0 } } } | ConvertTo-Json -Compress -Depth 4))
  Write-Heartbeat $rf 'DONE missing=1'
  $m = Get-MonitorSnapshot $rf
  Assert-True ($m.status -eq 'partial' -and $m.missingByComponent.audio -eq 1) 'partial(audio): audio miss is machine-readable'
  Assert-True ($null -eq $m.missingByComponent.word) 'partial(audio): zero-miss components are omitted'
  Assert-True ($m.hint -match 'audio-only targeted top-up' -and $m.hint -match 'NEVER a re-seed') 'partial(audio): hint prescribes the scoped remedy'

  # ============================================================
  # 4. PARTIAL via the heartbeat payload alone (no seed_result.json, no banner).
  # ============================================================
  $rf = New-RunFolder 'run-partial-hb'
  $null = Write-Transcript $rf @(' DemoIQ - Run Summary')
  Write-Heartbeat $rf 'DONE missing=5'
  $m = Get-MonitorSnapshot $rf
  Assert-True ($m.status -eq 'partial' -and $m.missingItems -eq 5) 'partial(heartbeat): DONE missing=5 payload alone flags partial'

  # ============================================================
  # 5. FAILED: [FATAL] in the transcript + FAILED heartbeat.
  # ============================================================
  $rf = New-RunFolder 'run-failed'
  $null = Write-Transcript $rf @('[FATAL] SEEDING FAILED: admin token dead')
  Write-Heartbeat $rf 'FAILED admin-session-failed: the administrator sign-in expired or was revoked'
  $m = Get-MonitorSnapshot $rf
  Assert-True ($m.status -eq 'failed') 'failed: fatal run reports status failed'

  # ============================================================
  # 6. RUNNING sanity: STARTED heartbeat + fresh log -> running (not partial/done).
  # ============================================================
  $rf = New-RunFolder 'run-running'
  $null = Write-Transcript $rf @('[EMAIL] OK: Sent from a@x')
  Write-Heartbeat $rf 'STARTED mode=1'
  $m = Get-MonitorSnapshot $rf
  Assert-True ($m.status -eq 'running') 'running: in-flight run stays running'
  Assert-True ($null -eq $m.missingItems) 'running: missingItems is null while in flight'

  # ============================================================
  # 7. Bounded-wait mode (-WaitSeconds): the pacing lives in the script, so the
  #    agent's loop is "call -> report -> call" with no timing judgment.
  # ============================================================
  function Invoke-MonitorWait([string]$rf, [int]$wait) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $json = & pwsh -NoProfile -ExecutionPolicy Bypass -File $monitor -OutputFolder $rf -WaitSeconds $wait -PollIntervalSeconds 1
    $sw.Stop()
    return @{ m = ($json | ConvertFrom-Json); sec = $sw.Elapsed.TotalSeconds }
  }
  # 7a. Terminal fast-path: a done run returns immediately, ignoring the ceiling.
  $r = Invoke-MonitorWait (Join-Path $work 'run-done') 20
  Assert-True ($r.m.status -eq 'done' -and $r.sec -lt 10) 'wait: terminal status returns immediately, ignoring the ceiling'
  Assert-True ($r.m.waitedSec -le 2) 'wait: terminal fast-path reports ~0 waitedSec'
  # 7b. Quiet run: nothing changes, so the call holds until the ceiling.
  $rf = New-RunFolder 'run-wait-quiet'
  $null = Write-Transcript $rf @('[EMAIL] OK: Sent from a@x')
  Write-Heartbeat $rf 'STARTED mode=1'
  $r = Invoke-MonitorWait $rf 4
  Assert-True ($r.m.status -eq 'running' -and $r.sec -ge 3) 'wait: quiet run holds the call until the ceiling'
  Assert-True ($r.m.waitedSec -ge 3) 'wait: waitedSec reports the blocked time'
  # 7c. Movement mid-wait: a new [TAG] line lands, the call returns EARLY with it.
  $rf = New-RunFolder 'run-wait-move'
  $log = Write-Transcript $rf @('[EMAIL] OK: Sent from a@x')
  Write-Heartbeat $rf 'STARTED mode=1'
  $moveOut = Join-Path $work 'wait-move.json'
  $hidden = if ($IsWindows) { @{ WindowStyle = 'Hidden' } } else { @{} }   # -WindowStyle is Windows-only
  $mon = Start-Process pwsh -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $monitor,
    '-OutputFolder', $rf, '-WaitSeconds', '25', '-PollIntervalSeconds', '1') `
    -RedirectStandardOutput $moveOut -PassThru @hidden
  Start-Sleep -Seconds 3
  Add-Content -Path $log -Value '[TEAMS] OK: alice@x posted 1/4'
  $exited = $mon.WaitForExit(15000)
  Assert-True $exited 'wait: monitor returned early after new log activity'
  if ($exited) {
    $m = Get-Content $moveOut -Raw | ConvertFrom-Json
    Assert-True ($m.status -eq 'running' -and $m.seeded.chats -eq 1) 'wait: early-return snapshot includes the new activity'
    Assert-True ($m.waitedSec -lt 20) 'wait: returned before the 25s ceiling'
  } else { try { $mon.Kill() } catch {} }

  # ============================================================
  # 8. run_history.ps1 outcome classification: Succeeded / Partial / Incomplete /
  #    Failed from the same transcript shapes (per-run folder layout).
  # ============================================================
  $ws = Join-Path $work 'ws'
  New-Item -ItemType Directory -Force (Join-Path $ws 'logs') | Out-Null
  $runSpecs = @(
    @{ name = '20260721_100000__succ__t1'; stamp = '20260721_100000'; lines = @(' DemoIQ - Run Summary', ' Done. Check Outlook, Teams, and OneDrive for seeded content.') },
    @{ name = '20260721_110000__part__t1'; stamp = '20260721_110000'; lines = @(' DemoIQ - Run Summary', ' PARTIAL: 5 requested item(s) could not be seeded.') },
    @{ name = '20260721_120000__incomp__t1'; stamp = '20260721_120000'; lines = @('[EMAIL] OK: Sent from a@x') },
    @{ name = '20260721_130000__fail__t1'; stamp = '20260721_130000'; lines = @('[FATAL] SEEDING FAILED: boom') })
  foreach ($spec in $runSpecs) {
    $rrf = Join-Path (Join-Path $ws 'runs') $spec.name
    New-Item -ItemType Directory -Force (Join-Path $rrf 'logs') | Out-Null
    Set-Content -Path (Join-Path $rrf ('logs\DemoIQ_' + $spec.stamp + '.log')) -Value ($spec.lines -join "`r`n") -Encoding UTF8
  }
  $hj = (& pwsh -NoProfile -ExecutionPolicy Bypass -File $history -LogsFolder (Join-Path $ws 'logs')) | ConvertFrom-Json
  Assert-True ($hj.ok -and $hj.count -eq 4) 'history: all four synthetic runs discovered'
  function Get-OutcomeFor([string]$leaf) {
    $r = @($hj.runs | Where-Object { $_.runFolder -and (Split-Path -Leaf $_.runFolder) -eq $leaf }) | Select-Object -First 1
    if ($r) { return [string]$r.outcome } else { return $null }
  }
  Assert-True ((Get-OutcomeFor '20260721_100000__succ__t1') -eq 'Succeeded') 'history: clean run -> Succeeded'
  Assert-True ((Get-OutcomeFor '20260721_110000__part__t1') -eq 'Partial') 'history: PARTIAL banner run -> Partial (not Incomplete)'
  Assert-True ((Get-OutcomeFor '20260721_120000__incomp__t1') -eq 'Incomplete') 'history: summary-less run -> Incomplete'
  Assert-True ((Get-OutcomeFor '20260721_130000__fail__t1') -eq 'Failed') 'history: fatal run -> Failed'
}
finally {
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host ("monitor-status: {0} passed, {1} failed" -f $script:pass, $script:fail) -ForegroundColor ($(if ($script:fail) { 'Red' } else { 'Green' }))
exit $(if ($script:fail) { 1 } else { 0 })

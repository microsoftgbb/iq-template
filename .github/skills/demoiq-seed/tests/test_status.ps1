# test_status.ps1 - scripts/status.ps1, the honest readiness report, offline.
#
# Builds a synthetic workspace (config library, run folders with heartbeats,
# tenant_state.json, cleanup_result.json, workiq_check.json) and saved receipts, then
# checks each state. "Seeded" must never read as ready; a run that stopped before its
# restore step must win over everything but a live run. No tenant, no network.
$ErrorActionPreference = 'Stop'

# The session key lives under $HOME on macOS/Linux: re-launch under a throwaway HOME.
if ($env:DEMOIQ_TEST_ISOLATED -ne '1' -and -not $IsWindows) {
  $iso = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-status-iso-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
  New-Item -ItemType Directory -Force (Join-Path $iso 'home') | Out-Null
  $env:HOME = Join-Path $iso 'home'; $env:DEMOIQ_TEST_ISOLATED = '1'
  & (Get-Process -Id $PID).Path -NoProfile -File $PSCommandPath
  $code = $LASTEXITCODE
  Remove-Item -LiteralPath $iso -Recurse -Force -ErrorAction SilentlyContinue
  exit $code
}

$script:fails = 0
function Check([string]$name, $cond) {
  if ($cond) { Write-Host "  PASS $name" -ForegroundColor Green } else { Write-Host "  FAIL $name" -ForegroundColor Red; $script:fails++ }
}

$S = Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts'
$status = Join-Path $S 'status.ps1'
$pwshExe = (Get-Process -Id $PID).Path
$root = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-status-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$ws = Join-Path $root 'ws'
New-Item -ItemType Directory -Force (Join-Path $ws 'configs'), (Join-Path $ws 'runs') | Out-Null
$savedSession = $env:DEMOIQ_SESSION_DIR
$env:DEMOIQ_SESSION_DIR = Join-Path $root 'session'

$APP = '0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0'; $TEN = '11111111-2222-3333-4444-555555555555'
function Write-Receipts([string]$Name, $Demoiq) {
  $p = Join-Path $root $Name
  [ordered]@{ ok = $true; demoiq = $Demoiq } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $p -Encoding UTF8
  return $p
}
$receipts = Write-Receipts 'receipts.json' ([ordered]@{ tenant_id = $TEN; client_id = $APP; consent = 'granted'; delegated_scopes = @('User.Read'); speech_resource_id = '/x/accounts/y'; audio = $null; checked_at = '2026-01-01T00:00:00Z' })
$unverified = Write-Receipts 'receipts-unverified.json' ([ordered]@{ tenant_id = $TEN; client_id = $APP; consent = 'unverified' })
$none = Write-Receipts 'receipts-none.json' $null

function Get-Status([string]$Receipts) {
  $raw = & $pwshExe -NoProfile -File $status -OutputFolder $ws -ReceiptsFile $Receipts 2>&1
  $line = @($raw | Where-Object { "$_".Trim().StartsWith('{') }) | Select-Object -Last 1
  return @{ code = $LASTEXITCODE; raw = ($raw -join "`n"); json = $(if ($line) { $line | ConvertFrom-Json } else { $null }) }
}
function New-Run([string]$Name, [string]$Kind, [string]$Heartbeat) {
  $folder = Join-Path (Join-Path $ws 'runs') $Name
  New-Item -ItemType Directory -Force (Join-Path $folder 'DemoIQ-Output') | Out-Null
  [ordered]@{ runId = $Name; createdAt = '2026-01-01T00:00:00'; kind = $Kind } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $folder 'run.json') -Encoding UTF8
  if ($Heartbeat) { Set-Content -LiteralPath (Join-Path $folder 'DemoIQ-Output/.run_seeder_status') -Value ("2026-01-01T00:00:00`t" + $Heartbeat) -Encoding UTF8 }
  return $folder
}
function Set-Json([string]$Path, $Object) { $Object | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $Path -Encoding UTF8 }

try {
  Write-Host '-- setup states --'
  $r = Get-Status $none
  Check 'no DemoIQ receipts: not-set-up with the runner command' ($r.code -eq 0 -and $r.json.state -eq 'not-set-up' -and ($r.json.next -join ' ') -match '--with-demoiq')
  Check 'the skill version is reported' ($r.json.skillVersion -match '^\d{4}-\d{2}-\d{2}\.\d+$')
  $r = Get-Status $receipts
  Check 'receipts but no config: needs-config' ($r.json.state -eq 'needs-config' -and $r.json.deployment.clientId -eq $APP)
  Set-Json (Join-Path $ws 'configs/DemoIQ_config_other.json') ([ordered]@{ adminUpn = 'admin@contoso.onmicrosoft.com'; existingAppId = '00000000-0000-0000-0000-000000000001'; u1upn = 'a@contoso.onmicrosoft.com'; u1pwd = 'Demo-Pw-1!' })
  Set-Json (Join-Path $ws 'configs/DemoIQ_config_enc.json') ([ordered]@{ v = 'cdx-enc-v1'; salt = @(1); iv = @(1); data = @(1) })
  $r = Get-Status $receipts
  Check 'a config for another app does not count' ($r.json.state -eq 'needs-config' -and $r.json.configs.count -eq 2 -and $r.json.configs.matching -eq 0)
  Check 'an encrypted config is flagged for conversion' (($r.json.attention -join ' ') -match 'convert_config')
  Check 'no config password is ever printed' (-not ($r.raw -match 'Demo-Pw-1!'))
  Set-Json (Join-Path $ws 'configs/DemoIQ_config_plant.json') ([ordered]@{ adminUpn = 'admin@contoso.onmicrosoft.com'; existingAppId = $APP })
  $r = Get-Status $receipts
  Check 'a config for this app without demo-user passwords is not ready' ($r.json.state -eq 'needs-config' -and $r.json.configs.matching -eq 1 -and $r.json.configs.ready -eq 0 -and
    $r.json.summary -match 'lack demo-user passwords' -and ($r.json.attention -join ' ') -match 'Config Builder')
  $five = [ordered]@{ adminUpn = 'admin@contoso.onmicrosoft.com'; existingAppId = $APP }
  foreach ($i in 1..5) { $five["u${i}upn"] = "user$i@contoso.onmicrosoft.com"; $five["u${i}pwd"] = "Demo-Pw-$i!" }
  $draft = [ordered]@{ demoiqDraft = $true }
  foreach ($k in $five.Keys) { $draft[$k] = $five[$k] }
  Set-Json (Join-Path $ws 'configs/DemoIQ_config_draft.json') $draft
  $r = Get-Status $receipts
  Check 'a draft never counts as ready, even with passwords' ($r.json.state -eq 'needs-config' -and $r.json.configs.drafts -eq 1 -and $r.json.configs.ready -eq 0)
  Set-Json (Join-Path $ws 'configs/DemoIQ_config_plant.json') $five
  $r = Get-Status $receipts
  Check 'a complete config and no runs: ready-to-seed' ($r.json.state -eq 'ready-to-seed' -and $r.json.configs.ready -eq 1 -and $r.json.configs.newestReady -like '*DemoIQ_config_plant.json')
  Check 'no demo-user password is ever printed' (-not ($r.raw -match 'Demo-Pw-[1-5]!'))
  Remove-Item -LiteralPath (Join-Path $ws 'configs/DemoIQ_config_draft.json')
  $r = Get-Status $unverified
  Check 'unverified consent is surfaced, not hidden' (($r.json.attention -join ' ') -match 'could not verify administrator consent')

  Write-Host '-- stored sign-in (account only) --'
  . (Join-Path $S 'session_store.ps1')
  $token = 'rt-' + [guid]::NewGuid().ToString('N')
  $null = Save-DemoIQSessionSecret -Name 'admin-session' -Value (@{ refreshToken = $token; tenantId = $TEN; clientId = $APP; upn = 'admin@contoso.onmicrosoft.com'; signedInAt = '2026-01-01T00:00:00Z' } | ConvertTo-Json -Compress)
  $r = Get-Status $receipts
  Check 'the stored sign-in is reported by account and deployment match' ($r.json.signIn.stored -and $r.json.signIn.upn -eq 'admin@contoso.onmicrosoft.com' -and $r.json.signIn.matchesDeployment -eq $true)
  Check 'the refresh token never appears in the output' (-not $r.raw.Contains($token))
  Clear-DemoIQSessionSecret -Name 'admin-session'

  Write-Host '-- run states --'
  $seed1 = New-Run '20260101_100000__plant' 'seed' 'DONE'
  $r = Get-Status $receipts
  Check 'a finished seed run is seeded, never ready' ($r.json.state -eq 'seeded' -and $r.json.summary -match 'not ready' -and $r.json.lastRun.outcome -eq 'done')
  Check 'the next step offers the optional Work IQ check (Flow 9)' (($r.json.next -join ' ') -match 'Work IQ' -and ($r.json.next -join ' ') -match 'Flow 9')
  Set-Json (Join-Path $seed1 'DemoIQ-Output/workiq_check.json') ([ordered]@{ verdict = 'answered-without-seeded-citations'; checkedAt = '2026-01-01T01:00:00Z'; askedAs = 'a@contoso.onmicrosoft.com'; askedAsIsDemoUser = $true; prompts = 3; answered = 2; withSeededCitations = 0 })
  $r = Get-Status $receipts
  Check 'a Work IQ check without seeded citations stays seeded and says why' ($r.json.state -eq 'seeded' -and $r.json.workIq.checked -and $r.json.summary -match 'indexing')
  Set-Json (Join-Path $seed1 'DemoIQ-Output/workiq_check.json') ([ordered]@{ verdict = 'found-seeded-content'; checkedAt = '2026-01-01T01:00:00Z'; askedAs = 'a@contoso.onmicrosoft.com'; askedAsIsDemoUser = $true; prompts = 3; answered = 3; withSeededCitations = 2 })
  $r = Get-Status $receipts
  Check 'Work IQ citing seeded content: workiq-verified with the counts' ($r.json.state -eq 'workiq-verified' -and $r.json.summary -match '2 of 3')
  $draftRun = Join-Path (Join-Path $ws 'runs') '20260101_110000__plant'
  New-Item -ItemType Directory -Force $draftRun | Out-Null
  Set-Json (Join-Path $draftRun 'run.json') ([ordered]@{ runId = '20260101_110000__plant'; createdAt = '2026-01-01T11:00:00'; kind = 'seed'; status = 'created' })
  $r = Get-Status $receipts
  Check 'a run folder that was never launched is not the last run' ($r.json.state -eq 'workiq-verified' -and $r.json.lastSeedRun.runId -eq '20260101_100000__plant' -and $r.json.lastRun.runId -eq '20260101_100000__plant')
  Remove-Item -LiteralPath $draftRun -Recurse -Force
  Set-Json (Join-Path $seed1 'DemoIQ-Output/workiq_check.json') ([ordered]@{ verdict = 'found-seeded-content'; askedAs = 'admin@contoso.onmicrosoft.com'; askedAsIsDemoUser = $false; prompts = 1; answered = 1; withSeededCitations = 1 })
  $r = Get-Status $receipts
  Check 'a check asked as a non-demo user is called out' (($r.json.attention -join ' ') -match 'not as a seeded demo user')
  Check '... and never makes the state workiq-verified' ($r.json.state -eq 'seeded' -and $r.json.summary -match 'does not count')

  $null = New-Run '20260102_100000__plant' 'seed' 'DONE missing=2'
  $r = Get-Status $receipts
  Check 'missing items: seeded-partial' ($r.json.state -eq 'seeded-partial' -and $r.json.lastSeedRun.outcome -eq 'partial')
  Check 'the Work IQ result belongs to its own run only' (-not $r.json.workIq.checked)
  $null = New-Run '20260103_100000__plant' 'seed' 'FAILED no-admin-session (sign in)'
  $r = Get-Status $receipts
  Check 'a failed run: seed-failed' ($r.json.state -eq 'seed-failed' -and $r.json.lastRun.outcome -eq 'failed')
  $null = New-Run '20260104_100000__plant' 'seed' 'STARTED mode=1'
  $r = Get-Status $receipts
  Check 'a started run with no seeder process is reported as stopped' ($r.json.state -eq 'seed-failed' -and $r.json.lastRun.outcome -eq 'stopped' -and -not $r.json.activeRun)

  $cu = New-Run '20260105_100000__plant-cleanup' 'cleanup' 'DONE failed=3'
  Set-Json (Join-Path $cu 'DemoIQ-Output/cleanup_result.json') ([ordered]@{ ok = $true; scope = 'LastRun'; failedTotal = 3 })
  $r = Get-Status $receipts
  Check 'a cleanup that left items: cleanup-partial with the count' ($r.json.state -eq 'cleanup-partial' -and $r.json.lastRun.itemsNotRemoved -eq 3 -and $r.json.lastRun.kind -eq 'cleanup')
  Check 'the newest seed run is still reported beside it' ($r.json.lastSeedRun.runId -eq '20260104_100000__plant')
  $cu2 = New-Run '20260106_100000__plant-cleanup' 'cleanup' 'DONE'
  Set-Json (Join-Path $cu2 'DemoIQ-Output/cleanup_result.json') ([ordered]@{ ok = $true; scope = 'AllRuns'; failedTotal = 0 })
  $r = Get-Status $receipts
  Check 'a complete cleanup: ready-to-seed again' ($r.json.state -eq 'ready-to-seed')

  Write-Host '-- tenant left prepared --'
  Set-Json (Join-Path $seed1 'DemoIQ-Output/tenant_state.json') ([ordered]@{ phase = 'prepared'; restored = $false; mfaReinstated = $false })
  $r = Get-Status $receipts
  Check 'an older run that never restored wins over later runs: tenant-prepared' ($r.json.state -eq 'tenant-prepared' -and @($r.json.preparedRuns).Count -eq 1 -and $r.json.preparedRuns[0] -eq $seed1)
  Check 'the next step names that run folder and the restore-not-strip rule' (($r.json.next -join ' ') -match [regex]::Escape($seed1) -and ($r.json.next -join ' ') -match 'policiesTouched')
  $raw = & $pwshExe -NoProfile -File $status -OutputFolder $ws -ReceiptsFile $receipts -MarkRestoredByHand $cu2 2>&1
  Check 'a run that was never left prepared cannot be marked (exit 2)' ($LASTEXITCODE -eq 2 -and ($raw -join '') -match 'Not a run that left tenant changes in place')
  $raw = & $pwshExe -NoProfile -File $status -OutputFolder $ws -ReceiptsFile $receipts -MarkRestoredByHand $seed1 2>&1
  $marked = @($raw | Where-Object { "$_".Trim().StartsWith('{') }) | Select-Object -Last 1 | ConvertFrom-Json
  Check 'a hand restore, once recorded, clears the state and stays visible' ($LASTEXITCODE -eq 0 -and $marked.state -eq 'ready-to-seed' -and @($marked.preparedRuns).Count -eq 0 -and
    ($marked.attention -join ' ') -match 'Restored by hand' -and (Get-Content -LiteralPath (Join-Path $seed1 'DemoIQ-Output/tenant_state.json') -Raw) -match 'prepared')
  Remove-Item -LiteralPath (Join-Path $seed1 'DemoIQ-Output/tenant_state.manual.json')
  Set-Json (Join-Path $seed1 'DemoIQ-Output/tenant_state.json') ([ordered]@{ phase = 'restored'; restored = $true; mfaReinstated = $true })
  $r = Get-Status $receipts
  Check 'after its restore the warning clears' ($r.json.state -eq 'ready-to-seed' -and @($r.json.preparedRuns).Count -eq 0)
  Set-Json (Join-Path $cu2 'DemoIQ-Output/tenant_state.json') ([ordered]@{ phase = 'restored'; restored = $false; mfaReinstated = $false })
  $r = Get-Status $receipts
  Check 'a restore that did not undo everything also leaves the tenant prepared' ($r.json.state -eq 'tenant-prepared' -and $r.json.preparedRuns[0] -eq $cu2 -and
    ($r.json.attention -join ' ') -match 'Restore incomplete')
  $null = & $pwshExe -NoProfile -File $status -OutputFolder $ws -ReceiptsFile $receipts -MarkRestoredByHand $cu2 2>&1
  $r = Get-Status $receipts
  Check '... and can be recorded as restored by hand' ($r.json.state -eq 'ready-to-seed' -and ($r.json.attention -join ' ') -match 'Restored by hand')

  if (-not $IsWindows) {
    Write-Host '-- live run --'
    $dummy = Start-Process -FilePath $pwshExe -ArgumentList @('-NoProfile', '-Command', 'Start-Sleep 30 # run_seeder.ps1 -Cleanup') -PassThru
    try {
      Start-Sleep -Milliseconds 800
      $r = Get-Status $receipts
      Check 'a live run_seeder process: running, with its kind' ($r.json.state -eq 'running' -and $r.json.activeRun.kind -eq 'cleanup' -and $r.json.activeRun.processId -eq $dummy.Id)
    } finally { Stop-Process -Id $dummy.Id -Force -ErrorAction SilentlyContinue }
  }
}
finally {
  $env:DEMOIQ_SESSION_DIR = $savedSession
  Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

if ($script:fails) { Write-Host "test_status: $($script:fails) FAILURE(S)" -ForegroundColor Red; exit 1 }
Write-Host 'test_status: all passed' -ForegroundColor Green
exit 0

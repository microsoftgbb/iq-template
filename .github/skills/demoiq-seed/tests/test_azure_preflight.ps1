# test_azure_preflight.ps1 - preflight_azure_cli.ps1 (Check C) tests.
#
# Proves the az-CLI tenant gate against a STUB az (az.cmd on Windows, a POSIX shell
# script on macOS/Linux) on PATH (fixture JSON, no real CLI, no network) with plain
# configs and a saved deployment-receipts file (the `deploy.py status --json` shape)
# that supplies the expected tenant, so neither the runner nor any network endpoint is
# reached. Critically also proves the OPT-OUT contract: a config without meeting audio
# must yield applicable:false WITHOUT invoking az at all (the stub records invocations).
# Also: an encrypted (cdx-enc-v1) config is refused with the convert hint, the Speech
# resource may come from the receipts, -ExpectedTenantId stands in when there are no
# receipts, and with neither the result is no-expected-tenant.
#
# Usage: pwsh -NoProfile -ExecutionPolicy Bypass -File tests/test_azure_preflight.ps1
# Exit 0 = pass. Touches only temp dirs; never contacts a tenant.
$ErrorActionPreference = 'Stop'

$script:failures = 0
function Assert-True($cond, [string]$label) {
  if ($cond) { Write-Host "  PASS: $label" -ForegroundColor Green }
  else { Write-Host "  FAIL: $label" -ForegroundColor Red; $script:failures++ }
}

$scriptUnderTest = Join-Path (Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts') 'preflight_azure_cli.ps1'
$pwshExe = Join-Path $PSHOME $(if ($IsWindows) { 'pwsh.exe' } else { 'pwsh' })   # full path: cases below rewrite PATH
$sysPath = if ($IsWindows) { "$env:SystemRoot\system32;$env:SystemRoot" } else { '/usr/bin:/bin' }
$root = Join-Path ([System.IO.Path]::GetTempPath()) ("demoiq-azpf-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $root -Force | Out-Null

$EXPECTED_TID = 'aaaaaaaa-1111-2222-3333-444444444444'
$WRONG_TID    = 'bbbbbbbb-1111-2222-3333-444444444444'
$SPEECH_SUB   = 'cccccccc-1111-2222-3333-444444444444'
$OTHER_SUB    = 'dddddddd-1111-2222-3333-444444444444'
$APP_ID       = 'eeeeeeee-1111-2222-3333-444444444444'
$DOMAIN       = 'contoso-demo.onmicrosoft.com'
$SPEECH_ID    = "/subscriptions/$SPEECH_SUB/resourceGroups/demorg/providers/Microsoft.CognitiveServices/accounts/demospeech"
$DEPLOY_SPEECH = "/subscriptions/$OTHER_SUB/resourceGroups/platformrg/providers/Microsoft.CognitiveServices/accounts/ai-fixture"

# ---- helpers ----------------------------------------------------------------
function Write-Json($value, [string]$path) {
  $value | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $path -Encoding UTF8
}

function New-Receipts([string]$path, [switch]$NoDemoIQ, [string]$TenantId, [string]$SpeechId, [string]$AudioStatus) {
  # The `deploy.py status --json` shape. Without -NoDemoIQ it carries the demoiq receipts;
  # with it, the deployment has not completed the demoiq phase (Get-DemoIQRunnerReceipts -> $null).
  $report = [ordered]@{ result = 'status'; phases = @{} }
  if (-not $NoDemoIQ) {
    $report.demoiq = [ordered]@{ tenant_id = $(if ($TenantId) { $TenantId } else { $EXPECTED_TID }); client_id = $APP_ID
      speech_resource_id = $(if ($SpeechId) { $SpeechId } else { $null }); consent = 'granted' }
    if ($AudioStatus) { $report.demoiq.audio = [ordered]@{ status = $AudioStatus; account = 'demospeech'; detail = 'fixture: text-to-speech answered HTTP 403' } }
  }
  Write-Json $report $path
}

function New-AzStub([string]$dir, [string]$showJson, [string]$listJson, [int]$showExit = 0) {
  # az stub: 'az account show ...' prints show.json, 'az account list ...'
  # prints list.json; every invocation appends to az_invoked.marker.
  New-Item -ItemType Directory -Path $dir -Force | Out-Null
  if ($showJson) { Set-Content -LiteralPath (Join-Path $dir 'show.json') -Value $showJson -Encoding UTF8 }
  if ($listJson) { Set-Content -LiteralPath (Join-Path $dir 'list.json') -Value $listJson -Encoding UTF8 }
  if (-not $IsWindows) {
    $sh = @(
      '#!/bin/sh'
      'd=$(dirname "$0")'
      'echo "$*" >> "$d/az_invoked.marker"'
      'if [ "$2" = "show" ]; then'
      "  if [ -f `"`$d/show.json`" ]; then cat `"`$d/show.json`"; exit $showExit; else exit 1; fi"
      'fi'
      'if [ "$2" = "list" ]; then'
      '  if [ -f "$d/list.json" ]; then cat "$d/list.json"; exit 0; else exit 1; fi'
      'fi'
      'exit 1'
    ) -join "`n"
    $azPath = Join-Path $dir 'az'
    [System.IO.File]::WriteAllText($azPath, $sh + "`n")
    [System.IO.File]::SetUnixFileMode($azPath, [System.IO.UnixFileMode]'UserRead, UserWrite, UserExecute')
    return
  }
  $cmd = @(
    '@echo off'
    'echo %* >> "%~dp0az_invoked.marker"'
    'if "%2"=="show" ('
    "  if exist `"%~dp0show.json`" ( type `"%~dp0show.json`" & exit /b $showExit ) else ( exit /b 1 )"
    ')'
    'if "%2"=="list" ('
    '  if exist "%~dp0list.json" ( type "%~dp0list.json" & exit /b 0 ) else ( exit /b 1 )'
    ')'
    'exit /b 1'
  ) -join "`r`n"
  Set-Content -LiteralPath (Join-Path $dir 'az.cmd') -Value $cmd -Encoding ASCII
}

$caseN = 0
$script:LastExit = $null
function Invoke-Preflight {
  param([hashtable]$Config, [string]$StubDir, [switch]$StripPath, [switch]$Encrypted,
    [switch]$NoReceipts, [string]$ReceiptsTenant, [string]$ReceiptsSpeechId, [string]$AudioStatus,
    [string]$ReceiptsPath, [string]$ExpectedTenantId)
  # Fresh workspace per case: a plain (or encrypted) config and a receipts file.
  $script:caseN++
  $ws = Join-Path $root ("ws" + $script:caseN)
  New-Item -ItemType Directory -Path $ws -Force | Out-Null
  $cfgPath = Join-Path $ws 'DemoIQ_config.json'
  if ($Encrypted) {
    # An upstream cdx-enc-v1 envelope: only its shape matters, it is never decrypted.
    Write-Json ([ordered]@{ v = 'cdx-enc-v1'; salt = @(1, 2, 3); iv = @(4, 5, 6); data = @(7, 8, 9) }) $cfgPath
  } else {
    Write-Json $Config $cfgPath
  }
  if (-not $ReceiptsPath) {
    $ReceiptsPath = Join-Path $ws 'receipts.json'
    New-Receipts $ReceiptsPath -NoDemoIQ:$NoReceipts -TenantId $ReceiptsTenant -SpeechId $ReceiptsSpeechId -AudioStatus $AudioStatus
  }
  $cli = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $scriptUnderTest, '-ConfigPath', $cfgPath, '-ReceiptsFile', $ReceiptsPath)
  if ($ExpectedTenantId) { $cli += @('-ExpectedTenantId', $ExpectedTenantId) }
  $oldPath = $env:PATH
  try {
    if ($StripPath) { $env:PATH = $sysPath }
    elseif ($StubDir) { $env:PATH = $StubDir + [System.IO.Path]::PathSeparator + $sysPath }
    $raw = & $pwshExe @cli 2>$null
    $script:LastExit = $LASTEXITCODE
  } finally {
    $env:PATH = $oldPath
  }
  $line = @($raw | Where-Object { $_ -and "$_".Trim().StartsWith('{') }) | Select-Object -Last 1
  if (-not $line) { throw "case $($script:caseN): no JSON line in output: $raw" }
  return ($line | ConvertFrom-Json)
}

function Get-AzMarker([string]$stubDir) {
  $m = Join-Path $stubDir 'az_invoked.marker'
  if (Test-Path $m) { return (Get-Content $m -Raw) } else { return $null }
}

$showRight = @{ tenantId = $EXPECTED_TID; id = $SPEECH_SUB; name = 'Demo Sub'; user = @{ name = "admin@$DOMAIN" } } | ConvertTo-Json -Compress
$showRightOtherSub = @{ tenantId = $EXPECTED_TID; id = $OTHER_SUB; name = 'Other Sub'; user = @{ name = "admin@$DOMAIN" } } | ConvertTo-Json -Compress
$showWrong = @{ tenantId = $WRONG_TID; id = $OTHER_SUB; name = 'Corp Sub'; user = @{ name = 'me@corp.com' } } | ConvertTo-Json -Compress
$listWithMatch = ,@(@{ tenantId = $WRONG_TID; id = $OTHER_SUB }, @{ tenantId = $EXPECTED_TID; id = $SPEECH_SUB }) | ConvertTo-Json -Compress -Depth 4
$listNoMatch = ,@(@{ tenantId = $WRONG_TID; id = $OTHER_SUB }) | ConvertTo-Json -Compress -Depth 4
$audioConfig = @{ adminUpn = "admin@$DOMAIN"; contentTypes = @('meetings'); speechResourceId = $SPEECH_ID }

try {
  Write-Host "-- opt-out contract: not-applicable never touches az --"

  # 1. meetings NOT selected (array shape) -> not-applicable, zero az calls.
  $stub1 = Join-Path $root 'stub1'; New-AzStub $stub1 $showWrong $listNoMatch
  $r = Invoke-Preflight -Config @{ adminUpn = "admin@$DOMAIN"; contentTypes = @('emails', 'word'); speechResourceId = $SPEECH_ID } -StubDir $stub1
  Assert-True ($r.ok -and $r.applicable -eq $false -and $r.status -eq 'not-applicable') 'meetings unselected: applicable=false / not-applicable'
  Assert-True ($r.reason -eq 'meetings-not-selected') 'meetings unselected: reason names the opt-out'
  Assert-True ($null -eq (Get-AzMarker $stub1)) 'meetings unselected: az was NEVER invoked'

  # 2. meetings selected but NO Speech resource in the config or the receipts -> not-applicable, zero az calls.
  $stub2 = Join-Path $root 'stub2'; New-AzStub $stub2 $showWrong $listNoMatch
  $r = Invoke-Preflight -Config @{ adminUpn = "admin@$DOMAIN"; contentTypes = @('emails', 'meetings'); speechResourceId = '' } -StubDir $stub2
  Assert-True ($r.ok -and $r.applicable -eq $false -and $r.reason -eq 'no-speech-resource') 'no Speech resource (config or receipts): applicable=false'
  Assert-True ($null -eq (Get-AzMarker $stub2)) 'no Speech resource: az was NEVER invoked'

  # 3. An encrypted (cdx-enc-v1) config is refused before anything else: exit 2 + convert hint.
  $stub3 = Join-Path $root 'stub3'; New-AzStub $stub3 $showRight $listWithMatch
  $r = Invoke-Preflight -Encrypted -StubDir $stub3
  Assert-True ($script:LastExit -eq 2 -and $r.ok -eq $false -and $r.error -match 'encrypted') 'encrypted config: refused (exit 2)'
  Assert-True ($r.hint -match 'convert_config\.ps1') 'encrypted config: hint names scripts/convert_config.ps1'
  Assert-True ($null -eq (Get-AzMarker $stub3)) 'encrypted config: az was NEVER invoked'

  Write-Host "-- applicable comparisons (stub az, receipts tenant, no network) --"

  # 4. Wrong tenant, cached sign-in for the right tenant exists -> single az account set fix.
  $stub4 = Join-Path $root 'stub4'; New-AzStub $stub4 $showWrong $listWithMatch
  $r = Invoke-Preflight -Config $audioConfig -StubDir $stub4
  Assert-True ($r.status -eq 'wrong-tenant') 'wrong tenant detected'
  Assert-True ($r.cachedMatch -eq $true) 'wrong tenant: cached right-tenant sign-in detected'
  Assert-True (@($r.fixCommands).Count -eq 1 -and $r.fixCommands[0] -eq "az account set --subscription $SPEECH_SUB") 'wrong tenant + cache: fix is one non-interactive account set'
  Assert-True ($r.expectedTenantId -eq $EXPECTED_TID -and $r.expectedTenantSource -eq 'receipts') 'expected tenant came from the deployment receipts (no network)'

  # 5. Wrong tenant, no cached match -> logout + login --tenant (+ account set).
  $stub5 = Join-Path $root 'stub5'; New-AzStub $stub5 $showWrong $listNoMatch
  $r = Invoke-Preflight -Config $audioConfig -StubDir $stub5
  Assert-True ($r.status -eq 'wrong-tenant' -and $r.cachedMatch -eq $false) 'wrong tenant, no cached match'
  Assert-True (@($r.fixCommands)[0] -eq 'az logout' -and @($r.fixCommands)[1] -eq "az login --tenant $EXPECTED_TID") 'no cache: fix is logout + login --tenant'

  # 6. Right tenant, wrong default subscription -> wrong-subscription self-fix.
  $stub6 = Join-Path $root 'stub6'; New-AzStub $stub6 $showRightOtherSub $listWithMatch
  $r = Invoke-Preflight -Config $audioConfig -StubDir $stub6
  Assert-True ($r.status -eq 'wrong-subscription') 'right tenant / wrong subscription detected'
  Assert-True (@($r.fixCommands).Count -eq 1 -and $r.fixCommands[0] -eq "az account set --subscription $SPEECH_SUB") 'wrong subscription: single account set fix'

  # 7. Right tenant + right subscription (object-shape contentTypes) -> ok.
  $stub7 = Join-Path $root 'stub7'; New-AzStub $stub7 $showRight $listWithMatch
  $r = Invoke-Preflight -Config @{ adminUpn = "admin@$DOMAIN"; contentTypes = @{ meetings = $true; emails = $true; word = $false }; speechResourceId = $SPEECH_ID } -StubDir $stub7
  Assert-True ($r.status -eq 'ok' -and $r.applicable -eq $true) 'object-shape contentTypes: right tenant reports ok'
  Assert-True ($r.azTenantId -eq $EXPECTED_TID -and $r.azSubscriptionId -eq $SPEECH_SUB) 'ok: az identifiers surfaced'

  # 8. No az session (az account show exits 1, no output) -> not-logged-in.
  $stub8 = Join-Path $root 'stub8'; New-AzStub $stub8 $null $null
  $r = Invoke-Preflight -Config $audioConfig -StubDir $stub8
  Assert-True ($r.status -eq 'not-logged-in') 'no session: not-logged-in'
  Assert-True (@($r.fixCommands)[0] -eq "az login --tenant $EXPECTED_TID") 'no session: fix is login --tenant'

  # 9. az CLI not installed (PATH stripped) -> no-cli.
  $r = Invoke-Preflight -Config $audioConfig -StripPath
  Assert-True ($r.status -eq 'no-cli') 'no CLI on PATH: no-cli'
  Assert-True (@($r.fixCommands).Count -ge 1) 'no CLI: install fix present'

  Write-Host "-- expected tenant and Speech resource sources --"

  # 10. Speech resource only in the receipts -> not applicable (the seeder uses only the
  #     config's), with the repoint command that would add audio; still no az call.
  $stub10 = Join-Path $root 'stub10'; New-AzStub $stub10 $showRightOtherSub $listWithMatch
  $r = Invoke-Preflight -Config @{ adminUpn = "admin@$DOMAIN"; contentTypes = @('meetings') } -StubDir $stub10 -ReceiptsSpeechId $SPEECH_ID
  Assert-True ($r.applicable -eq $false -and $r.reason -eq 'no-speech-resource' -and $r.note -match 'repoint_config\.ps1' -and $r.note -match 'ExecutionPolicy Bypass') "Speech resource only in the receipts: not applicable, repoint offered"

  # 11. -ExpectedTenantId without receipts -> the parameter is the expected tenant.
  $stub11 = Join-Path $root 'stub11'; New-AzStub $stub11 $showWrong $listNoMatch
  $r = Invoke-Preflight -Config $audioConfig -StubDir $stub11 -NoReceipts -ExpectedTenantId $EXPECTED_TID
  Assert-True ($r.status -eq 'wrong-tenant' -and $r.expectedTenantId -eq $EXPECTED_TID -and $r.expectedTenantSource -eq 'parameter') '-ExpectedTenantId without receipts: expected tenant from the parameter'
  Assert-True (@($r.fixCommands) -contains "az login --tenant $EXPECTED_TID") '-ExpectedTenantId without receipts: login fix targets that tenant'
  $stub11b = Join-Path $root 'stub11b'; New-AzStub $stub11b $showRight $listWithMatch
  $r = Invoke-Preflight -Config $audioConfig -StubDir $stub11b -NoReceipts -ExpectedTenantId $EXPECTED_TID
  Assert-True ($r.status -eq 'ok' -and $r.expectedTenantSource -eq 'parameter') '-ExpectedTenantId without receipts: a matching az context is ok'

  # 12. Neither receipts nor -ExpectedTenantId -> no-expected-tenant (a result: exit 0).
  $stub12 = Join-Path $root 'stub12'; New-AzStub $stub12 $showRight $listWithMatch
  $r = Invoke-Preflight -Config $audioConfig -StubDir $stub12 -NoReceipts
  Assert-True ($script:LastExit -eq 0 -and $r.ok -and $r.applicable -and $r.status -eq 'no-expected-tenant') 'no receipts and no -ExpectedTenantId: no-expected-tenant (exit 0)'
  Assert-True ($r.hint -match 'deploy\.py status' -and $r.azTenantId -eq $EXPECTED_TID) 'no-expected-tenant: hint names deploy.py status; az identity surfaced'
  Assert-True (@($r.fixCommands).Count -eq 0) 'no-expected-tenant: no az fix is prescribed'

  # 13. The receipts win over a conflicting -ExpectedTenantId (noted); a config Speech resource
  #     that is not the deployment's Foundry account is noted (the config's own resource is
  #     still the one checked); the deployment's failed audio check is relayed.
  $stub13 = Join-Path $root 'stub13'; New-AzStub $stub13 $showRight $listWithMatch
  $r = Invoke-Preflight -Config $audioConfig -StubDir $stub13 -ExpectedTenantId $WRONG_TID -ReceiptsSpeechId $DEPLOY_SPEECH -AudioStatus 'unavailable'
  Assert-True ($r.status -eq 'ok' -and $r.expectedTenantSource -eq 'receipts' -and $r.expectedTenantId -eq $EXPECTED_TID) 'the receipts win over a conflicting -ExpectedTenantId'
  Assert-True ($r.note -match [regex]::Escape($WRONG_TID) -and $r.note -match 'repoint_config\.ps1' -and $r.note -match 'ai-fixture') 'conflicting tenant and a config Speech resource that is not the deployment one are noted'
  Assert-True ($r.expectedSubscriptionId -eq $SPEECH_SUB) "the config's Speech resource is the one checked"
  Assert-True ($r.warning -match 'unavailable' -and $r.warning -match 'HTTP 403') "the deployment's unavailable audio check is relayed as a warning"

  # 14. Usage/IO errors exit 2: a non-GUID -ExpectedTenantId, a missing or malformed -ReceiptsFile
  #     (an explicit receipts file is never silently ignored).
  $r = Invoke-Preflight -Config $audioConfig -StubDir $stub13 -ExpectedTenantId 'contoso.onmicrosoft.com'
  Assert-True ($script:LastExit -eq 2 -and $r.ok -eq $false -and $r.error -match 'ExpectedTenantId') 'non-GUID -ExpectedTenantId: usage error (exit 2)'
  $r = Invoke-Preflight -Config $audioConfig -StubDir $stub13 -ReceiptsPath (Join-Path $root 'missing-receipts.json')
  Assert-True ($script:LastExit -eq 2 -and $r.ok -eq $false -and $r.error -match 'Receipts file not found') 'missing -ReceiptsFile: IO error (exit 2)'
  $garbage = Join-Path $root 'garbage-receipts.json'; Set-Content -LiteralPath $garbage -Value 'not json' -Encoding UTF8
  $r = Invoke-Preflight -Config $audioConfig -StubDir $stub13 -ReceiptsPath $garbage
  Assert-True ($script:LastExit -eq 2 -and $r.ok -eq $false -and $r.error -match 'Could not read the receipts file') 'malformed -ReceiptsFile: IO error (exit 2)'
}
finally {
  Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

if ($script:failures) { Write-Host "test_azure_preflight: $($script:failures) FAILURE(S)" -ForegroundColor Red; exit 1 }
Write-Host 'test_azure_preflight: all passed' -ForegroundColor Green
exit 0

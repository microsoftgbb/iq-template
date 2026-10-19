# test_seeder_bootstrap.ps1 - run a REAL patched seeder offline on this OS (Windows, macOS, Linux).
#
# Generates a seeder and its plain config from the fixture, stores a fake administrator
# sign-in in the encrypted session store (as admin_signin.ps1 does), and runs the seeder
# through the real launcher (run_seeder.ps1) in Fast mode. Invoke-RestMethod is shadowed
# by a stub that plays the Entra token endpoint and Microsoft Graph, records every request
# and stops the run at the first tenant-preparation call, so nothing leaves the machine.
# Proves, end to end on this OS:
#   * the launcher consumes the stored sign-in (gone from disk; the test sets no variable)
#   * the seeder reads its plain config, with no password prompt
#   * the seeder renews the administrator token with a refresh_token grant as a public
#     client (no client assertion or secret) for the config's app, in the sign-in's tenant
#   * Graph calls carry that delegated token, with no Graph PowerShell module or certificate
#   * the sign-in has left the environment before the first request
#   * a sign-in for another administrator, a missing sign-in and an unpatched or v1 seeder
#     are all refused before any request, with the reason in the heartbeat
#   * launch_seeder.ps1 fails fast on the same problems instead of launching a doomed run
$ErrorActionPreference = 'Stop'

# Self-isolate: a throwaway HOME and session folder, so the real workspace and any real
# sign-in are never read, overwritten or deleted.
if ($env:DEMOIQ_TEST_ISOLATED -ne '1') {
  $iso = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-boot-iso-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
  New-Item -ItemType Directory -Force (Join-Path $iso 'home') | Out-Null
  $saved = @{ HOME = $env:HOME; DEMOIQ_SESSION_DIR = $env:DEMOIQ_SESSION_DIR; DEMOIQ_TEST_ISOLATED = $env:DEMOIQ_TEST_ISOLATED }
  $env:HOME = Join-Path $iso 'home'; $env:DEMOIQ_SESSION_DIR = Join-Path $iso 'session'; $env:DEMOIQ_TEST_ISOLATED = '1'
  try {
    & (Get-Process -Id $PID).Path -NoProfile -File $PSCommandPath
    $code = $LASTEXITCODE
  } finally {
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    Remove-Item -LiteralPath $iso -Recurse -Force -ErrorAction SilentlyContinue
  }
  exit $code
}

$script:fails = 0
function Check([string]$name, $cond) {
  if ($cond) { Write-Host "  PASS $name" -ForegroundColor Green } else { Write-Host "  FAIL $name" -ForegroundColor Red; $script:fails++ }
}
$root = Split-Path $PSScriptRoot -Parent
$S = Join-Path $root 'scripts'
$pwshExe = (Get-Process -Id $PID).Path
$work = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-boot-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null

$Tenant = 'aaaaaaaa-1111-2222-3333-444444444444'
$App = '00000000-0000-0000-0000-000000000001'
$Admin = 'admin@fixturetest.onmicrosoft.com'
$RefreshToken = 'fixture-refresh-token'

function ConvertTo-Base64Url([string]$Text) {
  [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Text)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}
$scp = 'Policy.Read.All Policy.ReadWrite.ConditionalAccess Policy.ReadWrite.AuthenticationMethod User.Read.All Group.ReadWrite.All Chat.Create Files.ReadWrite.All Sites.Read.All Mail.Read People.Read.All OnlineMeetingTranscript.Read.All Chat.Read ChannelMessage.Read.All ExternalItem.Read.All'
$Jwt = (ConvertTo-Base64Url '{"alg":"none","typ":"JWT"}') + '.' +
  (ConvertTo-Base64Url (@{ tid = $Tenant; appid = $App; upn = $Admin; scp = $scp } | ConvertTo-Json -Compress)) + '.fixture'

function Save-SignIn([string]$Upn = $Admin) {
  $json = @{ refreshToken = $RefreshToken; tenantId = $Tenant; clientId = $App; upn = $Upn; signedInAt = (Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json -Compress
  return [bool](Save-DemoIQSessionSecret -Name 'admin-session' -Value $json)
}

function Invoke-Seeder([string]$Seeder, [string]$Config, [string]$Name, [string]$RunMode = '1') {
  # Run the real launcher in a fresh pwsh with Entra and Graph played by a recording stub.
  $requests = Join-Path $work "$Name-requests.json"
  $wrapper = Join-Path $work "$Name-wrapper.ps1"
  $stub = @'
$ErrorActionPreference = 'Stop'
function __t_Record([hashtable]$Entry) {
  $all = @(); if (Test-Path -LiteralPath '__REQUESTS__') { $all = @(Get-Content -LiteralPath '__REQUESTS__' -Raw | ConvertFrom-Json) }
  $all += [pscustomobject]$Entry
  ConvertTo-Json -InputObject @($all) -Depth 6 | Set-Content -LiteralPath '__REQUESTS__' -Encoding UTF8
}
function Install-Module { __t_Record @{ kind = 'module'; name = 'Install-Module' } }
function Connect-MgGraph { __t_Record @{ kind = 'module'; name = 'Connect-MgGraph' } }
function Invoke-RestMethod {
  param([string]$Method = 'GET', [string]$Uri, $Body, [string]$ContentType, $Headers, [int]$TimeoutSec,
    [Parameter(ValueFromRemainingArguments = $true)]$Rest)
  $inEnv = -not [string]::IsNullOrEmpty($env:DEMOIQ_ADMIN_SESSION)
  if ($Uri -match '^https://login\.microsoftonline\.com/([^/]+)/oauth2/v2\.0/token$') {
    __t_Record @{ kind = 'token'; tenant = $Matches[1]; grant = [string]$Body.grant_type; clientId = [string]$Body.client_id
      refreshToken = [string]$Body.refresh_token; scope = [string]$Body.scope; sessionInEnv = $inEnv
      assertion = [bool]($Body.client_assertion -or $Body.client_assertion_type); secret = [bool]$Body.client_secret }
    return [pscustomobject]@{ access_token = '__JWT__'; expires_in = 3600; refresh_token = 'rotated-refresh-token'; token_type = 'Bearer' }
  }
  $auth = if ($Headers) { [string]$Headers['Authorization'] } else { '' }
  __t_Record @{ kind = 'http'; method = $Method; uri = $Uri; bearer = ($auth -eq 'Bearer __JWT__'); sessionInEnv = $inEnv }
  if ($Uri -eq 'https://graph.microsoft.com/v1.0/organization') { return [pscustomobject]@{ value = @([pscustomobject]@{ id = '__TENANT__' }) } }
  if ($Uri -like 'https://graph.microsoft.com/v1.0/users/*/drive') { return [pscustomobject]@{ id = 'drive' } }
  if ($Uri -eq 'https://graph.microsoft.com/v1.0/policies/identitySecurityDefaultsEnforcementPolicy') {
    $env:DEMOIQ_ABORT_REASON = 'TEST-STOP-AT-TENANT-PREP'
    exit 3
  }
  throw "unexpected request: $Method $Uri"
}
& '__RUN_SEEDER__' -SeederScript '__SEEDER__' -ConfigFile '__CONFIG__' -RunMode __RUN_MODE__
exit $LASTEXITCODE
'@
  $stub = $stub.Replace('__REQUESTS__', $requests).Replace('__JWT__', $Jwt).Replace('__TENANT__', $Tenant).
    Replace('__RUN_SEEDER__', (Join-Path $S 'run_seeder.ps1')).Replace('__SEEDER__', $Seeder).Replace('__CONFIG__', $Config).
    Replace('__RUN_MODE__', $RunMode)
  Set-Content -LiteralPath $wrapper -Value $stub -Encoding UTF8
  # A seeder stuck at a prompt must fail the check, never hang the suite.
  $proc = Start-Process -FilePath $pwshExe -ArgumentList @('-NoProfile', '-File', ('"' + $wrapper + '"')) -PassThru -NoNewWindow `
    -RedirectStandardOutput (Join-Path $work "$Name-stdout.log") -RedirectStandardError (Join-Path $work "$Name-stderr.log")
  $timedOut = -not $proc.WaitForExit(240000)
  if ($timedOut) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
  $runDir = Split-Path -Parent $Seeder
  $log = Get-ChildItem -LiteralPath (Join-Path $runDir 'logs') -Filter 'DemoIQ_*.log' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  return [pscustomobject]@{
    Code = $(if ($timedOut) { -1 } else { $proc.ExitCode })
    TimedOut = $timedOut
    Status = (Get-Content -LiteralPath (Join-Path $runDir 'DemoIQ-Output/.run_seeder_status') -Raw -ErrorAction SilentlyContinue)
    Log = if ($log) { Get-Content -LiteralPath $log.FullName -Raw } else { '' }
    Requests = if (Test-Path -LiteralPath $requests) { @(Get-Content -LiteralPath $requests -Raw | ConvertFrom-Json) } else { @() }
  }
}

function Invoke-Launch([string]$Seeder, [string]$Config) {
  $text = & $pwshExe -NoProfile -File (Join-Path $S 'launch_seeder.ps1') -SeederScript $Seeder -ConfigFile $Config 2>$null
  return [pscustomobject]@{ Code = $LASTEXITCODE; Json = (($text | Select-Object -Last 1) | ConvertFrom-Json) }
}

try {
  . (Join-Path $S 'session_store.ps1')

  # --- generate a Fast-mode seeder and its plain config from the fixture ---
  $prefsFile = Join-Path $work 'prefs.json'
  Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'fixtures/test-prefs.json') -Destination $prefsFile
  $runDir = Join-Path $work 'run'
  $null = & node (Join-Path $S 'generate.js') --html (Join-Path $root 'engine/DemoIQ.html') --prefs $prefsFile `
    --emit both --out-dir $runDir 2>$null
  $seeder = Join-Path $runDir 'DemoIQ.ps1'
  $config = Join-Path $runDir 'DemoIQ_config.json'
  Check 'generate.js emitted the seeder and a plain config' ($LASTEXITCODE -eq 0 -and (Test-Path $seeder) -and
    (Get-Content -LiteralPath $config -Raw | ConvertFrom-Json).adminUpn -eq $Admin)

  # --- launch_seeder fails fast on a missing or mismatched sign-in ---
  $l = Invoke-Launch $seeder $config
  Check 'launch_seeder refuses to launch without a sign-in' ($l.Code -eq 2 -and -not $l.Json.ok -and $l.Json.error -match 'admin_signin\.ps1')
  Check 'sign-in for another administrator stored' (Save-SignIn 'someone.else@fixturetest.onmicrosoft.com')
  $l = Invoke-Launch $seeder $config
  Check "launch_seeder refuses a sign-in that is not the config's administrator" ($l.Code -eq 2 -and $l.Json.error -match 'someone\.else' -and $l.Json.error -match [regex]::Escape($Admin))
  Check '... and clears that unusable sign-in' (-not (Test-DemoIQSessionSecret -Name 'admin-session'))

  # --- the seeder itself refuses that sign-in before any request ---
  $null = Save-SignIn 'someone.else@fixturetest.onmicrosoft.com'
  $r = Invoke-Seeder $seeder $config 'mismatch'
  Check 'seeder stops on a sign-in for another administrator' ($r.Status -match 'FAILED admin-session: the administrator signed in as someone\.else')
  Check '... before any request leaves the machine' ($r.Requests.Count -eq 0)
  Check '... and the launcher consumed that sign-in' (-not (Test-DemoIQSessionSecret -Name 'admin-session'))

  # --- no sign-in: refused with the next action ---
  $r = Invoke-Seeder $seeder $config 'nosignin'
  Check 'run_seeder refuses to start without a sign-in' ($r.Status -match 'FAILED no-admin-session' -and $r.Requests.Count -eq 0)

  # --- the real run, up to the first tenant-preparation call ---
  Check 'administrator sign-in stored (encrypted session store)' (Save-SignIn)
  $l = Invoke-Launch (Join-Path $work 'missing.ps1') $config
  Check 'launch_seeder reports a missing seeder' ($l.Code -eq 2 -and $l.Json.error -match 'not found')
  $r = Invoke-Seeder $seeder $config 'run'
  Check 'run stopped exactly at the first tenant-preparation call' ($r.Status -match 'FAILED TEST-STOP-AT-TENANT-PREP')
  Check 'the launcher consumed the sign-in (gone from disk)' (-not (Test-DemoIQSessionSecret -Name 'admin-session'))
  Check 'seeder read its plain config with no password prompt' ($r.Log -match "\[CONFIG\] OK: Admin=$([regex]::Escape($Admin))" -and $r.Log -notmatch 'Config password')
  Check 'seeder started the delegated administrator session' ($r.Log -match '\[AUTH\] OK: delegated administrator session')
  Check 'seeder resolved the tenant from the delegated token' ($r.Log -match "\[SETUP\] OK: Tenant: $Tenant")
  $tokens = @($r.Requests | Where-Object kind -eq 'token')
  $http = @($r.Requests | Where-Object kind -eq 'http')
  Check 'exactly one token request (renewed once, then cached)' ($tokens.Count -eq 1)
  $t = $tokens | Select-Object -First 1
  Check 'token request: refresh_token grant for the config app in the sign-in tenant' ($t -and $t.grant -eq 'refresh_token' -and
    $t.clientId -eq $App -and $t.tenant -eq $Tenant -and $t.refreshToken -eq $RefreshToken -and $t.scope -match '^https://graph\.microsoft\.com/\.default')
  Check 'token request: public client (no client assertion, no client secret)' ($t -and -not $t.assertion -and -not $t.secret)
  Check 'Graph: organization, admin OneDrive, then Security Defaults' ((@($http | ForEach-Object uri) -join ' | ') -eq
    "https://graph.microsoft.com/v1.0/organization | https://graph.microsoft.com/v1.0/users/$Admin/drive | https://graph.microsoft.com/v1.0/policies/identitySecurityDefaultsEnforcementPolicy")
  Check 'every Graph call carries the delegated token' (@($http | Where-Object { -not $_.bearer }).Count -eq 0)
  Check 'the sign-in left the environment before the first request' (@($r.Requests | Where-Object sessionInEnv).Count -eq 0)
  Check 'no Graph PowerShell module was installed or connected' (@($r.Requests | Where-Object kind -eq 'module').Count -eq 0)

  # --- Full mode with no earlier run: the engine asks for the mode again; never a hang ---
  $null = Save-SignIn
  $r = Invoke-Seeder $seeder $config 'fullnohistory' '2'
  Check 'Full mode without history continues in Fast mode instead of looping' (-not $r.TimedOut -and $r.Status -match 'FAILED TEST-STOP-AT-TENANT-PREP' -and
    $r.Log -match '\[MODE\] WARN: Full mode needs an earlier run' -and $r.Log -match 'Fast mode selected')

  # --- a prompt that keeps rejecting the scripted answer ends the run with a reason ---
  $loopDir = Join-Path $work 'stuck'
  New-Item -ItemType Directory -Force $loopDir | Out-Null
  $marker = (& node -e "process.stdout.write(require(process.argv[1]).MARKER)" (Join-Path $S 'platform_patch.js'))
  Set-Content -LiteralPath (Join-Path $loopDir 'DemoIQ.ps1') -Encoding UTF8 -Value ($marker + "`nparam([string]`$ConfigFile)`n" +
    "while (`$true) { try { `$x = Read-Host 'Pick 7, 8 or 9'; if (`$x -match '^[789]$') { break }; throw 'invalid' } catch { } }")
  $null = Save-SignIn
  $r = Invoke-Seeder (Join-Path $loopDir 'DemoIQ.ps1') $config 'stuck'
  Check 'a seeder stuck at an unattended prompt fails with the reason (no hang)' (-not $r.TimedOut -and $r.Status -match "FAILED stuck-at-prompt: the seeder kept asking 'Pick 7, 8 or 9'")

  # --- per-user MFA: recorded per user and inherited by a resumed pass (generated blocks) ---
  $ps1Text = (Get-Content -LiteralPath $seeder -Raw) -replace "`r`n", "`n"
  $inheritBlock = [regex]::Match($ps1Text, '(?s)# GitHub Copilot adaptation of DemoIQ: inherit the users.*?\n\} catch \{\}\n').Value
  $finalUsersLine = [regex]::Match($ps1Text, '(?m)^      users = @\(if .*\$_pmFailed.*$').Value
  Check 'the seeder records each changed user (pending and final) and inherits them on resume' ($inheritBlock -and $finalUsersLine -and
    ([regex]::Matches($ps1Text, '(?m)^      users = @\(if \(\$Global:PerUserMfaOriginal\)')).Count -eq 2)
  $mfaDir = Join-Path $work 'mfa'; New-Item -ItemType Directory -Force $mfaDir | Out-Null
  $prev = @{ phase = 'prepared'; restored = $false; perUserMfa = @{ users = @(@{ upn = 'a@x'; uid = '1'; state = 'enforced' }, @{ upn = 'b@x'; uid = '2'; state = 'enabled' }) } }
  $prev | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $mfaDir 'tenant_state.json') -Encoding UTF8
  $runInherit = [scriptblock]::Create('$OutputDir = ''' + $mfaDir + "'`n" + '$Global:PerUserMfaOriginal = @{}' + "`n" + $inheritBlock + "`n" + '$Global:PerUserMfaOriginal')
  $inherited = & $runInherit 6>$null
  Check 'a resumed pass inherits the users an unfinished pass changed, with their original state' ($inherited.Count -eq 2 -and $inherited['a@x'].state -eq 'enforced' -and $inherited['b@x'].uid -eq '2')
  $Global:PerUserMfaOriginal = @{ 'a@x' = @{ uid = '1'; state = 'enforced' }; 'b@x' = @{ uid = '2'; state = 'enabled' } }
  $_tsMfaFailures = @('b@x: Forbidden')
  $record = & ([scriptblock]::Create("[ordered]@{ disabledForRun = 2`n" + $finalUsersLine))
  $byUpn = @{}; foreach ($u in @($record.users)) { $byUpn[$u.upn] = $u }
  Check 'the final record marks each user restored or not' ($byUpn['a@x'].restored -eq $true -and $byUpn['b@x'].restored -eq $false -and $byUpn['b@x'].state -eq 'enabled')
  @{ phase = 'restored'; restored = $false; perUserMfa = @{ users = @($record.users) } } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $mfaDir 'tenant_state.json') -Encoding UTF8
  $inherited = & $runInherit 6>$null
  Check 'after an incomplete restore only the users still changed are inherited' ($inherited.Count -eq 1 -and $inherited.ContainsKey('b@x'))
  @{ phase = 'restored'; restored = $true; perUserMfa = @{ users = @($record.users) } } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $mfaDir 'tenant_state.json') -Encoding UTF8
  $inherited = & $runInherit 6>$null
  Check 'a fully restored earlier pass hands nothing on' ($inherited.Count -eq 0)
  Remove-Variable -Name PerUserMfaOriginal -Scope Global -ErrorAction SilentlyContinue

  # --- unpatched and v1 seeders are refused before anything runs ---
  foreach ($case in @(@{ name = 'unpatched'; head = "Write-Host 'upstream seeder'" },
      @{ name = 'v1'; head = "# demoiq-seed: cross-platform patch v1 applied (GitHub Copilot adaptation of DemoIQ - https://aka.ms/demoiq)`nWrite-Host 'v1'" })) {
    $dir = Join-Path $work $case.name
    New-Item -ItemType Directory -Force $dir | Out-Null
    Set-Content -LiteralPath (Join-Path $dir 'DemoIQ.ps1') -Value $case.head -Encoding UTF8
    $null = Save-SignIn
    $null = & $pwshExe -NoProfile -File (Join-Path $S 'run_seeder.ps1') -SeederScript (Join-Path $dir 'DemoIQ.ps1') 2>&1
    $code = $LASTEXITCODE
    $st = Get-Content -LiteralPath (Join-Path $dir 'DemoIQ-Output/.run_seeder_status') -Raw -ErrorAction SilentlyContinue
    Check "run_seeder refuses a $($case.name) seeder" ($code -eq 2 -and $st -match 'not-a-demoiq-seed-seeder')
    Check "... and keeps the sign-in for a valid run ($($case.name))" (Test-DemoIQSessionSecret -Name 'admin-session')
    $l = Invoke-Launch (Join-Path $dir 'DemoIQ.ps1') $config
    Check "launch_seeder refuses a $($case.name) seeder" ($l.Code -eq 2 -and $l.Json.error -match 'generate\.js')
  }
}
finally {
  Clear-DemoIQSessionSecret -Name 'admin-session'
  Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

if ($script:fails) { Write-Host "test_seeder_bootstrap: $($script:fails) FAILURE(S)" -ForegroundColor Red; exit 1 }
Write-Host 'test_seeder_bootstrap: all passed' -ForegroundColor Green
exit 0

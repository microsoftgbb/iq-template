# test_admin_signin.ps1 - the administrator's per-run sign-in (scripts/admin_signin.ps1),
# offline on this OS. Invoke-RestMethod is shadowed by a scripted stub that plays the Entra
# device-code and token endpoints; Start-Sleep is instant. Proves:
#   * the tenant and app come from the deployment receipts, and missing receipts or
#     unconfirmed consent stop before any request
#   * the one-time code reaches only an owner-only file while the sign-in is pending -
#     never stdout or any other stream of the process, unless a person passes
#     -ShowCodeInTerminal - and the file is removed once the sign-in ends
#   * pending/slow-down polling, then a stored, encrypted session for exactly the receipts'
#     tenant and app; no token on stdout; a new sign-in replaces the earlier one
#   * the wrong account, missing consented scopes and a declined sign-in store nothing
#   * -Status reports without secrets and -Clear forgets the sign-in
$ErrorActionPreference = 'Stop'

if ($env:DEMOIQ_TEST_ISOLATED -ne '1') {
  $iso = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-signin-iso-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
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
$S = Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts'
$signIn = Join-Path $S 'admin_signin.ps1'
. (Join-Path $S 'session_store.ps1')
$work = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-signin-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null

$Tenant = 'bbbbbbbb-1111-2222-3333-444444444444'
$App = '00000000-0000-0000-0000-0000000000aa'
$Admin = 'admin@fixturetest.onmicrosoft.com'
$Code = 'FIXCODE42'
$Scopes = @('Calendars.ReadWrite', 'Policy.ReadWrite.ConditionalAccess', 'User.Read', 'offline_access', 'openid')

function ConvertTo-Base64Url([string]$Text) {
  [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Text)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}
function New-Jwt([string]$Upn = $Admin, [string]$Scp = 'Calendars.ReadWrite Policy.ReadWrite.ConditionalAccess User.Read', [string]$Tid = $Tenant) {
  (ConvertTo-Base64Url '{"alg":"none"}') + '.' + (ConvertTo-Base64Url (@{ tid = $Tid; appid = $App; upn = $Upn; scp = $Scp; wids = @('62e90394-69f5-4237-9190-012177145e10') } | ConvertTo-Json -Compress)) + '.x'
}
function New-OAuthError([string]$Code) {
  $er = [System.Management.Automation.ErrorRecord]::new([System.Exception]::new('400 (Bad Request)'), 'MockOAuth', [System.Management.Automation.ErrorCategory]::NotSpecified, $null)
  $er.ErrorDetails = [System.Management.Automation.ErrorDetails]::new((@{ error = $Code; error_description = "AADSTS: $Code" } | ConvertTo-Json -Compress))
  return $er
}
function Write-Receipts([string]$Consent = 'granted', [switch]$Missing) {
  $file = Join-Path $work ("receipts-$Consent" + $(if ($Missing) { '-missing' } else { '' }) + '.json')
  $report = [ordered]@{ result = 'status'; phases = @{} }
  if (-not $Missing) { $report.demoiq = [ordered]@{ tenant_id = $Tenant; client_id = $App; delegated_scopes = $Scopes; consent = $Consent } }
  $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $file -Encoding UTF8
  return $file
}

# ---- scripted stub for the Entra device-code and token endpoints ----
# State lives in one shared object, mutated in place: inside the stub, $script: would
# resolve to admin_signin.ps1's scope (the nearest script on the call stack).
$Stub = @{ Calls = [System.Collections.Generic.List[object]]::new(); Queue = [System.Collections.Generic.Queue[object]]::new(); CodeFile = $null }
function Set-PollQueue([object[]]$Responses) { $Stub.Queue.Clear(); foreach ($x in $Responses) { $Stub.Queue.Enqueue($x) } }
function Invoke-RestMethod {
  param([string]$Method, [string]$Uri, $Body, [string]$ContentType, [int]$TimeoutSec, [Parameter(ValueFromRemainingArguments = $true)]$Rest)
  $Stub.Calls.Add([pscustomobject]@{ uri = $Uri; grant = [string]$Body.grant_type; clientId = [string]$Body.client_id; scope = [string]$Body.scope })
  if ($Uri -eq "https://login.microsoftonline.com/$Tenant/oauth2/v2.0/devicecode") {
    return [pscustomobject]@{ device_code = 'device-123'; user_code = $Code; verification_uri = 'https://microsoft.com/devicelogin'; expires_in = 900; interval = 5
      message = "To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code $Code to authenticate." }
  }
  if ($Uri -eq "https://login.microsoftonline.com/$Tenant/oauth2/v2.0/token") {
    if (-not $Stub.CodeFile) {
      # What the administrator can read while the sign-in is pending.
      $f = Join-Path (Get-DemoIQSessionDirectory) 'admin-signin-code.txt'
      $there = Test-Path -LiteralPath $f
      $Stub.CodeFile = @{ exists = $there; hasCode = ($there -and [System.IO.File]::ReadAllText($f).Contains($Code))
        mode = if ($there -and -not $IsWindows) { [System.IO.File]::GetUnixFileMode($f) } else { $null } }
    }
    # An unscripted poll ends the sign-in instead of polling until the real deadline.
    $next = if ($Stub.Queue.Count) { $Stub.Queue.Dequeue() } else { New-OAuthError 'expired_token' }
    if ($next -is [System.Management.Automation.ErrorRecord]) { throw $next }
    return $next
  }
  throw "unexpected request: $Uri"
}
function Start-Sleep { param([Parameter(ValueFromRemainingArguments = $true)]$Rest) }

function Invoke-SignIn([hashtable]$Arguments) {
  $Stub.Calls.Clear(); $Stub.CodeFile = $null
  $lines = @(& $signIn @Arguments)
  return [pscustomobject]@{ Code = $LASTEXITCODE; Lines = $lines; Text = ($lines -join "`n"); Last = ($lines[-1] | ConvertFrom-Json) }
}
$codeFile = Join-Path (Get-DemoIQSessionDirectory) 'admin-signin-code.txt'

function Invoke-SignInChild([string]$Receipts, [switch]$ShowCode) {
  # A separate pwsh with EVERY output stream captured: what an agent's terminal would show.
  $wrapper = Join-Path $work 'child-signin.ps1'
  $body = @'
function Invoke-RestMethod {
  param([string]$Method, [string]$Uri, $Body, [string]$ContentType, [int]$TimeoutSec, [Parameter(ValueFromRemainingArguments = $true)]$Rest)
  if ($Uri -like '*/devicecode') {
    return [pscustomobject]@{ device_code = 'd'; user_code = '__CODE__'; verification_uri = 'https://microsoft.com/devicelogin'; expires_in = 900; interval = 1
      message = 'To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code __CODE__ to authenticate.' }
  }
  return [pscustomobject]@{ access_token = '__JWT__'; refresh_token = 'child-refresh'; expires_in = 3600 }
}
function Start-Sleep { param([Parameter(ValueFromRemainingArguments = $true)]$Rest) }
$a = @{ ReceiptsFile = '__RECEIPTS__' }
if ('__SHOW__' -eq '1') { $a.ShowCodeInTerminal = $true }
& '__SIGNIN__' @a
exit $LASTEXITCODE
'@
  $body = $body.Replace('__CODE__', $Code).Replace('__JWT__', (New-Jwt)).Replace('__RECEIPTS__', $Receipts).
    Replace('__SIGNIN__', $signIn).Replace('__SHOW__', $(if ($ShowCode) { '1' } else { '0' }))
  Set-Content -LiteralPath $wrapper -Value $body -Encoding UTF8
  $all = & (Get-Process -Id $PID).Path -NoProfile -File $wrapper 2>&1 | Out-String
  return [pscustomobject]@{ Code = $LASTEXITCODE; All = $all }
}

try {
  # --- receipts gate: nothing is requested without a completed demoiq phase ---
  $r = Invoke-SignIn @{ ReceiptsFile = (Write-Receipts -Missing) }
  Check 'no receipts: blocked with the runner command (exit 2)' ($r.Code -eq 2 -and $r.Last.reason -eq 'no-receipts' -and $r.Last.message -match '--with-demoiq --through demoiq')
  Check '... before any request' ($Stub.Calls.Count -eq 0)
  $r = Invoke-SignIn @{ ReceiptsFile = (Write-Receipts -Consent 'waiting') }
  Check 'unconfirmed consent: a person must act (exit 3)' ($r.Code -eq 3 -and $r.Last.reason -eq 'consent-missing' -and $r.Last.message -match '--grant-demoiq-consent' -and $Stub.Calls.Count -eq 0)

  # --- the happy path: pending, slow_down, then tokens ---
  $receipts = Write-Receipts
  $refresh = 'refresh-' + [guid]::NewGuid().ToString('N')
  Set-PollQueue @((New-OAuthError 'authorization_pending'), (New-OAuthError 'slow_down'),
    [pscustomobject]@{ access_token = (New-Jwt); refresh_token = $refresh; expires_in = 3600 })
  $r = Invoke-SignIn @{ ReceiptsFile = $receipts; ExpectedUpn = $Admin }
  Check 'signed in (exit 0, ok)' ($r.Code -eq 0 -and $r.Last.ok -and $r.Last.user -eq $Admin -and $r.Last.tenantId -eq $Tenant -and $r.Last.clientId -eq $App)
  $waiting = $r.Lines[0] | ConvertFrom-Json
  Check 'a waiting line points at the code file and the sign-in page' ($waiting.status -eq 'waiting' -and $waiting.codeFile -eq $codeFile -and $waiting.verificationUri -eq 'https://microsoft.com/devicelogin')
  Check 'the one-time code never reaches stdout' ($r.Text.IndexOf($Code) -lt 0)
  Check 'no token reaches stdout' ($r.Text.IndexOf($refresh) -lt 0 -and $r.Text.IndexOf('access_token') -lt 0)
  Check 'while pending, the code file holds the code' ($Stub.CodeFile -and $Stub.CodeFile.exists -and $Stub.CodeFile.hasCode)
  if (-not $IsWindows) { Check '... and is owner-only (0600)' ($Stub.CodeFile.mode -eq [System.IO.UnixFileMode]'UserRead, UserWrite') }
  Check 'the code file is removed once the sign-in ends' (-not (Test-Path -LiteralPath $codeFile))
  $dc = $Stub.Calls | Select-Object -First 1
  Check 'device code requested for the receipts app with Graph .default + offline_access' ($dc.clientId -eq $App -and $dc.scope -match '^https://graph\.microsoft\.com/\.default offline_access')
  Check 'polled with the device_code grant until tokens arrived' (@($Stub.Calls | Where-Object grant -eq 'urn:ietf:params:oauth:grant-type:device_code').Count -eq 3)
  $stored = Read-DemoIQSessionSecret -Name 'admin-session' | ConvertFrom-Json
  Check 'the session is stored for exactly this tenant, app and admin' ($stored.refreshToken -eq $refresh -and $stored.tenantId -eq $Tenant -and $stored.clientId -eq $App -and $stored.upn -eq $Admin)
  $raw = [System.IO.File]::ReadAllText((Get-DemoIQSessionSecretPath -Name 'admin-session'))
  Check 'the stored session is encrypted at rest' ($raw.IndexOf($refresh) -lt 0)
  if (-not $IsWindows) {
    $mode = [System.IO.File]::GetUnixFileMode((Get-DemoIQSessionSecretPath -Name 'admin-session'))
    Check 'the stored session is owner-only (0600)' ($mode -eq [System.IO.UnixFileMode]'UserRead, UserWrite')
  }

  # --- -Status and -Clear ---
  $r = Invoke-SignIn @{ Status = $true }
  Check '-Status reports the stored sign-in without secrets' ($r.Last.signedIn -and $r.Last.user -eq $Admin -and $r.Text.IndexOf($refresh) -lt 0)
  $r = Invoke-SignIn @{ Clear = $true }
  Check '-Clear forgets the sign-in' ($r.Last.cleared -and -not (Test-DemoIQSessionSecret -Name 'admin-session'))
  Check '-Status then reports no sign-in' (-not (Invoke-SignIn @{ Status = $true }).Last.signedIn)

  # --- refusals store nothing ---
  Set-PollQueue @([pscustomobject]@{ access_token = (New-Jwt -Upn 'other@fixturetest.onmicrosoft.com'); refresh_token = 'r'; expires_in = 3600 })
  $r = Invoke-SignIn @{ ReceiptsFile = $receipts; ExpectedUpn = $Admin }
  Check 'the wrong account: a person must act, nothing stored' ($r.Code -eq 3 -and $r.Last.reason -eq 'wrong-account' -and -not (Test-DemoIQSessionSecret -Name 'admin-session'))
  Set-PollQueue @([pscustomobject]@{ access_token = (New-Jwt -Scp 'User.Read'); refresh_token = 'r'; expires_in = 3600 })
  $r = Invoke-SignIn @{ ReceiptsFile = $receipts }
  Check 'consented scopes missing from the token: consent-missing, nothing stored' ($r.Code -eq 3 -and $r.Last.reason -eq 'consent-missing' -and
    $r.Last.message -match 'Calendars\.ReadWrite' -and -not (Test-DemoIQSessionSecret -Name 'admin-session'))
  Set-PollQueue @([pscustomobject]@{ access_token = (New-Jwt -Tid 'cccccccc-0000-0000-0000-000000000000'); refresh_token = 'r'; expires_in = 3600 })
  $r = Invoke-SignIn @{ ReceiptsFile = $receipts }
  Check 'a token for another tenant is refused' ($r.Code -eq 2 -and $r.Last.reason -eq 'wrong-tenant' -and -not (Test-DemoIQSessionSecret -Name 'admin-session'))
  Set-PollQueue @((New-OAuthError 'authorization_declined'))
  $r = Invoke-SignIn @{ ReceiptsFile = $receipts }
  Check 'a declined sign-in: declined, nothing stored, code file removed' ($r.Code -eq 3 -and $r.Last.reason -eq 'declined' -and
    -not (Test-DemoIQSessionSecret -Name 'admin-session') -and -not (Test-Path -LiteralPath $codeFile))
  Set-PollQueue @([pscustomobject]@{ access_token = (New-Jwt); refresh_token = 'r'; expires_in = 3600 })
  $r = Invoke-SignIn @{ ReceiptsFile = (Write-Receipts -Consent 'unverified') }
  Check 'unverified consent signs in with a warning' ($r.Code -eq 0 -and @($r.Last.warnings) -match '^consent-unverified')

  # --- every output stream, as an agent's terminal would capture it ---
  $c = Invoke-SignInChild $receipts
  Check 'all streams of a separate process: signed in, and the code appears nowhere' ($c.Code -eq 0 -and $c.All -match '"ok":true' -and $c.All.IndexOf($Code) -lt 0)
  Clear-DemoIQSessionSecret -Name 'admin-session'
  $c = Invoke-SignInChild $receipts -ShowCode
  Check '-ShowCodeInTerminal is the only way the code reaches the console' ($c.Code -eq 0 -and $c.All.IndexOf($Code) -ge 0)
  Check 'a sign-in stopped at the receipts gate keeps the earlier one' ((Invoke-SignIn @{ ReceiptsFile = (Write-Receipts -Consent 'waiting') }).Code -eq 3 -and
    (Test-DemoIQSessionSecret -Name 'admin-session'))
  Set-PollQueue @((New-OAuthError 'authorization_declined'))
  $null = Invoke-SignIn @{ ReceiptsFile = $receipts }
  Check 'a new sign-in replaces the earlier one, so a declined one leaves none behind' (-not (Test-DemoIQSessionSecret -Name 'admin-session'))
}
finally {
  Clear-DemoIQSessionSecret -Name 'admin-session'
  Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

if ($script:fails) { Write-Host "test_admin_signin: $($script:fails) FAILURE(S)" -ForegroundColor Red; exit 1 }
Write-Host 'test_admin_signin: all passed' -ForegroundColor Green
exit 0

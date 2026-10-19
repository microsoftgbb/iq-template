# test_preflight_credentials.ps1 - the demo users' credential pre-flight
# (scripts/preflight_credentials.ps1), offline on this OS.
#
# Every case runs the real script in a separate pwsh whose Invoke-RestMethod is a scripted
# stub playing the Entra token endpoint and Microsoft Graph (/me/licenseDetails). The stub
# records every request to a JSON file - never a password or a token. Proves:
#   * an encrypted (cdx-enc-v1) config is refused before any request (exit 2, convert hint)
#   * sign-ins go to the receipts' tenant with the receipts' app; without receipts, or when
#     the runner cannot be read, they fall back to the config's admin domain and
#     existingAppId and say so (source "config"); an explicit -ReceiptsFile must be readable
#   * every token request is a public-client password grant: exactly grant_type, client_id,
#     username, password and scope - no client_assertion(_type), no client_secret
#   * AADSTS errors map to per-user statuses (50126 bad-password, 50034 not-found, 50076
#     ok-pending-mfa, 65001 consent-needed, 700016 app-not-found, 7000218
#     app-not-public-client), and a success reads that user's own licenses, graded per family
#   * a blank password is never sent and a blank UPN is derived (derived:true)
#   * a receipts/config app mismatch stops with app-mismatch before any request
#   * no password or token ever appears in the output, and nothing is written to the workspace
$ErrorActionPreference = 'Stop'

# Self-isolate: a throwaway HOME and session folder, so the real workspace and any real
# sign-in are never read, overwritten or deleted.
if ($env:DEMOIQ_TEST_ISOLATED -ne '1') {
  $iso = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-cred-iso-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
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
$preflight = Join-Path $S 'preflight_credentials.ps1'
$pwshExe = (Get-Process -Id $PID).Path
$work = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-cred-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null

$Tenant = 'dddddddd-1111-2222-3333-444444444444'
$App = '00000000-0000-0000-0000-0000000000d1'
$OtherApp = '00000000-0000-0000-0000-0000000000d2'
$Domain = 'fixturecred.onmicrosoft.com'
$Admin = "admin@$Domain"
$Scope = 'https://graph.microsoft.com/User.Read offline_access openid'
$PublicClientKeys = 'client_id,grant_type,password,scope,username'
$RunnerUp = 'deploy.py up --with-demoiq --through demoiq'

# Fixture secrets (nothing real). The output must never contain any of them.
function New-Secret([string]$Label) { "$Label-" + [guid]::NewGuid().ToString('N').Substring(0, 12) }
$Pw = @{}; $Tokens = @{}
foreach ($i in 1..5) { $Pw[$i] = New-Secret "FixturePw$i"; $Tokens[$i] = New-Secret "fixture-access-$i" }
$RefreshToken = New-Secret 'fixture-refresh'
$Secrets = @($Pw.Values) + @($Tokens.Values) + @($RefreshToken)

function New-Sku([hashtable]$Plans) {
  # One license (SKU) whose service plans carry the given provisioning states.
  @{ skuPartNumber = 'SPE_E5'; servicePlans = @($Plans.Keys | Sort-Object | ForEach-Object { @{ servicePlanName = $_; provisioningStatus = $Plans[$_] } }) }
}
$AllLicensed = @(New-Sku @{ TEAMS1 = 'Success'; EXCHANGE_S_ENTERPRISE = 'Success'; SHAREPOINTENTERPRISE = 'Success'; SHAREPOINTWAC = 'Success' })
$NoTeams = @(New-Sku @{ EXCHANGE_S_ENTERPRISE = 'Success'; SHAREPOINTENTERPRISE = 'PendingProvisioning'; SHAREPOINTWAC = 'Success' })
$TeamsAndMail = @(New-Sku @{ TEAMS1 = 'Success'; EXCHANGE_S_STANDARD = 'Success' })

function New-Config([string]$AppId, $ContentTypes, [hashtable]$Upns = @{}, [hashtable]$Passwords = @{}) {
  # A plain DemoIQ config as the Config Builder saves it (no admin password, no certificate).
  $cfg = [ordered]@{ adminUpn = $Admin; adminPwd = ''; existingAppId = $AppId; existingCertThumbprint = ''; speechResourceId = ''
    demoLanguage = 'en-US'; industry = 'Manufacturing'; companyName = 'Fixture Works'; scenarioIndex = 0 }
  if ($null -ne $ContentTypes) { $cfg.contentTypes = $ContentTypes }
  foreach ($i in 1..5) {
    $cfg["u${i}upn"] = if ($Upns.ContainsKey($i)) { $Upns[$i] } else { "user$i@$Domain" }
    $cfg["u${i}pwd"] = if ($Passwords.ContainsKey($i)) { $Passwords[$i] } else { $Pw[$i] }
    $cfg["u${i}persona"] = 'Engineer'
  }
  return $cfg
}

function Write-Receipts([string]$Name, [switch]$NoDemoIQ) {
  # The `deploy.py status --json` shape; -NoDemoIQ = the demoiq phase has not completed.
  $file = Join-Path $work "$Name-receipts.json"
  $report = [ordered]@{ result = 'status'; phases = @{} }
  if (-not $NoDemoIQ) {
    $report.demoiq = [ordered]@{ tenant_id = $Tenant; client_id = $App; consent = 'granted'
      speech_resource_id = "/subscriptions/$Tenant/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/ai-fixture" }
  }
  $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $file -Encoding UTF8
  return $file
}

function Get-AllSignedIn([object[]]$Licenses = $AllLicensed) {
  $users = @{}
  foreach ($i in 1..5) { $users["user$i@$Domain"] = @{ password = $Pw[$i]; token = $Tokens[$i]; licenses = $Licenses } }
  return $users
}

# ---- the scripted Entra/Graph stub, run in a separate pwsh per case ----
# Its state lives in files (plan in, requests out): inside the stub, $script: would resolve
# to preflight_credentials.ps1's scope, so nothing is kept in script variables.
$StubTemplate = @'
$ErrorActionPreference = 'Stop'
$__tPlan = Get-Content -LiteralPath '__PLAN__' -Raw | ConvertFrom-Json
$__tErrors = @{
  50034 = @('invalid_grant', 'The user account {EUII Hidden} does not exist in the directory. To sign into this application, the account must be added to the directory.')
  50076 = @('invalid_grant', 'Due to a configuration change made by your administrator, or because you moved to a new location, you must use multi-factor authentication to access ''00000003-0000-0000-c000-000000000000''.')
  50126 = @('invalid_grant', 'Error validating credentials due to invalid username or password.')
  65001 = @('invalid_grant', 'The user or administrator has not consented to use the application with ID ''__APP__'' named ''fixture-demoiq-seeder''. Send an interactive authorization request for this user and resource.')
  700016 = @('unauthorized_client', 'Application with identifier ''__APP__'' was not found in the directory ''Fixture''. This can happen if the application has not been installed by the administrator of the tenant or consented to by any user in the tenant. You may have sent your authentication request to the wrong tenant.')
  7000218 = @('invalid_client', 'The request body must contain the following parameter: ''client_assertion'' or ''client_secret''.')
}
function __t_Record([hashtable]$Entry) {
  $all = @(); if (Test-Path -LiteralPath '__REQUESTS__') { $all = @(Get-Content -LiteralPath '__REQUESTS__' -Raw | ConvertFrom-Json) }
  $all += [pscustomobject]$Entry
  ConvertTo-Json -InputObject @($all) -Depth 6 | Set-Content -LiteralPath '__REQUESTS__' -Encoding UTF8
}
function __t_Fail([int]$Number) {
  # An Entra OAuth error as Invoke-RestMethod raises it: the JSON body in ErrorDetails.
  $e = $__tErrors[$Number]
  $json = @{ error = $e[0]; error_codes = @($Number)
    error_description = "AADSTS${Number}: $($e[1])`r`nTrace ID: 00000000-0000-0000-0000-000000000000`r`nCorrelation ID: 00000000-0000-0000-0000-000000000000`r`nTimestamp: 2026-10-19 10:00:00Z" } | ConvertTo-Json -Compress
  $er = [System.Management.Automation.ErrorRecord]::new([System.Exception]::new('Response status code does not indicate success: 400 (Bad Request).'),
    'FixtureOAuth', [System.Management.Automation.ErrorCategory]::InvalidOperation, $null)
  $er.ErrorDetails = [System.Management.Automation.ErrorDetails]::new($json)
  throw $er
}
function Invoke-RestMethod {
  param([string]$Method = 'GET', [string]$Uri, $Body, [string]$ContentType, $Headers, [int]$TimeoutSec,
    [Parameter(ValueFromRemainingArguments = $true)]$Rest)
  if ($Uri -match '^https://login\.microsoftonline\.com/([^/]+)/oauth2/v2\.0/token$') {
    $tenantSeen = $Matches[1]
    $user = [string]$Body['username']
    $plan = $__tPlan.users.$user
    $sent = [string]$Body['password']
    __t_Record @{ kind = 'token'; tenant = $tenantSeen; method = $Method; contentType = $ContentType
      keys = @($Body.Keys | ForEach-Object { [string]$_ } | Sort-Object); grant = [string]$Body['grant_type']
      clientId = [string]$Body['client_id']; username = $user; scope = [string]$Body['scope']
      rightPassword = [bool]($plan -and $sent -ceq [string]$plan.password) }
    if (-not $plan) { __t_Fail 50034 }
    if ($plan.code -in @(700016, 7000218)) { __t_Fail ([int]$plan.code) }   # the app is refused before the password
    if ($sent -cne [string]$plan.password) { __t_Fail 50126 }
    if ($plan.code) { __t_Fail ([int]$plan.code) }                          # MFA/CA and consent: after the password
    return [pscustomobject]@{ token_type = 'Bearer'; scope = 'User.Read openid profile email'; expires_in = 3600
      access_token = [string]$plan.token; refresh_token = '__REFRESH__'; id_token = 'fixture.id.token' }
  }
  if ($Uri -like 'https://graph.microsoft.com/v1.0/me/licenseDetails*') {
    $auth = if ($Headers) { [string]$Headers['Authorization'] } else { '' }
    $owner = $null
    foreach ($p in $__tPlan.users.PSObject.Properties) { if ($p.Value.token -and $auth -ceq ('Bearer ' + $p.Value.token)) { $owner = $p.Name } }
    __t_Record @{ kind = 'graph'; method = $Method; uri = $Uri; user = $owner }
    if (-not $owner) { throw 'licenseDetails was called without a demo user token' }
    return [pscustomobject]@{ value = @($__tPlan.users.$owner.licenses) }
  }
  __t_Record @{ kind = 'unexpected'; method = $Method; uri = $Uri }
  throw "unexpected request: $Method $Uri"
}
& '__PREFLIGHT__' __ARGS__
exit $LASTEXITCODE
'@

function Format-Quoted([string]$Text) { "'" + $Text.Replace("'", "''") + "'" }

$script:AllText = [System.Collections.Generic.List[string]]::new()
$script:AllTokenRequests = [System.Collections.Generic.List[object]]::new()
function Invoke-Preflight([string]$Name, $Config, [string]$Receipts, [hashtable]$Users = @{}, [string]$RawConfig, [switch]$NoRunner) {
  # Every case names a receipts file, or (-NoRunner) runs with an empty PATH so the real
  # runner can never be reached: no Python is found and the receipts cannot be read.
  if (-not $Receipts -and -not $NoRunner) { throw "case $Name passes neither -Receipts nor -NoRunner" }
  $cfgPath = Join-Path $work "$Name-config.json"
  if ($RawConfig) { Set-Content -LiteralPath $cfgPath -Value $RawConfig -Encoding UTF8 }
  elseif ($null -ne $Config) { $Config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $cfgPath -Encoding UTF8 }
  $planPath = Join-Path $work "$Name-plan.json"
  @{ users = $Users } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $planPath -Encoding UTF8
  $requests = Join-Path $work "$Name-requests.json"
  $arguments = '-ConfigPath ' + (Format-Quoted $cfgPath) + $(if ($Receipts) { ' -ReceiptsFile ' + (Format-Quoted $Receipts) } else { '' })
  $wrapper = Join-Path $work "$Name-wrapper.ps1"
  $text = $StubTemplate.Replace("'__PLAN__'", (Format-Quoted $planPath)).Replace("'__REQUESTS__'", (Format-Quoted $requests)).
    Replace("'__PREFLIGHT__'", (Format-Quoted $preflight)).Replace('__ARGS__', $arguments).Replace('__APP__', $App).Replace('__REFRESH__', $RefreshToken)
  Set-Content -LiteralPath $wrapper -Value $text -Encoding UTF8
  $oldPath = $env:PATH
  try {
    if ($NoRunner) { $env:PATH = (New-Item -ItemType Directory -Force (Join-Path $work 'empty-path')).FullName }
    $out = & $pwshExe -NoProfile -File $wrapper 2>&1
    $code = $LASTEXITCODE
  } finally {
    $env:PATH = $oldPath
  }
  $lines = @($out | ForEach-Object { "$_" })
  $script:AllText.Add(($lines -join "`n"))
  $line = $lines | Where-Object { $_.TrimStart().StartsWith('{') } | Select-Object -Last 1
  $reqs = if (Test-Path -LiteralPath $requests) { @(Get-Content -LiteralPath $requests -Raw | ConvertFrom-Json) } else { @() }
  foreach ($q in @($reqs | Where-Object kind -eq 'token')) { $script:AllTokenRequests.Add($q) }
  if (-not $line) { Write-Host "  (case $Name printed no JSON line, exit $code)" -ForegroundColor Yellow }
  $json = if ($line) { $line | ConvertFrom-Json } else { $null }
  $byRole = @{}
  if ($json -and $json.users) { foreach ($u in @($json.users)) { $byRole[[string]$u.role] = $u } }
  return [pscustomobject]@{ Code = $code; Json = $json; Users = $byRole; Requests = $reqs }
}

function Test-PublicClient([object[]]$TokenRequests) {
  # Exactly the five public-client fields, a password grant with the User.Read scope, POSTed as a form.
  return (@($TokenRequests | Where-Object {
        (@($_.keys) -join ',') -ne $PublicClientKeys -or $_.grant -ne 'password' -or $_.scope -ne $Scope -or
        $_.method -ne 'POST' -or $_.contentType -ne 'application/x-www-form-urlencoded' }).Count -eq 0)
}
function Test-NoAssertionOrSecret([object[]]$TokenRequests) {
  return (@($TokenRequests | Where-Object { @($_.keys) -match '^client_(assertion|assertion_type|secret)$' }).Count -eq 0)
}

try {
  Write-Host '-- refusals before any request --'
  $r = Invoke-Preflight 'encrypted' -RawConfig '{"v":"cdx-enc-v1","salt":[1,2,3],"iv":[4,5,6],"data":[7,8,9]}' -Receipts (Write-Receipts 'encrypted')
  Check 'an encrypted config is refused (exit 2, ok:false)' ($r.Code -eq 2 -and $r.Json.ok -eq $false -and $r.Json.reason -eq 'encrypted-config')
  Check '... with the one-time convert_config.ps1 hint' ($r.Json.hint -match 'convert_config\.ps1' -and $r.Json.hint -match 'your own terminal')
  Check '... before any request' ($r.Requests.Count -eq 0)

  $r = Invoke-Preflight 'mismatch' -Config (New-Config $OtherApp @('emails')) -Receipts (Write-Receipts 'mismatch') -Users (Get-AllSignedIn)
  Check 'receipts app differs from the config app: app-mismatch (exit 2, ok:false)' ($r.Code -eq 2 -and $r.Json.ok -eq $false -and $r.Json.reason -eq 'app-mismatch' -and
    $r.Json.clientId -eq $App -and $r.Json.configAppId -eq $OtherApp)
  Check '... with the repoint_config.ps1 hint' ($r.Json.hint -match 'repoint_config\.ps1')
  Check '... and no password was tested (no request)' ($r.Requests.Count -eq 0)
  $r = Invoke-Preflight 'noapp' -Config (New-Config '' @('emails')) -Receipts (Write-Receipts 'noapp') -Users (Get-AllSignedIn)
  Check 'a config without existingAppId is also app-mismatch, with no request' ($r.Code -eq 2 -and $r.Json.reason -eq 'app-mismatch' -and $r.Json.error -match 'names no app' -and $r.Requests.Count -eq 0)

  $r = Invoke-Preflight 'missing' -Receipts (Write-Receipts 'missing')
  Check 'a missing config is a usage error (exit 2)' ($r.Code -eq 2 -and $r.Json.ok -eq $false -and $r.Json.error -match 'Config not found' -and $r.Requests.Count -eq 0)
  $r = Invoke-Preflight 'badjson' -RawConfig ('{"adminUpn":"' + $Admin + '","u1pwd":"' + $Pw[1] + '"x}') -Receipts (Write-Receipts 'badjson')
  Check 'malformed config JSON: usage error naming only the position (no parser text that could quote a password)' ($r.Code -eq 2 -and
    $r.Json.error -match 'not valid JSON \(line \d+, position \d+\)' -and $r.Json.error -notmatch 'unexpected character' -and $r.Requests.Count -eq 0)
  $noAdmin = New-Config $App @('emails'); $noAdmin.adminUpn = ''
  $r = Invoke-Preflight 'noadmin' -Config $noAdmin -Receipts (Write-Receipts 'noadmin')
  Check 'a config without adminUpn is a usage error (exit 2)' ($r.Code -eq 2 -and $r.Json.error -match 'adminUpn' -and $r.Requests.Count -eq 0)
  $r = Invoke-Preflight 'badreceipts' -Config (New-Config $App @('emails')) -Receipts (Join-Path $work 'no-such-receipts.json')
  Check 'a missing -ReceiptsFile is an IO error (exit 2), not a silent fallback' ($r.Code -eq 2 -and $r.Json.error -match 'Receipts file not found' -and $r.Requests.Count -eq 0)
  $garbage = Join-Path $work 'garbage-receipts.json'; Set-Content -LiteralPath $garbage -Value 'not json' -Encoding UTF8
  $r = Invoke-Preflight 'garbagereceipts' -Config (New-Config $App @('emails')) -Receipts $garbage
  Check 'a malformed -ReceiptsFile is an IO error (exit 2)' ($r.Code -eq 2 -and $r.Json.error -match 'Could not read the receipts file' -and $r.Requests.Count -eq 0)

  Write-Host '-- receipts tenant and app; AADSTS mapping (1) and a licensed success --'
  $r = Invoke-Preflight 'receipts' -Config (New-Config $App @('emails', 'chats', 'word')) -Receipts (Write-Receipts 'receipts') -Users @{
    "user1@$Domain" = @{ password = 'not-the-config-password'; token = $Tokens[1] }   # AADSTS50126
    # user2 does not exist in the tenant                                             # AADSTS50034
    "user3@$Domain" = @{ password = $Pw[3]; code = 50076 }
    "user4@$Domain" = @{ password = $Pw[4]; code = 65001 }
    "user5@$Domain" = @{ password = $Pw[5]; token = $Tokens[5]; licenses = $AllLicensed }
  }
  $u = $r.Users
  $tokenReqs = @($r.Requests | Where-Object kind -eq 'token')
  Check 'completed check (exit 0, ok)' ($r.Code -eq 0 -and $r.Json.ok -eq $true)
  Check 'tenant and app from the receipts (source receipts, no note)' ($r.Json.source -eq 'receipts' -and $r.Json.tenant -eq $Tenant -and $r.Json.clientId -eq $App -and -not $r.Json.note)
  Check 'only the five demo users are checked (no admin row)' ($r.Json.checked -eq 5 -and @($r.Json.users | Where-Object { $_.role -notmatch '^user[1-5]$' -or $_.upn -eq $Admin }).Count -eq 0)
  Check 'one token request per demo user' ($tokenReqs.Count -eq 5)
  Check "every token request went to the receipts tenant with the receipts' client_id" (@($tokenReqs | Where-Object { $_.tenant -ne $Tenant -or $_.clientId -ne $App }).Count -eq 0)
  Check 'every token request is a public-client password grant (exactly grant_type, client_id, username, password, scope)' (Test-PublicClient $tokenReqs)
  Check 'no client_assertion, client_assertion_type or client_secret in any token request' (Test-NoAssertionOrSecret $tokenReqs)
  Check "each user's own password from the config was sent" (@($tokenReqs | Where-Object { $_.username -in @("user3@$Domain", "user4@$Domain", "user5@$Domain") -and -not $_.rightPassword }).Count -eq 0)
  Check 'AADSTS50126 -> bad-password' ($u.user1.status -eq 'bad-password' -and $u.user1.passwordValid -eq $false -and $u.user1.reason -eq 'AADSTS50126')
  Check 'AADSTS50034 -> not-found (with the Config Builder fix)' ($u.user2.status -eq 'not-found' -and $u.user2.passwordValid -eq $false -and $u.user2.hint -match 'Config Builder' -and $u.user2.derived -eq $false)
  Check 'AADSTS50076 -> ok-pending-mfa (password valid)' ($u.user3.status -eq 'ok-pending-mfa' -and $u.user3.passwordValid -eq $true)
  Check 'AADSTS65001 -> consent-needed with the --grant-demoiq-consent rerun' ($u.user4.status -eq 'consent-needed' -and $u.user4.passwordValid -eq $true -and
    $u.user4.hint -match [regex]::Escape("$RunnerUp --grant-demoiq-consent"))
  Check 'a success -> ok' ($u.user5.status -eq 'ok' -and $u.user5.passwordValid -eq $true -and $null -eq $u.user5.reason)
  Check 'passed counts ok and ok-pending-mfa only (consent-needed is not ready)' ($r.Json.passed -eq 2 -and $r.Json.failed -eq 3 -and $r.Json.allReady -eq $false)
  $graph = @($r.Requests | Where-Object kind -eq 'graph')
  Check "licenses read once, with the signed-in user's own token" ($graph.Count -eq 1 -and $graph[0].user -eq "user5@$Domain" -and $graph[0].method -eq 'GET' -and
    $graph[0].uri -like 'https://graph.microsoft.com/v1.0/me/licenseDetails*')
  Check 'the signed-in user is graded ok for every family' ($u.user5.licenses.status -eq 'ok' -and $u.user5.licenses.teams -eq 'ok' -and
    $u.user5.licenses.exchange -eq 'ok' -and $u.user5.licenses.sharepoint -eq 'ok')
  Check 'users without a token are unchecked, never guessed' (@('user1', 'user2', 'user3', 'user4' | Where-Object { $u[$_].licenses.status -ne 'unchecked' -or $u[$_].licenses.reason -ne 'no-token' }).Count -eq 0)
  Check 'license summary: families required by contentTypes; 1 ok, 4 unchecked' ((@($r.Json.licenses.requiredFamilies) -join ',') -eq 'teams,exchange,sharepoint' -and
    $r.Json.licenses.checked -eq 1 -and $r.Json.licenses.ok -eq 1 -and $r.Json.licenses.unchecked -eq 4 -and $r.Json.licenses.allLicensed -eq $false)
  Check 'no unexpected request' (@($r.Requests | Where-Object kind -eq 'unexpected').Count -eq 0)

  Write-Host '-- AADSTS mapping (2), license issues, blank password and derived UPN --'
  $types = [ordered]@{ emails = $true; chats = $true; meetings = $false; word = $false }
  $r = Invoke-Preflight 'app-errors' -Config (New-Config $App $types -Upns @{ 5 = '' } -Passwords @{ 4 = '' }) -Receipts (Write-Receipts 'app-errors') -Users @{
    "user1@$Domain" = @{ password = $Pw[1]; code = 700016 }
    "user2@$Domain" = @{ password = $Pw[2]; code = 7000218 }
    "user3@$Domain" = @{ password = $Pw[3]; token = $Tokens[3]; licenses = $NoTeams }
    "DaichiM@$Domain" = @{ password = $Pw[5]; token = $Tokens[5]; licenses = $TeamsAndMail }
  }
  $u = $r.Users
  $tokenReqs = @($r.Requests | Where-Object kind -eq 'token')
  Check 'AADSTS700016 -> app-not-found with the deploy.py rerun' ($u.user1.status -eq 'app-not-found' -and $u.user1.passwordValid -eq $false -and
    $u.user1.reason -eq 'AADSTS700016' -and $u.user1.hint -match [regex]::Escape($RunnerUp))
  Check 'AADSTS7000218 -> app-not-public-client with the deploy.py rerun' ($u.user2.status -eq 'app-not-public-client' -and $u.user2.passwordValid -eq $false -and
    $u.user2.reason -eq 'AADSTS7000218' -and $u.user2.hint -match [regex]::Escape($RunnerUp))
  Check 'a blank password is reported no-password and never sent' ($u.user4.status -eq 'no-password' -and @($tokenReqs | Where-Object username -eq "user4@$Domain").Count -eq 0 -and $tokenReqs.Count -eq 4)
  Check 'a blank UPN is derived from the admin domain (derived:true) and checked' ($u.user5.upn -eq "DaichiM@$Domain" -and $u.user5.derived -eq $true -and $u.user5.status -eq 'ok')
  Check 'public client again: exact fields, no assertion or secret' ((Test-PublicClient $tokenReqs) -and (Test-NoAssertionOrSecret $tokenReqs))
  Check 'object-shape contentTypes: only teams and exchange are required' ((@($r.Json.licenses.requiredFamilies) -join ',') -eq 'teams,exchange')
  Check 'a missing Teams plan is an issue; a pending plan that is not required is not' ($u.user3.status -eq 'ok' -and $u.user3.licenses.status -eq 'issues' -and
    (@($u.user3.licenses.missing) -join ',') -eq 'teams' -and @($u.user3.licenses.pending).Count -eq 0 -and $u.user3.licenses.sharepoint -eq 'pending' -and
    $u.user3.licenses.detail -match 'Teams')
  Check 'a user with every required plan is ok (an unrequired missing plan does not count)' ($u.user5.licenses.status -eq 'ok' -and $u.user5.licenses.sharepoint -eq 'missing')
  Check 'license summary: 2 checked (1 ok, 1 issue), 3 unchecked' ($r.Json.licenses.checked -eq 2 -and $r.Json.licenses.ok -eq 1 -and $r.Json.licenses.issues -eq 1 -and $r.Json.licenses.unchecked -eq 3)
  Check 'license issues do not change passed/allReady' ($r.Json.passed -eq 2 -and $r.Json.failed -eq 3 -and $r.Json.allReady -eq $false)

  Write-Host '-- no receipts: the config tenant and app, said so --'
  $r = Invoke-Preflight 'fallback' -Config (New-Config $OtherApp $null) -Receipts (Write-Receipts 'fallback' -NoDemoIQ) -Users (Get-AllSignedIn)
  $tokenReqs = @($r.Requests | Where-Object kind -eq 'token')
  Check 'without receipts: source config, tenant = admin domain, app = existingAppId, with a note' ($r.Code -eq 0 -and $r.Json.source -eq 'config' -and
    $r.Json.tenant -eq $Domain -and $r.Json.clientId -eq $OtherApp -and $r.Json.note -match 'no DemoIQ receipts')
  Check "... and the requests went to the admin domain's token endpoint with the config app" ($tokenReqs.Count -eq 5 -and
    @($tokenReqs | Where-Object { $_.tenant -ne $Domain -or $_.clientId -ne $OtherApp }).Count -eq 0)
  Check 'all five ready and licensed (no contentTypes = every family required)' ($r.Json.allReady -eq $true -and $r.Json.passed -eq 5 -and
    $r.Json.licenses.allLicensed -eq $true -and $r.Json.licenses.checked -eq 5 -and (@($r.Json.licenses.requiredFamilies) -join ',') -eq 'teams,exchange,sharepoint')
  $r = Invoke-Preflight 'norunner' -Config (New-Config $OtherApp @('emails')) -Users (Get-AllSignedIn) -NoRunner
  $tokenReqs = @($r.Requests | Where-Object kind -eq 'token')
  Check 'runner unavailable (no -ReceiptsFile, no Python): falls back to the config and says why' ($r.Code -eq 0 -and $r.Json.source -eq 'config' -and
    $r.Json.tenant -eq $Domain -and $r.Json.note -match 'could not be read' -and $tokenReqs.Count -eq 5 -and
    @($tokenReqs | Where-Object { $_.tenant -ne $Domain -or $_.clientId -ne $OtherApp }).Count -eq 0)

  Write-Host '-- secrets --'
  Check "across all cases, every token request was a public-client grant without assertion or secret ($($script:AllTokenRequests.Count) requests)" ($script:AllTokenRequests.Count -eq 19 -and
    (Test-PublicClient $script:AllTokenRequests.ToArray()) -and (Test-NoAssertionOrSecret $script:AllTokenRequests.ToArray()))
  $all = $script:AllText -join "`n"
  Check 'no password or token appears in any output' (@($Secrets | Where-Object { $all.Contains($_) }).Count -eq 0 -and $all -notmatch 'Bearer ')
  # HOME is isolated on macOS/Linux only (Windows resolves $HOME from USERPROFILE).
  if (-not $IsWindows) { Check 'nothing was written to the DemoIQ workspace' (-not (Test-Path -LiteralPath (Join-Path $HOME '.copilot'))) }
}
finally {
  Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

if ($script:fails) { Write-Host "test_preflight_credentials: $($script:fails) FAILURE(S)" -ForegroundColor Red; exit 1 }
Write-Host 'test_preflight_credentials: all passed' -ForegroundColor Green
exit 0

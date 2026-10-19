# test_cleanup_run.ps1 - an END-TO-END cleanup run, offline on this OS (Windows, macOS, Linux).
#
# Generates a REAL seeder and its plain config from the fixture (generate.js inlines the
# cleanup engine), creates a seed run folder holding a one-run cleanup.config and a seed
# ledger - plus a NEWER seed run folder that never wrote a manifest (cancelled at the
# content-preview gate or failed early), which no cleanup may touch - creates the cleanup run
# folder with new_run.ps1 -Kind cleanup, stores a fake administrator sign-in in the encrypted
# session store, and runs the REAL run_seeder.ps1 -Cleanup in a fresh pwsh where
# Invoke-RestMethod (and Invoke-WebRequest) are shadowed by a recording stub that plays the
# Entra token endpoint and Microsoft Graph over fake data (tenant preparation answered with
# no-change shapes). Start-Sleep is instant. Nothing leaves the machine. Three scenarios:
#   * LastRun, everything removed  -> heartbeat DONE
#   * AllRuns with the administrator's OneDrive copy (which also lists a run whose folder is
#     on another machine), a 403 on one user's meeting delete, a demo user whose password
#     grant fails with AADSTS50126, and -ArchiveLocalDeliverables -> DONE failed=2
#   * no manifest anywhere and no logs -> FAILED cleanup-failed, the tenant still restored
# Proves: content generation and seeding (steps 4-13) are skipped (no content-creation
# request, no run-mode menu); the team and chats are deleted with the ADMINISTRATOR's
# token and each user's items with THAT user's token; demo users sign in as a public client;
# the sign-in is consumed and out of the environment before the first request;
# cleanup_result.json, tenant_state.json, the pruned manifest and the cleared seed ledger;
# and monitor_seeding.ps1 / run_history.ps1 report the run as kind cleanup.
$ErrorActionPreference = 'Stop'

# Self-isolate: a throwaway HOME (USERPROFILE on Windows) and session folder, so the real
# workspace and any real sign-in are never read, overwritten or deleted.
if ($env:DEMOIQ_TEST_ISOLATED -ne '1') {
  $iso = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-cleanrun-iso-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
  New-Item -ItemType Directory -Force (Join-Path $iso 'home') | Out-Null
  $saved = @{ HOME = $env:HOME; USERPROFILE = $env:USERPROFILE; DEMOIQ_SESSION_DIR = $env:DEMOIQ_SESSION_DIR
    DEMOIQ_TEST_ISOLATED = $env:DEMOIQ_TEST_ISOLATED; DEMOIQ_TEST_ISO_ROOT = $env:DEMOIQ_TEST_ISO_ROOT }
  $env:HOME = Join-Path $iso 'home'; $env:DEMOIQ_SESSION_DIR = Join-Path $iso 'session'; $env:DEMOIQ_TEST_ISOLATED = '1'
  $env:DEMOIQ_TEST_ISO_ROOT = $iso
  if ($IsWindows) { $env:USERPROFILE = Join-Path $iso 'home' }
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
. (Join-Path $S 'session_store.ps1')

# The cleanup run resolves the workspace from HOME and clears seed ledgers in it: run only
# when that workspace is this test's throwaway folder.
$ws = Get-DemoIQWorkspace
if (-not $env:DEMOIQ_TEST_ISO_ROOT -or -not $ws.StartsWith($env:DEMOIQ_TEST_ISO_ROOT)) {
  Write-Host "  SKIP end-to-end cleanup run: the DemoIQ workspace ($ws) cannot be isolated on this OS" -ForegroundColor DarkYellow
  exit 0
}
$work = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-cleanrun-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null

$Tenant = 'bbbbbbbb-1111-2222-3333-444444444444'
$TenantDomain = 'fixturetest.onmicrosoft.com'
$App = '00000000-0000-0000-0000-000000000001'
$Admin = 'admin@fixturetest.onmicrosoft.com'
$Users = @('AdilE', 'BillieV', 'CoraT', 'CoreyG', 'DaichiM' | ForEach-Object { "$_@$TenantDomain" })
$AdminLower = $Admin.ToLowerInvariant(); $Adile = $Users[0].ToLowerInvariant(); $Billie = $Users[1].ToLowerInvariant(); $Cora = $Users[2].ToLowerInvariant(); $Daichi = $Users[4].ToLowerInvariant()
$TeamId = '11111111-2222-3333-4444-555555555555'
$EventId = 'AAMkADfixtureEvent1='
$FileName = 'North_Works_Runbook_20261018_0900.docx'
$GroupChat = '19:seedchat@thread.v2'
$MeetingChat = '19:meeting_pumpa@thread.v2'
$G = 'https://graph.microsoft.com/v1.0'
$OneDriveUri = "$G/me/drive/root:/DemoIQ-Output/cleanup.config:/content"
$ConsentFix = 'deploy.py up --with-demoiq --through demoiq --grant-demoiq-consent'

function ConvertTo-Base64Url([string]$Text) {
  [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Text)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}
$scp = 'Policy.Read.All Policy.ReadWrite.ConditionalAccess Policy.ReadWrite.AuthenticationMethod User.Read.All Group.ReadWrite.All Chat.Create Files.ReadWrite.All Sites.Read.All Mail.Read People.Read.All OnlineMeetingTranscript.Read.All Chat.Read ChannelMessage.Read.All ExternalItem.Read.All Chat.ManageDeletion.All Calendars.ReadWrite Mail.ReadWrite'
$AdminJwt = (ConvertTo-Base64Url '{"alg":"none","typ":"JWT"}') + '.' +
  (ConvertTo-Base64Url (@{ tid = $Tenant; appid = $App; upn = $Admin; scp = $scp } | ConvertTo-Json -Compress)) + '.fixture'

function Save-SignIn {
  $json = @{ refreshToken = 'fixture-refresh-token'; tenantId = $Tenant; clientId = $App; upn = $Admin; signedInAt = (Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json -Compress
  return [bool](Save-DemoIQSessionSecret -Name 'admin-session' -Value $json)
}

# ---- the seed run being cleaned up (A): one run in its manifest, a seed ledger, no logs ----
$seedRunId = '20261018_0900__seed__fixturetest'
$seedRun = Join-Path $ws "runs/$seedRunId"
$seedManifest = Join-Path $seedRun 'DemoIQ-Output/cleanup.config'
$seedLedger = Join-Path $seedRun 'DemoIQ-Output/seed_ledger.json'
# ---- a NEWER seed run folder that was never launched (cancelled at the content-preview
# gate) or failed before writing its manifest: run.json, the preview and a ledger, no
# cleanup.config. Cleaning up A must never touch it. ----
$neverRunId = '20261019_0800__never__fixturetest'
$neverRun = Join-Path $ws "runs/$neverRunId"
$neverLedger = Join-Path $neverRun 'DemoIQ-Output/seed_ledger.json'
$neverPreview = Join-Path $neverRun 'DemoIQ-Output/content_preview.html'
# A run only the administrator's OneDrive copy knows (its seed folder is on another machine).
$OneDriveOnlyRun = '2026-10-17 08:00:00'
$OneDriveOnlyTeam = '99999999-8888-7777-6666-555555555555'
function New-SeedManifestJson([switch]$WithOneDriveOnlyRun) {
  $run = [ordered]@{
    timestamp = '2026-10-18 09:00:00'; mode = 'Fast'; tenantId = $Tenant; appId = $App; certThumbprint = ''; adminUpn = $Admin
    caExclusionGroup = @{ id = 'ca-group-1' }
    team = [ordered]@{ groupId = $TeamId; channels = @(@{ name = 'Pump A'; id = '19:pumpa@thread.tacv2' }) }
    chats = @([ordered]@{ topic = 'Pump A war room'; id = $GroupChat; messages = @() })
    emails = @([ordered]@{ subject = 'Pump A vibration'; messages = @() })
    emailMessages = @()
    channels = @()
    files = @([ordered]@{ type = 'Word'; name = $FileName; url = 'https://example.invalid/runbook'; owner = $Users[0] })
    meetings = @([ordered]@{ subject = 'Pump A review'; eventId = $EventId; organizer = $Users[1] })
    meetingContent = @()
    users = $Users
  }
  $runs = @($run)
  if ($WithOneDriveOnlyRun) {
    $runs = @([ordered]@{ timestamp = $OneDriveOnlyRun; mode = 'Fast'; tenantId = $Tenant; appId = $App; adminUpn = $Admin
        team = [ordered]@{ groupId = $OneDriveOnlyTeam; channels = @() }; users = $Users }) + $runs
  }
  [ordered]@{
    tableOfContents = @($runs | ForEach-Object { @{ timestamp = $_.timestamp; mode = 'Fast'; admin = $Admin } })
    tenantConfig = [ordered]@{ tenantId = $Tenant; appId = $App; adminUpn = $Admin; caExclusionGroupId = 'ca-group-1'; teamGroupId = $TeamId }
    runs = $runs
  } | ConvertTo-Json -Depth 10
}
function Set-SeedRun([switch]$WithoutManifest) {
  foreach ($d in 'DemoIQ-Output', 'logs') { New-Item -ItemType Directory -Force (Join-Path $seedRun $d), (Join-Path $neverRun $d) | Out-Null }
  Set-Content -LiteralPath (Join-Path $seedRun 'run.json') -Encoding utf8 -Value (
    [ordered]@{ runId = $seedRunId; kind = 'seed'; createdAt = '2026-10-18T08:55:00'; tenant = $TenantDomain; status = 'launched' } | ConvertTo-Json)
  if ($WithoutManifest) { Remove-Item -LiteralPath $seedManifest -Force -ErrorAction SilentlyContinue }
  else { Set-Content -LiteralPath $seedManifest -Value (New-SeedManifestJson) -Encoding utf8 }
  Set-Content -LiteralPath $seedLedger -Value '{"schema":"demoiq-seed-ledger/1","items":[]}' -Encoding utf8
  Set-Content -LiteralPath (Join-Path $neverRun 'run.json') -Encoding utf8 -Value (
    [ordered]@{ runId = $neverRunId; kind = 'seed'; createdAt = '2026-10-19T08:00:00'; tenant = $TenantDomain; status = 'created' } | ConvertTo-Json)
  Set-Content -LiteralPath $neverPreview -Value '<html>content preview</html>' -Encoding utf8
  Set-Content -LiteralPath $neverLedger -Value '{"schema":"demoiq-seed-ledger/1","items":[{"id":"email-1","status":"seeded"}]}' -Encoding utf8
}
function Test-NeverRunUntouched {
  (Test-Path -LiteralPath $neverLedger) -and (Test-Path -LiteralPath $neverPreview) -and (Test-Path -LiteralPath (Join-Path $neverRun 'run.json')) -and
    -not (Test-Path -LiteralPath (Join-Path $ws "archive/${neverRunId}__DemoIQ-Output.zip"))
}

# ---- the fake tenant (stateless): what each user's /me lookups return ----
function New-Settings([string]$OneDriveManifest, [string[]]$BadPassword = @(), [string[]]$Denied = @()) {
  @{
    adminJwt = $AdminJwt; tenant = $Tenant; admin = $AdminLower
    oneDriveManifest = $OneDriveManifest; badPassword = @($BadPassword); denied = @($Denied)
    graph = @{
      events = @{ $Cora = @(@{ id = 'ev-residue'; subject = 'Canceled: Pump A review' }, @{ id = 'ev-keep'; subject = 'Team sync' }) }
      messages = @{
        $Adile = @(@{ id = 'm-sent'; subject = 'Pump A vibration' }, @{ id = 'm-keep'; subject = 'Quarterly numbers' })
        $Cora = @(@{ id = 'm-reply'; subject = 'RE: Pump A vibration' }, @{ id = 'm-cancel'; subject = 'Canceled: Pump A review' })
        $AdminLower = @(@{ id = 'm-share'; subject = "AdilE shared 'North_Works_Runbook' with you" }, @{ id = 'm-welcome'; subject = 'Welcome' })
      }
      drives = @{ $Adile = @(@{ id = 'item-runbook'; name = $FileName }, @{ id = 'item-keep'; name = 'Unrelated.docx' }) }
      chats = @(
        @{ id = $GroupChat; topic = 'Pump A war room'; chatType = 'group'; members = @($Adile, $Billie, $AdminLower) },
        @{ id = $MeetingChat; topic = 'Pump A review'; chatType = 'meeting'; members = @($Billie, $Cora) },
        @{ id = '19:coffee@thread.v2'; topic = 'Coffee'; chatType = 'group'; members = @($Cora, $Daichi) })
    }
  }
}

# ---- the wrapper: recording stubs, then the real launcher in cleanup mode ----
$WrapperTemplate = @'
$ErrorActionPreference = 'Stop'
# Stub state lives in ONE object (never $script:, which resolves to whichever script runs).
$__T = Get-Content -LiteralPath '__SETTINGS__' -Raw | ConvertFrom-Json -AsHashtable
$__T.log = '__REQUESTS__'
function __t_Record([hashtable]$Entry) {
  Add-Content -LiteralPath $__T.log -Value ($Entry | ConvertTo-Json -Compress -Depth 6) -Encoding utf8
}
function __t_Error([int]$Status, [string]$Json) {
  # Shaped like an Invoke-RestMethod failure: Response.StatusCode.value__ and the body.
  $exc = [System.Exception]::new("Response status code does not indicate success: $Status.")
  $resp = [pscustomobject]@{ StatusCode = [pscustomobject]@{ value__ = $Status }; Headers = [pscustomobject]@{ RetryAfter = $null } }
  $exc | Add-Member -NotePropertyName Response -NotePropertyValue $resp -Force
  $er = [System.Management.Automation.ErrorRecord]::new($exc, 'StubHttp', [System.Management.Automation.ErrorCategory]::NotSpecified, $null)
  $er.ErrorDetails = [System.Management.Automation.ErrorDetails]::new($Json)
  return $er
}
function __t_Items([string]$Kind, [string]$User) {
  $map = $__T.graph[$Kind]
  if (-not $map -or -not $User -or -not $map.ContainsKey($User)) { return }
  foreach ($i in @($map[$User])) { [pscustomobject]$i }
}
function Invoke-WebRequest {
  [CmdletBinding()]
  param([string]$Method = 'GET', [string]$Uri, [Parameter(ValueFromRemainingArguments = $true)]$Rest)
  __t_Record @{ kind = 'iwr'; method = $Method.ToUpperInvariant(); uri = $Uri }
  throw "unexpected Invoke-WebRequest: $Method $Uri"
}
function Start-Sleep {
  [CmdletBinding()]
  param([Parameter(Position = 0)][double]$Seconds, [int]$Milliseconds)
  __t_Record @{ kind = 'sleep'; seconds = $Seconds }
}
function Invoke-RestMethod {
  [CmdletBinding()]
  param([string]$Method = 'GET', [string]$Uri, $Body, [string]$ContentType, $Headers, [int]$TimeoutSec,
    [Parameter(ValueFromRemainingArguments = $true)]$Rest)
  $verb = $Method.ToUpperInvariant()
  $inEnv = -not [string]::IsNullOrEmpty($env:DEMOIQ_ADMIN_SESSION)
  if ($Uri -match '^https://login\.microsoftonline\.com/([^/]+)/oauth2/v2\.0/token$') {
    $tenantInUri = $Matches[1]
    $grant = [string]$Body['grant_type']; $user = ([string]$Body['username']).ToLowerInvariant()
    __t_Record @{ kind = 'token'; tenant = $tenantInUri; grant = $grant; clientId = [string]$Body['client_id']; user = $user
      scope = [string]$Body['scope']; keys = ((@($Body.Keys | ForEach-Object { [string]$_ }) | Sort-Object) -join ','); sessionInEnv = $inEnv }
    if ($grant -eq 'refresh_token') { return [pscustomobject]@{ access_token = $__T.adminJwt; expires_in = 3600; refresh_token = 'rotated-refresh-token'; token_type = 'Bearer' } }
    if ($grant -eq 'password') {
      if (@($__T.badPassword) -contains $user) {
        throw (__t_Error 400 '{"error":"invalid_grant","error_description":"AADSTS50126: Error validating credentials due to invalid username or password.","error_codes":[50126]}')
      }
      return [pscustomobject]@{ access_token = ('usertok.' + $user); expires_in = 3600; token_type = 'Bearer' }
    }
    throw "unexpected grant: $grant"
  }
  $auth = ''; if ($Headers) { $auth = [string]$Headers['Authorization'] }
  $who = if ($auth -eq ('Bearer ' + $__T.adminJwt)) { 'admin' } elseif ($auth -like 'Bearer usertok.*') { $auth.Substring(15) } elseif ($auth) { 'other' } else { 'none' }
  $me = if ($who -eq 'admin') { $__T.admin } elseif ($who -notin @('other', 'none')) { $who } else { $null }
  $bodyText = $null
  if ($null -ne $Body) {
    $bodyText = if ($Body -is [byte[]]) { [System.Text.Encoding]::UTF8.GetString($Body) } elseif ($Body -is [string]) { $Body } else { '<' + $Body.GetType().Name + '>' }
    if ($bodyText.Length -gt 6000) { $bodyText = $bodyText.Substring(0, 6000) }
  }
  __t_Record @{ kind = 'http'; method = $verb; uri = $Uri; who = $who; sessionInEnv = $inEnv; body = $bodyText }
  if (@($__T.denied) -contains "$verb $Uri $who") {
    throw (__t_Error 403 '{"error":{"code":"ErrorAccessDenied","message":"Access is denied. Check credentials and try again."}}')
  }
  $qi = $Uri.IndexOf('?')
  $path = $(if ($qi -ge 0) { $Uri.Substring(0, $qi) } else { $Uri }) -replace '^https://graph\.microsoft\.com', ''
  $query = if ($qi -ge 0) { $Uri.Substring($qi + 1) } else { '' }
  $route = "$verb $path"
  # Steps 1-3 and the restore region: a tenant that needs no change.
  if ($route -eq 'GET /v1.0/organization') { return [pscustomobject]@{ value = @([pscustomobject]@{ id = $__T.tenant; displayName = 'Fixture' }) } }
  if ($route -match '^GET /v1\.0/users/[^/]+/drive$') { return [pscustomobject]@{ id = 'drive-admin'; driveType = 'business' } }
  if ($route -eq 'GET /v1.0/policies/identitySecurityDefaultsEnforcementPolicy') { return [pscustomobject]@{ isEnabled = $false } }
  if ($route -eq 'GET /v1.0/identity/conditionalAccess/policies') { return [pscustomobject]@{ value = @() } }
  if ($route -eq 'GET /v1.0/groups' -and $query -match 'Demo-Seeder-MFA-Exclusion') { return [pscustomobject]@{ value = @([pscustomobject]@{ id = 'ca-group-1'; displayName = 'Demo-Seeder-MFA-Exclusion' }) } }
  if ($route -eq 'GET /v1.0/groups/ca-group-1/members') { return [pscustomobject]@{ value = @() } }
  if ($route -eq 'POST /v1.0/groups/ca-group-1/members/$ref') { return $null }
  if ($route -match '^DELETE /v1\.0/groups/ca-group-1/members/[^/]+/\$ref$') { return $null }
  if ($route -match '^GET /v1\.0/users/([^/]+)$') { return [pscustomobject]@{ id = ('id-' + $Matches[1].ToLowerInvariant()); userPrincipalName = $Matches[1] } }
  if ($route -match '^GET /beta/users/[^/]+/authentication/requirements$') { return [pscustomobject]@{ perUserMfaState = 'disabled' } }
  # The cleanup engine.
  if ($route -eq 'GET /v1.0/me/drive/root:/DemoIQ-Output/cleanup.config:/content') {
    if ($who -eq 'admin' -and $__T.oneDriveManifest) { return [string]$__T.oneDriveManifest }
    throw (__t_Error 404 '{"error":{"code":"itemNotFound","message":"The resource could not be found."}}')
  }
  if ($route -eq 'PUT /v1.0/me/drive/root:/DemoIQ-Output/cleanup.config:/content') { return [pscustomobject]@{ id = 'od-manifest'; name = 'cleanup.config' } }
  if ($route -match '^DELETE /v1\.0/(groups|chats)/') { return $null }
  if ($route -eq 'GET /v1.0/me/events') { return [pscustomobject]@{ value = @(__t_Items 'events' $me) } }
  if ($route -eq 'GET /v1.0/me/messages') {
    $items = @(__t_Items 'messages' $me)
    if ($query -match '\$search="subject:(.*?)"') {
      $term = [uri]::UnescapeDataString($Matches[1])
      $items = @($items | Where-Object { $_.subject.IndexOf($term, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 })
    }
    return [pscustomobject]@{ value = $items }
  }
  if ($route -eq 'GET /v1.0/me/drive/root/children') { return [pscustomobject]@{ value = @(__t_Items 'drives' $me) } }
  if ($route -eq 'GET /v1.0/me/chats') {
    return [pscustomobject]@{ value = @(@($__T.graph.chats) | Where-Object { @($_.members) -contains $me } |
        ForEach-Object { [pscustomobject]@{ id = $_.id; topic = $_.topic; chatType = $_.chatType } }) }
  }
  if ($route -match '^DELETE /v1\.0/me/(events|messages|drive/items)/') { return $null }
  # Anything else is recorded and answered, so the run continues.
  __t_Record @{ kind = 'unknown'; method = $verb; uri = $Uri; who = $who }
  if ($verb -eq 'GET') { return [pscustomobject]@{ value = @() } }
  return $null
}
& '__RUN_SEEDER__' -SeederScript '__SEEDER__' -ConfigFile '__CONFIG__' -Cleanup -CleanupScope '__SCOPE__' __EXTRA__
exit $LASTEXITCODE
'@

function Read-JsonFile([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return $null }
  try { return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json) } catch { return $null }
}

function Invoke-CleanupRun([string]$Name, [string]$Scope, [hashtable]$Settings, [switch]$Archive) {
  # The cleanup run folder exactly as the skill makes it, with the generated seeder in it.
  $nr = & (Join-Path $S 'new_run.ps1') -ScenarioTitle "purge $Name" -Tenant $TenantDomain -OutputFolder $ws -Kind cleanup | ConvertFrom-Json
  $rf = $nr.runFolder
  foreach ($f in 'DemoIQ.ps1', 'DemoIQ_config.json', 'run_identity.json') { Copy-Item -LiteralPath (Join-Path $gen $f) -Destination $rf }
  $seeder = Join-Path $rf 'DemoIQ.ps1'; $config = Join-Path $rf 'DemoIQ_config.json'
  $settingsFile = Join-Path $work "$Name-settings.json"
  $requests = Join-Path $work "$Name-requests.jsonl"
  $wrapper = Join-Path $work "$Name-wrapper.ps1"
  $Settings | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $settingsFile -Encoding utf8
  $q = { param($p) $p.Replace("'", "''") }
  $text = $WrapperTemplate.Replace('__SETTINGS__', (& $q $settingsFile)).Replace('__REQUESTS__', (& $q $requests)).
    Replace('__RUN_SEEDER__', (& $q (Join-Path $S 'run_seeder.ps1'))).Replace('__SEEDER__', (& $q $seeder)).
    Replace('__CONFIG__', (& $q $config)).Replace('__SCOPE__', $Scope).Replace('__EXTRA__', $(if ($Archive) { '-ArchiveLocalDeliverables' } else { '' }))
  Set-Content -LiteralPath $wrapper -Value $text -Encoding utf8
  $signedIn = Save-SignIn
  $console = @(& $pwshExe -NoProfile -File $wrapper 2>&1 | ForEach-Object { [string]$_ })
  $code = $LASTEXITCODE
  $log = Get-ChildItem -LiteralPath (Join-Path $rf 'logs') -Filter 'DemoIQ_*.log' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  $entries = @()
  if (Test-Path -LiteralPath $requests) { $entries = @(Get-Content -LiteralPath $requests | Where-Object { $_ } | ForEach-Object { $_ | ConvertFrom-Json }) }
  $status = [string](Get-Content -LiteralPath (Join-Path $rf 'DemoIQ-Output/.run_seeder_status') -Raw -ErrorAction SilentlyContinue)
  return [pscustomobject]@{
    Name = $Name; RunId = $nr.runId; RunFolder = $rf; Code = $code; SignedIn = $signedIn; Console = $console
    Status = ($status -split "`t", 2)[-1].Trim()
    Result = Read-JsonFile (Join-Path $rf 'DemoIQ-Output/cleanup_result.json')
    TenantState = Read-JsonFile (Join-Path $rf 'DemoIQ-Output/tenant_state.json')
    Log = if ($log) { Get-Content -LiteralPath $log.FullName -Raw } else { '' }
    Http = @($entries | Where-Object { $_.kind -eq 'http' })
    Tokens = @($entries | Where-Object { $_.kind -eq 'token' })
    Unknown = @($entries | Where-Object { $_.kind -eq 'unknown' })
    Iwr = @($entries | Where-Object { $_.kind -eq 'iwr' })
    Sleeps = @($entries | Where-Object { $_.kind -eq 'sleep' })
  }
}

function Show-Diagnostics($Run) {
  # On a failure, enough context to see where the run went.
  Write-Host "    [$($Run.Name)] exit $($Run.Code), heartbeat '$($Run.Status)'" -ForegroundColor DarkGray
  $Run.Console | Select-Object -Last 25 | ForEach-Object { Write-Host "    | $_" -ForegroundColor DarkGray }
}
function Get-Http($Run, [string]$Method, [string]$Uri) { @($Run.Http | Where-Object { $_.method -eq $Method -and $_.uri -eq $Uri }) }
function Get-Who($Run, [string]$Method, [string]$Uri) { (@(Get-Http $Run $Method $Uri) | ForEach-Object { $_.who }) -join ',' }
function Get-ContentCreation($Run) {
  # Any of these means steps 4-13 (content generation and seeding) ran.
  @($Run.Http | Where-Object {
      ($_.method -eq 'POST' -and $_.uri -match '/sendMail$|/me/messages$|/messages/[^/]+/(send|reply|replyAll|createReply|createReplyAll|forward)$|/chats$|/chats/[^/]+/messages$|/channels(/[^/]+/messages)?$|/events$|/onlineMeetings$|/teams$|/v1\.0/groups$|/invite$|/copilot/') -or
      ($_.method -eq 'PUT' -and $_.uri -match ':/content$' -and $_.uri -ne $OneDriveUri) -or
      ($_.method -eq 'PUT' -and $_.uri -match '/team$') -or
      ($_.method -eq 'PATCH' -and $_.uri -match '/me/messages/')
    }) + @($Run.Iwr)
}
function Invoke-Monitor([string]$RunFolder) {
  $out = & $pwshExe -NoProfile -File (Join-Path $S 'monitor_seeding.ps1') -OutputFolder $RunFolder 2>$null
  try { return (($out | Select-Object -Last 1) | ConvertFrom-Json) } catch { return $null }
}
function Get-HistoryRun([string]$RunId) {
  $out = & $pwshExe -NoProfile -File (Join-Path $S 'run_history.ps1') -LogsFolder (Join-Path $ws 'logs') 2>$null
  try { return @((($out | Select-Object -Last 1) | ConvertFrom-Json).runs | Where-Object { $_.runId -eq $RunId }) | Select-Object -First 1 } catch { return $null }
}

try {
  # ---- the seeder and its plain config, generated once from the fixture ----
  $gen = Join-Path $work 'generated'
  $prefsFile = Join-Path $work 'prefs.json'
  Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'fixtures/test-prefs.json') -Destination $prefsFile
  $null = & node (Join-Path $S 'generate.js') --html (Join-Path $root 'engine/DemoIQ.html') --prefs $prefsFile --emit both --out-dir $gen 2>$null
  Check 'generate.js emitted the seeder (cleanup engine inlined) and a plain config' ($LASTEXITCODE -eq 0 -and
    (Test-Path (Join-Path $gen 'DemoIQ.ps1')) -and (Select-String -LiteralPath (Join-Path $gen 'DemoIQ.ps1') -Pattern 'function Invoke-DemoIQCleanup ' -Quiet) -and
    (Read-JsonFile (Join-Path $gen 'DemoIQ_config.json')).adminUpn -eq $Admin)

  # ======================================================================== 1. LastRun
  Set-SeedRun
  $r1 = Invoke-CleanupRun 'last' 'LastRun' (New-Settings)
  $res = $r1.Result
  Check 'LastRun: heartbeat DONE (exit 0)' ($r1.Code -eq 0 -and $r1.Status -eq 'DONE')
  if ($r1.Status -ne 'DONE') { Show-Diagnostics $r1 }
  Check 'cleanup_result.json: ok, LastRun, 1 run, nothing left behind, tenant restored, manifest pruned' ($res -and $res.ok -and $res.scope -eq 'LastRun' -and
    $res.runsTargeted -eq 1 -and $res.failedTotal -eq 0 -and $res.tenantRestored -and $res.manifestPruned -and $res.planSource -eq 'manifest' -and
    [System.IO.Path]::GetFullPath([string]$res.manifestSource) -eq [System.IO.Path]::GetFullPath($seedManifest))
  $x = $res.results
  Check '... every planned item removed (team, meeting + residue, file, emails, notification, both chats)' ($x -and $x.groupsDeleted -eq 1 -and
    $x.meetingsDeleted -eq 1 -and $x.meetingResidueRemoved -eq 1 -and $x.meetingEmailsRemoved -eq 1 -and $x.filesDeleted -eq 1 -and
    $x.emailsDeleted -eq 2 -and $x.notificationEmailsDeleted -eq 1 -and $x.chatsDeleted -eq 1 -and $x.meetingChatsDeleted -eq 1 -and
    $x.chatsNotFound -eq 0 -and @($x.usersWithoutToken).Count -eq 0 -and @($x.errors).Count -eq 0 -and $x.approach -like 'delegated*')
  Check "... the deleted run's ledger cleared (its own folder only)" ((@($res.localState.ledgersCleared) -join ',') -eq $seedRunId -and -not (Test-Path -LiteralPath $seedLedger) -and
    @($res.localState.notFoundLocally).Count -eq 0)
  Check '... the newer seed folder that never wrote a manifest is untouched (ledger, preview, run.json)' (Test-NeverRunUntouched)
  Check "... the seed run's manifest pruned to no runs" (@((Read-JsonFile $seedManifest).runs).Count -eq 0)
  Check 'tenant_state.json: restored' ($r1.TenantState.phase -eq 'restored' -and $r1.TenantState.restored)
  Check 'the run-mode menu was skipped (cleanup run)' ($r1.Log -match 'Cleanup run: no run mode or history needed' -and $r1.Log -notmatch 'Choose a run mode')
  Check 'steps 4-13 skipped: no content-creation request, no Invoke-WebRequest' ((Get-ContentCreation $r1).Count -eq 0 -and $r1.Http.Count -gt 0)
  Check 'the team and both chats were deleted with the ADMINISTRATOR token' ((Get-Who $r1 DELETE "$G/groups/$TeamId") -eq 'admin' -and
    (Get-Who $r1 DELETE "$G/chats/$GroupChat") -eq 'admin' -and (Get-Who $r1 DELETE "$G/chats/$MeetingChat") -eq 'admin' -and
    @($r1.Http | Where-Object { $_.method -eq 'DELETE' -and $_.uri -match '/(groups|chats)/' -and $_.who -ne 'admin' }).Count -eq 0)
  Check "each user's items were deleted through /me with THAT user's token" ((Get-Who $r1 DELETE "$G/me/events/$EventId") -eq $Billie -and
    (Get-Who $r1 DELETE "$G/me/events/ev-residue") -eq $Cora -and (Get-Who $r1 DELETE "$G/me/messages/m-cancel") -eq $Cora -and
    (Get-Who $r1 DELETE "$G/me/drive/items/item-runbook") -eq $Adile -and (Get-Who $r1 DELETE "$G/me/messages/m-sent") -eq $Adile -and
    (Get-Who $r1 DELETE "$G/me/messages/m-reply") -eq $Cora -and (Get-Who $r1 DELETE "$G/me/messages/m-share") -eq 'admin')
  $chatLists = @(Get-Http $r1 GET "$G/me/chats?`$top=50")
  Check "chats were listed per user with that user's own token (5 demo users + administrator)" ($chatLists.Count -eq 6 -and
    (@($chatLists | ForEach-Object { $_.who } | Sort-Object -Unique)).Count -eq 6 -and @($chatLists | Where-Object { $_.who -eq 'admin' }).Count -eq 1)
  Check 'nothing outside the plan was deleted; nobody addressed through /users/{upn}' (@($r1.Http | Where-Object { $_.method -eq 'DELETE' -and $_.uri -match 'ev-keep|m-keep|m-welcome|item-keep|coffee' }).Count -eq 0 -and
    @($r1.Http | Where-Object { $_.method -eq 'DELETE' -and $_.uri -match '/users/' }).Count -eq 0)
  Check 'one second between the chat deletes' (@($r1.Sleeps | Where-Object { [double]$_.seconds -eq 1 }).Count -ge 1)
  $refresh = @($r1.Tokens | Where-Object { $_.grant -eq 'refresh_token' })
  $passwords = @($r1.Tokens | Where-Object { $_.grant -eq 'password' })
  Check 'administrator: one refresh-token grant for the config app in the sign-in tenant' ($refresh.Count -eq 1 -and $refresh[0].clientId -eq $App -and $refresh[0].tenant -eq $Tenant)
  Check 'demo users signed in as a public client (password grant: no client assertion, no secret)' ($passwords.Count -ge 6 -and
    @($passwords | Where-Object { $_.clientId -ne $App -or $_.tenant -ne $Tenant -or $_.keys -ne 'client_id,grant_type,password,scope,username' }).Count -eq 0 -and
    @($r1.Tokens | Where-Object { $_.keys -match 'client_assertion|client_secret' }).Count -eq 0)
  Check 'the readiness gate and every demo user had a token' (@($passwords | ForEach-Object { $_.user } | Sort-Object -Unique).Count -eq 5)
  Check 'the sign-in was consumed (gone from disk) and out of the environment before the first request' ($r1.SignedIn -and
    -not (Test-DemoIQSessionSecret -Name 'admin-session') -and @($r1.Http + $r1.Tokens | Where-Object { $_.sessionInEnv }).Count -eq 0)
  Check 'with no OneDrive copy (404) the local manifest was used and nothing was uploaded' ((Get-Who $r1 GET $OneDriveUri) -eq 'admin' -and (Get-Http $r1 PUT $OneDriveUri).Count -eq 0)
  if ($r1.Unknown.Count) { Write-Host "    note: $($r1.Unknown.Count) unlisted request(s) answered: $((@($r1.Unknown | ForEach-Object { "$($_.method) $($_.uri)" }) | Select-Object -First 5) -join '; ')" -ForegroundColor DarkGray }
  $m = Invoke-Monitor $r1.RunFolder
  Check 'monitor_seeding.ps1: kind cleanup, done, with the cleanup result' ($m -and $m.kind -eq 'cleanup' -and $m.status -eq 'done' -and $m.cleanupResult.ok -and
    $m.cleanupResult.failedTotal -eq 0 -and $m.cleanupResult.scope -eq 'LastRun' -and $m.cleanupResult.tenantRestored)
  $h = Get-HistoryRun $r1.RunId
  Check 'run_history.ps1: kind cleanup, Succeeded' ($h -and $h.kind -eq 'cleanup' -and $h.outcome -eq 'Succeeded' -and $h.cleanupScope -eq 'LastRun' -and $h.itemsNotRemoved -eq 0)

  # ======================================================================== 2. AllRuns, partial
  Set-SeedRun
  Set-Content -LiteralPath (Join-Path $seedRun 'DemoIQ-Output/aop_runbook.html') -Value '<html>runbook</html>' -Encoding utf8
  $denied = @("DELETE $G/me/events/$EventId $Billie")
  $r2 = Invoke-CleanupRun 'all' 'AllRuns' (New-Settings -OneDriveManifest (New-SeedManifestJson -WithOneDriveOnlyRun) -BadPassword @($Daichi) -Denied $denied) -Archive
  $res = $r2.Result
  Check 'AllRuns with failures: heartbeat DONE failed=2 (exit 0)' ($r2.Code -eq 0 -and $r2.Status -eq 'DONE failed=2')
  if ($r2.Status -ne 'DONE failed=2') { Show-Diagnostics $r2 }
  Check 'cleanup_result.json: ok, AllRuns, the OneDrive and local copies merged (2 runs, one known only to OneDrive), failedTotal 2' ($res -and $res.ok -and
    $res.scope -eq 'AllRuns' -and $res.runsTargeted -eq 2 -and $res.failedTotal -eq 2 -and $res.tenantRestored -and $res.manifestPruned -and $res.manifestSource -like 'merged 2 manifests:*')
  $x = $res.results
  Check '... the denied meeting and the user without a token are reported, with the consent hint' ($x.meetingsFailed -eq 1 -and $x.meetingsDeleted -eq 0 -and
    @($x.usersWithoutToken) -contains $Users[4] -and @($x.errors | Where-Object { $_ -like "meeting 'Pump A review'*" -and $_.Contains($ConsentFix) }).Count -eq 1 -and
    $x.groupsDeleted -eq 2 -and $x.filesDeleted -eq 1 -and $x.chatsDeleted -eq 1 -and (Get-Who $r2 DELETE "$G/groups/$OneDriveOnlyTeam") -eq 'admin')
  Check '... the user whose password grant failed (AADSTS50126) was asked once and never used' (@($r2.Tokens | Where-Object { $_.user -eq $Daichi }).Count -eq 1 -and
    @($r2.Http | Where-Object { $_.who -eq $Daichi }).Count -eq 0)
  $put = @(Get-Http $r2 PUT $OneDriveUri)
  $putRuns = if ($put.Count) { @(($put[0].body | ConvertFrom-Json).runs).Count } else { -1 }
  Check "the administrator's OneDrive manifest was read and pruned (PUT, administrator token, no runs left)" ((Get-Who $r2 GET $OneDriveUri) -eq 'admin' -and
    $put.Count -eq 1 -and $put[0].who -eq 'admin' -and $putRuns -eq 0)
  Check "... the seed run's ledger cleared; the run known only to OneDrive reported in notFoundLocally" ((@($res.localState.ledgersCleared) -join ',') -eq $seedRunId -and
    (@($res.localState.notFoundLocally) -join ',') -eq $OneDriveOnlyRun -and $r2.Log -match "No seed run folder on this machine holds run $OneDriveOnlyRun")
  # -ArchiveLocalDeliverables (through run_seeder): pruned and ledger-free BEFORE the backup,
  # then removed; run.json stays so run history keeps working.
  $zip = Join-Path $ws "archive/${seedRunId}__DemoIQ-Output.zip"
  $unzipped = Join-Path $work 'unzipped'
  if (Test-Path -LiteralPath $zip) { Expand-Archive -LiteralPath $zip -DestinationPath $unzipped -Force }
  Check "-ArchiveLocalDeliverables: the seed run's deliverables archived to <ws>/archive, then removed" (@($res.localState.localArchives).Count -eq 1 -and
    $res.localState.localArchives[0].run -eq $seedRunId -and (Test-Path -LiteralPath $zip) -and -not (Test-Path -LiteralPath (Join-Path $seedRun 'DemoIQ-Output')) -and
    (Test-Path -LiteralPath (Join-Path $seedRun 'run.json')))
  Check '... the archived manifest is already pruned and the ledger already cleared' ((Test-Path (Join-Path $unzipped 'aop_runbook.html')) -and
    @((Read-JsonFile (Join-Path $unzipped 'cleanup.config')).runs).Count -eq 0 -and -not (Test-Path (Join-Path $unzipped 'seed_ledger.json')))
  Check '... and the newer seed folder that never wrote a manifest is neither cleared nor archived' (Test-NeverRunUntouched)
  Check 'tenant_state.json: restored; steps 4-13 skipped; the sign-in consumed' ($r2.TenantState.phase -eq 'restored' -and (Get-ContentCreation $r2).Count -eq 0 -and
    -not (Test-DemoIQSessionSecret -Name 'admin-session') -and @($r2.Http + $r2.Tokens | Where-Object { $_.sessionInEnv }).Count -eq 0)
  $m = Invoke-Monitor $r2.RunFolder
  Check 'monitor_seeding.ps1: kind cleanup, partial, failedTotal 2' ($m -and $m.kind -eq 'cleanup' -and $m.status -eq 'partial' -and $m.cleanupResult.failedTotal -eq 2)
  $h = Get-HistoryRun $r2.RunId
  Check 'run_history.ps1: kind cleanup, Partial' ($h -and $h.kind -eq 'cleanup' -and $h.outcome -eq 'Partial' -and $h.itemsNotRemoved -eq 2)

  # ======================================================================== 3. nothing to clean up
  Set-SeedRun -WithoutManifest
  $r3 = Invoke-CleanupRun 'none' 'LastRun' (New-Settings)
  $res = $r3.Result
  Check 'no manifest and no logs: heartbeat FAILED cleanup-failed (exit 1)' ($r3.Code -eq 1 -and $r3.Status -like 'FAILED cleanup-failed: *No seed manifest*')
  if ($r3.Status -notlike 'FAILED cleanup-failed: *') { Show-Diagnostics $r3 }
  Check 'cleanup_result.json: not ok, with the reason; nothing deleted, nothing created' ($res -and -not $res.ok -and $res.error -match 'No seed manifest' -and
    @($r3.Http | Where-Object { $_.method -eq 'DELETE' }).Count -eq 0 -and (Get-ContentCreation $r3).Count -eq 0 -and $r3.Http.Count -gt 0)
  Check 'a failed cleanup still restores the tenant (tenant_state.json restored)' ($r3.TenantState.phase -eq 'restored' -and $r3.TenantState.restored -and $res.tenantRestored)
  Check '... touches no local run folder' ((Test-NeverRunUntouched) -and (Test-Path -LiteralPath $seedLedger) -and $null -eq $res.localState)
  Check '... and consumed the sign-in' (-not (Test-DemoIQSessionSecret -Name 'admin-session') -and @($r3.Http + $r3.Tokens | Where-Object { $_.sessionInEnv }).Count -eq 0)
  $m = Invoke-Monitor $r3.RunFolder
  Check 'monitor_seeding.ps1: kind cleanup, failed' ($m -and $m.kind -eq 'cleanup' -and $m.status -eq 'failed' -and $m.cleanupResult -and -not $m.cleanupResult.ok)
  $h = Get-HistoryRun $r3.RunId
  Check 'run_history.ps1: kind cleanup, Failed' ($h -and $h.kind -eq 'cleanup' -and $h.outcome -eq 'Failed')
}
finally {
  Clear-DemoIQSessionSecret -Name 'admin-session'
  Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

if ($script:fails) { Write-Host "test_cleanup_run: $($script:fails) FAILURE(S)" -ForegroundColor Red; exit 1 }
Write-Host 'test_cleanup_run: all passed' -ForegroundColor Green
exit 0

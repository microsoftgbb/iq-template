# test_cleanup_engine.ps1 - the delegated tenant cleanup engine (scripts/cleanup_engine.ps1)
# and the read-only preview CLI (scripts/cleanup_tenant.ps1), offline on this OS.
#
# Invoke-RestMethod is shadowed by a stub that plays Microsoft Graph over a fake demo tenant
# and the Entra token endpoint, and records every request with the token it carried;
# Start-Sleep is instant and recorded. Nothing leaves the machine. Proves:
#   * the engine can be inlined into the seeder: it parses clean, its top level holds only
#     function definitions, every function name contains DemoIQCleanup, and it carries none
#     of the constructs the seeder's post-patch check rejects
#   * plans for LastRun and AllRuns from one manifest, from two run folders (merged and
#     de-duplicated by run) and from the run logs
#   * routing: the team and chat deletes carry the administrator's token; each user's
#     meetings, mail, drive and chat list are reached through /me with that user's own token
#   * 404 counts as already deleted; HTTP 429 is retried after Retry-After
#   * a user without a token is reported once and none of their resources is requested
#   * an unmatched planned chat is reported in chatsNotFound; permission failures carry hints
#   * manifest pruning by run identity (local files rewritten, the OneDrive copy PUT as admin)
#   * local state: seed ledgers cleared, cleanup run folders skipped, deliverables archived
#   * demo-user sign-in: a public-client password grant, MFA/CA propagation retried, other
#     AADSTS errors final at once, the password never printed
#   * the preview CLI: no sign-in, local manifests only, -Execute and encrypted configs refused
$ErrorActionPreference = 'Stop'

# Self-isolate: a throwaway HOME and session folder, so the real workspace and any real
# sign-in are never read, overwritten or deleted.
if ($env:DEMOIQ_TEST_ISOLATED -ne '1') {
  $iso = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-purge-iso-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
  New-Item -ItemType Directory -Force (Join-Path $iso 'home') | Out-Null
  $saved = @{ HOME = $env:HOME; DEMOIQ_SESSION_DIR = $env:DEMOIQ_SESSION_DIR; DEMOIQ_TEST_ISOLATED = $env:DEMOIQ_TEST_ISOLATED; DEMOIQ_TEST_ISO_ROOT = $env:DEMOIQ_TEST_ISO_ROOT }
  $env:HOME = Join-Path $iso 'home'; $env:DEMOIQ_SESSION_DIR = Join-Path $iso 'session'; $env:DEMOIQ_TEST_ISOLATED = '1'
  $env:DEMOIQ_TEST_ISO_ROOT = $iso
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
$enginePath = Join-Path $S 'cleanup_engine.ps1'
$cliPath = Join-Path $S 'cleanup_tenant.ps1'
$pwshExe = (Get-Process -Id $PID).Path
$work = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-purge-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null

$GraphRoot = 'https://graph.microsoft.com/v1.0'
$OneDriveUri = 'https://graph.microsoft.com/v1.0/me/drive/root:/DemoIQ-Output/cleanup.config:/content'
$FxTenant = 'dddddddd-1111-2222-3333-444444444444'
$FxApp = '00000000-0000-0000-0000-0000000000dd'
$FxAdmin = 'admin@fixturetest.onmicrosoft.com'
$FxAnn = 'ann@fixturetest.onmicrosoft.com'
$FxBob = 'bob@fixturetest.onmicrosoft.com'
$FxCara = 'cara@fixturetest.onmicrosoft.com'
$FxPassword = 'Fixture-Demo-Pass-42!'
$Approach = 'delegated (administrator sign-in + demo-user tokens while MFA/CA is relaxed)'
$ConsentFix = 'deploy.py up --with-demoiq --through demoiq --grant-demoiq-consent'
function Tok([string]$Upn) { if ($Upn -eq $FxAdmin) { 'tok-admin' } else { 'tok-' + $Upn.ToLowerInvariant() } }

# ======================================================================== static checks
$engineText = [System.IO.File]::ReadAllText($enginePath)
$forbidden = @('client_assertion', 'client_credentials', 'Connect-MgGraph', 'Install-Module', 'Import-Module Microsoft.Graph',
  'Get-ChildItem Cert:', '$Global:Cert', "Read-Host 'Config password'", 'username=$AdminUPN', 'oauth2PermissionGrants',
  '$Global:AdminToken', '$Global:AccessToken', '$DToken', 'DEMOIQ_TELEMETRY_LOGGER', 'New-ClientAssertionJwt')
$hits = @($forbidden | Where-Object { $engineText.IndexOf($_, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 })
Check "engine: none of the seeder's forbidden literals$(if ($hits) { ' - found ' + ($hits -join ', ') })" ($hits.Count -eq 0)
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
  # The seeder's own post-patch check (platform_patch.js FORBIDDEN) over the engine text.
  $js = "const p=require(process.argv[1]);const t=require('fs').readFileSync(process.argv[2],'utf8');process.stdout.write(JSON.stringify(p.findForbidden(t)));"
  $found = & $node.Source -e $js (Join-Path $S 'platform_patch.js') $enginePath
  Check "engine passes platform_patch.js findForbidden ($found)" ($LASTEXITCODE -eq 0 -and $found -eq '[]')
} else { Write-Host '  SKIP platform_patch.js findForbidden (node not found)' -ForegroundColor DarkYellow }
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($enginePath, [ref]$tokens, [ref]$parseErrors)
Check 'engine parses with zero errors' (@($parseErrors).Count -eq 0)
$top = @($ast.EndBlock.Statements)
Check 'engine top level holds only function definitions' ($top.Count -gt 0 -and
  @($top | Where-Object { $_ -isnot [System.Management.Automation.Language.FunctionDefinitionAst] }).Count -eq 0)
Check 'engine has no param, begin or process block' ($null -eq $ast.ParamBlock -and $null -eq $ast.BeginBlock -and $null -eq $ast.ProcessBlock)
$fns = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true))
$badNames = @($fns | Where-Object { $_.Name -notmatch 'DemoIQCleanup' } | ForEach-Object { $_.Name })
Check "every engine function name contains DemoIQCleanup ($($fns.Count) functions)$(if ($badNames) { ' - ' + ($badNames -join ', ') })" ($fns.Count -gt 0 -and $badNames.Count -eq 0)
$exits = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.ExitStatementAst] }, $true))
$dots = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.InvocationOperator -eq 'Dot' }, $true))
$globals = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.VariableExpressionAst] -and ($n.VariablePath.IsGlobal -or $n.VariablePath.IsScript) }, $true))
$eap = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -match 'ErrorActionPreference' }, $true))
Check 'engine: no exit, dot-sourcing, global/script variables or $ErrorActionPreference change' ($exits.Count -eq 0 -and $dots.Count -eq 0 -and $globals.Count -eq 0 -and $eap.Count -eq 0)
Check 'engine: HTTP only through Invoke-RestMethod; no $PSScriptRoot' ($engineText -notmatch 'Invoke-WebRequest|HttpClient|WebClient|Invoke-MgGraphRequest|\$PSScriptRoot')
$cliText = [System.IO.File]::ReadAllText($cliPath)
Check 'CLI: comment-based help, dot-sources platform.ps1 + the engine, no pw_shared/password/certificate code' (
  $cliText -match '\.SYNOPSIS' -and $cliText -match "'platform\.ps1'" -and $cliText -match "'cleanup_engine\.ps1'" -and
  $cliText -notmatch 'pw_shared|ConfigPassword|DEMOIQ_CFG_PW|client_assertion|client_credentials|Thumbprint|AesGcm')
Check 'CLI help describes the cleanup run' ((Get-Help $cliPath).Synopsis -match 'Read-only dry-run')

# An inlined copy (not dot-sourced) works at the top level of a script with Stop errors.
$inline = Join-Path $work 'inline-probe.ps1'
Set-Content -LiteralPath $inline -Encoding utf8 -Value ("`$ErrorActionPreference = 'Stop'`nSet-StrictMode -Off`n" + $engineText + @'

$m = [pscustomobject]@{ runs = @([pscustomobject]@{ timestamp = '2026-10-19 10:00:00'; team = [pscustomobject]@{ groupId = 'g1' }; users = @('a@x.test') }) }
(New-DemoIQCleanupPlan -Manifest $m -Scope AllRuns -AdminUpn 'Admin@x.test').DemoUsers -join ','
'@)
$inlineOut = & $pwshExe -NoProfile -File $inline 2>&1
Check 'an inlined copy runs at a script top level with Stop errors' ($LASTEXITCODE -eq 0 -and ($inlineOut -join '') -eq 'a@x.test,admin@x.test')

. $enginePath

# ======================================================================== the Graph stub
# State lives in ONE shared object, mutated in place: inside the stub, $script: would
# resolve to whichever script is running on the call stack (the CLI, when it runs in-process).
$Stub = @{
  Calls      = [System.Collections.Generic.List[object]]::new()
  Sleeps     = [System.Collections.Generic.List[object]]::new()
  TokenAsks  = [System.Collections.Generic.List[string]]::new()
  NoToken    = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
  Faults     = @{}
  TokenQueue = [System.Collections.Generic.Queue[object]]::new()
  Unexpected = [System.Collections.Generic.List[string]]::new()
  Graph      = @{
    Groups = [System.Collections.Generic.HashSet[string]]::new()
    Events = @{}; Messages = @{}; Drives = @{}
    Chats = [System.Collections.Generic.Dictionary[string, object]]::new()
    OneDriveManifest = $null
  }
}

function New-MockHttpError {
  # Shaped like an Invoke-RestMethod failure: Exception.Response.StatusCode.value__,
  # Exception.Response.Headers.RetryAfter.Delta and the response body in ErrorDetails.
  param([int]$Status, [string]$Json, [int]$RetryAfter = 0, [string]$Message)
  if (-not $Message) { $Message = "Response status code does not indicate success: $Status." }
  $exc = [System.Exception]::new($Message)
  if ($Status) {
    $retry = if ($RetryAfter) { [pscustomobject]@{ Delta = [TimeSpan]::FromSeconds($RetryAfter); Date = $null } } else { $null }
    $resp = [pscustomobject]@{ StatusCode = [pscustomobject]@{ value__ = $Status }; Headers = [pscustomobject]@{ RetryAfter = $retry } }
    $exc | Add-Member -NotePropertyName Response -NotePropertyValue $resp -Force
  }
  $er = [System.Management.Automation.ErrorRecord]::new($exc, 'MockHttp', [System.Management.Automation.ErrorCategory]::NotSpecified, $null)
  if ($Json) { $er.ErrorDetails = [System.Management.Automation.ErrorDetails]::new($Json) }
  return $er
}
function New-NotFound { New-MockHttpError -Status 404 -Json '{"error":{"code":"ItemNotFound","message":"The resource could not be found."}}' }
function New-Denied { New-MockHttpError -Status 403 -Json '{"error":{"code":"Authorization_RequestDenied","message":"Insufficient privileges to complete the operation."}}' }
function New-AadError([string]$Code, [int]$Status = 400) {
  New-MockHttpError -Status $Status -Json (@{ error = 'invalid_grant'; error_description = "AADSTS${Code}: fixture sign-in failure.`r`nTrace ID: 0`r`nCorrelation ID: 0"; error_codes = @([int]$Code) } | ConvertTo-Json -Compress)
}
function Add-Fault([string]$Key, [object[]]$Errors) {
  if (-not $Stub.Faults.ContainsKey($Key)) { $Stub.Faults[$Key] = [System.Collections.Generic.Queue[object]]::new() }
  foreach ($e in $Errors) { $Stub.Faults[$Key].Enqueue($e) }
}

function Get-StubUser([string]$Token) {
  if ($Token -eq 'tok-admin') { return $FxAdmin }
  if ($Token -like 'tok-*@*') { return $Token.Substring(4) }
  return $null
}
function Remove-StubItem($Lists, [string]$Id) {
  foreach ($list in @($Lists)) {
    if ($null -eq $list) { continue }
    for ($i = 0; $i -lt $list.Count; $i++) { if ($list[$i].id -eq $Id) { $list.RemoveAt($i); return $true } }
  }
  return $false
}

function Invoke-RestMethod {
  [CmdletBinding()]
  param([string]$Method = 'GET', [string]$Uri, $Headers, $Body, [string]$ContentType, [int]$TimeoutSec)
  $verb = $Method.ToUpperInvariant()
  $token = $null
  if ($Headers -and $Headers['Authorization']) { $token = ([string]$Headers['Authorization']) -replace '^Bearer ', '' }
  $Stub.Calls.Add([pscustomobject]@{ Method = $verb; Uri = $Uri; Token = $token; Body = $Body; ContentType = $ContentType })
  foreach ($fk in @("$verb $Uri $token", "$verb $Uri")) {
    if ($Stub.Faults.ContainsKey($fk) -and $Stub.Faults[$fk].Count -gt 0) { throw $Stub.Faults[$fk].Dequeue() }
  }
  if ($Uri -like 'https://login.microsoftonline.com/*') {
    if ($Stub.TokenQueue.Count -eq 0) { throw 'token queue exhausted' }
    $next = $Stub.TokenQueue.Dequeue()
    if ($next -is [System.Management.Automation.ErrorRecord]) { throw $next }
    return $next
  }
  if (-not $Uri.StartsWith($GraphRoot + '/')) { $Stub.Unexpected.Add("$verb $Uri"); throw "unexpected request: $verb $Uri" }
  $me = Get-StubUser $token
  if (-not $me) { throw (New-MockHttpError -Status 401 -Json '{"error":{"code":"InvalidAuthenticationToken","message":"Access token is empty."}}') }
  $path = $Uri.Substring($GraphRoot.Length); $query = ''
  $qi = $path.IndexOf('?'); if ($qi -ge 0) { $query = $path.Substring($qi + 1); $path = $path.Substring(0, $qi) }
  $g = $Stub.Graph
  if ($verb -eq 'DELETE' -and $path -match '^/groups/([^/]+)$') {
    if ($me -ne $FxAdmin) { throw (New-Denied) }
    if ($g.Groups.Remove($Matches[1])) { return $null }
    throw (New-NotFound)
  }
  if ($verb -eq 'DELETE' -and $path -match '^/chats/(.+)$') {
    if ($me -ne $FxAdmin) { throw (New-Denied) }
    if ($g.Chats.Remove($Matches[1])) { return $null }
    throw (New-NotFound)
  }
  if ($path -eq '/me/drive/root:/DemoIQ-Output/cleanup.config:/content') {
    if ($me -ne $FxAdmin) { throw (New-NotFound) }
    if ($verb -eq 'GET') { if ($g.OneDriveManifest) { return [string]$g.OneDriveManifest }; throw (New-NotFound) }
    if ($verb -eq 'PUT') { $g.OneDriveManifest = [string]$Body; return [pscustomobject]@{ id = 'od-manifest'; name = 'cleanup.config' } }
  }
  if ($verb -eq 'GET' -and $path -eq '/me/events') { return [pscustomobject]@{ value = @($g.Events[$me]) } }
  if ($verb -eq 'DELETE' -and $path -match '^/me/events/(.+)$') { if (Remove-StubItem @(, $g.Events[$me]) $Matches[1]) { return $null }; throw (New-NotFound) }
  if ($verb -eq 'GET' -and $path -eq '/me/messages') {
    $all = @($g.Messages[$me] | Where-Object { $_ })
    if ($query -match '\$search="subject:(.*?)"') {
      $term = [uri]::UnescapeDataString($Matches[1])
      $all = @($all | Where-Object { $_.subject.IndexOf($term, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 })
    }
    return [pscustomobject]@{ value = $all }
  }
  if ($verb -eq 'DELETE' -and $path -match '^/me/messages/(.+)$') { if (Remove-StubItem @(, $g.Messages[$me]) $Matches[1]) { return $null }; throw (New-NotFound) }
  if ($verb -eq 'GET' -and $path -eq '/me/drive/root/children') {
    $d = $g.Drives[$me]
    if (-not $d) { throw (New-NotFound) }
    if ($query -match 'skiptoken=p2') { return [pscustomobject]@{ value = @($d.Page2) } }
    $resp = [pscustomobject]@{ value = @($d.Root) }
    if ($d.Page2.Count) { $resp | Add-Member -NotePropertyName '@odata.nextLink' -NotePropertyValue "$GraphRoot/me/drive/root/children?`$top=200&`$skiptoken=p2" }
    return $resp
  }
  if ($verb -eq 'GET' -and $path -match '^/me/drive/items/([^/]+)/children$') {
    $d = $g.Drives[$me]
    if (-not $d -or -not $d.Children.ContainsKey($Matches[1])) { throw (New-NotFound) }
    return [pscustomobject]@{ value = @($d.Children[$Matches[1]]) }
  }
  if ($verb -eq 'DELETE' -and $path -match '^/me/drive/items/([^/]+)$') {
    $d = $g.Drives[$me]
    if ($d -and (Remove-StubItem (@($d.Root, $d.Page2) + @($d.Children.Values)) $Matches[1])) { return $null }
    throw (New-NotFound)
  }
  if ($verb -eq 'GET' -and $path -eq '/me/chats') {
    return [pscustomobject]@{ value = @($g.Chats.Values | Where-Object { $_.members -contains $me } |
        ForEach-Object { [pscustomobject]@{ id = $_.id; topic = $_.topic; chatType = $_.chatType } }) }
  }
  $Stub.Unexpected.Add("$verb $Uri")
  throw "unexpected request: $verb $Uri"
}
function Start-Sleep { [CmdletBinding()] param([double]$Seconds, [int]$Milliseconds) $Stub.Sleeps.Add([int]$Seconds) }

$GetToken = {
  param($who)
  $Stub.TokenAsks.Add([string]$who)
  if ($Stub.NoToken.Contains([string]$who)) { return $null }
  Tok ([string]$who)
}
$AdminTokenBlock = { 'tok-admin' }

function New-List([object[]]$Items) { $l = [System.Collections.Generic.List[object]]::new(); foreach ($i in $Items) { $l.Add([pscustomobject]$i) }; return , $l }
function Reset-Stub {
  $Stub.Calls.Clear(); $Stub.Sleeps.Clear(); $Stub.TokenAsks.Clear(); $Stub.NoToken.Clear(); $Stub.Faults.Clear()
  $Stub.TokenQueue.Clear(); $Stub.Unexpected.Clear()
}
function Reset-FakeTenant {
  # The demo tenant after run 2 (LastRun) was seeded, plus content cleanup must not touch.
  Reset-Stub
  $g = $Stub.Graph
  $g.Groups.Clear(); foreach ($id in 'g-new', 'g-other') { [void]$g.Groups.Add($id) }
  $g.Events.Clear(); $g.Messages.Clear(); $g.Drives.Clear(); $g.Chats.Clear(); $g.OneDriveManifest = $null
  $g.Events[$FxAnn] = New-List @(@{ id = 'ev-a'; subject = 'Pump A review' }, @{ id = 'ev-ann-lunch'; subject = 'Lunch' })
  $g.Events[$FxBob] = New-List @(@{ id = 'ev-bob-res'; subject = 'Canceled: Pump A review' }, @{ id = 'ev-bob-sync'; subject = 'Team sync' })
  $g.Events[$FxCara] = New-List @()
  $g.Events[$FxAdmin] = New-List @(@{ id = 'ev-adm-res'; subject = 'Shift handover' })
  $g.Messages[$FxAnn] = New-List @(@{ id = 'm-ann-1'; subject = 'Pump A vibration' }, @{ id = 'm-ann-2'; subject = 'RE: Pump A vibration' }, @{ id = 'm-ann-3'; subject = 'Quarterly numbers' })
  $g.Messages[$FxBob] = New-List @(@{ id = 'm-bob-1'; subject = 'Pump A vibration' }, @{ id = 'm-bob-cxl'; subject = 'Canceled: Pump A review' }, @{ id = 'm-bob-lunch'; subject = 'Weekly lunch' })
  $g.Messages[$FxCara] = New-List @(@{ id = 'm-cara-1'; subject = 'South Works shift report' })
  $g.Messages[$FxAdmin] = New-List @(@{ id = 'm-adm-share'; subject = "Ann shared 'North_Works_Runbook' with you" }, @{ id = 'm-adm-welcome'; subject = 'Welcome' })
  $g.Drives[$FxAnn] = @{
    Root = New-List @(@{ id = 'fold-1'; name = 'DemoIQ'; folder = @{ childCount = 1 } })
    Page2 = New-List @(@{ id = 'f-unrel'; name = 'Unrelated.docx'; folder = $null })
    Children = @{ 'fold-1' = (New-List @(@{ id = 'f-run'; name = 'North_Works_Runbook_20261019_1000.docx'; folder = $null })) }
  }
  $g.Drives[$FxCara] = @{ Root = (New-List @(@{ id = 'f-costs'; name = 'Pump_B_Costs_20261019_1000.xlsx'; folder = $null })); Page2 = (New-List @()); Children = @{} }
  $g.Chats['19:new@thread.v2'] = [pscustomobject]@{ id = '19:new@thread.v2'; topic = 'Pump A war room'; chatType = 'group'; members = @($FxAnn, $FxBob, $FxAdmin) }
  $g.Chats['19:mtg-a@thread.v2'] = [pscustomobject]@{ id = '19:mtg-a@thread.v2'; topic = 'Pump A review'; chatType = 'meeting'; members = @($FxAnn, $FxBob) }
  $g.Chats['19:coffee@thread.v2'] = [pscustomobject]@{ id = '19:coffee@thread.v2'; topic = 'Coffee'; chatType = 'group'; members = @($FxBob, $FxCara) }
}
function Get-Calls([string]$Method, [string]$Uri) { @($Stub.Calls | Where-Object { $_.Method -eq $Method -and $_.Uri -eq $Uri }) }
function Count-Calls([string]$Method, [string]$Uri) { @(Get-Calls $Method $Uri).Count }
function First-Token([string]$Method, [string]$Uri) { $c = @(Get-Calls $Method $Uri); if ($c.Count) { $c[0].Token } else { '<no request>' } }
function Test-SamePath([string]$A, [string]$B) { $A -and $B -and ([System.IO.Path]::GetFullPath($A) -eq [System.IO.Path]::GetFullPath($B)) }

# ======================================================================== fixtures
function New-Run1 {
  [pscustomobject]@{
    timestamp = '2026-10-18 09:00:00'; mode = 'Fast'; tenantId = $FxTenant; appId = $FxApp; adminUpn = $FxAdmin
    caExclusionGroup = [pscustomobject]@{ id = 'ca-group' }
    team = [pscustomobject]@{ groupId = 'g-old'; channels = @([pscustomobject]@{ name = 'General'; id = '19:gen-old@thread.tacv2' }) }
    chats = @([pscustomobject]@{ topic = 'Kickoff crew'; id = '19:old@thread.v2'; messages = @() })
    emails = @([pscustomobject]@{ subject = 'Kickoff agenda'; messages = @() })
    files = @([pscustomobject]@{ type = 'Word'; name = 'Kickoff_Notes_20261018_0900.docx'; url = 'https://example.invalid/1'; owner = $FxAnn })
    meetings = @([pscustomobject]@{ subject = 'Kickoff North Works'; eventId = 'ev-old'; organizer = $FxAnn })
    users = @('ANN@fixturetest.onmicrosoft.com', $FxBob)
  }
}
function New-Run2 {
  [pscustomobject]@{
    timestamp = '2026-10-19 10:00:00'; mode = 'Fast'; tenantId = $FxTenant; appId = $FxApp; adminUpn = $FxAdmin
    caExclusionGroup = [pscustomobject]@{ id = 'ca-group' }
    team = [pscustomobject]@{ groupId = 'g-new'; channels = @([pscustomobject]@{ name = 'Pump A'; id = '19:c1@thread.tacv2' }, [pscustomobject]@{ name = 'Pump B'; id = '19:c2@thread.tacv2' }) }
    chats = @([pscustomobject]@{ topic = 'Pump A war room'; id = '19:new@thread.v2' }, [pscustomobject]@{ topic = 'Ghost chat'; id = '' })
    emails = @([pscustomobject]@{ subject = 'Pump A vibration' }, [pscustomobject]@{ subject = 'RE: Pump A vibration' }, [pscustomobject]@{ subject = 'South Works shift report' })
    files = @([pscustomobject]@{ type = 'Word'; name = 'North_Works_Runbook_20261019_1000.docx'; owner = $FxAnn },
      [pscustomobject]@{ type = 'Excel'; name = 'Pump_B_Costs_20261019_1000.xlsx'; owner = $FxCara })
    meetings = @([pscustomobject]@{ subject = 'Pump A review'; eventId = 'ev-a'; organizer = $FxAnn },
      [pscustomobject]@{ subject = 'Shift handover'; eventId = 'ev-c'; organizer = $FxCara })
    users = @($FxAnn, $FxBob, $FxCara)
  }
}
function New-Manifest([object[]]$Runs) {
  $toc = @(); $i = 1; foreach ($r in $Runs) { $toc += [pscustomobject]@{ run = $i; timestamp = $r.timestamp; mode = $r.mode; admin = $r.adminUpn }; $i++ }
  [pscustomobject]@{ tableOfContents = $toc; tenantConfig = [pscustomobject]@{ tenantId = $FxTenant; adminUpn = $FxAdmin; teamGroupId = 'g-new' }; runs = @($Runs) }
}
function Write-Manifest([string]$Path, [object[]]$Runs, $WrittenAt) {
  New-Item -ItemType Directory -Force (Split-Path -Parent $Path) | Out-Null
  (New-Manifest $Runs | ConvertTo-Json -Depth 12) | Set-Content -LiteralPath $Path -Encoding utf8
  if ($WrittenAt) { (Get-Item -LiteralPath $Path).LastWriteTime = [datetime]$WrittenAt }
  return (Get-Item -LiteralPath $Path).FullName
}
function Read-Json([string]$Path) { Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json }
function Split-Output([object[]]$All) {
  # Separate a function's result from its Write-Host lines (captured with 6>&1).
  [pscustomobject]@{
    Result = @($All | Where-Object { $_ -isnot [System.Management.Automation.InformationRecord] }) | Select-Object -Last 1
    Host   = (@($All | Where-Object { $_ -is [System.Management.Automation.InformationRecord] } | ForEach-Object { [string]$_.MessageData }) -join "`n")
  }
}

try {
  # ====================================================================== plans
  $both = Write-Manifest (Join-Path $work 'single/cleanup.config') @((New-Run1), (New-Run2))
  $mi = Read-DemoIQCleanupManifest -CleanupConfigPath $both -Scope LastRun 6>$null
  Check 'explicit manifest: plan source, local path, no OneDrive' ($mi.PlanSource -eq 'manifest' -and (Test-SamePath $mi.Source $both) -and (Test-SamePath $mi.LocalPath $both) -and
    @($mi.LocalPaths).Count -eq 1 -and -not $mi.FromOneDrive -and @($mi.Errors).Count -eq 0)
  $pl = New-DemoIQCleanupPlan -Manifest $mi.Manifest -Scope LastRun -AdminUpn 'Admin@FixtureTest.onmicrosoft.com'
  $c = $pl.Counts
  Check 'LastRun plan: the last run only (1 team, 2 meetings, 2 files, 2 email subjects, 2 chats)' ($c.groups -eq 1 -and $c.meetings -eq 2 -and
    $c.files -eq 2 -and $c.emailSubjects -eq 2 -and $c.chats -eq 2 -and @($pl.TargetRuns).Count -eq 1 -and @($pl.AllRuns).Count -eq 2)
  Check 'LastRun byRun row (manifest timestamp as written, RE: threads counted per entry)' (@($pl.ByRun).Count -eq 1 -and $pl.ByRun[0].when -eq '2026-10-19 10:00:00' -and $pl.ByRun[0].emails -eq 3)
  Check 'a planned chat without a recorded id is kept (by topic)' (@($pl.Plan.chats | Where-Object { $_.topic -eq 'Ghost chat' }).Count -eq 1)
  Check 'demo users: every run''s users, case-insensitive, plus the administrator lower-cased once' (@($pl.DemoUsers).Count -eq 4 -and
    @($pl.DemoUsers | Where-Object { $_ -eq $FxAdmin }).Count -eq 1 -and ($pl.DemoUsers -ccontains $FxAdmin))
  $pa = New-DemoIQCleanupPlan -Manifest $mi.Manifest -Scope AllRuns -AdminUpn $FxAdmin
  Check 'AllRuns plan: every run (2 teams, 3 meetings, 3 files, 3 subjects, 3 chats, 2 rows)' ($pa.Counts.groups -eq 2 -and $pa.Counts.meetings -eq 3 -and
    $pa.Counts.files -eq 3 -and $pa.Counts.emailSubjects -eq 3 -and $pa.Counts.chats -eq 3 -and @($pa.ByRun).Count -eq 2 -and @($pa.TargetRuns).Count -eq 2)
  $pk = New-DemoIQCleanupPlan -Manifest ([pscustomobject]@{ runs = @([pscustomobject]@{ timestamp = 't'; team = [pscustomobject]@{ groupId = 'g' } }) }) -Scope LastRun -AdminUpn $FxAdmin -KnownUsers @($FxBob, 'BOB@fixturetest.onmicrosoft.com')
  Check 'demo users fall back to the known users (plus the administrator)' (@($pk.DemoUsers).Count -eq 2 -and $pk.DemoUsers[0] -eq $FxBob -and $pk.DemoUsers[1] -eq $FxAdmin)

  # ====================================================================== run logs
  $logs = Join-Path $work 'logs'
  New-Item -ItemType Directory -Force $logs | Out-Null
  @(
    '**********************', 'PowerShell transcript start', '**********************',
    "[CHANNEL] Looking for existing team 'North Works Ops'...",
    " [CHANNEL] Team 'North Works Ops' not found. Creating new...",
    " [CHANNEL] Group 'North Works Ops' created (aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee). Adding members...",
    " [CHANNEL] OK: Channel 'Pump A' created (19:c1@thread.tacv2)",
    " [CHANNEL] OK: Channel 'Pump B' created (19:c2@thread.tacv2)",
    " [MEETING] OK: 'Pump A review' by $FxAnn (AAMkADevent1=)",
    ' [WORD] OK: Uploaded North_Works_Runbook_20261017_0800.docx (2048 bytes)',
    " [EXCEL] OK: Uploaded 'Pump_B_Costs_20261017_0800.xlsx' (id: 01XYZ)",
    " [EMAIL] OK (new): $FxBob -> 'Pump A vibration'",
    " [EMAIL] OK (reply): $FxAnn -> 'RE: Pump A vibration'",
    "[TEAMS] Creating group chat: 'Pump A war room'...",
    ' [TEAMS] OK: Group chat created (19:logchat@thread.v2)'
  ) | Set-Content -LiteralPath (Join-Path $logs 'DemoIQ_20261017_080000.log') -Encoding utf8
  @('[ENV] PowerShell 7.6 on host ConsoleHost', '[CONFIG] Nothing seeded in this run') | Set-Content -LiteralPath (Join-Path $logs 'DemoIQ_20261018_080000.log') -Encoding utf8
  $lr = @(Get-DemoIQCleanupRunsFromLogs -LogsFolder $logs)
  Check 'logs: one run rebuilt; a log that seeded nothing is skipped' ($lr.Count -eq 1 -and $lr[0].timestamp -eq 'DemoIQ_20261017_080000.log')
  Check 'logs: team id + name, 2 channels, the meeting and its organizer' ($lr[0].team.groupId -eq 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee' -and
    $lr[0].team.displayName -eq 'North Works Ops' -and @($lr[0].team.channels).Count -eq 2 -and @($lr[0].meetings).Count -eq 1 -and
    $lr[0].meetings[0].organizer -eq $FxAnn -and $lr[0].meetings[0].eventId -eq 'AAMkADevent1=')
  Check 'logs: files (owner *), emails, the chat with its topic, the users' (@($lr[0].files).Count -eq 2 -and @($lr[0].files | Where-Object { $_.owner -ne '*' }).Count -eq 0 -and
    @($lr[0].emails).Count -eq 2 -and $lr[0].chats[0].id -eq '19:logchat@thread.v2' -and $lr[0].chats[0].topic -eq 'Pump A war room' -and @($lr[0].users).Count -eq 2)
  $empty = Join-Path $work 'no-manifest'; New-Item -ItemType Directory -Force $empty | Out-Null
  $ml = Read-DemoIQCleanupManifest -SearchFolders @($empty) -LogsFolder @($logs) -Scope AllRuns 6>$null
  Check 'no manifest: the plan falls back to the logs' ($ml.PlanSource -eq 'logs' -and $ml.Source -like 'logs (*' -and @($ml.LocalPaths).Count -eq 0 -and -not $ml.FromOneDrive)
  $plg = New-DemoIQCleanupPlan -Manifest $ml.Manifest -Scope AllRuns -AdminUpn $FxAdmin
  Check 'log plan: counts, the team name and file base names for notification mail' ($plg.Counts.groups -eq 1 -and $plg.Counts.files -eq 2 -and
    $plg.Counts.emailSubjects -eq 1 -and $plg.Counts.chats -eq 1 -and @($plg.Plan.groupNames) -contains 'North Works Ops' -and
    @($plg.Plan.fileBaseNames) -contains 'North_Works_Runbook' -and $plg.ByRun[0].when -eq '2026-10-17 08:00')
  $mf = Read-DemoIQCleanupManifest -CleanupConfigPath $both -LogsFolder @($logs) -FromLogs 6>$null
  Check '-FromLogs uses the logs even when a manifest exists' ($mf.PlanSource -eq 'logs')
  $emptyManifest = Write-Manifest (Join-Path $work 'empty/cleanup.config') @()
  $me0 = Read-DemoIQCleanupManifest -CleanupConfigPath $emptyManifest -LogsFolder @($logs) 6>$null
  Check 'a manifest that lists no run falls back to the logs' ($me0.PlanSource -eq 'logs')
  Check 'nothing usable: $null' ($null -eq (Read-DemoIQCleanupManifest -CleanupConfigPath $emptyManifest -SearchFolders @($empty) 6>$null))

  # ====================================================================== execution: routing
  $mi = Read-DemoIQCleanupManifest -CleanupConfigPath $both -Scope LastRun 6>$null
  $pl = New-DemoIQCleanupPlan -Manifest $mi.Manifest -Scope LastRun -AdminUpn $FxAdmin
  Reset-FakeTenant
  Add-Fault "DELETE $GraphRoot/groups/g-new" @((New-MockHttpError -Status 429 -RetryAfter 7 -Json '{"error":{"code":"TooManyRequests","message":"Throttled"}}'))
  $out = Split-Output @(Invoke-DemoIQCleanup -PlanInfo $pl -AdminUpn $FxAdmin -GetToken $GetToken 6>&1)
  $r = $out.Result
  Check 'results: ordered, old counters + usersWithoutToken, approach, errors' ($r -is [System.Collections.Specialized.OrderedDictionary] -and
    (@($r.Keys) -join ',') -eq 'groupsDeleted,groupsFailed,meetingsDeleted,meetingsFailed,meetingResidueRemoved,meetingEmailsRemoved,filesDeleted,filesFailed,emailsDeleted,emailsFailed,notificationEmailsDeleted,chatsDeleted,chatsFailed,meetingChatsDeleted,chatsNotFound,usersWithoutToken,approach,errors' -and
    $r.approach -eq $Approach)
  Check 'team deleted after one HTTP 429 (Retry-After 7 s honored)' ($r.groupsDeleted -eq 1 -and $r.groupsFailed -eq 0 -and
    (Count-Calls DELETE "$GraphRoot/groups/g-new") -eq 2 -and $Stub.Sleeps[0] -eq 7 -and -not $Stub.Graph.Groups.Contains('g-new') -and $Stub.Graph.Groups.Contains('g-other'))
  $adminDeletes = @($Stub.Calls | Where-Object { $_.Method -eq 'DELETE' -and $_.Uri -match '/groups/|/chats/' })
  Check 'every team and chat DELETE carries the administrator''s token' ($adminDeletes.Count -eq 4 -and @($adminDeletes | Where-Object { $_.Token -ne 'tok-admin' }).Count -eq 0)
  Check 'meetings: each organizer deletes through /me with their own token; 404 = already gone' ($r.meetingsDeleted -eq 2 -and $r.meetingsFailed -eq 0 -and
    (First-Token DELETE "$GraphRoot/me/events/ev-a") -eq (Tok $FxAnn) -and (First-Token DELETE "$GraphRoot/me/events/ev-c") -eq (Tok $FxCara))
  Check 'meeting residue: canceled copies and notices swept in each user''s own mailbox' ($r.meetingResidueRemoved -eq 2 -and $r.meetingEmailsRemoved -eq 1 -and
    (First-Token DELETE "$GraphRoot/me/events/ev-bob-res") -eq (Tok $FxBob) -and (First-Token DELETE "$GraphRoot/me/events/ev-adm-res") -eq 'tok-admin' -and
    (First-Token DELETE "$GraphRoot/me/messages/m-bob-cxl") -eq (Tok $FxBob))
  Check 'files: the owner''s drive is indexed through /me (folders, next page) and the item deleted with the owner''s token' ($r.filesDeleted -eq 2 -and $r.filesFailed -eq 0 -and
    (First-Token GET "$GraphRoot/me/drive/root/children?`$top=200&`$skiptoken=p2") -eq (Tok $FxAnn) -and
    (First-Token GET "$GraphRoot/me/drive/items/fold-1/children?`$top=200") -eq (Tok $FxAnn) -and
    (First-Token DELETE "$GraphRoot/me/drive/items/f-run") -eq (Tok $FxAnn) -and (First-Token DELETE "$GraphRoot/me/drive/items/f-costs") -eq (Tok $FxCara) -and
    $Stub.Graph.Drives[$FxAnn].Page2.Count -eq 1)
  Check 'emails: seeded subjects (and RE: replies) deleted from each mailbox with its owner''s token' ($r.emailsDeleted -eq 4 -and $r.emailsFailed -eq 0 -and
    (First-Token DELETE "$GraphRoot/me/messages/m-ann-2") -eq (Tok $FxAnn) -and (First-Token DELETE "$GraphRoot/me/messages/m-cara-1") -eq (Tok $FxCara) -and
    @($Stub.Graph.Messages[$FxBob] | Where-Object { $_.id -eq 'm-bob-lunch' }).Count -eq 1)
  Check 'notification mail swept, the administrator''s with the administrator''s token' ($r.notificationEmailsDeleted -eq 1 -and
    (First-Token DELETE "$GraphRoot/me/messages/m-adm-share") -eq 'tok-admin')
  $chatLists = @(Get-Calls GET "$GraphRoot/me/chats?`$top=50")
  Check 'chats are listed per user with that user''s own token (administrator included)' ($chatLists.Count -eq 4 -and
    (@($chatLists | ForEach-Object { $_.Token } | Sort-Object) -join ',') -eq ((@((Tok $FxAnn), (Tok $FxBob), (Tok $FxCara), 'tok-admin') | Sort-Object) -join ','))
  Check 'group chat (by id) and meeting chat (by subject) deleted; 1 s between chat deletes' ($r.chatsDeleted -eq 1 -and $r.meetingChatsDeleted -eq 1 -and
    $r.chatsFailed -eq 0 -and -not $Stub.Graph.Chats.ContainsKey('19:new@thread.v2') -and -not $Stub.Graph.Chats.ContainsKey('19:mtg-a@thread.v2') -and
    $Stub.Graph.Chats.ContainsKey('19:coffee@thread.v2') -and (@($Stub.Sleeps) -join ',') -eq '7,1')
  Check 'an unmatched planned chat is reported loudly (chatsNotFound + error)' ($r.chatsNotFound -eq 1 -and @($r.errors).Count -eq 1 -and
    $r.errors[0] -match "^group chat 'Ghost chat': not found .* NOT deleted" -and $out.Host -match 'GROUP CHAT NOT FOUND')
  Check 'no user is addressed through /users/{upn}; no unexpected request' (@($Stub.Calls | Where-Object { $_.Uri -match '/users/' }).Count -eq 0 -and $Stub.Unexpected.Count -eq 0)
  Check 'every user''s token is asked for once (cached)' ($Stub.TokenAsks.Count -eq 4 -and @($r.usersWithoutToken).Count -eq 0)
  Check 'no token in the log lines' ($out.Host -notmatch 'tok-' -and $out.Host -match '\[PURGE\]')
  # The cleanup run's own transcript must never read as a seeded run in the logs fallback.
  $purgeLogs = Join-Path $work 'purge-logs'; New-Item -ItemType Directory -Force $purgeLogs | Out-Null
  Set-Content -LiteralPath (Join-Path $purgeLogs 'DemoIQ_20261019_120000.log') -Value $out.Host -Encoding utf8
  Check 'a cleanup transcript is not mistaken for a seeded run' (@(Get-DemoIQCleanupRunsFromLogs -LogsFolder $purgeLogs).Count -eq 0)

  # ====================================================================== a user without a token
  Reset-FakeTenant
  [void]$Stub.NoToken.Add($FxCara)
  $r = Split-Output @(Invoke-DemoIQCleanup -PlanInfo $pl -AdminUpn $FxAdmin -GetToken $GetToken 6>&1) | ForEach-Object { $_.Result }
  Check 'a user without a token: usersWithoutToken + one error' (@($r.usersWithoutToken).Count -eq 1 -and $r.usersWithoutToken[0] -eq $FxCara -and
    @($r.errors | Where-Object { $_ -eq "${FxCara}: no token - their mailbox, drive and chats were not swept" }).Count -eq 1 -and
    @($Stub.TokenAsks | Where-Object { $_ -eq $FxCara }).Count -eq 1)
  Check '... none of their resources is requested' (@($Stub.Calls | Where-Object { $_.Uri -match 'ev-c|f-costs|cara' -or $_.Token -eq (Tok $FxCara) }).Count -eq 0 -and
    (Count-Calls GET "$GraphRoot/me/chats?`$top=50") -eq 3)
  Check '... their meeting and file count as failed; everyone else is swept' ($r.meetingsDeleted -eq 1 -and $r.meetingsFailed -eq 1 -and $r.filesDeleted -eq 1 -and
    $r.filesFailed -eq 1 -and $r.emailsDeleted -eq 3 -and $r.groupsDeleted -eq 1 -and $r.chatsDeleted -eq 1 -and $r.meetingChatsDeleted -eq 1)

  # ====================================================================== permission hints
  Reset-FakeTenant
  Add-Fault "DELETE $GraphRoot/groups/g-new" @((New-Denied))
  foreach ($subj in 'Pump A vibration', 'South Works shift report') {
    $su = $GraphRoot + '/me/messages?$search="subject:' + [uri]::EscapeDataString($subj) + '"&$top=25&$select=id,subject'
    Add-Fault "GET $su $(Tok $FxBob)" @((New-MockHttpError -Status 403 -Json '{"error":{"code":"ErrorAccessDenied","message":"Access is denied. Check credentials and try again."}}'))
  }
  $r = Split-Output @(Invoke-DemoIQCleanup -PlanInfo $pl -AdminUpn $FxAdmin -GetToken $GetToken 6>&1) | ForEach-Object { $_.Result }
  $groupErr = @($r.errors | Where-Object { $_ -like 'group g-new:*' })
  Check '403 on the team: failed, with the role and consent hints' ($r.groupsFailed -eq 1 -and $groupErr.Count -eq 1 -and $groupErr[0] -match 'Authorization_RequestDenied' -and
    $groupErr[0].Contains('Global Administrator') -and $groupErr[0].Contains($ConsentFix))
  $searchErr = @($r.errors | Where-Object { $_ -like "email search ($FxBob)*" })
  Check '403 on a mailbox search: one consent hint per user and area' ($searchErr.Count -eq 1 -and $searchErr[0].Contains($ConsentFix) -and $r.emailsDeleted -eq 3)

  # ====================================================================== no administrator token
  Reset-FakeTenant
  [void]$Stub.NoToken.Add($FxAdmin)
  $r = Split-Output @(Invoke-DemoIQCleanup -PlanInfo $pl -AdminUpn $FxAdmin -GetToken $GetToken 6>&1) | ForEach-Object { $_.Result }
  Check 'no administrator token: team and chats not deleted, reported, nothing sent to /groups or /chats' ($r.groupsFailed -eq 1 -and $r.chatsFailed -eq 2 -and
    @($r.usersWithoutToken) -contains $FxAdmin -and @($r.errors | Where-Object { $_ -match '^no administrator token: 1 team\(s\) and 2 chat\(s\)' }).Count -eq 1 -and
    @($Stub.Calls | Where-Object { $_.Uri -match '/groups/|/chats/' }).Count -eq 0)

  # ====================================================================== Graph retries
  Reset-FakeTenant
  $u = "$GraphRoot/me/events?x=1"
  Add-Fault "GET $u" @((New-MockHttpError -Status 429 -RetryAfter 7), (New-MockHttpError -Status 429))
  $v = Invoke-DemoIQCleanupGraph -Method GET -Uri $u -Token (Tok $FxAnn) 6>$null
  Check 'Graph: HTTP 429 re-issued after Retry-After (default 5 s)' ((Count-Calls GET $u) -eq 3 -and (@($Stub.Sleeps) -join ',') -eq '7,5' -and @($v.value).Count -eq 2)
  Reset-Stub
  Add-Fault "GET $u" @((New-MockHttpError -Status 429), (New-MockHttpError -Status 429), (New-MockHttpError -Status 429))
  $thrown = $null; try { Invoke-DemoIQCleanupGraph -Method GET -Uri $u -Token (Tok $FxAnn) 6>$null } catch { $thrown = $_ }
  Check 'Graph: a third 429 is re-thrown' ($thrown -and (Get-DemoIQCleanupStatusCode $thrown) -eq 429 -and (Count-Calls GET $u) -eq 3)
  Reset-Stub
  Add-Fault "GET $u" @((New-MockHttpError -Status 500))
  $thrown = $null; try { Invoke-DemoIQCleanupGraph -Method GET -Uri $u -Token (Tok $FxAnn) 6>$null } catch { $thrown = $_ }
  Check 'Graph: any other failure is re-thrown at once' ($thrown -and (Get-DemoIQCleanupStatusCode $thrown) -eq 500 -and (Count-Calls GET $u) -eq 1 -and $Stub.Sleeps.Count -eq 0)

  # ====================================================================== demo-user sign-in
  $tokenUri = "https://login.microsoftonline.com/$FxTenant/oauth2/v2.0/token"
  function Invoke-UserToken([object[]]$Queue, [string]$Password = $FxPassword) {
    Reset-Stub
    foreach ($q in $Queue) { $Stub.TokenQueue.Enqueue($q) }
    Split-Output @(Get-DemoIQCleanupUserToken -TenantId $FxTenant -ClientId $FxApp -Upn $FxBob -Password $Password 6>&1)
  }
  $ok = [pscustomobject]@{ access_token = 'user-access-1'; refresh_token = 'r'; expires_in = 3600 }
  $t = Invoke-UserToken @($ok)
  $call = $Stub.Calls[0]
  $keys = @($call.Body.Keys | Sort-Object) -join ','
  Check 'sign-in: one public-client password grant to the tenant''s token endpoint' ($t.Result.ok -and $t.Result.token -eq 'user-access-1' -and $Stub.Calls.Count -eq 1 -and
    $call.Method -eq 'POST' -and $call.Uri -eq $tokenUri -and $call.ContentType -eq 'application/x-www-form-urlencoded')
  Check '... body is exactly grant_type, client_id, username, password, scope (no assertion or secret)' ($keys -eq 'client_id,grant_type,password,scope,username' -and
    $call.Body.grant_type -eq 'password' -and $call.Body.client_id -eq $FxApp -and $call.Body.username -eq $FxBob -and
    $call.Body.scope -eq 'https://graph.microsoft.com/.default offline_access' -and
    -not ($call.Body.ContainsKey('client_assertion') -or $call.Body.ContainsKey('client_assertion_type') -or $call.Body.ContainsKey('client_secret')))
  $t = Invoke-UserToken @((New-AadError '50076'), $ok)
  Check 'sign-in: AADSTS50076 (MFA change not yet effective) retried after 30 s, then succeeds' ($t.Result.ok -and $Stub.Calls.Count -eq 2 -and (@($Stub.Sleeps) -join ',') -eq '30')
  $t = Invoke-UserToken @((New-AadError '53003'), (New-AadError '50079'), $ok)
  Check 'sign-in: Conditional Access propagation waits 30 s x attempt' ($t.Result.ok -and (@($Stub.Sleeps) -join ',') -eq '30,60')
  $t = Invoke-UserToken @((New-AadError '50126'), $ok)
  Check 'sign-in: AADSTS50126 is final at once (permanent)' (-not $t.Result.ok -and $t.Result.permanent -and $t.Result.error -match '50126' -and
    $Stub.Calls.Count -eq 1 -and $Stub.Sleeps.Count -eq 0 -and $null -eq $t.Result.token)
  $t = Invoke-UserToken @((New-MockHttpError -Status 503 -RetryAfter 9 -Json '{"error":"temporarily_unavailable"}'), $ok)
  Check 'sign-in: HTTP 503 retried after Retry-After' ($t.Result.ok -and (@($Stub.Sleeps) -join ',') -eq '9')
  $net = [System.Management.Automation.ErrorRecord]::new([System.Net.Http.HttpRequestException]::new('No such host is known.'), 'Net', 'ConnectionError', $null)
  $t = Invoke-UserToken @($net, $ok)
  Check 'sign-in: a network failure is retried' ($t.Result.ok -and (@($Stub.Sleeps) -join ',') -eq '5')
  $t = Invoke-UserToken @((New-AadError '50079'), (New-AadError '50079'), (New-AadError '50079'), (New-AadError '50079'))
  Check 'sign-in: gives up after 4 attempts, not permanent' (-not $t.Result.ok -and -not $t.Result.permanent -and $Stub.Calls.Count -eq 4 -and (@($Stub.Sleeps) -join ',') -eq '30,60,90')
  $t = Invoke-UserToken @($ok) -Password ''
  Check 'sign-in: no password -> permanent, nothing sent' (-not $t.Result.ok -and $t.Result.permanent -and $t.Result.error -eq 'no password' -and $Stub.Calls.Count -eq 0)
  Reset-Stub
  $t = Get-DemoIQCleanupUserToken -TenantId $null -ClientId $FxApp -Upn $FxBob -Password $FxPassword 6>$null
  Check 'sign-in: a missing tenant is reported, not thrown' (-not $t.ok -and $t.permanent -and $Stub.Calls.Count -eq 0)
  $t = Invoke-UserToken @((New-AadError '50076'), (New-AadError '50126'))
  Check 'sign-in: the password never reaches the log lines' ($t.Host -match '\[PURGE\]' -and $t.Host.IndexOf($FxPassword) -lt 0)

  # ====================================================================== manifest pruning
  Reset-FakeTenant
  $pruneFile = Write-Manifest (Join-Path $work 'prune/cleanup.config') @((New-Run1), (New-Run2))
  $mi = Read-DemoIQCleanupManifest -CleanupConfigPath $pruneFile -Scope LastRun 6>$null
  $pl = New-DemoIQCleanupPlan -Manifest $mi.Manifest -Scope LastRun -AdminUpn $FxAdmin
  $mirror = Write-Manifest (Join-Path $work 'prune/mirror/cleanup.config') @((New-Run1), (New-Run2))
  $up = Update-DemoIQCleanupManifest -ManifestInfo $mi -PlanInfo $pl -Scope LastRun -LocalCopies @($mirror, (Join-Path $work 'prune/absent/cleanup.config')) 6>$null
  $after = Read-Json $pruneFile
  Check 'LastRun prune: only the last run leaves the local manifest' ($up.pruned -and @($up.errors).Count -eq 0 -and @($after.runs).Count -eq 1 -and
    $after.runs[0].timestamp -eq '2026-10-18 09:00:00' -and @($after.tableOfContents).Count -eq 1 -and $after.tenantConfig.adminUpn -eq $FxAdmin)
  Check '... an existing local copy is rewritten, a missing one is not created; nothing is uploaded' (@((Read-Json $mirror).runs).Count -eq 1 -and
    -not (Test-Path -LiteralPath (Join-Path $work 'prune/absent/cleanup.config')) -and $Stub.Calls.Count -eq 0)

  # The administrator's OneDrive copy is the LastRun source; the run's local copy is pruned too.
  Reset-FakeTenant
  $Stub.Graph.OneDriveManifest = (New-Manifest @((New-Run1), (New-Run2)) | ConvertTo-Json -Depth 12)
  $wsP = Join-Path $work 'ws-onedrive'
  $fA = Write-Manifest (Join-Path $wsP 'runs/20261018_0900__north__fixturetest/DemoIQ-Output/cleanup.config') @((New-Run1)) ([datetime]'2026-10-18 09:30')
  $fB = Write-Manifest (Join-Path $wsP 'runs/20261019_1000__south__fixturetest/DemoIQ-Output/cleanup.config') @((New-Run2)) ([datetime]'2026-10-19 10:30')
  $folders = @((Split-Path -Parent $fA), (Split-Path -Parent $fB))
  $mi = Read-DemoIQCleanupManifest -SearchFolders $folders -AdminToken $AdminTokenBlock -Scope LastRun 6>$null
  Check 'LastRun from the admin OneDrive (GET as the administrator); the run''s local copy is listed' ($mi.FromOneDrive -and $mi.Source -like 'admin OneDrive*' -and
    @($mi.LocalPaths).Count -eq 1 -and (Test-SamePath $mi.LocalPaths[0] $fB) -and (First-Token GET $OneDriveUri) -eq 'tok-admin')
  $pl = New-DemoIQCleanupPlan -Manifest $mi.Manifest -Scope LastRun -AdminUpn $FxAdmin
  $up = Update-DemoIQCleanupManifest -ManifestInfo $mi -PlanInfo $pl -Scope LastRun -AdminToken $AdminTokenBlock 6>$null
  $put = @(Get-Calls PUT $OneDriveUri)
  $odAfter = $Stub.Graph.OneDriveManifest | ConvertFrom-Json
  Check 'LastRun prune: PUT to the admin OneDrive with the administrator''s token, minus the last run' ($up.pruned -and @($up.errors).Count -eq 0 -and $put.Count -eq 1 -and
    $put[0].Token -eq 'tok-admin' -and $put[0].ContentType -eq 'application/json' -and @($odAfter.runs).Count -eq 1 -and $odAfter.runs[0].timestamp -eq '2026-10-18 09:00:00')
  Check '... the deleted run''s local copy is emptied, the other run folder is untouched' (@((Read-Json $fB).runs).Count -eq 0 -and @((Read-Json $fA).runs).Count -eq 1)
  # A pruned (empty) newest manifest no longer hides the older run.
  $mi = Read-DemoIQCleanupManifest -SearchFolders $folders -Scope LastRun 6>$null
  Check 'after the prune, LastRun finds the remaining run in the older folder' ((Test-SamePath $mi.Source $fA) -and @(Get-DemoIQCleanupManifestRuns $mi.Manifest).Count -eq 1)
  Reset-Stub
  Add-Fault "GET $OneDriveUri" @((New-Denied))
  $mi = Read-DemoIQCleanupManifest -SearchFolders $folders -AdminToken $AdminTokenBlock -Scope LastRun 6>$null
  Check 'an unreadable OneDrive copy is recorded and the local copies are used' ((Test-SamePath $mi.Source $fA) -and -not $mi.FromOneDrive -and
    @($mi.Errors | Where-Object { $_ -like 'admin OneDrive manifest: HTTP 403*' }).Count -eq 1)

  # ====================================================================== two run folders
  Reset-FakeTenant
  $ws2 = Join-Path $work 'ws-two'
  $f1 = Write-Manifest (Join-Path $ws2 'runs/20261018_0900__north__fixturetest/DemoIQ-Output/cleanup.config') @((New-Run1)) ([datetime]'2026-10-18 09:30')
  $f2 = Write-Manifest (Join-Path $ws2 'runs/20261019_1000__south__fixturetest/DemoIQ-Output/cleanup.config') @((New-Run2)) ([datetime]'2026-10-19 10:30')
  $folders = @((Split-Path -Parent $f1), (Split-Path -Parent $f2))
  $mi = Read-DemoIQCleanupManifest -SearchFolders $folders -Scope LastRun 6>$null
  $pl = New-DemoIQCleanupPlan -Manifest $mi.Manifest -Scope LastRun -AdminUpn $FxAdmin
  Check 'two run folders, LastRun: only the newest run' ((Test-SamePath $mi.Source $f2) -and @($mi.LocalPaths).Count -eq 1 -and $pl.Counts.groups -eq 1 -and
    $pl.Counts.meetings -eq 2 -and $pl.TargetRuns[0].timestamp -eq '2026-10-19 10:00:00')
  $Stub.Graph.OneDriveManifest = (New-Manifest @((New-Run2)) | ConvertTo-Json -Depth 12)
  $mi = Read-DemoIQCleanupManifest -SearchFolders $folders -AdminToken $AdminTokenBlock -Scope AllRuns 6>$null
  $pa = New-DemoIQCleanupPlan -Manifest $mi.Manifest -Scope AllRuns -AdminUpn $FxAdmin
  $merged = @(Get-DemoIQCleanupManifestRuns $mi.Manifest)
  Check 'two run folders, AllRuns: merged oldest first, the OneDrive duplicate counted once' ($merged.Count -eq 2 -and $merged[0].timestamp -eq '2026-10-18 09:00:00' -and
    $merged[1].timestamp -eq '2026-10-19 10:00:00' -and $mi.Source -like 'merged 3 manifests:*' -and $mi.FromOneDrive -and @($mi.LocalPaths).Count -eq 2)
  Check '... and the counts add up (2 teams, 3 meetings, 3 files, 3 subjects, 3 chats)' ($pa.Counts.groups -eq 2 -and $pa.Counts.meetings -eq 3 -and
    $pa.Counts.files -eq 3 -and $pa.Counts.emailSubjects -eq 3 -and $pa.Counts.chats -eq 3 -and @($pa.ByRun).Count -eq 2)
  $up = Update-DemoIQCleanupManifest -ManifestInfo $mi -PlanInfo $pa -Scope AllRuns -AdminToken $AdminTokenBlock 6>$null
  $odAfter = $Stub.Graph.OneDriveManifest | ConvertFrom-Json
  Check 'AllRuns prune: both run folders'' manifests emptied, OneDrive PUT with runs = [] and its other fields' ($up.pruned -and @($up.errors).Count -eq 0 -and
    @((Read-Json $f1).runs).Count -eq 0 -and @((Read-Json $f2).runs).Count -eq 0 -and @($odAfter.runs).Count -eq 0 -and $odAfter.tenantConfig.tenantId -eq $FxTenant -and
    (First-Token PUT $OneDriveUri) -eq 'tok-admin')
  Check '... afterwards nothing is left to clean up' ($null -eq (Read-DemoIQCleanupManifest -SearchFolders $folders -AdminToken $AdminTokenBlock -Scope AllRuns 6>$null))
  $lp = Read-DemoIQCleanupManifest -SearchFolders $folders -LogsFolder @($logs) -Scope AllRuns 6>$null
  $up = Update-DemoIQCleanupManifest -ManifestInfo $lp -PlanInfo (New-DemoIQCleanupPlan -Manifest $lp.Manifest -Scope AllRuns) -Scope AllRuns 6>$null
  Check 'a log-sourced plan prunes nothing' (-not $up.pruned -and @($up.errors).Count -eq 0)

  # ====================================================================== local state
  # Local follow-up touches only the seed run folders whose OWN run was deleted.
  function New-LocalRun([string]$Ws, [string]$Name, [string]$CreatedAt, [object[]]$Runs = @(), $WrittenAt, [string]$Kind = 'seed', [string]$Deliverable = 'aop_runbook.html') {
    $rf = Join-Path $Ws "runs/$Name"
    foreach ($d in 'DemoIQ-Output', 'logs') { New-Item -ItemType Directory -Force (Join-Path $rf $d) | Out-Null }
    Set-Content -LiteralPath (Join-Path $rf 'run.json') -Encoding utf8 -Value ([ordered]@{ runId = $Name; kind = $Kind; createdAt = $CreatedAt; status = 'created' } | ConvertTo-Json)
    if (@($Runs).Count) { $null = Write-Manifest (Join-Path $rf 'DemoIQ-Output/cleanup.config') $Runs $WrittenAt }
    Set-Content -LiteralPath (Join-Path $rf 'DemoIQ-Output/seed_ledger.json') -Value '{"items":[]}' -Encoding utf8
    Set-Content -LiteralPath (Join-Path $rf "DemoIQ-Output/$Deliverable") -Value "<html>$Name</html>" -Encoding utf8
    return (Get-Item -LiteralPath $rf).FullName
  }
  function Test-Ledger([string]$RunFolder) { Test-Path -LiteralPath (Join-Path $RunFolder 'DemoIQ-Output/seed_ledger.json') }
  function Get-Cleared($State) { @($State.ledgersCleared) -join ',' }

  # 1. The NEWEST folder by name (N) was created but never launched - its content preview and
  # ledger stand for a launch cancelled at the preview gate or one that failed before writing
  # its manifest. LastRun deletes B's run (the newest manifest): B is cleared, N never is.
  $wsN = Join-Path $work 'ws-never'
  $fA = New-LocalRun $wsN '20261018_0900__north__fixturetest' '2026-10-18T08:55:00' @((New-Run1)) ([datetime]'2026-10-18 09:30')
  $fB = New-LocalRun $wsN '20261019_1000__south__fixturetest' '2026-10-19T09:55:00' @((New-Run2)) ([datetime]'2026-10-19 10:30')
  $fN = New-LocalRun $wsN '20261019_2300__never__fixturetest' '2026-10-19T23:00:00' -Deliverable 'content_preview.html'
  $cByName = New-LocalRun $wsN '20261019_1100__purge-cleanup' '2026-10-19T11:00:00' @((New-Run2)) ([datetime]'2026-10-19 11:05')
  $cByKind = New-LocalRun $wsN '20261019_1200__purge' '2026-10-19T12:00:00' @((New-Run2)) ([datetime]'2026-10-19 12:05') -Kind cleanup
  Check 'seed run folders exclude cleanup runs (by name or run.json kind)' ((@(Get-DemoIQCleanupSeedRunFolders -Workspace $wsN | ForEach-Object { $_.Name }) -join ',') -eq
    '20261018_0900__north__fixturetest,20261019_1000__south__fixturetest,20261019_2300__never__fixturetest')
  $search = @(Get-DemoIQCleanupSeedRunFolders -Workspace $wsN | ForEach-Object { $_.FullName })
  $mi = Read-DemoIQCleanupManifest -SearchFolders $search -Scope LastRun 6>$null
  $pl = New-DemoIQCleanupPlan -Manifest $mi.Manifest -Scope LastRun -AdminUpn $FxAdmin
  $null = Update-DemoIQCleanupManifest -ManifestInfo $mi -PlanInfo $pl -Scope LastRun 6>$null
  Check 'LastRun deletes the newest manifest''s run (B''s), whose manifest is then pruned' (@($pl.TargetRuns).Count -eq 1 -and $pl.TargetRuns[0].timestamp -eq '2026-10-19 10:00:00' -and
    @((Read-Json (Join-Path $fB 'DemoIQ-Output/cleanup.config')).runs).Count -eq 0 -and $mi.LocalRunKeys.Count -eq 2)
  $ls = Clear-DemoIQCleanupLocalState -Workspace $wsN -PlanInfo $pl 6>$null
  Check 'without the read-time record a pruned folder is not recognized: nothing touched, the run reported' ((Get-Cleared $ls) -eq '' -and (Test-Ledger $fB) -and
    (@($ls.notFoundLocally) -join ',') -eq '2026-10-19 10:00:00')
  $ls = Clear-DemoIQCleanupLocalState -Workspace $wsN -PlanInfo $pl -ManifestInfo $mi 6>$null
  Check 'LastRun clears the ledger of the folder whose run was deleted (B), not of the newest folder' ((Get-Cleared $ls) -eq '20261019_1000__south__fixturetest' -and
    -not (Test-Ledger $fB) -and @($ls.notFoundLocally).Count -eq 0 -and @($ls.errors).Count -eq 0)
  Check '... the newer never-launched folder, the older run and the cleanup run folders keep their ledgers' ((Test-Ledger $fN) -and
    (Test-Path (Join-Path $fN 'DemoIQ-Output/content_preview.html')) -and (Test-Ledger $fA) -and (Test-Ledger $cByName) -and (Test-Ledger $cByKind))
  $ls = Clear-DemoIQCleanupLocalState -Workspace $wsN -PlanInfo $pl -ManifestInfo $mi -ArchiveLocalDeliverables 6>$null
  $zip = Join-Path $wsN 'archive/20261019_1000__south__fixturetest__DemoIQ-Output.zip'
  Check '-ArchiveLocalDeliverables archives first, then removes, only the deleted run''s deliverables' (@($ls.localArchives).Count -eq 1 -and
    (Test-SamePath $ls.localArchives[0].backup $zip) -and (Test-Path -LiteralPath $zip) -and -not (Test-Path (Join-Path $fB 'DemoIQ-Output')) -and
    (Test-Path (Join-Path $fN 'DemoIQ-Output/content_preview.html')) -and (Test-Path (Join-Path $fA 'DemoIQ-Output/aop_runbook.html')) -and @($ls.errors).Count -eq 0)

  # 2. The ledger is in A: LastRun deletes run 1 (the administrator's OneDrive copy), while the
  # newer folder B failed before writing its manifest (a ledger, no manifest).
  $wsA = Join-Path $work 'ws-older'
  $fA = New-LocalRun $wsA '20261018_0900__north__fixturetest' '2026-10-18T08:55:00' @((New-Run1)) ([datetime]'2026-10-18 09:30')
  $fB = New-LocalRun $wsA '20261019_1000__failed__fixturetest' '2026-10-19T09:55:00' -Deliverable 'content_preview.html'
  Reset-FakeTenant
  $Stub.Graph.OneDriveManifest = (New-Manifest @((New-Run1)) | ConvertTo-Json -Depth 12)
  $search = @(Get-DemoIQCleanupSeedRunFolders -Workspace $wsA | ForEach-Object { $_.FullName })
  $mi = Read-DemoIQCleanupManifest -SearchFolders $search -AdminToken $AdminTokenBlock -Scope LastRun 6>$null
  $pl = New-DemoIQCleanupPlan -Manifest $mi.Manifest -Scope LastRun -AdminUpn $FxAdmin
  $null = Update-DemoIQCleanupManifest -ManifestInfo $mi -PlanInfo $pl -Scope LastRun -AdminToken $AdminTokenBlock 6>$null
  $ls = Clear-DemoIQCleanupLocalState -Workspace $wsA -PlanInfo $pl -ManifestInfo $mi -ArchiveLocalDeliverables 6>$null
  Check 'a run deleted from the OneDrive copy clears ITS folder (A); the newer folder without a manifest keeps everything' ($mi.FromOneDrive -and
    (Get-Cleared $ls) -eq '20261018_0900__north__fixturetest' -and -not (Test-Ledger $fA) -and @($ls.localArchives).Count -eq 1 -and
    $ls.localArchives[0].run -eq '20261018_0900__north__fixturetest' -and (Test-Ledger $fB) -and (Test-Path (Join-Path $fB 'DemoIQ-Output/content_preview.html')))

  # 3. A deleted run that no local folder holds (only on the OneDrive copy) is reported.
  $wsO = Join-Path $work 'ws-onedrive-only'
  $fA = New-LocalRun $wsO '20261018_0900__north__fixturetest' '2026-10-18T08:55:00' @((New-Run1)) ([datetime]'2026-10-18 09:30')
  $run0 = New-Run1; $run0.timestamp = '2026-10-17 08:00:00'
  Reset-FakeTenant
  $Stub.Graph.OneDriveManifest = (New-Manifest @($run0, (New-Run1)) | ConvertTo-Json -Depth 12)
  $mi = Read-DemoIQCleanupManifest -SearchFolders @($fA) -AdminToken $AdminTokenBlock -Scope AllRuns 6>$null
  $pl = New-DemoIQCleanupPlan -Manifest $mi.Manifest -Scope AllRuns -AdminUpn $FxAdmin
  $null = Update-DemoIQCleanupManifest -ManifestInfo $mi -PlanInfo $pl -Scope AllRuns -AdminToken $AdminTokenBlock 6>$null
  $out = Split-Output @(Clear-DemoIQCleanupLocalState -Workspace $wsO -PlanInfo $pl -ManifestInfo $mi 6>&1)
  Check 'a deleted run no local folder holds is reported in notFoundLocally; the run that has one is cleared' (@($pl.TargetRuns).Count -eq 2 -and
    (Get-Cleared $out.Result) -eq '20261018_0900__north__fixturetest' -and (@($out.Result.notFoundLocally) -join ',') -eq '2026-10-17 08:00:00' -and
    $out.Host -match 'No seed run folder on this machine holds run 2026-10-17 08:00:00')

  # 4. A Full-mode folder (H) copies earlier runs into its manifest as history: deleting such a
  # run never clears H; deleting H's own run does.
  $wsH = Join-Path $work 'ws-history'
  $run3 = New-Run2; $run3.timestamp = '2026-10-19 14:00:00'; $run3.team = [pscustomobject]@{ groupId = 'g-three'; channels = @() }
  $fB = New-LocalRun $wsH '20261019_1000__south__fixturetest' '2026-10-19T09:55:00' @((New-Run2)) ([datetime]'2026-10-19 10:30')
  $fH = New-LocalRun $wsH '20261019_1355__fullmode__fixturetest' '2026-10-19T13:55:00' @((New-Run2), $run3) ([datetime]'2026-10-19 14:30')
  $mi = Read-DemoIQCleanupManifest -SearchFolders @($fB, $fH) -Scope AllRuns 6>$null
  $plRun2 = New-DemoIQCleanupPlan -Manifest (New-Manifest @((New-Run1), (New-Run2))) -Scope LastRun -AdminUpn $FxAdmin
  $ls = Clear-DemoIQCleanupLocalState -Workspace $wsH -PlanInfo $plRun2 -ManifestInfo $mi 6>$null
  Check 'a run a Full-mode folder only holds as history does not clear that folder' ((Get-Cleared $ls) -eq '20261019_1000__south__fixturetest' -and (Test-Ledger $fH))
  $ls = Clear-DemoIQCleanupLocalState -Workspace $wsH -PlanInfo (New-DemoIQCleanupPlan -Manifest $mi.Manifest -Scope AllRuns -AdminUpn $FxAdmin) -ManifestInfo $mi 6>$null
  Check '... while its own run does' ((Get-Cleared $ls) -eq '20261019_1355__fullmode__fixturetest' -and -not (Test-Ledger $fH) -and @($ls.notFoundLocally).Count -eq 0)

  # 5. A log-sourced plan: the folder holding the deleted run's log, and only that one.
  $wsG = Join-Path $work 'ws-logs'
  $fL = New-LocalRun $wsG '20261017_0755__logged__fixturetest' '2026-10-17T07:55:00'
  $fM = New-LocalRun $wsG '20261018_0755__other__fixturetest' '2026-10-18T07:55:00'
  Copy-Item -LiteralPath (Join-Path $logs 'DemoIQ_20261017_080000.log') -Destination (Join-Path $fL 'logs')
  Copy-Item -LiteralPath (Join-Path $logs 'DemoIQ_20261018_080000.log') -Destination (Join-Path $fM 'logs')
  $mi = Read-DemoIQCleanupManifest -SearchFolders @($fL, $fM) -LogsFolder @((Join-Path $fL 'logs'), (Join-Path $fM 'logs')) -Scope AllRuns 6>$null
  $ls = Clear-DemoIQCleanupLocalState -Workspace $wsG -PlanInfo (New-DemoIQCleanupPlan -Manifest $mi.Manifest -Scope AllRuns -AdminUpn $FxAdmin) -ManifestInfo $mi 6>$null
  Check 'a log-sourced plan clears the folder that holds the deleted run''s log only' ($mi.PlanSource -eq 'logs' -and
    (Get-Cleared $ls) -eq '20261017_0755__logged__fixturetest' -and (Test-Ledger $fM) -and @($ls.notFoundLocally).Count -eq 0)

  # ====================================================================== the preview CLI
  function Invoke-Cli([string[]]$Arguments) {
    $lines = @(& $pwshExe -NoProfile -File $cliPath @Arguments 2>&1 | ForEach-Object { [string]$_ })
    $code = $LASTEXITCODE
    $last = $lines | Where-Object { $_.TrimStart().StartsWith('{') } | Select-Object -Last 1
    $obj = $null; if ($last) { try { $obj = $last | ConvertFrom-Json } catch {} }
    [pscustomobject]@{ Code = $code; Text = ($lines -join "`n"); Json = $obj }
  }
  . (Join-Path $S 'session_store.ps1')
  $rfCli = Join-Path $work 'cli-run'
  $cliManifest = Write-Manifest (Join-Path $rfCli 'DemoIQ-Output/cleanup.config') @((New-Run1), (New-Run2))
  $x = Invoke-Cli @('-RunFolder', $rfCli, '-Json')
  $p = $x.Json
  Check 'CLI: a dry-run of the run folder''s manifest, with no sign-in (exit 0)' ($x.Code -eq 0 -and $p.ok -and $p.mode -eq 'dry-run' -and $p.scope -eq 'LastRun' -and
    -not (Test-DemoIQSessionSecret -Name 'admin-session'))
  Check 'CLI: manifestSource is the local file; counts for the last run' ((Test-SamePath $p.manifestSource $cliManifest) -and
    $p.planSource -eq 'manifest' -and $p.runsTargeted -eq 1 -and $p.totalRunsKnown -eq 2 -and $p.plan.groups -eq 1 -and $p.plan.meetings -eq 2 -and
    $p.plan.files -eq 2 -and $p.plan.emailSubjects -eq 2 -and $p.plan.chats -eq 2 -and @($p.byRun).Count -eq 1 -and $p.tenant -eq 'fixturetest.onmicrosoft.com')
  Check 'CLI: the note says nothing was deleted and how deletion runs' ($p.note -match 'nothing was deleted' -and $p.note.Contains('scripts/admin_signin.ps1') -and
    $p.note.Contains('scripts/launch_seeder.ps1 -Cleanup -Scope LastRun'))
  $x = Invoke-Cli @('-RunFolder', $rfCli, '-Scope', 'AllRuns', '-Json')
  Check 'CLI: -Scope AllRuns' ($x.Code -eq 0 -and $x.Json.runsTargeted -eq 2 -and $x.Json.plan.groups -eq 2 -and $x.Json.plan.chats -eq 3)
  $x = Invoke-Cli @('-RunFolder', $rfCli)
  Check 'CLI: the readable report' ($x.Code -eq 0 -and $x.Text -match 'PREVIEW \(nothing deleted\)' -and
    $x.Text.Contains('TOTAL to remove:  Teams=1  Meetings=2  Files=2  Email threads=2  Chats=2') -and $x.Text.Contains('launch_seeder.ps1 -Cleanup -Scope LastRun'))
  $x = Invoke-Cli @('-CleanupConfigPath', $cliManifest, '-LogsFolder', $logs, '-FromLogs', '-Json')
  Check 'CLI: -FromLogs previews the logs' ($x.Code -eq 0 -and $x.Json.planSource -eq 'logs' -and $x.Json.plan.groups -eq 1)
  $x = Invoke-Cli @('-RunFolder', $rfCli, '-Execute', '-Json')
  Check 'CLI: -Execute is refused (exit 2) with the cleanup-run route' ($x.Code -eq 2 -and -not $x.Json.ok -and
    $x.Json.error -eq 'Deletion runs as a cleanup run that relaxes and restores MFA/CA like a seed: sign in with scripts/admin_signin.ps1, then run scripts/launch_seeder.ps1 -Cleanup -Scope LastRun.')
  $enc = Join-Path $work 'encrypted.json'
  Set-Content -LiteralPath $enc -Value '{"v":"cdx-enc-v1","salt":[1,2,3],"iv":[4,5],"data":[6,7]}' -Encoding utf8
  $x = Invoke-Cli @('-ConfigPath', $enc, '-RunFolder', $rfCli, '-Json')
  Check 'CLI: an encrypted config is refused (exit 2) with the convert hint' ($x.Code -eq 2 -and -not $x.Json.ok -and $x.Json.error.Contains('scripts/convert_config.ps1'))
  $plainCfg = Join-Path $work 'plain-config.json'
  Set-Content -LiteralPath $plainCfg -Encoding utf8 -Value (@{ adminUpn = 'admin@other-fixture.onmicrosoft.com'; u1upn = $FxAnn; existingAppId = $FxApp } | ConvertTo-Json)
  $x = Invoke-Cli @('-ConfigPath', $plainCfg, '-RunFolder', $rfCli, '-Json')
  Check 'CLI: a plain config supplies the administrator' ($x.Code -eq 0 -and $x.Json.tenant -eq 'other-fixture.onmicrosoft.com')
  $x = Invoke-Cli @('-RunFolder', (Join-Path $work 'missing-run'), '-Json')
  Check 'CLI: a missing run folder is refused (exit 2)' ($x.Code -eq 2 -and $x.Json.error -like 'Run folder not found*')

  # In-process, so the Graph stub would see any request: the preview sends none.
  Reset-Stub
  $inProc = & $cliPath -RunFolder $rfCli -Scope AllRuns -Json
  Check 'CLI: never contacts the tenant (no request reached the stub)' ($LASTEXITCODE -eq 0 -and $Stub.Calls.Count -eq 0 -and ($inProc | ConvertFrom-Json).runsTargeted -eq 2)

  # The default search: every seed run folder of the workspace, never a cleanup run's.
  $wsHome = Get-DemoIQWorkspace
  if ($env:DEMOIQ_TEST_ISO_ROOT -and $wsHome.StartsWith($env:DEMOIQ_TEST_ISO_ROOT)) {
    $null = Write-Manifest (Join-Path $wsHome 'runs/20261018_0900__north__fixturetest/DemoIQ-Output/cleanup.config') @((New-Run1)) ([datetime]'2026-10-18 09:30')
    $null = Write-Manifest (Join-Path $wsHome 'runs/20261019_1000__south__fixturetest/DemoIQ-Output/cleanup.config') @((New-Run2)) ([datetime]'2026-10-19 10:30')
    $stray = New-Run2; $stray.timestamp = '2026-10-19 11:30:00'
    $null = Write-Manifest (Join-Path $wsHome 'runs/20261019_1130__purge-cleanup/DemoIQ-Output/cleanup.config') @($stray) ([datetime]'2026-10-19 11:45')
    $x = Invoke-Cli @('-Scope', 'AllRuns', '-Json')
    Check 'CLI default search: every seed run folder merged, cleanup runs skipped' ($x.Code -eq 0 -and $x.Json.totalRunsKnown -eq 2 -and $x.Json.runsTargeted -eq 2 -and
      $x.Json.plan.groups -eq 2 -and $x.Json.plan.meetings -eq 3 -and $x.Json.manifestSource -like 'merged 2 manifests:*')
    $x = Invoke-Cli @('-Json')
    Check 'CLI default search, LastRun: the newest seed run' ($x.Code -eq 0 -and $x.Json.runsTargeted -eq 1 -and $x.Json.byRun[0].when -eq '2026-10-19 10:00:00' -and
      $x.Json.manifestSource -like '*20261019_1000__south__fixturetest*')
  } else { Write-Host '  SKIP CLI default workspace search (HOME is not isolated on this OS)' -ForegroundColor DarkYellow }
}
finally {
  Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

if ($script:fails) { Write-Host "test_cleanup_engine: $($script:fails) FAILURE(S)" -ForegroundColor Red; exit 1 }
Write-Host 'test_cleanup_engine: all passed' -ForegroundColor Green
exit 0

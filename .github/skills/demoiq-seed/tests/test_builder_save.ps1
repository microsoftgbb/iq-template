# test_builder_save.ps1 - the Config Builder's save and drafted-config routes, offline.
#
# Starts the REAL scripts/serve_local.ps1 as a foreground child process (loopback only)
# with the save endpoint on, a throwaway configs library and a fixed token, and drives it
# over HTTP exactly as DemoIQ-Config-Builder.html does. Proves, on this OS:
#   * POST /save stores a PLAIN config, owner-only (0600 on macOS/Linux) in an owner-only
#     library, with adminPwd and existingCertThumbprint forced empty and every other
#     field intact; the same name twice never overwrites (numeric suffix), and a path in
#     the name is sanitized into the library
#   * no or a wrong token = 403; an encrypted cdx-enc-v1 envelope (with the convert hint),
#     a non-object, invalid JSON, or a config without a usable adminUpn, GUID app ID or
#     string demo-user UPNs = 422; an empty body = 400; nothing is written for any of them
#   * GET /config returns a saved or drafted config (token-gated: 403 without; 400 for a
#     path or an unsanitized name; 404 for an unknown one; 422 for an encrypted file)
#   * -PrefillAppId / -PrefillSpeechId / -PrefillConfig land URL-encoded in [SERVE-URL],
#     and the server output never carries a password
#   * without -AllowSave there is no /save and no /config
#   * a draft ("demoiqDraft": true) named by -PrefillConfig is REPLACED by the config the
#     Builder saves: the save strips the marker, the draft is deleted only after a
#     successful write, and the reply names it (replacedDraft). A refused save, a
#     non-draft prefill, a draft changed or removed after startup, or another draft in
#     the library is never deleted; a draft that cannot be deleted leaves the save
#     standing with replacedDraftError (macOS/Windows: needs an undeletable file)
#   * token at rest (macOS/Linux): the serve folder is 0700, the state file and the
#     -Detach token bootstrap file are 0600 from their first byte, the detached child
#     deletes the bootstrap file and still knows the draft it replaces, and the token
#     never appears in stdout or the child's logs except in the [SERVE-URL] fragment
$ErrorActionPreference = 'Stop'

# Self-isolate: a throwaway HOME and session folder, so the real workspace (configs
# library, serve state files) is never read, overwritten or deleted.
if ($env:DEMOIQ_TEST_ISOLATED -ne '1') {
  $iso = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-save-iso-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
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
$serveScript = Join-Path $root 'scripts/serve_local.ps1'
$pwshExe = (Get-Process -Id $PID).Path
$work = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-save-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null
$lib = Join-Path $work 'configs'   # created by serve_local itself
$Token = 'fixture-save-token-0123456789abcdef'
$Speech = '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-fixture/providers/Microsoft.CognitiveServices/accounts/ai-fixture'
$DraftName = 'DemoIQ_config - Draft (fixture).json'
$prefs = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'fixtures/test-prefs.json') -Raw | ConvertFrom-Json

function Get-FreePort {
  $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
  $l.Start()
  try { return ([System.Net.IPEndPoint]$l.LocalEndpoint).Port } finally { $l.Stop() }
}

function Start-Serve([string[]]$Extra) {
  # A foreground serve_local child (not -Detach), so the test can always stop it.
  $port = Get-FreePort
  $psi = [System.Diagnostics.ProcessStartInfo]::new($pwshExe)
  foreach ($a in @('-NoProfile', '-File', $serveScript, '-Root', $root, '-Path', 'DemoIQ-Config-Builder.html',
      '-Port', "$port", '-TimeoutMinutes', '5') + $Extra) { $psi.ArgumentList.Add([string]$a) }
  $psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
  $psi.WorkingDirectory = $work
  $p = [System.Diagnostics.Process]::Start($psi)
  $srv = [pscustomobject]@{ Process = $p; Port = $port; Base = "http://127.0.0.1:$port"
    Out = $p.StandardOutput.ReadToEndAsync(); Err = $p.StandardError.ReadToEndAsync(); Up = $false }
  $deadline = (Get-Date).AddSeconds(30)
  while (-not $srv.Up -and -not $p.HasExited -and (Get-Date) -lt $deadline) {
    try {
      $null = Invoke-WebRequest -Uri "$($srv.Base)/" -Method Head -TimeoutSec 2 -SkipHttpErrorCheck -NoProxy
      $srv.Up = $true
    } catch { Start-Sleep -Milliseconds 200 }
  }
  return $srv
}

function Stop-Serve($Srv) {
  # Kill the child, collect everything it printed and remove the state file it could not.
  if (-not $Srv) { return '' }
  try { if (-not $Srv.Process.HasExited) { $Srv.Process.Kill($true) } } catch {}
  try { $null = $Srv.Process.WaitForExit(10000) } catch {}
  $text = ''
  try { if ($Srv.Out.Wait(5000)) { $text += $Srv.Out.Result } } catch {}
  try { if ($Srv.Err.Wait(2000)) { $text += $Srv.Err.Result } } catch {}
  $state = Join-Path $HOME ".copilot/DemoIQ-Output-Scout/serve/$($Srv.Port).json"
  try {
    if ((Test-Path -LiteralPath $state) -and ((Get-Content -LiteralPath $state -Raw | ConvertFrom-Json).pid -eq $Srv.Process.Id)) {
      Remove-Item -LiteralPath $state -Force
    }
  } catch {}
  return $text
}

function Invoke-Serve($Srv, [string]$Method, [string]$PathAndQuery, [string]$Token, $Body) {
  # Status 0 = the connection itself failed (no HTTP response).
  $h = @{}; if ($Token) { $h['X-Save-Token'] = $Token }
  $req = @{ Uri = $Srv.Base + $PathAndQuery; Method = $Method; Headers = $h; SkipHttpErrorCheck = $true; TimeoutSec = 20; NoProxy = $true }
  if ($null -ne $Body) {
    # The leading comma keeps a byte[] whole (an if-statement would enumerate it).
    $req.Body = if ($Body -is [byte[]]) { , $Body } else { , [System.Text.UTF8Encoding]::new($false).GetBytes([string]$Body) }
    $req.ContentType = 'application/json; charset=utf-8'
  }
  try {
    $r = Invoke-WebRequest @req
    $text = if ($r.Content -is [byte[]]) { [System.Text.Encoding]::UTF8.GetString($r.Content) } else { [string]$r.Content }
    $json = $null; try { $json = ConvertFrom-Json -InputObject $text } catch {}
    return [pscustomobject]@{ Status = [int]$r.StatusCode; Json = $json; Text = $text }
  } catch { return [pscustomobject]@{ Status = 0; Json = $null; Text = $_.Exception.Message } }
}

function Get-LibraryFiles { @(Get-ChildItem -LiteralPath $lib -File -Force -ErrorAction SilentlyContinue) }
function Q([string]$s) { [System.Uri]::EscapeDataString($s) }
function Get-OccurrenceCount([string]$Text, [string]$Needle) { [regex]::Matches([string]$Text, [regex]::Escape($Needle)).Count }
function Write-Json([string]$Path, $Obj) { Set-Content -LiteralPath $Path -Value ($Obj | ConvertTo-Json -Depth 8) -Encoding UTF8 }
function Read-Json([string]$Path) { ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($Path)) }
function New-Library([string]$Name) { (New-Item -ItemType Directory -Force (Join-Path $work $Name)).FullName }
function Test-Absent($Json, [string]$Key) { $null -eq $Json -or $null -eq $Json.PSObject.Properties[$Key] }
$Owner600 = [System.IO.UnixFileMode]'UserRead, UserWrite'
$Owner700 = [System.IO.UnixFileMode]'UserRead, UserWrite, UserExecute'
$serveDir = Join-Path (Join-Path (Join-Path $HOME '.copilot') 'DemoIQ-Output-Scout') 'serve'

function New-DraftConfig {
  # As draft_config.ps1 writes one: the project's story, the deployment's app, the five
  # demo users without passwords, and the draft marker.
  $d = [ordered]@{ demoiqDraft = $true; adminUpn = $prefs.adminUpn; adminPwd = ''; existingAppId = $prefs.existingAppId
    existingCertThumbprint = ''; companyName = 'North Works'; customScenarioText = "Title: Pump B Maintenance at North Works`nDescription: Pump B vibration." }
  for ($i = 1; $i -le 5; $i++) { $d["u${i}upn"] = $prefs.users[$i - 1].upn; $d["u${i}pwd"] = '' }
  return $d
}

# The Config Builder's config shape (buildConfigData), from the fixture, with the two
# upstream credential fields deliberately filled: the server must blank them.
$cfg = [ordered]@{
  adminUpn = $prefs.adminUpn; adminPwd = $prefs.adminPwd
  existingAppId = $prefs.existingAppId; existingCertThumbprint = $prefs.existingCertThumbprint
  speechResourceId = $Speech; demoLanguage = $prefs.demoLanguage; speechSpeedRate = 1.2
  streamUrl = ''; streamKey = ''; industry = $prefs.industry
  companyName = "N$([char]0x00F6)rthwind Tr$([char]0x00E5)ders"
  rossId = ''; opportunityId = ''; tpid = ''
  customScenarioText = "Title: Pump A outage`nDescription: Pump A trips at North Works."
  scenarioIndex = $prefs.scenarioIndex; scenarioId = $null
}
for ($i = 1; $i -le 5; $i++) {
  $u = $prefs.users[$i - 1]
  $cfg["u${i}upn"] = $u.upn; $cfg["u${i}pwd"] = $u.pwd; $cfg["u${i}persona"] = ''; $cfg["u${i}personaCustom"] = ''
}
$cfg.aopSurfaces = @(); $cfg.aopAgents = @(); $cfg.contentTypes = @($prefs.contentTypes)
$cfg.options = [ordered]@{ wordDocs = 2; pptDecks = 2; excelWorkbooks = 2; excelTables = 2; emailThreaded = $true; meetingRecurring = $false }
$cfg.describeInChat = $false
$body = $cfg | ConvertTo-Json -Depth 8
# The same completed config carrying a stray draft marker: a save must never keep it.
$marked = [ordered]@{ demoiqDraft = $true }; foreach ($k in $cfg.Keys) { $marked[$k] = $cfg[$k] }
$bodyWithMark = $marked | ConvertTo-Json -Depth 8
$userPwd = [string]$prefs.users[0].pwd
$envelope = '{"v":"cdx-enc-v1","salt":[1,2,3],"iv":[4,5,6],"data":[7,8,9]}'

$srv = $null; $srv2 = $null; $srvB = $null; $srvC = $null; $srvD = $null; $srvE = $null; $detachedPid = $null
$out = ''; $out2 = ''
try {
  # ---------------------------------------------------------------- the server
  $srv = Start-Serve @('-AllowSave', '-SaveDir', $lib, '-SaveToken', $Token,
    '-PrefillAppId', $prefs.existingAppId, '-PrefillSpeechId', $Speech, '-PrefillConfig', $DraftName)
  Check 'serve_local answers on loopback (foreground child, -AllowSave)' $srv.Up
  if (-not $srv.Up) { throw "serve_local did not start: $(Stop-Serve $srv)" }
  if (-not $IsWindows) {
    Check 'the configs library it created is owner-only (0700)' ([System.IO.File]::GetUnixFileMode($lib) -eq $Owner700)
    $stateFile = Join-Path $serveDir "$($srv.Port).json"
    Check 'the serve folder holding the token is owner-only (0700)' ((Test-Path -LiteralPath $serveDir) -and [System.IO.File]::GetUnixFileMode($serveDir) -eq $Owner700)
    Check '... and the state file with the token is owner-only (0600)' ((Test-Path -LiteralPath $stateFile) -and
      [System.IO.File]::GetUnixFileMode($stateFile) -eq $Owner600 -and (Read-Json $stateFile).saveToken -ceq $Token)
  }
  $page = Invoke-Serve $srv 'GET' '/DemoIQ-Config-Builder.html'
  Check 'still serves the Config Builder page' ($page.Status -eq 200 -and $page.Text -match 'demoiq-skill-version')

  # ---------------------------------------------------------------- POST /save
  $first = Join-Path $lib 'DemoIQ_config - Fixture.json'
  $r = Invoke-Serve $srv 'POST' "/save?name=$(Q 'DemoIQ_config - Fixture.json')" $Token $body
  Check 'POST /save with the token: 200, ok' ($r.Status -eq 200 -and $r.Json.ok -eq $true)
  Check '... answers { ok, name, path } for the file in the library' ($r.Json.name -eq 'DemoIQ_config - Fixture.json' -and
    [System.IO.Path]::GetFullPath([string]$r.Json.path) -eq [System.IO.Path]::GetFullPath($first) -and (Test-Path -LiteralPath $first))
  if (-not $IsWindows) {
    Check '... the saved config is owner-only (0600)' ((Test-Path -LiteralPath $first) -and [System.IO.File]::GetUnixFileMode($first) -eq [System.IO.UnixFileMode]'UserRead, UserWrite')
  }
  $savedText = if (Test-Path -LiteralPath $first) { [System.IO.File]::ReadAllText($first) } else { '{}' }
  $s = ConvertFrom-Json -InputObject $savedText
  Check '... plain JSON (no encryption envelope)' ($null -eq $s.v -and $s.adminUpn -eq $prefs.adminUpn)
  Check '... adminPwd and existingCertThumbprint saved as empty strings' ($s.adminPwd -is [string] -and $s.adminPwd -eq '' -and
    $s.existingCertThumbprint -is [string] -and $s.existingCertThumbprint -eq '')
  Check '... neither value appears anywhere in the file' ($savedText.IndexOf([string]$prefs.adminPwd) -lt 0 -and $savedText.IndexOf([string]$prefs.existingCertThumbprint) -lt 0)
  Check '... every field kept, in order' ((@($s.PSObject.Properties.Name) -join ',') -eq (@($cfg.Keys) -join ','))
  $usersIntact = $true
  for ($i = 1; $i -le 5; $i++) { if ($s."u${i}upn" -cne $cfg["u${i}upn"] -or $s."u${i}pwd" -cne $cfg["u${i}pwd"]) { $usersIntact = $false } }
  Check '... demo users intact (UPNs and passwords)' $usersIntact
  Check '... app, speech, company (non-ASCII) and scenario text intact' ($s.existingAppId -eq $prefs.existingAppId -and $s.speechResourceId -ceq $Speech -and
    $s.companyName -ceq $cfg.companyName -and $s.customScenarioText -ceq $cfg.customScenarioText)
  Check '... numbers, booleans, arrays and options intact' ($s.speechSpeedRate -eq 1.2 -and $s.scenarioIndex -eq $prefs.scenarioIndex -and $s.describeInChat -eq $false -and
    (@($s.contentTypes) -join ',') -eq (@($prefs.contentTypes) -join ',') -and @($s.aopSurfaces).Count -eq 0 -and
    $s.options.wordDocs -eq 2 -and $s.options.emailThreaded -eq $true -and $s.options.meetingRecurring -eq $false)
  Check '... no temporary file left behind' (@(Get-LibraryFiles | Where-Object { $_.Name -like '*.tmp' }).Count -eq 0)

  $hash = (Get-FileHash -LiteralPath $first).Hash
  $cfg2 = [ordered]@{}; foreach ($k in $cfg.Keys) { $cfg2[$k] = $cfg[$k] }; $cfg2.companyName = 'Second save'
  $r2 = Invoke-Serve $srv 'POST' "/save?name=$(Q 'DemoIQ_config - Fixture.json')" $Token ($cfg2 | ConvertTo-Json -Depth 8)
  $second = Join-Path $lib 'DemoIQ_config - Fixture_1.json'
  Check 'the same name again: a new suffixed file' ($r2.Status -eq 200 -and $r2.Json.name -eq 'DemoIQ_config - Fixture_1.json' -and
    (Test-Path -LiteralPath $second) -and (ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($second))).companyName -eq 'Second save')
  Check '... and the first file is untouched' ((Get-FileHash -LiteralPath $first).Hash -eq $hash)
  $r3 = Invoke-Serve $srv 'POST' "/save?name=$(Q '../../escape.json')" $Token $body
  Check 'a path in the name is sanitized into the library (no traversal)' ($r3.Status -eq 200 -and $r3.Json.name -eq 'DemoIQ_config_escape.json' -and
    (Test-Path -LiteralPath (Join-Path $lib 'DemoIQ_config_escape.json')) -and -not (Test-Path -LiteralPath (Join-Path $work 'escape.json')))
  $r4 = Invoke-Serve $srv 'POST' "/save?name=$(Q 'DemoIQ_config_noapp.json')" $Token '{"adminUpn":"admin@fixturetest.onmicrosoft.com","existingAppId":""}'
  $noApp = Join-Path $lib 'DemoIQ_config_noapp.json'
  Check 'an empty existingAppId is accepted; the credential fields are added empty' ($r4.Status -eq 200 -and (Test-Path -LiteralPath $noApp) -and
    (ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($noApp))).existingCertThumbprint -eq '')

  # ---------------------------------------------------------------- refusals
  $count = (Get-LibraryFiles).Count
  Check 'POST /save without a token: 403' ((Invoke-Serve $srv 'POST' '/save' '' $body).Status -eq 403)
  Check 'POST /save with a wrong token: 403' ((Invoke-Serve $srv 'POST' '/save' 'wrong-token' $body).Status -eq 403)
  $e = Invoke-Serve $srv 'POST' '/save' $Token $envelope
  Check 'an encrypted cdx-enc-v1 envelope: 422 with the convert hint' ($e.Status -eq 422 -and $e.Json.ok -eq $false -and $e.Json.error -match 'convert_config\.ps1')
  Check 'a JSON array: 422' ((Invoke-Serve $srv 'POST' '/save' $Token '[{"adminUpn":"admin@fixturetest.onmicrosoft.com"}]').Status -eq 422)
  Check 'a JSON string: 422' ((Invoke-Serve $srv 'POST' '/save' $Token '"admin@fixturetest.onmicrosoft.com"').Status -eq 422)
  Check 'invalid JSON: 422' ((Invoke-Serve $srv 'POST' '/save' $Token '{"adminUpn":').Status -eq 422)
  $m = Invoke-Serve $srv 'POST' '/save' $Token '{"existingAppId":"00000000-0000-0000-0000-000000000001","u1upn":"a@b"}'
  Check 'no adminUpn: 422 naming adminUpn' ($m.Status -eq 422 -and $m.Json.error -match 'adminUpn')
  Check "an adminUpn without '@': 422" ((Invoke-Serve $srv 'POST' '/save' $Token '{"adminUpn":"admin"}').Status -eq 422)
  Check 'a non-string adminUpn: 422' ((Invoke-Serve $srv 'POST' '/save' $Token '{"adminUpn":42}').Status -eq 422)
  $a = Invoke-Serve $srv 'POST' '/save' $Token '{"adminUpn":"a@b.onmicrosoft.com","existingAppId":"not-a-guid"}'
  Check 'an existingAppId that is not a GUID: 422 naming existingAppId' ($a.Status -eq 422 -and $a.Json.error -match 'existingAppId')
  $u = Invoke-Serve $srv 'POST' '/save' $Token '{"adminUpn":"a@b.onmicrosoft.com","u2upn":5}'
  Check 'a non-string demo-user UPN: 422 naming it' ($u.Status -eq 422 -and $u.Json.error -match 'u2upn')
  Check 'an empty body: 400' ((Invoke-Serve $srv 'POST' '/save' $Token ([byte[]]@())).Status -eq 400)
  $big = '{"adminUpn":"a@b.onmicrosoft.com","pad":"' + ('x' * 1100000) + '"}'
  $o = Invoke-Serve $srv 'POST' '/save' $Token $big
  Check 'a body over 1 MB is refused (413, or the connection is cut)' ($o.Status -eq 413 -or $o.Status -eq 0)
  Check 'PUT /save: 405' ((Invoke-Serve $srv 'PUT' '/save' $Token $body).Status -eq 405)
  Check 'nothing was written by any refused request' ((Get-LibraryFiles).Count -eq $count)

  # ---------------------------------------------------------------- GET /config
  $g = Invoke-Serve $srv 'GET' "/config?name=$(Q 'DemoIQ_config - Fixture.json')" $Token
  Check 'GET /config with the token: the saved config' ($g.Status -eq 200 -and $g.Json.adminUpn -eq $prefs.adminUpn -and
    $g.Json.u1pwd -ceq $userPwd -and $g.Json.companyName -ceq $cfg.companyName -and $g.Json.adminPwd -eq '')
  Check '... token in the query works too' ((Invoke-Serve $srv 'GET' "/config?name=$(Q 'DemoIQ_config - Fixture.json')&token=$(Q $Token)").Status -eq 200)
  Check '... without the token: 403' ((Invoke-Serve $srv 'GET' "/config?name=$(Q 'DemoIQ_config - Fixture.json')").Status -eq 403)
  Check '... with a wrong token: 403' ((Invoke-Serve $srv 'GET' "/config?name=$(Q 'DemoIQ_config - Fixture.json')" 'wrong-token').Status -eq 403)
  foreach ($bad in @('../x.json', '../DemoIQ_config.json', 'sub/DemoIQ_config.json', 'sub\DemoIQ_config.json', 'other.json', 'DemoIQ_config', '')) {
    Check "... name '$bad': 400" ((Invoke-Serve $srv 'GET' "/config?name=$(Q $bad)" $Token).Status -eq 400)
  }
  Check '... an unknown name: 404' ((Invoke-Serve $srv 'GET' "/config?name=$(Q 'DemoIQ_config_missing.json')" $Token).Status -eq 404)
  Set-Content -LiteralPath (Join-Path $lib 'DemoIQ_config_old.json') -Value $envelope -Encoding UTF8
  $x = Invoke-Serve $srv 'GET' "/config?name=$(Q 'DemoIQ_config_old.json')" $Token
  Check '... an encrypted config: 422 with the convert hint' ($x.Status -eq 422 -and $x.Json.error -match 'convert_config\.ps1')
  # A drafted config, as the agent writes one from the repository scenario (no passwords
  # yet) - written AFTER this server started, so it is not the draft this server replaces.
  $draft = [ordered]@{ demoiqDraft = $true; adminUpn = $prefs.adminUpn; adminPwd = 'drafted-admin-secret'; existingAppId = $prefs.existingAppId
    existingCertThumbprint = 'ABCDEF'; companyName = 'North Works'; customScenarioText = "Title: Pump B`nDescription: Pump B vibration." }
  for ($i = 1; $i -le 5; $i++) { $draft["u${i}upn"] = $prefs.users[$i - 1].upn; $draft["u${i}pwd"] = '' }
  Set-Content -LiteralPath (Join-Path $lib $DraftName) -Value ($draft | ConvertTo-Json) -Encoding UTF8
  $dr = Invoke-Serve $srv 'GET' "/config?name=$(Q $DraftName)" $Token
  Check 'GET /config returns the drafted config the Builder opens prefilled' ($dr.Status -eq 200 -and $dr.Json.companyName -eq 'North Works' -and
    $dr.Json.customScenarioText -ceq $draft.customScenarioText -and $dr.Json.u5upn -eq $prefs.users[4].upn)
  Check '... with its admin password and thumbprint returned empty' ($dr.Json.adminPwd -eq '' -and $dr.Json.existingCertThumbprint -eq '' -and $dr.Text -notmatch 'drafted-admin-secret')
  Check '... and its demoiqDraft marker kept for the Builder' ($dr.Json.demoiqDraft -eq $true)
  Check 'POST /config: 405' ((Invoke-Serve $srv 'POST' "/config?name=$(Q $DraftName)" $Token $body).Status -eq 405)
  $rm = Invoke-Serve $srv 'POST' "/save?name=$(Q 'DemoIQ_config_marked.json')" $Token $bodyWithMark
  $markedFile = Join-Path $lib 'DemoIQ_config_marked.json'
  Check 'a posted config carrying demoiqDraft is saved without it' ($rm.Status -eq 200 -and (Test-Path -LiteralPath $markedFile) -and
    (Test-Absent (Read-Json $markedFile) 'demoiqDraft') -and (Read-Json $markedFile).u1pwd -ceq $userPwd)
  Check '... and a draft that was not this server''s draft at startup is never deleted' ((Test-Absent $rm.Json 'replacedDraft') -and
    (Test-Absent $rm.Json 'replacedDraftError') -and (Test-Path -LiteralPath (Join-Path $lib $DraftName)))

  $out = Stop-Serve $srv; $srv = $null
  $url = @($out -split "`r?`n" | Where-Object { $_ -like '`[SERVE-URL`]*' }) | Select-Object -First 1
  Check '[SERVE-URL] carries appId= (URL-encoded)' ($url -and $url.Contains('appId=' + (Q $prefs.existingAppId)))
  Check '[SERVE-URL] carries speechId= (URL-encoded)' ($url -and $url.Contains('speechId=' + (Q $Speech)))
  Check '[SERVE-URL] carries config= (URL-encoded drafted config name)' ($url -and $url.Contains('config=' + (Q $DraftName)))
  Check '[SERVE-URL] keeps the save token in the #fragment' ($url -and $url.EndsWith('#saveToken=' + $Token))
  Check '[SERVE-URL] has no certificate parameter' ($url -and $url -notmatch '[?&]cert=')
  Check 'a -PrefillConfig draft not yet in the library is named in a WARN' ($out -match [regex]::Escape("-PrefillConfig '$DraftName' is not in the configs library"))
  Check 'each save printed [SAVED] <path>' (([regex]::Matches($out, '(?m)^\[SAVED\] ')).Count -eq 5 -and $out.Contains("[SAVED] $first"))
  Check 'the server output carries no password' ($out.IndexOf([string]$prefs.adminPwd) -lt 0 -and $out.IndexOf($userPwd) -lt 0)
  Check 'the save token appears in stdout only once: in the [SERVE-URL] fragment' ((Get-OccurrenceCount $out $Token) -eq 1 -and
    $url -and (Get-OccurrenceCount $url "#saveToken=$Token") -eq 1)
  Check 'no draft replacement was logged by a server whose prefill was no draft' ($out -notmatch 'Replaced draft')

  # ---------------------------------------------------------------- without -AllowSave
  $srv2 = Start-Serve @('-PrefillConfig', $DraftName)
  Check 'serve_local without -AllowSave answers' $srv2.Up
  Check '... has no POST /save (405)' ((Invoke-Serve $srv2 'POST' '/save' $Token $body).Status -eq 405)
  Check '... has no GET /config (404)' ((Invoke-Serve $srv2 'GET' "/config?name=$(Q $DraftName)" $Token).Status -eq 404)
  $out2 = Stop-Serve $srv2; $srv2 = $null
  Check '... warns that -PrefillConfig needs -AllowSave' ($out2 -match '-PrefillConfig .* needs -AllowSave')
  Check '... and its [SERVE-URL] carries no save token' ($out2 -match '\[SERVE-URL\] \S+config=' -and $out2 -notmatch 'saveToken=')

  # ---------------------------------------------------------------- the Builder's side (static)
  $builderText = [System.IO.File]::ReadAllText((Join-Path $root 'DemoIQ-Config-Builder.html'))
  $buildFn = [regex]::Match($builderText, '(?s)function buildConfigData\(\)\{.*?\r?\n\}\r?\n').Value
  Check 'Builder: buildConfigData() never writes demoiqDraft, and Save posts exactly it' ($buildFn.Length -gt 1000 -and $buildFn -notmatch 'demoiqDraft' -and
    $builderText.Contains('const body=JSON.stringify(buildConfigData());'))
  Check 'Builder: a draft from GET /config (demoiqDraft === true) gets the review-and-passwords notice' ($builderText.Contains('j.demoiqDraft===true') -and
    $builderText.Contains("is a draft from your project\'s story: review it, type the five demo-user passwords"))
  Check 'Builder: the save notice reports replacedDraft and replacedDraftError' ($builderText.Contains('j.replacedDraft ?') -and $builderText.Contains('j.replacedDraftError'))

  # ---------------------------------------------------------------- a draft is replaced
  $libB = New-Library 'lib-draft'
  $draftB = Join-Path $libB 'DemoIQ_config_plant.json'
  Write-Json $draftB (New-DraftConfig)
  $otherDraft = Join-Path $libB 'DemoIQ_config_other.json'   # a draft this server was not started with
  Write-Json $otherDraft (New-DraftConfig)
  $srvB = Start-Serve @('-AllowSave', '-SaveDir', $libB, '-SaveToken', $Token, '-PrefillConfig', 'DemoIQ_config_plant.json')
  Check 'draft prefill: serve_local answers' $srvB.Up
  $gB = Invoke-Serve $srvB 'GET' '/config?name=DemoIQ_config_plant.json' $Token
  Check '... GET /config hands the Builder the draft with its marker' ($gB.Status -eq 200 -and $gB.Json.demoiqDraft -eq $true -and $gB.Json.companyName -eq 'North Works')
  Check '... a refused save never deletes the draft' ((Invoke-Serve $srvB 'POST' '/save' $Token '{"adminUpn":"admin"}').Status -eq 422 -and (Test-Path -LiteralPath $draftB))
  $saveName = 'DemoIQ_config - Pump B Maintenance at North Works - English (United States).json'
  $rB = Invoke-Serve $srvB 'POST' "/save?name=$(Q $saveName)" $Token $bodyWithMark
  $savedB = Join-Path $libB $saveName
  Check '... saving the completed config: 200 { ok, name, path, savedTo, replacedDraft }' ($rB.Status -eq 200 -and $rB.Json.ok -eq $true -and
    (@($rB.Json.PSObject.Properties.Name) -join ',') -eq 'ok,name,path,savedTo,replacedDraft' -and $rB.Json.name -eq $saveName -and
    $rB.Json.replacedDraft -eq 'DemoIQ_config_plant.json' -and [System.IO.Path]::GetFullPath([string]$rB.Json.path) -eq [System.IO.Path]::GetFullPath($savedB))
  Check '... the draft is deleted' (-not (Test-Path -LiteralPath $draftB))
  $sB = if (Test-Path -LiteralPath $savedB) { Read-Json $savedB } else { $null }
  Check '... the new file is no draft and keeps the typed passwords' ($sB -and (Test-Absent $sB 'demoiqDraft') -and $sB.u5pwd -ceq [string]$prefs.users[4].pwd -and $sB.adminUpn -eq $prefs.adminUpn)
  if (-not $IsWindows) { Check '... and is owner-only (0600)' ($sB -and [System.IO.File]::GetUnixFileMode($savedB) -eq $Owner600) }
  Check '... another draft in the library is never touched' (Test-Path -LiteralPath $otherDraft)
  $rB2 = Invoke-Serve $srvB 'POST' "/save?name=$(Q $saveName)" $Token $body
  Check 'a second save: ok as a new suffixed file, nothing more replaced' ($rB2.Status -eq 200 -and $rB2.Json.name -eq ($saveName -replace '\.json$', '_1.json') -and
    (Test-Absent $rB2.Json 'replacedDraft') -and (Test-Absent $rB2.Json 'replacedDraftError') -and (Test-Path -LiteralPath $savedB) -and (Test-Path -LiteralPath $otherDraft))
  $outB = Stop-Serve $srvB; $srvB = $null
  Check '... one [SERVE] line logged the replacement' ((Get-OccurrenceCount $outB '[SERVE] Replaced draft DemoIQ_config_plant.json') -eq 1)

  # ---------------------------------------------------------------- a non-draft prefill
  $libC = New-Library 'lib-saved'
  $savedC = Join-Path $libC 'DemoIQ_config_saved.json'
  Write-Json $savedC $cfg
  $hashC = (Get-FileHash -LiteralPath $savedC).Hash
  $srvC = Start-Serve @('-AllowSave', '-SaveDir', $libC, '-SaveToken', $Token, '-PrefillConfig', 'DemoIQ_config_saved.json')
  $rC = Invoke-Serve $srvC 'POST' "/save?name=$(Q 'DemoIQ_config_saved.json')" $Token $body
  Check 'a non-draft -PrefillConfig is never touched: a new suffixed file, nothing replaced' ($rC.Status -eq 200 -and $rC.Json.name -eq 'DemoIQ_config_saved_1.json' -and
    (Test-Absent $rC.Json 'replacedDraft') -and (Test-Absent $rC.Json 'replacedDraftError') -and (Get-FileHash -LiteralPath $savedC).Hash -eq $hashC)
  $outC = Stop-Serve $srvC; $srvC = $null
  Check '... and no replacement was logged' ($outC -notmatch 'Replaced draft')

  # ---------------------------------------------------------------- a draft changed or removed after startup
  $libD = New-Library 'lib-changed'
  $draftD = Join-Path $libD 'DemoIQ_config_d.json'
  Write-Json $draftD (New-DraftConfig)
  $srvD = Start-Serve @('-AllowSave', '-SaveDir', $libD, '-SaveToken', $Token, '-PrefillConfig', 'DemoIQ_config_d.json')
  $changed = New-DraftConfig; $changed.demoiqDraft = 'true'   # a string is not the marker
  Write-Json $draftD $changed
  $rD1 = Invoke-Serve $srvD 'POST' "/save?name=$(Q 'DemoIQ_config - D.json')" $Token $body
  Check 'a draft changed to a non-draft after startup: save ok, nothing deleted' ($rD1.Status -eq 200 -and (Test-Absent $rD1.Json 'replacedDraft') -and
    (Test-Absent $rD1.Json 'replacedDraftError') -and (Test-Path -LiteralPath $draftD))
  Remove-Item -LiteralPath $draftD -Force
  $rD2 = Invoke-Serve $srvD 'POST' "/save?name=$(Q 'DemoIQ_config_d.json')" $Token $bodyWithMark
  Check 'a draft already removed: save ok (even under the draft''s free name), nothing deleted' ($rD2.Status -eq 200 -and $rD2.Json.name -eq 'DemoIQ_config_d.json' -and
    (Test-Absent $rD2.Json 'replacedDraft') -and (Test-Absent $rD2.Json 'replacedDraftError') -and (Test-Path -LiteralPath $draftD) -and (Test-Absent (Read-Json $draftD) 'demoiqDraft'))
  $outD = Stop-Serve $srvD; $srvD = $null
  Check '... and no replacement was logged' ($outD -notmatch 'Replaced draft')

  # ---------------------------------------------------------------- a draft that cannot be deleted
  if ($IsMacOS -or $IsWindows) {
    $libE = New-Library 'lib-locked'
    $draftE = Join-Path $libE 'DemoIQ_config_locked.json'
    Write-Json $draftE (New-DraftConfig)
    $srvE = Start-Serve @('-AllowSave', '-SaveDir', $libE, '-SaveToken', $Token, '-PrefillConfig', 'DemoIQ_config_locked.json')
    $lock = $null
    try {
      # macOS: the user-immutable flag; Windows: an open handle that allows reads, not deletes.
      if ($IsMacOS) { & /usr/bin/chflags uchg $draftE } else { $lock = [System.IO.File]::Open($draftE, 'Open', 'Read', 'Read') }
      $rE = Invoke-Serve $srvE 'POST' "/save?name=$(Q 'DemoIQ_config - E.json')" $Token $body
      Check 'a draft that cannot be deleted: the save still succeeds, with replacedDraftError' ($rE.Status -eq 200 -and $rE.Json.ok -eq $true -and
        $rE.Json.replacedDraftError -match 'DemoIQ_config_locked\.json' -and (Test-Absent $rE.Json 'replacedDraft') -and
        (Test-Path -LiteralPath $draftE) -and (Test-Path -LiteralPath (Join-Path $libE 'DemoIQ_config - E.json')))
    } finally {
      if ($IsMacOS) { & /usr/bin/chflags nouchg $draftE } elseif ($lock) { $lock.Dispose() }
    }
    $outE = Stop-Serve $srvE; $srvE = $null
    Check '... and a WARN line names the draft' ($outE -match 'WARN: could not delete the replaced draft DemoIQ_config_locked\.json')
  } else { Write-Host '  SKIP undeletable-draft check (needs chflags on macOS or a share lock on Windows)' }

  # ---------------------------------------------------------------- -Detach: token at rest
  if (-not $IsWindows) {
    # The detached child deletes the token bootstrap file at startup, so a stand-in node
    # first on PATH (the parent starts the child through detach.js) records the file's
    # mode and its folder's BEFORE the child exists, then runs the real node. (A stand-in
    # pwsh cannot work: pwsh puts $PSHOME first on PATH.)
    $realNode = (Get-Command node -CommandType Application | Select-Object -First 1).Source
    $fakeBin = New-Library 'fakebin'
    $wrapper = Join-Path $fakeBin 'node'
    $modes = Join-Path $work 'detach-modes.txt'
    $wrapperText = @'
#!/bin/sh
prev=''
for a in "$@"; do
  if [ "$prev" = '-SaveTokenFile' ]; then
    { ls -ld "$a" | cut -c1-10; ls -ld "$(dirname "$a")" | cut -c1-10; } > "$DEMOIQ_TEST_MODES" 2>&1
  fi
  prev=$a
done
exec "$DEMOIQ_TEST_REALNODE" "$@"
'@
    [System.IO.File]::WriteAllText($wrapper, ($wrapperText -replace "`r`n", "`n"))
    [System.IO.File]::SetUnixFileMode($wrapper, $Owner700)
    $libF = New-Library 'lib-detach'
    $draftF = Join-Path $libF 'DemoIQ_config_detach.json'
    Write-Json $draftF (New-DraftConfig)
    $portF = Get-FreePort
    $psi = [System.Diagnostics.ProcessStartInfo]::new($pwshExe)
    foreach ($a in @('-NoProfile', '-File', $serveScript, '-Detach', '-Root', $root, '-Path', 'DemoIQ-Config-Builder.html', '-Port', "$portF",
        '-TimeoutMinutes', '1', '-AllowSave', '-SaveDir', $libF, '-SaveToken', $Token, '-PrefillConfig', 'DemoIQ_config_detach.json')) { $psi.ArgumentList.Add([string]$a) }
    $psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.WorkingDirectory = $work
    $psi.Environment['PATH'] = $fakeBin + [System.IO.Path]::PathSeparator + $env:PATH
    $psi.Environment['DEMOIQ_TEST_MODES'] = $modes
    $psi.Environment['DEMOIQ_TEST_REALNODE'] = $realNode
    $parent = [System.Diagnostics.Process]::Start($psi)
    $pOut = $parent.StandardOutput.ReadToEndAsync(); $pErr = $parent.StandardError.ReadToEndAsync()
    $null = $parent.WaitForExit(60000)
    $outF = $pOut.Result + $pErr.Result
    if ($outF -match '\[SERVE-PID\] (\d+)') { $detachedPid = [int]$Matches[1] }
    Check '-Detach: the detached server came up' ($parent.ExitCode -eq 0 -and $detachedPid)
    $m = @(Get-Content -LiteralPath $modes -ErrorAction SilentlyContinue)
    Check '... its token bootstrap file was owner-only (0600) in an owner-only serve folder (0700)' ($m.Count -eq 2 -and $m[0] -ceq '-rw-------' -and $m[1] -ceq 'drwx------')
    Check '... and the child deleted that file at startup' (-not (Test-Path -LiteralPath (Join-Path $serveDir "serve_$portF.token")))
    $stateF = Join-Path $serveDir "$portF.json"
    Check '... the child''s state file is owner-only (0600)' ((Test-Path -LiteralPath $stateF) -and [System.IO.File]::GetUnixFileMode($stateF) -eq $Owner600 -and (Read-Json $stateF).pid -eq $detachedPid)
    $srvF = [pscustomobject]@{ Base = "http://127.0.0.1:$portF" }
    $rF = Invoke-Serve $srvF 'POST' "/save?name=$(Q 'DemoIQ_config - Detached.json')" $Token $body
    Check '... the detached child knows its draft: the save replaces it' ($rF.Status -eq 200 -and $rF.Json.replacedDraft -eq 'DemoIQ_config_detach.json' -and -not (Test-Path -LiteralPath $draftF))
    $urlF = @($outF -split "`r?`n" | Where-Object { $_ -like '`[SERVE-URL`]*' }) | Select-Object -First 1
    Check '... the token appears in the parent''s stdout only once: in the [SERVE-URL] fragment' ((Get-OccurrenceCount $outF $Token) -eq 1 -and
      $urlF -and $urlF.EndsWith("#saveToken=$Token"))
    $childLogs = (@("serve_$portF.log", "serve_$portF.stdout.log") | ForEach-Object {
        $f = Join-Path $serveDir $_; if (Test-Path -LiteralPath $f) { [System.IO.File]::ReadAllText($f) } }) -join "`n"
    Check '... and never in the child''s logs' ($childLogs -match '\[SERVE\]' -and (Get-OccurrenceCount $childLogs $Token) -eq 0)
  } else { Write-Host '  SKIP -Detach token-at-rest checks (POSIX file modes)' }
}
finally {
  foreach ($s in @($srv, $srv2, $srvB, $srvC, $srvD, $srvE)) { if ($s) { $null = Stop-Serve $s } }
  if ($detachedPid) {
    Stop-Process -Id $detachedPid -Force -ErrorAction SilentlyContinue
    $stateF = Join-Path $serveDir "$portF.json"
    try { if ((Test-Path -LiteralPath $stateF) -and (Read-Json $stateF).pid -eq $detachedPid) { Remove-Item -LiteralPath $stateF -Force } } catch {}
  }
  Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

if ($script:fails) { Write-Host "test_builder_save: $($script:fails) FAILURE(S)" -ForegroundColor Red; exit 1 }
Write-Host 'test_builder_save: all passed' -ForegroundColor Green
exit 0

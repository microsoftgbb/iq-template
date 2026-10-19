# test_platform.ps1 - the GitHub Copilot adaptation's cross-platform layer, offline.
#
# Covers scripts/platform.ps1, session_store.ps1 (the named session secret that carries
# the administrator's sign-in), convert_config.ps1 (the one-time conversion of an
# encrypted upstream config), generate.js plain configs, find_config.ps1 and
# repoint_config.ps1 against saved deployment receipts, detach.js-style detached
# processes, stage_deliverables.ps1's Git guard, launch_seeder.ps1's fail-fast and
# cleanup_local.ps1.
#
# Nothing is contacted: receipts are a saved `deploy.py status --json` file. The suite
# re-launches itself under a throwaway HOME and session folder (macOS/Linux) so the real
# DemoIQ workspace and sign-in are never touched; every workspace-writing check also
# points at its own temp folder, so the same checks are safe on Windows.
$ErrorActionPreference = 'Stop'

if ($env:DEMOIQ_TEST_ISOLATED -ne '1' -and -not $IsWindows) {
  $iso = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-plat-iso-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
  New-Item -ItemType Directory -Force (Join-Path $iso 'home') | Out-Null
  $env:HOME = Join-Path $iso 'home'; $env:DEMOIQ_TEST_ISOLATED = '1'
  $env:DEMOIQ_SESSION_DIR = Join-Path $iso 'session'
  & (Get-Process -Id $PID).Path -NoProfile -File $PSCommandPath
  $code = $LASTEXITCODE
  Remove-Item -LiteralPath $iso -Recurse -Force -ErrorAction SilentlyContinue
  exit $code
}
if ($IsWindows -and -not $env:DEMOIQ_SESSION_DIR) {
  $env:DEMOIQ_SESSION_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-plat-session-' + [guid]::NewGuid().ToString('N'))
}

$script:fails = 0
function Check([string]$name, $cond) {
  if ($cond) { Write-Host "  PASS $name" -ForegroundColor Green } else { Write-Host "  FAIL $name" -ForegroundColor Red; $script:fails++ }
}
function JsonOf($raw) { (@($raw | Where-Object { "$_".Trim().StartsWith('{') }) | Select-Object -Last 1) | ConvertFrom-Json }
function Mode([string]$p) { [System.IO.File]::GetUnixFileMode($p).ToString() }
$ownerOnly = '^(UserWrite, UserRead|UserRead, UserWrite)$'

$root = Split-Path $PSScriptRoot -Parent
$S = Join-Path $root 'scripts'
$pwshExe = (Get-Process -Id $PID).Path
$unix = -not $IsWindows
$work = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-plat-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null

function Invoke-Child([string]$file, [string[]]$a, [hashtable]$envs, [switch]$NoStdin) {
  $saved = @{}
  foreach ($k in @($envs.Keys)) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $envs[$k]) }
  try {
    if ($NoStdin) { $raw = '' | & $pwshExe -NoProfile -File $file @a 2>&1 }
    else { $raw = & $pwshExe -NoProfile -File $file @a 2>&1 }
    return @{ code = $LASTEXITCODE; raw = @($raw); json = (JsonOf $raw) }
  } finally {
    foreach ($k in @($saved.Keys)) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
  }
}

try {
  . (Join-Path $S 'session_store.ps1')
  Check 'workspace resolves under HOME (~/.copilot/DemoIQ-Output-Scout)' ((Get-DemoIQWorkspace) -eq (Join-Path (Join-Path $HOME '.copilot') 'DemoIQ-Output-Scout'))

  # ------------------------------------------------------------ session store
  Write-Host '-- session store (the administrator sign-in) --'
  $secret = '{"refreshToken":"rt-' + [guid]::NewGuid().ToString('N') + '","upn":"admin@contoso.onmicrosoft.com"}'
  $sfile = Save-DemoIQSessionSecret -Name 'admin-session' -Value $secret
  Check 'secret stored under its name' ($sfile -and (Test-Path -LiteralPath $sfile) -and $sfile -eq (Get-DemoIQSessionSecretPath -Name 'admin-session'))
  $stored = [System.IO.File]::ReadAllText($sfile)
  $hex = -join ([System.Text.Encoding]::Unicode.GetBytes($secret) | ForEach-Object { $_.ToString('x2') })
  Check 'stored form contains neither the plaintext nor its UTF-16 hex' ($stored.IndexOf($secret) -lt 0 -and $stored.ToLowerInvariant().IndexOf($hex) -lt 0)
  if ($unix) {
    Check 'secret file is owner-only (0600)' ((Mode $sfile) -match $ownerOnly)
    Check 'secret file is an AES-256-GCM envelope' (($stored | ConvertFrom-Json).v -eq 'demoiq-session-v1')
    Check 'key file is owner-only (0600)' ((Mode (Get-DemoIQSessionKeyPath)) -match $ownerOnly)
  }
  Set-Content -LiteralPath (Join-Path $work 'secret.ps1') -Value ". '$(Join-Path $S 'session_store.ps1')'; if ((Read-DemoIQSessionSecret -Name 'admin-session') -eq `$env:EXPECT_SECRET) { 'SAME' } else { 'DIFFERENT' }" -Encoding UTF8
  $child = Invoke-Child -file (Join-Path $work 'secret.ps1') -a @() -envs @{ EXPECT_SECRET = $secret }
  Check 'a FRESH process reads the same secret' (($child.raw -join '') -match 'SAME')
  if ($unix) {
    $env2 = $stored | ConvertFrom-Json; $bytes = [Convert]::FromBase64String($env2.data); $bytes[0] = $bytes[0] -bxor 0xFF
    $env2.data = [Convert]::ToBase64String($bytes)
    [System.IO.File]::WriteAllText($sfile, ($env2 | ConvertTo-Json -Compress))
    Check 'a tampered envelope is rejected' (-not (Read-DemoIQSessionSecret -Name 'admin-session'))
    [System.IO.File]::WriteAllText($sfile, $stored)
  }
  Check 'Test-DemoIQSessionSecret sees it' (Test-DemoIQSessionSecret -Name 'admin-session')
  [System.IO.File]::SetLastWriteTime($sfile, (Get-Date).AddHours(-13))
  Check 'an expired (>12h) secret is reported missing and deleted' (-not (Test-DemoIQSessionSecret -Name 'admin-session') -and -not (Test-Path -LiteralPath $sfile))
  $null = Save-DemoIQSessionSecret -Name 'admin-session' -Value $secret
  [System.IO.File]::SetLastWriteTime($sfile, (Get-Date).AddHours(-13))
  Check 'reading an expired secret returns nothing and deletes it' (-not (Read-DemoIQSessionSecret -Name 'admin-session') -and -not (Test-Path -LiteralPath $sfile))
  $null = Save-DemoIQSessionSecret -Name 'admin-session' -Value $secret
  Clear-DemoIQSessionSecret -Name 'admin-session'
  Check 'Clear-DemoIQSessionSecret removes it' (-not (Test-Path -LiteralPath $sfile))
  $escaped = $false; try { $null = Get-DemoIQSessionSecretPath -Name '../escape' } catch { $escaped = $true }
  Check 'a secret name cannot leave the session folder' $escaped

  # ------------------------------------------------------------ plain configs in generate.js
  Write-Host '-- generate.js plain configs --'
  $prefs = Get-Content (Join-Path $PSScriptRoot 'fixtures/test-prefs.json') -Raw
  Set-Content -LiteralPath (Join-Path $work 'prefs.json') -Value $prefs -Encoding UTF8
  $plainDir = Join-Path $work 'plain'
  $null = & node (Join-Path $S 'generate.js') --html (Join-Path $root 'engine/DemoIQ.html') --prefs (Join-Path $work 'prefs.json') `
    --emit config --out-dir $plainDir 2>$null
  $plain = Join-Path $plainDir 'DemoIQ_config.json'
  $plainObj = Get-Content -LiteralPath $plain -Raw | ConvertFrom-Json
  Check 'generate.js --emit config writes a plain config' ($LASTEXITCODE -eq 0 -and $plainObj.adminUpn -eq 'admin@fixturetest.onmicrosoft.com')
  Check 'the plain config never carries an admin password or a certificate thumbprint' ([string]::IsNullOrEmpty($plainObj.adminPwd) -and [string]::IsNullOrEmpty($plainObj.existingCertThumbprint))
  if ($unix) { Check 'the plain config is owner-only (0600)' ((Mode $plain) -match $ownerOnly) }
  $promptsOut = Join-Path $work 'prompts.json'
  Set-Content -LiteralPath (Join-Path $work 'empty.json') -Value '{}'
  $gen = & node (Join-Path $S 'generate.js') --html (Join-Path $root 'engine/DemoIQ.html') --prefs (Join-Path $work 'empty.json') `
    --from-config $plain --emit prompts --out-file $promptsOut 2>&1
  $summary = JsonOf $gen
  Check 'generate.js --from-config reads a plain config with no password' ($LASTEXITCODE -eq 0 -and $summary.ok -and (Test-Path $promptsOut))
  $dumpText = [System.IO.File]::ReadAllText($promptsOut)
  $dump = $dumpText | ConvertFrom-Json
  Check '--out-file writes the complete JSON document and a one-line summary' ($dump.ok -and $null -ne $dump.prompts -and $summary.outFile -eq (Resolve-Path $promptsOut).Path -and $summary.bytes -eq [System.Text.Encoding]::UTF8.GetByteCount($dumpText))

  # ------------------------------------------------------------ convert_config.ps1
  Write-Host '-- convert_config.ps1 (encrypted upstream configs) --'
  # An engine-format encrypted config (cdx-enc-v1: PBKDF2 200000/SHA256 + AES-256-GCM,
  # data = ciphertext || tag), built here because nothing in this adaptation writes one.
  $CFGPW = 'Cfg-Pw-1!-Ünï'
  $cfgDir = Join-Path $work 'cfg'; New-Item -ItemType Directory -Force $cfgDir | Out-Null
  $encCfg = Join-Path $cfgDir 'DemoIQ_config_enc.json'
  $upstream = Get-Content -LiteralPath $plain -Raw | ConvertFrom-Json
  $upstream.adminPwd = 'upstream-admin-pw'; $upstream.existingCertThumbprint = 'AB' * 20
  $plainBytes = [System.Text.Encoding]::UTF8.GetBytes(($upstream | ConvertTo-Json -Depth 6))
  $salt = [byte[]]::new(16); $iv = [byte[]]::new(12)
  [System.Security.Cryptography.RandomNumberGenerator]::Fill($salt); [System.Security.Cryptography.RandomNumberGenerator]::Fill($iv)
  $kdf = [System.Security.Cryptography.Rfc2898DeriveBytes]::new([System.Text.Encoding]::UTF8.GetBytes($CFGPW), $salt, 200000, [System.Security.Cryptography.HashAlgorithmName]::SHA256)
  $ct = [byte[]]::new($plainBytes.Length); $tag = [byte[]]::new(16)
  $gcm = [System.Security.Cryptography.AesGcm]::new($kdf.GetBytes(32), 16)
  try { $gcm.Encrypt($iv, $plainBytes, $ct, $tag) } finally { $gcm.Dispose(); $kdf.Dispose() }
  @{ v = 'cdx-enc-v1'; salt = [int[]]$salt; iv = [int[]]$iv; data = [int[]]($ct + $tag) } | ConvertTo-Json -Compress -Depth 4 |
    Set-Content -LiteralPath $encCfg -Encoding UTF8
  $convert = Join-Path $S 'convert_config.ps1'
  $ast = [System.Management.Automation.Language.Parser]::ParseFile($convert, [ref]$null, [ref]$null)
  $fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'ConvertFrom-DemoIQEncryptedConfig' }, $true)
  . ([scriptblock]::Create($fn.Extent.Text))
  $envelope = Get-Content -LiteralPath $encCfg -Raw | ConvertFrom-Json
  $decrypted = ConvertFrom-DemoIQEncryptedConfig -Envelope $envelope -Password (ConvertTo-SecureString $CFGPW -AsPlainText -Force)
  Check 'the right password decrypts the engine format' ($decrypted -and $decrypted.adminUpn -eq 'admin@fixturetest.onmicrosoft.com')
  $wrong = ConvertFrom-DemoIQEncryptedConfig -Envelope $envelope -Password (ConvertTo-SecureString 'not-it' -AsPlainText -Force)
  Check 'a wrong password decrypts nothing' ($null -eq $wrong)
  $r = Invoke-Child $convert @('-Path', $encCfg) @{} -NoStdin
  Check 'without an interactive console it refuses (exit 2, no-console) and prints the user command' ($r.code -eq 2 -and $r.json.reason -eq 'no-console' -and $r.json.error -match [regex]::Escape($encCfg))
  Check 'the refusal never asks for or prints the password' (-not (($r.raw -join "`n") -match 'Password for'))
  $r = Invoke-Child $convert @('-Path', $plain) @{} -NoStdin
  Check 'a plain config needs no conversion (exit 2, not-encrypted)' ($r.code -eq 2 -and $r.json.reason -eq 'not-encrypted')
  $r = Invoke-Child $convert @('-Path', (Join-Path $work 'missing.json')) @{} -NoStdin
  Check 'a missing config is reported (exit 2, not-found)' ($r.code -eq 2 -and $r.json.reason -eq 'not-found')
  $gen = & node (Join-Path $S 'generate.js') --html (Join-Path $root 'engine/DemoIQ.html') --prefs (Join-Path $work 'empty.json') `
    --from-config $encCfg --emit prompts 2>&1
  Check 'generate.js refuses an encrypted config with the convert hint' ($LASTEXITCODE -eq 2 -and (($gen | Out-String) -match 'convert it once'))

  # ------------------------------------------------------------ find_config / repoint_config
  Write-Host '-- find_config.ps1 + repoint_config.ps1 (saved receipts) --'
  $tws = Join-Path $work 'ws'
  $lib = Join-Path $tws 'configs'; New-Item -ItemType Directory -Force $lib | Out-Null
  $libPlain = Join-Path $lib 'DemoIQ_config_plant.json'
  Copy-Item -LiteralPath $plain -Destination $libPlain
  Copy-Item -LiteralPath $encCfg -Destination (Join-Path $lib 'DemoIQ_config_upstream.json')
  $APP = '0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0'; $TEN = '11111111-2222-3333-4444-555555555555'
  $SPEECH = "/subscriptions/00000000-0000-0000-0000-00000000000a/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/fdry-test"
  $receipts = Join-Path $work 'receipts.json'
  [ordered]@{ ok = $true; demoiq = [ordered]@{ tenant_id = $TEN; client_id = $APP; consent = 'granted'
      delegated_scopes = @('User.Read', 'Mail.ReadWrite'); speech_resource_id = $SPEECH; audio = [ordered]@{ ok = $true }
      checked_at = '2026-01-01T00:00:00Z' } } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $receipts -Encoding UTF8
  $noDemoiq = Join-Path $work 'receipts-none.json'
  '{"ok": true, "demoiq": null}' | Set-Content -LiteralPath $noDemoiq -Encoding UTF8
  $find = Join-Path $S 'find_config.ps1'; $repoint = Join-Path $S 'repoint_config.ps1'
  $users = @(1..5 | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$plainObj.("u${_}upn")) }).Count

  $r = Invoke-Child $find @('-OutputFolder', $tws, '-ReceiptsFile', $receipts) @{}
  $mine = @($r.json.configs | Where-Object { $_.name -eq 'DemoIQ_config_plant.json' })[0]
  $theirs = @($r.json.configs | Where-Object { $_.name -eq 'DemoIQ_config_upstream.json' })[0]
  Check 'find_config lists the library with the deployment identity' ($r.code -eq 0 -and $r.json.ok -and $r.json.count -eq 2 -and $r.json.deployment.clientId -eq $APP -and $r.json.deployment.tenantId -eq $TEN -and $r.json.deployment.consent -eq 'granted')
  Check 'a plain config is summarized without any password' ($mine.adminUpn -eq 'admin@fixturetest.onmicrosoft.com' -and $mine.demoUsers -eq $users -and -not (($r.raw -join '') -match 'upstream-admin-pw'))
  Check 'a config for another app is flagged (appMatchesDeployment false)' ($mine.appMatchesDeployment -eq $false)
  Check 'an encrypted config is listed as encrypted only' ($theirs.encrypted -eq $true -and -not $theirs.adminUpn)
  Check 'find_config creates owner-only library and scratch folders' (-not $unix -or (((Mode $lib) -match 'UserRead') -and -not ((Mode $lib) -match 'Group|Other')))
  $r = Invoke-Child $find @('-OutputFolder', $tws, '-ReceiptsFile', $noDemoiq) @{}
  Check 'no DemoIQ receipts yet: deployment null with the runner command' ($r.json.ok -and $null -eq $r.json.deployment -and $r.json.deploymentError -match '--with-demoiq')

  $r = Invoke-Child $repoint @('-ConfigPath', $libPlain, '-ReceiptsFile', $receipts) @{}
  $after = Get-Content -LiteralPath $libPlain -Raw | ConvertFrom-Json
  Check 'repoint_config sets the app and Speech resource from the receipts' ($r.code -eq 0 -and $r.json.changed.existingAppId -and $after.existingAppId -eq $APP -and $after.speechResourceId -eq $SPEECH)
  Check 'repoint_config leaves the rest of the config alone' ($after.adminUpn -eq $plainObj.adminUpn -and [string]::IsNullOrEmpty($after.adminPwd) -and [string]::IsNullOrEmpty($after.existingCertThumbprint))
  if ($unix) { Check 'the repointed config stays owner-only (0600)' ((Mode $libPlain) -match $ownerOnly) }
  $r = Invoke-Child $find @('-OutputFolder', $tws, '-ReceiptsFile', $receipts, '-NoReceipts') @{}
  Check '-NoReceipts lists the library without a deployment lookup' ($r.json.ok -and $null -eq $r.json.deployment -and -not $r.json.deploymentError)
  $r = Invoke-Child $find @('-OutputFolder', $tws, '-ReceiptsFile', $receipts) @{}
  Check 'after repointing the config matches the deployment' (@($r.json.configs | Where-Object { $_.name -eq 'DemoIQ_config_plant.json' })[0].appMatchesDeployment -eq $true)
  $r = Invoke-Child $repoint @('-ConfigPath', (Join-Path $lib 'DemoIQ_config_upstream.json'), '-ReceiptsFile', $receipts) @{}
  Check 'repoint_config refuses an encrypted config with the convert hint' ($r.code -eq 2 -and $r.json.error -match 'convert_config')
  $r = Invoke-Child $repoint @('-ConfigPath', $libPlain, '-ReceiptsFile', $noDemoiq) @{}
  Check 'repoint_config without receipts or -AppId refuses (exit 2)' ($r.code -eq 2 -and $r.json.error -match 'No app ID')
  $r = Invoke-Child $repoint @('-ConfigPath', $libPlain, '-ReceiptsFile', $noDemoiq, '-AppId', 'not-a-guid') @{}
  Check 'repoint_config rejects a malformed app ID' ($r.code -eq 2 -and $r.json.error -match 'Not an app')
  $other = Join-Path $lib 'DemoIQ_config_other.json'
  Set-Content -LiteralPath $other -Value '{"adminUpn":"keep@contoso.onmicrosoft.com","contentTypes":["emails"]}' -Encoding UTF8
  $r = Invoke-Child $repoint @('-ConfigPath', $libPlain, '-ReceiptsFile', $receipts, '-OutPath', $other) @{}
  Check 'repoint_config never replaces another config without -Force' ($r.code -eq 2 -and $r.json.error -match '-Force' -and (Get-Content -LiteralPath $other -Raw) -match 'keep@')
  $r = Invoke-Child $find @('-OutputFolder', $tws, '-NoReceipts') @{}
  Check 'a one-item content list stays a JSON array' ((($r.raw -join '') -match '"name":"DemoIQ_config_other\.json"[^}]*"contentTypes":\["emails"\]'))
  $r = Invoke-Child $repoint @('-ConfigPath', $libPlain, '-ReceiptsFile', $receipts, '-OutPath', $other, '-Force') @{}
  Check '... and replaces it with -Force' ($r.code -eq 0 -and (Get-Content -LiteralPath $other -Raw | ConvertFrom-Json).existingAppId -eq $APP)
  Remove-Item -LiteralPath $other

  $newCfg = Join-Path $lib 'DemoIQ_config_saved.json'
  $job = Start-Job -ScriptBlock { param($src, $dst) Start-Sleep -Seconds 5; Set-Content -LiteralPath $dst -Value (Get-Content -LiteralPath $src -Raw) } -ArgumentList $libPlain, $newCfg
  try {
    $r = Invoke-Child $find @('-OutputFolder', $tws, '-NoReceipts', '-WaitSeconds', '40') @{}
    Check 'watch mode returns the config saved during the wait' (@($r.json.newConfigs).Count -eq 1 -and $r.json.newConfigs[0] -eq (Resolve-Path $newCfg).Path -and $r.json.waitedSeconds -lt 40)
  } finally { $null = Receive-Job $job -Wait -AutoRemoveJob -ErrorAction SilentlyContinue }

  # ------------------------------------------------------------ draft_config.ps1
  Write-Host '-- draft_config.ps1 (story -> library config) --'
  $draft = Join-Path $S 'draft_config.ps1'
  $dws = Join-Path $work 'draftws'
  $five = 'AlexW@contoso.onmicrosoft.com,MeganB@contoso.onmicrosoft.com,LeeG@contoso.onmicrosoft.com,NestorW@contoso.onmicrosoft.com,PattiF@contoso.onmicrosoft.com'
  $common = @('-AdminUpn', 'admin@contoso.onmicrosoft.com', '-ReceiptsFile', $receipts, '-OutputFolder', $dws)
  $r = Invoke-Child $draft (@('-Name', 'plant', '-DemoUsers', $five, '-Preset', 'sample-asset-operations') + $common) @{}
  $dcfgPath = Join-Path $dws 'configs/DemoIQ_config_plant.json'
  $dcfg = Get-Content -LiteralPath $dcfgPath -Raw | ConvertFrom-Json
  Check 'the sample preset drafts a library config for this deployment' ($r.code -eq 0 -and $r.json.ok -and $dcfg.existingAppId -eq $APP -and $dcfg.speechResourceId -eq $SPEECH -and $r.json.story.title -eq 'Pump B Maintenance at North Works')
  Check 'the template sample facts reach the story' ($dcfg.customScenarioText -match 'North Works' -and $dcfg.customScenarioText -match 'Pump B, which is in Maintenance' -and $dcfg.customScenarioText -match 'Conveyor A')
  Check 'exactly the five named demo users, no passwords, no invented defaults' ($dcfg.u1upn -eq 'AlexW@contoso.onmicrosoft.com' -and $dcfg.u5upn -eq 'PattiF@contoso.onmicrosoft.com' -and -not (1..5 | Where-Object { $dcfg."u${_}pwd" }) -and $r.json.passwordsNeeded -eq 5)
  Check 'no admin password or certificate thumbprint' ([string]::IsNullOrEmpty($dcfg.adminPwd) -and [string]::IsNullOrEmpty($dcfg.existingCertThumbprint))
  Check 'the preset content selection is kept in the Builder shape' (($dcfg.contentTypes -join ',') -eq 'emails,chats,channel,meetings,word,ppt,excel,aop' -and $dcfg.options.meetingCount -eq 2)
  $bc = $r.json.builderCommand
  Check 'the Builder command serves the page with saving on and the draft prefilled' ($bc -match '-AllowSave' -and $bc -match '-Root "' -and $bc -match '-Path DemoIQ-Config-Builder\.html' -and
    $bc -match ('-PrefillAppId ' + $APP) -and $bc -match '-PrefillConfig "DemoIQ_config_plant\.json"' -and $bc -match '-SaveDir "')
  if ($unix) {
    # Run the printed command attached (without -Detach/-OpenBrowser) and fetch the draft
    # the way the Builder page does: GET /config with the session's save token.
    $tokens = @([regex]::Matches(($bc -replace '^.*?-File ', ''), '"[^"]*"|\S+') | ForEach-Object { $_.Value.Trim('"') })
    $serveFile = $tokens[0]
    $serveArgs = @($tokens | Select-Object -Skip 1 | Where-Object { $_ -notin '-Detach', '-OpenBrowser' })
    $serveLog = Join-Path $work 'serve-draft.log'
    $argLine = (@('-NoProfile', '-File', $serveFile) + $serveArgs | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }) -join ' '
    $srv = Start-Process -FilePath $pwshExe -ArgumentList $argLine -RedirectStandardOutput $serveLog -PassThru
    try {
      $url = $null
      foreach ($i in 1..40) {
        Start-Sleep -Milliseconds 250
        if (Test-Path -LiteralPath $serveLog) { $url = @(Get-Content -LiteralPath $serveLog | Where-Object { $_ -like '`[SERVE-URL`]*' }) | Select-Object -First 1 }
        if ($url) { break }
      }
      $u = if ($url) { [uri](($url -replace '^\[SERVE-URL\]\s*', '').Trim()) } else { $null }
      $saveToken = if ($u -and $u.Fragment -match 'saveToken=([^&]+)') { [uri]::UnescapeDataString($Matches[1]) } else { $null }
      $draftBack = $null
      if ($saveToken) {
        $draftBack = Invoke-RestMethod -Uri ("{0}://{1}:{2}/config?name=DemoIQ_config_plant.json" -f $u.Scheme, $u.Host, $u.Port) -Headers @{ 'X-Save-Token' = $saveToken } -TimeoutSec 10
      }
      Check 'the Builder command really serves the draft to the page (GET /config)' ($u -and $u.Query -match 'config=DemoIQ_config_plant\.json' -and $draftBack -and $draftBack.customScenarioText -match 'North Works' -and $draftBack.existingAppId -eq $APP)
    } finally { Stop-Process -Id $srv.Id -Force -ErrorAction SilentlyContinue }
  }
  if ($unix) { Check 'the draft and its prefs are owner-only (0600)' (((Mode $dcfgPath) -match $ownerOnly) -and ((Mode $r.json.prefsPath) -match $ownerOnly)) }
  $r = Invoke-Child $find @('-OutputFolder', $dws, '-ReceiptsFile', $receipts) @{}
  $listed = @($r.json.configs)[0]
  Check 'find_config sees the draft: matches the deployment, passwords still needed' ($listed.appMatchesDeployment -eq $true -and $listed.demoUsers -eq 5 -and $listed.demoUsersWithPassword -eq 0 -and @($listed.contentTypes).Count -eq 8)
  Check 'the draft is marked as a draft and never ready' ($dcfg.demoiqDraft -eq $true -and $listed.draft -eq $true -and $listed.ready -eq $false)
  $storyFile = Join-Path $work 'story.txt'
  "Title: Conveyor A Throughput at South Works`nDescription: A fictional story about South Works absorbing North Works volume while Pump B is in Maintenance.`nExcel: South_Works_Throughput.xlsx" | Set-Content -LiteralPath $storyFile -Encoding UTF8
  $r = Invoke-Child $draft (@('-Name', 'south', '-DemoUsers', $five, '-Preset', 'sample-asset-operations', '-StoryFile', $storyFile, '-CompanyName', 'Fabrikam') + $common) @{}
  $scfg = Get-Content -LiteralPath (Join-Path $dws 'configs/DemoIQ_config_south.json') -Raw | ConvertFrom-Json
  Check 'an approved story file wins over the preset story' ($r.code -eq 0 -and $r.json.story.title -eq 'Conveyor A Throughput at South Works' -and $scfg.companyName -eq 'Fabrikam' -and $null -eq $scfg.scenarioIndex)
  $r = Invoke-Child $draft (@('-Name', 'storyonly', '-DemoUsers', $five, '-StoryFile', $storyFile) + $common) @{}
  $ocfg = Get-Content -LiteralPath (Join-Path $dws 'configs/DemoIQ_config_storyonly.json') -Raw | ConvertFrom-Json
  Check 'a story without a preset gets the sample content mix (meetings and the AoP runbook included)' ($r.code -eq 0 -and
    ($ocfg.contentTypes -join ',') -eq 'emails,chats,channel,meetings,word,ppt,excel,aop' -and $ocfg.customScenarioText -match 'Conveyor A Throughput')
  $r = Invoke-Child $draft (@('-Name', 'plant', '-DemoUsers', $five, '-Preset', 'sample-asset-operations') + $common) @{}
  Check 'an existing config is never replaced without -Force' ($r.code -eq 2 -and $r.json.error -match '-Force')
  $r = Invoke-Child $draft (@('-Name', 'plant', '-DemoUsers', $five, '-Preset', 'sample-asset-operations', '-Force') + $common) @{}
  Check '-Force replaces it' ($r.code -eq 0 -and $r.json.ok)
  $r = Invoke-Child $draft (@('-Name', 'four', '-DemoUsers', 'AlexW@contoso.onmicrosoft.com,MeganB@contoso.onmicrosoft.com,LeeG@contoso.onmicrosoft.com,NestorW@contoso.onmicrosoft.com', '-Preset', 'sample-asset-operations') + $common) @{}
  Check 'fewer than five demo users is refused' ($r.code -eq 2 -and $r.json.error -match 'exactly five')
  $r = Invoke-Child $draft (@('-Name', 'adm', '-DemoUsers', ($five -replace 'PattiF@', 'ADMIN@'), '-Preset', 'sample-asset-operations') + $common) @{}
  Check 'the administrator as a demo user is refused' ($r.code -eq 2 -and $r.json.error -match 'administrator cannot be a demo user')
  $r = Invoke-Child $draft (@('-Name', 'dup', '-DemoUsers', ($five -replace 'PattiF@', 'alexw@'), '-Preset', 'sample-asset-operations') + $common) @{}
  Check 'a repeated demo user is refused (case-insensitive)' ($r.code -eq 2 -and $r.json.error -match 'once')
  $r = Invoke-Child $draft (@('-Name', 'dom', '-DemoUsers', ($five -replace 'PattiF@contoso', 'PattiF@fabrikam'), '-Preset', 'sample-asset-operations') + $common) @{}
  Check 'a demo user outside the administrator''s domain is a warning, not a block' ($r.code -eq 0 -and (@($r.json.warnings) -join ' ') -match 'PattiF@fabrikam')
  $r = Invoke-Child $draft @('-Name', 'none', '-DemoUsers', $five, '-Preset', 'sample-asset-operations', '-AdminUpn', 'admin@contoso.onmicrosoft.com', '-ReceiptsFile', $noDemoiq, '-OutputFolder', $dws) @{}
  Check 'no DemoIQ receipts: refused with the runner command' ($r.code -eq 2 -and $r.json.error -match '--with-demoiq')
  $r = Invoke-Child $draft (@('-Name', 'nostory', '-DemoUsers', $five) + $common) @{}
  Check 'no story and no preset is refused' ($r.code -eq 2 -and $r.json.error -match 'story')
  $r = Invoke-Child $draft (@('-Name', 'Bad Name', '-DemoUsers', $five, '-Preset', 'sample-asset-operations') + $common) @{}
  Check 'an unsafe file name is refused' ($r.code -eq 2)
  $r = Invoke-Child $draft (@('-Name', 'missing', '-DemoUsers', $five, '-Preset', 'no-such-preset') + $common) @{}
  Check 'an unknown preset is refused' ($r.code -eq 2 -and $r.json.error -match 'Preset not found')

  # ------------------------------------------------------------ detach + processes
  if ($unix) {
    Write-Host '-- detached processes --'
    $marker = Join-Path $work 'detached.marker'
    $cpid = Start-DemoIQDetachedProcess -FilePath '/bin/sh' -ArgumentList @('-c', "sleep 1; echo done > '$marker'") -LogFile (Join-Path $work 'detach.log')
    $myPgid = ((& /bin/ps -o 'pgid=' -p $PID) | Out-String).Trim()
    $childPgid = ((& /bin/ps -o 'pgid=' -p $cpid) | Out-String).Trim()
    Check 'detached child runs in its own process group' ($cpid -gt 0 -and $childPgid -and $childPgid -ne $myPgid)
    Start-Sleep -Seconds 2
    Check 'detached child kept running after the launcher returned' ((Test-Path $marker) -and ((Get-Content $marker -Raw).Trim() -eq 'done'))
    $ptag = 'demoiq-test-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
    $ppid2 = Start-DemoIQDetachedProcess -FilePath $pwshExe -ArgumentList @('-NoProfile', '-Command', "Start-Sleep 8 # $ptag")
    Start-Sleep -Milliseconds 800
    $hit = @(Get-DemoIQPwshProcess | Where-Object { $_.ProcessId -eq $ppid2 })
    Check 'Get-DemoIQPwshProcess lists the pwsh process with its command line' ($hit.Count -eq 1 -and $hit[0].CommandLine -match $ptag)
    Stop-Process -Id $ppid2 -ErrorAction SilentlyContinue
  }
  if ($IsLinux) {
    Set-Content -LiteralPath (Join-Path $work 'url.ps1') -Value ". '$(Join-Path $S 'platform.ps1')'; if (Open-DemoIQUrl 'http://127.0.0.1:9/') { 'OPENED' } else { 'NO-BROWSER' }" -Encoding UTF8
    $r = Invoke-Child (Join-Path $work 'url.ps1') @() @{ BROWSER = $null; DISPLAY = $null; WAYLAND_DISPLAY = $null }
    Check 'headless Linux: Open-DemoIQUrl reports no browser instead of failing' (($r.raw -join '') -match 'NO-BROWSER')
  }

  # ------------------------------------------------------------ helpers
  Write-Host '-- helpers --'
  $src = Join-Path $work 'out'; New-Item -ItemType Directory -Force $src | Out-Null
  Set-Content -LiteralPath (Join-Path $src 'aop_demo_runbook_20260101_120000.html') -Value '<html></html>'
  $repo = Join-Path $work 'repo'; New-Item -ItemType Directory -Force (Join-Path $repo '.git') | Out-Null
  $r = Invoke-Child (Join-Path $S 'stage_deliverables.ps1') @('-SourceDir', $src, '-WorkspacePath', (Join-Path $repo 'sub')) @{}
  Check 'stage_deliverables refuses a folder inside a Git work tree' (-not $r.json.ok -and $r.json.error -match 'Git work tree' -and -not (Test-Path (Join-Path $repo 'sub')))
  $r = Invoke-Child (Join-Path $S 'stage_deliverables.ps1') @('-SourceDir', $src, '-WorkspacePath', (Join-Path $work 'outside')) @{}
  Check 'stage_deliverables copies the AoP runbook to a folder outside Git' ($r.json.ok -and @($r.json.files).Count -eq 1)
  $r = Invoke-Child (Join-Path $S 'launch_seeder.ps1') @('-SeederScript', (Join-Path $src 'aop_demo_runbook_20260101_120000.html')) @{}
  Check 'launch_seeder fails fast (exit 2) on a file that is not a generated seeder' ($r.code -eq 2 -and $r.json.error -match 'generate\.js')
  if ($unix) {
    $privateDir = '^(UserExecute, UserWrite, UserRead|UserRead, UserWrite, UserExecute)$'
    $r = Invoke-Child (Join-Path $S 'find_config.ps1') @('-NoReceipts') @{}
    Check 'the default workspace root is owner-only (0700) once DemoIQ writes into it' ($r.json.ok -and (Mode (Get-DemoIQWorkspace)) -match $privateDir)
    $r = Invoke-Child (Join-Path $S 'new_run.ps1') @('-ScenarioTitle', 'Perm check', '-Tenant', 'contoso.onmicrosoft.com', '-OutputFolder', (Join-Path $work 'permws')) @{}
    Check 'new_run creates owner-only run folders (0700)' ($r.json.ok -and (Mode $r.json.runFolder) -match $privateDir -and (Mode (Split-Path -Parent $r.json.runFolder)) -match $privateDir)
  }

  # ------------------------------------------------------------ cleanup_local.ps1
  Write-Host '-- cleanup_local.ps1 (runs, sign-in, config library) --'
  $cws = Join-Path $work 'cleanws'
  New-Item -ItemType Directory -Force (Join-Path $cws 'runs/r1/DemoIQ-Output'), (Join-Path $cws 'configs'), (Join-Path $cws 'scratch'), (Join-Path $cws '.session') | Out-Null
  Set-Content -LiteralPath (Join-Path $cws 'runs/r1/DemoIQ-Output/cleanup.config') -Value '{}'
  Copy-Item -LiteralPath $plain -Destination (Join-Path $cws 'configs/DemoIQ_config.json')
  Set-Content -LiteralPath (Join-Path $cws '.session/admin-signin-code.txt') -Value 'ABCD-EFGH'
  Set-Content -LiteralPath (Join-Path $cws 'app_registration.json') -Value '{"appId":"00000000-0000-0000-0000-000000000001"}'
  $signIn = Save-DemoIQSessionSecret -Name 'admin-session' -Value $secret
  $cl = Join-Path $S 'cleanup_local.ps1'
  $r = Invoke-Child $cl @('-OutputFolder', $cws) @{}
  $inv = @($r.json.inventory | ForEach-Object { $_.kind + ':' + (Split-Path $_.path -Leaf) })
  Check 'depth 1 dry-run: runs, scratch, the session folder and the stored sign-in' ($r.code -eq 0 -and $r.json.mode -eq 'dry-run' -and ($inv -contains 'artifact:runs') -and ($inv -contains 'artifact:scratch') -and ($inv -contains 'session:.session') -and ($inv -contains ('session:' + (Split-Path $signIn -Leaf))))
  Check 'depth 1 keeps the config library' (-not ($inv -like 'config:*') -and @($r.json.kept | Where-Object { $_.kind -eq 'config' }).Count -eq 1)
  Check 'files from upstream or a certificate-based version are reported, never inventoried' (@($r.json.foreign).Count -eq 1 -and -not ($inv -like '*app_registration.json'))
  Check 'dry-run deletes nothing' ((Test-Path (Join-Path $cws 'runs')) -and (Test-Path -LiteralPath $signIn))
  $r = Invoke-Child $cl @('-OutputFolder', $cws, '-Depth', '3') @{}
  Check 'depth 3 is refused: the Entra app belongs to Terraform' ($r.code -eq 2 -and $r.json.mode -eq 'refused' -and $r.json.error -match 'demoiq_enabled')
  $r = Invoke-Child $cl @('-OutputFolder', $cws, '-Execute') @{}
  Check '-Execute without the confirmation token is blocked' ($r.code -eq 0 -and $r.json.mode -eq 'blocked' -and $r.json.confirmToken -eq 'CLEANUP-1' -and (Test-Path (Join-Path $cws 'runs')))
  $r = Invoke-Child $cl @('-OutputFolder', $cws, '-Execute', '-Confirm', 'CLEANUP-1') @{}
  Check 'depth 1 execute: clean exit' ($r.code -eq 0 -and $r.json.ok)
  Check 'depth 1 execute: runs, session folder and stored sign-in gone; configs kept' (-not (Test-Path (Join-Path $cws 'runs')) -and -not (Test-Path (Join-Path $cws '.session')) -and -not (Test-Path -LiteralPath $signIn) -and (Test-Path (Join-Path $cws 'configs/DemoIQ_config.json')))
  $r = Invoke-Child $cl @('-OutputFolder', $cws, '-Depth', 'FullLocal', '-Execute', '-Confirm', 'CLEANUP-2') @{}
  Check 'depth 2 execute: the config library goes too' ($r.code -eq 0 -and $r.json.ok -and -not (Test-Path (Join-Path $cws 'configs')))
  Check 'depth 2 leaves foreign files and therefore the workspace in place' ((Test-Path (Join-Path $cws 'app_registration.json')) -and (Test-Path $cws))
  Remove-Item -LiteralPath (Join-Path $cws 'app_registration.json')
  $r = Invoke-Child $cl @('-OutputFolder', $cws, '-Depth', '2', '-Execute', '-Confirm', 'CLEANUP-2') @{}
  Check 'depth 2 removes an emptied workspace' ($r.code -eq 0 -and -not (Test-Path $cws))
}
finally {
  Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
  if ($IsWindows) { Remove-Item -LiteralPath $env:DEMOIQ_SESSION_DIR -Recurse -Force -ErrorAction SilentlyContinue }
}

if ($script:fails) { Write-Host "test_platform: $($script:fails) FAILURE(S)" -ForegroundColor Red; exit 1 }
Write-Host 'test_platform: all passed' -ForegroundColor Green
exit 0

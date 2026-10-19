<#
  draft_config.ps1 - draft a plain DemoIQ config from a story, for review in the Builder.

  The story comes from the project's own scenario (the agent drafts a story file from
  scenario/ sources and the user approves it) or from a preset in scripts/presets/ -
  sample-asset-operations is the template's North Works / South Works sample. It is
  one-way: the story shapes what DemoIQ seeds into Microsoft 365, and nothing seeded is
  ever fed back into Foundry IQ knowledge.

  This writes two owner-only files and nothing else:
    <ws>/scratch/prefs-<name>.json       the merged preferences
    <ws>/configs/DemoIQ_config_<name>.json   via generate.js --emit config, with
                                         existingAppId and speechResourceId from the
                                         deployment receipts, marked "demoiqDraft": true
  Demo-user passwords are never taken here. Open the Config Builder prefilled with the
  printed builderCommand; the user reviews the draft, types the passwords and saves. The
  saved config replaces the draft (find_config.ps1 and status.ps1 never count a draft as
  ready).

  Params:
    -Name          short slug for the file name (lowercase letters, digits, -)
    -AdminUpn      the administrator who signs in for each run
    -DemoUsers     the five existing demo-user UPNs (array or comma-separated); never the
                   administrator, whose MFA is never relaxed
    -Preset        a preset name in scripts/presets (e.g. sample-asset-operations) or a path
    -StoryFile     a UTF-8 text file holding the story in the customScenarioText format
                   (Title:, Description:, Excel:, Word:, PowerPoint: lines); wins over
                   the preset's story
    -Industry, -CompanyName, -Language, -ContentTypes   optional overrides; without a
                   preset or -ContentTypes, the content mix (content types, AoP surfaces
                   and agents, counts) is the sample preset's
    -ReceiptsFile  (optional) a saved `deploy.py status --json` output instead of the runner
    -OutputFolder  the workspace <ws> (default ~/.copilot/DemoIQ-Output-Scout)
    -Force         replace an existing config of the same name

  Output: ONE compact JSON line:
    { ok, name, configPath, prefsPath, appId, story{ title, industry, companyName },
      demoUsers[], passwordsNeeded, warnings[], builderCommand }
  Exit 0; 2 on a usage problem (no receipts, bad UPN, admin among the demo users, missing
  preset or story, an existing config without -Force) or a generator failure.
#>
param(
  [Parameter(Mandatory = $true)][string]$Name,
  [Parameter(Mandatory = $true)][string]$AdminUpn,
  [Parameter(Mandatory = $true)][string[]]$DemoUsers,
  [string]$Preset,
  [string]$StoryFile,
  [string]$Industry,
  [string]$CompanyName,
  [string]$Language,
  [string[]]$ContentTypes,
  [string]$ReceiptsFile,
  [string]$OutputFolder,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'platform.ps1')
function Out-Json($o) { $o | ConvertTo-Json -Compress -Depth 6 }
function Fail([string]$Message) { Out-Json ([ordered]@{ ok = $false; error = $Message }); exit 2 }
$upnPattern = '^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$'

if ($Name -notmatch '^[a-z0-9][a-z0-9-]{0,40}$') { Fail "Use a short lowercase name (letters, digits, -): $Name" }
if ($AdminUpn -notmatch $upnPattern) { Fail "Not a user principal name: $AdminUpn" }
$users = @($DemoUsers | ForEach-Object { $_ -split '[,;\s]+' } | Where-Object { $_ } | ForEach-Object { $_.Trim() })
# The engine casts exactly five accounts (u1..u5) and invents CDX default names for any
# blank slot, which may not exist on this tenant - so all five are required, as in the Builder.
if ($users.Count -ne 5) { Fail "Name exactly five demo users (got $($users.Count)): DemoIQ casts five distinct accounts and invents default names for missing ones." }
foreach ($u in $users) { if ($u -notmatch $upnPattern) { Fail "Not a user principal name: $u" } }
if (@($users | Where-Object { $_ -ieq $AdminUpn }).Count) { Fail 'The administrator cannot be a demo user: demo users sign in with a password while their MFA is relaxed for the run, and the administrator''s MFA is never relaxed.' }
if (@($users | ForEach-Object { $_.ToLowerInvariant() } | Sort-Object -Unique).Count -ne $users.Count) { Fail 'Each demo user can be named once.' }
$warnings = New-Object System.Collections.Generic.List[string]
$adminDomain = ($AdminUpn -split '@')[1]
$otherDomain = @($users | Where-Object { ($_ -split '@')[1] -ine $adminDomain })
if ($otherDomain.Count) { $warnings.Add("Not in the administrator's domain ($adminDomain): $($otherDomain -join ', '). Demo users must be accounts in the same tenant.") }

$receipts = $null
try { $receipts = Get-DemoIQRunnerReceipts -ReceiptsFile $ReceiptsFile } catch { Fail $_.Exception.Message }
if (-not $receipts -or -not $receipts.client_id) { Fail 'DemoIQ is not set up for this deployment yet: run deploy.py up --with-demoiq --through demoiq.' }

$prefs = [ordered]@{}
if ($Preset) {
  $presetPath = if (Test-Path -LiteralPath $Preset -PathType Leaf) { $Preset } else { Join-Path (Join-Path $PSScriptRoot 'presets') "$Preset.json" }
  if (-not (Test-Path -LiteralPath $presetPath -PathType Leaf)) { Fail "Preset not found: $Preset" }
  $p = Get-Content -LiteralPath $presetPath -Raw | ConvertFrom-Json
  foreach ($prop in $p.PSObject.Properties) {
    if ($prop.Name -like '_*') { continue }
    if ($prop.Name -in @('adminUpn', 'adminPwd', 'existingAppId', 'existingCertThumbprint', 'speechResourceId', 'users')) { continue }
    $prefs[$prop.Name] = $prop.Value
  }
}
if ($StoryFile) {
  if (-not (Test-Path -LiteralPath $StoryFile -PathType Leaf)) { Fail "Story file not found: $StoryFile" }
  $story = (Get-Content -LiteralPath $StoryFile -Raw).Trim()
  if (-not $story) { Fail "The story file is empty: $StoryFile" }
  $prefs.customScenarioText = $story
  $prefs.scenarioIndex = $null
}
if (-not $prefs.customScenarioText -and -not $prefs.scenarioIndex) { Fail 'Give a story: -StoryFile, or a -Preset that carries one.' }
if ($Industry) { $prefs.industry = $Industry }
if ($PSBoundParameters.ContainsKey('CompanyName')) { $prefs.companyName = $CompanyName }
if ($Language) { $prefs.demoLanguage = $Language }
if ($ContentTypes) { $prefs.contentTypes = @($ContentTypes | ForEach-Object { $_ -split '[,;\s]+' } | Where-Object { $_ }) }
# A story without a content selection gets the sample's mix (meetings and the AoP runbook
# included - the optional Work IQ check asks the runbook's prompts), not the Builder's defaults.
if ($null -eq $prefs.contentTypes) {
  $sample = Get-Content -LiteralPath (Join-Path (Join-Path $PSScriptRoot 'presets') 'sample-asset-operations.json') -Raw | ConvertFrom-Json
  foreach ($key in 'contentTypes', 'aopSurfaces', 'aopAgents', 'options') {
    if ($null -eq $prefs[$key] -and $null -ne $sample.$key) { $prefs[$key] = $sample.$key }
  }
}
$prefs.adminUpn = $AdminUpn
$prefs.adminPwd = ''
$prefs.existingAppId = [string]$receipts.client_id
$prefs.existingCertThumbprint = ''
$prefs.speechResourceId = $(if ($receipts.speech_resource_id) { [string]$receipts.speech_resource_id } else { '' })
$prefs.users = @($users | ForEach-Object { [ordered]@{ upn = $_; pwd = ''; persona = '' } })

$ws = if ($OutputFolder) { $OutputFolder } else { Get-DemoIQWorkspace }
$configsHome = New-DemoIQPrivateDirectory (Join-Path $ws 'configs')
$scratch = New-DemoIQPrivateDirectory (Join-Path $ws 'scratch')
$configName = "DemoIQ_config_$Name.json"
$configPath = Join-Path $configsHome $configName
if ((Test-Path -LiteralPath $configPath) -and -not $Force) { Fail "A config named $configName already exists. Pick another -Name, or pass -Force to replace it." }
$prefsPath = Join-Path $scratch "prefs-$Name.json"
[System.IO.File]::WriteAllText($prefsPath, '')
Set-DemoIQPrivateFile $prefsPath
[System.IO.File]::WriteAllText($prefsPath, ($prefs | ConvertTo-Json -Depth 6))

$node = Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $node) { Fail "Node.js was not found. $(Get-DemoIQInstallHint node)" }
$html = Join-Path (Split-Path $PSScriptRoot -Parent) 'engine/DemoIQ.html'
$gen = & $node.Source (Join-Path $PSScriptRoot 'generate.js') --html $html --prefs $prefsPath --emit config --out-dir $configsHome --config-name $configName 2>&1
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $configPath)) { Fail "generate.js could not write the config: $(($gen | Out-String).Trim())" }

# The engine's config export has no content selection; add it in the shape the Builder
# saves (contentTypes array, options object) so the Builder shows it and runs use it.
# demoiqDraft marks the file as a draft until the user completes it in the Builder (the
# local server drops the marker on save and removes the superseded draft).
$cfg = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
foreach ($key in 'contentTypes', 'options') {
  if ($null -eq $prefs[$key]) { continue }
  if ($cfg.PSObject.Properties[$key]) { $cfg.$key = $prefs[$key] } else { $cfg | Add-Member -NotePropertyName $key -NotePropertyValue $prefs[$key] }
}
$cfg | Add-Member -NotePropertyName 'demoiqDraft' -NotePropertyValue $true -Force
$temp = "$configPath.tmp"
[System.IO.File]::WriteAllText($temp, '')
Set-DemoIQPrivateFile $temp
[System.IO.File]::WriteAllText($temp, ($cfg | ConvertTo-Json -Depth 10))
Move-Item -LiteralPath $temp -Destination $configPath -Force
Set-DemoIQPrivateFile $configPath

$title = $null
if ($cfg.customScenarioText -match '(?m)^\s*Title:\s*(.+?)\s*$') { $title = $Matches[1] }
$serve = Join-Path $PSScriptRoot 'serve_local.ps1'
$builderArgs = @('-Detach', '-AllowSave', '-OpenBrowser', '-Root', "`"$(Split-Path $PSScriptRoot -Parent)`"", '-Path', 'DemoIQ-Config-Builder.html',
  '-PrefillAppId', [string]$cfg.existingAppId)
if ($cfg.speechResourceId) { $builderArgs += @('-PrefillSpeechId', "`"$($cfg.speechResourceId)`"") }
$builderArgs += @('-PrefillConfig', "`"$configName`"")
if ($OutputFolder) { $builderArgs += @('-SaveDir', "`"$configsHome`"") }
Out-Json ([ordered]@{
    ok = $true; name = $Name; configPath = (Resolve-Path -LiteralPath $configPath).Path; prefsPath = $prefsPath
    appId = [string]$cfg.existingAppId
    story = [ordered]@{ title = $title; industry = [string]$cfg.industry; companyName = [string]$cfg.companyName }
    demoUsers = $users; passwordsNeeded = $users.Count; warnings = $warnings
    builderCommand = "pwsh -NoProfile -ExecutionPolicy Bypass -File `"$serve`" $($builderArgs -join ' ')"
  })
exit 0

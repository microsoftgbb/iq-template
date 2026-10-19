<#
  find_config.ps1 - the DemoIQ config library and this deployment's DemoIQ identity.

  ONE home for configs: the workspace config library <ws>/configs. The Config Builder
  saves straight into it (serve_local.ps1 POST /save) and drafted configs are written
  there too, so nothing is ever swept in from Downloads or other folders. This script:

    1. DISCOVERY - lists the configs in <ws>/configs, newest first, with what the agent
       needs to choose and check one (administrator, app, demo users, content types).
       It never returns a password. An encrypted (cdx-enc-v1) config from upstream
       DemoIQ is listed with encrypted:true: convert it once with convert_config.ps1.
    2. WATCH MODE (-WaitSeconds > 0) - polls the library until a config is saved after
       the call started (the moment the user clicks Save config in the Builder), or the
       timeout expires.
    3. DEPLOYMENT IDENTITY - the deploy-platform runner's receipts (deploy.py status
       --json -> demoiq): tenant, DemoIQ app client ID, consent, Speech resource and
       the audio check. null until the demoiq phase has completed.

  Params:
    -OutputFolder  the workspace <ws> (default ~/.copilot/DemoIQ-Output-Scout)
    -WaitSeconds   (optional, default 0) watch mode
    -ReceiptsFile  (optional) a saved `deploy.py status --json` output instead of the runner
    -NoReceipts    skip the receipts lookup (fast library listing only)

  Output: ONE compact JSON line:
    { ok, ws, configsHome, scratchDir, configs:[{ path, name, lastWrite, encrypted,
      adminUpn, existingAppId, appMatchesDeployment, draft, demoUsers,
      demoUsersWithPassword, ready, contentTypes }], count, newConfigs:[path], waitedSeconds,
      deployment:{ tenantId, clientId, consent, delegatedScopes, speechResourceId,
      audio, checkedAt } | null, deploymentError }
    draft: written by draft_config.ps1 and not yet completed in the Config Builder.
    ready: names this deployment's app, is not a draft and has five demo users with
    passwords - a config a run can use as it is.
#>
param(
  [string]$OutputFolder,
  [int]$WaitSeconds = 0,
  [string]$ReceiptsFile,
  [switch]$NoReceipts
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'platform.ps1')
function Out-Json($o) { $o | ConvertTo-Json -Compress -Depth 6 }

$ws = if ($OutputFolder) { $OutputFolder } else { Get-DemoIQWorkspace }
$configsHome = New-DemoIQPrivateDirectory (Join-Path $ws 'configs')
$scratchDir = New-DemoIQPrivateDirectory (Join-Path $ws 'scratch')

$deployment = $null; $deploymentError = $null
if (-not $NoReceipts) {
  try {
    $r = Get-DemoIQRunnerReceipts -ReceiptsFile $ReceiptsFile
    if ($r -and $r.client_id) {
      $deployment = [ordered]@{
        tenantId = [string]$r.tenant_id; clientId = [string]$r.client_id; consent = [string]$r.consent
        delegatedScopes = @($r.delegated_scopes); speechResourceId = [string]$r.speech_resource_id
        audio = $r.audio; checkedAt = [string]$r.checked_at
      }
    } else {
      $deploymentError = 'DemoIQ is not set up for this deployment yet: run deploy.py up --with-demoiq --through demoiq.'
    }
  } catch { $deploymentError = $_.Exception.Message }
}

function Get-ConfigSummary([System.IO.FileInfo]$File) {
  $entry = [ordered]@{ path = $File.FullName; name = $File.Name; lastWrite = $File.LastWriteTime.ToString('s'); encrypted = $false }
  try { $c = Get-Content -LiteralPath $File.FullName -Raw | ConvertFrom-Json } catch { $entry.error = 'not valid JSON'; return $entry }
  if (-not $c) { $entry.error = 'empty'; return $entry }
  if ($c.v -eq 'cdx-enc-v1') { $entry.encrypted = $true; return $entry }
  $entry.adminUpn = [string]$c.adminUpn
  $entry.existingAppId = [string]$c.existingAppId
  $entry.appMatchesDeployment = if ($deployment) { [string]$c.existingAppId -eq $deployment.clientId } else { $null }
  # A draft (scripts/draft_config.ps1) is reviewed and completed in the Config Builder.
  $entry.draft = ($c.demoiqDraft -eq $true)
  $users = @(1..5 | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$c.("u${_}upn")) })
  $entry.demoUsers = $users.Count
  $entry.demoUsersWithPassword = @($users | Where-Object { -not [string]::IsNullOrEmpty([string]$c.("u${_}pwd")) }).Count
  $entry.ready = ($entry.appMatchesDeployment -eq $true -and -not $entry.draft -and $entry.demoUsersWithPassword -eq 5)
  # The Builder saves contentTypes as an array of names; the engine page as an object map.
  # @() keeps a one-item list an array (an if-expression would unroll it).
  $entry.contentTypes = @(if ($c.contentTypes -is [array]) { $c.contentTypes }
                          elseif ($c.contentTypes) { $c.contentTypes.PSObject.Properties | Where-Object { $_.Value } | ForEach-Object { $_.Name } })
  return $entry
}

function Get-LibraryFiles {
  @(Get-ChildItem -LiteralPath $configsHome -Filter 'DemoIQ_config*.json' -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending)
}

$watchStart = Get-Date
$newConfigs = @()
$waitedSeconds = 0
if ($WaitSeconds -gt 0) {
  $deadline = $watchStart.AddSeconds($WaitSeconds)
  while ($true) {
    $newConfigs = @(Get-LibraryFiles | Where-Object { $_.LastWriteTime -ge $watchStart } | ForEach-Object { $_.FullName })
    if ($newConfigs.Count -or (Get-Date) -ge $deadline) { break }
    Start-Sleep -Seconds 2
  }
  $waitedSeconds = [int][Math]::Round(((Get-Date) - $watchStart).TotalSeconds)
}

$configs = @(Get-LibraryFiles | ForEach-Object { Get-ConfigSummary $_ })
Out-Json ([ordered]@{
    ok = $true; ws = $ws; configsHome = $configsHome; scratchDir = $scratchDir
    configs = $configs; count = $configs.Count; newConfigs = $newConfigs; waitedSeconds = $waitedSeconds
    deployment = $deployment; deploymentError = $deploymentError
  })

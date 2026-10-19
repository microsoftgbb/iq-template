<#
  repoint_config.ps1 - point a plain DemoIQ config at this deployment's DemoIQ app.

  The seeder reads the app (client) ID from the config at run time, and the launcher
  refuses a run whose config names another app than the administrator's sign-in. After a
  redeployment (a new DemoIQ app) or for a config made elsewhere, this sets the identity
  fields from the deployment receipts (deploy.py status --json -> demoiq) instead of an
  agent hand-editing the file:
    existingAppId     <- receipts client_id (or -AppId)
    speechResourceId  <- receipts speech_resource_id (or -SpeechResourceId; kept when
                         neither names one)
  It also clears the two upstream credential fields this adaptation never uses
  (adminPwd, existingCertThumbprint). Nothing else changes. The file is rewritten
  atomically and owner-only.

  Params:
    -ConfigPath        the plain DemoIQ_config.json (updated in place unless -OutPath)
    -ReceiptsFile      (optional) a saved `deploy.py status --json` output instead of the runner
    -AppId             (optional) explicit app (client) ID, wins over the receipts
    -SpeechResourceId  (optional) explicit Speech/Foundry resource ID, wins over the receipts
    -OutPath           (optional) write the result here instead of in place; an existing
                       file is never replaced without -Force
    -Force             replace an existing -OutPath

  Output: ONE compact JSON line:
    { ok, configPath, changed{ existingAppId, speechResourceId, credentialFieldsCleared },
      before{ existingAppId, speechResourceId }, after{ existingAppId, speechResourceId } }
  Exit 0 on success; 2 on a usage/IO problem (missing or encrypted config, no app ID, an
  existing -OutPath without -Force).
#>
param(
  [Parameter(Mandatory = $true)][string]$ConfigPath,
  [string]$ReceiptsFile,
  [string]$AppId,
  [string]$SpeechResourceId,
  [string]$OutPath,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'platform.ps1')
function Out-Json($o) { $o | ConvertTo-Json -Compress -Depth 5 }
function Fail([string]$Message) { Out-Json ([ordered]@{ ok = $false; error = $Message }); exit 2 }

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { Fail "Config not found: $ConfigPath" }
if ($OutPath -and -not $Force -and (Test-Path -LiteralPath $OutPath) -and
    [System.IO.Path]::GetFullPath($OutPath) -ne [System.IO.Path]::GetFullPath($ConfigPath)) {
  Fail "$OutPath already exists; pick another -OutPath or pass -Force to replace it."
}
$config = $null
try { $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json } catch {}
if (-not $config) { Fail "The config is not valid JSON: $ConfigPath" }
if ($config.v -eq 'cdx-enc-v1') { Fail 'This config is encrypted. Convert it once with scripts/convert_config.ps1 (in your own terminal), then repoint the converted file.' }

if (-not $AppId -or -not $SpeechResourceId) {
  $receipts = $null
  try { $receipts = Get-DemoIQRunnerReceipts -ReceiptsFile $ReceiptsFile } catch {}
  if ($receipts) {
    if (-not $AppId) { $AppId = [string]$receipts.client_id }
    if (-not $SpeechResourceId) { $SpeechResourceId = [string]$receipts.speech_resource_id }
  }
}
if (-not $AppId) { Fail 'No app ID: the deployment receipts have no DemoIQ app yet (run deploy.py up --with-demoiq --through demoiq) and -AppId was not given.' }
if ($AppId -notmatch '^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$') { Fail "Not an app (client) ID: $AppId" }

$before = [ordered]@{ existingAppId = [string]$config.existingAppId; speechResourceId = [string]$config.speechResourceId }
$credentialFieldsCleared = (-not [string]::IsNullOrEmpty([string]$config.adminPwd)) -or (-not [string]::IsNullOrEmpty([string]$config.existingCertThumbprint))
$values = [ordered]@{ existingAppId = $AppId; adminPwd = ''; existingCertThumbprint = '' }
if ($SpeechResourceId) { $values.speechResourceId = $SpeechResourceId }
foreach ($key in $values.Keys) {
  if ($config.PSObject.Properties[$key]) { $config.$key = $values[$key] }
  else { $config | Add-Member -NotePropertyName $key -NotePropertyValue $values[$key] }
}

$target = if ($OutPath) { $OutPath } else { $ConfigPath }
$temp = "$target.tmp"
try {
  [System.IO.File]::WriteAllText($temp, '')
  Set-DemoIQPrivateFile $temp
  [System.IO.File]::WriteAllText($temp, ($config | ConvertTo-Json -Depth 10))
  Move-Item -LiteralPath $temp -Destination $target -Force
  Set-DemoIQPrivateFile $target
} catch {
  Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
  Fail "Could not write $target`: $($_.Exception.Message)"
}

$after = [ordered]@{ existingAppId = [string]$config.existingAppId; speechResourceId = [string]$config.speechResourceId }
Out-Json ([ordered]@{
    ok = $true; configPath = (Resolve-Path -LiteralPath $target).Path
    changed = [ordered]@{
      existingAppId = ($before.existingAppId -ne $after.existingAppId)
      speechResourceId = ($before.speechResourceId -ne $after.speechResourceId)
      credentialFieldsCleared = $credentialFieldsCleared
    }
    before = $before; after = $after
  })
exit 0

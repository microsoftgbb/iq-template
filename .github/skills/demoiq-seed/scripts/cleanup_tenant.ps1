<#
.SYNOPSIS
  Preview what a DemoIQ tenant cleanup would remove. Read-only dry-run: local files only,
  no sign-in, nothing deleted.

.DESCRIPTION
  cleanup_tenant.ps1 reads the seeder's own manifests (cleanup.config) and run logs on THIS
  machine and reports what cleaning up the last run (-Scope LastRun) or every run
  (-Scope AllRuns) would delete from the demo tenant: the Teams team with its channels,
  meetings with their residue, OneDrive files, seeded email threads and group/meeting chats.
  It never contacts the tenant, needs no sign-in and deletes nothing.

  Deletion runs as a CLEANUP RUN of the generated seeder, which behaves like a seed run: the
  tenant administrator signs in for that run (scripts/admin_signin.ps1), the seeder relaxes
  MFA/Conditional Access for the demo users exactly as for seeding, deletes with delegated
  tokens - the administrator's for the team and the chats, each demo user's own for their
  meetings, mail, files and chat list (scripts/cleanup_engine.ps1) - then restores MFA/CA:

      scripts/admin_signin.ps1
      scripts/launch_seeder.ps1 -Cleanup -Scope <LastRun|AllRuns>

  No certificate, app-only token or stored password is involved. -Execute is refused here.

  Plan source (the same engine the cleanup run uses, minus the administrator's OneDrive copy,
  which needs a sign-in):
    * -CleanupConfigPath, a manifest file you name; else
    * the manifests of -RunFolder, else of every seed run folder <ws>/runs/*/DemoIQ-Output
      (<ws> = ~/.copilot/DemoIQ-Output-Scout; cleanup run folders are skipped). LastRun uses
      the newest manifest that lists a run; AllRuns merges them all (de-duplicated by run);
    * the run logs (DemoIQ_*.log) when no manifest lists a run, or always with -FromLogs.
      Logs carry no file owner, so a log-sourced file is looked up in every demo user's drive.

.PARAMETER ConfigPath
  Optional plain DemoIQ config (DemoIQ_config.json). Supplies the administrator (always part
  of the sweep) and the demo users (u1upn..u5upn) when a manifest lists none. An encrypted
  upstream config (cdx-enc-v1) is refused: convert it once with scripts/convert_config.ps1.

.PARAMETER RunFolder
  A seed run folder (<ws>/runs/<runId>). Its DemoIQ-Output/cleanup.config is the manifest and
  its logs folder the log fallback.

.PARAMETER CleanupConfigPath
  A cleanup.config to preview instead of searching the run folders.

.PARAMETER Scope
  LastRun (default): the most recent run. AllRuns: every run the manifests (or logs) know.

.PARAMETER FromLogs
  Build the plan from the run logs instead of the manifests.

.PARAMETER LogsFolder
  The folder of DemoIQ_*.log files for the log fallback. Default: -RunFolder's logs folder,
  else every seed run folder's logs folder.

.PARAMETER Json
  Print the raw JSON object instead of the readable report.

.PARAMETER Execute
  Refused (exit 2): deletion runs as a cleanup run, see the description.

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/cleanup_tenant.ps1 -Scope LastRun

.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/cleanup_tenant.ps1 -RunFolder "<run folder>" -Scope AllRuns -Json

.OUTPUTS
  One JSON object (-Json) or a readable report:
  { ok, mode:"dry-run", scope, tenant, manifestSource, planSource, runsTargeted,
    totalRunsKnown, plan{ groups, meetings, files, emailSubjects, chats },
    byRun[{ when, groups, meetings, files, emails, chats }], note[, warnings] }
  or { ok:false, error }. Exit 0 on a preview (also when there is nothing to clean up),
  2 on refused or invalid input, 1 on an unexpected failure.
#>
param(
  [string]$ConfigPath,
  [string]$RunFolder,
  [string]$CleanupConfigPath,
  [ValidateSet('LastRun', 'AllRuns')][string]$Scope = 'LastRun',
  [switch]$FromLogs,
  [string]$LogsFolder,
  [switch]$Json,
  [switch]$Execute
)

# PowerShell 7 is required. Under Windows PowerShell 5.1, re-launch under pwsh with the same
# arguments.
if ($PSVersionTable.PSVersion.Major -lt 7) {
  $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
  if (-not $pwsh) {
    Write-Output '{"ok":false,"error":"PowerShell 7.4+ (pwsh) is required: winget install --id Microsoft.PowerShell -e"}'
    exit 1
  }
  $self = if ($PSCommandPath) { $PSCommandPath } else { $MyInvocation.MyCommand.Path }
  $fwd = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $self)
  foreach ($kv in $PSBoundParameters.GetEnumerator()) {
    if ($kv.Value -is [switch]) { if ($kv.Value.IsPresent) { $fwd += "-$($kv.Key)" } }
    else { $fwd += "-$($kv.Key)"; $fwd += [string]$kv.Value }
  }
  & $pwsh.Source @fwd
  exit $LASTEXITCODE
}

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'platform.ps1')
. (Join-Path $PSScriptRoot 'cleanup_engine.ps1')

function Out-Json($o) { $o | ConvertTo-Json -Compress -Depth 8 }

# Render a result: raw JSON with -Json, otherwise a readable report.
function Out-Result($o) {
  if ($Json) { Out-Json $o; return }
  $h = [ordered]@{}; foreach ($k in $o.Keys) { $h[$k] = $o[$k] }
  if (-not $h.ok) {
    Write-Host ''
    Write-Host "  Cleanup preview: $($h.error)" -ForegroundColor Red
    Write-Host ''
    return
  }
  Write-Host ''
  Write-Host '  DemoIQ Cleanup - PREVIEW (nothing deleted)' -ForegroundColor Cyan
  Write-Host "  Tenant: $($h.tenant)   Scope: $($h.scope)   Source: $($h.planSource)   Runs: $($h.runsTargeted)/$($h.totalRunsKnown)" -ForegroundColor DarkGray
  Write-Host "  Plan from: $($h.manifestSource)" -ForegroundColor DarkGray
  Write-Host ''
  if ($h.byRun) {
    $h.byRun | ForEach-Object {
      [pscustomobject]@{ When = $_.when; Teams = $_.groups; Meetings = $_.meetings; Files = $_.files; Emails = $_.emails; Chats = $_.chats }
    } | Format-Table -AutoSize | Out-Host
  }
  $p = $h.plan
  Write-Host "  TOTAL to remove:  Teams=$($p.groups)  Meetings=$($p.meetings)  Files=$($p.files)  Email threads=$($p.emailSubjects)  Chats=$($p.chats)" -ForegroundColor Yellow
  foreach ($w in @($h.warnings | Where-Object { $_ })) { Write-Host "  Note: $w" -ForegroundColor DarkYellow }
  Write-Host ''
  Write-Host '  Nothing was deleted. Deletion runs as a cleanup run that relaxes and restores MFA/CA like a seed:' -ForegroundColor DarkGray
  Write-Host "  sign in with scripts/admin_signin.ps1, then run scripts/launch_seeder.ps1 -Cleanup -Scope $($h.scope)." -ForegroundColor DarkGray
  Write-Host '  Chats are soft-deleted by the administrator (restorable for 7 days).' -ForegroundColor DarkGray
  Write-Host ''
}

function Stop-Preview([string]$Message, [int]$Code = 2) {
  Out-Result ([ordered]@{ ok = $false; error = $Message })
  exit $Code
}

$deleteHow = "Deletion runs as a cleanup run that relaxes and restores MFA/CA like a seed: sign in with scripts/admin_signin.ps1, then run scripts/launch_seeder.ps1 -Cleanup -Scope $Scope."
if ($Execute) { Stop-Preview $deleteHow }

try {
  # ---- the optional plain config: administrator + known demo users ----
  $adminUpn = $null
  $knownUsers = @()
  if ($ConfigPath) {
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { Stop-Preview "Config not found: $ConfigPath" }
    $cfg = $null
    try { $cfg = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json } catch {}
    if (-not $cfg) { Stop-Preview "Not a readable DemoIQ config (JSON): $ConfigPath" }
    if ($cfg.v -eq 'cdx-enc-v1') {
      Stop-Preview "This config is encrypted (an upstream DemoIQ config): $ConfigPath. Convert it once with scripts/convert_config.ps1, then preview with the converted plain config."
    }
    if ($cfg.adminUpn) { $adminUpn = ([string]$cfg.adminUpn).Trim() }
    foreach ($n in 1..5) { $u = [string]$cfg."u${n}upn"; if ($u.Trim()) { $knownUsers += $u.Trim() } }
    foreach ($u in @($cfg.users)) { if ($u -and $u.upn) { $knownUsers += ([string]$u.upn).Trim() } }
  }

  # ---- where to look: explicit file, a run folder, or every seed run folder ----
  $ws = Get-DemoIQWorkspace
  if ($CleanupConfigPath -and -not (Test-Path -LiteralPath $CleanupConfigPath -PathType Leaf)) { Stop-Preview "Cleanup manifest not found: $CleanupConfigPath" }
  if ($LogsFolder -and -not (Test-Path -LiteralPath $LogsFolder -PathType Container)) { Stop-Preview "Logs folder not found: $LogsFolder" }
  if ($RunFolder) {
    if (-not (Test-Path -LiteralPath $RunFolder -PathType Container)) { Stop-Preview "Run folder not found: $RunFolder" }
    $RunFolder = (Resolve-Path -LiteralPath $RunFolder).ProviderPath
    $searchFolders = @(Join-Path $RunFolder 'DemoIQ-Output')
    $defaultLogs = @(Join-Path $RunFolder 'logs')
  } else {
    $seedRuns = @(Get-DemoIQCleanupSeedRunFolders -Workspace $ws)
    $searchFolders = @($seedRuns | ForEach-Object { Join-Path $_.FullName 'DemoIQ-Output' })
    $defaultLogs = @(@($seedRuns | ForEach-Object { Join-Path $_.FullName 'logs' }) + @(Join-Path $ws 'logs'))
  }
  if ($CleanupConfigPath) { $searchFolders = @() }
  $logs = if ($LogsFolder) { @($LogsFolder) } else { $defaultLogs }

  # Local files only: no -AdminToken, so the administrator's OneDrive copy is never read.
  $info = Read-DemoIQCleanupManifest -CleanupConfigPath $CleanupConfigPath -SearchFolders $searchFolders `
    -LogsFolder $logs -FromLogs:$FromLogs -Scope $Scope 6>$null

  $manifest = if ($info) { $info.Manifest } else { $null }
  $lastRun = @(Get-DemoIQCleanupManifestRuns $manifest) | Select-Object -Last 1
  if (-not $adminUpn -and $manifest -and $manifest.tenantConfig -and $manifest.tenantConfig.adminUpn) { $adminUpn = [string]$manifest.tenantConfig.adminUpn }
  if (-not $adminUpn -and $lastRun -and $lastRun.adminUpn) { $adminUpn = [string]$lastRun.adminUpn }
  $tenant = if ($adminUpn -and $adminUpn -match '@') { $adminUpn.Split('@')[1].ToLowerInvariant() }
  elseif ($manifest -and $manifest.tenantConfig -and $manifest.tenantConfig.tenantId) { [string]$manifest.tenantConfig.tenantId }
  elseif ($lastRun -and $lastRun.tenantId) { [string]$lastRun.tenantId }
  else { $null }

  if (-not $info) {
    Out-Result ([ordered]@{ ok = $false; error = 'No seed manifest and no usable run logs found. Nothing to clean up.'; tenant = $tenant })
    exit 0
  }

  $planInfo = New-DemoIQCleanupPlan -Manifest $info.Manifest -Scope $Scope -AdminUpn $adminUpn -KnownUsers $knownUsers
  $result = [ordered]@{
    ok             = $true
    mode           = 'dry-run'
    scope          = $Scope
    tenant         = $tenant
    manifestSource = $info.Source
    planSource     = $info.PlanSource
    runsTargeted   = @($planInfo.TargetRuns).Count
    totalRunsKnown = @($planInfo.AllRuns).Count
    plan           = $planInfo.Counts
    byRun          = @($planInfo.ByRun)
    note           = "Dry-run only - nothing was deleted; this preview read local manifests and logs and never contacted the tenant. $deleteHow Teams, meetings and files delete cleanly; emails are best-effort (some replies may remain); group and meeting chats are soft-deleted by the administrator (restorable for 7 days)."
  }
  if (@($info.Errors).Count -gt 0) { $result.warnings = @($info.Errors) }
  Out-Result $result
  exit 0
}
catch {
  Out-Result ([ordered]@{ ok = $false; error = "Cleanup preview failed: $($_.Exception.Message)" })
  exit 1
}

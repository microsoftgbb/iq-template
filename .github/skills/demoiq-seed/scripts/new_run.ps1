<#
  new_run.ps1 - create a fresh per-run folder + initial run.json manifest (Flow 1d step 6).

  Every seeding run gets its OWN folder under the workspace:

      <$ws>\runs\<yyyyMMdd_HHmm>__<scenario-slug>__<tenant-short>\
      <$ws>\runs\<yyyyMMdd_HHmm>__<scenario-slug>__<tenant-short>-cleanup\   (-Kind cleanup)

  The agent generates the run's DemoIQ.ps1 INTO this folder (generate.js --out-dir),
  and because the seeder writes everything beside itself ($PSScriptRoot), the run's
  DemoIQ-Output\ and logs\ land here too - so one run never overwrites another and
  "clean up run X" / "show past runs" can address runs individually.

  run.json is the run's manifest: this script writes the creation fields; the
  launcher (launch_seeder.ps1) merges in the launch fields (launchedAt, runMode,
  processId). Outcome/counts stay derivable from the run's own transcript log
  (run_history.ps1 parses it), so nothing needs to write to the manifest while
  the seeder runs. skillVersion records the VERSION file at run creation - which
  skill build made this run.

  Params:
    -ScenarioTitle  the run's scenario title (used for the folder slug + manifest).
                    Falls back to 'demo' when omitted.
    -Tenant         the target tenant domain (e.g. contoso.onmicrosoft.com). The
                    first DNS label becomes the folder's tenant-short segment.
    -ConfigPath     (optional) path of the plain config driving this run
                    (recorded in run.json).
    -Kind           seed (default) | cleanup. A cleanup run (it deletes seeded content
                    and restores the tenant, see launch_seeder.ps1 -Cleanup) gets its own
                    folder, suffixed -cleanup, so it never mixes with a seed run's output.
    -OutputFolder   the workspace $ws (default ~\.copilot\DemoIQ-Output-Scout).

  Output: ONE compact JSON line:
    { ok, runId, runFolder, runJson }
#>
param(
  [string]$ScenarioTitle,
  [string]$Tenant,
  [string]$ConfigPath,
  [string]$OutputFolder,
  [ValidateSet('seed', 'cleanup')][string]$Kind = 'seed'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'version_shared.ps1')
. (Join-Path $PSScriptRoot 'platform.ps1')
function Out-Json($o) { $o | ConvertTo-Json -Compress -Depth 5 }

if (-not $OutputFolder) { $OutputFolder = Join-Path (Join-Path $HOME '.copilot') 'DemoIQ-Output-Scout' }
$runsRoot = Join-Path $OutputFolder 'runs'
$runsRoot = New-DemoIQPrivateDirectory $runsRoot

# Slug: lowercase ASCII, everything else collapsed to single hyphens, bounded length.
function To-Slug([string]$s, [int]$max) {
  if (-not $s) { return '' }
  $slug = $s.ToLowerInvariant() -replace '[^a-z0-9]+', '-'
  $slug = $slug.Trim('-')
  if ($slug.Length -gt $max) { $slug = $slug.Substring(0, $max).Trim('-') }
  return $slug
}

$scenarioSlug = To-Slug $ScenarioTitle 40
if (-not $scenarioSlug) { $scenarioSlug = 'demo' }
$tenantShort = ''
if ($Tenant) { $tenantShort = To-Slug (($Tenant -split '\.')[0]) 24 }

$suffix = if ($Kind -eq 'cleanup') { '-cleanup' } else { '' }
$stamp = Get-Date -Format 'yyyyMMdd_HHmm'
$runId = $(if ($tenantShort) { "${stamp}__${scenarioSlug}__${tenantShort}" } else { "${stamp}__${scenarioSlug}" }) + $suffix
$runFolder = Join-Path $runsRoot $runId
if (Test-Path $runFolder) {
  # Same scenario+tenant within the same minute: disambiguate with seconds.
  $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
  $runId = $(if ($tenantShort) { "${stamp}__${scenarioSlug}__${tenantShort}" } else { "${stamp}__${scenarioSlug}" }) + $suffix
  $runFolder = Join-Path $runsRoot $runId
}
$null = New-DemoIQPrivateDirectory $runFolder
# Pre-create the run's expected output subfolders. The seeder makes them itself at
# run time, but PRE-seed writers target them too (the content preview writes
# DemoIQ-Output\content_preview.html at the 1d approval gate) - a missing folder
# there failed a live preview write. Empty pre-created folders are harmless to the
# seeder/monitor (both create-with -Force / glob newest).
New-Item -ItemType Directory -Force -Path (Join-Path $runFolder 'DemoIQ-Output') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $runFolder 'logs') | Out-Null

$manifest = [ordered]@{
  runId         = $runId
  kind          = $Kind
  createdAt     = (Get-Date).ToString('s')
  skillVersion  = (Get-SkillVersion)
  scenarioTitle = $ScenarioTitle
  tenant        = $Tenant
  configFile    = $ConfigPath
  status        = 'created'
}
$runJson = Join-Path $runFolder 'run.json'
($manifest | ConvertTo-Json -Depth 4) | Set-Content -Path $runJson -Encoding UTF8

Out-Json ([ordered]@{ ok = $true; runId = $runId; runFolder = $runFolder; runJson = $runJson })

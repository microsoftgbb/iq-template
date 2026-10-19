<#
  version.ps1 - print the skill's version, and prove every mirror agrees.

  Usage:
    pwsh -NoProfile -File scripts/version.ps1            # one JSON line (report)
    pwsh -NoProfile -File scripts/version.ps1 -Check     # same, exit 1 on any drift
    pwsh -NoProfile -File scripts/version.ps1 -Short     # just the version string

  Report: { ok, skill, displayName, version, sources{ VERSION, "SKILL.md frontmatter",
            "SKILL.md version line", "CHANGELOG.md top entry", "<builder> meta",
            "<builder> stamp", "README.md" }, problems[], git{ repo, tag, tagged,
            describe, dirty, head }, root }
  `ok` is true only when every source equals VERSION and VERSION is YYYY-MM-DD.N.
  The git block is informational (tagged = a `v<version>` tag exists locally).

  -Root <path>  operate on another copy of the skill (tests use a temp copy).
#>
param(
  [string]$Root,
  [switch]$Check,
  [switch]$Short
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'version_shared.ps1')

$report = Get-SkillVersionReport $Root
if ($Short) {
  if ($report.version) { Write-Output $report.version }
  exit $(if ($report.ok) { 0 } else { 1 })
}
$report | ConvertTo-Json -Depth 6 -Compress
if ($Check -and -not $report.ok) { exit 1 }
exit 0

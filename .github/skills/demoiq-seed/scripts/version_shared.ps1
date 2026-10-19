# version_shared.ps1 - the skill's version contract, shared by version.ps1,
# bump_version.ps1, package_release.ps1, new_run.ps1 and status.ps1.
# Dot-source it:   . (Join-Path $PSScriptRoot 'version_shared.ps1')
#
# ONE version, `YYYY-MM-DD.N` (release day + same-day sequence), lives in the
# skill root's `VERSION` file and is MIRRORED - never hand-edited - into the places
# below. In the GitHub Copilot adaptation (demoiq-seed) it is the DemoIQ-Scout release
# the adaptation is based on; SKILL.md frontmatter carries only name + description (the
# GitHub Copilot skill schema), so the version lives in its prose line:
#   SKILL.md prose              - **Version: <v>**
#   CHANGELOG.md                the first "## [<v>] - <date>" entry (below Unreleased);
#                               MAINTAINER-ONLY: .gitattributes export-ignore keeps it,
#                               bump_version.ps1 and package_release.ps1 out of the zip
#   *Config-Builder.html        <meta name="demoiq-skill-version" content="<v>">
#                               + <span class="skill-version">v<v></span>
#   README.md (when present)    **Version:** <v>
#   git                         tag v<v>
# Get-SkillVersion is the cheap runtime read (VERSION only); Get-SkillVersionReport
# compares every mirror; Set-SkillVersion stamps them all; Add-SkillChangelogEntry
# turns the Unreleased notes into a released entry. No Set-StrictMode here: this
# file is dot-sourced into scripts that must keep their own strictness.

$script:DemoIQVersionRe = '^\d{4}-\d{2}-\d{2}\.\d+$'
$script:DemoIQSkillRootDefault = Split-Path $PSScriptRoot -Parent

function Get-SkillRoot([string]$Root) {
  if ($Root) { return (Resolve-Path -LiteralPath $Root).Path }
  return $script:DemoIQSkillRootDefault
}

function Test-SkillVersionFormat([string]$Version) {
  return [bool]($Version -and $Version -match $script:DemoIQVersionRe)
}

function Compare-SkillVersion([string]$A, [string]$B) {
  # -1 when A < B, 0 when equal, 1 when A > B. Date part ordinal, then N numeric.
  $da, $na = $A.Split('.', 2); $db, $nb = $B.Split('.', 2)
  $c = [string]::CompareOrdinal($da, $db)
  if ($c -ne 0) { return [math]::Sign($c) }
  return [math]::Sign([int]$na - [int]$nb)
}

function Get-SkillDisplayName([string]$Name) {
  switch ($Name) {
    'demoiq-seed'           { return 'DemoIQ for GitHub Copilot' }
    'demoiq-scout'          { return 'DemoIQ-Scout' }
    'demoiq-scout-dynamics' { return 'DemoIQ-Scout-Dynamics' }
    default                 { return $Name }
  }
}

function Get-SkillVersion([string]$Root) {
  # The cheap runtime read: the VERSION file only. $null when the file is missing.
  $f = Join-Path (Get-SkillRoot $Root) 'VERSION'
  if (-not (Test-Path -LiteralPath $f)) { return $null }
  $v = ([IO.File]::ReadAllText($f)).Trim()
  if ($v) { return $v } else { return $null }
}

function Read-SkillText([string]$Path) { return [IO.File]::ReadAllText($Path) }
function Write-SkillText([string]$Path, [string]$Text) {
  [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}

function Get-SkillBuilderPath([string]$Root) {
  $b = Get-ChildItem -LiteralPath (Get-SkillRoot $Root) -Filter '*Config-Builder.html' -File | Select-Object -First 1
  if ($b) { return $b.FullName } else { return $null }
}

function Get-SkillVersionSources([string]$Root) {
  # Every place the version is mirrored -> the value found there ($null = absent).
  $root = Get-SkillRoot $Root
  $src = [ordered]@{}
  $src.VERSION = Get-SkillVersion $root

  $skillMd = Join-Path $root 'SKILL.md'
  $md = if (Test-Path -LiteralPath $skillMd) { Read-SkillText $skillMd } else { '' }
  $src.'SKILL.md version line' = if ($md -match '(?m)^- \*\*Version: (\S+)\*\*') { $Matches[1] } else { $null }

  # CHANGELOG.md is maintainer-only (export-ignore keeps it out of the distribution zip),
  # so it is a mirror only where it exists.
  $cl = Join-Path $root 'CHANGELOG.md'
  if (Test-Path -LiteralPath $cl) {
    $src.'CHANGELOG.md top entry' = if ((Read-SkillText $cl) -match '(?m)^## \[(\d{4}-\d{2}-\d{2}\.\d+)\]') { $Matches[1] } else { $null }
  }

  $builder = Get-SkillBuilderPath $root
  if ($builder) {
    $h = Read-SkillText $builder
    $meta = if ($h -match '<meta name="demoiq-skill-version" content="([^"]+)"') { $Matches[1] } else { $null }
    $span = if ($h -match '<span class="skill-version"[^>]*>v([^<]+)</span>') { $Matches[1] } else { $null }
    $src[(Split-Path $builder -Leaf) + ' meta'] = $meta
    $src[(Split-Path $builder -Leaf) + ' stamp'] = $span
  }

  $readme = Join-Path $root 'README.md'
  if (Test-Path -LiteralPath $readme) {
    $src.'README.md' = if ((Read-SkillText $readme) -match '(?m)^\*\*Version:\*\* (\S+)') { $Matches[1] } else { $null }
  }
  return $src
}

function Invoke-SkillGit([string]$Root, [string[]]$GitArgs) {
  # git without letting a non-zero exit or stderr line trip a caller's -ErrorAction Stop.
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return [ordered]@{ code = 127; out = @() } }
  $prevEA = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  $prevNative = $null
  if (Test-Path variable:global:PSNativeCommandUseErrorActionPreference) {
    $prevNative = $global:PSNativeCommandUseErrorActionPreference; $global:PSNativeCommandUseErrorActionPreference = $false
  }
  try {
    $out = & git -C $Root @GitArgs 2>$null
    return [ordered]@{ code = $LASTEXITCODE; out = @($out | ForEach-Object { "$_" }) }
  } finally {
    $ErrorActionPreference = $prevEA
    if ($null -ne $prevNative) { $global:PSNativeCommandUseErrorActionPreference = $prevNative }
  }
}

function Get-SkillGitState([string]$Root, [string]$Version) {
  $git = [ordered]@{ repo = $false; tag = $null; tagged = $false; describe = $null; dirty = $null; head = $null }
  $r = Invoke-SkillGit $Root @('rev-parse', '--is-inside-work-tree')
  if ($r.code -ne 0 -or ($r.out -join '') -ne 'true') { return $git }
  $git.repo = $true
  $git.head = ((Invoke-SkillGit $Root @('rev-parse', '--short', 'HEAD')).out -join '')
  if ($Version) {
    $git.tag = "v$Version"
    $git.tagged = [bool]((Invoke-SkillGit $Root @('tag', '--list', "v$Version")).out -join '')
  }
  $git.describe = ((Invoke-SkillGit $Root @('describe', '--tags', '--always', '--dirty')).out -join '')
  $git.dirty = [bool]((Invoke-SkillGit $Root @('status', '--porcelain')).out -join '')
  return $git
}

function Get-SkillVersionReport([string]$Root) {
  $root = Get-SkillRoot $Root
  $skillMd = Join-Path $root 'SKILL.md'
  $name = if ((Test-Path -LiteralPath $skillMd) -and ((Read-SkillText $skillMd) -match '(?m)^name:\s*(\S+)')) { $Matches[1] } else { Split-Path $root -Leaf }
  $src = Get-SkillVersionSources $root
  $current = $src.VERSION
  $problems = @()
  if (-not $current) { $problems += 'VERSION file missing or empty' }
  elseif (-not (Test-SkillVersionFormat $current)) { $problems += "VERSION '$current' is not YYYY-MM-DD.N" }
  foreach ($k in @($src.Keys)) {
    if ($k -eq 'VERSION') { continue }
    if ($src[$k] -ne $current) { $problems += "$k = '$($src[$k])' (expected '$current')" }
  }
  $git = Get-SkillGitState $root $current
  return [ordered]@{
    ok = ($problems.Count -eq 0); skill = $name; displayName = (Get-SkillDisplayName $name)
    version = $current; sources = $src; problems = $problems; git = $git; root = $root
  }
}

function Set-SkillVersion([string]$Root, [string]$Version) {
  # Stamp every mirror (CHANGELOG.md excluded - see Add-SkillChangelogEntry). Returns the files touched.
  if (-not (Test-SkillVersionFormat $Version)) { throw "Version '$Version' is not YYYY-MM-DD.N" }
  $root = Get-SkillRoot $Root
  $touched = @()

  Write-SkillText (Join-Path $root 'VERSION') ($Version + "`n"); $touched += 'VERSION'

  $skillMd = Join-Path $root 'SKILL.md'
  $md = Read-SkillText $skillMd
  if ($md -notmatch '(?m)^- \*\*Version: \S+\*\*') { throw 'SKILL.md has no "- **Version: <v>**" line' }
  $md = [regex]::Replace($md, '(?m)^- \*\*Version: \S+\*\*', "- **Version: $Version**")
  Write-SkillText $skillMd $md; $touched += 'SKILL.md'

  $builder = Get-SkillBuilderPath $root
  if ($builder) {
    $h = Read-SkillText $builder
    if ($h -notmatch '<meta name="demoiq-skill-version" content="[^"]+"') { throw "$(Split-Path $builder -Leaf) has no demoiq-skill-version meta tag" }
    $h = [regex]::Replace($h, '<meta name="demoiq-skill-version" content="[^"]+"', "<meta name=`"demoiq-skill-version`" content=`"$Version`"")
    $h = [regex]::Replace($h, '(<span class="skill-version"[^>]*>)v[^<]+(</span>)', ('$1v' + $Version + '$2'))
    Write-SkillText $builder $h; $touched += (Split-Path $builder -Leaf)
  }

  $readme = Join-Path $root 'README.md'
  if ((Test-Path -LiteralPath $readme) -and ((Read-SkillText $readme) -match '(?m)^\*\*Version:\*\* \S+')) {
    Write-SkillText $readme ([regex]::Replace((Read-SkillText $readme), '(?m)^\*\*Version:\*\* \S+', "**Version:** $Version")); $touched += 'README.md'
  }
  return $touched
}

function Get-SkillChangelogUnreleased([string]$Root) {
  $path = Join-Path (Get-SkillRoot $Root) 'CHANGELOG.md'
  if (-not (Test-Path -LiteralPath $path)) { return $null }
  $m = [regex]::Match((Read-SkillText $path), '(?ms)^## \[Unreleased\][ \t]*\r?$(.*?)(?=^## \[|\z)')
  if (-not $m.Success) { return $null }
  return $m.Groups[1].Value.Trim()
}

function Add-SkillChangelogEntry([string]$Root, [string]$Version, [string[]]$Lines) {
  # Moves the Unreleased notes (plus $Lines, first) into a new "## [<v>] - <date>" entry.
  $path = Join-Path (Get-SkillRoot $Root) 'CHANGELOG.md'
  if (-not (Test-Path -LiteralPath $path)) { throw 'CHANGELOG.md is missing' }
  $text = Read-SkillText $path
  $nl = if ($text -match "`r`n") { "`r`n" } else { "`n" }
  $m = [regex]::Match($text, '(?ms)^## \[Unreleased\][ \t]*\r?$(.*?)(?=^## \[|\z)')
  if (-not $m.Success) { throw 'CHANGELOG.md has no "## [Unreleased]" section' }
  if ($text -match ('(?m)^## \[' + [regex]::Escape($Version) + '\]')) { throw "CHANGELOG.md already has an entry for $Version" }
  $pending = $m.Groups[1].Value.Trim()
  $body = @()
  foreach ($l in @($Lines)) { if ($null -ne $l -and $l.Trim()) { $body += $l.Trim() } }
  if ($pending) { $body += $pending }
  if (-not $body) { throw "Nothing to release: add notes under '## [Unreleased]' in CHANGELOG.md or pass -Summary / -Notes" }
  $entry = "## [Unreleased]$nl$nl## [$Version] - $($Version.Substring(0, 10))$nl$nl" + (($body -join ($nl + $nl)).Trim()) + $nl + $nl
  $new = $text.Substring(0, $m.Index) + $entry + $text.Substring($m.Index + $m.Length)
  Write-SkillText $path $new
}

function Get-SkillNextVersion([string]$Root, [datetime]$Day = (Get-Date)) {
  # Today's next sequence number, counting VERSION and every CHANGELOG entry dated today.
  $root = Get-SkillRoot $Root
  $today = $Day.ToString('yyyy-MM-dd')
  $seen = @()
  $cur = Get-SkillVersion $root
  if ($cur -and $cur.StartsWith($today + '.')) { $seen += [int]$cur.Split('.', 2)[1] }
  $cl = Join-Path $root 'CHANGELOG.md'
  if (Test-Path -LiteralPath $cl) {
    foreach ($mm in [regex]::Matches((Read-SkillText $cl), ('(?m)^## \[' + [regex]::Escape($today) + '\.(\d+)\]'))) { $seen += [int]$mm.Groups[1].Value }
  }
  $n = if ($seen) { ($seen | Measure-Object -Maximum).Maximum + 1 } else { 1 }
  return "$today.$n"
}

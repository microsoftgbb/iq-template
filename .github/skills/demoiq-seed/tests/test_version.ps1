# test_version.ps1 - the version contract, offline.
#
# (1) the SHIPPED skill is consistent: VERSION == SKILL.md version line == CHANGELOG.md
#     top entry == Config Builder meta + stamp (== README when present), VERSION is
#     YYYY-MM-DD.N, and the SKILL.md frontmatter follows the GitHub Copilot skill schema
#     (name == folder name, description; no version key);
# (2) bump_version.ps1 on a TEMP COPY of the stamped files: -DryRun changes nothing,
#     -Next picks today's next N, a real bump stamps every mirror and moves the
#     Unreleased notes (summary first) into the new entry, and it REFUSES an equal /
#     older / malformed version, an empty release, and -Commit outside a git repo;
# (3) run manifests carry skillVersion (new_run.ps1 into a temp folder) and the
#     health check + test runner reference the contract.
# (1) and (3) run everywhere; (2) and the changelog checks only on a MAINTAINER checkout
# (CHANGELOG.md + bump_version.ps1 present) - the distribution zip ships neither.
# Never touches the real skill files: every write lands in a temp folder.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$S = Join-Path $root 'scripts'
$fails = @()
function Check($name, $cond) { if ($cond) { Write-Host "  PASS $name" } else { Write-Host "  FAIL $name"; $script:fails += $name } }
function JsonLine($raw) { return (@($raw | Where-Object { "$_" -match '^\s*\{' }) | Select-Object -Last 1 | ConvertFrom-Json) }
function Run([string[]]$a) { $raw = & pwsh -NoProfile -ExecutionPolicy Bypass @a 2>&1; return @{ code = $LASTEXITCODE; json = (JsonLine $raw); raw = ($raw -join "`n") } }

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("skill-version-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $tmp | Out-Null
try {
  # ---- (1) the shipped skill --------------------------------------------------------
  $rep = Run @('-File', (Join-Path $S 'version.ps1'), '-Check')
  Check 'version.ps1 -Check: ok, exit 0' ($rep.code -eq 0 -and $rep.json.ok)
  $v = $rep.json.version
  Check "VERSION is YYYY-MM-DD.N ($v)" ($v -match '^\d{4}-\d{2}-\d{2}\.\d+$')
  $srcNames = @($rep.json.sources.PSObject.Properties.Name)
  $maintainer = (Test-Path (Join-Path $root 'CHANGELOG.md')) -and (Test-Path (Join-Path $S 'bump_version.ps1'))
  $skillText = Get-Content (Join-Path $root 'SKILL.md') -Raw
  $fm = if ($skillText -match '(?s)\A---\r?\n(.*?)\r?\n---') { $Matches[1] } else { '' }
  $fmKeys = @([regex]::Matches($fm, '(?m)^([A-Za-z-]+):') | ForEach-Object { $_.Groups[1].Value })
  Check 'SKILL.md frontmatter is name + description only (GitHub Copilot skill schema)' ((($fmKeys | Sort-Object) -join ',') -eq 'description,name')
  Check 'SKILL.md name equals its folder name' ($fm -match ('(?m)^name:\s*' + [regex]::Escape((Split-Path $root -Leaf)) + '\s*$'))
  Check 'sources cover the version line, builder meta + stamp (+ changelog on a maintainer checkout)' (($srcNames -notcontains 'SKILL.md frontmatter') -and ($srcNames -contains 'SKILL.md version line') -and (($srcNames -contains 'CHANGELOG.md top entry') -eq $maintainer) -and (@($srcNames | Where-Object { $_ -like '* meta' }).Count -eq 1) -and (@($srcNames | Where-Object { $_ -like '* stamp' }).Count -eq 1))
  Check 'every source equals VERSION' (@($rep.json.sources.PSObject.Properties | Where-Object { $_.Value -ne $v }).Count -eq 0)
  $short = & pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $S 'version.ps1') -Short
  Check '-Short prints the bare version' ("$short".Trim() -eq $v)
  if (-not $maintainer) { Write-Host '  SKIP maintainer-only checks (no CHANGELOG.md / bump_version.ps1 - a distribution copy)' }
  else {
  $cl = Get-Content (Join-Path $root 'CHANGELOG.md') -Raw
  Check 'CHANGELOG.md has an Unreleased section above the top entry' ($cl -match '(?ms)^## \[Unreleased\].*?^## \[' + [regex]::Escape($v) + '\] - ' + [regex]::Escape($v.Substring(0, 10)))
  Check 'CHANGELOG.md entries are newest first' ($(
    $vs = @([regex]::Matches($cl, '(?m)^## \[(\d{4}-\d{2}-\d{2}\.\d+)\]') | ForEach-Object { $_.Groups[1].Value })
    $sorted = @($vs | Sort-Object { $_.Substring(0, 10) }, { [int]$_.Split('.', 2)[1] } -Descending)
    ($vs -join ',') -eq ($sorted -join ',') -and $vs.Count -ge 1))
  }
  Check 'SKILL.md still says to state the version when asked' ((Get-Content (Join-Path $root 'SKILL.md') -Raw) -match 'State this version when')
  Check 'no change history ships in SKILL.md (one version line, no Prior version entries)' (([regex]::Matches((Get-Content (Join-Path $root 'SKILL.md') -Raw), '(?m)^- \*\*Version: ')).Count -eq 1 -and (Get-Content (Join-Path $root 'SKILL.md') -Raw) -notmatch 'Prior version 20')

  # ---- (2) bump on a temp copy (maintainer checkout only) --------------------------
  if ($maintainer) {
  $copy = Join-Path $tmp 'skill'; New-Item -ItemType Directory -Force $copy | Out-Null
  foreach ($f in @('VERSION', 'SKILL.md', 'CHANGELOG.md', 'README.md')) { if (Test-Path (Join-Path $root $f)) { Copy-Item (Join-Path $root $f) $copy } }
  Get-ChildItem $root -Filter '*Config-Builder.html' -File | Copy-Item -Destination $copy
  # queue a note under Unreleased in the copy (LF or CRLF, whichever the file uses)
  $clPath = Join-Path $copy 'CHANGELOG.md'; $clText = [IO.File]::ReadAllText($clPath)
  $nl = if ($clText -match "`r`n") { "`r`n" } else { "`n" }
  $clText = $clText -replace '(?m)^## \[Unreleased\][ \t]*\r?$', ('## [Unreleased]' + $nl + $nl + '- queued note from the test')
  [IO.File]::WriteAllText($clPath, $clText, [Text.UTF8Encoding]::new($false))

  $today = (Get-Date).ToString('yyyy-MM-dd')
  $dry = Run @('-File', (Join-Path $S 'bump_version.ps1'), '-Root', $copy, '-Next', '-Summary', 'dry run', '-DryRun')
  Check '-DryRun: ok, reports from/to, changes nothing' ($dry.code -eq 0 -and $dry.json.ok -and $dry.json.dryRun -and $dry.json.from -eq $v -and $dry.json.to -like "$today.*" -and ((Get-Content (Join-Path $copy 'VERSION') -Raw).Trim() -eq $v))
  $next = $dry.json.to
  $expectN = if ($v.StartsWith("$today.")) { [int]$v.Split('.', 2)[1] + 1 } else { 1 }
  Check "-Next picks today's next N ($next)" ($next -eq "$today.$expectN")

  $b = Run @('-File', (Join-Path $S 'bump_version.ps1'), '-Root', $copy, '-Version', $next, '-Summary', 'Test release summary', '-Notes', 'second line')
  Check 'bump: ok, from -> to, files stamped' ($b.code -eq 0 -and $b.json.ok -and $b.json.from -eq $v -and $b.json.to -eq $next -and (@($b.json.files) -contains 'VERSION') -and (@($b.json.files) -contains 'SKILL.md') -and (@($b.json.files) -contains 'CHANGELOG.md'))
  $chk = Run @('-File', (Join-Path $S 'version.ps1'), '-Root', $copy, '-Check')
  Check 'after bump: every mirror in the copy equals the new version' ($chk.code -eq 0 -and $chk.json.ok -and $chk.json.version -eq $next)
  $cl2 = [IO.File]::ReadAllText($clPath)
  $entry = [regex]::Match($cl2, '(?ms)^## \[' + [regex]::Escape($next) + '\] - ' + [regex]::Escape($today) + '\r?$(.*?)(?=^## \[|\z)')
  Check 'CHANGELOG: new entry holds summary, notes, then the queued note; Unreleased is empty again' ($entry.Success -and $entry.Groups[1].Value -match '(?s)Test release summary.*second line.*queued note from the test' -and ($cl2 -match '(?ms)^## \[Unreleased\][ \t]*\r?\n\s*## \['))
  Check 'CHANGELOG: line endings preserved' ($(if ($nl -eq "`r`n") { $cl2 -notmatch "[^`r]`n" } else { $cl2 -notmatch "`r" }))
  $skillCopy = [IO.File]::ReadAllText((Join-Path $copy 'SKILL.md'))
  Check 'SKILL.md: frontmatter version: under name:, exactly once' (([regex]::Matches($skillCopy, '(?m)^version: ')).Count -eq 1 -and $skillCopy -match '(?m)^name: \S+\r?\nversion: ' + [regex]::Escape($next))
  Check 'SKILL.md: version line updated' ($skillCopy -match ('(?m)^- \*\*Version: ' + [regex]::Escape($next) + '\*\*'))
  $builderCopy = Get-ChildItem $copy -Filter '*Config-Builder.html' | Select-Object -First 1
  $bh = [IO.File]::ReadAllText($builderCopy.FullName)
  Check 'Builder: meta + visible stamp updated' ($bh -match ('<meta name="demoiq-skill-version" content="' + [regex]::Escape($next) + '"') -and $bh -match ('<span class="skill-version"[^>]*>v' + [regex]::Escape($next) + '</span>'))

  # refusals - none of these may change the copy
  $snap = (Get-Content (Join-Path $copy 'VERSION') -Raw).Trim()
  $r1 = Run @('-File', (Join-Path $S 'bump_version.ps1'), '-Root', $copy, '-Version', $next, '-Summary', 'x')
  Check 'refuses an equal version' ($r1.code -eq 1 -and -not $r1.json.ok -and $r1.json.error -match 'not newer')
  $r2 = Run @('-File', (Join-Path $S 'bump_version.ps1'), '-Root', $copy, '-Version', '2020-01-01.1', '-Summary', 'x')
  Check 'refuses an older version' ($r2.code -eq 1 -and $r2.json.error -match 'not newer')
  $r3 = Run @('-File', (Join-Path $S 'bump_version.ps1'), '-Root', $copy, '-Version', '2.8.2', '-Summary', 'x')
  Check 'refuses a malformed version' ($r3.code -eq 1 -and $r3.json.error -match 'YYYY-MM-DD')
  $r4 = Run @('-File', (Join-Path $S 'bump_version.ps1'), '-Root', $copy, '-Next')
  Check 'refuses an empty release (no summary, nothing queued)' ($r4.code -eq 1 -and $r4.json.error -match 'Nothing to release')
  $r5 = Run @('-File', (Join-Path $S 'bump_version.ps1'), '-Root', $copy, '-Next', '-DryRun', '-Version', '2030-01-01.1')
  Check 'refuses -Next together with -Version' ($r5.code -eq 1 -and $r5.json.error -match 'not both')
  Check 'refusals changed nothing' (((Get-Content (Join-Path $copy 'VERSION') -Raw).Trim() -eq $snap) -and ([IO.File]::ReadAllText($clPath) -eq $cl2))
  $r6 = Run @('-File', (Join-Path $S 'bump_version.ps1'), '-Root', $copy, '-Next', '-Summary', 'needs git', '-Commit')
  Check '-Commit outside a git repository fails loudly' ($r6.code -eq 1 -and $r6.json.error -match 'not a git repository')
  # drift is caught: break one mirror and -Check must fail naming it
  [IO.File]::WriteAllText((Join-Path $copy 'VERSION'), "2031-01-01.1`n", [Text.UTF8Encoding]::new($false))
  $drift = Run @('-File', (Join-Path $S 'version.ps1'), '-Root', $copy, '-Check')
  Check 'drift: -Check exits 1 and names every mirror that disagrees' ($drift.code -eq 1 -and -not $drift.json.ok -and @($drift.json.problems).Count -ge 4 -and (($drift.json.problems -join ' ') -match 'SKILL.md frontmatter'))
  $r7 = Run @('-File', (Join-Path $S 'bump_version.ps1'), '-Root', $copy, '-Next', '-Summary', 'x')
  Check 'bump refuses to run on top of drift' ($r7.code -eq 1 -and $r7.json.error -match 'inconsistent')
  }

  # ---- (3) runtime consumers -------------------------------------------------------
  $skillName = if ((Get-Content (Join-Path $root 'SKILL.md') -Raw) -match '(?m)^name:\s*(\S+)') { $Matches[1] } else { '' }
  $runWs = Join-Path $tmp 'ws'; New-Item -ItemType Directory -Force $runWs | Out-Null
  $nrArgs = if ($skillName -eq 'demoiq-scout-dynamics') { @('-ConfigName', 'Version Test', '-EnvUrl', 'https://org-test.crm.dynamics.com', '-Tenant', 'test.onmicrosoft.com', '-OutputFolder', $runWs) }
            else { @('-ScenarioTitle', 'Version Test', '-Tenant', 'test.onmicrosoft.com', '-OutputFolder', $runWs) }
  $nr = Run (@('-File', (Join-Path $S 'new_run.ps1')) + $nrArgs)
  $manifest = if ($nr.json -and $nr.json.runJson -and (Test-Path $nr.json.runJson)) { Get-Content $nr.json.runJson -Raw | ConvertFrom-Json } else { $null }
  Check "new_run.ps1: run.json carries skillVersion = $v" ($nr.code -eq 0 -and $manifest -and $manifest.skillVersion -eq $v)
  Check 'status.ps1 reports the version' ((Get-Content (Join-Path $S 'status.ps1') -Raw) -match 'Get-SkillVersion')
  Check 'run_tests.ps1 runs this suite' ((Get-Content (Join-Path $PSScriptRoot 'run_tests.ps1') -Raw) -match 'test_version\.ps1')
}
finally {
  Remove-Item $tmp -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue
}

if ($fails.Count) { Write-Host ("FAILED: " + ($fails -join '; ')) -ForegroundColor Red; exit 1 }
Write-Host 'test_version: all passed' -ForegroundColor Green
exit 0

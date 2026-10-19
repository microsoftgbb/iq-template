# test_splitter.ps1 - tests for the GENERATED Split-DemoDocSegments.
#
# This deliberately tests the real thing, not a lookalike: it runs
# scripts/generate.js against tests/fixtures/test-prefs.json (fake, non-secret
# credentials - generation is fully offline), extracts the emitted
# Split-DemoDocSegments function from the generated DemoIQ.ps1, and executes it
# under pwsh against LF and CRLF fixture content. The original defect - CRLF
# content parsing 0 delimiters because the .NET multiline $ anchor never matches
# before \r\n - is only visible in the GENERATED PowerShell (the JS template
# escaping is part of the bug surface), so a JS-side regex test is not enough.
#
# Dependency-free: needs only Node 18+ and pwsh. Run via tests/run_tests.ps1.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("demoiq-splitter-test-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null

$script:pass = 0; $script:fail = 0
function Assert-True([bool]$cond, [string]$name) {
  if ($cond) { $script:pass++; Write-Host "  PASS $name" -ForegroundColor Green }
  else { $script:fail++; Write-Host "  FAIL $name" -ForegroundColor Red }
}

try {
  # --- generate a real seeder from the fake fixture ---
  $gen = node (Join-Path $repo 'scripts\generate.js') --html (Join-Path $repo 'engine\DemoIQ.html') `
    --prefs (Join-Path $PSScriptRoot 'fixtures\test-prefs.json') --out-dir $work --emit ps1 2>$null
  $ps1Path = Join-Path $work 'DemoIQ.ps1'
  Assert-True (Test-Path $ps1Path) 'generate.js emitted DemoIQ.ps1 from the test fixture'

  # --- the generated seeder must contain the corrected, fully-escaped regex ---
  $ps1Text = Get-Content -Raw $ps1Path
  $wantRegexLine = "`$rx = [regex]'(?m)^[ \t]*={3,}[ \t]*FILE:[ \t]*([^=\r\n]*?)[ \t]*={3,}[ \t]*\r?`$'"
  Assert-True ($ps1Text.Contains($wantRegexLine)) 'generated seeder contains the CRLF-safe delimiter regex as literal text'
  Assert-True (-not ($ps1Text -match "FILE:\[ `t\]")) 'generated regex contains no raw TAB control characters'

  # --- the generated seeder must parse cleanly ---
  $tok = $null; $perr = $null
  [System.Management.Automation.Language.Parser]::ParseFile($ps1Path, [ref]$tok, [ref]$perr) | Out-Null
  Assert-True (@($perr).Count -eq 0) 'generated seeder has zero PowerShell parse errors'

  # --- the pre-upload guard must be baked in with the fixture's expected counts ---
  Assert-True ($ps1Text.Contains('$_officeCountErrors')) 'generated seeder contains the pre-upload count guard'
  Assert-True ($ps1Text.Contains('if ($_wSegs.Count -ne 2)')) 'guard bakes the configured wordDocs count (2)'

  # --- the retry-menu re-upload paths must carry the same count guard ---
  Assert-True ($ps1Text.Contains('[RETRY] [WORD] Expected 2 documents')) 'retry path guards Word re-uploads with the baked count'
  Assert-True ($ps1Text.Contains('[RETRY] [PPT] Expected 2 decks')) 'retry path guards PowerPoint re-uploads with the baked count'
  Assert-True ($ps1Text.Contains('[RETRY] [EXCEL] Expected 2 workbooks')) 'retry path guards Excel re-uploads with the baked count'

  # --- extract and execute the REAL generated function ---
  $m = [regex]::Match($ps1Text, '(?ms)^function Split-DemoDocSegments \{.*?^\}')
  Assert-True $m.Success 'Split-DemoDocSegments found in the generated seeder'
  Invoke-Expression $m.Value

  $CRLF = "`r`n"; $LF = "`n"
  function Make-Doc([string]$nl, [string[]]$names, [int]$bodyLines = 4) {
    $parts = @()
    foreach ($n in $names) {
      $parts += "===== FILE: $n ====="
      1..$bodyLines | ForEach-Object { $parts += "Body line $_ of $n with enough words to be real content." }
    }
    return ($parts -join $nl) + $nl
  }

  # 1-2. single-file responses, no delimiter (classic behavior), LF and CRLF
  foreach ($case in @(@{nl = $LF; tag = 'LF' }, @{nl = $CRLF; tag = 'CRLF' })) {
    $c = "Report Title" + $case.nl + "Line two of the report." + $case.nl + "Line three."
    $segs = @(Split-DemoDocSegments -Content $c -DefaultName 'Primary.docx' -Ext '.docx')
    Assert-True ($segs.Count -eq 1 -and $segs[0].Name -eq 'Primary.docx' -and $segs[0].Body.Contains('Line three.')) "single-file $($case.tag): 1 segment, default name, body intact"
  }

  # 3-4. two-file responses, LF and CRLF (CRLF is THE regression case)
  foreach ($case in @(@{nl = $LF; tag = 'LF' }, @{nl = $CRLF; tag = 'CRLF' })) {
    $c = Make-Doc $case.nl @('Primary.docx', 'Companion_Brief.docx')
    $segs = @(Split-DemoDocSegments -Content $c -DefaultName 'Primary.docx' -Ext '.docx')
    Assert-True ($segs.Count -eq 2) "two-file $($case.tag): 2 segments parsed"
    Assert-True ($segs[1].Name -eq 'Companion_Brief.docx') "two-file $($case.tag): companion name preserved"
  }

  # 5. five files (the engine's max), CRLF
  $five = 1..5 | ForEach-Object { "Deck_$_.pptx" }
  $segs = @(Split-DemoDocSegments -Content (Make-Doc $CRLF $five) -DefaultName 'Deck_1.pptx' -Ext '.pptx')
  Assert-True ($segs.Count -eq 5) 'five-file CRLF: 5 segments parsed'

  # 6. optional spaces/tabs around delimiters (CRLF)
  $c = " `t ===== `t FILE: `t Spaced_One.xlsx `t ===== `t $CRLF" + "col1`tcol2${CRLF}1`t2$CRLF$CRLF" +
      "=====FILE:Tight_Two.xlsx=====$CRLF" + "colA`tcolB${CRLF}3`t4$CRLF"
  $segs = @(Split-DemoDocSegments -Content $c -DefaultName 'Spaced_One.xlsx' -Ext '.xlsx')
  Assert-True ($segs.Count -eq 2 -and $segs[1].Name -eq 'Tight_Two.xlsx') 'spaces/tabs around delimiters tolerated (CRLF)'

  # 7. unique filename preservation for extras + no CR bleed into names/bodies
  $c = Make-Doc $CRLF @('First.docx', 'Alpha_Plan.docx', 'Beta_Review.docx')
  $segs = @(Split-DemoDocSegments -Content $c -DefaultName 'Scenario_Primary.docx' -Ext '.docx')
  Assert-True (($segs[1].Name -eq 'Alpha_Plan.docx') -and ($segs[2].Name -eq 'Beta_Review.docx')) 'extra segment names preserved uniquely'
  Assert-True (-not ($segs | Where-Object { $_.Name.Contains([char]13) -or $_.Body.EndsWith([char]13) })) 'no carriage returns bleed into names or bodies'

  # 8. primary filename: segment 0 always takes DefaultName, whatever the delimiter said
  Assert-True ($segs[0].Name -eq 'Scenario_Primary.docx') 'first segment always keeps the scenario primary name'

  # 9. empty segment rejection: back-to-back delimiters drop the empty one
  $c = "===== FILE: Has_Body.docx =====${CRLF}Real content here.$CRLF===== FILE: Empty_One.docx =====$CRLF$CRLF===== FILE: Also_Real.docx =====${CRLF}More content.$CRLF"
  $segs = @(Split-DemoDocSegments -Content $c -DefaultName 'Has_Body.docx' -Ext '.docx')
  Assert-True ($segs.Count -eq 2 -and ($segs | ForEach-Object { $_.Name }) -notcontains 'Empty_One.docx') 'empty-bodied segment is dropped'

  # 10. filename sanitization: Windows-invalid characters (incl. backslash) stripped
  $c = "===== FILE: First.docx =====${CRLF}Primary body.$CRLF===== FILE: Bad\Name*?.docx =====${CRLF}Second body.$CRLF"
  $segs = @(Split-DemoDocSegments -Content $c -DefaultName 'First.docx' -Ext '.docx')
  Assert-True ($segs[1].Name -eq 'BadName.docx') 'invalid filename characters (incl. backslash) are stripped'

  # 11. the REAL defect bundle shape: 2 CRLF-delimited segments per type must parse as 2
  $c = Make-Doc $CRLF @('Internal_AI_Pilot_Evaluation_Report.docx', 'AI_Scale_Governance_and_Investment_Brief.docx') 12
  $segs = @(Split-DemoDocSegments -Content $c -DefaultName 'Internal_AI_Pilot_Evaluation_Report.docx' -Ext '.docx')
  Assert-True ($segs.Count -eq 2) 'affected-run shape (2 CRLF segments) parses as 2 segments'
}
finally {
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host ("splitter: {0} passed, {1} failed" -f $script:pass, $script:fail) -ForegroundColor ($(if ($script:fail) { 'Red' } else { 'Green' }))
exit $(if ($script:fail) { 1 } else { 0 })

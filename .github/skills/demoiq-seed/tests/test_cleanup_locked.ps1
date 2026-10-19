# test_cleanup_locked.ps1 - cleanup_local.ps1 locked-file classification tests.
#
# Proves the fix for "cleanup couldn't delete the docx/pptx I had
# open": a held-open file must NOT collapse the whole tree into one opaque
# error - everything else is still deleted, the open file lands in
# lockedFiles[] with its exact path, ok=false, exit 3; after closing the file a
# re-run completes clean (exit 0). Uses a real FileStream share-violation, a
# synthetic $ws and its own session folder, so the real workspace and sign-in are
# never touched. Dependency-free; run via tests/run_tests.ps1.
#
# The lock contract is Windows-specific: macOS/Linux let a process delete a file
# another process holds open (unlink semantics), so there the same scenario must
# simply complete clean - nothing can be left behind by an open file.
$ErrorActionPreference = 'Stop'

$script:failures = 0
function Assert-True($cond, [string]$label) {
  if ($cond) { Write-Host "  PASS: $label" -ForegroundColor Green }
  else { Write-Host "  FAIL: $label" -ForegroundColor Red; $script:failures++ }
}

$cleanup = Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\cleanup_local.ps1'
$root = Join-Path ([System.IO.Path]::GetTempPath()) ("demoiq-clnlock-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$ws = Join-Path $root 'ws'
New-Item -ItemType Directory -Path (Join-Path $ws 'runs\r1\DemoIQ-Output') -Force | Out-Null
$env:DEMOIQ_SESSION_DIR = Join-Path $root 'session'

$openDoc  = Join-Path $ws 'runs\r1\DemoIQ-Output\Quarterly_Review.docx'
$freeDoc  = Join-Path $ws 'runs\r1\DemoIQ-Output\Notes.txt'
Set-Content -LiteralPath $openDoc -Value 'seeded doc' -Encoding UTF8
Set-Content -LiteralPath $freeDoc -Value 'deletable' -Encoding UTF8

function Invoke-Cleanup([string[]]$extra) {
  $raw = & pwsh -NoProfile -ExecutionPolicy Bypass -File $cleanup `
    -OutputFolder $ws @extra 2>$null
  $code = $LASTEXITCODE
  $line = @($raw | Where-Object { $_ -and "$_".Trim().StartsWith('{') }) | Select-Object -Last 1
  if (-not $line) { throw "no JSON line in cleanup output: $raw" }
  return @{ json = ($line | ConvertFrom-Json); exit = $code }
}

Write-Host "-- dry-run stays exit 0 --"
$r = Invoke-Cleanup @()
Assert-True ($r.exit -eq 0 -and $r.json.mode -eq 'dry-run') 'dry-run: exit 0, nothing deleted'
Assert-True (Test-Path $openDoc) 'dry-run: files untouched'

Write-Host "-- execute with one file held open (Office-style lock) --"
$hold = [System.IO.File]::Open($openDoc, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
if (-not $IsWindows) {
  try {
    $r = Invoke-Cleanup @('-Execute', '-Confirm', 'CLEANUP-1')
    Assert-True ($r.exit -eq 0 -and $r.json.ok -eq $true) 'macOS/Linux: an open file does not block cleanup (exit 0, ok)'
    Assert-True (@($r.json.lockedFiles).Count -eq 0) 'macOS/Linux: lockedFiles[] empty'
    Assert-True (-not (Test-Path (Join-Path $ws 'runs'))) 'macOS/Linux: the runs tree is fully gone, open file included'
  } finally { $hold.Close() }
  try { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue } catch {}
  if ($script:failures) { Write-Host "test_cleanup_locked: $($script:failures) FAILURE(S)" -ForegroundColor Red; exit 1 }
  Write-Host 'test_cleanup_locked: all passed' -ForegroundColor Green
  exit 0
}
try {
  $r = Invoke-Cleanup @('-Execute', '-Confirm', 'CLEANUP-1')
  Assert-True ($r.exit -eq 3) 'locked run: exit 3 (machine-detectable partial)'
  Assert-True ($r.json.ok -eq $false) 'locked run: ok=false'
  Assert-True (@($r.json.lockedFiles).Count -eq 1 -and $r.json.lockedFiles[0].path -eq $openDoc) 'locked run: the open file is named in lockedFiles[]'
  Assert-True (-not (Test-Path $freeDoc)) 'locked run: the deletable neighbor file WAS removed'
  Assert-True (Test-Path $openDoc) 'locked run: the open file itself survives (lock cannot be broken)'
  Assert-True ([bool](@($r.json.errors) -match 'close them')) 'locked run: errors[] tells the user to close and re-run'
} finally {
  $hold.Close()
}

Write-Host "-- re-run after closing the file --"
$r = Invoke-Cleanup @('-Execute', '-Confirm', 'CLEANUP-1')
Assert-True ($r.exit -eq 0 -and $r.json.ok -eq $true) 're-run: clean exit 0 after the file is closed'
Assert-True (@($r.json.lockedFiles).Count -eq 0) 're-run: lockedFiles[] empty'
Assert-True (-not (Test-Path (Join-Path $ws 'runs'))) 're-run: the runs tree is fully gone'

try { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue } catch {}

if ($script:failures) { Write-Host "test_cleanup_locked: $($script:failures) FAILURE(S)" -ForegroundColor Red; exit 1 }
Write-Host 'test_cleanup_locked: all passed' -ForegroundColor Green
exit 0

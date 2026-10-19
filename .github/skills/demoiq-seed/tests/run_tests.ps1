# run_tests.ps1 - demoiq-seed dependency-free test suite (Windows, macOS, Linux).
#
# Runs everything with Node built-ins and pwsh only (no package.json, no npm
# installs - deliberate, per the skill's zero-dependency posture):
#   1. tests/test_splitter.ps1          - the GENERATED Split-DemoDocSegments under pwsh
#                                         (generates a real seeder from a fake fixture)
#   2. tests/test_token_resilience.ps1  - the GENERATED token-retry / seed-ledger /
#                                         targeted-replay block, driven with a mocked
#                                         token endpoint (no tenant contacted)
#   3. tests/test_monitor_status.ps1    - monitor_seeding.ps1 done/partial/failed
#                                         detection + bounded-wait mode, and
#                                         run_history.ps1 outcomes
#   4. tests/test_azure_preflight.ps1   - preflight_azure_cli.ps1 (Check C) against a
#                                         stub az and saved receipts, incl. the no-audio
#                                         opt-out contract
#   5. tests/test_preflight_credentials.ps1 - preflight_credentials.ps1 (Check B): the
#                                         demo users' public-client sign-in (Entra stubbed)
#   6. tests/test_cleanup_locked.ps1    - cleanup_local.ps1 open-file handling (Windows
#                                         locks; macOS/Linux unlink semantics)
#   7. tests/test_validator.js          - validate_pasted.js Office multi-file rules
#   8. tests/test_verifier.js           - verify_seeded_files.js expected-count rules
#   9. tests/test_engagement_fields.js  - config field round-trip, company-name
#                                         propagation, IDs never in content, and
#                                         no telemetry anywhere
#  10. tests/test_aop_surfaces.js       - Copilot Code / Autopilot AoP surfaces
#  11. tests/test_version.ps1           - the version contract + GitHub Copilot
#                                         SKILL.md frontmatter schema
#  12. tests/test_platform_patch.js     - the delegated seeder patch, its cleanup mode, and
#                                         its loud failure on engine drift or a forbidden
#                                         construct
#  13. tests/test_platform.ps1          - session store, plain configs, the one-time
#                                         converter, the config library tools (find, repoint,
#                                         draft), detach and local cleanup
#  14. tests/test_builder_save.ps1      - the Config Builder's direct save through
#                                         serve_local.ps1 (plain, owner-only, no credentials)
#  15. tests/test_admin_signin.ps1      - the administrator's per-run device-code sign-in
#                                         (Entra stubbed; the code never reaches stdout)
#  16. tests/test_seeder_bootstrap.ps1  - a real patched seeder through the real launcher:
#                                         plain config, consumed sign-in, refresh-token
#                                         renewal, Graph over REST, and a cleanup run
#                                         (Entra/Graph stubbed)
#  17. tests/test_cleanup_engine.ps1    - the delegated tenant-cleanup engine and the
#                                         dry-run cleanup_tenant.ps1 (Graph stubbed)
#  18. tests/test_cleanup_run.ps1       - an end-to-end cleanup run: a real seeder through
#                                         the real run_seeder.ps1 -Cleanup (tenant prep,
#                                         delegated deletes, restore, cleanup_result.json,
#                                         monitor and history; Entra/Graph stubbed)
#  19. tests/test_status.ps1            - status.ps1: honest readiness states
#  20. tests/test_workiq_check.js       - workiq_check.js: runbook prompts and citation
#                                         matching for the optional Work IQ check
#
# Usage:  pwsh -NoProfile -ExecutionPolicy Bypass -File tests/run_tests.ps1
# Exit 0 = all suites passed. Touches nothing outside its own temp folder; never
# contacts a tenant, never shows a dialog.
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$failed = @()

# SUITE-WIDE isolation: a throwaway HOME and TMPDIR (macOS/Linux) and session folder, so
# no suite can read, overwrite or delete the developer's real DemoIQ workspace, config
# library or administrator sign-in, and nothing a suite leaves in a temp folder outlives
# the run. Telemetry is forced off as a belt-and-braces guard should any
# upstream DemoIQ writer ever be reachable.
$iso = Join-Path ([System.IO.Path]::GetTempPath()) ('demoiq-suite-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force (Join-Path $iso 'home') | Out-Null
$saved = @{ HOME = $env:HOME; TMPDIR = $env:TMPDIR; DEMOIQ_TELEMETRY = $env:DEMOIQ_TELEMETRY; DEMOIQ_SESSION_DIR = $env:DEMOIQ_SESSION_DIR; DEMOIQ_ADMIN_SESSION = $env:DEMOIQ_ADMIN_SESSION }
if (-not $IsWindows) {
  $env:HOME = Join-Path $iso 'home'
  $env:TMPDIR = (New-Item -ItemType Directory -Force (Join-Path $iso 'tmp')).FullName
}
$env:DEMOIQ_SESSION_DIR = Join-Path $iso 'session'
$env:DEMOIQ_TELEMETRY = 'off'
$env:DEMOIQ_ADMIN_SESSION = $null

function Invoke-Suite([string]$title, [string]$file) {
  Write-Host ''
  Write-Host "== $title ==" -ForegroundColor Cyan
  if ($file.EndsWith('.js')) { & node (Join-Path $here $file) }
  else { & pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here $file) }
  if ($LASTEXITCODE -ne 0) { $script:failed += $file }
}

try {
  Invoke-Suite 'splitter (generated PowerShell under pwsh)' 'test_splitter.ps1'
  Invoke-Suite 'token resilience (generated PowerShell under pwsh)' 'test_token_resilience.ps1'
  Invoke-Suite 'monitor status (partial/done/failed detection under pwsh)' 'test_monitor_status.ps1'
  Invoke-Suite 'azure preflight (Check C az-tenant gate under pwsh)' 'test_azure_preflight.ps1'
  Invoke-Suite 'demo-user credential preflight (Check B, Entra stubbed, under pwsh)' 'test_preflight_credentials.ps1'
  Invoke-Suite 'cleanup open-file handling (under pwsh)' 'test_cleanup_locked.ps1'
  Invoke-Suite 'validate_pasted.js' 'test_validator.js'
  Invoke-Suite 'verify_seeded_files.js' 'test_verifier.js'
  Invoke-Suite 'config fields + company-name propagation + no telemetry (node)' 'test_engagement_fields.js'
  Invoke-Suite 'Copilot Code / Autopilot AoP surfaces (node)' 'test_aop_surfaces.js'
  Invoke-Suite 'version contract + SKILL.md schema' 'test_version.ps1'
  Invoke-Suite 'delegated seeder patch (node)' 'test_platform_patch.js'
  Invoke-Suite 'cross-platform layer (pwsh)' 'test_platform.ps1'
  Invoke-Suite 'Config Builder direct save (pwsh)' 'test_builder_save.ps1'
  Invoke-Suite 'administrator sign-in (pwsh)' 'test_admin_signin.ps1'
  Invoke-Suite 'patched seeder bootstrap on this OS (pwsh)' 'test_seeder_bootstrap.ps1'
  Invoke-Suite 'delegated tenant cleanup engine (pwsh)' 'test_cleanup_engine.ps1'
  Invoke-Suite 'end-to-end cleanup run (pwsh)' 'test_cleanup_run.ps1'
  Invoke-Suite 'readiness report (pwsh)' 'test_status.ps1'
  Invoke-Suite 'Work IQ check helper (node)' 'test_workiq_check.js'
}
finally {
  foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
  Remove-Item -LiteralPath $iso -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ''
if ($failed.Count) {
  Write-Host ("SUITE FAILED: " + ($failed -join ', ')) -ForegroundColor Red
  exit 1
}
Write-Host "ALL SUITES PASSED" -ForegroundColor Green
exit 0

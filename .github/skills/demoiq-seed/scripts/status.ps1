<#
  status.ps1 - where DemoIQ stands for this deployment, stated honestly.

  Read-only and offline: reads the deployment receipts (deploy.py status --json ->
  demoiq), the config library, the stored administrator sign-in (its account only, never
  its token) and the newest run folders. It makes no tenant calls.

  "Seeded" is never "ready". A seed run proves the content was written; whether Work IQ
  answers from it depends on Microsoft 365 indexing, Work IQ consent and licensing. Only
  the optional Work IQ check (scripts/workiq_check.js, run as a demo user) adds that
  evidence, and even then only for the prompts it asked.

  state (first match wins):
    running             a seed or cleanup run is in progress (monitor it)
    tenant-prepared     a run left MFA / Conditional Access / Security Defaults changes in
                        place: it stopped before its restore step (tenant_state.json phase
                        "prepared") or its restore did not fully succeed (phase "restored",
                        restored false) - resolve this first: resume that run folder or
                        restore by hand
    not-set-up          no DemoIQ receipts: deploy.py up --with-demoiq --through demoiq
    needs-config        no READY config: one that names this deployment's DemoIQ app, is not
                        a draft (demoiqDraft) and has five demo users with passwords
    ready-to-seed       a ready config exists and nothing is seeded (or the newest run was a
                        complete cleanup)
    cleanup-partial     the newest cleanup left items behind (itemsNotRemoved)
    cleanup-failed      the newest cleanup failed or stopped
    seed-failed         the newest seed run failed or stopped
    seeded-partial      the newest seed run finished with requested items missing
    seeded              the newest seed run finished; Work IQ not checked, or the check
                        did not find seeded content (indexing can take hours)
    workiq-verified     the newest seed run finished and the Work IQ check, asked as one of
                        its demo users, found seeded content for at least one runbook prompt

  A run folder that new_run.ps1 created but nobody launched (run.json status "created", no
  heartbeat) is not a run and never becomes lastRun or lastSeedRun.

  Params:
    -OutputFolder  the workspace <ws> (default ~/.copilot/DemoIQ-Output-Scout)
    -ReceiptsFile  (optional) a saved `deploy.py status --json` output instead of the runner
    -NoReceipts    skip the receipts lookup (state cannot be better than not-set-up)
    -MarkRestoredByHand <run folder>
                   record that the user restored a run's remaining tenant changes by hand
                   (Flow 6 "Tenant left prepared"): writes
                   DemoIQ-Output/tenant_state.manual.json beside tenant_state.json, which
                   stays untouched as evidence. Only for a run whose tenant_state.json
                   left changes in place (see tenant-prepared); exit 2 otherwise.

  Output: ONE compact JSON line:
    { ok, state, summary, attention[], next[], skillVersion, deployment, deploymentError,
      configs{ count, matching, ready, drafts, encrypted, newestReady, newestMatching },
      signIn{}, activeRun, lastRun, lastSeedRun, preparedRuns[], workIq }
#>
param(
  [string]$OutputFolder,
  [string]$ReceiptsFile,
  [switch]$NoReceipts,
  [string]$MarkRestoredByHand
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'session_store.ps1')
. (Join-Path $PSScriptRoot 'version_shared.ps1')
function Out-Json($o) { $o | ConvertTo-Json -Compress -Depth 8 }
function Read-Json([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  try { return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json) } catch { return $null }
}
# A run left tenant changes in place when it stopped before its restore step, or when its
# restore step ran but did not undo everything it changed.
function Test-ChangesLeft($TenantState) {
  if (-not $TenantState) { return $false }
  return ($TenantState.phase -eq 'prepared' -or ($TenantState.phase -eq 'restored' -and $TenantState.restored -eq $false))
}

$ws = if ($OutputFolder) { $OutputFolder } else { Get-DemoIQWorkspace }
$attention = New-Object System.Collections.Generic.List[string]
$next = New-Object System.Collections.Generic.List[string]

if ($MarkRestoredByHand) {
  $markState = Read-Json (Join-Path $MarkRestoredByHand 'DemoIQ-Output/tenant_state.json')
  if (-not (Test-ChangesLeft $markState)) {
    Out-Json ([ordered]@{ ok = $false; error = "Not a run that left tenant changes in place (no DemoIQ-Output/tenant_state.json with phase 'prepared', or 'restored' with restored false): $MarkRestoredByHand" })
    exit 2
  }
  $markPath = Join-Path $MarkRestoredByHand 'DemoIQ-Output/tenant_state.manual.json'
  [System.IO.File]::WriteAllText($markPath, '')
  Set-DemoIQPrivateFile $markPath
  [System.IO.File]::WriteAllText($markPath, ([ordered]@{ restoredByHand = $true; recordedAt = (Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json))
}

# ---------- deployment receipts
$deployment = $null; $deploymentError = $null
if (-not $NoReceipts) {
  try {
    $r = Get-DemoIQRunnerReceipts -ReceiptsFile $ReceiptsFile
    if ($r -and $r.client_id) {
      $deployment = [ordered]@{
        tenantId = [string]$r.tenant_id; clientId = [string]$r.client_id; consent = [string]$r.consent
        speechResourceId = [string]$r.speech_resource_id; audio = $r.audio; checkedAt = [string]$r.checked_at
      }
    } else { $deploymentError = 'DemoIQ is not set up for this deployment yet.' }
  } catch { $deploymentError = $_.Exception.Message }
}
if ($deployment -and $deployment.consent -ne 'granted') {
  $attention.Add('The runner could not verify administrator consent for the DemoIQ app; the sign-in step checks it again.')
}

# ---------- config library (plain configs only; never a password)
# ready = names this deployment's app, is not a draft and has five demo users with passwords.
$configs = [ordered]@{ count = 0; matching = 0; ready = 0; drafts = 0; encrypted = 0; newestReady = $null; newestMatching = $null }
$configsHome = Join-Path $ws 'configs'
foreach ($f in @(Get-ChildItem -LiteralPath $configsHome -Filter 'DemoIQ_config*.json' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)) {
  $configs.count++
  $c = Read-Json $f.FullName
  if (-not $c) { continue }
  if ($c.v -eq 'cdx-enc-v1') { $configs.encrypted++; continue }
  $isDraft = ($c.demoiqDraft -eq $true)
  if ($isDraft) { $configs.drafts++ }
  if ($deployment -and [string]$c.existingAppId -eq $deployment.clientId) {
    $configs.matching++
    if (-not $configs.newestMatching) { $configs.newestMatching = $f.FullName }
    $complete = @(1..5 | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$c.("u${_}upn")) -and -not [string]::IsNullOrEmpty([string]$c.("u${_}pwd")) }).Count -eq 5
    if ($complete -and -not $isDraft) {
      $configs.ready++
      if (-not $configs.newestReady) { $configs.newestReady = $f.FullName }
    }
  }
  $c = $null
}
if ($configs.encrypted -gt 0) {
  $attention.Add("$($configs.encrypted) encrypted config(s) in the library: convert each once in your own terminal with scripts/convert_config.ps1.")
}
if ($configs.matching -gt $configs.ready) {
  $attention.Add("$($configs.matching - $configs.ready) config(s) for this deployment are drafts or lack demo-user passwords: complete them in the Config Builder (Flow 1a).")
}

# ---------- stored administrator sign-in (account only)
$signIn = [ordered]@{ stored = $false }
$stored = Read-DemoIQSessionSecret -Name 'admin-session'
if ($stored) {
  try {
    $s = $stored | ConvertFrom-Json
    $signIn = [ordered]@{
      stored = $true; upn = [string]$s.upn; tenantId = [string]$s.tenantId; signedInAt = [string]$s.signedInAt
      matchesDeployment = $(if ($deployment) { [string]$s.tenantId -eq $deployment.tenantId -and [string]$s.clientId -eq $deployment.clientId } else { $null })
    }
  } catch {}
  $stored = $null
}

# ---------- runs (newest first by creation time)
function Get-RunState([string]$Folder) {
  $manifest = Read-Json (Join-Path $Folder 'run.json')
  $out = Join-Path $Folder 'DemoIQ-Output'
  $heartbeat = $null
  $hbPath = Join-Path $out '.run_seeder_status'
  if (Test-Path -LiteralPath $hbPath) { $heartbeat = ((Get-Content -LiteralPath $hbPath -Raw) -split "`t", 2)[-1].Trim() }
  $kind = if ($manifest -and $manifest.kind -eq 'cleanup') { 'cleanup' } else { 'seed' }
  $tenantState = Read-Json (Join-Path $out 'tenant_state.json')
  $cleanupResult = if ($kind -eq 'cleanup') { Read-Json (Join-Path $out 'cleanup_result.json') } else { $null }
  $outcome = switch -Regex ($heartbeat) {
    '^DONE missing=(\d+)' { if ([int]$Matches[1] -gt 0) { 'partial' } else { 'done' }; break }
    '^DONE failed=(\d+)'  { if ([int]$Matches[1] -gt 0) { 'partial' } else { 'done' }; break }
    '^DONE'               { 'done'; break }
    '^FAILED'             { 'failed'; break }
    '^(STARTING|STARTED)' { 'started'; break }
    default               { 'unknown' }
  }
  return [ordered]@{
    kind = $kind
    runId = $(if ($manifest) { [string]$manifest.runId } else { Split-Path -Leaf $Folder })
    runFolder = $Folder
    createdAt = $(if ($manifest) { [string]$manifest.createdAt } else { $null })
    outcome = $outcome
    heartbeat = $heartbeat
    tenantState = $(if ($tenantState) { [ordered]@{ phase = [string]$tenantState.phase; restored = $tenantState.restored; mfaReinstated = $tenantState.mfaReinstated } } else { $null })
    itemsNotRemoved = $(if ($cleanupResult) { [int]$cleanupResult.failedTotal } else { $null })
  }
}
$runFolders = @(Get-ChildItem -LiteralPath (Join-Path $ws 'runs') -Directory -ErrorAction SilentlyContinue |
  Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'run.json') } | Sort-Object Name -Descending)
# A folder new_run.ps1 created that nobody launched (still authoring, or cancelled at the
# content preview) is not a run: it never touched the tenant.
$launchedFolders = @($runFolders | Where-Object {
    $m = Read-Json (Join-Path $_.FullName 'run.json')
    -not ($m -and $m.status -eq 'created' -and -not (Test-Path -LiteralPath (Join-Path $_.FullName 'DemoIQ-Output/.run_seeder_status')))
  })
$lastRun = if ($launchedFolders.Count) { Get-RunState $launchedFolders[0].FullName } else { $null }
$lastSeedRun = $null
foreach ($rf in $launchedFolders) {
  $m = Read-Json (Join-Path $rf.FullName 'run.json')
  if (-not $m -or $m.kind -ne 'cleanup') { $lastSeedRun = Get-RunState $rf.FullName; break }
}

$activeRun = $null
try {
  $proc = @(Get-DemoIQPwshProcess | Where-Object { $_.CommandLine -match 'run_seeder\.ps1' }) | Select-Object -First 1
  if ($proc) { $activeRun = [ordered]@{ processId = $proc.ProcessId; kind = $(if ($proc.CommandLine -match '-Cleanup\b') { 'cleanup' } else { 'seed' }) } }
} catch {}
foreach ($run in @($lastRun, $lastSeedRun)) {
  if ($run -and $run.outcome -eq 'started' -and -not $activeRun) { $run.outcome = 'stopped' }
}
# A run that stopped before its restore step, or whose restore did not fully succeed, leaves
# tenant changes in place, and a later run in another folder treats them as pre-existing
# (restore-not-strip), so every run folder counts - not only the newest.
$preparedRuns = @(foreach ($rf in $runFolders) {
    $ts = Read-Json (Join-Path $rf.FullName 'DemoIQ-Output/tenant_state.json')
    if (Test-ChangesLeft $ts) {
      $manual = Read-Json (Join-Path $rf.FullName 'DemoIQ-Output/tenant_state.manual.json')
      if ($manual -and $manual.restoredByHand) {
        $attention.Add("Restored by hand (recorded $($manual.recordedAt)): $($rf.FullName) - its tenant_state.json still describes what that run changed.")
        continue
      }
      if ($ts.phase -eq 'prepared') {
        $attention.Add("Not restored: $($rf.FullName) stopped before its restore step (MFA is not reinstated for its demo users until that step completes or you restore by hand).")
      } else {
        $attention.Add("Restore incomplete: $($rf.FullName) could not undo every change - see the failures in its DemoIQ-Output/tenant_state.json.")
      }
      $rf.FullName
    }
  })

# ---------- Work IQ check for the newest seed run
$workIq = [ordered]@{ checked = $false }
if ($lastSeedRun) {
  $w = Read-Json (Join-Path $lastSeedRun.runFolder 'DemoIQ-Output/workiq_check.json')
  if ($w) {
    $workIq = [ordered]@{
      checked = $true; verdict = [string]$w.verdict; checkedAt = [string]$w.checkedAt; askedAs = [string]$w.askedAs
      askedAsIsDemoUser = [bool]$w.askedAsIsDemoUser; prompts = [int]$w.prompts; answered = [int]$w.answered
      withSeededCitations = [int]$w.withSeededCitations
    }
  }
}

# ---------- state
$prepared = $preparedRuns.Count -gt 0
$state = if ($activeRun) { 'running' }
         elseif ($prepared) { 'tenant-prepared' }
         elseif (-not $deployment) { 'not-set-up' }
         elseif ($configs.ready -eq 0) { 'needs-config' }
         elseif (-not $lastRun) { 'ready-to-seed' }
         elseif ($lastRun.kind -eq 'cleanup') {
           switch ($lastRun.outcome) { 'done' { 'ready-to-seed' } 'partial' { 'cleanup-partial' } default { 'cleanup-failed' } }
         }
         elseif ($lastRun.outcome -eq 'done') { $(if ($workIq.checked -and $workIq.verdict -eq 'found-seeded-content' -and $workIq.askedAsIsDemoUser) { 'workiq-verified' } else { 'seeded' }) }
         elseif ($lastRun.outcome -eq 'partial') { 'seeded-partial' }
         else { 'seed-failed' }

$summary = switch ($state) {
  'running'         { "A DemoIQ $($activeRun.kind) run is in progress." }
  'tenant-prepared' { "$($preparedRuns.Count) run(s) left tenant changes in place: MFA, Conditional Access exclusions or Security Defaults may still be relaxed for the demo users." }
  'not-set-up'      { 'DemoIQ is not set up for this deployment.' }
  'needs-config'    {
    if ($configs.matching -gt 0) { 'DemoIQ is set up, but the configs for this deployment are drafts or lack demo-user passwords.' }
    else { 'DemoIQ is set up, but no config in the library names this deployment''s DemoIQ app.' }
  }
  'ready-to-seed'   { 'Ready to seed: DemoIQ is set up and a complete config for this deployment exists.' }
  'cleanup-partial' { "The newest cleanup finished, but $($lastRun.itemsNotRemoved) item(s) were not removed." }
  'cleanup-failed'  { 'The newest cleanup failed or stopped before finishing.' }
  'seed-failed'     { 'The newest seed run failed or stopped before finishing.' }
  'seeded-partial'  { 'Seeded, with some requested items missing. Seeded is not ready: Work IQ has not been shown to answer from this content.' }
  'seeded'          {
    if ($workIq.checked -and -not $workIq.askedAsIsDemoUser) { "Seeded. The Work IQ check was asked as $($workIq.askedAs), not as a seeded demo user, so it does not count; ask again as one of the run's demo users." }
    elseif ($workIq.checked) { "Seeded. The Work IQ check ($($workIq.verdict)) did not cite seeded content yet; indexing can take hours, so check again later." }
    else { 'Seeded. Seeded is not ready: Work IQ has not been checked against this content (optional, as a demo user).' }
  }
  'workiq-verified' { "Seeded, and Work IQ cited seeded content for $($workIq.withSeededCitations) of $($workIq.prompts) runbook prompt(s) when asked as $($workIq.askedAs)." }
}

switch ($state) {
  'running'         { $next.Add('Monitor the run: scripts/monitor_seeding.ps1 -OutputFolder "<run folder>" -WaitSeconds 60') }
  'tenant-prepared' { $next.Add("Resume the run folder $($preparedRuns[0]) so its restore step runs again, or restore by hand from its DemoIQ-Output/tenant_state.json (remove the exclusion group only from the policies in caExclusion.policiesTouched) - Flow 6 ""Tenant left prepared"".") }
  'not-set-up'      { $next.Add('python3 .github/skills/deploy-platform/scripts/deploy.py up --with-demoiq --through demoiq') }
  'needs-config'    { $next.Add('Draft, complete or repoint a config for this deployment (Flow 1a).') }
  'ready-to-seed'   { $next.Add('Build a seed (Flow 1): author and preview the content, sign in, launch.') }
  'cleanup-partial' { $next.Add('Run the cleanup again for the same scope (Flow 6); it retries what is left.') }
  'cleanup-failed'  { $next.Add('Read the cleanup run log, then run the cleanup again (Flow 6).') }
  'seed-failed'     { $next.Add('Fix the cause from the run log (scripts/monitor_seeding.ps1), then resume the same run folder (Flow 1h, "Recover a partial or failed run").') }
  'seeded-partial'  { $next.Add('Resume the same run folder to re-attempt only the missing items (Flow 1h, "Recover a partial or failed run").') }
  'seeded'          { $next.Add('Optional: check Work IQ against the seeded content as a demo user (Flow 9).') }
  'workiq-verified' { $next.Add('Demo with the AoP runbook prompts.') }
}
if ($workIq.checked -and -not $workIq.askedAsIsDemoUser) {
  $attention.Add("The Work IQ check was asked as $($workIq.askedAs), not as a seeded demo user; its result says little about the seeded content.")
}

Out-Json ([ordered]@{
    ok = $true; state = $state; summary = $summary; attention = $attention; next = $next
    skillVersion = $(try { Get-SkillVersion } catch { $null })
    deployment = $deployment; deploymentError = $deploymentError; configs = $configs; signIn = $signIn
    activeRun = $activeRun; lastRun = $lastRun; lastSeedRun = $lastSeedRun; preparedRuns = $preparedRuns; workIq = $workIq
  })
exit 0

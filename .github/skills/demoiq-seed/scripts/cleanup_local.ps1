<#
  cleanup_local.ps1 - remove DemoIQ's LOCAL footprint from this machine.

  The local counterpart to Flow 6 (which removes seeded content from the TENANT). The
  GitHub Copilot adaptation keeps no certificate, app registration or config password on
  this machine - the DemoIQ app lives in Terraform - so there are two depths:

    1 / Runs        - run folders (runs\, legacy DemoIQ-Output\, logs\, DemoIQ.ps1,
                      tenant-readiness.json, DemoIQ-Deliverables\), the archive\ zips,
                      scratch\, the local server state (serve\), and the session store:
                      the administrator's stored sign-in, the pending sign-in code file and
                      the session key (.session\). KEEPS the config library (configs\).
    2 / FullLocal   - everything in (1) PLUS the config library (configs\, which holds the
                      demo-user passwords) and then the workspace folder itself when empty:
                      "remove DemoIQ from this machine".

  Files left in the shared workspace by the upstream DemoIQ-Scout skill or an older
  certificate-based version (app_registration.json, speech_resource.json, certs\,
  telemetry\) are reported under foreign[] and never deleted here - remove them with that
  tool, or by hand once nothing uses them. Removing the DemoIQ Entra app is a Terraform
  change (deploy.py; see the deploy-platform skill), never a local cleanup - depth 3 is
  refused with that pointer. Clean up the tenant content first (Flow 6).

  SAFETY: dry-run/inventory is the DEFAULT. Nothing is deleted unless you pass -Execute AND
  -Confirm (an explicit confirmation token). Output: one JSON line
  { ok, depth, mode, confirmToken, outputFolder, inventory[], foreign[], removed[], kept[],
    lockedFiles[], errors[] }. lockedFiles[] names each file that survived because it is
  OPEN (a seeded Office file still open, a browser tab viewing it) - probed with a real
  share-violation test after everything else in the same tree was removed. Exit codes:
  0 = clean (or dry-run/refusal), 3 = completed but PARTIAL (lockedFiles[] or errors[]),
  2 = depth 3 refused.

  Params:
    -Depth          1|2 or Runs|FullLocal (default 1)
    -OutputFolder   the workspace <ws> (default ~/.copilot/DemoIQ-Output-Scout)
    -Execute        actually delete (omit for dry-run/inventory)
    -Confirm        required with -Execute: must equal the printed token ("CLEANUP-<depth>")
#>
[CmdletBinding()]
param(
  [string]$Depth = '1',
  [string]$OutputFolder,
  [switch]$Execute,
  [string]$Confirm
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'session_store.ps1')
function Out-Json($o) { Write-Output ($o | ConvertTo-Json -Compress -Depth 8) }

$depthN = switch -Regex ($Depth) {
  '^(1|runs|artifacts)$' { 1 }
  '^(2|fulllocal)$'      { 2 }
  '^(3|fulllocalandentra|entra)$' { 3 }
  default { 1 }
}
if ($depthN -eq 3) {
  Out-Json ([ordered]@{ ok = $false; depth = 3; mode = 'refused'
      error = 'The DemoIQ Entra app is managed by Terraform, not by this machine. Clean up the tenant content first (Flow 6), then set demoiq_enabled = false in infra/terraform.tfvars and rerun deploy.py up (or destroy the platform). Local cleanup offers depths 1 and 2.' })
  exit 2
}
if (-not $OutputFolder) { $OutputFolder = Get-DemoIQWorkspace }

$inventory = New-Object System.Collections.Generic.List[object]
$foreign = New-Object System.Collections.Generic.List[object]
$kept = New-Object System.Collections.Generic.List[object]
function Add-Item([string]$Kind, [string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $full = [System.IO.Path]::GetFullPath($Path)
  foreach ($i in $inventory) {
    $root = [System.IO.Path]::GetFullPath($i.path).TrimEnd([char[]]'/\') + [System.IO.Path]::DirectorySeparatorChar
    if ($full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) { return }
  }
  $inventory.Add([ordered]@{ kind = $Kind; path = $Path })
}

if (Test-Path -LiteralPath $OutputFolder) {
  foreach ($name in 'runs', 'archive', 'scratch', 'serve', 'DemoIQ-Output', 'logs', 'DemoIQ-Deliverables') {
    Add-Item 'artifact' (Join-Path $OutputFolder $name)
  }
  foreach ($pat in 'DemoIQ.ps1', 'tenant-readiness.json', '*.log', 'content_preview.html', 'demoiq-empty-prefs.json') {
    foreach ($f in (Get-ChildItem -LiteralPath $OutputFolder -Filter $pat -File -ErrorAction SilentlyContinue)) { Add-Item 'artifact' $f.FullName }
  }
  # The session store: the key folder (.session, which also holds a pending sign-in code).
  Add-Item 'session' (Join-Path $OutputFolder '.session')
  $configs = Join-Path $OutputFolder 'configs'
  if ($depthN -ge 2) {
    Add-Item 'config' $configs
    foreach ($f in (Get-ChildItem -LiteralPath $OutputFolder -Filter 'DemoIQ_config*.json' -File -ErrorAction SilentlyContinue)) { Add-Item 'config' $f.FullName }
  } elseif (Test-Path -LiteralPath $configs) {
    $kept.Add([ordered]@{ kind = 'config'; path = $configs; note = 'the config library is kept at depth 1; depth 2 removes it' })
  }
  foreach ($name in 'app_registration.json', 'speech_resource.json', 'certs', 'telemetry') {
    $p = Join-Path $OutputFolder $name
    if (Test-Path -LiteralPath $p) {
      $foreign.Add([ordered]@{ path = $p; note = 'left by upstream DemoIQ-Scout or an older certificate-based version; not removed here' })
    }
  }
}
# The administrator's stored sign-in lives in the per-user session folder, outside <ws>.
$signIn = Get-DemoIQSessionSecretPath -Name 'admin-session'
Add-Item 'session' $signIn

$token = "CLEANUP-$depthN"
$removed = New-Object System.Collections.Generic.List[object]
$errors = New-Object System.Collections.Generic.List[string]
$lockedFiles = New-Object System.Collections.Generic.List[object]

if ($Execute -and $Confirm -ne $token) {
  Out-Json ([ordered]@{ ok = $false; depth = $depthN; mode = 'blocked'
      error = "Refusing to delete: pass -Confirm '$token' to execute depth $depthN."
      confirmToken = $token; inventory = $inventory; foreign = $foreign })
  exit 0
}

function Test-FileLocked([string]$p) {
  try {
    $fs = [System.IO.File]::Open($p, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    $fs.Close(); return $false
  } catch [System.IO.IOException] { return $true } catch { return $false }
}

if ($Execute) {
  # An OPEN Office file (a seeded .docx still open, a browser tab holding it) must not
  # collapse a whole tree into one opaque error: retry once after a short beat, then walk
  # what survived and classify each file - a share-violation probe puts it in
  # lockedFiles[] so the user can close exactly those; everything deletable still goes.
  foreach ($it in $inventory) {
    if (-not (Test-Path -LiteralPath $it.path)) { continue }
    $removedOk = $false; $failMsg = $null
    foreach ($attempt in 1..2) {
      try { Remove-Item -LiteralPath $it.path -Recurse -Force -ErrorAction Stop; $removedOk = $true; break }
      catch { $failMsg = $_.Exception.Message; if ($attempt -eq 1) { Start-Sleep -Milliseconds 750 } }
    }
    if ($removedOk) { $removed.Add([ordered]@{ kind = $it.kind; path = $it.path }); continue }
    if (Test-Path -LiteralPath $it.path -PathType Container) {
      $lockedHere = 0; $otherHere = 0
      foreach ($f in @(Get-ChildItem -LiteralPath $it.path -Recurse -File -Force -ErrorAction SilentlyContinue)) {
        try { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop }
        catch {
          if (Test-FileLocked $f.FullName) { $lockedHere++; $lockedFiles.Add([ordered]@{ path = $f.FullName; kind = $it.kind }) }
          else { $otherHere++; $errors.Add("Failed to remove $($f.FullName): $($_.Exception.Message -replace '\s+', ' ')") }
        }
      }
      try { Remove-Item -LiteralPath $it.path -Recurse -Force -ErrorAction Stop } catch {}
      if ($lockedHere -gt 0) {
        $errors.Add("Partially removed $($it.path): $lockedHere file(s) are OPEN/locked (see lockedFiles) - close them and re-run this cleanup; everything else in the folder was removed.")
      } elseif (Test-Path -LiteralPath $it.path) {
        if ($otherHere -eq 0) { $errors.Add("Failed to remove $($it.path): $($failMsg -replace '\s+', ' ')") }
      } else {
        $removed.Add([ordered]@{ kind = $it.kind; path = $it.path; note = 'removed-after-per-file-pass' })
      }
    } elseif (Test-FileLocked $it.path) {
      $lockedFiles.Add([ordered]@{ path = $it.path; kind = $it.kind })
      $errors.Add("$($it.path) is OPEN/locked - close it and re-run this cleanup.")
    } else {
      $errors.Add("Failed to remove $($it.path): $($failMsg -replace '\s+', ' ')")
    }
  }
  # Depth 2 leaves no empty workspace behind (foreign files keep it in place).
  if ($depthN -ge 2 -and (Test-Path -LiteralPath $OutputFolder) -and
      -not (Get-ChildItem -LiteralPath $OutputFolder -Force -ErrorAction SilentlyContinue)) {
    try { Remove-Item -LiteralPath $OutputFolder -Force; $removed.Add([ordered]@{ kind = 'workspace'; path = $OutputFolder }) } catch {}
  }
}

$out = [ordered]@{
  ok           = ($errors.Count -eq 0 -and $lockedFiles.Count -eq 0)
  depth        = $depthN
  mode         = $(if ($Execute) { 'execute' } else { 'dry-run' })
  confirmToken = $token
  outputFolder = $OutputFolder
  inventory    = $inventory
  foreign      = $foreign
  removed      = $removed
  kept         = $kept
  lockedFiles  = $lockedFiles.ToArray()
  errors       = $errors
}
Out-Json $out
if (-not $out.ok) { exit 3 }
exit 0

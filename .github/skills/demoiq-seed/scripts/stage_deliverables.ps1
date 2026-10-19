<#
  stage_deliverables.ps1 - copy a run's LOCAL deliverables into a folder the user
  chose, so they can be opened or shared from there via file:/// links.

  The seeder writes its outputs under ~/.copilot/DemoIQ-Output-Scout. This helper copies
  the newest run outputs into:

      <WorkspacePath>/DemoIQ-Deliverables/<runstamp>/

  and prints the copied files (with file:/// URLs) as JSON.

  GitHub Copilot adaptation: the default end-of-run deliverable is the AoP runbook
  served from the run folder with serve_local.ps1 (no copy needed). Use this helper only
  when the user asks for copies in a specific folder. It REFUSES a destination inside a
  Git work tree (seeded tenant content must never be committed) unless -AllowGitWorkTree.

  Params:
    -SourceDir      the run's output dir holding the deliverables (e.g.
                    "<$ws>/runs/<runId>/DemoIQ-Output"). Required.
    -WorkspacePath  the destination folder the user chose. Required.
    -AllowGitWorkTree  permit a destination inside a Git work tree (explicit user request).
    -RunLog         optional path to the run log to include.
    -RunStamp       optional run stamp; derived from the AoP filename or now if omitted.

  By default ONLY the AoP runbook is staged (plus -RunLog if given): the AoP is the
  single end-of-run deliverable - it already records what was produced, who owns it,
  and where it lives in the tenant. Everything else is opt-in, staged only when the
  user explicitly asks for that file:
    -IncludeAudio      also stage the newest meeting_transcript_*.mp3.
    -IncludeTranscript also stage the newest meeting_transcript_*.txt.
    -IncludePreview    also stage content_preview.html. The preview belongs to the
                       pre-seed approval gate (Flow 1d step 5b, shown once before
                       seeding); an end-of-run preview link reads as a second,
                       competing summary of the same content.

  Output (one JSON line):
    { ok, runStamp, dest, destUrl, files:[{ kind, name, path, url }], errors:[] }
    (destUrl is the staged folder itself as a file:/// URL - link it in the
    "Deliverables" intro sentence so "staged into your workspace" answers "where?".)
#>
param(
  [Parameter(Mandatory)][string]$SourceDir,
  [Parameter(Mandatory)][string]$WorkspacePath,
  [string]$RunLog,
  [string]$RunStamp,
  [switch]$IncludeAudio,
  [switch]$IncludeTranscript,
  [switch]$IncludePreview,
  [switch]$AllowGitWorkTree
)

function Out-Json($o) { $o | ConvertTo-Json -Compress -Depth 6 }

# file:/// URL with spaces/special chars percent-encoded, forward slashes.
function To-FileUrl([string]$p) {
  $abs = [System.IO.Path]::GetFullPath($p)
  $u = [System.Uri]::new($abs)
  return $u.AbsoluteUri
}

$errors = @()
if (-not (Test-Path $SourceDir)) {
  Write-Output (Out-Json @{ ok = $false; error = "SourceDir not found: $SourceDir"; files = @(); errors = @() })
  exit 0
}

# Newest matcher
function Newest([string]$dir, [string]$pattern) {
  Get-ChildItem -Path $dir -Filter $pattern -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
}

# --- collect candidate deliverables (newest of each) ---
# Default = the AoP runbook only; audio/transcript/preview are staged solely on
# explicit user request via the Include* switches (see the header).
$picks = @()
$aop = Newest $SourceDir 'aop_demo_*.html'
if ($aop) { $picks += @{ kind = 'aop'; file = $aop } }
if ($IncludeTranscript) {
  $txt = Newest $SourceDir 'meeting_transcript_*.txt'
  if ($txt) { $picks += @{ kind = 'transcript'; file = $txt } }
}
if ($IncludeAudio) {
  $mp3 = Newest $SourceDir 'meeting_transcript_*.mp3'
  if ($mp3) { $picks += @{ kind = 'audio'; file = $mp3 } }
}
if ($IncludePreview) {
  $prev = Newest $SourceDir 'content_preview.html'
  if ($prev) { $picks += @{ kind = 'preview'; file = $prev } }
}
if ($RunLog -and (Test-Path $RunLog)) { $picks += @{ kind = 'log'; file = (Get-Item $RunLog) } }

# --- derive run stamp (from the AoP filename's yyyyMMdd_HHmmss, else now) ---
if (-not $RunStamp) {
  if ($aop -and $aop.Name -match '(\d{8}_\d{6})') { $RunStamp = $Matches[1] }
  else { $RunStamp = (Get-Date).ToString('yyyyMMdd_HHmmss') }
}

# --- never stage tenant content into a Git work tree (it could be committed) ---
if (-not $AllowGitWorkTree) {
  $probe = [System.IO.Path]::GetFullPath($WorkspacePath)
  while ($probe) {
    if (Test-Path -LiteralPath (Join-Path $probe '.git')) {
      Write-Output (Out-Json @{ ok = $false; error = "Refusing to stage DemoIQ deliverables inside the Git work tree '$probe' - choose a folder outside the repository (or pass -AllowGitWorkTree on explicit request)."; files = @(); errors = @() })
      exit 0
    }
    $parent = Split-Path -Parent $probe
    if (-not $parent -or $parent -eq $probe) { break }
    $probe = $parent
  }
}

# --- destination inside the chosen folder ---
$dest = Join-Path (Join-Path $WorkspacePath 'DemoIQ-Deliverables') $RunStamp
try { New-Item -ItemType Directory -Force -Path $dest | Out-Null }
catch { Write-Output (Out-Json @{ ok = $false; error = "Cannot create dest: $($_.Exception.Message)"; files = @(); errors = @() }); exit 0 }

$staged = @()
foreach ($p in $picks) {
  try {
    $target = Join-Path $dest $p.file.Name
    Copy-Item -Path $p.file.FullName -Destination $target -Force
    $staged += [ordered]@{ kind = $p.kind; name = $p.file.Name; path = $target; url = (To-FileUrl $target) }
  } catch {
    $errors += "Failed to copy $($p.file.Name): $($_.Exception.Message)"
  }
}

Write-Output (Out-Json ([ordered]@{
  ok       = ($staged.Count -gt 0)
  runStamp = $RunStamp
  dest     = $dest
  destUrl  = (To-FileUrl $dest)
  files    = $staged
  errors   = $errors
}))

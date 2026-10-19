<#
  stream_rtmp.ps1 - push a generated meeting MP3 into a LIVE Teams meeting via
  RTMP-In, using the exact FFmpeg command DemoIQ.html generates (engine parity).

  Bundled with DemoIQ (adopted verbatim from the DemoIQ-CoworkGuide skill, where it
  was validated end-to-end) - live streaming needs no other skill installed. This
  is the runnable streaming step of Flow 3. The browser steps (sign in to Teams
  web as the meeting organizer, start the meeting, enable RTMP-In, read the Ingest
  URL + Stream key) are performed by the user in their own browser; this script
  only does the FFmpeg push once it has the MP3, the RTMP ingest URL, and the
  stream key.

  Requires FFmpeg on PATH (Windows: winget install --id Gyan.FFmpeg -e --source winget;
  macOS: brew install ffmpeg; Linux: your distribution's ffmpeg package).
  The stream key is a secret - it is never printed in full.
#>
param(
  [Parameter(Mandatory = $true)][string]$Mp3,
  [Parameter(Mandatory = $true)][string]$StreamUrl,   # rtmp://...rtmpingest.../meeting/<id>
  [Parameter(Mandatory = $true)][string]$StreamKey,
  [switch]$WhatIfOnly                                 # print the command (key masked) and exit
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Mp3)) { Write-Error "MP3 not found: $Mp3" -ErrorAction Continue; exit 2 }
if ($StreamUrl -notmatch '^rtmps?://') { Write-Error "StreamUrl must start with rtmp:// or rtmps:// (got: $StreamUrl)" -ErrorAction Continue; exit 2 }
if ([string]::IsNullOrWhiteSpace($StreamKey)) { Write-Error 'StreamKey is empty.' -ErrorAction Continue; exit 2 }

$dest = if ($StreamUrl.EndsWith('/')) { $StreamUrl + $StreamKey } else { $StreamUrl + '/' + $StreamKey }
$maskedKey = if ($StreamKey.Length -gt 6) { $StreamKey.Substring(0, 3) + '...' + $StreamKey.Substring($StreamKey.Length - 3) } else { '***' }
$maskedDest = if ($StreamUrl.EndsWith('/')) { $StreamUrl + $maskedKey } else { $StreamUrl + '/' + $maskedKey }

Write-Host "Streaming MP3 : $Mp3" -ForegroundColor Cyan
Write-Host "To (masked)   : $maskedDest" -ForegroundColor DarkCyan
Write-Host "Press Q or Ctrl+C to stop." -ForegroundColor Gray

# Exact DemoIQ engine command (DemoIQ.html streamBuildFfmpegCmd):
#   ffmpeg -re -i "<mp3>" -b:a 128k -ar 44100 -acodec aac -threads 4 -f flv "<url>/<key>"
$ffArgs = @('-re', '-i', $Mp3, '-b:a', '128k', '-ar', '44100', '-acodec', 'aac', '-threads', '4', '-f', 'flv', $dest)

if ($WhatIfOnly) {
  $shown = ($ffArgs | ForEach-Object { if ($_ -eq $dest) { "`"$maskedDest`"" } elseif ($_ -match '\s') { "`"$_`"" } else { $_ } }) -join ' '
  Write-Host "ffmpeg $shown" -ForegroundColor Yellow
  exit 0
}

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
  . (Join-Path $PSScriptRoot 'platform.ps1')
  Write-Error "FFmpeg is not on PATH. Install it first:`n  $(Get-DemoIQInstallHint ffmpeg)`nthen reopen the shell and retry." -ErrorAction Continue
  exit 3
}

& ffmpeg @ffArgs
$code = $LASTEXITCODE
if ($code -eq 0) { Write-Host 'Stream finished.' -ForegroundColor Green }
else { Write-Host "FFmpeg exited with code $code." -ForegroundColor Yellow }
exit $code

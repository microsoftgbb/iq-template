<#
  platform.ps1 - cross-platform helpers for the demoiq-seed skill.

  This is the GitHub Copilot adaptation layer around the DemoIQ engine. Upstream
  DemoIQ-Scout targets Windows (DPAPI, WinForms, hidden windows); these helpers keep
  that Windows behavior and add equivalent macOS and Linux implementations, so every
  script can call ONE function per concern.

  Dot-source it from a sibling script:
      . (Join-Path $PSScriptRoot 'platform.ps1')

  Concerns covered:
    * workspace + private files      Get-DemoIQWorkspace, New-DemoIQPrivateDirectory,
                                     Set-DemoIQPrivateFile
    * detached background processes  Start-DemoIQDetachedProcess (macOS/Linux; Windows
                                     callers keep their upstream Start-Process/WMI path)
    * default browser                Open-DemoIQUrl
    * process inspection             Get-DemoIQPwshProcess
    * install hints                  Get-DemoIQInstallHint
    * deployment receipts            Get-DemoIQRepositoryRoot, Get-DemoIQRunnerReceipts,
                                     ConvertFrom-DemoIQJwtPayload
#>

$script:DemoIQPlatformDir = $PSScriptRoot

function Test-DemoIQWindows {
  # $IsWindows does not exist in Windows PowerShell 5.1 (Desktop edition).
  return [bool]($IsWindows -or $PSVersionTable.PSEdition -eq 'Desktop')
}

function Get-DemoIQWorkspace {
  # The single canonical DemoIQ home (configs, runs, scratch, the session key), outside
  # the repository. The folder name is kept from upstream DemoIQ-Scout.
  return (Join-Path (Join-Path $HOME '.copilot') 'DemoIQ-Output-Scout')
}

function Set-DemoIQPrivateFile {
  # Owner read/write only (0600) on macOS/Linux. Windows profile ACLs already
  # restrict the user's profile, so this is a no-op there.
  param([Parameter(Mandatory)][string]$Path)
  if (Test-DemoIQWindows) { return }
  [System.IO.File]::SetUnixFileMode($Path, [System.IO.UnixFileMode]'UserRead, UserWrite')
}

function New-DemoIQPrivateDirectory {
  # Create (if needed) and restrict a folder to its owner (0700) on macOS/Linux. A folder
  # inside the DemoIQ workspace also restricts the workspace root, so nothing in it (runs,
  # logs, local server state) is reachable by other local users.
  param([Parameter(Mandatory)][string]$Path)
  New-Item -ItemType Directory -Force -Path $Path | Out-Null
  if (-not (Test-DemoIQWindows)) {
    [System.IO.File]::SetUnixFileMode($Path, [System.IO.UnixFileMode]'UserRead, UserWrite, UserExecute')
    try {
      $ws = [System.IO.Path]::GetFullPath((Get-DemoIQWorkspace)).TrimEnd('/')
      $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('/')
      if ($full -ne $ws -and $full.StartsWith($ws + '/', [System.StringComparison]::Ordinal) -and (Test-Path -LiteralPath $ws -PathType Container)) {
        [System.IO.File]::SetUnixFileMode($ws, [System.IO.UnixFileMode]'UserRead, UserWrite, UserExecute')
      }
    } catch {}
  }
  return (Resolve-Path -LiteralPath $Path).Path
}

function Open-DemoIQUrl {
  <#
    Open a URL in the user's default browser. Returns $true when a browser launch was
    attempted, $false when this host has no browser to open (headless SSH, CI) - the
    caller then shows the URL for a manual click.
  #>
  param([Parameter(Mandatory)][string]$Url)
  try {
    if (Test-DemoIQWindows) { Start-Process $Url; return $true }
    if ($IsMacOS) {
      & /usr/bin/open $Url 2>$null
      return ($LASTEXITCODE -eq 0)
    }
    # Linux: VS Code / Codespaces terminals export $BROWSER (opens on the user's own
    # machine); desktop sessions have a display and xdg-open.
    if ($env:BROWSER) {
      $parts = @($env:BROWSER -split ':' | Where-Object { $_ })
      $exe = Get-Command $parts[0] -ErrorAction SilentlyContinue
      if ($exe) { Start-Process -FilePath $exe.Source -ArgumentList $Url; return $true }
    }
    if (($env:DISPLAY -or $env:WAYLAND_DISPLAY) -and (Get-Command xdg-open -ErrorAction SilentlyContinue)) {
      Start-Process -FilePath 'xdg-open' -ArgumentList $Url
      return $true
    }
  } catch {}
  return $false
}

function Start-DemoIQDetachedProcess {
  <#
    macOS/Linux: start a process in its OWN session (setsid via Node's detached
    spawn) so it outlives the calling shell, the Copilot tool call and any
    process-group kill. Stdout/stderr go to -LogFile (or are discarded).
    Returns the child's process ID. Windows callers keep their upstream
    Start-Process / WMI launch path.
  #>
  param(
    [Parameter(Mandatory)][string]$FilePath,
    [string[]]$ArgumentList = @(),
    [string]$LogFile,
    [string]$WorkingDirectory
  )
  $node = Get-Command node -ErrorAction SilentlyContinue
  if (-not $node) { throw 'Node.js is required to start a detached process on macOS/Linux (node was not found on PATH).' }
  $helper = Join-Path $script:DemoIQPlatformDir 'detach.js'
  $nodeArgs = @($helper)
  if ($LogFile) { $nodeArgs += @('--log', $LogFile) }
  if ($WorkingDirectory) { $nodeArgs += @('--cwd', $WorkingDirectory) }
  $nodeArgs += '--'
  $nodeArgs += $FilePath
  $nodeArgs += $ArgumentList
  $raw = & $node.Source @nodeArgs
  $parsed = $null
  try { $parsed = ($raw | Select-Object -Last 1) | ConvertFrom-Json } catch {}
  if ($LASTEXITCODE -ne 0 -or -not $parsed -or -not $parsed.ok) {
    $why = if ($parsed -and $parsed.error) { $parsed.error } else { ($raw -join ' ') }
    throw "Could not start the detached process: $why"
  }
  return [int]$parsed.pid
}

function Get-DemoIQPwshProcess {
  # Running PowerShell 7 processes with their command lines (all platforms).
  if (Test-DemoIQWindows) {
    return @(Get-CimInstance Win32_Process -Filter "Name = 'pwsh.exe'" -ErrorAction SilentlyContinue |
      ForEach-Object { [pscustomobject]@{ ProcessId = [int]$_.ProcessId; CommandLine = [string]$_.CommandLine } })
  }
  if ($IsLinux) {
    return @(Get-ChildItem -LiteralPath '/proc' -Directory -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match '^\d+$' } | ForEach-Object {
        try {
          $cmd = ([System.IO.File]::ReadAllText("/proc/$($_.Name)/cmdline")) -replace "`0", ' '
          if ($cmd -match '(^|/)pwsh(\s|$)') { [pscustomobject]@{ ProcessId = [int]$_.Name; CommandLine = $cmd.Trim() } }
        } catch {}
      })
  }
  $lines = & /bin/ps -axo 'pid=,command=' 2>$null
  return @($lines | ForEach-Object {
      if ($_ -match '^\s*(\d+)\s+(.*)$') {
        $procId = [int]$Matches[1]; $cmd = $Matches[2].Trim()
        if ($cmd -match '(^|/)pwsh(\s|$)') { [pscustomobject]@{ ProcessId = $procId; CommandLine = $cmd } }
      }
    })
}

function Get-DemoIQInstallHint {
  # One install command for the current OS (shown to the user, never run silently).
  param([Parameter(Mandatory)][ValidateSet('pwsh', 'node', 'az', 'ffmpeg')][string]$Tool)
  $os = if (Test-DemoIQWindows) { 'windows' } elseif ($IsMacOS) { 'macos' } else { 'linux' }
  $hints = @{
    pwsh   = @{ windows = 'winget install --id Microsoft.PowerShell -e'; macos = 'brew install --cask powershell'
                linux = 'see https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-linux' }
    node   = @{ windows = 'winget install --id OpenJS.NodeJS.LTS -e'; macos = 'brew install node'
                linux = 'install Node.js 18+ from your distribution or https://nodejs.org' }
    az     = @{ windows = 'winget install --id Microsoft.AzureCLI -e'; macos = 'brew install azure-cli'
                linux = 'see https://learn.microsoft.com/cli/azure/install-azure-cli-linux' }
    ffmpeg = @{ windows = 'winget install --id Gyan.FFmpeg -e --source winget'; macos = 'brew install ffmpeg'
                linux = 'sudo apt-get install -y ffmpeg (or your distribution''s package manager)' }
  }
  return $hints[$Tool][$os]
}

function Get-DemoIQRepositoryRoot {
  # scripts -> demoiq-seed -> skills -> .github -> repository root
  return (Resolve-Path -LiteralPath (Join-Path $script:DemoIQPlatformDir '../../../..')).Path
}

function Get-DemoIQRunnerReceipts {
  <#
    The deploy-platform runner's DemoIQ receipts (`deploy.py status --json` -> demoiq):
    tenant_id, client_id, delegated_scopes, consent, speech_resource_id, audio.
    -ReceiptsFile reads a saved copy of that JSON instead of running the runner, which
    makes no cloud calls for `status`. Returns $null until the demoiq phase completes.
  #>
  param([string]$ReceiptsFile)
  $text = $null
  if ($ReceiptsFile) {
    $text = Get-Content -LiteralPath $ReceiptsFile -Raw -ErrorAction Stop
  } else {
    $deploy = Join-Path (Get-DemoIQRepositoryRoot) '.github/skills/deploy-platform/scripts/deploy.py'
    if (-not (Test-Path -LiteralPath $deploy)) { throw "The deploy-platform runner was not found at $deploy." }
    foreach ($candidate in @(, @('python3')) + @(, @('python')) + @(, @('py', '-3'))) {
      $exe = Get-Command $candidate[0] -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
      if (-not $exe) { continue }
      $runnerArgs = @($candidate | Select-Object -Skip 1) + @($deploy, 'status', '--json')
      $out = & $exe.Source @runnerArgs 2>$null
      if ($LASTEXITCODE -eq 0 -and $out) { $text = $out -join "`n"; break }
    }
    if (-not $text) { throw 'Could not read the deployment receipts. Run: python3 .github/skills/deploy-platform/scripts/deploy.py status --json' }
  }
  return ($text | ConvertFrom-Json).demoiq
}

function ConvertFrom-DemoIQJwtPayload {
  # The claims of a JWT (no signature check: only for reading our own token's tid/upn/scp).
  param([string]$Token)
  try {
    $p = ($Token -split '\.')[1].Replace('-', '+').Replace('_', '/')
    switch ($p.Length % 4) { 2 { $p += '==' } 3 { $p += '=' } }
    return ([System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($p)) | ConvertFrom-Json)
  } catch { return $null }
}

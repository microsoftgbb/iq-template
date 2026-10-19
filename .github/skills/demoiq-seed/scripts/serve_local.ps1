<#
  serve_local.ps1 - tiny local static file server for the DemoIQ skill.

  WHY: the browser-automation tool refuses `file://` URLs, so the Config Builder and the
  content preview could not be opened directly and the agent had to improvise throwaway
  `python -m http.server` instances (with the wrong web root half the time). This is the
  ONE helper the skill uses for both: it serves a folder over http://127.0.0.1:<port>/ on
  a stable port and prints a machine-readable URL the agent can open.

  It binds only to the loopback interface (127.0.0.1) - it is not reachable from other
  machines. GET/HEAD serve files at/below -Root only (no directory traversal above the
  served root). The configs library is reachable ONLY through two opt-in routes
  (-AllowSave), and each request must carry the per-server token embedded in the served
  URL (header X-Save-Token, or a `token` query) - other local processes don't have it:
    POST /save?name=<file>   the Config Builder's save. Accepts a PLAIN DemoIQ config: one
                             JSON object (<= 1 MB) with a string adminUpn containing '@',
                             existingAppId a GUID or empty, and string u{i}upn values. An
                             encrypted cdx-enc-v1 envelope is refused (422) with the hint
                             to convert it once with scripts/convert_config.ps1. adminPwd
                             and existingCertThumbprint (upstream engine fields) are always
                             written as empty strings. The filename is sanitized to
                             `DemoIQ_config*.json`, the file is written atomically and
                             owner-only (0600 on macOS/Linux; the profile ACL protects it
                             on Windows) ONLY into the configs library (or -SaveDir), and
                             an existing file is never overwritten (a numeric suffix is
                             added). A saved config is never a draft: `demoiqDraft` is
                             stripped. When the server was started with -PrefillConfig
                             naming a DRAFT ("demoiqDraft": true, written only by
                             draft_config.ps1), the completed config REPLACES it: after the
                             new file is written, that one draft is deleted - only if it
                             still exists, still parses with demoiqDraft: true and is not
                             the file just written. The reply is { ok, name, path, savedTo }
                             plus replacedDraft: <draft file name> (or replacedDraftError:
                             <reason> - the save itself still succeeded). Nothing else is
                             ever deleted; a non-draft -PrefillConfig file is never touched.
    GET /config?name=<file>  returns one plain config from the library - how the Builder
                             opens prefilled from a drafted config (-PrefillConfig). The
                             name must be a bare, already-sanitized `DemoIQ_config*.json`
                             file name (400 otherwise); a missing file is 404 and an
                             encrypted one 422 (convert hint). The admin password and
                             certificate thumbprint fields are returned empty.
  Nothing else is ever writable, and nothing else in the library is ever readable or
  deleted.

  Params:
    -Root            folder to serve (required unless -Probe). Only files at/below this
                     folder are served.
    -Path            (optional) a file under -Root to surface a direct URL for (e.g. the
                     Config Builder html or the preview html). May include a ?query string.
    -Port            (optional) fixed port; default 0 = prefer the well-known DemoIQ port
                     53713 (stable across relaunches, so the Config Builder's in-browser
                     draft - keyed to the page origin - survives a server restart), falling
                     back to a free random port when 53713 is taken.
    -Probe  <url>    health-check an ALREADY-RUNNING serve URL instead of serving:
                     exit 0 = alive, 4 = dead. Session checkpoints can kill a NON-detached
                     background server, so flows probe a previously shared URL before
                     showing it again - and re-launch when the probe reports dead.
    -Detach          launch the server as an INDEPENDENT background process (created via
                     WMI so it escapes the calling terminal's process tree - session
                     checkpoints cannot kill it), print the URL/PID/log markers, and return
                     immediately. The child auto-exits after -TimeoutMinutes.
    -TimeoutMinutes  hard server lifetime in minutes (default 120), so detached instances
                     never linger. Applies to foreground runs too.
    -OpenBrowser     ALSO open the served URL (the -Path direct URL when given, else the
                     base URL) in the USER'S DEFAULT browser via Start-Process, right after
                     the server is confirmed up. This is the canonical way the Config
                     Builder / preview / walkthrough reach the user's screen: their own
                     browser, their own profile - NO browser-automation session. (An
                     agent-driven Edge automation profile hit session conflicts in a live
                     run and stranded the page in a profile the user never looks at.)
    -PrefillAppId    (optional) the deployment's DemoIQ app client ID (from the deployment
                     receipts: deploy.py status) to pre-fill in the Config Builder, where
                     it is read-only. The script URL-encodes it and appends ?appId=... to
                     the -Path URL itself - ALWAYS prefer these three params over
                     hand-building the query (speechResourceId contains '/'; one
                     hand-encoding slip = a silently empty form - a real failure mode).
    -PrefillSpeechId (optional) Speech resource ID to pre-fill (appended as speechId=...).
    -PrefillConfig   (optional) file name of a drafted config in the configs library
                     (`DemoIQ_config*.json`; a path is reduced to its file name), appended
                     as config=... The Builder loads it through GET /config and opens
                     prefilled, so the user reviews it and adds the demo-user passwords.
                     When that file is a draft (demoiqDraft: true) at startup, the first
                     successful POST /save replaces it (see POST /save above). Needs
                     -AllowSave; a WARN line names a draft that is not in the library.
                     (The upstream DemoIQ-Scout skill also minted engagement-tracking IDs
                     here; this adaptation never collects or forwards them.)
    -AllowSave       enable the token-gated POST /save and GET /config routes.
    -SaveDir         the configs library /save writes to and /config reads from (default:
                     ~/.copilot/DemoIQ-Output-Scout/configs, kept owner-only).
    -SaveToken       explicit save token (tests / direct foreground runs). Default: a
                     fresh GUID per server.
    -SaveTokenFile   (internal) ephemeral bootstrap file the -Detach parent writes the
                     token into; the child reads AND DELETES it at startup. This keeps the
                     token OFF the child's command line (transcript headers record command
                     lines) and out of the child's log.
    -LogFile         (internal) transcript log path for the detached child.

  Token at rest: while a save-enabled server runs, the live token exists in the served
  URL (browser-side), in the state file below and - for a moment, -Detach only - in the
  bootstrap file the child deletes at startup. Both files are owner-only from their
  first byte (0600) inside an owner-only `serve` folder (0700) on macOS/Linux; the
  Windows profile ACL protects them there. The token dies with the server; stdout only
  ever carries it inside the [SERVE-URL] fragment.

  Output (stdout, as it starts):
    [SERVE] http://127.0.0.1:<port>/          <- base URL (server root)
    [SERVE-URL] http://127.0.0.1:<port>/...   <- direct URL for -Path (when given; includes
                                                 #saveToken=... as the URL FRAGMENT when
                                                 -AllowSave - fragments never leave the browser)
    [SERVE-PID] <pid>                         <- detached child PID (-Detach only)
    [SERVE-LOG] <path>                        <- detached child log (-Detach only)
    [SAVED] <path>                            <- a POST /save just landed a config
    [SERVE] Replaced draft <name> ...         <- ... and deleted the draft it completes
    [PROBE] alive|dead <url>                  <- -Probe mode result
    [SERVE-OPENED] <url> (default browser)    <- -OpenBrowser launched the user's browser
    [SERVE] WARN: ...                         <- e.g. a -PrefillConfig draft that is not in
                                                 the configs library
  The agent launches with -OpenBrowser (the page opens in the USER'S default browser)
  and pastes the same [SERVE-URL] into chat as the clickable fallback link.

  While a server runs, an owner-only state file `$ws/serve/<port>.json` records { url,
  pid, root, allowSave, saveToken, saveDir, startedAt, timeoutMinutes } so a LATER
  session can rediscover a detached server: read it, -Probe the url, and REUSE the server
  (token included) instead of double-launching. It is removed on graceful exit; always
  probe before trusting it.
#>
param(
  [string]$Root,
  [string]$Path,
  [int]$Port = 0,
  [string]$Probe,
  [switch]$Detach,
  [int]$TimeoutMinutes = 120,
  [switch]$OpenBrowser,
  [switch]$AllowSave,
  [string]$SaveDir,
  [string]$SaveToken,
  [string]$SaveTokenFile,
  [string]$LogFile,
  [string]$PrefillAppId,
  [string]$PrefillSpeechId,
  [string]$PrefillConfig
)

. (Join-Path $PSScriptRoot 'platform.ps1')

# Open a URL in the user's DEFAULT browser (their own profile) - never an automation
# profile. Best-effort: a failure to open must never take the server down. A host
# without a browser (SSH, Codespaces over gh codespace ssh) gets a clear hint instead.
function Open-InDefaultBrowser([string]$Url) {
  if (-not $Url) { return }
  if (Open-DemoIQUrl $Url) {
    Write-Host "[SERVE-OPENED] $Url (default browser)"
  } else {
    Write-Host "[SERVE] WARN: this host has no browser to open - share the URL for a manual click. When the browser is on another machine (Codespaces/SSH), forward the port privately first (for example: gh codespace ports forward ${Port}:${Port})." -ForegroundColor DarkYellow
  }
}

$ErrorActionPreference = 'Stop'

# Detached child: hydrate the save token from the parent's ephemeral bootstrap file BEFORE
# the transcript starts, and delete the file - the token never rides the child's command
# line (transcript headers record command lines) and never appears in the child's log.
if ($SaveTokenFile -and (Test-Path $SaveTokenFile)) {
  try {
    $SaveToken = (Get-Content $SaveTokenFile -Raw).Trim()
    Remove-Item $SaveTokenFile -Force
  } catch {}
}

# Detached child: self-log via transcript (WMI-created processes have no captured stdout).
if ($LogFile) { try { Start-Transcript -Path $LogFile -Force | Out-Null } catch {} }

# ---- -Probe mode: is a previously started server still answering? ----
if ($Probe) {
  try {
    $r = Invoke-WebRequest -Uri $Probe -Method Head -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
    Write-Host "[PROBE] alive $Probe (HTTP $($r.StatusCode))"
    exit 0
  } catch {
    # Any HTTP status < 500 still proves the SERVER is alive (e.g. 404 on a moved
    # file) - only a refused/failed connection means the server is gone.
    $sc = $null; try { $sc = [int]$_.Exception.Response.StatusCode } catch {}
    if ($sc -ge 200 -and $sc -lt 500) { Write-Host "[PROBE] alive $Probe (HTTP $sc)"; exit 0 }
    Write-Host "[PROBE] dead $Probe ($($_.Exception.Message))"
    exit 4
  }
}

if (-not $Root) {
  Write-Host '[SERVE] ERROR: -Root is required (or use -Probe <url>).' -ForegroundColor Red
  exit 2
}
if (-not (Test-Path $Root -PathType Container)) {
  Write-Host "[SERVE] ERROR: root folder not found: $Root" -ForegroundColor Red
  exit 2
}
$rootFull = (Resolve-Path $Root).Path.TrimEnd('\', '/')
# Separator-terminated prefix for the containment guard below: a bare prefix match
# would let root C:\demo also "contain" C:\demo2\secret. Compared case-insensitively
# on Windows/macOS and case-sensitively on Linux (case-sensitive file systems).
$script:CasePaths = [bool]$IsLinux
function ConvertTo-PathKey([string]$p) { if ($script:CasePaths) { $p } else { $p.ToLowerInvariant() } }
$rootCmp = ConvertTo-PathKey $rootFull
$rootPrefix = if ($rootCmp.EndsWith('\') -or $rootCmp.EndsWith('/')) { $rootCmp } else { $rootCmp + [System.IO.Path]::DirectorySeparatorChar }

# Pick a port: prefer the stable DemoIQ port so the builder's origin-keyed draft survives
# relaunches; fall back to a free random loopback port when it's taken.
$PreferredPort = 53713
if (-not $Port -or $Port -le 0) {
  try {
    $tryBind = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $PreferredPort)
    $tryBind.Start(); $tryBind.Stop()
    $Port = $PreferredPort
  } catch {
    $tmp = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $tmp.Start(); $Port = ([System.Net.IPEndPoint]$tmp.LocalEndpoint).Port; $tmp.Stop()
  }
}
$base = "http://127.0.0.1:$Port/"

# ---- The configs library: plain DemoIQ configs (POST /save, GET /config) ----------
$script:EncryptedConfigHint = 'This is an encrypted DemoIQ config (cdx-enc-v1). The GitHub Copilot adaptation uses plain, owner-only configs: convert it once with scripts/convert_config.ps1 in your own terminal, then use the converted file.'
# Keep date-like strings as strings (pwsh 7.5+), so a config round-trips unchanged.
$script:JsonReadArgs = @{}
if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $script:JsonReadArgs.DateKind = 'String' }

function ConvertTo-DemoIQConfigName([string]$Name) {
  # Basename only, safe charset, forced DemoIQ_config prefix + .json.
  if ([string]::IsNullOrWhiteSpace($Name)) { $Name = 'DemoIQ_config.json' }
  $Name = [System.IO.Path]::GetFileName($Name)
  $Name = ($Name -replace '[^A-Za-z0-9 ._()\-]', '_').Trim(' ', '.')
  if ($Name -notmatch '\.json$') { $Name += '.json' }
  if ($Name -notlike 'DemoIQ_config*') { $Name = 'DemoIQ_config_' + $Name }
  return $Name
}

function Test-DemoIQConfigName([string]$Name) {
  # Reads accept only a bare, ALREADY-sanitized name: no separators, so no traversal.
  return (-not [string]::IsNullOrWhiteSpace($Name)) -and ($Name -notmatch '[\\/]') -and
    ($Name -ceq (ConvertTo-DemoIQConfigName $Name))
}

function Read-DemoIQConfigJson([string]$Text) {
  # A plain DemoIQ config is ONE JSON object. Returns @{ Config } or @{ Status; Error }.
  $t = $Text.TrimStart([char]0xFEFF).Trim()
  $cfg = $null
  if ($t.StartsWith('{', [System.StringComparison]::Ordinal)) {
    try { $cfg = ConvertFrom-Json -InputObject $t -Depth 20 @script:JsonReadArgs -ErrorAction Stop } catch { $cfg = $null }
  }
  if ($cfg -isnot [System.Management.Automation.PSCustomObject]) {
    return @{ Status = 422; Error = 'not a DemoIQ config: expected one JSON object' }
  }
  if ([string]$cfg.v -eq 'cdx-enc-v1') { return @{ Status = 422; Error = $script:EncryptedConfigHint } }
  return @{ Config = $cfg }
}

function Get-DemoIQSaveProblem($Config) {
  # What POST /save requires of a plain config (the Config Builder always sends this).
  $upn = $Config.PSObject.Properties['adminUpn']
  if (-not $upn -or $upn.Value -isnot [string] -or [string]::IsNullOrWhiteSpace($upn.Value) -or -not $upn.Value.Contains('@')) {
    return 'adminUpn must be the tenant administrator''s sign-in name (user@domain)'
  }
  $app = $Config.PSObject.Properties['existingAppId']
  if ($app -and $null -ne $app.Value -and -not ($app.Value -is [string] -and
      ($app.Value -eq '' -or $app.Value -match '\A[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\z'))) {
    return 'existingAppId must be the deployment''s DemoIQ app client ID (a GUID) or empty'
  }
  foreach ($p in $Config.PSObject.Properties) {
    if ($p.Name -match '^u\d+upn$' -and $p.Value -isnot [string]) { return "$($p.Name) must be a string" }
  }
  return $null
}

function ConvertTo-DemoIQConfigBytes($Config) {
  # adminPwd and existingCertThumbprint stay in the upstream engine's shape but are
  # ALWAYS empty: the administrator signs in per run and the app has no certificate.
  foreach ($k in 'adminPwd', 'existingCertThumbprint') {
    $p = $Config.PSObject.Properties[$k]
    if ($p) { $p.Value = '' } else { $Config | Add-Member -NotePropertyName $k -NotePropertyValue '' }
  }
  return [System.Text.UTF8Encoding]::new($false).GetBytes((ConvertTo-Json -InputObject $Config -Depth 64) + "`n")
}

function Write-DemoIQPrivateBytes([string]$Path, [byte[]]$Bytes) {
  # A NEW file, owner-only from its first byte on macOS/Linux (0600 at creation); the
  # Windows profile ACL protects the library there.
  $opts = [System.IO.FileStreamOptions]::new()
  $opts.Mode = [System.IO.FileMode]::CreateNew
  $opts.Access = [System.IO.FileAccess]::Write
  if (-not (Test-DemoIQWindows)) { $opts.UnixCreateMode = [System.IO.UnixFileMode]'UserRead, UserWrite' }
  $fs = [System.IO.FileStream]::new($Path, $opts)
  try { $fs.Write($Bytes, 0, $Bytes.Length); $fs.Flush($true) } finally { $fs.Dispose() }
  Set-DemoIQPrivateFile -Path $Path
}

function Write-DemoIQPrivateText([string]$Path, [string]$Text) {
  # Owner-only from the first byte (via a private temp file in the same folder), atomically
  # replacing an older file - e.g. a state file a killed server left behind.
  $tmp = Join-Path (Split-Path -Parent $Path) ('.' + [guid]::NewGuid().ToString('N') + '.tmp')
  try {
    Write-DemoIQPrivateBytes -Path $tmp -Bytes ([System.Text.UTF8Encoding]::new($false).GetBytes($Text))
    [System.IO.File]::Move($tmp, $Path, $true)
  } finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
}

function Save-DemoIQConfigFile([string]$Name, [byte[]]$Bytes) {
  # Atomic and never overwriting: write a private temp file in the library, then move it
  # to the first free name (Move refuses an existing destination, so even a racing save
  # cannot clobber a config). Returns the saved path, or $null when no name is free.
  $tmp = Join-Path $SaveDir ('.' + [guid]::NewGuid().ToString('N') + '.tmp')
  $stem = [System.IO.Path]::GetFileNameWithoutExtension($Name)
  try {
    Write-DemoIQPrivateBytes -Path $tmp -Bytes $Bytes
    for ($i = 0; $i -lt 1000; $i++) {
      $dest = if ($i -eq 0) { Join-Path $SaveDir $Name } else { Join-Path $SaveDir ('{0}_{1}.json' -f $stem, $i) }
      if (Test-Path -LiteralPath $dest) { continue }
      try { [System.IO.File]::Move($tmp, $dest); return $dest }
      catch { if (-not (Test-Path -LiteralPath $dest)) { throw } }
    }
    return $null
  } finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
}

function Test-DemoIQDraftFile([string]$Path) {
  # A draft is a plain config in the library with the top-level boolean
  # "demoiqDraft": true (written only by draft_config.ps1).
  try {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf) -or (Get-Item -LiteralPath $Path).Length -gt 1MB) { return $false }
    $parsed = Read-DemoIQConfigJson ([System.IO.File]::ReadAllText($Path))
    return [bool]($parsed.Config -and $parsed.Config.demoiqDraft -is [bool] -and $parsed.Config.demoiqDraft)
  } catch { return $false }
}

function Remove-DemoIQReplacedDraft([string]$SavedPath) {
  # After a successful save, delete the ONE draft this server was started with
  # (-PrefillConfig) - only if it still exists, is still a draft and is not the file
  # just written. Returns @{ Name } when deleted, @{ Error } when deleting failed (the
  # save itself stands), or @{} when there is nothing to replace.
  $draft = $script:DraftPath
  if (-not $draft) { return @{} }
  $same = (ConvertTo-PathKey ([System.IO.Path]::GetFullPath($draft))) -ceq (ConvertTo-PathKey ([System.IO.Path]::GetFullPath($SavedPath)))
  if ($same -or -not (Test-DemoIQDraftFile $draft)) { return @{} }
  $name = [System.IO.Path]::GetFileName($draft)
  try {
    Remove-Item -LiteralPath $draft -Force -ErrorAction Stop
    $script:DraftPath = $null
    Write-Host "[SERVE] Replaced draft $name - deleted now that $([System.IO.Path]::GetFileName($SavedPath)) is saved"
    return @{ Name = $name }
  } catch {
    Write-Host "[SERVE] WARN: could not delete the replaced draft ${name}: $($_.Exception.Message)" -ForegroundColor DarkYellow
    return @{ Error = "could not delete the draft ${name}: $($_.Exception.Message)" }
  }
}

# Save-endpoint setup (token + the configs library).
if ($AllowSave) {
  if (-not $SaveToken) { $SaveToken = [guid]::NewGuid().ToString('N') }
  # The library holds demo-user passwords: owner-only (0700 on macOS/Linux), as
  # find_config.ps1 keeps it. An existing custom -SaveDir keeps its own mode.
  if (-not $SaveDir) { $SaveDir = New-DemoIQPrivateDirectory (Join-Path (Get-DemoIQWorkspace) 'configs') }
  elseif (-not (Test-Path -LiteralPath $SaveDir -PathType Container)) { $SaveDir = New-DemoIQPrivateDirectory $SaveDir }
  $SaveDir = (Resolve-Path -LiteralPath $SaveDir).Path
}

# -PrefillConfig: a drafted config in the library, by bare file name. When it is a draft
# (demoiqDraft: true) at startup, the config the Builder saves replaces it.
$prefillConfigName = $null
$script:DraftPath = $null
if ($PrefillConfig) {
  $prefillConfigName = [System.IO.Path]::GetFileName($PrefillConfig.Trim())
  $why = if (-not $AllowSave) { 'needs -AllowSave (the Builder loads drafts through the token-gated GET /config)' }
         elseif (-not (Test-DemoIQConfigName $prefillConfigName)) { 'is not a safe DemoIQ_config*.json file name (letters, digits, space . _ ( ) -)' }
         elseif (-not (Test-Path -LiteralPath (Join-Path $SaveDir $prefillConfigName) -PathType Leaf)) { "is not in the configs library ($SaveDir)" }
  if ($why) { Write-Host "[SERVE] WARN: -PrefillConfig '$prefillConfigName' $why - the Config Builder opens without that draft." -ForegroundColor DarkYellow }
  elseif (Test-DemoIQDraftFile (Join-Path $SaveDir $prefillConfigName)) { $script:DraftPath = Join-Path $SaveDir $prefillConfigName }
}

# Direct URL for -Path (computed once; used by the detach parent AND the server itself).
$directUrl = $null
if ($Path) {
  # Split any ?query, map the file part to a root-relative URL, re-append the query.
  $q = ''
  $filePart = $Path
  $qi = $Path.IndexOf('?')
  if ($qi -ge 0) { $filePart = $Path.Substring(0, $qi); $q = $Path.Substring($qi) }
  # Prefill params: encoded and appended HERE so callers never hand-build the
  # query string (see the -Prefill* param docs above).
  $prefillPairs = @()
  if ($PrefillAppId)      { $prefillPairs += ('appId='    + [System.Uri]::EscapeDataString($PrefillAppId.Trim())) }
  if ($PrefillSpeechId)   { $prefillPairs += ('speechId=' + [System.Uri]::EscapeDataString($PrefillSpeechId.Trim())) }
  if ($prefillConfigName) { $prefillPairs += ('config='   + [System.Uri]::EscapeDataString($prefillConfigName)) }
  if ($prefillPairs.Count) {
    $sep = if ($q) { '&' } else { '?' }
    $q = $q + $sep + ($prefillPairs -join '&')
  }
  $rel = $null
  if (Test-Path $filePart) {
    $full = (Resolve-Path $filePart).Path
    if ($full.ToLowerInvariant().StartsWith($rootFull.ToLowerInvariant())) {
      $rel = $full.Substring($rootFull.Length).TrimStart('\', '/').Replace('\', '/')
    }
  } else {
    # Treat -Path as already root-relative if it isn't an on-disk path.
    $rel = $filePart.TrimStart('\', '/').Replace('\', '/')
  }
  if ($rel) {
    # The save token rides the URL #fragment, NOT the ?query: fragments never leave the
    # browser (not sent in any request, never in server/transcript logs, invisible to
    # probes), which is the rule for secret-bearing values in local URLs. The builder
    # reads the hash first and falls back to the query, so older query-style URLs keep
    # working during the transition.
    $frag = if ($AllowSave) { "#saveToken=$SaveToken" } else { '' }
    $directUrl = $base + $rel + $q + $frag
  }
}

# Holds the state file and the -Detach token bootstrap file, so it is owner-only (0700).
$stateDir = Join-Path (Get-DemoIQWorkspace) 'serve'

# ---- -Detach: spawn an INDEPENDENT server process and return immediately ----
if ($Detach) {
  $stateDir = New-DemoIQPrivateDirectory $stateDir
  $log = Join-Path $stateDir "serve_$Port.log"
  Remove-Item $log -Force -ErrorAction SilentlyContinue
  $pwshExe = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
  if (-not $pwshExe) { $pwshExe = (Get-Command powershell).Source }
  $self = $PSCommandPath

  # -WindowStyle is a Windows-only pwsh switch (pwsh exits 64 with it on macOS/Linux).
  $childArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass')
  if (Test-DemoIQWindows) { $childArgs += @('-WindowStyle', 'Hidden') }
  $childArgs += @('-File', $self, '-Root', $rootFull, '-Port', $Port,
    '-TimeoutMinutes', $TimeoutMinutes, '-LogFile', $log)
  $tokenBootstrap = $null
  if ($AllowSave) {
    # Hand the token to the child via an ephemeral file, NOT the command line - transcript
    # headers (and process listings) record command lines. The child deletes it at startup.
    $tokenBootstrap = Join-Path $stateDir "serve_$Port.token"
    Write-DemoIQPrivateText -Path $tokenBootstrap -Text $SaveToken
    $childArgs += @('-AllowSave', '-SaveTokenFile', $tokenBootstrap, '-SaveDir', $SaveDir)
    # The child serves /save, so it must know the draft a save replaces (a file name,
    # not a secret).
    if ($prefillConfigName) { $childArgs += @('-PrefillConfig', $prefillConfigName) }
  }

  # Windows: prefer WMI process creation - the child is parented under the WMI host,
  # OUTSIDE this terminal's process tree/job, so a session checkpoint killing this tree
  # can't take the server down with it. macOS/Linux: a new session via detach.js.
  $childPid = $null
  if (-not (Test-DemoIQWindows)) {
    try {
      $childPid = Start-DemoIQDetachedProcess -FilePath $pwshExe -ArgumentList $childArgs `
        -LogFile (Join-Path $stateDir "serve_$Port.stdout.log")
    } catch {
      if ($tokenBootstrap) { Remove-Item $tokenBootstrap -Force -ErrorAction SilentlyContinue }
      Write-Host "[SERVE] ERROR: could not start the detached server: $($_.Exception.Message)" -ForegroundColor Red
      exit 3
    }
  }
  $qArgs = $childArgs | ForEach-Object {
    $s = "$_"
    if ($s -match '[\s"]') {
      $s = $s -replace '"', '\"'
      # Double any TRAILING backslashes so "...\" can't swallow the closing quote
      # (classic CommandLineToArgvW parsing bug).
      $s = $s -replace '(\\+)$', '$1$1'
      '"' + $s + '"'
    } else { $s }
  }
  $cmdline = ('"{0}" {1}' -f $pwshExe, ($qArgs -join ' '))
  if (-not $childPid) {
    try {
      $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmdline }
      if ($r.ReturnValue -eq 0 -and $r.ProcessId) { $childPid = [int]$r.ProcessId }
    } catch {}
  }
  if (-not $childPid) {
    # Fallback: plain detached child (still independent of THIS script, but shares the
    # caller's process tree, so a tree kill can reach it - WMI is the preferred path).
    $p = Start-Process -FilePath $pwshExe -ArgumentList $childArgs -WindowStyle Hidden -PassThru
    $childPid = $p.Id
  }

  # Wait for the child to answer (any HTTP < 500 proves it's up).
  $alive = $false
  for ($i = 0; $i -lt 20 -and -not $alive; $i++) {
    Start-Sleep -Milliseconds 400
    try {
      Invoke-WebRequest -Uri $base -Method Head -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop | Out-Null
      $alive = $true
    } catch {
      $sc = $null; try { $sc = [int]$_.Exception.Response.StatusCode } catch {}
      if ($sc -ge 200 -and $sc -lt 500) { $alive = $true }
    }
  }
  if (-not $alive) {
    if ($tokenBootstrap) { Remove-Item $tokenBootstrap -Force -ErrorAction SilentlyContinue }
    Write-Host "[SERVE] ERROR: detached server did not come up on $base - see $log" -ForegroundColor Red
    exit 3
  }
  Write-Host "[SERVE] $base"
  if ($directUrl) { Write-Host "[SERVE-URL] $directUrl" }
  Write-Host "[SERVE-PID] $childPid"
  Write-Host "[SERVE-LOG] $log"
  if ($AllowSave) { Write-Host "[SERVE] Config library ON - the Config Builder saves plain, owner-only configs straight into (and loads drafts from) $SaveDir" }
  Write-Host "[SERVE] Detached - survives this terminal/session; auto-exits after $TimeoutMinutes minutes."
  if ($OpenBrowser) { Open-InDefaultBrowser $(if ($directUrl) { $directUrl } else { $base }) }
  if ($LogFile) { try { Stop-Transcript | Out-Null } catch {} }
  exit 0
}

# ---- Serve ------------------------------------------------------------------
$mime = @{
  '.html' = 'text/html; charset=utf-8'; '.htm' = 'text/html; charset=utf-8'
  '.js' = 'text/javascript; charset=utf-8'; '.mjs' = 'text/javascript; charset=utf-8'
  '.css' = 'text/css; charset=utf-8'; '.json' = 'application/json; charset=utf-8'
  '.svg' = 'image/svg+xml'; '.png' = 'image/png'; '.jpg' = 'image/jpeg'; '.jpeg' = 'image/jpeg'
  '.gif' = 'image/gif'; '.webp' = 'image/webp'; '.ico' = 'image/x-icon'
  '.woff' = 'font/woff'; '.woff2' = 'font/woff2'; '.ttf' = 'font/ttf'
  '.mp3' = 'audio/mpeg'; '.txt' = 'text/plain; charset=utf-8'; '.map' = 'application/json'
}

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
try { $listener.Start() }
catch {
  Write-Host "[SERVE] ERROR: could not start on port $Port - $($_.Exception.Message)" -ForegroundColor Red
  exit 3
}

# State file: lets a LATER session rediscover (probe + reuse) this server. It holds the
# save token, so it is owner-only from its first byte, in the owner-only serve folder.
$stateDir = New-DemoIQPrivateDirectory $stateDir
$stateFile = Join-Path $stateDir "$Port.json"
Write-DemoIQPrivateText -Path $stateFile -Text (@{ url = $base; pid = $PID; root = $rootFull; port = $Port
    allowSave = [bool]$AllowSave; saveToken = $SaveToken; saveDir = $SaveDir
    startedAt = (Get-Date).ToString('o'); timeoutMinutes = $TimeoutMinutes
  } | ConvertTo-Json -Compress)

Write-Host "[SERVE] $base"
if ($directUrl) { Write-Host "[SERVE-URL] $directUrl" }
if ($AllowSave) { Write-Host "[SERVE] Config library ON - POST /save and GET /config (token-gated) write/read plain, owner-only DemoIQ_config*.json in $SaveDir" }
Write-Host "[SERVE] Ready. Auto-exit after $TimeoutMinutes minutes. Press Ctrl+C (or kill this terminal) to stop."
if ($OpenBrowser) { Open-InDefaultBrowser $(if ($directUrl) { $directUrl } else { $base }) }

function Send-Bytes {
  param($Res, [int]$Code, [byte[]]$Bytes)
  $Res.StatusCode = $Code
  $Res.ContentType = 'application/json; charset=utf-8'
  $Res.Headers['Cache-Control'] = 'no-store'
  $Res.ContentLength64 = $Bytes.Length
  $Res.OutputStream.Write($Bytes, 0, $Bytes.Length)
  $Res.Close()
}
function Send-Json {
  param($Res, [int]$Code, $Obj)
  Send-Bytes $Res $Code ([Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Obj -Compress -Depth 8)))
}
function Test-SaveToken($Req) {
  $tok = $Req.Headers['X-Save-Token']; if (-not $tok) { $tok = $Req.QueryString['token'] }
  return [bool]($SaveToken -and $tok -and ($tok -ceq $SaveToken))
}

$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
try {
  $stop = $false
  while (-not $stop -and $listener.IsListening) {
    # Async accept with a 1s tick so the lifetime deadline is enforced even when idle.
    $ctxTask = $listener.GetContextAsync()
    while (-not $ctxTask.Wait(1000)) {
      if ((Get-Date) -gt $deadline) { $stop = $true; break }
    }
    if ($stop) { Write-Host "[SERVE] Auto-exit: $TimeoutMinutes-minute lifetime reached."; break }
    $ctx = $ctxTask.Result
    try {
      $req = $ctx.Request; $res = $ctx.Response

      # ---- POST /save: the ONLY writable route (opt-in, token-gated, plain config) ----
      if ($req.HttpMethod -eq 'POST') {
        if (-not $AllowSave -or $req.Url.AbsolutePath -ne '/save') { $res.StatusCode = 405; $res.Close(); continue }
        if (-not (Test-SaveToken $req)) { Send-Json $res 403 @{ ok = $false; error = 'bad or missing save token' }; continue }
        $maxBytes = 1MB
        if ($req.ContentLength64 -gt $maxBytes) { Send-Json $res 413 @{ ok = $false; error = 'config too large (limit 1 MB)' }; continue }
        $ms = [System.IO.MemoryStream]::new(); $buf = [byte[]]::new(65536)
        while (($n = $req.InputStream.Read($buf, 0, $buf.Length)) -gt 0) {
          $ms.Write($buf, 0, $n)
          if ($ms.Length -gt $maxBytes) { break }
        }
        if ($ms.Length -gt $maxBytes) { Send-Json $res 413 @{ ok = $false; error = 'config too large (limit 1 MB)' }; continue }
        if ($ms.Length -eq 0) { Send-Json $res 400 @{ ok = $false; error = 'empty request body' }; continue }
        # Only a plain DemoIQ config object is accepted - never arbitrary content.
        $parsed = Read-DemoIQConfigJson ([Text.Encoding]::UTF8.GetString($ms.ToArray()))
        if (-not $parsed.Config) { Send-Json $res $parsed.Status @{ ok = $false; error = $parsed.Error }; continue }
        $problem = Get-DemoIQSaveProblem $parsed.Config
        if ($problem) { Send-Json $res 422 @{ ok = $false; error = $problem }; continue }
        # A saved config is never a draft.
        foreach ($mark in @($parsed.Config.PSObject.Properties | Where-Object { $_.Name -eq 'demoiqDraft' })) { $parsed.Config.PSObject.Properties.Remove($mark.Name) }
        $dest = Save-DemoIQConfigFile -Name (ConvertTo-DemoIQConfigName ([string]$req.QueryString['name'])) -Bytes (ConvertTo-DemoIQConfigBytes $parsed.Config)
        if (-not $dest) { Send-Json $res 409 @{ ok = $false; error = 'too many configs with this name - choose another' }; continue }
        Write-Host "[SAVED] $dest"
        $reply = [ordered]@{ ok = $true; name = [System.IO.Path]::GetFileName($dest); path = $dest; savedTo = $dest }
        $replaced = Remove-DemoIQReplacedDraft -SavedPath $dest
        if ($replaced.Name) { $reply.replacedDraft = $replaced.Name } elseif ($replaced.Error) { $reply.replacedDraftError = $replaced.Error }
        Send-Json $res 200 $reply
        continue
      }

      # ---- GET /config: ONE plain config from the library (opt-in, token-gated) ----
      if ($req.Url.AbsolutePath -eq '/config') {
        if (-not $AllowSave) { Send-Json $res 404 @{ ok = $false; error = 'not found' }; continue }
        if ($req.HttpMethod -ne 'GET') { $res.StatusCode = 405; $res.Close(); continue }
        if (-not (Test-SaveToken $req)) { Send-Json $res 403 @{ ok = $false; error = 'bad or missing save token' }; continue }
        $name = [string]$req.QueryString['name']
        if (-not (Test-DemoIQConfigName $name)) { Send-Json $res 400 @{ ok = $false; error = 'name must be a bare DemoIQ_config*.json file name (letters, digits, space . _ ( ) -) in the configs library' }; continue }
        $file = Join-Path $SaveDir $name
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { Send-Json $res 404 @{ ok = $false; error = "no $name in the configs library" }; continue }
        if ((Get-Item -LiteralPath $file).Length -gt 1MB) { Send-Json $res 413 @{ ok = $false; error = 'config too large (limit 1 MB)' }; continue }
        $parsed = Read-DemoIQConfigJson ([System.IO.File]::ReadAllText($file))
        if (-not $parsed.Config) { Send-Json $res $parsed.Status @{ ok = $false; error = $parsed.Error }; continue }
        Send-Bytes $res 200 (ConvertTo-DemoIQConfigBytes $parsed.Config)
        continue
      }

      if ($req.HttpMethod -ne 'GET' -and $req.HttpMethod -ne 'HEAD') { $res.StatusCode = 405; $res.Close(); continue }
      $relUrl = [System.Uri]::UnescapeDataString($req.Url.AbsolutePath).TrimStart('/')
      if ([string]::IsNullOrWhiteSpace($relUrl)) { $relUrl = 'index.html' }
      $target = Join-Path $rootFull ($relUrl -replace '/', '\')
      $targetFull = [System.IO.Path]::GetFullPath($target)
      # Containment guard: never serve outside the root. The candidate must BE the
      # root or sit below it (root + separator) - not merely share the root as a
      # string prefix.
      $targetCmp = ConvertTo-PathKey $targetFull
      if (-not ($targetCmp -eq $rootCmp -or $targetCmp.StartsWith($rootPrefix))) { $res.StatusCode = 403; $res.Close(); continue }
      if (-not (Test-Path $targetFull -PathType Leaf)) { $res.StatusCode = 404; $res.Close(); continue }
      $ext = [System.IO.Path]::GetExtension($targetFull).ToLowerInvariant()
      $res.ContentType = if ($mime.ContainsKey($ext)) { $mime[$ext] } else { 'application/octet-stream' }
      $res.Headers['Cache-Control'] = 'no-store'
      $bytes = [System.IO.File]::ReadAllBytes($targetFull)
      $res.ContentLength64 = $bytes.Length
      if ($req.HttpMethod -eq 'GET') { $res.OutputStream.Write($bytes, 0, $bytes.Length) }
      $res.Close()
    } catch {
      try { $ctx.Response.StatusCode = 500; $ctx.Response.Close() } catch {}
    }
  }
} finally {
  try { $listener.Stop(); $listener.Close() } catch {}
  try { Remove-Item $stateFile -Force -ErrorAction SilentlyContinue } catch {}
  if ($LogFile) { try { Stop-Transcript | Out-Null } catch {} }
}

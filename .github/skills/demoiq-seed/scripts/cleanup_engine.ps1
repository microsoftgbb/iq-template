# cleanup_engine.ps1 - the DemoIQ tenant cleanup engine (demoiq-seed, the GitHub Copilot
# adaptation of DemoIQ - https://aka.ms/demoiq).
#
# Removes the fictional demo content DemoIQ seeded into a NON-PRODUCTION demo tenant, driven
# by the seeder's own manifest (cleanup.config) or, failing that, by its run logs. Delegated
# tokens only - no certificate, no app-only token, no stored password:
#   * the administrator's per-run sign-in deletes the Teams team (DELETE /groups/{id}) and
#     the group and meeting chats (DELETE /chats/{id}: soft delete, restorable for 7 days,
#     one delete per second per tenant);
#   * each demo user's own token, through /me, removes that user's meetings and meeting
#     residue, seeded mail and notifications and OneDrive files, and lists the chats to
#     delete. Demo users sign in with a password, which works only while their MFA and
#     Conditional Access are relaxed - so deletion runs inside a CLEANUP RUN of the
#     generated seeder, which relaxes MFA/CA exactly as for seeding and restores it after;
#   * the administrator is part of every sweep (invites, notifications) with their own token.
#
# A FUNCTION LIBRARY. The generated seeder inlines this text at its top level and
# scripts/cleanup_tenant.ps1 (the read-only preview) dot-sources it, so the top level holds
# only comments and functions, every function name contains DemoIQCleanup, HTTP goes only
# through Invoke-RestMethod, the public functions record expected failures instead of
# throwing, and nothing prints or stores a token or password. Log lines are tagged [PURGE].
#
# Public functions, in call order:
#   Read-DemoIQCleanupManifest     the manifest to clean up from (explicit file, the admin's
#                                  OneDrive copy, local copies, run logs; AllRuns merges)
#   New-DemoIQCleanupPlan          what to delete for LastRun or AllRuns
#   Get-DemoIQCleanupUserToken     a demo user's delegated token (public-client password grant)
#   Invoke-DemoIQCleanup           delete, with the demo users' and the administrator's tokens
#   Update-DemoIQCleanupManifest   prune the deleted runs from every manifest copy
#   Clear-DemoIQCleanupLocalState  clear the deleted runs' seed ledgers (their own run folders
#                                  only), optionally archive their deliverables
# and the building blocks Get-DemoIQCleanupRunsFromLogs, Get-DemoIQCleanupSeedRunFolders and
# Invoke-DemoIQCleanupGraph (Microsoft Graph with HTTP 429 retries).

# ---------------------------------------------------------------- error helpers

function Get-DemoIQCleanupStatusCode {
  # The HTTP status of a failed Invoke-RestMethod call, or $null (no HTTP response).
  param($ErrorRecord)
  try {
    $code = $ErrorRecord.Exception.Response.StatusCode.value__
    if ($code) { return [int]$code }
  } catch {}
  try {
    $m = [regex]::Match([string]$ErrorRecord.Exception.Message, 'status code does not indicate success:\s*(\d{3})|returned an error:\s*\((\d{3})\)')
    if ($m.Success) { return [int]($m.Groups[1].Value + $m.Groups[2].Value) }
  } catch {}
  return $null
}

function Get-DemoIQCleanupRetryAfter {
  # Seconds to wait before re-issuing a throttled request: the Retry-After header, else
  # -Default; at least 1, at most 300.
  param($ErrorRecord, [int]$Default = 5)
  $wait = $null
  try {
    $ra = $ErrorRecord.Exception.Response.Headers.RetryAfter
    if ($ra -and $null -ne $ra.Delta) { $wait = [int][Math]::Ceiling($ra.Delta.TotalSeconds) }
    elseif ($ra -and $null -ne $ra.Date) { $wait = [int][Math]::Ceiling(($ra.Date - [DateTimeOffset]::UtcNow).TotalSeconds) }
  } catch {}
  if ($null -eq $wait) {
    try {
      $v = [string](@($ErrorRecord.Exception.Response.Headers.GetValues('Retry-After'))[0])
      if ($v -match '^\s*(\d+)\s*$') { $wait = [int]$Matches[1] }
    } catch {}
  }
  if ($null -eq $wait) { $wait = $Default }
  if ($wait -lt 1) { $wait = 1 }
  if ($wait -gt 300) { $wait = 300 }
  return $wait
}

function Get-DemoIQCleanupErrorText {
  # One short line describing a failed request: status, service error code and message.
  # Never a token or a request body.
  param($ErrorRecord)
  $status = Get-DemoIQCleanupStatusCode $ErrorRecord
  $code = $null; $detail = $null
  $raw = $null
  try { $raw = [string]$ErrorRecord.ErrorDetails.Message } catch {}
  if ($raw) {
    try {
      $j = $raw | ConvertFrom-Json -ErrorAction Stop
      if ($j.error -and $j.error.code) { $code = [string]$j.error.code; $detail = [string]$j.error.message }
      elseif ($j.error) { $code = [string]$j.error; $detail = [string]$j.error_description }
    } catch {}
  }
  if (-not $detail) { try { $detail = [string]$ErrorRecord.Exception.Message } catch {} }
  if (-not $detail) { $detail = 'unknown error' }
  $line = (($detail -split "`r?`n")[0]).Trim()
  if ($line.Length -gt 240) { $line = $line.Substring(0, 240) + '...' }
  $head = (@($(if ($status) { "HTTP $status" }), $code) | Where-Object { $_ }) -join ' '
  if ($head) { return "${head}: $line" }
  return $line
}

function Get-DemoIQCleanupAadCode {
  # The AADSTS error number of a failed sign-in (for example '50076'), or $null.
  param($ErrorRecord)
  $raw = ''
  try { $raw = [string]$ErrorRecord.ErrorDetails.Message } catch {}
  if ($raw) {
    try {
      $j = $raw | ConvertFrom-Json -ErrorAction Stop
      $codes = @(@($j.error_codes) | Where-Object { $_ })
      if ($codes.Count -gt 0) { return [string]$codes[0] }
    } catch {}
  }
  try { $raw += ' ' + [string]$ErrorRecord.Exception.Message } catch {}
  $m = [regex]::Match($raw, 'AADSTS(\d{5,6})')
  if ($m.Success) { return $m.Groups[1].Value }
  return $null
}

function Get-DemoIQCleanupErrorHint {
  # What to do about a permission failure (HTTP 403 / Authorization_RequestDenied), or $null.
  param($ErrorRecord, [switch]$Administrator)
  $status = Get-DemoIQCleanupStatusCode $ErrorRecord
  $text = ''
  try { $text = [string]$ErrorRecord.Exception.Message + ' ' + [string]$ErrorRecord.ErrorDetails.Message } catch {}
  if ($status -eq 403 -or $text -match 'Authorization_RequestDenied|Access is denied|insufficient privileges|ErrorAccessDenied|\b403\b') {
    $consent = 'an administrator reruns: deploy.py up --with-demoiq --through demoiq --grant-demoiq-consent'
    if ($Administrator) {
      return "PERMISSION DENIED - the signed-in administrator needs a role that may delete it (Global Administrator; Teams Administrator for chats), or the DemoIQ app's delegated consent is missing ($consent)"
    }
    return "PERMISSION DENIED - the DemoIQ app's delegated consent is missing ($consent)"
  }
  return $null
}

function Add-DemoIQCleanupHint {
  # Record a permission hint once per user and area (never once per item).
  param([hashtable]$State, [string]$Area, $ErrorRecord, [switch]$Administrator)
  $hint = Get-DemoIQCleanupErrorHint -ErrorRecord $ErrorRecord -Administrator:$Administrator
  if (-not $hint) { return }
  $key = $Area.ToLowerInvariant()
  if ($State.Hinted.ContainsKey($key)) { return }
  $State.Hinted[$key] = $true
  $State.Problems.Add("${Area}: $(Get-DemoIQCleanupErrorText $ErrorRecord) - $hint")
}

# ---------------------------------------------------------------- small helpers

function Get-DemoIQCleanupOneDriveManifestUri {
  # The manifest copy on the signed-in administrator's own OneDrive.
  return 'https://graph.microsoft.com/v1.0/me/drive/root:/DemoIQ-Output/cleanup.config:/content'
}

function Get-DemoIQCleanupRunKey {
  # A run's identity: its timestamp, normalized so that text and parsed dates compare equal.
  param($Run)
  $ts = $null
  try { $ts = $Run.timestamp } catch {}
  if ($null -eq $ts) { return '' }
  $inv = [System.Globalization.CultureInfo]::InvariantCulture
  if ($ts -is [datetime]) { return $ts.ToString('yyyy-MM-dd HH:mm:ss', $inv) }
  if ($ts -is [datetimeoffset]) { return $ts.DateTime.ToString('yyyy-MM-dd HH:mm:ss', $inv) }
  $s = ([string]$ts).Trim()
  $parsed = [datetime]::MinValue
  $formats = [string[]]@('yyyy-MM-dd HH:mm:ss', 'yyyy-MM-ddTHH:mm:ss')
  if ([datetime]::TryParseExact($s, $formats, $inv, [System.Globalization.DateTimeStyles]::None, [ref]$parsed)) {
    return $parsed.ToString('yyyy-MM-dd HH:mm:ss', $inv)
  }
  return $s
}

function Get-DemoIQCleanupRunLabel {
  # A run's display time: the manifest timestamp as written, a log-sourced run's log name as
  # yyyy-MM-dd HH:mm.
  param($Run)
  $ts = $null
  try { $ts = $Run.timestamp } catch {}
  $inv = [System.Globalization.CultureInfo]::InvariantCulture
  if ($ts -is [datetime]) { return $ts.ToString('yyyy-MM-dd HH:mm:ss', $inv) }
  $when = '' + $ts
  $tm = [regex]::Match($when, 'DemoIQ_(\d{8})_(\d{6})')
  if ($tm.Success) {
    try { $when = [datetime]::ParseExact($tm.Groups[1].Value + $tm.Groups[2].Value, 'yyyyMMddHHmmss', $inv).ToString('yyyy-MM-dd HH:mm', $inv) } catch {}
  }
  return $when
}

function Get-DemoIQCleanupSubjectKey {
  # A meeting subject without Canceled:/RE:/FW: prefixes, lower-cased, for residue matching.
  param([string]$Subject)
  return ((('' + $Subject) -replace '^(Canceled|Cancelled):\s*', '' -replace '^RE:\s*', '' -replace '^FW:\s*', '').ToLower().Trim())
}

function Get-DemoIQCleanupUniqueUsers {
  # User principal names, de-duplicated case-insensitively, first spelling kept, in order.
  param([object[]]$Users)
  $seen = @{}
  foreach ($u in @($Users)) {
    if ($u -isnot [string] -or -not $u.Trim()) { continue }
    $k = $u.Trim().ToLowerInvariant()
    if ($seen.ContainsKey($k)) { continue }
    $seen[$k] = $true
    $u.Trim()
  }
}

function ConvertFrom-DemoIQCleanupManifestText {
  # A manifest object from what a file or Graph returned (parsed object, JSON text or bytes).
  param($Content)
  if ($null -eq $Content) { throw 'empty manifest' }
  if ($Content -is [byte[]]) { $Content = [System.Text.Encoding]::UTF8.GetString($Content) }
  if ($Content -is [string]) {
    $text = $Content.TrimStart([char]0xFEFF).Trim()
    if (-not $text) { throw 'empty manifest' }
    $Content = $text | ConvertFrom-Json -ErrorAction Stop
  }
  if ($Content -is [System.Management.Automation.PSCustomObject] -or $Content -is [System.Collections.IDictionary]) { return $Content }
  throw 'not a DemoIQ cleanup manifest'
}

function Read-DemoIQCleanupManifestFile {
  # A local cleanup.config, parsed. Throws when it cannot be read or parsed.
  param([Parameter(Mandatory)][string]$Path)
  return (ConvertFrom-DemoIQCleanupManifestText -Content ([System.IO.File]::ReadAllText($Path)))
}

function Get-DemoIQCleanupManifestRuns {
  # The runs a manifest lists (none when it lists none). Wrap the call in @() to count them.
  param($Manifest)
  if ($null -eq $Manifest) { return }
  $runs = if ($Manifest -is [System.Collections.IDictionary]) { $Manifest['runs'] } else { $Manifest.runs }
  @($runs) | Where-Object { $null -ne $_ }
}

function New-DemoIQCleanupManifestCopy {
  # A copy of a manifest's top-level fields with these runs (and a matching table of contents).
  param($Manifest, [object[]]$Runs)
  $kept = @($Runs | Where-Object { $null -ne $_ })
  $copy = [ordered]@{}
  if ($Manifest -is [System.Collections.IDictionary]) { foreach ($k in $Manifest.Keys) { $copy[[string]$k] = $Manifest[$k] } }
  elseif ($null -ne $Manifest) { foreach ($p in $Manifest.PSObject.Properties) { $copy[$p.Name] = $p.Value } }
  if ($copy.Contains('tableOfContents')) {
    $toc = @(); $i = 1
    foreach ($r in $kept) { $toc += [pscustomobject]@{ run = $i; timestamp = $r.timestamp; mode = $r.mode; admin = $r.adminUpn }; $i++ }
    $copy['tableOfContents'] = $toc
  }
  $copy['runs'] = $kept
  return [pscustomobject]$copy
}

function Test-DemoIQCleanupRunTargeted {
  # Whether a run is one of the targeted runs (matched by timestamp).
  param($Run, [hashtable]$Keys)
  $k = Get-DemoIQCleanupRunKey $Run
  return [bool]($k -and $Keys.ContainsKey($k))
}

function Test-DemoIQCleanupCleanupRunFolder {
  # A cleanup run's own folder (named *-cleanup, or run.json kind "cleanup") is not a seed run.
  param([Parameter(Mandatory)][string]$Path)
  if ((Split-Path -Leaf $Path) -like '*-cleanup') { return $true }
  $runJson = Join-Path $Path 'run.json'
  if (Test-Path -LiteralPath $runJson -PathType Leaf) {
    try { if ([string]((Get-Content -LiteralPath $runJson -Raw | ConvertFrom-Json).kind) -eq 'cleanup') { return $true } } catch {}
  }
  return $false
}

function Get-DemoIQCleanupSeedRunFolders {
  # The workspace's SEED run folders (<ws>/runs/<runId>), oldest first by name (run ids start
  # with their creation time). Cleanup run folders are left out.
  param([Parameter(Mandatory)][string]$Workspace)
  $runsRoot = Join-Path $Workspace 'runs'
  if (-not (Test-Path -LiteralPath $runsRoot -PathType Container)) { return }
  Get-ChildItem -LiteralPath $runsRoot -Directory -ErrorAction SilentlyContinue | Sort-Object Name |
    Where-Object { -not (Test-DemoIQCleanupCleanupRunFolder -Path $_.FullName) }
}

function Get-DemoIQCleanupRunFolderCreated {
  # When a run folder was created: run.json createdAt, else the yyyyMMdd_HHmm[ss] stamp its
  # name starts with; $null when neither is known.
  param([Parameter(Mandatory)][string]$Path)
  $inv = [System.Globalization.CultureInfo]::InvariantCulture
  $d = [datetime]::MinValue
  try {
    $created = (Get-Content -LiteralPath (Join-Path $Path 'run.json') -Raw -ErrorAction Stop | ConvertFrom-Json).createdAt
    if ($created -is [datetime]) { return $(if ($created.Kind -eq [System.DateTimeKind]::Utc) { $created.ToLocalTime() } else { $created }) }
    if ($created -and [datetime]::TryParse([string]$created, $inv, [System.Globalization.DateTimeStyles]::None, [ref]$d)) { return $d }
  } catch {}
  $m = [regex]::Match((Split-Path -Leaf $Path), '^(\d{8})_(\d{4})(\d{2})?')
  if ($m.Success) {
    $stamp = $m.Groups[1].Value + $m.Groups[2].Value + $(if ($m.Groups[3].Success) { $m.Groups[3].Value } else { '00' })
    if ([datetime]::TryParseExact($stamp, 'yyyyMMddHHmmss', $inv, [System.Globalization.DateTimeStyles]::None, [ref]$d)) { return $d }
  }
  return $null
}

function Get-DemoIQCleanupOwnRunKeys {
  # The identities of the runs seeded FROM a run folder: the runs its manifests list - as
  # -Recorded (path -> run keys, read before the manifests were pruned) and as they list them
  # now - that are not older than the folder (earlier runs are history a Full-mode seed copied
  # in), plus the runs its own logs record (log names, the identity of a log-sourced run).
  param([Parameter(Mandatory)][string]$Folder, [hashtable]$Recorded)
  $keys = [ordered]@{}
  $created = Get-DemoIQCleanupRunFolderCreated -Path $Folder
  $prefix = [System.IO.Path]::GetFullPath($Folder).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
  $candidates = [System.Collections.Generic.List[string]]::new()
  if ($Recorded) {
    foreach ($p in @($Recorded.Keys)) {
      if (([string]$p).StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { foreach ($k in @($Recorded[$p])) { $candidates.Add([string]$k) } }
    }
  }
  foreach ($f in @(Get-ChildItem -LiteralPath $Folder -Recurse -Filter 'cleanup.config' -File -ErrorAction SilentlyContinue)) {
    try { foreach ($r in @(Get-DemoIQCleanupManifestRuns (Read-DemoIQCleanupManifestFile -Path $f.FullName))) { $candidates.Add((Get-DemoIQCleanupRunKey $r)) } } catch {}
  }
  $inv = [System.Globalization.CultureInfo]::InvariantCulture
  foreach ($k in $candidates) {
    if (-not $k -or $keys.Contains($k)) { continue }
    $t = [datetime]::MinValue
    if ($created -and [datetime]::TryParseExact($k, 'yyyy-MM-dd HH:mm:ss', $inv, [System.Globalization.DateTimeStyles]::None, [ref]$t) -and $t -lt $created) { continue }
    $keys[$k] = $true
  }
  $logs = Join-Path $Folder 'logs'
  if (Test-Path -LiteralPath $logs -PathType Container) {
    foreach ($l in @(Get-ChildItem -LiteralPath $logs -Filter 'DemoIQ_*.log' -File -ErrorAction SilentlyContinue)) { if (-not $keys.Contains($l.Name)) { $keys[$l.Name] = $true } }
  }
  return @($keys.Keys)
}

function Get-DemoIQCleanupTokenFor {
  # The access token for a user (cached per user), or $null. A user without a token is
  # reported once, then skipped by every sweep.
  param([hashtable]$State, [string]$Upn)
  if (-not $Upn -or -not $Upn.Trim()) { return $null }
  $key = $Upn.Trim().ToLowerInvariant()
  if (-not $State.Tokens.ContainsKey($key)) {
    $found = $null
    try { $found = @(& $State.GetToken $Upn.Trim()) | Where-Object { $_ -is [string] -and $_ } | Select-Object -Last 1 } catch { $found = $null }
    $State.Tokens[$key] = [string]$found
    if (-not $found) {
      $State.Tokenless.Add($Upn.Trim())
      $State.Problems.Add("$($Upn.Trim()): no token - their mailbox, drive and chats were not swept")
      Write-Host "[PURGE] $($Upn.Trim()): no token - their mailbox, drive and chats are skipped" -ForegroundColor DarkYellow
    }
  }
  $t = $State.Tokens[$key]
  if ($t) { return $t }
  return $null
}

# ---------------------------------------------------------------- run logs and manifests

function Get-DemoIQCleanupRunsFromLogs {
  # Manifest-shaped runs rebuilt from the seeder's transcripts (DemoIQ_*.log), oldest first
  # (log names carry the start time). The logs hold the team, channel, meeting and chat ids,
  # file names and email subjects; a file's owner is not logged, so log-sourced files carry
  # owner '*' (looked up in every demo user's drive). Logs that seeded nothing deletable are
  # skipped. -LogsFolder takes one folder or several (one per run folder).
  # Outputs the runs; wrap the call in @() to count them.
  param([string[]]$LogsFolder)
  $logFiles = @()
  foreach ($folder in @($LogsFolder)) {
    if (-not $folder -or -not (Test-Path -LiteralPath $folder -PathType Container)) { continue }
    $logFiles += @(Get-ChildItem -LiteralPath $folder -Filter 'DemoIQ_*.log' -File -ErrorAction SilentlyContinue)
  }
  $runs = @()
  foreach ($f in @($logFiles | Sort-Object Name)) {
    $lines = @(Get-Content -LiteralPath $f.FullName -ErrorAction SilentlyContinue)
    if ($lines.Count -eq 0) { continue }
    $groupId = $null; $teamName = $null; $channelCount = 0
    $meetings = @(); $fileNames = @(); $emails = @(); $chats = @(); $users = @{}
    $pendingChatTopic = $null
    foreach ($l in $lines) {
      $m = [regex]::Match($l, "\[CHANNEL\] Group '(.*?)' created \(([0-9a-fA-F-]{36})\)")
      if ($m.Success -and -not $groupId) { $teamName = $m.Groups[1].Value.Trim(); $groupId = $m.Groups[2].Value }
      if ($l -match "\[CHANNEL\] OK: Channel '.+?' created \(") { $channelCount++ }
      $m = [regex]::Match($l, "\[MEETING\] OK:\s*'(.+?)'\s*by\s*(\S+)\s*\((.+?)\)")
      if ($m.Success) {
        $meetings += [pscustomobject]@{ subject = $m.Groups[1].Value.Trim(); organizer = $m.Groups[2].Value.Trim(); eventId = $m.Groups[3].Value.Trim() }
        $users[$m.Groups[2].Value.Trim().ToLower()] = $m.Groups[2].Value.Trim()
      }
      $m = [regex]::Match($l, "\[(?:WORD|PPT|EXCEL)\] OK: Uploaded '?(.+?\.(?:docx|pptx|xlsx))'?")
      if ($m.Success) { $fileNames += $m.Groups[1].Value.Trim() }
      $m = [regex]::Match($l, "\[EMAIL\] OK[^:]*:\s*(\S+)\s*->\s*'(.+?)'")
      if ($m.Success) { $emails += $m.Groups[2].Value.Trim(); $users[$m.Groups[1].Value.Trim().ToLower()] = $m.Groups[1].Value.Trim() }
      $m = [regex]::Match($l, "\[TEAMS\] Creating group chat:\s*'(.+?)'")
      if ($m.Success) { $pendingChatTopic = $m.Groups[1].Value.Trim() }
      $m = [regex]::Match($l, "\[TEAMS\] OK: Group chat created \((19:.+?@thread\.v2)\)")
      if ($m.Success) { $chats += [pscustomobject]@{ id = $m.Groups[1].Value.Trim(); topic = $pendingChatTopic }; $pendingChatTopic = $null }
    }
    if (-not $groupId -and $meetings.Count -eq 0 -and $fileNames.Count -eq 0 -and $emails.Count -eq 0 -and $chats.Count -eq 0) { continue }
    $runs += [pscustomobject]@{
      timestamp = $f.Name
      team      = [pscustomobject]@{ groupId = $groupId; displayName = $teamName; channels = @(if ($channelCount -gt 0) { 1..$channelCount }) }
      meetings  = $meetings
      files     = @($fileNames | Select-Object -Unique | ForEach-Object { [pscustomobject]@{ type = ''; name = $_; owner = '*' } })
      emails    = @($emails | ForEach-Object { [pscustomobject]@{ subject = $_ } })
      chats     = $chats
      users     = @($users.Values)
    }
  }
  return $runs
}

function Read-DemoIQCleanupManifest {
  # The seed manifest to clean up from, as
  #   [pscustomobject]@{ Manifest; Source; PlanSource ('manifest'|'logs'); LocalPath;
  #                      LocalPaths; FromOneDrive; OneDriveManifest; Errors; LocalRunKeys }
  # or $null when no manifest and no run log lists a run. Sources: -CleanupConfigPath, the
  # administrator's OneDrive copy (only with -AdminToken, a scriptblock returning the
  # administrator's access token) and every cleanup.config under -SearchFolders.
  #   LastRun  the first source that lists a run, in this order: -CleanupConfigPath, the
  #            OneDrive copy, the newest local cleanup.config (by write time).
  #   AllRuns  every source merged: runs de-duplicated by timestamp (first seen kept: explicit
  #            file, OneDrive, then local copies newest first) and ordered oldest to newest.
  # A manifest that lists no run (one already pruned, for example) does not end the search.
  # LocalPaths: the local manifests to prune after deletion - AllRuns: every local manifest
  # that lists a run; LastRun: the local manifests holding the chosen source's last run
  # (normally one file). LocalPath = LocalPaths[0] or $null. FromOneDrive: the OneDrive copy
  # contributed (LastRun: it is the source or holds its last run); OneDriveManifest is that
  # copy. LocalRunKeys: every local manifest read -> the run identities it listed when read
  # (Clear-DemoIQCleanupLocalState still knows them once the manifests are pruned). The run
  # logs (-LogsFolder, one or more folders) are used when no manifest lists a run, or always
  # with -FromLogs. Unreadable sources are recorded in Errors and skipped.
  param(
    [string]$CleanupConfigPath,
    [string[]]$SearchFolders,
    [scriptblock]$AdminToken,
    [string[]]$LogsFolder,
    [switch]$FromLogs,
    [ValidateSet('LastRun', 'AllRuns')][string]$Scope = 'LastRun'
  )
  $problems = [System.Collections.Generic.List[string]]::new()
  $localKeys = [ordered]@{}
  $info = $null
  if (-not $FromLogs) {
    try {
      $explicit = $null; $oneDrive = $null
      $locals = [System.Collections.Generic.List[object]]::new()
      $seenPaths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
      if ($CleanupConfigPath) {
        if (Test-Path -LiteralPath $CleanupConfigPath -PathType Leaf) {
          $full = (Resolve-Path -LiteralPath $CleanupConfigPath).ProviderPath
          [void]$seenPaths.Add($full)
          try {
            $m = Read-DemoIQCleanupManifestFile -Path $full
            $explicit = @{ Kind = 'local'; Path = $full; Desc = $full; Manifest = $m; Runs = @(Get-DemoIQCleanupManifestRuns $m) }
          } catch { $problems.Add("cleanup manifest ${full}: $($_.Exception.Message)") }
        } else { $problems.Add("cleanup manifest not found: $CleanupConfigPath") }
      }
      if ($AdminToken) {
        try {
          $tok = @(& $AdminToken) | Where-Object { $_ -is [string] -and $_ } | Select-Object -Last 1
          if (-not $tok) { throw 'no administrator token' }
          $raw = Invoke-DemoIQCleanupGraph -Method GET -Uri (Get-DemoIQCleanupOneDriveManifestUri) -Token $tok
          $m = ConvertFrom-DemoIQCleanupManifestText -Content $raw
          $oneDrive = @{ Kind = 'onedrive'; Path = $null; Desc = 'admin OneDrive DemoIQ-Output/cleanup.config'; Manifest = $m; Runs = @(Get-DemoIQCleanupManifestRuns $m) }
        } catch {
          if ((Get-DemoIQCleanupStatusCode $_) -eq 404) { Write-Host "[PURGE] No manifest on the administrator's OneDrive (DemoIQ-Output/cleanup.config)" -ForegroundColor DarkGray }
          else { $problems.Add("admin OneDrive manifest: $(Get-DemoIQCleanupErrorText $_)") }
        }
      }
      $found = @()
      foreach ($sf in @($SearchFolders)) {
        if (-not $sf -or -not (Test-Path -LiteralPath $sf -PathType Container)) { continue }
        $found += @(Get-ChildItem -LiteralPath $sf -Recurse -Filter 'cleanup.config' -File -ErrorAction SilentlyContinue)
      }
      foreach ($fi in @($found | Sort-Object LastWriteTime -Descending)) {
        if (-not $seenPaths.Add($fi.FullName)) { continue }
        try {
          $m = Read-DemoIQCleanupManifestFile -Path $fi.FullName
          $locals.Add(@{ Kind = 'local'; Path = $fi.FullName; Desc = $fi.FullName; Manifest = $m; Runs = @(Get-DemoIQCleanupManifestRuns $m) })
        } catch { $problems.Add("cleanup manifest $($fi.FullName): $($_.Exception.Message)") }
      }
      foreach ($src in @(@($explicit) + @($locals) | Where-Object { $_ })) {
        $localKeys[$src.Path] = @($src.Runs | ForEach-Object { Get-DemoIQCleanupRunKey $_ } | Where-Object { $_ })
      }

      $candidates = @(@($explicit, $oneDrive) + @($locals) | Where-Object { $_ -and $_.Runs.Count -gt 0 })
      if ($candidates.Count -gt 0) {
        if ($Scope -eq 'AllRuns') {
          $seenRuns = @{}
          $merged = [System.Collections.Generic.List[object]]::new()
          foreach ($src in $candidates) {
            foreach ($r in $src.Runs) {
              $k = Get-DemoIQCleanupRunKey $r
              if ($k) { if ($seenRuns.ContainsKey($k)) { continue }; $seenRuns[$k] = $true }
              $merged.Add($r)
            }
          }
          $sorted = @($merged | Sort-Object -Stable -Property @{ Expression = { Get-DemoIQCleanupRunKey $_ } })
          $paths = @($candidates | Where-Object { $_.Kind -eq 'local' } | ForEach-Object { $_.Path })
          $odUsed = [bool]($oneDrive -and $oneDrive.Runs.Count -gt 0)
          $info = [pscustomobject]@{
            Manifest         = (New-DemoIQCleanupManifestCopy -Manifest $candidates[0].Manifest -Runs $sorted)
            Source           = $(if ($candidates.Count -eq 1) { $candidates[0].Desc } else { "merged $($candidates.Count) manifests: " + (@($candidates | ForEach-Object { $_.Desc }) -join '; ') })
            PlanSource       = 'manifest'
            LocalPath        = $(if ($paths.Count) { $paths[0] } else { $null })
            LocalPaths       = $paths
            FromOneDrive     = $odUsed
            OneDriveManifest = $(if ($odUsed) { $oneDrive.Manifest } else { $null })
            Errors           = @()
          }
        } else {
          $chosen = $candidates[0]
          $lastKey = Get-DemoIQCleanupRunKey $chosen.Runs[-1]
          $paths = [System.Collections.Generic.List[string]]::new()
          if ($chosen.Kind -eq 'local') { $paths.Add($chosen.Path) }
          if ($lastKey) {
            foreach ($src in @(@($explicit) + @($locals) | Where-Object { $_ })) {
              if ($src.Path -eq $chosen.Path -or $paths.Contains($src.Path)) { continue }
              if (@($src.Runs | Where-Object { (Get-DemoIQCleanupRunKey $_) -eq $lastKey }).Count -gt 0) { $paths.Add($src.Path) }
            }
          }
          $odUsed = [bool]($oneDrive -and ($chosen.Kind -eq 'onedrive' -or
              ($lastKey -and @($oneDrive.Runs | Where-Object { (Get-DemoIQCleanupRunKey $_) -eq $lastKey }).Count -gt 0)))
          $info = [pscustomobject]@{
            Manifest         = $chosen.Manifest
            Source           = $chosen.Desc
            PlanSource       = 'manifest'
            LocalPath        = $(if ($paths.Count) { $paths[0] } else { $null })
            LocalPaths       = @($paths)
            FromOneDrive     = $odUsed
            OneDriveManifest = $(if ($odUsed) { $oneDrive.Manifest } else { $null })
            Errors           = @()
          }
        }
      }
    } catch {
      $problems.Add("manifest search: $($_.Exception.Message)")
      $info = $null
    }
  }
  if (-not $info) {
    $logRuns = @(Get-DemoIQCleanupRunsFromLogs -LogsFolder $LogsFolder)
    if ($logRuns.Count -gt 0) {
      $usedFolders = @(@($LogsFolder) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) })
      $info = [pscustomobject]@{
        Manifest         = [pscustomobject]@{ runs = $logRuns; tenantConfig = [pscustomobject]@{ tenantId = $null } }
        Source           = "logs ($($usedFolders -join '; '))"
        PlanSource       = 'logs'
        LocalPath        = $null
        LocalPaths       = @()
        FromOneDrive     = $false
        OneDriveManifest = $null
        Errors           = @()
      }
    }
  }
  foreach ($p in $problems) { Write-Host "[PURGE] Note: $p" -ForegroundColor DarkYellow }
  if (-not $info) {
    Write-Host '[PURGE] No manifest or run log lists a seeded run - nothing to clean up' -ForegroundColor DarkYellow
    return $null
  }
  $info.Errors = @($problems)
  $info | Add-Member -NotePropertyName LocalRunKeys -NotePropertyValue $localKeys -Force
  Write-Host "[PURGE] Plan source ($Scope): $($info.Source) - $(@(Get-DemoIQCleanupManifestRuns $info.Manifest).Count) run(s)" -ForegroundColor Cyan
  return $info
}

function New-DemoIQCleanupPlan {
  # What to delete for the targeted runs (LastRun = the manifest's last run; AllRuns = all):
  #   [pscustomobject]@{ Plan; ByRun; DemoUsers; TargetRuns; AllRuns; Counts }
  # Plan holds groups, meetings, files, emailSubjects, chats, groupNames, fileBaseNames.
  # DemoUsers (every sweep) = the users of all the manifest's runs, else -KnownUsers, always
  # plus the administrator (lower-cased). Makes no request.
  param(
    [Parameter(Mandatory)]$Manifest,
    [Parameter(Mandatory)][ValidateSet('LastRun', 'AllRuns')][string]$Scope,
    [string]$AdminUpn,
    [string[]]$KnownUsers
  )
  $allRuns = @(Get-DemoIQCleanupManifestRuns $Manifest)
  $targetRuns = @(if ($Scope -eq 'AllRuns') { $allRuns } elseif ($allRuns.Count -gt 0) { $allRuns[-1] })
  $plan = [ordered]@{ groups = @(); meetings = @(); files = @(); emailSubjects = @(); chats = @(); groupNames = @(); fileBaseNames = @() }
  $byRun = @()
  foreach ($run in $targetRuns) {
    $rc = [ordered]@{ groups = 0; meetings = 0; files = 0; emails = 0; chats = 0 }
    if ($run.team -and $run.team.groupId) {
      $plan.groups += [pscustomobject]@{ id = [string]$run.team.groupId; channels = @(@($run.team.channels) | Where-Object { $null -ne $_ }).Count }
      $rc.groups = 1
    }
    if ($run.team -and $run.team.displayName) { $plan.groupNames += ('' + $run.team.displayName) }
    foreach ($m in @($run.meetings)) {
      if ($m -and $m.eventId -and $m.organizer) {
        $plan.meetings += [pscustomobject]@{ subject = $m.subject; eventId = [string]$m.eventId; organizer = [string]$m.organizer }
        $rc.meetings++
      }
    }
    # Files: owner may be '*' (log-sourced: looked up in every demo user's drive).
    foreach ($f in @($run.files)) {
      if ($f -and $f.name -and $f.owner) {
        $plan.files += [pscustomobject]@{ type = $f.type; name = [string]$f.name; owner = [string]$f.owner }
        $rc.files++
        # The clean file name (no _YYYYMMDD_HHMM stamp, no extension) that file-share
        # notification emails quote.
        $base = (([string]$f.name) -replace '_\d{8}_\d{3,6}', '' -replace '\.(docx|pptx|xlsx)$', '')
        if ($base) { $plan.fileBaseNames += $base }
      }
    }
    foreach ($e in @($run.emails)) {
      if ($e -and $e.subject) { $plan.emailSubjects += (([string]$e.subject) -replace '^RE:\s*', '' -replace '^FW:\s*', ''); $rc.emails++ }
    }
    # A chat without a recorded id is still planned by topic, so it is deleted when found
    # and reported in chatsNotFound when not - never skipped silently.
    foreach ($c in @($run.chats)) {
      if ($c -and ($c.id -or $c.topic)) { $plan.chats += [pscustomobject]@{ id = [string]$c.id; topic = $c.topic }; $rc.chats++ }
    }
    $byRun += [pscustomobject]@{
      when = (Get-DemoIQCleanupRunLabel $run)
      groups = $rc.groups; meetings = $rc.meetings; files = $rc.files; emails = $rc.emails; chats = $rc.chats
    }
  }
  $plan.emailSubjects = @($plan.emailSubjects | Where-Object { $_ } | Select-Object -Unique)
  $plan.groupNames = @($plan.groupNames | Where-Object { $_ } | Select-Object -Unique)
  $plan.fileBaseNames = @($plan.fileBaseNames | Where-Object { $_ } | Select-Object -Unique)

  $names = @()
  foreach ($r in $allRuns) {
    foreach ($u in @($r.users)) {
      if ($u -is [string]) { $names += $u } elseif ($u -and $u.upn) { $names += [string]$u.upn }
    }
  }
  $demoUsers = @(Get-DemoIQCleanupUniqueUsers $names)
  if ($demoUsers.Count -eq 0) { $demoUsers = @(Get-DemoIQCleanupUniqueUsers @($KnownUsers)) }
  # The administrator's mailbox, drive and chat list carry seeding residue too (meeting
  # invites, notifications, shared files): the administrator is in every sweep.
  if ($AdminUpn -and $AdminUpn.Trim() -and ($demoUsers -notcontains $AdminUpn.Trim())) {
    $demoUsers = @($demoUsers + $AdminUpn.Trim().ToLowerInvariant())
  }
  $counts = [ordered]@{
    groups = @($plan.groups).Count; meetings = @($plan.meetings).Count; files = @($plan.files).Count
    emailSubjects = @($plan.emailSubjects).Count; chats = @($plan.chats).Count
  }
  return [pscustomobject]@{ Plan = $plan; ByRun = @($byRun); DemoUsers = @($demoUsers); TargetRuns = @($targetRuns); AllRuns = @($allRuns); Counts = $counts }
}

# ---------------------------------------------------------------- tokens and Graph

function Get-DemoIQCleanupUserToken {
  # A demo user's delegated Microsoft Graph token: a public-client password grant (no client
  # secret or assertion) for the DemoIQ app. Works only while the user's MFA/Conditional
  # Access is relaxed. Returns @{ ok; token; permanent; error }.
  # Retried up to -MaxAttempts: network failures and HTTP 429/5xx (after Retry-After), and
  # the codes an MFA/CA change shows until it has propagated (AADSTS50076, AADSTS50079,
  # AADSTS53003; 30 s x attempt). Any other AADSTS error is final at once (permanent).
  # The request body is never logged.
  param(
    [Parameter(Mandatory)][AllowEmptyString()][string]$TenantId,
    [Parameter(Mandatory)][AllowEmptyString()][string]$ClientId,
    [Parameter(Mandatory)][AllowEmptyString()][string]$Upn,
    [string]$Password,
    [int]$MaxAttempts = 4
  )
  if (-not $TenantId -or -not $ClientId -or -not $Upn) { return @{ ok = $false; token = $null; permanent = $true; error = 'no tenant, app or user' } }
  if ([string]::IsNullOrEmpty($Password)) { return @{ ok = $false; token = $null; permanent = $true; error = 'no password' } }
  if ($MaxAttempts -lt 1) { $MaxAttempts = 1 }
  $uri = "https://login.microsoftonline.com/$TenantId/oauth2/v2.0/token"
  $lastError = $null
  for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    $wait = 5 * $attempt
    $why = $null
    try {
      $resp = Invoke-RestMethod -Method POST -Uri $uri -ContentType 'application/x-www-form-urlencoded' -TimeoutSec 60 -ErrorAction Stop -Body @{
        grant_type = 'password'; client_id = $ClientId; username = $Upn; password = $Password
        scope = 'https://graph.microsoft.com/.default offline_access'
      }
      if ($resp -and $resp.access_token) { return @{ ok = $true; token = [string]$resp.access_token; permanent = $false; error = $null } }
      $lastError = 'the token endpoint returned no access token'
      $why = 'no access token in the response'
    } catch {
      $status = Get-DemoIQCleanupStatusCode $_
      $aad = Get-DemoIQCleanupAadCode $_
      $lastError = Get-DemoIQCleanupErrorText $_
      if ($status -eq 429 -or ($status -ge 500 -and $status -le 599)) {
        $wait = Get-DemoIQCleanupRetryAfter -ErrorRecord $_ -Default (5 * $attempt)
        $why = "HTTP $status"
      } elseif ($aad -in @('50076', '50079', '53003')) {
        $wait = 30 * $attempt
        $why = "AADSTS$aad (the MFA/Conditional Access change has not reached sign-in yet)"
      } elseif ($aad -or $status) {
        Write-Host "[PURGE] Sign-in for ${Upn} failed: $lastError" -ForegroundColor DarkYellow
        return @{ ok = $false; token = $null; permanent = $true; error = $lastError }
      } else {
        $why = 'network failure'
      }
    }
    if ($attempt -lt $MaxAttempts) {
      Write-Host "[PURGE] Sign-in for ${Upn}: $why - retrying in $wait s (attempt $attempt of $MaxAttempts)" -ForegroundColor DarkGray
      Start-Sleep -Seconds $wait
    }
  }
  Write-Host "[PURGE] Sign-in for ${Upn} failed after $MaxAttempts attempt(s): $lastError" -ForegroundColor DarkYellow
  return @{ ok = $false; token = $null; permanent = $false; error = $lastError }
}

function Invoke-DemoIQCleanupGraph {
  # One Microsoft Graph request with a delegated token. HTTP 429 is re-issued up to twice
  # after Retry-After (5 s when absent); any other failure is re-thrown for the caller.
  param(
    [Parameter(Mandatory)][ValidateSet('GET', 'DELETE', 'PUT')][string]$Method,
    [Parameter(Mandatory)][string]$Uri,
    [Parameter(Mandatory)][string]$Token,
    [string]$Body,
    [string]$ContentType
  )
  $request = @{ Method = $Method; Uri = $Uri; Headers = @{ Authorization = 'Bearer ' + $Token }; TimeoutSec = 60; ErrorAction = 'Stop' }
  if ($PSBoundParameters.ContainsKey('Body')) { $request.Body = $Body }
  if ($ContentType) { $request.ContentType = $ContentType }
  for ($attempt = 0; ; $attempt++) {
    try { return (Invoke-RestMethod @request) }
    catch {
      if ((Get-DemoIQCleanupStatusCode $_) -ne 429 -or $attempt -ge 2) { throw }
      $wait = Get-DemoIQCleanupRetryAfter -ErrorRecord $_ -Default 5
      Write-Host "[PURGE] Graph throttled (HTTP 429) - retrying in $wait s" -ForegroundColor DarkGray
      Start-Sleep -Seconds $wait
    }
  }
}

# ---------------------------------------------------------------- deletion

function Invoke-DemoIQCleanup {
  # Delete the planned content (PlanInfo from New-DemoIQCleanupPlan). Returns ordered
  # results: the per-kind counters, usersWithoutToken, approach and errors. Never throws.
  # -GetToken is called as  & $GetToken <upn>  and returns that user's access token or $null;
  # the caller maps the administrator's UPN to the administrator's token. Tokens are cached
  # per user. -GetToken runs inside this function: reach seeder state through global-scope
  # variables or its own parameters, not through bare names this function might also use.
  # Routing: the team and chat deletes use the administrator's token; each user's meetings,
  # meeting residue, mail, drive and chat list are reached through /me with that user's own
  # token (a meeting is deleted by its organizer). 404 counts as already deleted.
  param(
    [Parameter(Mandatory)]$PlanInfo,
    [Parameter(Mandatory)][AllowEmptyString()][string]$AdminUpn,
    [Parameter(Mandatory)][scriptblock]$GetToken
  )
  $graphRoot = 'https://graph.microsoft.com/v1.0'
  $result = [ordered]@{
    groupsDeleted = 0; groupsFailed = 0
    meetingsDeleted = 0; meetingsFailed = 0
    meetingResidueRemoved = 0; meetingEmailsRemoved = 0
    filesDeleted = 0; filesFailed = 0
    emailsDeleted = 0; emailsFailed = 0
    notificationEmailsDeleted = 0
    chatsDeleted = 0; chatsFailed = 0; meetingChatsDeleted = 0; chatsNotFound = 0
    usersWithoutToken = @()
    approach = 'delegated (administrator sign-in + demo-user tokens while MFA/CA is relaxed)'
    errors = @()
  }
  $state = @{
    GetToken = $GetToken; Tokens = @{}; Hinted = @{}
    Tokenless = [System.Collections.Generic.List[string]]::new()
    Problems = [System.Collections.Generic.List[string]]::new()
  }
  try {
    $plan = $PlanInfo.Plan
    $sweepUsers = @(Get-DemoIQCleanupUniqueUsers @($PlanInfo.DemoUsers))
    Write-Host ("[PURGE] Deleting: {0} team(s), {1} meeting(s), {2} file(s), {3} email subject(s), {4} chat(s); sweeping {5} mailbox(es)" -f
      @($plan.groups).Count, @($plan.meetings).Count, @($plan.files).Count, @($plan.emailSubjects).Count, @($plan.chats).Count, $sweepUsers.Count) -ForegroundColor Cyan
    $adminTok = Get-DemoIQCleanupTokenFor -State $state -Upn $AdminUpn

    # Teams team(s): deleting the group removes the team, its channels and their messages.
    $skippedTeams = 0
    foreach ($g in @($plan.groups)) {
      if (-not $g -or -not $g.id) { continue }
      if (-not $adminTok) { $result.groupsFailed++; $skippedTeams++; continue }
      try {
        $null = Invoke-DemoIQCleanupGraph -Method DELETE -Uri "$graphRoot/groups/$($g.id)" -Token $adminTok
        $result.groupsDeleted++
        Write-Host "[PURGE] Team $($g.id): deleted" -ForegroundColor DarkGreen
      } catch {
        if ((Get-DemoIQCleanupStatusCode $_) -eq 404) { $result.groupsDeleted++; Write-Host "[PURGE] Team $($g.id): already gone" -ForegroundColor DarkGray }
        else {
          $result.groupsFailed++
          $hint = Get-DemoIQCleanupErrorHint -ErrorRecord $_ -Administrator
          $state.Problems.Add("group $($g.id): $(Get-DemoIQCleanupErrorText $_)$(if ($hint) { " - $hint" })")
          Write-Host "[PURGE] Team $($g.id): NOT deleted" -ForegroundColor DarkYellow
        }
      }
    }

    # Meetings: the organizer deletes the event from their own calendar. Outlook then sends
    # cancellations, which leave canceled copies and notices in the attendees' mailboxes -
    # swept below by subject.
    foreach ($m in @($plan.meetings)) {
      if (-not $m) { continue }
      $orgTok = Get-DemoIQCleanupTokenFor -State $state -Upn $m.organizer
      if (-not $orgTok) { $result.meetingsFailed++; continue }
      try {
        $null = Invoke-DemoIQCleanupGraph -Method DELETE -Uri "$graphRoot/me/events/$($m.eventId)" -Token $orgTok
        $result.meetingsDeleted++
      } catch {
        if ((Get-DemoIQCleanupStatusCode $_) -eq 404) { $result.meetingsDeleted++ }
        else {
          $result.meetingsFailed++
          $hint = Get-DemoIQCleanupErrorHint -ErrorRecord $_
          $state.Problems.Add("meeting '$($m.subject)' ($($m.organizer)): $(Get-DemoIQCleanupErrorText $_)$(if ($hint) { " - $hint" })")
        }
      }
    }
    if (@($plan.meetings).Count -gt 0) {
      $meetingKeys = @{}
      foreach ($m in @($plan.meetings)) { if ($m -and $m.subject) { $meetingKeys[(Get-DemoIQCleanupSubjectKey $m.subject)] = $true } }
      $residueUsers = @(Get-DemoIQCleanupUniqueUsers (@(@($plan.meetings) | ForEach-Object { [string]$_.organizer }) + $sweepUsers))
      foreach ($u in $residueUsers) {
        $tok = Get-DemoIQCleanupTokenFor -State $state -Upn $u
        if (-not $tok) { continue }
        $uri = "$graphRoot/me/events?`$select=id,subject&`$top=100"; $pages = 0
        while ($uri -and $pages -lt 50) {
          $pages++
          try { $page = Invoke-DemoIQCleanupGraph -Method GET -Uri $uri -Token $tok }
          catch { Add-DemoIQCleanupHint -State $state -Area "calendar ($u)" -ErrorRecord $_; break }
          foreach ($ev in @($page.value)) {
            if ($ev -and $ev.subject -and $meetingKeys.ContainsKey((Get-DemoIQCleanupSubjectKey $ev.subject))) {
              try { $null = Invoke-DemoIQCleanupGraph -Method DELETE -Uri "$graphRoot/me/events/$($ev.id)" -Token $tok; $result.meetingResidueRemoved++ } catch {}
            }
          }
          $uri = $page.'@odata.nextLink'
        }
        $uri = "$graphRoot/me/messages?`$select=id,subject&`$top=100"; $pages = 0
        while ($uri -and $pages -lt 10) {
          $pages++
          try { $page = Invoke-DemoIQCleanupGraph -Method GET -Uri $uri -Token $tok }
          catch { Add-DemoIQCleanupHint -State $state -Area "mailbox ($u)" -ErrorRecord $_; break }
          foreach ($msg in @($page.value)) {
            if ($msg -and $msg.subject -and $meetingKeys.ContainsKey((Get-DemoIQCleanupSubjectKey $msg.subject))) {
              try { $null = Invoke-DemoIQCleanupGraph -Method DELETE -Uri "$graphRoot/me/messages/$($msg.id)" -Token $tok; $result.meetingEmailsRemoved++ } catch {}
            }
          }
          $uri = $page.'@odata.nextLink'
        }
      }
    }
    Write-Host "[PURGE] Meetings: $($result.meetingsDeleted) deleted, $($result.meetingsFailed) failed; residue: $($result.meetingResidueRemoved) calendar copies, $($result.meetingEmailsRemoved) emails" -ForegroundColor DarkGreen

    # OneDrive files: each owner's drive is indexed with the owner's token (root and
    # subfolders, every page), then the matching item is deleted. Owner '*' (log-sourced)
    # means every demo user's drive is searched.
    $owners = @(Get-DemoIQCleanupUniqueUsers @(@($plan.files) | Where-Object { $_ -and $_.owner -and $_.owner -ne '*' } | ForEach-Object { [string]$_.owner }))
    $anyOwner = @(@($plan.files) | Where-Object { $_ -and $_.owner -eq '*' }).Count -gt 0
    if ($anyOwner) { $owners = @(Get-DemoIQCleanupUniqueUsers ($owners + $sweepUsers)) }
    $driveIndex = @{}
    foreach ($ow in $owners) {
      $tok = Get-DemoIQCleanupTokenFor -State $state -Upn $ow
      if (-not $tok) { continue }
      $idx = @{}
      $queue = [System.Collections.Generic.Queue[string]]::new()
      $queue.Enqueue("$graphRoot/me/drive/root/children?`$top=200")
      $requests = 0
      while ($queue.Count -gt 0) {
        if ($requests -ge 2000) { $state.Problems.Add("drive ($ow): stopped indexing after $requests requests - some files may remain"); break }
        $next = $queue.Dequeue(); $requests++
        try { $resp = Invoke-DemoIQCleanupGraph -Method GET -Uri $next -Token $tok }
        catch { Add-DemoIQCleanupHint -State $state -Area "drive ($ow)" -ErrorRecord $_; continue }
        foreach ($it in @($resp.value)) {
          if (-not $it) { continue }
          if ($it.folder) { $queue.Enqueue("$graphRoot/me/drive/items/$($it.id)/children?`$top=200") }
          if ($it.name -and -not $idx.ContainsKey($it.name)) { $idx[$it.name] = $it.id }
        }
        if ($resp.'@odata.nextLink') { $queue.Enqueue($resp.'@odata.nextLink') }
      }
      $driveIndex[$ow.ToLowerInvariant()] = $idx
    }
    foreach ($f in @($plan.files)) {
      if (-not $f -or -not $f.name) { continue }
      try {
        if ($f.owner -eq '*') {
          $done = $false; $unswept = $false
          foreach ($ow in $owners) {
            $idx = $driveIndex[$ow.ToLowerInvariant()]
            if ($null -eq $idx) { $unswept = $true; continue }
            if ($idx.ContainsKey($f.name)) {
              $tok = Get-DemoIQCleanupTokenFor -State $state -Upn $ow
              $null = Invoke-DemoIQCleanupGraph -Method DELETE -Uri "$graphRoot/me/drive/items/$($idx[$f.name])" -Token $tok
              $done = $true; break
            }
          }
          # Not in any swept drive = already gone, unless a drive could not be swept.
          if ($done -or -not $unswept) { $result.filesDeleted++ } else { $result.filesFailed++ }
        } else {
          $tok = Get-DemoIQCleanupTokenFor -State $state -Upn $f.owner
          if (-not $tok) { $result.filesFailed++; continue }
          $idx = $driveIndex[([string]$f.owner).Trim().ToLowerInvariant()]
          if ($idx -and $idx.ContainsKey($f.name)) {
            $null = Invoke-DemoIQCleanupGraph -Method DELETE -Uri "$graphRoot/me/drive/items/$($idx[$f.name])" -Token $tok
          }
          $result.filesDeleted++
        }
      } catch {
        if ((Get-DemoIQCleanupStatusCode $_) -eq 404) { $result.filesDeleted++ }
        else {
          $result.filesFailed++
          $hint = Get-DemoIQCleanupErrorHint -ErrorRecord $_
          $state.Problems.Add("file '$($f.name)': $(Get-DemoIQCleanupErrorText $_)$(if ($hint) { " - $hint" })")
        }
      }
    }
    Write-Host "[PURGE] Files: $($result.filesDeleted) deleted, $($result.filesFailed) failed" -ForegroundColor DarkGreen

    # Emails: in every swept mailbox, delete the messages whose subject is a seeded subject
    # (originals and replies, in the sender's and every recipient's mailbox).
    $subjects = @($plan.emailSubjects | Where-Object { $_ })
    if ($subjects.Count -gt 0) {
      foreach ($u in $sweepUsers) {
        $tok = Get-DemoIQCleanupTokenFor -State $state -Upn $u
        if (-not $tok) { continue }
        foreach ($subj in $subjects) {
          try {
            $search = $graphRoot + '/me/messages?$search="subject:' + [uri]::EscapeDataString($subj) + '"&$top=25&$select=id,subject'
            $hits = @((Invoke-DemoIQCleanupGraph -Method GET -Uri $search -Token $tok).value)
            foreach ($msg in $hits) {
              if (-not $msg) { continue }
              $s = ([string]$msg.subject) -replace '^RE:\s*', '' -replace '^FW:\s*', ''
              if ($s -and ($s -eq $subj)) {
                try {
                  $null = Invoke-DemoIQCleanupGraph -Method DELETE -Uri "$graphRoot/me/messages/$($msg.id)" -Token $tok
                  $result.emailsDeleted++
                } catch {
                  if ((Get-DemoIQCleanupStatusCode $_) -eq 404) { $result.emailsDeleted++ }
                  else { $result.emailsFailed++; Add-DemoIQCleanupHint -State $state -Area "email delete ($u)" -ErrorRecord $_ }
                }
              }
            }
          } catch { Add-DemoIQCleanupHint -State $state -Area "email search ($u)" -ErrorRecord $_ }
        }
      }
    }

    # Notification emails seeding caused: group join/welcome mail (the team's name) and
    # file-share notices (a seeded file's clean name).
    $groupNames = @($plan.groupNames | Where-Object { $_ })
    $baseNames = @($plan.fileBaseNames | Where-Object { $_ })
    if ($groupNames.Count -gt 0 -or $baseNames.Count -gt 0) {
      foreach ($u in $sweepUsers) {
        $tok = Get-DemoIQCleanupTokenFor -State $state -Upn $u
        if (-not $tok) { continue }
        $uri = "$graphRoot/me/messages?`$select=id,subject&`$top=200"; $pages = 0
        while ($uri -and $pages -lt 15) {
          $pages++
          try { $page = Invoke-DemoIQCleanupGraph -Method GET -Uri $uri -Token $tok }
          catch { Add-DemoIQCleanupHint -State $state -Area "mailbox ($u)" -ErrorRecord $_; break }
          foreach ($msg in @($page.value)) {
            $subj = if ($msg) { '' + $msg.subject } else { '' }
            if (-not $subj) { continue }
            $hit = $false
            foreach ($gn in $groupNames) { if ($subj.IndexOf($gn, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { $hit = $true; break } }
            if (-not $hit -and $subj -match '(?i)shared|invited you to edit|sharing') {
              foreach ($b in $baseNames) { if ($subj.IndexOf($b, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { $hit = $true; break } }
            }
            if ($hit) {
              try { $null = Invoke-DemoIQCleanupGraph -Method DELETE -Uri "$graphRoot/me/messages/$($msg.id)" -Token $tok; $result.notificationEmailsDeleted++ } catch {}
            }
          }
          $uri = $page.'@odata.nextLink'
        }
      }
    }
    Write-Host "[PURGE] Emails: $($result.emailsDeleted) deleted, $($result.emailsFailed) failed; notifications: $($result.notificationEmailsDeleted)" -ForegroundColor DarkGreen

    # Group and meeting chats: found in each user's own chat list (with that user's token)
    # by seeded id, chat topic or meeting subject; deleted by the administrator
    # (DELETE /chats/{id}: soft delete, restorable for 7 days, one per second per tenant).
    $meetingTopics = @{}
    foreach ($m in @($plan.meetings)) { if ($m -and $m.subject) { $meetingTopics[('' + $m.subject).ToLower().Trim()] = $true } }
    $plannedTopics = @{}
    $plannedIds = @{}
    foreach ($c in @($plan.chats)) {
      if (-not $c) { continue }
      if ($c.topic) { $plannedTopics[('' + $c.topic).ToLower().Trim()] = $true }
      if ($c.id -and $c.id -match '^19:') { $plannedIds[[string]$c.id] = $true }
    }
    $toDelete = [ordered]@{}
    foreach ($id in $plannedIds.Keys) { $toDelete[$id] = 'group(id)' }
    $matchedTopics = @{}
    if ($plannedTopics.Count -gt 0 -or $meetingTopics.Count -gt 0) {
      foreach ($u in $sweepUsers) {
        $tok = Get-DemoIQCleanupTokenFor -State $state -Upn $u
        if (-not $tok) { continue }
        try {
          $uri = "$graphRoot/me/chats?`$top=50"; $pages = 0
          while ($uri -and $pages -lt 20) {
            $pages++
            $page = Invoke-DemoIQCleanupGraph -Method GET -Uri $uri -Token $tok
            foreach ($ch in @($page.value)) {
              if (-not $ch) { continue }
              $t = ('' + $ch.topic).ToLower().Trim()
              if (-not $t) { continue }
              if ($ch.chatType -eq 'group' -and $plannedTopics.ContainsKey($t)) { $toDelete[[string]$ch.id] = 'group'; $matchedTopics[$t] = $true }
              elseif ($ch.chatType -eq 'meeting' -and $meetingTopics.ContainsKey($t)) { $toDelete[[string]$ch.id] = 'meeting' }
            }
            $uri = $page.'@odata.nextLink'
          }
        } catch { Add-DemoIQCleanupHint -State $state -Area "chat list ($u)" -ErrorRecord $_ }
      }
    }
    $skippedChats = 0
    $first = $true
    foreach ($cid in @($toDelete.Keys)) {
      $kind = $toDelete[$cid]
      if (-not $adminTok) { $result.chatsFailed++; $skippedChats++; continue }
      if (-not $first) { Start-Sleep -Seconds 1 }  # one chat delete per second per tenant
      $first = $false
      try {
        $null = Invoke-DemoIQCleanupGraph -Method DELETE -Uri "$graphRoot/chats/$cid" -Token $adminTok
        if ($kind -eq 'meeting') { $result.meetingChatsDeleted++ } else { $result.chatsDeleted++ }
      } catch {
        if ((Get-DemoIQCleanupStatusCode $_) -eq 404) { if ($kind -eq 'meeting') { $result.meetingChatsDeleted++ } else { $result.chatsDeleted++ } }
        else {
          $result.chatsFailed++
          $hint = Get-DemoIQCleanupErrorHint -ErrorRecord $_ -Administrator
          $state.Problems.Add("chat $cid ($kind): $(Get-DemoIQCleanupErrorText $_)$(if ($hint) { " - $hint" })")
        }
      }
    }
    if ($skippedTeams -gt 0 -or $skippedChats -gt 0) {
      $state.Problems.Add("no administrator token: $skippedTeams team(s) and $skippedChats chat(s) were not deleted")
    }
    # A planned group chat neither addressed by id nor matched by topic is still in Teams:
    # report it loudly, never skip it silently.
    foreach ($c in @($plan.chats)) {
      if (-not $c) { continue }
      $t = ('' + $c.topic).ToLower().Trim()
      $addressedById = ($c.id -and $plannedIds.ContainsKey([string]$c.id))
      if (-not $addressedById -and (-not $t -or -not $matchedTopics.ContainsKey($t))) {
        $result.chatsNotFound++
        $label = if ($c.topic) { "'$($c.topic)'" } elseif ($c.id) { $c.id } else { '(no topic recorded)' }
        $state.Problems.Add("group chat ${label}: not found in any swept user's chat list - NOT deleted. Verify in Teams; it may use a different topic, or its members are outside the demo users + admin.")
        Write-Host "[PURGE] GROUP CHAT NOT FOUND: $label - NOT deleted; verify it in Teams" -ForegroundColor Yellow
      }
    }
    Write-Host "[PURGE] Chats: $($result.chatsDeleted) group, $($result.meetingChatsDeleted) meeting deleted; $($result.chatsFailed) failed; $($result.chatsNotFound) not found" -ForegroundColor DarkGreen
  } catch {
    $state.Problems.Add("cleanup stopped early: $(Get-DemoIQCleanupErrorText $_)")
    Write-Host "[PURGE] Cleanup stopped early: $(Get-DemoIQCleanupErrorText $_)" -ForegroundColor Red
  }
  $result.usersWithoutToken = @($state.Tokenless)
  $result.errors = @($state.Problems)
  return $result
}

# ---------------------------------------------------------------- after deletion

function Update-DemoIQCleanupManifest {
  # Prune the deleted runs (PlanInfo.TargetRuns, matched by timestamp) from every manifest
  # copy Read-DemoIQCleanupManifest found. Only for PlanSource 'manifest' (run logs are an
  # audit trail, not a deletion record). Each local file in ManifestInfo.LocalPaths is
  # re-read and rewritten with its remaining runs (JSON depth 12, UTF-8); each existing
  # -LocalCopies file is a mirror and gets the remaining merged manifest; when FromOneDrive,
  # the administrator's OneDrive copy minus the targeted runs is PUT with -AdminToken (for
  # AllRuns that leaves runs = @() and its other fields). Returns @{ pruned; errors }:
  # pruned when at least one copy was rewritten. Never throws.
  param(
    [Parameter(Mandatory)]$ManifestInfo,
    [Parameter(Mandatory)]$PlanInfo,
    [Parameter(Mandatory)][ValidateSet('LastRun', 'AllRuns')][string]$Scope,
    [scriptblock]$AdminToken,
    [string[]]$LocalCopies
  )
  $problems = [System.Collections.Generic.List[string]]::new()
  $written = 0
  try {
    if ($ManifestInfo -and $ManifestInfo.PlanSource -eq 'manifest') {
      $targetKeys = @{}
      $keyless = 0
      foreach ($r in @($PlanInfo.TargetRuns | Where-Object { $null -ne $_ })) {
        $k = Get-DemoIQCleanupRunKey $r
        if ($k) { $targetKeys[$k] = $true } else { $keyless++ }
      }
      if ($keyless -gt 0) { $problems.Add("$keyless targeted run(s) carry no timestamp and stay in the manifest") }
      if ($targetKeys.Count -gt 0) {
        $done = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
        foreach ($p in @(@($ManifestInfo.LocalPaths) + @($ManifestInfo.LocalPath))) {
          if (-not $p -or -not $done.Add([string]$p)) { continue }
          if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { $problems.Add("manifest ${p}: not found - not pruned"); continue }
          try {
            $m = Read-DemoIQCleanupManifestFile -Path $p
            $runs = @(Get-DemoIQCleanupManifestRuns $m)
            $keep = @($runs | Where-Object { -not (Test-DemoIQCleanupRunTargeted -Run $_ -Keys $targetKeys) })
            $json = New-DemoIQCleanupManifestCopy -Manifest $m -Runs $keep | ConvertTo-Json -Depth 12
            Set-Content -LiteralPath $p -Value $json -Encoding utf8
            $written++
            Write-Host "[PURGE] Manifest pruned: $p ($($runs.Count - $keep.Count) run(s) removed, $($keep.Count) left)" -ForegroundColor DarkGreen
          } catch { $problems.Add("manifest prune (${p}): $($_.Exception.Message)") }
        }
        $mergedKeep = @(@(Get-DemoIQCleanupManifestRuns $ManifestInfo.Manifest) | Where-Object { -not (Test-DemoIQCleanupRunTargeted -Run $_ -Keys $targetKeys) })
        $mirrorJson = $null
        foreach ($p in @($LocalCopies)) {
          if (-not $p -or -not (Test-Path -LiteralPath $p -PathType Leaf) -or -not $done.Add([string]$p)) { continue }
          try {
            if (-not $mirrorJson) { $mirrorJson = New-DemoIQCleanupManifestCopy -Manifest $ManifestInfo.Manifest -Runs $mergedKeep | ConvertTo-Json -Depth 12 }
            Set-Content -LiteralPath $p -Value $mirrorJson -Encoding utf8
            $written++
            Write-Host "[PURGE] Manifest copy rewritten: $p ($($mergedKeep.Count) run(s) left)" -ForegroundColor DarkGreen
          } catch { $problems.Add("manifest copy (${p}): $($_.Exception.Message)") }
        }
        if ($ManifestInfo.FromOneDrive) {
          if (-not $AdminToken) { $problems.Add('admin OneDrive manifest not pruned: no administrator sign-in was given') }
          else {
            try {
              $od = if ($ManifestInfo.OneDriveManifest) { $ManifestInfo.OneDriveManifest } else { $ManifestInfo.Manifest }
              $odKeep = @(@(Get-DemoIQCleanupManifestRuns $od) | Where-Object { -not (Test-DemoIQCleanupRunTargeted -Run $_ -Keys $targetKeys) })
              $json = New-DemoIQCleanupManifestCopy -Manifest $od -Runs $odKeep | ConvertTo-Json -Depth 12
              $tok = @(& $AdminToken) | Where-Object { $_ -is [string] -and $_ } | Select-Object -Last 1
              if (-not $tok) { throw 'no administrator token' }
              $null = Invoke-DemoIQCleanupGraph -Method PUT -Uri (Get-DemoIQCleanupOneDriveManifestUri) -Token $tok -Body $json -ContentType 'application/json'
              $written++
              Write-Host "[PURGE] Admin OneDrive manifest pruned ($($odKeep.Count) run(s) left)" -ForegroundColor DarkGreen
            } catch { $problems.Add("admin OneDrive manifest not pruned: $(Get-DemoIQCleanupErrorText $_)") }
          }
        }
      }
    }
  } catch { $problems.Add("manifest prune: $($_.Exception.Message)") }
  return [ordered]@{ pruned = ($written -gt 0); errors = @($problems) }
}

function Clear-DemoIQCleanupLocalState {
  # Local follow-up for the seed run folders under <Workspace>/runs whose OWN run was deleted
  # (PlanInfo.TargetRuns, matched by run identity: the timestamp, or the log name of a
  # log-sourced run):
  #   * delete DemoIQ-Output/seed_ledger.json - the seeder's resume state would otherwise
  #     claim the purged content is still seeded;
  #   * with -ArchiveLocalDeliverables, zip DemoIQ-Output to <Workspace>/archive FIRST, then
  #     remove it (run.json and logs stay, so run history keeps working).
  # A folder's own runs are the runs its manifest lists that are not older than the folder (a
  # Full-mode seed copies earlier runs in as history) - as -ManifestInfo recorded them before
  # Update-DemoIQCleanupManifest pruned the manifests, or as the manifest lists them now - and
  # the runs its own logs record. A folder holding no targeted run (created but never
  # launched, failed before writing its manifest, any cleanup run folder) is never touched. A
  # targeted run that no local folder holds (known only from the administrator's OneDrive
  # copy, for example) clears nothing and is reported in notFoundLocally.
  # Returns @{ ledgersCleared; localArchives; notFoundLocally; errors }. Never throws.
  param(
    [Parameter(Mandatory)][string]$Workspace,
    [Parameter(Mandatory)]$PlanInfo,
    $ManifestInfo,
    [switch]$ArchiveLocalDeliverables
  )
  $cleared = [System.Collections.Generic.List[string]]::new()
  $archives = [System.Collections.Generic.List[object]]::new()
  $notFound = [System.Collections.Generic.List[string]]::new()
  $problems = [System.Collections.Generic.List[string]]::new()
  try {
    $targets = [ordered]@{}
    foreach ($r in @($PlanInfo.TargetRuns | Where-Object { $null -ne $_ })) {
      $k = Get-DemoIQCleanupRunKey $r
      if ($k) { if (-not $targets.Contains($k)) { $targets[$k] = Get-DemoIQCleanupRunLabel $r } }
      else { $notFound.Add('(a run without a timestamp)') }
    }
    $recorded = @{}
    $runKeys = if ($ManifestInfo) { $ManifestInfo.LocalRunKeys } else { $null }
    if ($runKeys -is [System.Collections.IDictionary]) {
      foreach ($p in @($runKeys.Keys)) { try { $recorded[[System.IO.Path]::GetFullPath([string]$p)] = @($runKeys[$p]) } catch {} }
    } elseif ($runKeys) {
      foreach ($p in $runKeys.PSObject.Properties) { try { $recorded[[System.IO.Path]::GetFullPath($p.Name)] = @($p.Value) } catch {} }
    }
    $folders = [System.Collections.Generic.List[object]]::new()
    $held = @{}
    if ($targets.Count -gt 0) {
      foreach ($rf in @(Get-DemoIQCleanupSeedRunFolders -Workspace $Workspace)) {
        $hits = @(@(Get-DemoIQCleanupOwnRunKeys -Folder $rf.FullName -Recorded $recorded) | Where-Object { $targets.Contains($_) })
        if ($hits.Count -eq 0) { continue }
        $folders.Add($rf)
        foreach ($h in $hits) { $held[$h] = $true }
      }
    }
    foreach ($k in @($targets.Keys)) { if (-not $held.ContainsKey($k)) { $notFound.Add([string]$targets[$k]) } }
    foreach ($rf in $folders) {
      $ledger = Join-Path (Join-Path $rf.FullName 'DemoIQ-Output') 'seed_ledger.json'
      if (Test-Path -LiteralPath $ledger -PathType Leaf) {
        try { Remove-Item -LiteralPath $ledger -Force; $cleared.Add($rf.Name) }
        catch { $problems.Add("ledger clear ($($rf.Name)): $($_.Exception.Message)") }
      }
    }
    if ($ArchiveLocalDeliverables) {
      $archiveRoot = Join-Path $Workspace 'archive'
      foreach ($rf in $folders) {
        $outDir = Join-Path $rf.FullName 'DemoIQ-Output'
        if (-not (Test-Path -LiteralPath $outDir -PathType Container)) { continue }
        $items = @(Get-ChildItem -LiteralPath $outDir -Force -ErrorAction SilentlyContinue)
        if ($items.Count -eq 0) { continue }
        try {
          New-Item -ItemType Directory -Force -Path $archiveRoot | Out-Null
          $zip = Join-Path $archiveRoot ($rf.Name + '__DemoIQ-Output.zip')
          Compress-Archive -LiteralPath @($items | ForEach-Object { $_.FullName }) -DestinationPath $zip -Force
          Remove-Item -LiteralPath $outDir -Recurse -Force
          $archives.Add([ordered]@{ run = $rf.Name; backup = $zip })
        } catch { $problems.Add("local archive ($($rf.Name)): $($_.Exception.Message)") }
      }
    }
  } catch { $problems.Add("local state: $($_.Exception.Message)") }
  if ($cleared.Count -gt 0) { Write-Host "[PURGE] Seed ledger cleared: $($cleared -join ', ')" -ForegroundColor DarkGreen }
  foreach ($a in $archives) { Write-Host "[PURGE] Local deliverables of $($a.run) archived to $($a.backup), then removed" -ForegroundColor DarkGreen }
  foreach ($n in $notFound) { Write-Host "[PURGE] No seed run folder on this machine holds run $n - nothing cleared locally for it" -ForegroundColor DarkYellow }
  return [ordered]@{ ledgersCleared = @($cleared); localArchives = @($archives); notFoundLocally = @($notFound); errors = @($problems) }
}

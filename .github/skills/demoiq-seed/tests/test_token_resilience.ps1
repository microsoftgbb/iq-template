# test_token_resilience.ps1 - tests for the GENERATED token-retry,
# seed-ledger, and targeted-replay resilience block (the fix for
# "DemoIQ leaves Teams content incomplete after one user's token request times out").
#
# Like test_splitter.ps1, this tests the REAL emitted PowerShell, not a lookalike:
# it generates a seeder from the fake fixture, extracts the self-contained
# `# --- BEGIN/END DEMOIQ RESILIENCE ---` block, Invoke-Expression's it, and drives
# the functions with a mocked token endpoint (Invoke-RestMethod shadowed) so no
# tenant is ever contacted. Covers the issue's acceptance tests:
#   * transient timeout on one user's token -> bounded retry -> success, full counts
#   * permanent failure -> fail fast, other content preserved, partial report
#   * transient vs permanent classification (network / 429 / 5xx / AADSTS)
#   * ledger idempotency + restart-safety (already-seeded items are skipped)
#   * targeted replay only re-sends the failed user's chat/channel/meeting-chat items
#   * the GitHub Copilot adaptation's seeder carries NO usage telemetry (platform_patch.js)
# plus the follow-up fix batch:
#   * completion computed AFTER the retry menu (post-menu recompute), full
#     "PARTIAL: N requested item(s) could not be seeded" banner phrase pinned
#   * flat-format chat replay resolves item N (not item 0) and strips the HH:MM prefix
#   * replay resets the 2-strike send threshold so held-token senders really re-send
#   * 429 Retry-After honored via Headers.GetValues (mock mirrors HttpResponseHeaders)
#   * zero-mail-token runs ledger every email (component visible in the report)
#   * recovery re-runs never-attempted (skipped-no-token) emails/meetings ONLY -
#     'failed' ones are never re-sent / re-created (ambiguous, might exist server-side)
#   * empty-target meeting chats re-derive the thread id from the event (eventId detail)
#
# Dependency-free: needs only Node 18+ and pwsh. Run via tests/run_tests.ps1.
# The seeder is generated ONCE (meetings/chats/channel/emails all enabled) - the
# resilience block is emitted unconditionally, and the content-specific suites extract
# their functions from the same text via the AST.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("demoiq-resilience-test-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null

$script:pass = 0; $script:fail = 0
function Assert-True([bool]$cond, [string]$name) {
  if ($cond) { $script:pass++; Write-Host "  PASS $name" -ForegroundColor Green }
  else { $script:fail++; Write-Host "  FAIL $name" -ForegroundColor Red }
}

# ---- Mock scaffolding: shadow the cmdlets the resilience block calls ----
# A scripted queue of token-endpoint outcomes. Each Invoke-RestMethod call consumes
# one entry: a hashtable => returned as the token response; an ErrorRecord/Exception
# => thrown (simulating a network/HTTP/AAD failure).
$script:irmQueue = @()
$script:irmIdx = 0
$script:irmCalls = 0
function Invoke-RestMethod {
  param([Parameter(ValueFromRemainingArguments = $true)] $Rest)
  $script:irmCalls++
  if ($script:irmIdx -ge $script:irmQueue.Count) { throw "mock IRM queue exhausted (call $($script:irmCalls))" }
  $action = $script:irmQueue[$script:irmIdx]; $script:irmIdx++
  if ($action -is [System.Management.Automation.ErrorRecord] -or $action -is [Exception]) { throw $action }
  return $action
}
# Deterministic + instant: no real waiting or jitter. Start-Sleep records its raw
# arguments (e.g. "-Milliseconds 7000") so tests can assert the waited duration.
$script:sleepCalls = @()
function Start-Sleep { param([Parameter(ValueFromRemainingArguments = $true)] $Rest) $script:sleepCalls += ,($Rest -join ' ') }
function Get-Random { param([Parameter(ValueFromRemainingArguments = $true)] $Rest) 0 }

# Build an ErrorRecord shaped like a PS Invoke-RestMethod failure so the classifier
# can read $_.Exception.Response.StatusCode.value__, Retry-After, and $_.ErrorDetails.Message.
# Headers mirrors the REAL System.Net.Http.Headers.HttpResponseHeaders contract: the
# value is reachable ONLY via GetValues(name) (throws when the header is absent) -
# hashtable-style indexing returns nothing in pwsh, which is exactly the dead pattern
# the production code must avoid, so the mock must not certify it.
function New-MockHttpError {
  param([string]$Message = 'error', [int]$Status = 0, [string]$Json, [int]$RetryAfter = 0)
  $exc = [System.Exception]::new($Message)
  if ($Status -or $RetryAfter) {
    $hdrs = [pscustomobject]@{ __retryAfter = $(if ($RetryAfter) { @("$RetryAfter") } else { $null }) }
    $hdrs | Add-Member -MemberType ScriptMethod -Name GetValues -Value {
      param([string]$name)
      if ($name -eq 'Retry-After' -and $this.__retryAfter) { return $this.__retryAfter }
      throw [System.InvalidOperationException]::new("header '$name' not found")
    }
    $resp = [pscustomobject]@{ StatusCode = [pscustomobject]@{ value__ = [int]$Status }; Headers = $hdrs }
    $exc | Add-Member -NotePropertyName Response -NotePropertyValue $resp -Force
  }
  $er = [System.Management.Automation.ErrorRecord]::new($exc, 'MockHttp', [System.Management.Automation.ErrorCategory]::NotSpecified, $null)
  if ($Json) { $er.ErrorDetails = [System.Management.Automation.ErrorDetails]::new($Json) }
  return $er
}
function Set-IrmQueue { param([object[]]$Actions) $script:irmQueue = @($Actions); $script:irmIdx = 0; $script:irmCalls = 0 }
function Token-Response { param([string]$T = 'access-token') @{ access_token = $T } }

# ---- Recovery send-function stubs: record calls; a successful send marks the ledger seeded ----
# Send-DelegatedChatMessage MIRRORS the real function's 2-strike threshold guard: without
# it, the "replay resets the threshold" fix would be untestable against stubs.
$script:sentChat = @(); $script:sentChannel = @(); $script:sentMeetingChat = @()
$script:sentChatText = @{}; $script:sentMeetingChatThreads = @{}
$script:logIssues = @()
function Log-Issue { param([string]$Severity, [string]$Category, [string]$Message) $script:logIssues += [pscustomobject]@{ severity = $Severity; category = $Category; message = $Message } }
function Send-DelegatedChatMessage { param($SenderUPN, $MessageText, $ItemId)
  if (-not $Global:ChatSendFailures) { $Global:ChatSendFailures = @{} }
  if ($Global:ChatSendFailures[$SenderUPN] -ge 2) { Set-SeedItem -Id $ItemId -Component 'chatMessage' -User $SenderUPN -Status 'failed' -Target ([string]$Global:ChatId) -Detail 'sender failure threshold'; return }
  $script:sentChat += $ItemId; $script:sentChatText[$ItemId] = $MessageText
  Set-SeedItem -Id $ItemId -Component 'chatMessage' -User $SenderUPN -Status 'seeded' -Target ([string]$Global:ChatId) }
function Send-ChannelMessage { param($SenderUPN, $MessageText, $ChannelId, $ItemId) $script:sentChannel += $ItemId; Set-SeedItem -Id $ItemId -Component 'channelMessage' -User $SenderUPN -Status 'seeded' -Target ([string]$ChannelId) }
function Send-MeetingChatMessage { param($ThreadId, $SenderUPN, $MessageText, $ItemId) $script:sentMeetingChat += $ItemId; $script:sentMeetingChatThreads[$ItemId] = $ThreadId; Set-SeedItem -Id $ItemId -Component 'meetingChatMessage' -User $SenderUPN -Status 'seeded' -Target $ThreadId }

function Reset-Mocks {
  $script:sentChat = @(); $script:sentChannel = @(); $script:sentMeetingChat = @(); $script:logIssues = @()
  $script:sentChatText = @{}; $script:sentMeetingChatThreads = @{}; $script:sleepCalls = @()
  $Global:UserTokenCache = @{}; $Global:UserTokenStatus = @{}; $Global:MailTokenStatus = @{}
  $Global:MailTokenCache = @{}; $Global:SeededMeetings = $null; $Global:ReplaySkippedOnly = $false
  $Global:ChatSendFailures = @{}; $Global:ChatId = $null
}

try {
  # --- generate a real seeder ONCE from the fake fixture (all content types enabled;
  # one generation covers the resilience, email and meeting suites below) ---
  $allPrefs = Get-Content -Raw (Join-Path $PSScriptRoot 'fixtures\test-prefs.json') | ConvertFrom-Json
  $allPrefs.contentTypes = @('emails', 'chats', 'channel', 'meetings', 'word', 'ppt', 'excel')
  $allPrefsPath = Join-Path $work 'all-prefs.json'
  ($allPrefs | ConvertTo-Json -Depth 10) | Set-Content -LiteralPath $allPrefsPath -Encoding UTF8
  node (Join-Path $repo 'scripts\generate.js') --html (Join-Path $repo 'engine\DemoIQ.html') `
    --prefs $allPrefsPath --out-dir $work --emit ps1 2>$null | Out-Null
  $ps1Path = Join-Path $work 'DemoIQ.ps1'
  Assert-True (Test-Path $ps1Path) 'generate.js emitted DemoIQ.ps1 from the test fixture'
  $ps1Text = Get-Content -Raw $ps1Path

  # --- the resilience block must be present and extractable ---
  $m = [regex]::Match($ps1Text, '(?ms)^# --- BEGIN DEMOIQ RESILIENCE.*?^# --- END DEMOIQ RESILIENCE ---')
  Assert-True $m.Success 'resilience block found in the generated seeder'
  # Wiring: ledger init + recovery-before-restore + honest banner
  Assert-True ($ps1Text.Contains('Initialize-SeedLedger')) 'seeder calls Initialize-SeedLedger before seeding'
  Assert-True ($ps1Text -match 'Invoke-UserTokenRecovery') 'seeder invokes targeted recovery'
  $recIdx = $ps1Text.IndexOf('if (Get-Command Invoke-UserTokenRecovery')
  $restoreIdx = $ps1Text.IndexOf('CLEANUP: RESTORE TENANT SETTINGS')
  Assert-True ($recIdx -gt 0 -and $restoreIdx -gt 0 -and $recIdx -lt $restoreIdx) 'recovery runs BEFORE the tenant-restore step'
  Assert-True (-not ($ps1Text -match 'DEMOIQ_TELEMETRY_LOGGER|__telOutcome|log_event\.ps1|\[TELEMETRY\]')) 'seeder carries no usage telemetry (removed by platform_patch.js)'
  Assert-True ($ps1Text -match "PARTIAL: ' \+ \`$_missingTotal") 'closing banner reports PARTIAL when items are missing'
  # az-tenant in-run backstop: must sit AFTER the admin-token
  # guard and BEFORE the Copilot content block (i.e. before anything seeds), be
  # need-gated on meetings + Speech, and the doomed hidden 'az login --username'
  # recovery must be gone. The whole generated seeder must also PARSE clean -
  # the guard lives inside a JS template literal where one bad escape breaks
  # the emitted PowerShell far from the edit.
  $idxAdminGuard = $ps1Text.IndexOf('admin-session-failed')
  $idxAzGuard = $ps1Text.IndexOf('az-tenant-mismatch')
  $idxCopilot = $ps1Text.IndexOf('CopilotHttpScript')
  Assert-True ($idxAdminGuard -gt 0 -and $idxAzGuard -gt $idxAdminGuard -and ($idxCopilot -lt 0 -or $idxCopilot -gt $idxAzGuard)) 'az-tenant guard sits between the admin-token guard and the content blocks'
  Assert-True ($ps1Text.Contains('$wantMeetings -and $_azgSpeechId')) 'az-tenant guard is need-gated on meetings + a Speech resource'
  Assert-True (-not ($ps1Text -match 'az login --username')) 'the dead hidden az login --username recovery is gone'
  $parseErrs = $null; $parseToks = $null
  $null = [System.Management.Automation.Language.Parser]::ParseFile($ps1Path, [ref]$parseToks, [ref]$parseErrs)
  Assert-True (@($parseErrs).Count -eq 0) "generated DemoIQ.ps1 parses clean ($(@($parseErrs).Count) parser error(s))"
  # Tenant-state restore: the restore step must finish ALL its Graph work
  # (CA exclusion removal, per-user MFA restore, SecDef re-enable) while still
  # CONNECTED, print the deterministic TENANT STATE block, and only then disconnect.
  # The old order disconnected first, so the SecDef re-enable always failed.
  $_rsIdx = $ps1Text.IndexOf('CLEANUP: RESTORE TENANT SETTINGS')
  $_tsIdx = $ps1Text.IndexOf('TENANT STATE (post-run)')
  Assert-True ($_rsIdx -gt 0 -and $_tsIdx -gt $_rsIdx) 'deterministic TENANT STATE block sits after the restore step'
  $_restoreRegion = $ps1Text.Substring($_rsIdx, $_tsIdx - $_rsIdx)
  Assert-True (-not $_restoreRegion.Contains('Disconnect-MgGraph')) 'no Graph disconnect inside the restore region (SecDef re-enable runs connected)'
  Assert-True ($_restoreRegion.Contains('identitySecurityDefaultsEnforcementPolicy')) 'SecDef re-enable sits inside the still-connected restore region'
  Assert-True ($_restoreRegion.Contains('Restoring per-user MFA')) 'per-user MFA restore sits inside the restore region'
  Assert-True ($ps1Text.IndexOf('Disconnect-MgGraph', $_tsIdx) -gt 0) 'the restore step still disconnects after the TENANT STATE block'
  Assert-True ($ps1Text.Contains('tenant_state.json')) 'tenant-state artifact (tenant_state.json) is written'
  Assert-True ($ps1Text.Contains('$Global:PerUserMfaOriginal')) 'original per-user MFA states are recorded for the restore'
  Assert-True ($ps1Text.Contains('MFA REINSTATED')) 'the MFA REINSTATED verdict line is printed'
  # Post-menu recompute: the retry menu mutates the ledger, so the completion figure and
  # seed_result.json must both be computed AFTER it and before the banner.
  $idxMenu = $ps1Text.IndexOf('SMART RETRY MECHANISM')
  $idxCompute = $ps1Text.IndexOf('$_missingTotal = [int](Write-SeedCompletionReport)')
  $idxResult = $ps1Text.IndexOf('seed_result.json')
  $idxBanner = $ps1Text.IndexOf("PARTIAL: ' + ")
  Assert-True ($idxMenu -gt 0 -and $idxCompute -gt $idxMenu -and $idxResult -gt $idxMenu) 'completion + seed_result.json computed AFTER the retry menu'
  Assert-True ($idxBanner -gt $idxResult) 'closing banner prints after the recomputed completion'
  # The monitor's regex depends on this exact phrase - pin the FULL banner sentence.
  Assert-True ($ps1Text -match "PARTIAL: ' \+ \`$_missingTotal \+ ' requested item\(s\) could not be seeded\.") 'full banner phrase pinned: PARTIAL: N requested item(s) could not be seeded'
  # Security: the token requester must never write the request body / password / token to output.
  $leak = $false
  foreach ($ln in ($m.Value -split "\r?\n")) {
    if ($ln -match 'Write-(Host|Output|Verbose|Warning|Debug|Information)' -and $ln -match '\$Password|\$_body|\$pwd\b|access_token') { $leak = $true }
  }
  Assert-True (-not $leak) 'token requester never writes the password or request body to output'
  # GitHub Copilot adaptation: demo users sign in as a public client - no certificate signs a client assertion.
  Assert-True ($m.Value -notmatch 'client_assertion|New-ClientAssertionJwt') 'demo-user token requests carry no client assertion'
  Assert-True ($m.Value -match 'grant_type=''password'';client_id=\$Global:AppId;username=\$Upn;') 'demo-user token requests are public-client password grants'

  $OutputDir = Join-Path $work 'ledger-out'
  New-Item -ItemType Directory -Force $OutputDir | Out-Null
  $Global:AppId = 'app'; $Global:TenantId = 'tenant'
  $Global:UserPasswords = @{}

  # --- load the REAL emitted functions into this scope ---
  Invoke-Expression $m.Value
  Assert-True ((Get-Command Request-DemoDelegatedToken -ErrorAction SilentlyContinue) -and (Get-Command Invoke-UserTokenRecovery -ErrorAction SilentlyContinue)) 'resilience functions defined after Invoke-Expression'

  $scope = 'https://graph.microsoft.com/Chat.ReadWrite'

  # ============================================================
  # 1. CLASSIFICATION: transient network failure is retried, then succeeds
  # ============================================================
  Reset-Mocks
  Set-IrmQueue @(
    (New-MockHttpError -Message 'A connection attempt failed because the connected party did not properly respond after a period of time. (login.microsoftonline.com:443)'),
    (Token-Response 'adil-token')
  )
  $r = Request-DemoDelegatedToken -Upn 'AdilE@x' -Password 'pw' -Scope $scope -Label 'ROPC' -MaxAttempts 4
  Assert-True ($r.ok -and $r.token -eq 'adil-token') 'transient timeout: succeeds on the retry'
  Assert-True (-not $r.permanent) 'transient timeout: not classified permanent'
  Assert-True ($script:irmCalls -eq 2) 'transient timeout: exactly one retry (2 calls)'

  # ============================================================
  # 2. CLASSIFICATION: permanent AADSTS bad-credential fails fast (no retry)
  # ============================================================
  Reset-Mocks
  Set-IrmQueue @(
    (New-MockHttpError -Status 400 -Json '{"error":"invalid_grant","error_description":"AADSTS50126: Error validating credentials due to invalid username or password."}'),
    (Token-Response 'should-not-be-used')
  )
  $r = Request-DemoDelegatedToken -Upn 'AdilE@x' -Password 'badpw' -Scope $scope -Label 'ROPC' -MaxAttempts 4
  Assert-True (-not $r.ok -and $r.permanent) 'bad credential: fails fast and is permanent'
  Assert-True ($script:irmCalls -eq 1) 'bad credential: no retry (1 call)'

  # ============================================================
  # 3. CLASSIFICATION: 429 and 5xx are transient; empty password is permanent
  # ============================================================
  Reset-Mocks
  Set-IrmQueue @((New-MockHttpError -Status 429 -RetryAfter 1 -Message 'Too Many Requests'), (Token-Response 't'))
  $r = Request-DemoDelegatedToken -Upn 'u@x' -Password 'pw' -Scope $scope -MaxAttempts 4
  Assert-True ($r.ok -and $script:irmCalls -eq 2) 'HTTP 429: retried then succeeds'

  Reset-Mocks
  Set-IrmQueue @((New-MockHttpError -Status 503 -Message 'Service Unavailable'), (New-MockHttpError -Status 502 -Message 'Bad Gateway'), (Token-Response 't'))
  $r = Request-DemoDelegatedToken -Upn 'u@x' -Password 'pw' -Scope $scope -MaxAttempts 4
  Assert-True ($r.ok -and $script:irmCalls -eq 3) 'HTTP 5xx: retried across attempts then succeeds'

  Reset-Mocks
  Set-IrmQueue @((Token-Response 'unused'))
  $r = Request-DemoDelegatedToken -Upn 'u@x' -Password '' -Scope $scope -MaxAttempts 4
  Assert-True (-not $r.ok -and $r.permanent -and $script:irmCalls -eq 0) 'empty password: permanent, endpoint never called'

  # ============================================================
  # 4. EXHAUSTION: persistent transient failure returns non-permanent failure after MaxAttempts
  # ============================================================
  Reset-Mocks
  Set-IrmQueue @(
    (New-MockHttpError -Status 503 -Message 'down'), (New-MockHttpError -Status 503 -Message 'down'),
    (New-MockHttpError -Status 503 -Message 'down'), (New-MockHttpError -Status 503 -Message 'down')
  )
  $r = Request-DemoDelegatedToken -Upn 'u@x' -Password 'pw' -Scope $scope -MaxAttempts 4
  Assert-True (-not $r.ok -and -not $r.permanent -and $script:irmCalls -eq 4) 'exhausted transient: fails after MaxAttempts, still not permanent'

  # ============================================================
  # 5. LEDGER: record / query / persistence across a simulated restart
  # ============================================================
  Reset-Mocks
  Initialize-SeedLedger
  Set-SeedItem -Id 'chat:0:0' -Component 'chatMessage' -User 'A@x' -Status 'seeded' -Target 'chatid'
  Set-SeedItem -Id 'chat:0:1' -Component 'chatMessage' -User 'B@x' -Status 'skipped-no-token' -Target 'chatid'
  Assert-True (Test-SeedItemDone 'chat:0:0') 'ledger: seeded item reports done'
  Assert-True (-not (Test-SeedItemDone 'chat:0:1')) 'ledger: skipped item reports not-done'
  Assert-True (Test-Path (Join-Path $OutputDir 'seed_ledger.json')) 'ledger: persisted to seed_ledger.json'
  # simulate a process restart: fresh in-memory ledger, reload from disk
  $Global:SeedLedger = [ordered]@{}
  Initialize-SeedLedger
  Assert-True (Test-SeedItemDone 'chat:0:0') 'ledger: seeded state survives a restart (reload from disk)'
  Assert-True (-not (Test-SeedItemDone 'chat:0:1')) 'ledger: skipped state survives a restart'

  # ============================================================
  # 6. ACCEPTANCE TEST 1: transient timeout on AdilE -> targeted replay -> full counts,
  #    no duplication of other users' posts.
  # ============================================================
  Reset-Mocks
  Remove-Item (Join-Path $OutputDir 'seed_ledger.json') -Force -ErrorAction SilentlyContinue
  Initialize-SeedLedger
  $users = @('DakotaS@x','EkaS@x','HadarC@x','KaiC@x','AdilE@x')
  $Global:UserPasswords = @{}; foreach ($u in $users) { $Global:UserPasswords[$u] = 'pw' }
  # AdilE got no token on the main pass (transient); the other four did.
  foreach ($u in $users) { $Global:UserTokenCache[$u] = if ($u -eq 'AdilE@x') { $null } else { 'tok' } }
  $Global:UserTokenStatus['AdilE@x'] = @{ state = 'transient'; error = 'timeout' }

  # Authored content matching the incident: 18 chat msgs, 16 channel posts, 44 meeting-chat msgs.
  # AdilE owns 4 chat, 4 channel, 11 meeting-chat; the rest belong to the other four users.
  $chatMsgs = @(); for ($i = 0; $i -lt 18; $i++) { $snd = if ($i % 4 -eq 3) { 'AdilE@x' } else { $users[$i % 4] }; $chatMsgs += [pscustomobject]@{ sender = $snd; text = "chat $i" } }
  $chanArr = @(); for ($i = 0; $i -lt 16; $i++) { $snd = if ($i % 4 -eq 3) { 'AdilE@x' } else { $users[$i % 4] }; $chanArr += [pscustomobject]@{ channelName = 'General'; sender = $snd; message = "chan $i" } }
  $mtgChat = @(); for ($j = 0; $j -lt 44; $j++) { $snd = if ($j % 4 -eq 3) { 'AdilE@x' } else { $users[$j % 3] }; $mtgChat += [pscustomobject]@{ sender = $snd; text = "mc $j" } }
  $Global:SeedData = [pscustomobject]@{
    chats    = @([pscustomobject]@{ messages = $chatMsgs })
    channels = $chanArr
    meetings = @([pscustomobject]@{ subject = 'M'; chatMessages = $mtgChat })
  }
  # Seed the ledger the way the main pass would: AdilE's items skipped-no-token, others seeded.
  $adilChat = 0; $adilChan = 0; $adilMc = 0
  for ($i = 0; $i -lt 18; $i++) { $snd = $chatMsgs[$i].sender; if ($snd -eq 'AdilE@x') { Set-SeedItem -Id "chat:0:$i" -Component 'chatMessage' -User $snd -Status 'skipped-no-token' -Target 'chatid'; $adilChat++ } else { Set-SeedItem -Id "chat:0:$i" -Component 'chatMessage' -User $snd -Status 'seeded' -Target 'chatid' } }
  for ($i = 0; $i -lt 16; $i++) { $snd = $chanArr[$i].sender; if ($snd -eq 'AdilE@x') { Set-SeedItem -Id "channel:$i" -Component 'channelMessage' -User $snd -Status 'skipped-no-token' -Target 'chanid'; $adilChan++ } else { Set-SeedItem -Id "channel:$i" -Component 'channelMessage' -User $snd -Status 'seeded' -Target 'chanid' } }
  Set-SeedItem -Id 'meeting:0' -Component 'meeting' -User 'DakotaS@x' -Status 'seeded' -Target 'thread-0'
  for ($j = 0; $j -lt 44; $j++) { $snd = $mtgChat[$j].sender; if ($snd -eq 'AdilE@x') { Set-SeedItem -Id "meetingchat:0:$j" -Component 'meetingChatMessage' -User $snd -Status 'skipped-no-token' -Target 'thread-0'; $adilMc++ } else { Set-SeedItem -Id "meetingchat:0:$j" -Component 'meetingChatMessage' -User $snd -Status 'seeded' -Target 'thread-0' } }

  # Sanity: the pre-recovery state matches the incident's numbers.
  $pre = Get-SeedCompletionReport
  Assert-True ($pre['chatMessage'].seeded -eq 14 -and $pre['chatMessage'].requested -eq 18) 'AT1 pre: chat 14/18 seeded'
  Assert-True ($pre['channelMessage'].seeded -eq 12 -and $pre['channelMessage'].requested -eq 16) 'AT1 pre: channel 12/16 seeded'
  Assert-True ($pre['meetingChatMessage'].seeded -eq 33 -and $pre['meetingChatMessage'].requested -eq 44) 'AT1 pre: meeting-chat 33/44 seeded'

  # AdilE's retried token now succeeds on the first recovery attempt.
  Set-IrmQueue @((Token-Response 'adil-recovered'))
  Invoke-UserTokenRecovery

  $post = Get-SeedCompletionReport
  Assert-True ($post['chatMessage'].seeded -eq 18 -and $post['chatMessage'].missing -eq 0) 'AT1: group-chat 18/18 after replay'
  Assert-True ($post['channelMessage'].seeded -eq 16 -and $post['channelMessage'].missing -eq 0) 'AT1: channel 16/16 after replay'
  Assert-True ($post['meetingChatMessage'].seeded -eq 44 -and $post['meetingChatMessage'].missing -eq 0) 'AT1: meeting-chat 44/44 after replay'
  # Only AdilE's items were re-sent - no duplication of the other users' already-seeded posts.
  Assert-True ($script:sentChat.Count -eq $adilChat -and $adilChat -eq 4) 'AT1: exactly AdilE''s 4 chat msgs replayed (no dupes)'
  Assert-True ($script:sentChannel.Count -eq $adilChan -and $adilChan -eq 4) 'AT1: exactly AdilE''s 4 channel posts replayed (no dupes)'
  Assert-True ($script:sentMeetingChat.Count -eq $adilMc -and $adilMc -eq 11) 'AT1: exactly AdilE''s 11 meeting-chat msgs replayed (no dupes)'
  # Every replayed id must belong to an AdilE-sender item (the only ones left pending).
  $replayedAllAdil = $true
  foreach ($id in $script:sentChat) { if ($Global:SeedLedger[$id].user -ne 'AdilE@x') { $replayedAllAdil = $false } }
  foreach ($id in $script:sentChannel) { if ($Global:SeedLedger[$id].user -ne 'AdilE@x') { $replayedAllAdil = $false } }
  foreach ($id in $script:sentMeetingChat) { if ($Global:SeedLedger[$id].user -ne 'AdilE@x') { $replayedAllAdil = $false } }
  Assert-True $replayedAllAdil 'AT1: every replayed item belongs to AdilE (no other user re-posted)'

  # ============================================================
  # 7. ACCEPTANCE TEST 2: permanent AdilE failure -> content preserved, partial report,
  #    other users untouched.
  # ============================================================
  Reset-Mocks
  Remove-Item (Join-Path $OutputDir 'seed_ledger.json') -Force -ErrorAction SilentlyContinue
  Initialize-SeedLedger
  $Global:UserPasswords = @{}; foreach ($u in $users) { $Global:UserPasswords[$u] = 'pw' }
  foreach ($u in $users) { $Global:UserTokenCache[$u] = if ($u -eq 'AdilE@x') { $null } else { 'tok' } }
  # Main pass already classified AdilE permanent (bad password).
  $Global:UserTokenStatus['AdilE@x'] = @{ state = 'permanent'; error = 'AADSTS50126' }
  $Global:SeedData = [pscustomobject]@{ chats = @([pscustomobject]@{ messages = $chatMsgs }); channels = $chanArr; meetings = @([pscustomobject]@{ subject = 'M'; chatMessages = $mtgChat }) }
  for ($i = 0; $i -lt 18; $i++) { $snd = $chatMsgs[$i].sender; Set-SeedItem -Id "chat:0:$i" -Component 'chatMessage' -User $snd -Status $(if ($snd -eq 'AdilE@x') { 'skipped-no-token' } else { 'seeded' }) -Target 'chatid' }
  for ($i = 0; $i -lt 16; $i++) { $snd = $chanArr[$i].sender; Set-SeedItem -Id "channel:$i" -Component 'channelMessage' -User $snd -Status $(if ($snd -eq 'AdilE@x') { 'skipped-no-token' } else { 'seeded' }) -Target 'chanid' }
  for ($j = 0; $j -lt 44; $j++) { $snd = $mtgChat[$j].sender; Set-SeedItem -Id "meetingchat:0:$j" -Component 'meetingChatMessage' -User $snd -Status $(if ($snd -eq 'AdilE@x') { 'skipped-no-token' } else { 'seeded' }) -Target 'thread-0' }

  # Recovery must NOT call the token endpoint for a permanent user.
  Set-IrmQueue @()
  Invoke-UserTokenRecovery

  Assert-True ($script:irmCalls -eq 0) 'AT2: permanent user is not retried against the token endpoint'
  Assert-True ($script:sentChat.Count -eq 0 -and $script:sentChannel.Count -eq 0 -and $script:sentMeetingChat.Count -eq 0) 'AT2: nothing replayed for a permanent failure'
  $rep = Get-SeedCompletionReport
  Assert-True ($rep['chatMessage'].seeded -eq 14 -and $rep['chatMessage'].missing -eq 4) 'AT2: other users'' chat content preserved (14/18)'
  Assert-True ($rep['channelMessage'].missing -eq 4 -and $rep['meetingChatMessage'].missing -eq 11) 'AT2: AdilE''s items reported missing per component'
  Assert-True ([string]$rep['chatMessage'].missingByUser['AdilE@x'] -eq '4') 'AT2: partial report attributes the 4 missing chats to AdilE'
  Assert-True ([string]$rep['meetingChatMessage'].missingByUser['AdilE@x'] -eq '11') 'AT2: partial report attributes the 11 missing meeting-chats to AdilE'
  $missTotal = 0; foreach ($k in $rep.Keys) { $missTotal += [int]$rep[$k].missing }
  Assert-True ($missTotal -eq 19) 'AT2: total missing = 19 (no clean DONE)'

  # ============================================================
  # 8. IDEMPOTENCY / NO-OP: recovery with no pending items does nothing (and never
  #    touches Office files - files are not a ledger message component).
  # ============================================================
  Reset-Mocks
  Remove-Item (Join-Path $OutputDir 'seed_ledger.json') -Force -ErrorAction SilentlyContinue
  Initialize-SeedLedger
  $Global:SeedData = [pscustomobject]@{ chats = @([pscustomobject]@{ messages = $chatMsgs }); channels = $chanArr; meetings = @() }
  Set-SeedItem -Id 'chat:0:0' -Component 'chatMessage' -User 'A@x' -Status 'seeded' -Target 'c'
  Set-SeedItem -Id 'email:0' -Component 'email' -User 'A@x' -Status 'failed' -Detail 'HTTP 500'
  Set-SeedItem -Id 'meeting:0' -Component 'meeting' -User 'A@x' -Status 'failed' -Detail 'create failed'
  Set-IrmQueue @()
  Invoke-UserTokenRecovery
  Assert-True ($script:irmCalls -eq 0 -and $script:sentChat.Count -eq 0) 'no-op: nothing to replay when no message items are pending'
  # A failed email / failed meeting are NOT message components -> recovery must never
  # replay them (re-creating a meeting or re-sending a file would duplicate).
  $rep8 = Get-SeedCompletionReport
  Assert-True ($rep8['meeting'].missing -eq 1) 'no-op: failed meeting stays missing (never auto-recreated by recovery)'
  Assert-True ($rep8['email'].missing -eq 1) 'no-op: failed email stays missing (recovery never touches non-Teams components)'

  # ============================================================
  # 9. PARTIAL RECOVERY: transient user still failing at recovery time -> items stay
  #    missing and are reported (recovery does not fabricate success).
  # ============================================================
  Reset-Mocks
  Remove-Item (Join-Path $OutputDir 'seed_ledger.json') -Force -ErrorAction SilentlyContinue
  Initialize-SeedLedger
  $Global:UserPasswords = @{ 'AdilE@x' = 'pw' }
  $Global:UserTokenCache = @{ 'AdilE@x' = $null }
  $Global:UserTokenStatus = @{ 'AdilE@x' = @{ state = 'transient'; error = 'timeout' } }
  $Global:SeedData = [pscustomobject]@{ chats = @([pscustomobject]@{ messages = @([pscustomobject]@{ sender = 'AdilE@x'; text = 'x' }) }); channels = @(); meetings = @() }
  Set-SeedItem -Id 'chat:0:0' -Component 'chatMessage' -User 'AdilE@x' -Status 'skipped-no-token' -Target 'chatid'
  # Recovery's 4 attempts all time out -> token still unavailable -> item stays missing.
  Set-IrmQueue @((New-MockHttpError -Message 'connection attempt failed'), (New-MockHttpError -Message 'connection attempt failed'), (New-MockHttpError -Message 'connection attempt failed'), (New-MockHttpError -Message 'connection attempt failed'))
  Invoke-UserTokenRecovery
  Assert-True ($script:sentChat.Count -eq 0) 'partial: no send when the token never recovers'
  $rep9 = Get-SeedCompletionReport
  Assert-True ($rep9['chatMessage'].missing -eq 1) 'partial: still-missing item is reported, not silently dropped'
  Assert-True (@($script:logIssues | Where-Object { $_.category -eq 'REPLAY' -and $_.severity -eq 'FAIL' }).Count -eq 1) 'partial: a REPLAY FAIL issue is logged for the unrecovered user'

  # ============================================================
  # 9b. FLAT-FORMAT CHAT REPLAY: legacy flat ids are chat:0:<flatIdx> - replay must
  #     resolve item N's text (element [$_g] is always element 0) and strip the
  #     legacy "HH:MM sender@upn " prefix exactly like the seed loop does.
  # ============================================================
  Reset-Mocks
  Remove-Item (Join-Path $OutputDir 'seed_ledger.json') -Force -ErrorAction SilentlyContinue
  Initialize-SeedLedger
  $Global:UserPasswords = @{ 'AdilE@x' = 'pw' }
  $Global:UserTokenCache = @{ 'AdilE@x' = 'tok' }   # token survived: replay goes straight to the send
  $Global:SeedData = [pscustomobject]@{ chats = @(
    '09:00 AdilE@x first flat message',
    '09:05 AdilE@x second flat message',
    [pscustomobject]@{ sender = 'AdilE@x'; message = 'third flat message (object form)' }) }
  Set-SeedItem -Id 'chat:0:1' -Component 'chatMessage' -User 'AdilE@x' -Status 'skipped-no-token' -Target 'chatid'
  Set-SeedItem -Id 'chat:0:2' -Component 'chatMessage' -User 'AdilE@x' -Status 'skipped-no-token' -Target 'chatid'
  Set-IrmQueue @()
  Invoke-UserTokenRecovery
  Assert-True ($script:sentChat -contains 'chat:0:1' -and $script:sentChat -contains 'chat:0:2') 'flat replay: both pending flat items re-sent'
  Assert-True ($script:sentChatText['chat:0:1'] -eq 'second flat message') 'flat replay: item 1 posts ITS OWN text (prefix stripped), not item 0''s'
  Assert-True ($script:sentChatText['chat:0:2'] -eq 'third flat message (object form)') 'flat replay: object-form flat item resolves .message by flat index'

  # ============================================================
  # 9c. THRESHOLD RESET: a sender who KEPT their token but tripped the 2-strike send
  #     threshold must be reset in the replay loop - otherwise the replay re-marks
  #     their items failed with zero Graph calls (the stub mirrors the real guard).
  # ============================================================
  Reset-Mocks
  Remove-Item (Join-Path $OutputDir 'seed_ledger.json') -Force -ErrorAction SilentlyContinue
  Initialize-SeedLedger
  $Global:UserPasswords = @{ 'AdilE@x' = 'pw' }
  $Global:UserTokenCache = @{ 'AdilE@x' = 'tok' }
  $Global:ChatSendFailures = @{ 'AdilE@x' = 2 }     # tripped during the main pass
  $Global:SeedData = [pscustomobject]@{ chats = @([pscustomobject]@{ messages = @([pscustomobject]@{ sender = 'AdilE@x'; text = 'retry me' }) }) }
  Set-SeedItem -Id 'chat:0:0' -Component 'chatMessage' -User 'AdilE@x' -Status 'failed' -Target 'chatid' -Detail 'sender failure threshold'
  Set-IrmQueue @()
  Invoke-UserTokenRecovery
  Assert-True ($script:sentChat -contains 'chat:0:0') 'threshold reset: the replay actually attempts the send (not a zero-call re-fail)'
  Assert-True ($Global:ChatSendFailures['AdilE@x'] -eq 0) 'threshold reset: ChatSendFailures cleared for the replayed sender'
  Assert-True (Test-SeedItemDone 'chat:0:0') 'threshold reset: the item lands seeded after the replay'

  # ============================================================
  # 9d. RETRY-AFTER: a 429's Retry-After header is honored via Headers.GetValues()
  #     (indexing HttpResponseHeaders returns empty in pwsh - the old dead pattern);
  #     an absent header falls back to the exponential backoff, never a 0s sleep.
  # ============================================================
  Reset-Mocks
  Set-IrmQueue @((New-MockHttpError -Status 429 -RetryAfter 7 -Message 'Too Many Requests'), (Token-Response 't'))
  $r = Request-DemoDelegatedToken -Upn 'u@x' -Password 'pw' -Scope $scope -MaxAttempts 4
  Assert-True ($r.ok) 'retry-after: 429 retried then succeeds'
  Assert-True (@($script:sleepCalls | Where-Object { $_ -eq '-Milliseconds 7000' }).Count -eq 1) 'retry-after: waited the header''s 7s (7000ms), not the 2s default backoff'
  Reset-Mocks
  Set-IrmQueue @((New-MockHttpError -Status 429 -Message 'Too Many Requests'), (Token-Response 't'))
  $r = Request-DemoDelegatedToken -Upn 'u@x' -Password 'pw' -Scope $scope -MaxAttempts 4
  Assert-True ($r.ok) 'retry-after absent: 429 still retried'
  Assert-True (@($script:sleepCalls | Where-Object { $_ -eq '-Milliseconds 2000' }).Count -eq 1) 'retry-after absent: falls back to the 2s exponential backoff (never 0s)'

  # ============================================================
  # 10. MEETING SUCCESS DETECTION: the first meeting must be ledgered 'seeded' even
  #     when $Global:SeededMeetings starts $null (@($null).Count is 1, not 0 - a wrong
  #     guard would mark a successful first meeting as failed and fabricate a miss).
  # ============================================================
  Reset-Mocks
  Remove-Item (Join-Path $OutputDir 'seed_ledger.json') -Force -ErrorAction SilentlyContinue
  Initialize-SeedLedger
  # The single generation above enabled meetings, so the REAL Seed-TeamsMeetings is in
  # $ps1Text - extract it via the AST (a (?ms)^function...^} regex trips on nested braces).
  $_astTok = $null; $_astErr = $null
  $mtgAst = [System.Management.Automation.Language.Parser]::ParseInput($ps1Text, [ref]$_astTok, [ref]$_astErr)
  $mtgDef = $mtgAst.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Seed-TeamsMeetings' }, $true) | Select-Object -First 1
  Assert-True ($null -ne $mtgDef) 'Seed-TeamsMeetings extracted from the generated seeder'
  Invoke-Expression $mtgDef.Extent.Text
  # Stub Create-DemoMeeting to simulate a successful create: append to SeededMeetings and
  # return a thread id - exactly what the real function does on success.
  function Create-DemoMeeting { param($OrganizerUPN, $Subject, $BodyContent, $AttendeeUPNs, $StartDateTime, $EndDateTime, $TimeZone, $Recurrence)
    if (-not $Global:SeededMeetings) { $Global:SeededMeetings = @() }
    $Global:SeededMeetings += [pscustomobject]@{ Subject = $Subject }
    return 'thread-xyz'
  }
  $Global:SeededMeetings = $null   # the real pre-first-meeting state
  $Global:AllUsers = @('DakotaS@x')
  $Global:UserPasswords = @{ 'DakotaS@x' = 'pw' }
  $Global:MailTokenCache = @{ 'DakotaS@x' = 'mailtok' }
  $fileUrlMap = @{}; $fileDisplayMap = @{}
  $Global:SeedData = [pscustomobject]@{ meetings = @([pscustomobject]@{ subject = 'Kickoff'; organizer = 'DakotaS@x'; attendees = @('DakotaS@x'); startDateTime = '2026-07-21T09:00:00'; endDateTime = '2026-07-21T09:30:00'; timeZone = 'UTC'; body = '<p>x</p>'; chatMessages = @([pscustomobject]@{ sender = 'DakotaS@x'; text = 'hi' }) }) }
  Seed-TeamsMeetings
  Assert-True (Test-SeedItemDone 'meeting:0') 'meeting: first meeting ledgered seeded despite null SeededMeetings start'
  $rep10 = Get-SeedCompletionReport
  Assert-True (-not $rep10.Contains('meeting') -or [int]$rep10['meeting'].missing -eq 0) 'meeting: no fabricated miss for a successful first meeting'
  Assert-True (Test-SeedItemDone 'meetingchat:0:0') 'meeting: the meeting-chat message posted and ledgered seeded'

  # ============================================================
  # 11. EMAIL BLIND SPOT: when EVERY mail token fails, the early return must ledger
  #     each flat email skipped-no-token - the component must never vanish from the
  #     completion report (a clean DONE with zero emails was the failure mode).
  # ============================================================
  # Extract the REAL email functions from the same generated text (used by 11 + 12).
  foreach ($_fn in @('Seed-OutlookEmails', 'Split-EmailAddresses', 'ConvertTo-RecipientObjects')) {
    $_fnDef = $mtgAst.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) |
      Where-Object { $_.Name -eq $_fn } | Select-Object -First 1
    Assert-True ($null -ne $_fnDef) "$_fn extracted from the generated seeder"
    Invoke-Expression $_fnDef.Extent.Text
  }
  # Token state is arranged per test - the real Acquire-MailTokens must not run.
  function Acquire-MailTokens {}
  $SeedEmails = $true
  $AdminUPN = 'admin@x'
  Reset-Mocks
  Remove-Item (Join-Path $OutputDir 'seed_ledger.json') -Force -ErrorAction SilentlyContinue
  Initialize-SeedLedger
  $Global:MailTokenCache = @{}
  $Global:SeededEmailMsgs = @()
  $Global:SeedData = [pscustomobject]@{ emails = @(
    [pscustomobject]@{ from = 'DakotaS@x'; to = 'EkaS@x'; subject = 'S1'; body = 'b1' },
    [pscustomobject]@{ from = 'EkaS@x'; to = 'DakotaS@x'; subject = 'S2'; body = 'b2' },
    [pscustomobject]@{ from = 'AdilE@x'; to = 'EkaS@x'; subject = 'S3'; body = 'b3' }) }
  Set-IrmQueue @()
  Seed-OutlookEmails
  Assert-True ($script:irmCalls -eq 0) 'email blind spot: no Graph calls when zero mail tokens'
  Assert-True ((-not (Test-SeedItemDone 'email:0')) -and $Global:SeedLedger['email:0'].status -eq 'skipped-no-token') 'email blind spot: email:0 ledgered skipped-no-token'
  Assert-True ($Global:SeedLedger['email:1'].user -eq 'EkaS@x' -and $Global:SeedLedger['email:2'].user -eq 'AdilE@x') 'email blind spot: ledger attributes each email to its sender'
  $rep11 = Get-SeedCompletionReport
  Assert-True ($rep11.Contains('email') -and [int]$rep11['email'].missing -eq 3) 'email blind spot: completion report shows email 0/3 (component visible, not vanished)'

  # ============================================================
  # 12. EMAIL REPLAY IS SKIPPED-ONLY: under $Global:ReplaySkippedOnly the email pass
  #     re-sends never-attempted (skipped-no-token) emails but NEVER re-attempts a
  #     'failed' one (ambiguous - it may have landed server-side); without the flag
  #     (the user-driven retry menu) failed emails stay retryable.
  # ============================================================
  Reset-Mocks
  Remove-Item (Join-Path $OutputDir 'seed_ledger.json') -Force -ErrorAction SilentlyContinue
  Initialize-SeedLedger
  $Global:MailTokenCache = @{ 'DakotaS@x' = 'mtok'; 'EkaS@x' = 'mtok2' }
  $Global:SeededEmailMsgs = @()
  $Global:SeedData = [pscustomobject]@{ emails = @(
    [pscustomobject]@{ from = 'DakotaS@x'; to = 'EkaS@x'; subject = 'Ambiguous send'; body = 'b1' },
    [pscustomobject]@{ from = 'EkaS@x'; to = 'DakotaS@x'; subject = 'Never attempted'; body = 'b2' }) }
  Set-SeedItem -Id 'email:0' -Component 'email' -User 'DakotaS@x' -Status 'failed' -Detail 'HTTP 500'
  Set-SeedItem -Id 'email:1' -Component 'email' -User 'EkaS@x' -Status 'skipped-no-token' -Detail 'no mail token'
  # Queue serves ONLY email:1's send (draft POST + send POST) - a re-attempt of the
  # failed email:0 would exhaust the queue and fail loudly.
  Set-IrmQueue @( @{ id = 'd1'; internetMessageId = 'im1'; conversationId = 'c1' }, @{} )
  $Global:ReplaySkippedOnly = $true
  try { Seed-OutlookEmails } finally { $Global:ReplaySkippedOnly = $false }
  Assert-True ($script:irmCalls -eq 2) 'skipped-only replay: exactly one email sent (2 Graph calls)'
  Assert-True (Test-SeedItemDone 'email:1') 'skipped-only replay: the never-attempted email is seeded'
  Assert-True ($Global:SeedLedger['email:0'].status -eq 'failed') 'skipped-only replay: the failed (ambiguous) email is NOT re-sent'
  # Same ledger, flag OFF (retry-menu semantics): the failed email IS re-attempted.
  Set-IrmQueue @( @{ id = 'd2'; internetMessageId = 'im2'; conversationId = 'c2' }, @{} )
  Seed-OutlookEmails
  Assert-True ($script:irmCalls -eq 2 -and (Test-SeedItemDone 'email:0')) 'flag off: the user-driven pass still retries the failed email (menu semantics preserved)'

  # ============================================================
  # 13. MEETING RECOVERY: a meeting skipped for a missing organizer token is created
  #     EXACTLY ONCE by the recovery pass (per-user cal-token re-acquire), and a
  #     'failed' meeting is NEVER re-created (may exist server-side).
  # ============================================================
  $script:createMeetingCalls = 0
  function Create-DemoMeeting { param($OrganizerUPN, $Subject, $BodyContent, $AttendeeUPNs, $StartDateTime, $EndDateTime, $TimeZone, $Recurrence)
    $script:createMeetingCalls++
    if (-not $Global:SeededMeetings) { $Global:SeededMeetings = @() }
    $Global:SeededMeetings += [pscustomobject]@{ Subject = $Subject; EventId = ('EVT-' + $script:createMeetingCalls); OrganizerUPN = $OrganizerUPN }
    return ('thread-' + $script:createMeetingCalls)
  }
  Reset-Mocks
  Remove-Item (Join-Path $OutputDir 'seed_ledger.json') -Force -ErrorAction SilentlyContinue
  Initialize-SeedLedger
  $Global:AllUsers = @('DakotaS@x', 'EkaS@x')
  $Global:UserPasswords = @{ 'DakotaS@x' = 'pw'; 'EkaS@x' = 'pw' }
  $Global:UserTokenCache = @{ 'DakotaS@x' = 'tok'; 'EkaS@x' = 'tok' }
  $Global:MailTokenCache = @{ 'EkaS@x' = 'etok' }   # organizer of the FAILED meeting has a token
  $Global:MailTokenStatus = @{ 'DakotaS@x' = @{ state = 'transient'; error = 'timeout' } }
  $fileUrlMap = @{}; $fileDisplayMap = @{}
  $Global:SeedData = [pscustomobject]@{ meetings = @(
    [pscustomobject]@{ subject = 'SkippedMtg'; organizer = 'DakotaS@x'; attendees = @('DakotaS@x'); startDateTime = '2026-07-21T09:00:00'; endDateTime = '2026-07-21T09:30:00'; timeZone = 'UTC'; body = '<p>x</p>'; chatMessages = @([pscustomobject]@{ sender = 'DakotaS@x'; text = 'hello' }) },
    [pscustomobject]@{ subject = 'FailedMtg'; organizer = 'EkaS@x'; attendees = @('EkaS@x'); startDateTime = '2026-07-21T10:00:00'; endDateTime = '2026-07-21T10:30:00'; timeZone = 'UTC'; body = '<p>y</p>'; chatMessages = @() }) }
  Set-SeedItem -Id 'meeting:0' -Component 'meeting' -User 'DakotaS@x' -Status 'skipped-no-token' -Detail 'no organizer calendar token'
  Set-SeedItem -Id 'meetingchat:0:0' -Component 'meetingChatMessage' -User 'DakotaS@x' -Status 'failed' -Detail 'meeting not created'
  Set-SeedItem -Id 'meeting:1' -Component 'meeting' -User 'EkaS@x' -Status 'failed' -Detail 'creation failed after 3 attempts'
  Set-IrmQueue @((Token-Response 'dakota-cal'))   # ONE cal-token re-acquire (DakotaS only)
  Invoke-UserTokenRecovery
  Assert-True ($script:createMeetingCalls -eq 1) 'meeting recovery: exactly ONE meeting created (skipped-no-token only)'
  Assert-True (Test-SeedItemDone 'meeting:0' -and $Global:SeedLedger['meeting:0'].target -eq 'thread-1') 'meeting recovery: the skipped meeting is created and its thread recorded'
  Assert-True ($Global:SeedLedger['meeting:0'].detail -eq 'eventId:EVT-1') 'meeting recovery: the meeting ledger carries the eventId detail'
  Assert-True ($Global:SeedLedger['meeting:1'].status -eq 'failed') 'meeting recovery: the FAILED meeting is never re-created'
  Assert-True (Test-SeedItemDone 'meetingchat:0:0' -and $script:sentMeetingChat.Count -eq 1) 'meeting recovery: its chat message posts exactly once (no double-post from the replay loop)'
  Assert-True ($script:irmCalls -eq 1) 'meeting recovery: one token call (per-user cal loop skips holders + permanent)'

  # ============================================================
  # 14. THREAD RE-DERIVATION: a created meeting whose joinUrl never parsed (empty
  #     Target, eventId in Detail) gets its chat thread re-derived from the calendar
  #     event; a meeting without an eventId detail leaves its items missing (honest).
  # ============================================================
  Reset-Mocks
  Remove-Item (Join-Path $OutputDir 'seed_ledger.json') -Force -ErrorAction SilentlyContinue
  Initialize-SeedLedger
  $Global:UserPasswords = @{ 'DakotaS@x' = 'pw' }
  $Global:UserTokenCache = @{ 'DakotaS@x' = 'tok' }
  $Global:MailTokenCache = @{ 'DakotaS@x' = 'mailtok' }
  $Global:SeedData = [pscustomobject]@{ meetings = @(
    [pscustomobject]@{ subject = 'M0'; organizer = 'DakotaS@x'; chatMessages = @([pscustomobject]@{ sender = 'DakotaS@x'; text = 'hi' }) },
    [pscustomobject]@{ subject = 'M1'; organizer = 'DakotaS@x'; chatMessages = @([pscustomobject]@{ sender = 'DakotaS@x'; text = 'x2' }) }) }
  Set-SeedItem -Id 'meeting:0' -Component 'meeting' -User 'DakotaS@x' -Status 'seeded' -Target '' -Detail 'eventId:EVT9'
  Set-SeedItem -Id 'meetingchat:0:0' -Component 'meetingChatMessage' -User 'DakotaS@x' -Status 'failed' -Target '' -Detail 'meeting thread unavailable'
  Set-SeedItem -Id 'meeting:1' -Component 'meeting' -User 'DakotaS@x' -Status 'seeded' -Target '' -Detail ''
  Set-SeedItem -Id 'meetingchat:1:0' -Component 'meetingChatMessage' -User 'DakotaS@x' -Status 'failed' -Target '' -Detail 'meeting thread unavailable'
  # ONE event GET serves meeting:0's re-derivation; meeting:1 has no eventId -> no GET.
  Set-IrmQueue @([pscustomobject]@{ onlineMeeting = [pscustomobject]@{ joinUrl = 'https://teams.microsoft.com/l/meetup-join/19%3ameeting_XYZ%40thread.v2/0' } })
  Invoke-UserTokenRecovery
  Assert-True ($script:sentMeetingChatThreads['meetingchat:0:0'] -eq '19:meeting_XYZ@thread.v2') 'thread re-derivation: chat replayed into the thread parsed from the calendar event'
  Assert-True ($Global:SeedLedger['meeting:0'].target -eq '19:meeting_XYZ@thread.v2') 'thread re-derivation: the meeting ledger Target is refreshed'
  Assert-True (-not (Test-SeedItemDone 'meetingchat:1:0')) 'thread re-derivation: no eventId -> items honestly stay missing'
  Assert-True ($script:irmCalls -eq 1 -and $script:sentMeetingChat.Count -eq 1) 'thread re-derivation: one event GET, one replayed chat'

  # ============================================================
  # 13. ADMIN TOKEN RENEWAL (GitHub Copilot adaptation, platform_patch.js): renewal runs
  #     five minutes before expiry, and a failed or revoked renewal keeps serving the
  #     cached token until it really expires - a short sign-in outage must never fail
  #     the tenant restore at the end of a run.
  # ============================================================
  Reset-Mocks
  $_astTok2 = $null; $_astErr2 = $null
  $seederAst = [System.Management.Automation.Language.Parser]::ParseInput($ps1Text, [ref]$_astTok2, [ref]$_astErr2)
  $tokDef = $seederAst.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-DemoIQAdminToken' }, $true) | Select-Object -First 1
  Assert-True ($null -ne $tokDef) 'Get-DemoIQAdminToken extracted from the generated seeder'
  Invoke-Expression $tokDef.Extent.Text
  function Set-AdminState([int]$RenewInSec, [int]$ExpireInSec) {
    $now = [datetime]::UtcNow
    $Global:DemoIQAdmin = @{ TenantId = 't'; ClientId = 'c'; Upn = 'a@x'; RefreshToken = 'rt-1'; AccessToken = 'cached'
      RenewAfter = $now.AddSeconds($RenewInSec); ExpiresAt = $now.AddSeconds($ExpireInSec); FailedAt = $null; WarnedAt = $null; Revoked = $false }
  }
  Set-AdminState 60 300; Set-IrmQueue @()
  Assert-True ((Get-DemoIQAdminToken) -eq 'cached' -and $script:irmCalls -eq 0) 'admin token: cached before the renewal window, no request'
  Set-AdminState -60 240; Set-IrmQueue @([pscustomobject]@{ access_token = 'fresh'; expires_in = 3600; refresh_token = 'rt-2' })
  Assert-True ((Get-DemoIQAdminToken) -eq 'fresh' -and $Global:DemoIQAdmin.RefreshToken -eq 'rt-2') 'admin token: renewed, refresh token rotated'
  Assert-True ($Global:DemoIQAdmin.RenewAfter -gt [datetime]::UtcNow.AddMinutes(50) -and $Global:DemoIQAdmin.ExpiresAt -gt $Global:DemoIQAdmin.RenewAfter) 'admin token: next renewal before the new expiry'
  Set-AdminState -60 240
  $netErr = New-MockHttpError -Message 'A connection attempt failed because the connected party did not properly respond'
  Set-IrmQueue @($netErr, $netErr, $netErr)
  Assert-True ((Get-DemoIQAdminToken) -eq 'cached' -and $script:irmCalls -eq 3) 'admin token: three failed renewals keep the unexpired cached token (no throw)'
  Set-IrmQueue @()
  Assert-True ((Get-DemoIQAdminToken) -eq 'cached' -and $script:irmCalls -eq 0) 'admin token: during the one-minute backoff the cached token is served without a request'
  Set-AdminState -60 240
  Set-IrmQueue @((New-MockHttpError -Status 400 -Json '{"error":"invalid_grant","error_description":"AADSTS70043: The refresh token has expired."}'))
  Assert-True ((Get-DemoIQAdminToken -Soft) -eq 'cached' -and $Global:DemoIQAdmin.Revoked -and $script:irmCalls -eq 1) 'admin token: invalid_grant stops retrying; the cached token is still served'
  $Global:DemoIQAdmin.ExpiresAt = [datetime]::UtcNow.AddSeconds(-1)
  Assert-True ($null -eq (Get-DemoIQAdminToken -Soft)) 'admin token: -Soft returns nothing once the cached token has expired'
  $threw = $false
  try { $null = Get-DemoIQAdminToken } catch { $threw = $_.Exception.Message -match 'admin_signin\.ps1' }
  Assert-True $threw 'admin token: a hard call then throws with the sign-in hint'
}
finally {
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host ("token-resilience: {0} passed, {1} failed" -f $script:pass, $script:fail) -ForegroundColor ($(if ($script:fail) { 'Red' } else { 'Green' }))
exit $(if ($script:fail) { 1 } else { 0 })

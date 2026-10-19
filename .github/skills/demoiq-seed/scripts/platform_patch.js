'use strict';
/*
 * platform_patch.js - adapt a DemoIQ-generated seeder (DemoIQ.ps1) to the GitHub Copilot
 * delegated model, for PowerShell 7 on Windows, macOS and Linux.
 *
 * generate.js applies this right after the engine's own generatePs1(); the engine
 * (engine/DemoIQ.html) stays byte-identical to the DemoIQ release it came from, so this
 * file is the whole seeder delta:
 *   - plain, owner-only config (no config password or decryption)
 *   - no certificate and no stored admin password: the administrator signs in for each
 *     run (scripts/admin_signin.ps1) and the seeder renews that delegated token itself
 *   - Microsoft Graph over REST (no Graph PowerShell modules to install or load)
 *   - demo users sign in as a public client (no client assertion)
 *   - consent comes from deploy.py, never from the seeder; no app-only tokens
 *   - no usage telemetry
 *
 * Every patch is anchored on exact upstream text. A REQUIRED patch whose anchor is
 * missing (or found more often than expected) throws PlatformPatchError - generate.js
 * then exits 10. After patching, FORBIDDEN lists what must never survive (a certificate,
 * an app-only token, an admin password sign-in, consent writes, telemetry); any hit also
 * throws. A DemoIQ engine update therefore can never silently produce a seeder outside
 * this model.
 */

const MARKER = '# demoiq-seed: delegated patch v2 applied (GitHub Copilot adaptation of DemoIQ - https://aka.ms/demoiq)';
const MARKER_TOKEN = 'demoiq-seed: delegated patch v2';

const fs = require('fs');
const path = require('path');

// The delegated cleanup engine (function library) inlined into every seeder; a cleanup
// run (DEMOIQ_RUN_KIND=cleanup) uses it between tenant preparation and restore.
const CLEANUP_ENGINE_FILE = path.join(__dirname, 'cleanup_engine.ps1');

class PlatformPatchError extends Error {}

// The reads of the admin token the upstream seeder caches once and reuses for the whole
// run. Each becomes a call that renews the delegated token shortly before it expires.
const STALE_ADMIN_TOKEN = /\$(?:Global:AdminToken|Global:AccessToken|DToken)\b(?!\s*=)/g;

const ADMIN_SESSION = String.raw`# -- Administrator session (GitHub Copilot adaptation of DemoIQ - https://aka.ms/demoiq) --
# No certificate and no stored admin password. The administrator signs in for each run
# (scripts/admin_signin.ps1); the launcher hands this process that sign-in through
# DEMOIQ_ADMIN_SESSION, which is cleared from the environment as soon as it is read.
# Microsoft Graph is called over REST with the administrator's delegated token, so no
# Graph PowerShell module is installed or loaded.
if ([string]::IsNullOrWhiteSpace($Global:AppId)) { throw 'App ID is missing from config. Fill it in the HTML UI and re-save.' }
$Global:AppId = $Global:AppId.Trim()
$Global:DemoIQAdmin = $null

function ConvertFrom-DemoIQJwtPayload {
  param([string]$Token)
  try {
    $_jp = ($Token -split '\.')[1].Replace('-', '+').Replace('_', '/')
    switch ($_jp.Length % 4) { 2 { $_jp += '==' } 3 { $_jp += '=' } }
    return ([System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($_jp)) | ConvertFrom-Json)
  } catch { return $null }
}

function Get-DemoIQAdminToken {
  # The administrator's delegated Graph token, renewed from the sign-in five minutes
  # before it expires. When renewal fails (or backs off for a minute after a failure),
  # the cached token keeps working until it really expires, so a short sign-in outage
  # never stops the tenant restore. -Soft returns $null (with at most one warning a
  # minute) instead of throwing once no unexpired token is left.
  param([switch]$Soft)
  $_a = $Global:DemoIQAdmin
  if ($_a -and $_a.AccessToken -and [datetime]::UtcNow -lt $_a.RenewAfter) { return $_a.AccessToken }
  $_why = 'no administrator sign-in was loaded'
  if ($_a -and $_a.Revoked) {
    $_why = 'the administrator sign-in expired or was revoked'
  } elseif ($_a -and $_a.FailedAt -and ([datetime]::UtcNow - $_a.FailedAt).TotalSeconds -lt 60) {
    $_why = 'the sign-in service failed less than a minute ago'
  } elseif ($_a) {
    foreach ($_wait in 0, 5, 15) {
      if ($_wait) { Start-Sleep -Seconds $_wait }
      try {
        $_r = Invoke-RestMethod -Method POST -Uri ('https://login.microsoftonline.com/' + $_a.TenantId + '/oauth2/v2.0/token') -ContentType 'application/x-www-form-urlencoded' -TimeoutSec 60 -ErrorAction Stop -Body @{
          grant_type = 'refresh_token'; client_id = $_a.ClientId; refresh_token = $_a.RefreshToken; scope = 'https://graph.microsoft.com/.default offline_access'
        }
        $_now = [datetime]::UtcNow
        $_a.AccessToken = [string]$_r.access_token
        $_a.ExpiresAt = $_now.AddSeconds([int]$_r.expires_in - 60)
        $_a.RenewAfter = $_now.AddSeconds([Math]::Max(60, [int]$_r.expires_in - 300))
        if ($_r.refresh_token) { $_a.RefreshToken = [string]$_r.refresh_token }
        $_a.FailedAt = $null
        return $_a.AccessToken
      } catch {
        $_err = $null; try { $_err = $_.ErrorDetails.Message | ConvertFrom-Json } catch {}
        $_why = if ($_err -and $_err.error_description) { (($_err.error_description -split "\r?\n")[0]).Trim() } else { $_.Exception.Message }
        if ($_err -and ($_err.error -in @('invalid_grant', 'interaction_required', 'invalid_client', 'unauthorized_client'))) { $_a.Revoked = $true; break }
      }
    }
    $_a.FailedAt = [datetime]::UtcNow
  }
  if ($_a -and $_a.AccessToken -and [datetime]::UtcNow -lt $_a.ExpiresAt) { return $_a.AccessToken }
  $_msg = "Could not renew the administrator's Graph token ($_why). Sign in again with scripts/admin_signin.ps1, then re-launch."
  if ($Soft) {
    if (-not $_a -or -not $_a.WarnedAt -or ([datetime]::UtcNow - $_a.WarnedAt).TotalSeconds -ge 60) {
      Write-Host "[AUTH] WARN: $_msg" -ForegroundColor DarkYellow
      if ($_a) { $_a.WarnedAt = [datetime]::UtcNow }
    }
    return $null
  }
  throw $_msg
}

function Invoke-MgGraphRequest {
  # Microsoft Graph over REST with the administrator's delegated token. Stands in for the
  # Graph PowerShell SDK cmdlet the upstream seeder calls: JSON bodies, relative URIs,
  # 429/503/504 retries and -ErrorAction behave the same; only graph.microsoft.com is called.
  [CmdletBinding()]
  param([string]$Method = 'GET', [Parameter(Mandatory)][string]$Uri, [object]$Body, [string]$ContentType = 'application/json', [string]$OutputType)
  $_uri = if ($Uri -match '^https://') { $Uri } else { 'https://graph.microsoft.com/' + $Uri.TrimStart('/') }
  if (([uri]$_uri).Host -ne 'graph.microsoft.com') { throw "Invoke-MgGraphRequest only calls Microsoft Graph, not $(([uri]$_uri).Host)." }
  $_req = @{ Method = $Method; Uri = $_uri; TimeoutSec = 120 }
  if ($PSBoundParameters.ContainsKey('Body') -and $null -ne $Body) {
    $_text = if ($Body -is [string]) { $Body } else { $Body | ConvertTo-Json -Depth 20 -Compress }
    $_req.Body = [System.Text.Encoding]::UTF8.GetBytes($_text)
    $_req.ContentType = $ContentType
  }
  for ($_try = 1; ; $_try++) {
    $_req.Headers = @{ Authorization = 'Bearer ' + (Get-DemoIQAdminToken) }
    try { return Invoke-RestMethod @_req -ErrorAction Stop }
    catch {
      $_code = 0; try { $_code = [int]$_.Exception.Response.StatusCode } catch {}
      if (($_code -in @(429, 503, 504)) -and ($_try -lt 5)) {
        $_pause = [Math]::Min(60, 2 * $_try)
        try { $_ra = $_.Exception.Response.Headers.RetryAfter.Delta; if ($_ra) { $_pause = [Math]::Min(60, [Math]::Max(1, [int]$_ra.TotalSeconds)) } } catch {}
        Start-Sleep -Seconds $_pause
        continue
      }
      if ($ErrorActionPreference -in @('SilentlyContinue', 'Ignore')) { return }
      $PSCmdlet.ThrowTerminatingError($_)
    }
  }
}

function Get-MgContext {
  [CmdletBinding()] param()
  $_c = ConvertFrom-DemoIQJwtPayload (Get-DemoIQAdminToken)
  if (-not $_c) { return $null }
  [pscustomobject]@{
    TenantId = [string]$_c.tid
    Account = [string]$(if ($_c.upn) { $_c.upn } else { $_c.preferred_username })
    ClientId = [string]$(if ($_c.appid) { $_c.appid } else { $_c.azp })
    Scopes = @(([string]$_c.scp) -split ' ' | Where-Object { $_ })
    AuthType = 'Delegated'
  }
}

function Disconnect-MgGraph { [CmdletBinding()] param() }

function Initialize-DemoIQAdminSession {
  $_raw = $env:DEMOIQ_ADMIN_SESSION
  $env:DEMOIQ_ADMIN_SESSION = $null
  $_fix = 'Sign in with scripts/admin_signin.ps1, then launch with scripts/launch_seeder.ps1.'
  $_s = $null
  if (-not [string]::IsNullOrWhiteSpace($_raw)) { try { $_s = $_raw | ConvertFrom-Json } catch {} }
  $_raw = $null
  $_problem = $null
  if (-not $_s) { $_problem = 'no administrator sign-in was handed to this run' }
  elseif (@('refreshToken', 'tenantId', 'clientId', 'upn' | Where-Object { [string]::IsNullOrWhiteSpace([string]$_s.$_) }).Count) { $_problem = 'the administrator sign-in handed to this run is incomplete' }
  elseif ([string]$_s.clientId -ne $Global:AppId) { $_problem = "the administrator signed in to app $($_s.clientId), but this config uses app $Global:AppId" }
  elseif ([string]$_s.upn -ne [string]$AdminUPN) { $_problem = "the administrator signed in as $($_s.upn), but this config names $AdminUPN" }
  if ($_problem) {
    $env:DEMOIQ_ABORT_REASON = 'admin-session: ' + $_problem + '; nothing was seeded'
    throw "Cannot start: $_problem. $_fix"
  }
  $Global:DemoIQAdmin = @{
    TenantId = [string]$_s.tenantId; ClientId = [string]$_s.clientId; Upn = [string]$_s.upn
    RefreshToken = [string]$_s.refreshToken; AccessToken = $null; RenewAfter = [datetime]::MinValue
    ExpiresAt = [datetime]::MinValue; FailedAt = $null; WarnedAt = $null; Revoked = $false
  }
  $_s = $null
  try { $_claims = ConvertFrom-DemoIQJwtPayload (Get-DemoIQAdminToken) }
  catch { $env:DEMOIQ_ABORT_REASON = 'admin-session: ' + $_.Exception.Message + ' Nothing was seeded.'; throw }
  $_tokenApp = if ($_claims.appid) { [string]$_claims.appid } else { [string]$_claims.azp }
  $_tokenUpn = if ($_claims.upn) { [string]$_claims.upn } else { [string]$_claims.preferred_username }
  if (-not $_claims -or [string]$_claims.tid -ne $Global:DemoIQAdmin.TenantId -or $_tokenApp -ne $Global:AppId -or $_tokenUpn -ne [string]$AdminUPN) {
    $env:DEMOIQ_ABORT_REASON = 'admin-session: the administrator token does not match this tenant, app and admin; nothing was seeded'
    throw "The administrator token does not match tenant $($Global:DemoIQAdmin.TenantId), app $Global:AppId and admin $AdminUPN. $_fix"
  }
  Write-Host "[AUTH] OK: delegated administrator session for $($Global:DemoIQAdmin.Upn) (tenant $($Global:DemoIQAdmin.TenantId)); no certificate or stored admin password." -ForegroundColor Green
}

Initialize-DemoIQAdminSession
`;

const CONSENT_CHECK = String.raw`# GitHub Copilot adaptation of DemoIQ: administrator consent comes from deploy.py
# (--grant-demoiq-consent), never from this script, and the app is delegated-only.
Write-Host ''; Write-Host '[CONSENT] Checking the administrator token for the consented delegated scopes...' -ForegroundColor Cyan
$_consentHave = @((Get-MgContext).Scopes)
$_consentMissing = @('Policy.Read.All', 'Policy.ReadWrite.ConditionalAccess', 'Policy.ReadWrite.AuthenticationMethod', 'User.Read.All',
  'Group.ReadWrite.All', 'Chat.Create', 'Files.ReadWrite.All', 'Sites.Read.All', 'Mail.Read', 'People.Read.All',
  'OnlineMeetingTranscript.Read.All', 'Chat.Read', 'ChannelMessage.Read.All', 'ExternalItem.Read.All') | Where-Object { $_consentHave -notcontains $_ }
if ($_consentMissing) {
  Log-Issue -Severity 'WARN' -Category 'CONSENT' -Message ('The administrator token lacks ' + ($_consentMissing -join ', ') + '. An administrator reruns deploy.py up --with-demoiq --through demoiq --grant-demoiq-consent, then signs in again.')
} else {
  Write-Host ' [CONSENT] OK: the delegated scopes are consented.' -ForegroundColor DarkGreen
}
`;

const ADMIN_SIGN_IN = String.raw`# -- STEP 4a: Administrator token (GitHub Copilot adaptation: the per-run sign-in, renewed automatically) --
# Every later read of the upstream admin-token variables calls Get-DemoIQAdminToken instead.
Write-Host ''; Write-Host "[AUTH] Using the administrator sign-in for: $AdminUPN" -ForegroundColor Cyan
if (Get-DemoIQAdminToken -Soft) { Write-Host '[AUTH] OK: administrator token ready (renewed automatically for the rest of the run)' -ForegroundColor Green }
`;

// --- Cleanup run (inserted by applyCleanupMode) ---------------------------------------
const CLEANUP_OPEN = String.raw`# -- Cleanup run (GitHub Copilot adaptation of DemoIQ - https://aka.ms/demoiq) --
# With DEMOIQ_RUN_KIND=cleanup the run prepares the tenant exactly as a seed does (steps 1-3:
# demo users excluded from Conditional Access, their per-user MFA and Security Defaults
# relaxed), skips content generation and seeding (steps 4-13), deletes the seeded content
# with delegated tokens - the Teams team and chats as the administrator; meetings, files and
# mail as each demo user - and then restores the tenant (step 14) and stops.
if ($env:DEMOIQ_RUN_KIND -ne 'cleanup') {
`;

const CLEANUP_DELETE = String.raw`}
if ($env:DEMOIQ_RUN_KIND -eq 'cleanup') {
  Update-Progress -Step 4 -Status 'Cleanup: deleting the seeded content' -Detail 'Teams team and chats as the administrator; meetings, files and mail as each demo user.'
  $_cuScope = if ($env:DEMOIQ_CLEANUP_SCOPE -eq 'AllRuns') { 'AllRuns' } else { 'LastRun' }
  $_cuResult = [ordered]@{
    schema = 'demoiq-cleanup-result/1'; ok = $false; scope = $_cuScope; manifestSource = $null; planSource = $null
    runsTargeted = 0; plan = $null; results = $null; manifestPruned = $false; localState = $null
    failedTotal = 0; tenantRestored = $false; error = $null; written = $null
  }
  Write-Host ''; Write-Host "[PURGE] Cleanup run ($_cuScope): reading the seed manifest..." -ForegroundColor Cyan
  try {
    # The same sources the dry-run preview (cleanup_tenant.ps1) reads: the seed run folders'
    # manifests, then their logs - plus the administrator's OneDrive copy, which needs the sign-in.
    $_cuSeedRuns = @()
    if ($env:DEMOIQ_CLEANUP_WORKSPACE) { $_cuSeedRuns = @(Get-DemoIQCleanupSeedRunFolders -Workspace $env:DEMOIQ_CLEANUP_WORKSPACE | ForEach-Object { $_.FullName }) }
    $_cuSearch = if ($_cuSeedRuns.Count) { $_cuSeedRuns } else { @([string]$env:DEMOIQ_CLEANUP_SEARCH -split [System.IO.Path]::PathSeparator | Where-Object { $_ }) }
    $_cuLogs = @($_cuSeedRuns | ForEach-Object { Join-Path $_ 'logs' } | Where-Object { Test-Path -LiteralPath $_ -PathType Container })
    $_cuManifest = Read-DemoIQCleanupManifest -Scope $_cuScope -CleanupConfigPath $env:DEMOIQ_CLEANUP_MANIFEST -SearchFolders $_cuSearch -LogsFolder $_cuLogs -AdminToken { Get-DemoIQAdminToken }
    if (-not $_cuManifest) { throw 'No seed manifest (cleanup.config) and no run logs were found: there is nothing to clean up.' }
    $_cuPlan = New-DemoIQCleanupPlan -Manifest $_cuManifest.Manifest -Scope $_cuScope -AdminUpn $AdminUPN -KnownUsers $AllUsers
    $_cuResult.manifestSource = $_cuManifest.Source; $_cuResult.planSource = $_cuManifest.PlanSource
    $_cuResult.runsTargeted = @($_cuPlan.TargetRuns).Count; $_cuResult.plan = $_cuPlan.Counts
    $_cuResult.results = Invoke-DemoIQCleanup -PlanInfo $_cuPlan -AdminUpn $AdminUPN -GetToken {
      param([string]$Upn)
      if ($Upn -eq $AdminUPN) { return (Get-DemoIQAdminToken -Soft) }
      $_cuTok = Get-DemoIQCleanupUserToken -TenantId $Global:TenantId -ClientId $Global:AppId -Upn $Upn -Password ([string]$Global:UserPasswords[$Upn])
      if ($_cuTok.ok) { return $_cuTok.token }
      Write-Host "[PURGE] WARN: no token for $Upn - $($_cuTok.error)" -ForegroundColor DarkYellow
      return $null
    }
    $_cuPrune = Update-DemoIQCleanupManifest -ManifestInfo $_cuManifest -PlanInfo $_cuPlan -Scope $_cuScope -AdminToken { Get-DemoIQAdminToken }
    $_cuResult.manifestPruned = [bool]$_cuPrune.pruned
    if ($env:DEMOIQ_CLEANUP_WORKSPACE) {
      $_cuResult.localState = Clear-DemoIQCleanupLocalState -Workspace $env:DEMOIQ_CLEANUP_WORKSPACE -ManifestInfo $_cuManifest -PlanInfo $_cuPlan -ArchiveLocalDeliverables:($env:DEMOIQ_CLEANUP_ARCHIVE -eq '1')
    }
    $_cuResult.ok = $true
  } catch {
    $_cuResult.error = [string]$_.Exception.Message
    Log-Issue -Severity 'FAIL' -Category 'PURGE' -Message $_cuResult.error
  }
}

`;

const CLEANUP_FINISH = String.raw`
if ($env:DEMOIQ_RUN_KIND -eq 'cleanup') {
  $_cuResult.tenantRestored = [bool]$_tsAllRestored
  if ($_cuResult.results) {
    $_cuR = $_cuResult.results
    $_cuResult.failedTotal = [int]$_cuR.groupsFailed + [int]$_cuR.meetingsFailed + [int]$_cuR.filesFailed + [int]$_cuR.emailsFailed +
      [int]$_cuR.chatsFailed + [int]$_cuR.chatsNotFound + @($_cuR.usersWithoutToken | Where-Object { $_ }).Count
  }
  $_cuResult.written = (Get-Date).ToString('o')
  try { ($_cuResult | ConvertTo-Json -Depth 8) | Set-Content -LiteralPath (Join-Path $OutputDir 'cleanup_result.json') -Encoding UTF8 } catch {}
  Write-Host ''
  if ($_cuResult.ok) {
    Write-Host ('[PURGE] DONE: ' + $_cuResult.runsTargeted + ' run(s) targeted; ' + $_cuResult.failedTotal + ' item(s) not removed - see DemoIQ-Output/cleanup_result.json.') -ForegroundColor Green
  } else {
    Write-Host "[PURGE] FAILED: $($_cuResult.error)" -ForegroundColor Red
  }
  Stop-RunLogging
  if (-not $_cuResult.ok) { $env:DEMOIQ_ABORT_REASON = 'cleanup-failed: ' + $_cuResult.error; exit 1 }
  return
}
`;

const CLEANUP_NO_MODE_MENU = "if ($env:DEMOIQ_RUN_KIND -eq 'cleanup') { $_fastMode = $true; $_modeConfirmed = $true; Write-Host '  >> Cleanup run: no run mode or history needed.' -ForegroundColor Cyan }\n";

const STEP4_ANCHOR = '# ============================================================\n# STEP 4: COPILOT CONTENT GENERATION\n';
const RESTORE_ANCHOR = '# ============================================================\n# CLEANUP: RESTORE TENANT SETTINGS\n';
const RESTORE_END = '\nDisconnect-MgGraph | Out-Null\n';
const MODE_MENU_ANCHOR = '$_modeConfirmed = $false\n\nwhile (-not $_modeConfirmed) {\n';

const PATCHES = [
  {
    id: 'plain-config',
    required: true,
    why: 'Configs are plain, owner-only JSON: no config password prompt and no decryption.',
    find: /# -- Read and decrypt config file --\n[\s\S]*?\n  Write-Host "\[CONFIG\] Most likely: wrong password\." -ForegroundColor DarkYellow\n  Stop-RunLogging\n  exit 1\n\}\n/g,
    replace: String.raw`# -- Read config file (GitHub Copilot adaptation of DemoIQ: plain, owner-only JSON) --
Write-Host "[CONFIG] Reading $ConfigFile..." -ForegroundColor Cyan
$_cfg = $null
try { $_cfg = Get-Content -LiteralPath $ConfigFile -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop } catch {
  Write-Host "[CONFIG] FAIL: $ConfigFile is not a readable JSON config: $($_.Exception.Message)" -ForegroundColor Red
  $env:DEMOIQ_ABORT_REASON = 'config-unreadable: nothing was seeded'
  Stop-RunLogging
  exit 1
}
if (-not $_cfg -or $_cfg.v -eq 'cdx-enc-v1') {
  Write-Host '[CONFIG] FAIL: this config is encrypted or empty. The demoiq-seed skill uses plain, owner-only configs: convert or re-save it with the skill, then re-launch.' -ForegroundColor Red
  $env:DEMOIQ_ABORT_REASON = 'config-encrypted: convert it with the demoiq-seed skill; nothing was seeded'
  Stop-RunLogging
  exit 1
}
`,
    min: 1, max: 1,
    verify: (removed) => removed.indexOf("Read-Host 'Config password'") >= 0 && removed.indexOf('cdx-enc-v1') >= 0,
  },
  {
    id: 'no-config-decryption-helper',
    required: false,
    why: 'The embedded AES-GCM helper only decrypted configs; skip compiling it.',
    find: /# -- Load AES-GCM decryption helper[^\n]*\n\$_csB64 = '[A-Za-z0-9+/=]*'\ntry \{\n  Add-Type -TypeDefinition \(\[System\.Text\.Encoding\]::UTF8\.GetString\(\[System\.Convert\]::FromBase64String\(\$_csB64\)\)\)\n\} catch \{\n  if \(\$_\.Exception\.Message -notmatch 'already exists'\) \{ throw \}\n\}\n/g,
    replace: '# (The config decryption helper is removed: the GitHub Copilot adaptation uses plain configs.)\n',
    min: 1, max: 1,
  },
  {
    id: 'password-bstr',
    required: false,
    why: 'PtrToStringAuto decodes a BSTR as UTF-8 on macOS/Linux and returns only the first character of a typed password.',
    find: '[System.Runtime.InteropServices.Marshal]::PtrToStringAuto([System.Runtime.InteropServices.Marshal]::SecureStringToBSTR(',
    replace: '[System.Runtime.InteropServices.Marshal]::PtrToStringBSTR([System.Runtime.InteropServices.Marshal]::SecureStringToBSTR(',
    min: 1, max: 5,
  },
  {
    id: 'no-users-unattended',
    required: false,
    why: 'Unattended runs cannot answer the demo-user prompts; stop with the reason instead.',
    find: '# If no demo users were configured in the tool, prompt now\nif($AllUsers.Count -eq 0){\n',
    replace: "# If no demo users were configured in the tool, prompt now\n" +
      "if($AllUsers.Count -eq 0 -and $env:DEMOIQ_LAUNCHER){ $env:DEMOIQ_ABORT_REASON = 'no-demo-users: the config names no demo users; nothing was seeded'; throw 'The config names no demo users. Add them with the Config Builder, then re-launch.' }\n" +
      'if($AllUsers.Count -eq 0){\n',
    min: 1, max: 1,
  },
  {
    id: 'admin-session',
    required: true,
    why: 'Replace the certificate lookup and client-assertion signer with the per-run delegated administrator session and the Graph REST helpers.',
    find: /# -- Certificate lookup --\n[\s\S]*?\nfunction New-ClientAssertionJwt \{\n[\s\S]*?\n  return "\$unsigned\.\$sigB64"\n\}\n/g,
    replace: ADMIN_SESSION,
    min: 1, max: 1,
    verify: (removed) => removed.indexOf('Cert:\\CurrentUser\\My') >= 0 && removed.indexOf('[CERT] OK') >= 0,
  },
  {
    id: 'history-admin-token',
    required: true,
    why: "Read the previous run's cleanup.config from the administrator's OneDrive with the delegated token, not an app-only one.",
    find: /        # Acquire app-only token using client credentials \(certificate assertion\)\n[\s\S]*?\n        \$_odToken = \$_odTokResp\.access_token\n/g,
    replace: "        # GitHub Copilot adaptation: the administrator's own delegated token reads their OneDrive.\n" +
      '        $_odToken = Get-DemoIQAdminToken\n',
    min: 1, max: 1,
    verify: (removed) => removed.indexOf('client_credentials') >= 0 && removed.length < 2000,
  },
  {
    id: 'step-1-comment',
    required: false,
    why: 'Describe step 1 as the delegated sign-in it now is.',
    find: /# STEP 1: CONNECT TO MICROSOFT GRAPH \(Silent, Certificate-Based\)\n# =+\n(?:#[^\n]*\n)*?# =+\n/g,
    replace: '# STEP 1: CONNECT TO MICROSOFT GRAPH (delegated administrator sign-in)\n' +
      '# ============================================================\n' +
      "# GitHub Copilot adaptation of DemoIQ: Graph calls use the administrator's per-run\n" +
      '# delegated sign-in (scripts/admin_signin.ps1). The app has delegated permissions\n' +
      '# only, so tenant preparation (CA policies, groups) runs as that administrator.\n' +
      '# ============================================================\n',
    min: 1, max: 1,
  },
  {
    id: 'graph-connect',
    required: true,
    why: 'Connect to Microsoft Graph with the delegated session instead of installing, loading and certificate-connecting the Graph PowerShell SDK.',
    find: /(Update-Progress -Step 1 -Status '[^'\n]*') -Detail '[^'\n]*'\nWrite-Host '\[SETUP\] Connecting to Graph[^\n]*\n[\s\S]*?\n  throw\n\}\n(?=\n\$ctx=Get-MgContext\n)/g,
    replace: (match, progress) => progress + " -Detail 'Delegated administrator sign-in; verifying the tenant.'\n" +
      "Write-Host '[SETUP] Connecting to Graph (delegated administrator sign-in; no Graph PowerShell modules)...' -ForegroundColor Cyan\n" +
      "$null = Invoke-MgGraphRequest -Method GET -Uri 'https://graph.microsoft.com/v1.0/organization' -ErrorAction Stop\n",
    min: 1, max: 1,
    verify: (removed) => removed.indexOf('Connect-MgGraph') >= 0 && removed.indexOf('Install-Module') >= 0,
  },
  {
    id: 'admin-onedrive-provisioning',
    required: true,
    why: "Trigger the administrator's OneDrive provisioning with the delegated token, not an app-only one.",
    find: /  \$_provJwt = New-ClientAssertionJwt [^\n]*\n  \$_provTok = \(Invoke-RestMethod -Method POST `\n[\s\S]*?-ErrorAction Stop\)\.access_token\n/g,
    replace: '  $_provTok = Get-DemoIQAdminToken\n',
    min: 1, max: 1,
    verify: (removed) => removed.indexOf('client_credentials') >= 0 && removed.length < 1000,
  },
  {
    id: 'admin-keeps-mfa',
    required: false,
    why: 'The administrator signs in interactively, so only demo users join the temporary MFA/CA exclusion group.',
    find: '  if ($AdminUPN -and $AdminUPN -notin $caUsers) { $caUsers = @($AdminUPN) + $caUsers }\n',
    replace: '  # GitHub Copilot adaptation: the administrator signs in interactively (with MFA) and is never excluded.\n',
    min: 1, max: 1,
  },
  {
    id: 'consent-by-runner',
    required: true,
    why: 'Consent is granted by deploy.py; the delegated-only app cannot (and must not) write permission grants.',
    find: /Write-Host ''; Write-Host '\[CONSENT\] Granting delegated consent for app\.\.\.' -ForegroundColor Cyan\ntry \{\n[\s\S]*?\n  Write-Host " \[CONSENT\] WARN: You may need to re-run app_reg_cert_setup\.ps1 if ROPC fails below\." -ForegroundColor DarkYellow\n\}\n/g,
    replace: CONSENT_CHECK,
    min: 1, max: 1,
    verify: (removed) => removed.indexOf('oauth2PermissionGrants') >= 0,
  },
  {
    id: 'admin-sign-in',
    required: true,
    why: "Use the administrator's per-run sign-in instead of a password (ROPC) sign-in.",
    find: /# -- STEP 4a: Admin ROPC token \(for Copilot API access\) --\n[\s\S]*?\n\}\n(?=\n# -- Fail fast on a dead admin token)/g,
    replace: ADMIN_SIGN_IN,
    min: 1, max: 1,
    verify: (removed) => removed.indexOf("grant_type='password'") >= 0 && removed.indexOf('username=$AdminUPN') >= 0,
  },
  {
    id: 'admin-token-fail-fast-title',
    required: false,
    why: 'Describe the in-run admin token guard in delegated terms.',
    find: 'the admin ROPC token could not be acquired',
    replace: 'the administrator token could not be renewed',
    min: 1, max: 1,
  },
  {
    id: 'admin-token-fail-fast-fix',
    required: false,
    why: 'The fix for a dead admin token is a new sign-in, not a password.',
    find: '  Fix: correct the admin password in the config (Config Builder > Save & Encrypt) and re-run, or disable chats/AoP for a run that does not need the admin token.',
    replace: '  Fix: sign in again with scripts/admin_signin.ps1 and re-launch, or disable chats/AoP for a run that does not need the admin token.',
    min: 1, max: 1,
  },
  {
    id: 'admin-token-fail-fast-reason',
    required: false,
    why: 'Report the delegated failure reason to the launcher heartbeat.',
    find: 'admin-ropc-failed: wrong admin password or token endpoint unreachable; nothing was seeded',
    replace: 'admin-session-failed: the administrator sign-in expired or the sign-in service was unreachable; nothing was seeded',
    min: 1, max: 1,
  },
  {
    id: 'public-client-sign-in',
    required: true,
    why: 'Demo users sign in as a public client: there is no certificate to sign a client assertion.',
    find: /client_assertion_type='urn:ietf:params:oauth:client-assertion-type:jwt-bearer';client_assertion=\(New-ClientAssertionJwt -ClientId \$Global:AppId -TenantId \$(?:Global:)?TenantId -Certificate \$Global:Cert\);/g,
    replace: '',
    min: 1, max: 10,
  },
  {
    id: 'no-app-only-token',
    required: true,
    why: 'The app holds delegated permissions only, so there is no app-only upload fallback.',
    find: /function Get-AppOnlyGraphToken \{\n[\s\S]*?\n\}\n(?=\nfunction Resolve-FileUploadAuth)/g,
    replace: 'function Get-AppOnlyGraphToken {\n' +
      '  # GitHub Copilot adaptation of DemoIQ: the app is delegated-only, so there is no app-only\n' +
      '  # upload fallback; a file whose owner has no token is skipped and reported.\n' +
      '  return $null\n' +
      '}\n',
    min: 1, max: 1,
    verify: (removed) => removed.indexOf('client_credentials') >= 0,
  },
  {
    id: 'fresh-admin-token',
    required: true,
    why: 'Delegated tokens last about an hour; renew the admin token wherever the run reads it instead of reusing the first one.',
    find: STALE_ADMIN_TOKEN,
    replace: '$(Get-DemoIQAdminToken -Soft)',
    min: 1, max: 200,
  },
  {
    id: 'cert-store-location-label',
    required: true,
    why: 'Record that this run used no certificate store.',
    find: /(\$_tenantCfgItems\['certStoreLocation'\]\s*=\s*)'Cert:\\CurrentUser\\My'/g,
    replace: (match, assignment) => assignment + "'none (delegated sign-in)'",
    min: 1, max: 1,
  },
  {
    id: 'consent-hint-header',
    required: false,
    why: 'Point the header troubleshooting note at the consent route this adaptation uses.',
    find: '#   AADSTS65001  -> Scopes not consented. Re-run the app_reg_cert_setup.ps1 script.\n',
    replace: '#   AADSTS65001  -> Scopes not consented. An administrator reruns deploy.py with --grant-demoiq-consent.\n',
    min: 1, max: 1,
  },
  {
    id: 'per-user-mfa-inherit',
    required: true,
    why: 'A resumed run must restore per-user MFA that an unfinished earlier pass of the same folder turned off: those users now read "disabled", so the engine alone would never record them.',
    find: "$Global:PerUserMfaOriginal = @{}\nWrite-Host ''; Write-Host '[MFA] Checking per-user MFA for demo users...' -ForegroundColor Cyan\n",
    replace: String.raw`$Global:PerUserMfaOriginal = @{}
# GitHub Copilot adaptation of DemoIQ: inherit the users an unfinished earlier pass of this
# folder changed (tenant_state.json perUserMfa.users), so this run's restore puts them back.
try {
  $_tsPrevMfaPath = Join-Path $OutputDir 'tenant_state.json'
  if (Test-Path -LiteralPath $_tsPrevMfaPath) {
    $_tsPrevMfa = Get-Content -LiteralPath $_tsPrevMfaPath -Raw -ErrorAction Stop | ConvertFrom-Json
    if ($_tsPrevMfa -and (-not $_tsPrevMfa.restored) -and $_tsPrevMfa.perUserMfa -and $_tsPrevMfa.perUserMfa.users) {
      foreach ($_pu in @($_tsPrevMfa.perUserMfa.users)) {
        if ($_pu.upn -and $_pu.uid -and $_pu.state -and $_pu.restored -ne $true) { $Global:PerUserMfaOriginal[[string]$_pu.upn] = @{ uid = [string]$_pu.uid; state = [string]$_pu.state } }
      }
      if ($Global:PerUserMfaOriginal.Count -gt 0) { Write-Host " [MFA] Prior unfinished pass in this folder changed per-user MFA for $($Global:PerUserMfaOriginal.Count) user(s) - inheriting them for restore at cleanup." -ForegroundColor DarkYellow }
    }
  }
} catch {}
Write-Host ''; Write-Host '[MFA] Checking per-user MFA for demo users...' -ForegroundColor Cyan
`,
    min: 1, max: 1,
  },
  {
    id: 'per-user-mfa-record-pending',
    required: true,
    why: 'Record which users the run changed and their original per-user MFA state, so a resumed run or a manual restore knows exactly what to put back.',
    find: '    perUserMfa = [ordered]@{ disabledForRun = [int]$Global:PerUserMfaOriginal.Count; restored = 0; failures = @() }\n',
    replace: '    perUserMfa = [ordered]@{ disabledForRun = [int]$Global:PerUserMfaOriginal.Count; restored = 0; failures = @()\n' +
      '      users = @(if ($Global:PerUserMfaOriginal) { $Global:PerUserMfaOriginal.GetEnumerator() | ForEach-Object { [ordered]@{ upn = [string]$_.Key; uid = [string]$_.Value.uid; state = [string]$_.Value.state } } }) }\n',
    min: 1, max: 1,
  },
  {
    id: 'per-user-mfa-record-final',
    required: true,
    why: 'Keep the per-user record after the restore, marking each user restored or not, so an incomplete restore can be finished from the file.',
    find: '    perUserMfa = [ordered]@{ disabledForRun = [int]$_tsMfaChanged; restored = [int]$_tsMfaRestored; failures = @($_tsMfaFailures) }\n',
    replace: '    perUserMfa = [ordered]@{ disabledForRun = [int]$_tsMfaChanged; restored = [int]$_tsMfaRestored; failures = @($_tsMfaFailures)\n' +
      "      users = @(if ($Global:PerUserMfaOriginal) { $Global:PerUserMfaOriginal.GetEnumerator() | ForEach-Object { $_pmKey = [string]$_.Key; $_pmVal = $_.Value; $_pmFailed = @(@($_tsMfaFailures) | Where-Object { ([string]$_).StartsWith($_pmKey + ': ') }).Count -gt 0; [ordered]@{ upn = $_pmKey; uid = [string]$_pmVal.uid; state = [string]$_pmVal.state; restored = (-not $_pmFailed) } } }) }\n",
    min: 1, max: 1,
  },
  {
    id: 'remove-usage-telemetry',
    required: true,
    why: 'The GitHub Copilot adaptation ships no telemetry: drop the seed_summary writer call.',
    find: /# \u2500\u2500 Deterministic seed telemetry \u2500\u2500\r?\n[\s\S]*?\r?\n\} catch \{\}\r?\n/g,
    replace: '# (DemoIQ usage telemetry is removed in the demoiq-seed GitHub Copilot adaptation.)\n',
    min: 1, max: 1,
    verify: (removed) => removed.indexOf('DEMOIQ_TELEMETRY_LOGGER') >= 0 && removed.indexOf('log_event') >= 0,
  },
];

// What must never survive in a patched seeder, whatever the engine version.
const FORBIDDEN = [
  { id: 'certificate-assertion', re: /New-ClientAssertionJwt|client_assertion/ },
  { id: 'app-only-token', re: /client_credentials/ },
  { id: 'graph-powershell-module', re: /Connect-MgGraph|Install-Module|Import-Module\s+Microsoft\.Graph/ },
  { id: 'certificate-store', re: /Get-ChildItem\s+Cert:|\$Global:Cert(?![A-Za-z0-9_])/ },
  { id: 'config-password', re: /Read-Host\s+'Config password'/ },
  { id: 'admin-password-sign-in', re: /username=\$AdminUPN/ },
  { id: 'consent-writes', re: /oauth2PermissionGrants/ },
  { id: 'stale-admin-token', re: new RegExp(STALE_ADMIN_TOKEN.source) },
  { id: 'usage-telemetry', re: /DEMOIQ_TELEMETRY_LOGGER/ },
];

function countOccurrences(text, find) {
  if (typeof find === 'string') {
    let n = 0, i = 0;
    while ((i = text.indexOf(find, i)) >= 0) { n++; i += find.length; }
    return { count: n, matches: n ? [find] : [] };
  }
  const matches = text.match(globalRegExp(find)) || [];
  return { count: matches.length, matches };
}

function globalRegExp(re) {
  return new RegExp(re.source, re.flags.includes('g') ? re.flags : re.flags + 'g');
}

function applyOne(text, p) {
  if (typeof p.find === 'string') return text.split(p.find).join(p.replace);
  // A function replacement keeps PowerShell text such as $' or $& literal.
  const replacer = typeof p.replace === 'function' ? p.replace : () => p.replace;
  return text.replace(globalRegExp(p.find), replacer);
}

function findForbidden(ps1) {
  return FORBIDDEN.filter(f => f.re.test(ps1)).map(f => f.id);
}

function countText(text, needle) { return text.split(needle).length - 1; }

// Cleanup runs reuse the seeder's own tenant preparation and restore: steps 4-13 are
// wrapped in "if not a cleanup run", the delegated cleanup engine runs right before the
// unchanged restore region, and a cleanup run stops right after it.
function applyCleanupMode(text) {
  const anchors = [['STEP 4', STEP4_ANCHOR], ['restore', RESTORE_ANCHOR]];
  for (const [label, anchor] of anchors) {
    const n = countText(text, anchor);
    if (n !== 1) throw new PlatformPatchError(`cleanup mode: the ${label} anchor occurs ${n} times (expected 1). The DemoIQ engine changed: update scripts/platform_patch.js.`);
  }
  const step4 = text.indexOf(STEP4_ANCHOR);
  const restore = text.indexOf(RESTORE_ANCHOR);
  const restoreEnd = text.indexOf(RESTORE_END, restore);
  const region = restoreEnd > 0 ? text.slice(restore, restoreEnd) : '';
  if (!(step4 < restore) || restoreEnd < 0 || region.indexOf('$Global:TenantRestored = $true') < 0 ||
      region.indexOf('tenant_state.json') < 0 || region.indexOf('MFA REINSTATED') < 0) {
    throw new PlatformPatchError('cleanup mode: the tenant restore region no longer matches. The DemoIQ engine changed: update scripts/platform_patch.js.');
  }
  let engine;
  try { engine = fs.readFileSync(CLEANUP_ENGINE_FILE, 'utf8'); }
  catch (e) { throw new PlatformPatchError('cleanup mode: scripts/cleanup_engine.ps1 is missing.'); }
  engine = engine.replace(/^\uFEFF/, '').replace(/\r\n/g, '\n').trimEnd() + '\n\n';
  const end = restoreEnd + RESTORE_END.length;
  let out = text.slice(0, step4) + engine + CLEANUP_OPEN + text.slice(step4, restore) + CLEANUP_DELETE +
    text.slice(restore, end) + CLEANUP_FINISH + text.slice(end);
  if (countText(out, MODE_MENU_ANCHOR) === 1) out = out.replace(MODE_MENU_ANCHOR, () => MODE_MENU_ANCHOR.replace('while', () => CLEANUP_NO_MODE_MENU + 'while'));
  return out;
}

function applyPlatformPatch(ps1) {
  if (typeof ps1 !== 'string' || !ps1) throw new PlatformPatchError('empty seeder text');
  if (ps1.indexOf(MARKER) >= 0) return { ps1, applied: [], skipped: [], alreadyPatched: true };
  let out = ps1;
  const applied = [], skipped = [];
  for (const p of PATCHES) {
    const { count, matches } = countOccurrences(out, p.find);
    const ok = count >= p.min && count <= p.max && (!p.verify || matches.every(p.verify));
    if (!ok) {
      if (p.required) {
        throw new PlatformPatchError(`required patch "${p.id}" did not match the seeder (found ${count}, expected ${p.min}-${p.max}${p.verify ? ' with verification' : ''}). ` +
          `The DemoIQ engine changed: update scripts/platform_patch.js before seeding. Reason for the patch: ${p.why}`);
      }
      skipped.push(p.id);
      continue;
    }
    out = applyOne(out, p);
    applied.push(p.id);
  }
  out = applyCleanupMode(out);
  applied.push('cleanup-mode');
  const forbidden = findForbidden(out);
  if (forbidden.length) {
    throw new PlatformPatchError(`the patched seeder still contains: ${forbidden.join(', ')}. ` +
      'The DemoIQ engine changed: update scripts/platform_patch.js before seeding.');
  }
  return { ps1: MARKER + '\n' + out, applied, skipped, alreadyPatched: false };
}

module.exports = { applyPlatformPatch, PlatformPatchError, MARKER, MARKER_TOKEN, PATCHES, FORBIDDEN, findForbidden, CLEANUP_ENGINE_FILE };

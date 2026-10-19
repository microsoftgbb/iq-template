<#
  preflight_credentials.ps1 - Tier-3 credential pre-flight for DemoIQ: the five demo users.

  Purpose: BEFORE committing to a ~15-20 min seeding run, prove that each of the five demo
  users in the plain DemoIQ config can actually sign in with the password stored there.
  Wrong demo-user passwords are the #1 silent failure: the seeder runs and the containers
  the administrator creates (Teams team/channels, group chat) appear, but EVERY per-user
  step fails (ROPC AADSTS50126, Files/Mail 400), so no emails, files, chats or channel
  posts are produced.

  This check signs each demo user in exactly the way the seeder does: a PUBLIC-CLIENT
  Resource-Owner-Password-Credentials (ROPC) grant to the delegated-only DemoIQ app that
  deploy.py registered - grant_type=password, client_id, username, password and scope, and
  nothing else. The app has no certificate and no secret, so there is no client assertion.
  AADSTS50126 (bad username or password) is returned BEFORE any MFA step, so wrong
  passwords are caught even though MFA/Conditional Access still applies to the demo users
  (the seeder relaxes it for them during the run). A user who is past the password check
  but blocked by MFA/CA has a VALID password and is reported ok-pending-mfa.

  DEMO USERS ONLY: there is no administrator password anywhere. The administrator signs in
  for each run with a device code (admin_signin.ps1), so there is nothing to check here.

  TENANT AND APP come from the deployment receipts (`deploy.py status --json` -> demoiq:
  tenant_id, client_id; read with Get-DemoIQRunnerReceipts from platform.ps1). Until the
  demoiq phase has completed (no receipts), or when the runner cannot be run here, the
  check falls back to the config's admin UPN domain and existingAppId and says so
  (source "config" plus a note). When the receipts name another app than the config's
  existingAppId, no password is tested (reason "app-mismatch"): the seeder signs the demo
  users in with the config's app, so regenerate the config from the receipts first
  (repoint_config.ps1).

  READ-ONLY: it makes no tenant changes, seeds nothing and writes nothing to disk. Tokens
  stay in memory for the license read below; no token or password is ever printed.

  LICENSE CHECK (the "Teams channels seed no conversations" symptom): a user whose
  password is right but who lacks the required service plans fails in exactly that silent
  shape - the team and channels get created (by the administrator), but the per-user
  posts/uploads for the missing workload fail or vanish. So for every user whose sign-in
  ISSUED a token, this script also reads that user's OWN license details
  (GET /v1.0/me/licenseDetails with the user's delegated token - User.Read, read-only) and
  grades the three service-plan families the seeder depends on:
    teams      (servicePlanName TEAMS*)        - chats / channel posts / meetings content
    exchange   (EXCHANGE_S_*)                  - emails + meeting calendar events
    sharepoint (SHAREPOINT*, excluding -WAC)   - Word/PPT/Excel OneDrive uploads + the AoP
  Which families are REQUIRED comes from the config's contentTypes (both on-disk shapes
  accepted: an array of names or an object map name -> bool; an empty/absent list means
  the engine's baked fallbacks seed everything, so all three are required). Per family the
  grade is: ok (a matching plan provisioned Success) | pending (assigned but still
  PendingProvisioning/PendingActivation - common on freshly provisioned tenants; that
  workload may fail until provisioning completes) | disabled (assigned but turned off in
  the license options) | missing (no matching plan at all). Users that produced no token
  (ok-pending-mfa, consent-needed, failures) report licenses.status "unchecked" - the check
  never invents a verdict it could not read. License problems do NOT change
  "passed"/"allReady" (those stay strictly about signing in); they surface in the per-user
  "licenses" block and the top-level "licenses" summary, and the 1c.5 flow gates on them.

  Inputs:
    -ConfigPath     the plain DemoIQ config (<ws>/configs/*.json). An old encrypted
                    (cdx-enc-v1) config is refused: convert it once, in your own terminal,
                    with scripts/convert_config.ps1.
    -ReceiptsFile   optional saved `deploy.py status --json` output (default: ask the
                    runner, which makes no cloud calls for status)

  Output: a single compact JSON line, e.g.
    { "ok": true, "source": "receipts", "tenant": "<tenant id>", "clientId": "<app id>",
      "domain": "contoso.onmicrosoft.com", "checked": 5, "passed": 4, "failed": 1,
      "allReady": false, "users": [ { "upn": "...", "role": "user1", "derived": false,
      "status": "ok", "passwordValid": true, "reason": null, "hint": "...",
      "licenses": { "status": "ok|issues|pending|unchecked", "reason": null,
      "teams": "ok", "exchange": "ok", "sharepoint": "pending", "missing": [],
      "pending": ["sharepoint"], "detail": "..." } }, ... ],
      "licenses": { "requiredFamilies": ["teams","exchange","sharepoint"], "checked": 4,
      "ok": 3, "issues": 0, "pending": 1, "unchecked": 1, "allLicensed": false } }
  "source" is "receipts" (tenant = the receipts' tenant ID, clientId = the receipts' app) or
  "config" (tenant = the admin UPN domain, clientId = the config's existingAppId; a "note"
  says why the receipts were not used). The top-level "licenses" summary counts USERS by
  their licenses.status; "allLicensed" is true only when every user was checked and graded
  ok for every required family.
  Per-user "derived" is true when the config carried NO UPN for that slot and the standard
  CDX account name was assumed (<prefix>@<admin domain>) - a not-found on a derived row
  means the tenant does not ship the CDX demo users and the real UPNs must be set in the
  Config Builder.
  Per-user "status" is one of:
    ok                    - token issued; this user is ready.
    ok-pending-mfa        - password VALID, but MFA/Conditional Access blocks the token now
                            (AADSTS50076/50079/50072/50074/50158/53003); the seeder relaxes
                            MFA/CA for the demo users during the run, so this passes then.
    bad-password          - wrong password or UPN (AADSTS50126) -> re-save the config.
    not-found             - the UPN does not exist in the tenant (AADSTS50034).
    disabled              - account disabled (AADSTS50057).
    locked                - account locked (AADSTS50053).
    password-expired      - password expired (AADSTS50055); reset it, update the config.
    consent-needed        - password valid, but the DemoIQ app has no administrator consent
                            (AADSTS65001) -> an administrator reruns deploy.py with
                            --grant-demoiq-consent.
    app-not-found         - the DemoIQ app does not exist in the tenant (AADSTS700016)
                            -> rerun deploy.py up --with-demoiq --through demoiq.
    app-not-public-client - the app does not allow public-client (password) sign-in
                            (AADSTS7000218) -> rerun deploy.py up --with-demoiq --through demoiq.
    no-password           - the config has no password for this user.
    error                 - any other failure (see hint).
  "passed" counts the users ready for the run (ok, ok-pending-mfa), "failed" the rest, and
  "allReady" is true when none failed. passwordValid stays true for consent-needed (the
  password was accepted), but that user is not ready until consent is in place.
  Exit code is 0 on a completed check (even when users fail - that is a result, not a
  crash). It exits 2 when the check cannot run as asked: a missing, unreadable or
  encrypted config, no adminUpn, no app to sign in with, an unreadable -ReceiptsFile, or
  app-mismatch (ok:false, with "error" and usually "hint" / "reason").
#>
param(
  [Parameter(Mandatory = $true)][string]$ConfigPath,
  [string]$ReceiptsFile
)

# -- Require PowerShell 7+ (the skill's runtime). Re-launch under pwsh if started in 5.1. --
if ($PSVersionTable.PSVersion.Major -lt 7) {
  $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
  if (-not $pwsh) {
    Write-Host 'This script requires PowerShell 7+ (pwsh). Install it (winget install Microsoft.PowerShell) or run with: pwsh -NoProfile -ExecutionPolicy Bypass -File .\preflight_credentials.ps1 ...' -ForegroundColor Red
    exit 1
  }
  $self = if ($PSCommandPath) { $PSCommandPath } else { $MyInvocation.MyCommand.Path }
  $fwd = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $self)
  foreach ($kv in $PSBoundParameters.GetEnumerator()) {
    if ($kv.Value -is [switch]) { if ($kv.Value.IsPresent) { $fwd += "-$($kv.Key)" } }
    else { $fwd += "-$($kv.Key)"; $fwd += [string]$kv.Value }
  }
  & $pwsh.Source @fwd
  exit $LASTEXITCODE
}

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'platform.ps1')

$RunnerUp = 'python3 .github/skills/deploy-platform/scripts/deploy.py up --with-demoiq --through demoiq'
$RunnerStatus = 'python3 .github/skills/deploy-platform/scripts/deploy.py status --json'
# User.Read is one of the app's consented scopes and all the license self-read needs.
$Scope = 'https://graph.microsoft.com/User.Read offline_access openid'

function Write-Result($obj) {
  $obj | ConvertTo-Json -Compress -Depth 6
}

function Stop-Preflight([System.Collections.IDictionary]$Fields) {
  # The check cannot run as asked: report why (ok:false) and exit 2.
  $result = [ordered]@{ ok = $false }
  foreach ($k in $Fields.Keys) { $result[$k] = $Fields[$k] }
  Write-Result $result
  exit 2
}

# ---- 1. Read the plain config ----------------------------------------------
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
  Stop-Preflight @{ error = "Config not found: $ConfigPath" }
}
$cfg = $null
try { $cfg = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json }
catch {
  # Only the position: the parser's message can quote characters of a value (a password).
  $where = if ($_.Exception.Message -match 'line (\d+), position (\d+)') { " (line $($Matches[1]), position $($Matches[2]))" } else { '' }
  Stop-Preflight @{ error = "The config is not valid JSON$where`: $ConfigPath. Re-save it with the Config Builder." }
}
if ($cfg -isnot [System.Management.Automation.PSCustomObject]) {
  Stop-Preflight @{ error = "Not a DemoIQ config (expected one JSON object): $ConfigPath" }
}
if ([string]$cfg.v -eq 'cdx-enc-v1') {
  Stop-Preflight ([ordered]@{ reason = 'encrypted-config'
      error = "This config is encrypted (cdx-enc-v1): $ConfigPath. DemoIQ reads plain configs only."
      hint = "Convert it once in your own terminal (it asks for the old config password): pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/convert_config.ps1 -Path `"$ConfigPath`" - then re-run this check with the converted config." })
}

$adminUpn = ([string]$cfg.adminUpn).Trim()
if ($adminUpn -notmatch '^[^@\s]+@[^@\s]+$') {
  Stop-Preflight @{ error = 'Config has no usable adminUpn; recreate the config.' }
}
$domain = $adminUpn.Split('@')[1].ToLowerInvariant()
$configApp = ([string]$cfg.existingAppId).Trim()

# ---- 2. Tenant and app: the deployment receipts, else the config -----------
# An explicit -ReceiptsFile must be readable (never a silent fallback); the runner itself
# may be unavailable on this host, which falls back to the config below.
$receiptsHint = "Omit -ReceiptsFile to ask the runner directly, or save its output again: $RunnerStatus"
if ($ReceiptsFile -and -not (Test-Path -LiteralPath $ReceiptsFile -PathType Leaf)) {
  Stop-Preflight ([ordered]@{ domain = $domain; error = "Receipts file not found: $ReceiptsFile"; hint = $receiptsHint })
}
$receipts = $null; $receiptsProblem = $null
try { $receipts = Get-DemoIQRunnerReceipts -ReceiptsFile $ReceiptsFile }
catch {
  if ($ReceiptsFile) {
    Stop-Preflight ([ordered]@{ domain = $domain
        error = "Could not read the receipts file $ReceiptsFile`: $($_.Exception.Message)"; hint = $receiptsHint })
  }
  $receiptsProblem = $_.Exception.Message
}

$note = $null
if ($receipts -and [string]$receipts.tenant_id -and [string]$receipts.client_id) {
  $source = 'receipts'
  $tenant = ([string]$receipts.tenant_id).Trim()
  $clientId = ([string]$receipts.client_id).Trim()
  if ($configApp -ne $clientId) {
    # Never test passwords against another app than the one the seeder will use.
    $named = if ($configApp) { "names the app $configApp" } else { 'names no app (existingAppId is empty)' }
    $repoint = "pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/repoint_config.ps1 -ConfigPath `"$ConfigPath`"" +
      $(if ($ReceiptsFile) { " -ReceiptsFile `"$ReceiptsFile`"" } else { '' })
    Stop-Preflight ([ordered]@{ reason = 'app-mismatch'; source = $source; tenant = $tenant; clientId = $clientId
        configAppId = $configApp; domain = $domain
        error = "The config $named, but this deployment's DemoIQ app is $clientId. The seeder signs the demo users in with the config's app, so no password was tested."
        hint = "Regenerate the config from the deployment receipts: $repoint - then re-run this check." })
  }
} else {
  $source = 'config'
  $tenant = $domain
  $clientId = $configApp
  $why = if ($receiptsProblem) { "the deployment receipts could not be read ($receiptsProblem)" }
         elseif ($ReceiptsFile) { "the receipts file $ReceiptsFile has no DemoIQ receipts (the demoiq phase had not completed)" }
         else { 'the deployment has no DemoIQ receipts yet (the demoiq phase has not completed)' }
  if (-not $clientId) {
    Stop-Preflight ([ordered]@{ source = $source; tenant = $tenant; domain = $domain
        error = "No DemoIQ app to sign in with: $why, and the config has no existingAppId."
        hint = "Set up DemoIQ with: $RunnerUp (an administrator adds --grant-demoiq-consent), then regenerate the config from the receipts with scripts/repoint_config.ps1." })
  }
  $note = "Signed in against the config's tenant domain and app ID because $why. Confirm the deployment with: $RunnerStatus"
}

# ---- 3. Collect the demo users (u1..u5) from the config --------------------
# The Config Builder has NO preset demo-user names and refuses to save until the user typed
# all five UPNs, so configs it saves always carry explicit u{i}upn values. A BLANK u{i}upn
# still appears in legacy and engine-saved configs; derive it the same way the seeder does
# (generate.js: PREFIXES[i] + '@' + domain, the old CDX default names) so such a config is
# not falsely rejected - and mark the row derived=true, because the CDX names do not exist
# on every tenant and a not-found on an invented UPN needs a different fix message.
$PREFIXES = @('AdilE', 'BillieV', 'CoraT', 'CoreyG', 'DaichiM')
$targets = @()
foreach ($i in 1..5) {
  $u = ([string]$cfg."u${i}upn").Trim()
  $wasDerived = $false
  if ($u -notmatch '@') {
    $u = $PREFIXES[$i - 1] + '@' + $domain
    $wasDerived = $true
  }
  $targets += [pscustomobject]@{ upn = $u; pwd = [string]$cfg."u${i}pwd"; role = "user$i"; derived = $wasDerived }
}

# ---- 3b. Which license families does THIS config's content need? -----------
# contentTypes has TWO on-disk shapes (the Config Builder saves an ARRAY of names; the
# engine page's own save writes an OBJECT map name -> bool) - normalize both, exactly like
# every other consumer. An empty/absent list means the engine seeds its baked fallback
# content set, which touches every workload, so all three families are required.
$ctList = @()
try {
  $ctRaw = $cfg.contentTypes
  if ($null -ne $ctRaw) {
    if ($ctRaw -is [System.Array]) { $ctList = @($ctRaw | ForEach-Object { [string]$_ }) }
    else { $ctList = @($ctRaw.PSObject.Properties | Where-Object { $_.Value } | ForEach-Object { [string]$_.Name }) }
  }
} catch { $ctList = @() }
$requiredFamilies = @()
if ($ctList.Count -eq 0) {
  $requiredFamilies = @('teams', 'exchange', 'sharepoint')
} else {
  $ctSet = @{}
  foreach ($c in $ctList) { $ctSet[([string]$c).ToLowerInvariant()] = $true }
  if ($ctSet['chats'] -or $ctSet['channel'] -or $ctSet['meetings']) { $requiredFamilies += 'teams' }
  if ($ctSet['emails'] -or $ctSet['meetings']) { $requiredFamilies += 'exchange' }
  if ($ctSet['word'] -or $ctSet['ppt'] -or $ctSet['excel'] -or $ctSet['aop']) { $requiredFamilies += 'sharepoint' }
}

# ---- 4. Map a sign-in error to a status + friendly hint ---------------------
$tenantLabel = if ($source -eq 'receipts') { "the deployment tenant $tenant" } else { "tenant '$domain'" }
$configAppHint = if ($source -eq 'config') { ' If the config still names an older DemoIQ app, regenerate it from the receipts afterwards with scripts/repoint_config.ps1.' } else { '' }

function Get-SignInError($ErrorRecord) {
  # The OAuth error body: its first description line (no trace IDs) and its AADSTS codes.
  $e = $null
  try { $e = $ErrorRecord.ErrorDetails.Message | ConvertFrom-Json } catch {}
  $description = if ($e -and $e.error_description) { (([string]$e.error_description -split "\r?\n")[0]).Trim() }
                 else { $ErrorRecord.Exception.Message }
  $codes = @(if ($e -and $e.error_codes) { $e.error_codes | ForEach-Object { "AADSTS$_" } })
  return @{ description = $description; text = ((@($description) + $codes) -join ' ') }
}

function Resolve-SignInError([string]$Text, [string]$Description, [string]$Upn) {
  if ($Text -match 'AADSTS50126\b') {
    return @{ status = 'bad-password'; passwordValid = $false; reason = 'AADSTS50126'
      hint = "Wrong password (or UPN) for $Upn. Re-save the config with the correct password, or reset this user's password in the tenant." }
  }
  if ($Text -match 'AADSTS50034\b') {
    return @{ status = 'not-found'; passwordValid = $false; reason = 'AADSTS50034'
      hint = "$Upn does not exist in $tenantLabel. Check the UPN spelling/domain in the config." }
  }
  if ($Text -match 'AADSTS50057\b') {
    return @{ status = 'disabled'; passwordValid = $false; reason = 'AADSTS50057'
      hint = "$Upn is disabled. Re-enable the account in the tenant." }
  }
  if ($Text -match 'AADSTS50053\b') {
    return @{ status = 'locked'; passwordValid = $false; reason = 'AADSTS50053'
      hint = "$Upn is locked (too many bad sign-ins). Wait and/or reset, then retry." }
  }
  if ($Text -match 'AADSTS50055\b') {
    return @{ status = 'password-expired'; passwordValid = $false; reason = 'AADSTS50055'
      hint = "$Upn's password is expired. Reset it, then update the config." }
  }
  # MFA / Conditional Access: evaluated AFTER the password was accepted, so the password is
  # valid. The seeder relaxes MFA/CA for the demo users during the run.
  if ($Text -match 'AADSTS(50076|50079|50072|50074|50158|53003)\b') {
    return @{ status = 'ok-pending-mfa'; passwordValid = $true; reason = 'mfa-pending'
      hint = "$Upn's password is VALID; MFA/Conditional Access blocks the password sign-in right now. The seeder relaxes MFA/Conditional Access for the demo users during the run, so this passes then." }
  }
  if ($Text -match 'AADSTS65001\b') {
    return @{ status = 'consent-needed'; passwordValid = $true; reason = 'AADSTS65001'
      hint = "$Upn's password is valid, but the DemoIQ app $clientId has no administrator consent in $tenantLabel. An administrator runs: $RunnerUp --grant-demoiq-consent" }
  }
  if ($Text -match 'AADSTS700016\b') {
    return @{ status = 'app-not-found'; passwordValid = $false; reason = 'AADSTS700016'
      hint = "The DemoIQ app $clientId was not found in $tenantLabel. Rerun: $RunnerUp$configAppHint" }
  }
  if ($Text -match 'AADSTS7000218\b') {
    return @{ status = 'app-not-public-client'; passwordValid = $false; reason = 'AADSTS7000218'
      hint = "The DemoIQ app $clientId does not allow public-client sign-in, so demo users cannot sign in with their passwords. Rerun: $RunnerUp (it restores the app's public-client setting).$configAppHint" }
  }
  return @{ status = 'error'; passwordValid = $false; reason = 'unknown'
    hint = "Sign-in failed for $Upn`: $Description" }
}

# ---- 5. Sign each demo user in: public-client ROPC --------------------------
$tokenEndpoint = "https://login.microsoftonline.com/$tenant/oauth2/v2.0/token"
$results = @()
# In memory only, for the license read below; tokens never reach the output or disk.
$userTokens = @{}
foreach ($t in $targets) {
  if ([string]::IsNullOrWhiteSpace($t.pwd)) {
    $results += [ordered]@{ upn = $t.upn; role = $t.role; derived = [bool]$t.derived; status = 'no-password'; passwordValid = $false
      reason = 'no-password'; hint = "The config has no password for $($t.upn). Re-save the config with this user's password." }
    continue
  }
  try {
    # Exactly these five fields: a public client sends no client_assertion and no client_secret.
    $body = @{ grant_type = 'password'; client_id = $clientId; username = $t.upn; password = $t.pwd; scope = $Scope }
    $resp = Invoke-RestMethod -Method POST -Uri $tokenEndpoint -ContentType 'application/x-www-form-urlencoded' -Body $body -TimeoutSec 60 -ErrorAction Stop
    if ($resp.access_token) {
      $userTokens[[string]$t.upn] = [string]$resp.access_token
      $results += [ordered]@{ upn = $t.upn; role = $t.role; derived = [bool]$t.derived; status = 'ok'; passwordValid = $true
        reason = $null; hint = "$($t.upn) signed in successfully." }
    } else {
      $results += [ordered]@{ upn = $t.upn; role = $t.role; derived = [bool]$t.derived; status = 'error'; passwordValid = $false
        reason = 'no-token'; hint = "$($t.upn) returned no token." }
    }
  } catch {
    $signInError = Get-SignInError $_
    $m = Resolve-SignInError $signInError.text $signInError.description $t.upn
    # A demo-user not-found usually means the config carries the five standard CDX account
    # names on a tenant that provisions DIFFERENT users (Diamond, customer-shaped tenants) -
    # point at the real fix, and say so explicitly when the UPN was never in the config.
    if ($m.status -eq 'not-found') {
      if ($t.derived) {
        $m.hint = "$($t.upn) does not exist in $tenantLabel - and the config never set this UPN: it was derived from the admin domain using the old CDX default account name (a legacy/engine-saved config). This tenant provisions different demo users. Open the Config Builder, enter the five demo-user UPNs that actually exist in this tenant (required fields in the Demo users card), and save a fresh config."
      } else {
        $m.hint = $m.hint + ' If this tenant provisions different demo users than the standard CDX accounts, open the Config Builder, correct the five demo-user UPNs, and re-save the config.'
      }
    }
    $results += [ordered]@{ upn = $t.upn; role = $t.role; derived = [bool]$t.derived; status = $m.status
      passwordValid = $m.passwordValid; reason = $m.reason; hint = $m.hint }
  }
}
$body = $null; $resp = $null

# ---- 6. License check per signed-in user (read-only, own token) -------------
# Only users whose sign-in issued a token can be read (/me/licenseDetails is a self-read
# under User.Read); everything else is honestly 'unchecked'. Never fails the script: a
# Graph error grades that one row unchecked with a reason.
function Get-FamilyStatus([object[]]$Plans, [string]$Family) {
  $match = switch ($Family) {
    'teams'      { @($Plans | Where-Object { $_.servicePlanName -like 'TEAMS*' }) }
    'exchange'   { @($Plans | Where-Object { $_.servicePlanName -like 'EXCHANGE_S_*' }) }
    'sharepoint' { @($Plans | Where-Object { $_.servicePlanName -like 'SHAREPOINT*' -and $_.servicePlanName -notlike 'SHAREPOINTWAC*' }) }
    default      { @() }
  }
  if (-not $match -or @($match).Count -eq 0) { return 'missing' }
  if (@($match | Where-Object { $_.provisioningStatus -eq 'Success' }).Count -gt 0) { return 'ok' }
  if (@($match | Where-Object { $_.provisioningStatus -like 'Pending*' }).Count -gt 0) { return 'pending' }
  return 'disabled'
}
$licSummary = [ordered]@{ requiredFamilies = @($requiredFamilies); checked = 0; ok = 0; issues = 0; pending = 0; unchecked = 0; allLicensed = $false }
foreach ($r in $results) {
  $lic = [ordered]@{ status = 'unchecked'; reason = $null; teams = $null; exchange = $null; sharepoint = $null; missing = @(); pending = @(); detail = $null }
  $tok = $userTokens[[string]$r.upn]
  if ([string]::IsNullOrEmpty($tok)) {
    $lic.reason = 'no-token'
    $licSummary.unchecked++
  } else {
    try {
      $ld = Invoke-RestMethod -Method GET -Uri 'https://graph.microsoft.com/v1.0/me/licenseDetails?$select=servicePlans,skuPartNumber' -Headers @{ Authorization = "Bearer $tok" } -TimeoutSec 30 -ErrorAction Stop
      $plans = @()
      foreach ($l in @($ld.value)) { if ($l.servicePlans) { $plans += @($l.servicePlans) } }
      foreach ($fam in @('teams', 'exchange', 'sharepoint')) { $lic[$fam] = Get-FamilyStatus $plans $fam }
      $missing = @(); $pendingF = @()
      foreach ($fam in @($requiredFamilies)) {
        if ($lic[$fam] -in @('missing', 'disabled')) { $missing += $fam }
        elseif ($lic[$fam] -eq 'pending') { $pendingF += $fam }
      }
      $lic.missing = @($missing); $lic.pending = @($pendingF)
      $licSummary.checked++
      if ($missing.Count -gt 0) {
        $lic.status = 'issues'; $licSummary.issues++
        $teamsNote = if ($missing -contains 'teams') { " Without a Teams plan, chats/channel posts/meetings sent AS this user do not land - the channels get created (by the administrator) but stay empty of conversations, the classic 'channels seed no conversations' field symptom." } else { '' }
        $lic.detail = "$($r.upn) has no provisioned service plan for: $($missing -join ', '). Content for those workloads cannot seed as this user.$teamsNote Assign/enable the missing license (e.g. an M365 E3/E5 seat with the plan enabled) in the tenant, then re-run this gate."
      } elseif ($pendingF.Count -gt 0) {
        $lic.status = 'pending'; $licSummary.pending++
        $lic.detail = "$($r.upn) has these plans assigned but still PROVISIONING: $($pendingF -join ', '). Freshly provisioned tenants land here - seeding now may fail for those workloads; provisioning usually completes within minutes to a few hours."
      } else {
        $lic.status = 'ok'; $licSummary.ok++
      }
    } catch {
      $lic.reason = 'check-failed'
      $lic.detail = "Could not read license details for $($r.upn): $($_.Exception.Message)"
      $licSummary.unchecked++
    }
  }
  $r['licenses'] = $lic
}
$tok = $null
$userTokens.Clear()
$licSummary.allLicensed = [bool]($licSummary.checked -gt 0 -and $licSummary.issues -eq 0 -and $licSummary.pending -eq 0 -and $licSummary.unchecked -eq 0)

# ---- 7. Summarise: "passed" = ready for the run (ok or ok-pending-mfa) ------
$passed = @($results | Where-Object { $_.status -in @('ok', 'ok-pending-mfa') }).Count
$failed = $results.Count - $passed
$summary = [ordered]@{ ok = $true; source = $source; tenant = $tenant; clientId = $clientId; domain = $domain
  checked = $results.Count; passed = $passed; failed = $failed; allReady = ($failed -eq 0); users = $results
  licenses = $licSummary }
if ($note) { $summary.note = $note }
Write-Result $summary
exit 0

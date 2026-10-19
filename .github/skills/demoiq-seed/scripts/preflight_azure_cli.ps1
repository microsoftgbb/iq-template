<#
  preflight_azure_cli.ps1 - Check C: Azure CLI tenant gate for meeting-audio (MP3) runs.

  Purpose: the seeder's text-to-speech step authenticates with a LIVE Azure CLI token
  (az account get-access-token --resource https://cognitiveservices.azure.com) on the
  platform's Foundry account - there is no stored key. A cached `az` login for a different
  tenant (a corporate session is the classic case) is invisible until the audio step fails
  mid-run, ~15-20 min in, after everything else has already seeded. This gate proves the
  `az` CLI context matches the demo tenant BEFORE the run commits ("everybody will be
  falling into that" - an easy, universal trap).

  ZERO COST WHEN AUDIO IS OFF: meeting audio is optional. When the config does not select
  meetings, this gate reports applicable:false without reading the receipts or invoking
  `az`; when the config names no Speech resource (speechResourceId - the only one the
  seeder uses), it reports applicable:false without invoking `az`, with a note naming
  repoint_config.ps1 when the deployment receipts offer one. A non-audio run never gains
  a new step, prompt or wait from this check.

  EXPECTED TENANT: the deployment receipts' tenant_id (`deploy.py status --json` ->
  demoiq, read with Get-DemoIQRunnerReceipts from platform.ps1), else -ExpectedTenantId.
  With neither, the az context cannot be verified (status no-expected-tenant). No network
  call is made: no OIDC discovery and no sign-in.

  READ-ONLY: `az account show` / `az account list` are local token-cache reads (no network
  sign-in is ever triggered). It makes no tenant changes, never writes credentials, and
  never modifies the az context itself - fixes are REPORTED as fixCommands, not executed.

  Inputs:
    -ConfigPath        the plain DemoIQ config (<ws>/configs/*.json). An old encrypted
                       (cdx-enc-v1) config is refused: convert it once, in your own
                       terminal, with scripts/convert_config.ps1.
    -ReceiptsFile      optional saved `deploy.py status --json` output (default: ask the
                       runner, which makes no cloud calls for status)
    -ExpectedTenantId  optional demo tenant ID (GUID), used when there are no receipts

  Output: a single compact JSON line, e.g.
    { "ok": true, "applicable": true, "status": "wrong-tenant",
      "domain": "contoso.onmicrosoft.com", "azUser": "me@corp.com",
      "azTenantId": "1111...", "azSubscriptionId": "3333...",
      "expectedTenantId": "2222...", "expectedTenantSource": "receipts",
      "expectedSubscriptionId": "4444...", "cachedMatch": false,
      "hint": "...", "fixCommands": [ "az logout", "az login --tenant 2222..." ] }
  "expectedTenantSource" is "receipts" or "parameter". "status" is one of:
    not-applicable     - audio not in play (meetings unselected, or no Speech resource):
                         nothing to check, nothing to report to the user.
    ok                 - az context matches the demo tenant (and the Speech resource's
                         subscription, when it is the az default).
    wrong-tenant       - az is signed into a DIFFERENT tenant. fixCommands carry the
                         remedy; when cachedMatch is true a sign-in for the right tenant is
                         already cached and a non-interactive `az account set` is enough.
    wrong-subscription - right tenant, but the az default subscription is not the Speech
                         resource's; `az account set` fixes it.
    not-logged-in      - no az session at all.
    no-cli             - the az CLI is not installed on this machine.
    no-expected-tenant - neither the deployment receipts nor -ExpectedTenantId name the
                         demo tenant, so the az context cannot be verified: confirm the
                         receipts with deploy.py status (or pass -ExpectedTenantId), re-run.
  An optional "note" carries context that does not block (another az identity than the
  config's administrator, a config Speech resource that is not the deployment's Foundry
  account, an -ExpectedTenantId that differs from the receipts - the receipts win). An
  optional "warning" relays the deployment's own meeting-audio check when it reported
  unavailable.
  Exit code is 0 on a completed check (any status - that is a result, not a crash). It
  exits 2 on usage/IO errors (ok:false with "error"): a missing, unreadable or encrypted
  config, no adminUpn, an unreadable -ReceiptsFile, or an -ExpectedTenantId that is not a
  tenant ID.
#>
param(
  [Parameter(Mandatory = $true)][string]$ConfigPath,
  [string]$ReceiptsFile,
  [string]$ExpectedTenantId
)

# -- Require PowerShell 7+ (the skill's runtime). Re-launch under pwsh if started in 5.1. --
if ($PSVersionTable.PSVersion.Major -lt 7) {
  $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
  if (-not $pwsh) {
    Write-Host 'This script requires PowerShell 7+ (pwsh). Install it (winget install Microsoft.PowerShell) or run with: pwsh -NoProfile -ExecutionPolicy Bypass -File .\preflight_azure_cli.ps1 ...' -ForegroundColor Red
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
$GuidPattern = '^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$'

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

$ExpectedTenantId = ([string]$ExpectedTenantId).Trim()
if ($ExpectedTenantId -and ($ExpectedTenantId -notmatch $GuidPattern)) {
  Stop-Preflight @{ error = "-ExpectedTenantId must be a tenant ID (GUID), not '$ExpectedTenantId'." }
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

# ---- 2. Applicability, part 1: meetings selected ---------------------------
# contentTypes has TWO on-disk shapes (array of names from the Config Builder, object map
# {meetings:true,...} from the engine page) - normalize both, exactly as the builder's
# applyConfig does.
$selectedTypes = @()
if ($cfg.PSObject.Properties['contentTypes']) {
  $ctv = $cfg.contentTypes
  if ($ctv -is [System.Array]) { $selectedTypes = @($ctv | ForEach-Object { [string]$_ }) }
  elseif ($ctv -is [System.Management.Automation.PSCustomObject]) {
    $selectedTypes = @($ctv.PSObject.Properties | Where-Object { $_.Value } | ForEach-Object { $_.Name })
  }
}
if (-not ($selectedTypes -contains 'meetings')) {
  # Audio is not in play: report and stop WITHOUT reading the receipts or touching `az` -
  # a non-audio run must never pay for this gate.
  Write-Result ([ordered]@{ ok = $true; applicable = $false; status = 'not-applicable'; domain = $domain
      reason = 'meetings-not-selected' })
  exit 0
}

# ---- 3. Deployment receipts (local: the runner makes no cloud calls for status) ----
# An explicit -ReceiptsFile must be readable (never a silent fallback); the runner itself
# may be unavailable on this host, which leaves -ExpectedTenantId below.
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

# ---- 4. Applicability, part 2: the config names a Speech resource ----------
# The seeder synthesizes audio only with the CONFIG's speechResourceId, so that alone
# decides applicability; the receipts' Foundry account is only offered as a fix.
$speechResourceId = ([string]$cfg.speechResourceId).Trim()
$receiptsSpeech = if ($receipts) { ([string]$receipts.speech_resource_id).Trim() } else { '' }
if (-not $speechResourceId) {
  # Still no `az`: without a Speech resource the seeder synthesizes no audio.
  $noSpeech = [ordered]@{ ok = $true; applicable = $false; status = 'not-applicable'; domain = $domain
    reason = 'no-speech-resource' }
  if ($receiptsSpeech) {
    $noSpeech.note = "The config names no Speech resource, so this run makes no meeting audio (meetings and transcripts still seed). To add audio, point the config at this deployment's Foundry account: pwsh -NoProfile -ExecutionPolicy Bypass -File .github/skills/demoiq-seed/scripts/repoint_config.ps1 -ConfigPath `"$ConfigPath`""
  }
  Write-Result $noSpeech
  exit 0
}

# ---- 5. The EXPECTED tenant: the receipts, else -ExpectedTenantId ----------
$notes = @()
$expectedTid = $null; $expectedSource = $null
$receiptsTid = if ($receipts) { ([string]$receipts.tenant_id).Trim() } else { '' }
if ($receiptsTid -match $GuidPattern) {
  $expectedTid = $receiptsTid; $expectedSource = 'receipts'
  if ($ExpectedTenantId -and ($ExpectedTenantId -ne $receiptsTid)) {
    $notes += "-ExpectedTenantId $ExpectedTenantId differs from the deployment receipts' tenant $receiptsTid; the receipts' tenant was used, because DemoIQ seeds the deployment tenant."
  }
} elseif ($ExpectedTenantId) {
  $expectedTid = $ExpectedTenantId; $expectedSource = 'parameter'
}
$expectedSubId = $null
if ($speechResourceId -match '/subscriptions/([0-9a-fA-F-]{36})/') { $expectedSubId = $Matches[1] }
if ($receiptsSpeech -and ($speechResourceId -ne $receiptsSpeech)) {
  $notes += "The config's speechResourceId is not this deployment's Foundry account ($receiptsSpeech); regenerate the config from the receipts with scripts/repoint_config.ps1 so meeting audio uses it."
}
$warning = $null
if ($receipts -and $receipts.audio -and ([string]$receipts.audio.status -eq 'unavailable')) {
  $detail = [string]$receipts.audio.detail
  $warning = "deploy.py's own meeting-audio check reported unavailable$(if ($detail) { " ($detail)" }). Rerun $RunnerUp to re-check, or deselect meeting audio for this run."
}

function Write-Final([System.Collections.IDictionary]$Result) {
  if ($notes.Count) { $Result['note'] = ($notes -join ' ') }
  if ($warning) { $Result['warning'] = $warning }
  Write-Result $Result
  exit 0
}

# ---- 6. Read the CURRENT az CLI context (local cache only) -----------------
$loginFix = if ($expectedTid) { "az login --tenant $expectedTid" } else { 'az login' }
$azCmd = Get-Command az -ErrorAction SilentlyContinue
if (-not $azCmd) {
  Write-Final ([ordered]@{ ok = $true; applicable = $true; status = 'no-cli'; domain = $domain
      expectedTenantId = $expectedTid; expectedTenantSource = $expectedSource
      hint = 'Meeting audio (MP3) needs the Azure CLI, and it is not installed on this machine. Install it, sign in to the demo tenant, and re-run this check - or deselect meeting audio for this run.'
      fixCommands = @((Get-DemoIQInstallHint az), $loginFix) })
}

$azAccount = $null
try {
  $azRaw = & $azCmd.Source account show --only-show-errors -o json 2>$null
  if ($LASTEXITCODE -eq 0 -and $azRaw) { $azAccount = ($azRaw -join "`n") | ConvertFrom-Json }
} catch {}

if (-not $azAccount -or -not $azAccount.tenantId) {
  Write-Final ([ordered]@{ ok = $true; applicable = $true; status = 'not-logged-in'; domain = $domain
      expectedTenantId = $expectedTid; expectedTenantSource = $expectedSource
      hint = "No Azure CLI session found. Meeting audio (MP3) authenticates with a live az token, so sign in to the demo tenant ($domain) before seeding."
      fixCommands = @($loginFix) })
}

$azUser = [string]$azAccount.user.name
$azTid  = [string]$azAccount.tenantId
$azSub  = [string]$azAccount.id

if (-not $expectedTid) {
  # Never guess the demo tenant: without receipts or -ExpectedTenantId there is nothing to
  # compare with, so report the az identity and how to resolve the expected tenant.
  $why = if ($receiptsProblem) { " (the deployment receipts could not be read: $receiptsProblem)" } else { '' }
  Write-Final ([ordered]@{ ok = $true; applicable = $true; status = 'no-expected-tenant'; domain = $domain
      azUser = $azUser; azTenantId = $azTid; azSubscriptionId = $azSub; expectedSubscriptionId = $expectedSubId
      hint = "Neither the deployment receipts nor -ExpectedTenantId name the demo tenant$why, so the Azure CLI context (signed in as $azUser, tenant $azTid) cannot be verified for meeting audio. Confirm the DemoIQ receipts with: $RunnerStatus (or pass -ExpectedTenantId <tenant ID>), then re-run this check."
      fixCommands = @() })
}

# ---- 7. Compare and report -------------------------------------------------
if ($azTid -ne $expectedTid) {
  # Before prescribing an interactive re-login, see if a sign-in for the RIGHT tenant is
  # already cached (az keeps one context per subscription): if so, a non-interactive
  # `az account set` is the whole fix.
  $cachedMatch = $null
  try {
    $listRaw = & $azCmd.Source account list --only-show-errors -o json 2>$null
    if ($LASTEXITCODE -eq 0 -and $listRaw) {
      $cachedMatch = (($listRaw -join "`n") | ConvertFrom-Json) |
        Where-Object { $_.tenantId -eq $expectedTid } |
        Sort-Object { $_.id -ne $expectedSubId } | Select-Object -First 1
    }
  } catch {}
  $fix = if ($cachedMatch) { @("az account set --subscription $($cachedMatch.id)") }
         else {
           @('az logout', "az login --tenant $expectedTid") +
           $(if ($expectedSubId) { @("az account set --subscription $expectedSubId") } else { @() })
         }
  Write-Final ([ordered]@{ ok = $true; applicable = $true; status = 'wrong-tenant'; domain = $domain
      azUser = $azUser; azTenantId = $azTid; azSubscriptionId = $azSub
      expectedTenantId = $expectedTid; expectedTenantSource = $expectedSource
      expectedSubscriptionId = $expectedSubId; cachedMatch = [bool]$cachedMatch
      hint = "The Azure CLI is signed in as $azUser (tenant $azTid), but this run's Speech resource lives in tenant $expectedTid ($domain). MP3 generation WILL fail until the az context points at the demo tenant.$(if ($cachedMatch) { ' A cached sign-in for the demo tenant already exists - switching the default subscription is enough (no new sign-in).' })"
      fixCommands = @($fix) })
}

if ($expectedSubId -and $azSub -and ($azSub -ne $expectedSubId)) {
  Write-Final ([ordered]@{ ok = $true; applicable = $true; status = 'wrong-subscription'; domain = $domain
      azUser = $azUser; azTenantId = $azTid; azSubscriptionId = $azSub
      expectedTenantId = $expectedTid; expectedTenantSource = $expectedSource
      expectedSubscriptionId = $expectedSubId
      hint = "Right tenant, but the az default subscription ($azSub) is not the Speech resource's ($expectedSubId). A non-interactive 'az account set' fixes it - no new sign-in."
      fixCommands = @("az account set --subscription $expectedSubId") })
}

if ($azUser -and ($azUser.ToLowerInvariant() -ne $adminUpn.ToLowerInvariant())) {
  # Right tenant, different identity: fine as long as that identity holds a data-plane role
  # on the Speech (Foundry) account. Not a block - just context if audio later returns 403.
  $notes += "az user $azUser differs from the config's administrator $adminUpn; meeting audio uses this az account's own token, so it works only if that account holds a data-plane role on the Speech (Foundry) account (deploy.py grants the deploying account Foundry User)."
}
Write-Final ([ordered]@{ ok = $true; applicable = $true; status = 'ok'; domain = $domain
    azUser = $azUser; azTenantId = $azTid; azSubscriptionId = $azSub
    expectedTenantId = $expectedTid; expectedTenantSource = $expectedSource
    expectedSubscriptionId = $expectedSubId })

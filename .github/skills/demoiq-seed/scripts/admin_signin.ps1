<#
  admin_signin.ps1 - the administrator's sign-in for one DemoIQ seed or cleanup run.

  GitHub Copilot adaptation of DemoIQ (https://aka.ms/demoiq): there is no certificate
  and no stored admin password. Before each seed or cleanup run, a tenant administrator
  signs in (device code, with their MFA) to the delegated-only DemoIQ app that deploy.py
  registered and consented. The tenant and app come from the deployment receipts
  (deploy.py status --json -> demoiq), so the sign-in always targets the deploying tenant.

  The refresh token goes into the encrypted session store (DPAPI on Windows, AES-256-GCM
  in an owner-only file on macOS/Linux) for at most 12 hours; launch_seeder.ps1 hands it
  to exactly one run, which removes it from disk when it starts.

  ONE-TIME CODE: written only to an owner-only file. It never appears on stdout or the
  console, so an agent never sees or relays it, even in a pseudo-terminal: open that
  file yourself. A person running this in their own terminal may add -ShowCodeInTerminal.

  Params:
    -ExpectedUpn     the config's adminUpn; the sign-in must be that account
    -ReceiptsFile    saved `deploy.py status --json` output (default: ask the runner)
    -TimeoutSeconds  how long to wait for the sign-in (default 900; never past code expiry)
    -ShowCodeInTerminal  also print the sign-in instructions to this console (people only)
    -Status          report whether a sign-in is stored (no secrets)
    -Clear           forget the stored sign-in

  Output (stdout): JSON lines, never a token or the one-time code.
    {"status":"waiting","codeFile":..,"verificationUri":..,"expiresAt":..,"next":..}  while waiting
    {"ok":true,"tenantId":..,"user":..,"clientId":..,"scopes":N,"expiresAt":..,"warnings":[..]}
    {"ok":false,"reason":"<reason>","message":..}
      reasons: no-receipts, consent-missing, wrong-tenant, wrong-app, wrong-account,
               declined, expired, sign-in-failed, network, store-failed
  Exit: 0 done; 3 a person must act (consent, declined, expired, wrong account); 2 blocked.
#>
param(
  [string]$ExpectedUpn,
  [string]$ReceiptsFile,
  [int]$TimeoutSeconds = 900,
  [switch]$ShowCodeInTerminal,
  [switch]$Status,
  [switch]$Clear
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'session_store.ps1')

$SessionName = 'admin-session'
$SessionHours = 12
$GraphScope = 'https://graph.microsoft.com/.default offline_access openid profile'
$GlobalAdministratorRole = '62e90394-69f5-4237-9190-012177145e10'
$SignInOnlyScopes = @('offline_access', 'openid', 'profile', 'email')
$RunnerHint = 'python3 .github/skills/deploy-platform/scripts/deploy.py up --with-demoiq --through demoiq'

function Out-Json($o) { $o | ConvertTo-Json -Compress -Depth 5 }

function Stop-SignIn([int]$Code, [string]$Reason, [string]$Message) {
  Out-Json ([ordered]@{ ok = $false; reason = $Reason; message = $Message })
  exit $Code
}

function Get-OAuthError($ErrorRecord) {
  $e = $null
  try { $e = $ErrorRecord.ErrorDetails.Message | ConvertFrom-Json } catch {}
  $description = if ($e -and $e.error_description) { (($e.error_description -split "\r?\n")[0]).Trim() } else { $ErrorRecord.Exception.Message }
  return @{ error = if ($e) { [string]$e.error } else { '' }; description = $description }
}

if ($Clear) {
  Clear-DemoIQSessionSecret -Name $SessionName
  Out-Json ([ordered]@{ ok = $true; cleared = $true })
  exit 0
}

if ($Status) {
  $stored = $null
  try { $stored = Read-DemoIQSessionSecret -Name $SessionName -MaxAgeHours $SessionHours | ConvertFrom-Json } catch {}
  $report = [ordered]@{ ok = $true; signedIn = [bool]$stored }
  if ($stored) {
    $report.user = [string]$stored.upn; $report.tenantId = [string]$stored.tenantId; $report.clientId = [string]$stored.clientId
    $written = [System.IO.File]::GetLastWriteTimeUtc((Get-DemoIQSessionSecretPath -Name $SessionName))
    $report.expiresAt = $written.AddHours($SessionHours).ToString('o')
  }
  Out-Json $report
  exit 0
}

# --- target: the deploying tenant's DemoIQ app, from the runner's receipts ---
$receipts = $null
try { $receipts = Get-DemoIQRunnerReceipts -ReceiptsFile $ReceiptsFile }
catch { Stop-SignIn 2 'no-receipts' $_.Exception.Message }
if (-not $receipts -or -not $receipts.client_id -or -not $receipts.tenant_id) {
  Stop-SignIn 2 'no-receipts' "DemoIQ is not set up for this deployment yet. Run: $RunnerHint (an administrator adds --grant-demoiq-consent)."
}
if ([string]$receipts.consent -notin @('granted', 'unverified')) {
  Stop-SignIn 3 'consent-missing' "Administrator consent for the DemoIQ app is not confirmed. An administrator runs: $RunnerHint --grant-demoiq-consent"
}
$tenant = [string]$receipts.tenant_id
$client = [string]$receipts.client_id
$required = @($receipts.delegated_scopes | Where-Object { $_ -and $SignInOnlyScopes -notcontains $_ })
$authority = "https://login.microsoftonline.com/$tenant/oauth2/v2.0"
# A new sign-in replaces any earlier one, which never lingers if this one fails.
Clear-DemoIQSessionSecret -Name $SessionName

# --- device code: the one-time code goes to an owner-only file, never to stdout ---
try {
  $device = Invoke-RestMethod -Method POST -Uri "$authority/devicecode" -ContentType 'application/x-www-form-urlencoded' `
    -TimeoutSec 60 -Body @{ client_id = $client; scope = $GraphScope }
} catch {
  Stop-SignIn 2 'network' ('Could not start the sign-in: ' + (Get-OAuthError $_).description)
}
$codeFile = Join-Path (New-DemoIQPrivateDirectory (Get-DemoIQSessionDirectory)) 'admin-signin-code.txt'
[System.IO.File]::WriteAllText($codeFile, '')
Set-DemoIQPrivateFile $codeFile
[System.IO.File]::WriteAllText($codeFile, "DemoIQ administrator sign-in (tenant $tenant)`n$($device.message)`n")
if ($ShowCodeInTerminal) { Write-Host $device.message -ForegroundColor Yellow }

$deadline = (Get-Date).AddSeconds([Math]::Max(1, [Math]::Min($TimeoutSeconds, [int]$device.expires_in)))
$who = if ($ExpectedUpn) { " as $ExpectedUpn" } else { ' as a tenant administrator' }
Out-Json ([ordered]@{
    status = 'waiting'
    codeFile = $codeFile
    verificationUri = [string]$device.verification_uri
    expiresAt = $deadline.ToUniversalTime().ToString('o')
    next = "Open $codeFile yourself to read the one-time code, then sign in$who at $($device.verification_uri)."
  })

$tokens = $null
$interval = [Math]::Max(1, [int]$device.interval)
try {
  while (-not $tokens -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds $interval
    try {
      $tokens = Invoke-RestMethod -Method POST -Uri "$authority/token" -ContentType 'application/x-www-form-urlencoded' -TimeoutSec 60 -Body @{
        grant_type = 'urn:ietf:params:oauth:grant-type:device_code'; client_id = $client; device_code = $device.device_code
      }
    } catch {
      $e = Get-OAuthError $_
      if ($e.error -eq 'slow_down') { $interval += 5 }
      elseif ($e.error -eq 'authorization_declined') { Stop-SignIn 3 'declined' 'The sign-in was declined.' }
      elseif ($e.error -eq 'expired_token') { Stop-SignIn 3 'expired' 'The one-time code expired. Run admin_signin.ps1 again.' }
      elseif ($e.error -and $e.error -ne 'authorization_pending') { Stop-SignIn 2 'sign-in-failed' $e.description }
    }
  }
} finally {
  Remove-Item -LiteralPath $codeFile -Force -ErrorAction SilentlyContinue
}
if (-not $tokens) { Stop-SignIn 3 'expired' 'The sign-in did not finish in time. Run admin_signin.ps1 again.' }

# --- the sign-in must be this tenant's DemoIQ app, the expected account and the consented scopes ---
$claims = ConvertFrom-DemoIQJwtPayload ([string]$tokens.access_token)
$user = if ($claims.upn) { [string]$claims.upn } else { [string]$claims.preferred_username }
$app = if ($claims.appid) { [string]$claims.appid } else { [string]$claims.azp }
if (-not $claims -or [string]$claims.tid -ne $tenant) { Stop-SignIn 2 'wrong-tenant' "The sign-in is not for the deployment tenant $tenant." }
if ($app -ne $client) { Stop-SignIn 2 'wrong-app' "The sign-in is not for the DemoIQ app $client." }
if ($ExpectedUpn -and $user -ne $ExpectedUpn) {
  Stop-SignIn 3 'wrong-account' "Signed in as $user, but the config's administrator is $ExpectedUpn. Run admin_signin.ps1 again and choose $ExpectedUpn (or correct adminUpn in the config)."
}
$granted = @(([string]$claims.scp) -split ' ' | Where-Object { $_ })
$missing = @($required | Where-Object { $granted -notcontains $_ })
if ($missing.Count) {
  Stop-SignIn 3 'consent-missing' ("The sign-in lacks " + ($missing -join ', ') + ". An administrator runs: $RunnerHint --grant-demoiq-consent")
}
if (-not $tokens.refresh_token) { Stop-SignIn 2 'sign-in-failed' 'The sign-in returned no refresh token (offline_access).' }

$warnings = @()
if ($claims.wids -and @($claims.wids) -notcontains $GlobalAdministratorRole) {
  $warnings += 'not-global-administrator: DemoIQ prepares the tenant (Conditional Access, per-user MFA, Security Defaults) as this account, which normally needs a Global Administrator.'
}
if ([string]$receipts.consent -eq 'unverified') {
  $warnings += 'consent-unverified: deploy.py could not read the tenant-wide grant. If demo users fail with AADSTS65001, an administrator reruns deploy.py with --grant-demoiq-consent.'
}

$session = Out-Json ([ordered]@{
    refreshToken = [string]$tokens.refresh_token; tenantId = $tenant; clientId = $client; upn = $user
    signedInAt = (Get-Date).ToUniversalTime().ToString('o')
  })
$tokens = $null
$path = Save-DemoIQSessionSecret -Name $SessionName -Value $session
$session = $null
if (-not $path) { Stop-SignIn 2 'store-failed' 'Could not store the sign-in in the encrypted session store.' }

Out-Json ([ordered]@{
    ok = $true; tenantId = $tenant; user = $user; clientId = $client; scopes = $granted.Count
    expiresAt = (Get-Date).AddHours($SessionHours).ToUniversalTime().ToString('o'); warnings = $warnings
  })
exit 0

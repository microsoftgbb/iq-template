<#
  session_store.ps1 - the per-user session store for short-lived DemoIQ secrets.

  The GitHub Copilot adaptation of DemoIQ keeps no certificate, no admin password and no
  config password. The only secret it stores is the administrator's per-run sign-in
  (named 'admin-session'): admin_signin.ps1 writes it, launch_seeder.ps1 checks it and
  run_seeder.ps1 consumes it. GitHub Copilot runs every terminal command in a fresh
  process, so this file is what carries the sign-in from one step to the next.

    * Save-DemoIQSessionSecret / Read-DemoIQSessionSecret / Test-DemoIQSessionSecret /
      Clear-DemoIQSessionSecret - named secrets, never in plaintext:
        Windows       DPAPI (ConvertFrom-SecureString, encrypted to the current user)
        macOS/Linux   an AES-256-GCM envelope in an owner-only (0600) file; the random
                      256-bit key lives in a separate owner-only file under the DemoIQ
                      workspace. (PowerShell's ConvertFrom-SecureString does NOT encrypt
                      on macOS/Linux - it only hex-encodes - so it is never used there.)
      A secret expires after 12 hours (default) and is deleted when found expired.

  FOLDER: $env:DEMOIQ_SESSION_DIR if set, else
      Windows  <TEMP>                      (per-user temp folder)
      macOS    $TMPDIR                     (per-user private temp folder)
      Linux    $XDG_RUNTIME_DIR            (per-user tmpfs) when available,
               else <workspace>/.session
  FILE: <folder>/.demoiq_<name>       KEY (macOS/Linux): <workspace>/.session/key

  USAGE (dot-source from a sibling script):
      . (Join-Path $PSScriptRoot 'session_store.ps1')
#>

. (Join-Path $PSScriptRoot 'platform.ps1')

$script:DemoIQSessionEnvelope = 'demoiq-session-v1'

function Get-DemoIQSessionDirectory {
  return (Join-Path (Get-DemoIQWorkspace) '.session')
}

function Get-DemoIQSessionKeyPath {
  return (Join-Path (Get-DemoIQSessionDirectory) 'key')
}

function ConvertFrom-DemoIQSecureString {
  # PtrToStringBSTR is correct on every platform (PtrToStringAuto truncates the
  # string to its first character on macOS/Linux).
  param([System.Security.SecureString]$Secure)
  if (-not $Secure) { return $null }
  $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
  try { return [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
  finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Get-DemoIQSessionKey {
  # 32 random bytes, created once, owner-only. Returns $null when unavailable.
  param([switch]$Create)
  $path = Get-DemoIQSessionKeyPath
  if (Test-Path -LiteralPath $path) {
    try {
      $k = [Convert]::FromBase64String(([System.IO.File]::ReadAllText($path)).Trim())
      if ($k.Length -eq 32) { return , $k }
    } catch {}
    if (-not $Create) { return $null }
  }
  if (-not $Create) { return $null }
  $null = New-DemoIQPrivateDirectory (Split-Path -Parent $path)
  $k = [byte[]]::new(32)
  [System.Security.Cryptography.RandomNumberGenerator]::Fill($k)
  [System.IO.File]::WriteAllText($path, '')
  Set-DemoIQPrivateFile $path
  [System.IO.File]::WriteAllText($path, [Convert]::ToBase64String($k))
  return , $k
}

function Protect-DemoIQSessionSecret {
  param([Parameter(Mandatory)][string]$Plain)
  $key = Get-DemoIQSessionKey -Create
  $nonce = [byte[]]::new(12); [System.Security.Cryptography.RandomNumberGenerator]::Fill($nonce)
  $data = [System.Text.Encoding]::UTF8.GetBytes($Plain)
  $cipher = [byte[]]::new($data.Length); $tag = [byte[]]::new(16)
  $gcm = [System.Security.Cryptography.AesGcm]::new($key, 16)
  try { $gcm.Encrypt($nonce, $data, $cipher, $tag) } finally { $gcm.Dispose() }
  return ([ordered]@{
      v = $script:DemoIQSessionEnvelope
      iv = [Convert]::ToBase64String($nonce)
      tag = [Convert]::ToBase64String($tag)
      data = [Convert]::ToBase64String($cipher)
    } | ConvertTo-Json -Compress)
}

function Unprotect-DemoIQSessionSecret {
  param([Parameter(Mandatory)][string]$Envelope)
  $key = Get-DemoIQSessionKey
  if (-not $key) { return $null }
  try {
    $e = $Envelope | ConvertFrom-Json
    if ($e.v -ne $script:DemoIQSessionEnvelope) { return $null }
    $nonce = [Convert]::FromBase64String($e.iv); $tag = [Convert]::FromBase64String($e.tag)
    $cipher = [Convert]::FromBase64String($e.data); $plain = [byte[]]::new($cipher.Length)
    $gcm = [System.Security.Cryptography.AesGcm]::new($key, 16)
    try { $gcm.Decrypt($nonce, $cipher, $tag, $plain) } finally { $gcm.Dispose() }
    return [System.Text.Encoding]::UTF8.GetString($plain)
  } catch { return $null }
}

function Get-DemoIQSessionSecretPath {
  param([Parameter(Mandatory)][ValidatePattern('^[a-z0-9][a-z0-9-]{0,40}$')][string]$Name)
  $dir = if ($env:DEMOIQ_SESSION_DIR) { $env:DEMOIQ_SESSION_DIR }
         elseif ((Test-DemoIQWindows) -or $IsMacOS) { [System.IO.Path]::GetTempPath() }
         elseif ($env:XDG_RUNTIME_DIR -and (Test-Path -LiteralPath $env:XDG_RUNTIME_DIR -PathType Container)) { $env:XDG_RUNTIME_DIR }
         else { Get-DemoIQSessionDirectory }
  return (Join-Path $dir ".demoiq_$Name")
}

function Save-DemoIQSessionSecret {
  # Returns the file path, or $null when the secret could not be stored.
  param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][AllowEmptyString()][string]$Value
  )
  if ([string]::IsNullOrEmpty($Value)) { return $null }
  try {
    $path = Get-DemoIQSessionSecretPath -Name $Name
    $dir = Split-Path -Parent $path
    if ($dir -eq (Get-DemoIQSessionDirectory) -or $dir -eq $env:DEMOIQ_SESSION_DIR) { $null = New-DemoIQPrivateDirectory $dir }
    elseif (-not (Test-Path -LiteralPath $dir)) { return $null }
    $content = if (Test-DemoIQWindows) {
      ConvertFrom-SecureString (ConvertTo-SecureString $Value -AsPlainText -Force)   # DPAPI, current user only
    } else {
      Protect-DemoIQSessionSecret -Plain $Value
    }
    [System.IO.File]::WriteAllText($path, '')
    Set-DemoIQPrivateFile $path
    [System.IO.File]::WriteAllText($path, $content)
    return $path
  } catch {
    return $null
  }
}

function Read-DemoIQSessionSecret {
  # Returns the secret text, or $null when it is missing, unreadable or older than
  # -MaxAgeHours (an expired secret is deleted).
  param([Parameter(Mandatory)][string]$Name, [double]$MaxAgeHours = 12)
  $path = Get-DemoIQSessionSecretPath -Name $Name
  if (-not (Test-Path -LiteralPath $path)) { return $null }
  try {
    if (((Get-Date) - [System.IO.File]::GetLastWriteTime($path)).TotalHours -gt $MaxAgeHours) {
      Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
      return $null
    }
    $raw = ([System.IO.File]::ReadAllText($path)).Trim()
    if ([string]::IsNullOrEmpty($raw)) { return $null }
    if (Test-DemoIQWindows) { return (ConvertFrom-DemoIQSecureString (ConvertTo-SecureString $raw)) }
    return (Unprotect-DemoIQSessionSecret -Envelope $raw)
  } catch {
    return $null
  }
}

function Test-DemoIQSessionSecret {
  # Cheap check for launchers: a stored, unexpired secret exists (nothing is decrypted).
  # An expired secret is deleted here too, so it never outlives its lifetime on disk.
  param([Parameter(Mandatory)][string]$Name, [double]$MaxAgeHours = 12)
  $path = Get-DemoIQSessionSecretPath -Name $Name
  if (-not (Test-Path -LiteralPath $path)) { return $false }
  if (((Get-Date) - [System.IO.File]::GetLastWriteTime($path)).TotalHours -le $MaxAgeHours) { return $true }
  Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
  return $false
}

function Clear-DemoIQSessionSecret {
  param([Parameter(Mandatory)][string]$Name)
  $path = Get-DemoIQSessionSecretPath -Name $Name
  if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
}

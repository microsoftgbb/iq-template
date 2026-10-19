<#
  convert_config.ps1 - convert an encrypted DemoIQ config (cdx-enc-v1) once into the
  plain, owner-only config this GitHub Copilot adaptation uses.

  RUN IT IN YOUR OWN TERMINAL. It asks for the config password with a masked prompt and
  refuses to run without an interactive console, so the password never passes through a
  command line, an environment variable or chat - and an agent cannot run it for you.

  What it does:
    - decrypts the config with its password (PBKDF2-SHA256 200000 + AES-256-GCM, exactly
      as DemoIQ wrote it; three attempts),
    - clears adminPwd and existingCertThumbprint (this adaptation has no admin password
      or certificate) and, unless -NoRepoint, points existingAppId and speechResourceId
      at this deployment's DemoIQ app from the deployment receipts,
    - writes the plain config owner-only into the config library <ws>/configs (or
      -OutPath), never overwriting a file,
    - leaves the encrypted file untouched: delete it yourself once the plain copy works.

  Params:
    -Path          the encrypted config
    -OutPath       (optional) where to write the plain config
    -ReceiptsFile  (optional) a saved `deploy.py status --json` output instead of the runner
    -NoRepoint     keep existingAppId / speechResourceId as they are

  Output: ONE compact JSON line, never a secret:
    { ok, source, path, repointed, demoUsers, reason?, error? }
  Exit: 0 converted; 2 usage problem (not found, not encrypted, no interactive console);
        3 wrong password after three attempts.
#>
param(
  [Parameter(Mandatory = $true)][string]$Path,
  [string]$OutPath,
  [string]$ReceiptsFile,
  [switch]$NoRepoint
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'platform.ps1')
function Out-Json($o) { $o | ConvertTo-Json -Compress -Depth 5 }
function Fail([int]$Code, [string]$Reason, [string]$Message) {
  Out-Json ([ordered]@{ ok = $false; reason = $Reason; error = $Message })
  exit $Code
}
function Set-ConfigField($Config, [string]$Name, $Value) {
  if ($Config.PSObject.Properties[$Name]) { $Config.$Name = $Value } else { $Config | Add-Member -NotePropertyName $Name -NotePropertyValue $Value }
}

function ConvertFrom-DemoIQEncryptedConfig {
  # Decrypts a cdx-enc-v1 envelope. Returns the config object, or $null for a wrong password.
  param([Parameter(Mandatory)]$Envelope, [Parameter(Mandatory)][System.Security.SecureString]$Password)
  $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
  try { $plainPassword = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
  finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
  $data = [byte[]]$Envelope.data
  if ($data.Length -le 16) { return $null }
  $kdf = [System.Security.Cryptography.Rfc2898DeriveBytes]::new([System.Text.Encoding]::UTF8.GetBytes($plainPassword),
    [byte[]]$Envelope.salt, 200000, [System.Security.Cryptography.HashAlgorithmName]::SHA256)
  $plainPassword = $null
  try { $key = $kdf.GetBytes(32) } finally { $kdf.Dispose() }
  $cipher = $data[0..($data.Length - 17)]
  $tag = $data[($data.Length - 16)..($data.Length - 1)]
  $plain = [byte[]]::new($cipher.Length)
  $gcm = [System.Security.Cryptography.AesGcm]::new($key, 16)
  try { $gcm.Decrypt([byte[]]$Envelope.iv, [byte[]]$cipher, [byte[]]$tag, $plain) }
  catch { return $null }
  finally { $gcm.Dispose(); [Array]::Clear($key, 0, $key.Length) }
  return ([System.Text.Encoding]::UTF8.GetString($plain) | ConvertFrom-Json)
}

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { Fail 2 'not-found' "Config not found: $Path" }
$source = (Resolve-Path -LiteralPath $Path).Path
$envelope = $null
try { $envelope = Get-Content -LiteralPath $source -Raw | ConvertFrom-Json } catch {}
if (-not $envelope -or $envelope.v -ne 'cdx-enc-v1' -or -not $envelope.salt -or -not $envelope.iv -or -not $envelope.data) {
  Fail 2 'not-encrypted' 'This is not an encrypted DemoIQ config (cdx-enc-v1); a plain config needs no conversion.'
}
if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
  Fail 2 'no-console' "Run this in your own terminal - it asks for the config password with a masked prompt: pwsh -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Path `"$source`""
}

$config = $null
for ($attempt = 1; $attempt -le 3 -and -not $config; $attempt++) {
  $secure = Read-Host -AsSecureString "Password for $(Split-Path -Leaf $source)"
  $config = ConvertFrom-DemoIQEncryptedConfig -Envelope $envelope -Password $secure
  $secure.Dispose()
  if (-not $config -and $attempt -lt 3) { Write-Host 'That password does not decrypt this config. Try again.' -ForegroundColor Yellow }
}
if (-not $config) { Fail 3 'wrong-password' 'Three attempts did not decrypt this config. Nothing was written.' }

# This adaptation never stores an admin password or certificate thumbprint.
Set-ConfigField $config 'adminPwd' ''
Set-ConfigField $config 'existingCertThumbprint' ''
$repointed = $false
if (-not $NoRepoint) {
  $receipts = $null
  try { $receipts = Get-DemoIQRunnerReceipts -ReceiptsFile $ReceiptsFile } catch {}
  if ($receipts -and $receipts.client_id) {
    Set-ConfigField $config 'existingAppId' ([string]$receipts.client_id)
    if ($receipts.speech_resource_id) { Set-ConfigField $config 'speechResourceId' ([string]$receipts.speech_resource_id) }
    $repointed = $true
  }
}

if (-not $OutPath) {
  $configsHome = New-DemoIQPrivateDirectory (Join-Path (Get-DemoIQWorkspace) 'configs')
  $base = [System.IO.Path]::GetFileNameWithoutExtension($source) -replace '[^A-Za-z0-9_.-]', '_'
  if ($base -notlike 'DemoIQ_config*') { $base = "DemoIQ_config_$base" }
  $OutPath = Join-Path $configsHome "$base-plain.json"
}
$n = 1; $target = $OutPath
while (Test-Path -LiteralPath $target) { $target = [System.IO.Path]::ChangeExtension($OutPath, $null).TrimEnd('.') + "-$n.json"; $n++ }
$temp = "$target.tmp"
[System.IO.File]::WriteAllText($temp, '')
Set-DemoIQPrivateFile $temp
[System.IO.File]::WriteAllText($temp, ($config | ConvertTo-Json -Depth 10))
Move-Item -LiteralPath $temp -Destination $target
Set-DemoIQPrivateFile $target

$users = @(1..5 | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$config.("u${_}upn")) }).Count
Out-Json ([ordered]@{ ok = $true; source = $source; path = (Resolve-Path -LiteralPath $target).Path; repointed = $repointed; demoUsers = $users })
exit 0

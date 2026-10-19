// test_platform_patch.js - the GitHub Copilot adaptation's seeder patch
// (scripts/platform_patch.js, applied by generate.js). Node built-ins only.
//
// Covers:
//   1. A real seeder generated from the fixture carries the v2 marker, every patch
//      applied, and nothing from FORBIDDEN (certificate, app-only token, Graph
//      PowerShell module, config password, admin password sign-in, consent writes,
//      cached admin token, telemetry).
//   2. The delegated model is in place: plain config, per-run administrator session
//      renewed by refresh token, Graph over REST to graph.microsoft.com only, public-
//      client demo-user sign-in, consent left to deploy.py, the administrator kept out
//      of the MFA/CA exclusion.
//   3. The patch is idempotent and fails LOUDLY (generate.js exit 10) when a required
//      anchor disappears from the engine or a forbidden construct survives, instead of
//      emitting a seeder outside the delegated model.
//   4. Cleanup mode: the same seeder skips content generation and seeding, deletes the
//      manifests' content between the tenant preparation and the restore, and writes
//      cleanup_result.json - and fails loudly when its anchors drift.
'use strict';

const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const root = path.resolve(__dirname, '..');
const generateJs = path.join(root, 'scripts', 'generate.js');
const engineHtml = path.join(root, 'engine', 'DemoIQ.html');
const { applyPlatformPatch, PATCHES, MARKER, MARKER_TOKEN, findForbidden } = require(path.join(root, 'scripts', 'platform_patch.js'));

let failures = 0;
function check(name, ok, detail) {
  if (ok) console.log('  ok - ' + name);
  else { failures++; console.log('  FAIL - ' + name + (detail ? ' :: ' + detail : '')); }
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'demoiq-patch-'));
const home = path.join(tmp, 'home');
fs.mkdirSync(home);
const prefs = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'test-prefs.json'), 'utf8'));
prefs.contentTypes = ['emails', 'chats', 'channel', 'meetings', 'word', 'ppt', 'excel', 'aop'];
prefs.speechResourceId = '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/fixture';
const prefsFile = path.join(tmp, 'prefs.json');
fs.writeFileSync(prefsFile, JSON.stringify(prefs), 'utf8');

function generate(html, outDir) {
  return spawnSync(process.execPath, [generateJs, '--html', html, '--prefs', prefsFile,
    '--emit', 'ps1', '--out-dir', outDir, '--script-name', 'DemoIQ.ps1'],
    { encoding: 'utf8', env: Object.assign({}, process.env, { HOME: home, USERPROFILE: home }), timeout: 180000 });
}
function count(text, needle) { return text.split(needle).length - 1; }

console.log('1. generated seeder is patched and holds nothing forbidden');
const out = path.join(tmp, 'out');
const r = generate(engineHtml, out);
check('generate.js --emit ps1 exits 0', r.status === 0, 'exit=' + r.status + ' ' + String(r.stderr).slice(0, 300));
let ps1 = '';
let result = null;
try { ps1 = fs.readFileSync(path.join(out, 'DemoIQ.ps1'), 'utf8').replace(/^\uFEFF/, ''); } catch (e) {}
try { result = JSON.parse(r.stdout); } catch (e) {}
check('seeder starts with the v2 marker', ps1.startsWith(MARKER) && MARKER.indexOf(MARKER_TOKEN) >= 0);
const applied = (result && result.outputs && result.outputs.platformPatch && result.outputs.platformPatch.applied) || [];
check('every required patch reported as applied', PATCHES.filter(p => p.required).every(p => applied.includes(p.id)), 'applied=' + applied.join(','));
check('optional patches applied on the current engine', PATCHES.every(p => applied.includes(p.id)), 'applied=' + applied.join(','));
check('no forbidden construct survives', findForbidden(ps1).length === 0, findForbidden(ps1).join(','));
check('the launchers check this marker', ['run_seeder.ps1', 'launch_seeder.ps1'].every(f =>
  fs.readFileSync(path.join(root, 'scripts', f), 'utf8').indexOf("'" + MARKER_TOKEN + "'") >= 0));
const fixturePw = prefs.users[0].pwd;
check('the seeder never bakes a demo-user password from prefs (they are read from the config at run time)', fixturePw && ps1.indexOf(fixturePw) < 0);
let prefsCopy = null;
try { prefsCopy = fs.readFileSync(path.join(out, 'demoiq-prefs.json'), 'utf8'); } catch (e) {}
check('the run folder keeps the prefs for a re-seed, without any credential field', prefsCopy &&
  JSON.parse(prefsCopy).customScenarioText === prefs.customScenarioText && JSON.parse(prefsCopy).users.length === prefs.users.length &&
  prefsCopy.indexOf(fixturePw) < 0 && !/"(adminPwd|existingCertThumbprint|streamKey|pwd)"/.test(prefsCopy));
if (process.platform !== 'win32' && prefsCopy) check('the prefs copy is owner-only (0600)', (fs.statSync(path.join(out, 'demoiq-prefs.json')).mode & 0o777) === 0o600);

console.log('2. the delegated model is in place');
check('plain config: read as JSON, no password prompt, no decryption',
  ps1.indexOf("$_cfg = Get-Content -LiteralPath $ConfigFile -Raw -ErrorAction Stop | ConvertFrom-Json") >= 0 &&
  ps1.indexOf('Rfc2898DeriveBytes') < 0 && ps1.indexOf('AesGcmHelper') < 0);
check('an encrypted config stops the run with the convert hint', /\$_cfg\.v -eq 'cdx-enc-v1'[\s\S]{0,400}convert or re-save it with the skill/.test(ps1));
check('the administrator session is defined once and started once',
  count(ps1, 'function Initialize-DemoIQAdminSession {') === 1 && count(ps1, '\nInitialize-DemoIQAdminSession\n') === 1);
check('the session is started before the first Graph or history call',
  ps1.indexOf('\nInitialize-DemoIQAdminSession\n') < ps1.indexOf('Run Mode Selection') &&
  ps1.indexOf('\nInitialize-DemoIQAdminSession\n') < ps1.indexOf("-Uri 'https://graph.microsoft.com/v1.0/organization'"));
check('the session comes from DEMOIQ_ADMIN_SESSION and is cleared on read',
  /\$_raw = \$env:DEMOIQ_ADMIN_SESSION\n  \$env:DEMOIQ_ADMIN_SESSION = \$null/.test(ps1));
check('the admin token is renewed with a public-client refresh_token grant',
  /grant_type = 'refresh_token'; client_id = \$_a\.ClientId; refresh_token = \$_a\.RefreshToken; scope = 'https:\/\/graph\.microsoft\.com\/\.default offline_access'/.test(ps1) &&
  ps1.indexOf('client_secret') < 0);
check('Graph is called over REST, to graph.microsoft.com only',
  ps1.indexOf('function Invoke-MgGraphRequest {') >= 0 && ps1.indexOf("([uri]$_uri).Host -ne 'graph.microsoft.com'") >= 0);
check('every cached admin-token read renews the token', count(ps1, '$(Get-DemoIQAdminToken -Soft)') >= 10, String(count(ps1, '$(Get-DemoIQAdminToken -Soft)')));
check('demo users sign in as a public client', count(ps1, "grant_type='password';client_id=$Global:AppId;username=") >= 2);
check('no app-only fallback', /function Get-AppOnlyGraphToken \{\n(?:  #[^\n]*\n)*  return \$null\n\}/.test(ps1));
check('consent is checked, never granted, by the seeder', ps1.indexOf('[CONSENT] Checking the administrator token') >= 0);
check('the administrator stays out of the MFA/CA exclusion', ps1.indexOf('$caUsers = @($AdminUPN) + $caUsers') < 0);
check('no certificate store location is recorded', ps1.indexOf("$_tenantCfgItems['certStoreLocation']     = 'none (delegated sign-in)'") >= 0);
check('no PtrToStringAuto (truncates typed passwords on macOS/Linux)', ps1.indexOf('PtrToStringAuto') < 0);
check('no telemetry writer or run-id plumbing', !/DEMOIQ_TELEMETRY|log_event|__telArgs|\[TELEMETRY\]/.test(ps1));
check('telemetry removal is announced in place', ps1.indexOf('usage telemetry is removed in the demoiq-seed') >= 0);

console.log('3. idempotent, and loud on engine drift');
const again = applyPlatformPatch(ps1);
check('re-applying is a no-op', again.alreadyPatched === true && again.ps1 === ps1);
const html = fs.readFileSync(engineHtml, 'utf8');
function drift(name, from, to, expectInError) {
  const changed = html.replace(from, to);
  check(name + ': drift really changed the engine copy', changed !== html);
  const file = path.join(tmp, 'DemoIQ-' + name + '.html');
  fs.writeFileSync(file, changed, 'utf8');
  const dir = path.join(tmp, 'out-' + name);
  const d = generate(file, dir);
  check(name + ': generate.js exits 10', d.status === 10, 'exit=' + d.status + ' ' + String(d.stderr).slice(0, 200));
  check(name + ': the error names the cause', expectInError.test(String(d.stderr)), String(d.stderr).slice(0, 300));
  check(name + ': no seeder was written', !fs.existsSync(path.join(dir, 'DemoIQ.ps1')));
}
drift('anchor-gone', '# -- Certificate lookup --', '# -- Certificate check --', /admin-session/);
drift('forbidden-survives', '# -- Trigger OneDrive provisioning for admin (fire-and-forget) --',
  'Connect-MgGraph -Identity\n# -- Trigger OneDrive provisioning for admin (fire-and-forget) --', /graph-powershell-module/);
check('findForbidden flags a certificate assertion and an admin password sign-in',
  findForbidden("client_assertion=$x; username=$AdminUPN").join(',') === 'certificate-assertion,admin-password-sign-in');
check('findForbidden flags a cached admin-token read but not an assignment',
  findForbidden('$h = "Bearer $Global:AdminToken"').includes('stale-admin-token') && findForbidden('$Global:AdminToken = $null').length === 0);

console.log('4. cleanup mode: the same seeder runs a tenant cleanup');
check('cleanup mode reported as applied', applied.includes('cleanup-mode'));
const cleanupEngine = fs.readFileSync(path.join(root, 'scripts', 'cleanup_engine.ps1'), 'utf8').replace(/^\uFEFF/, '');
check('the cleanup engine is inlined once', count(ps1, 'function Invoke-DemoIQCleanup {') === 1 && count(ps1, 'function Read-DemoIQCleanupManifest {') === 1);
check('the inlined engine holds nothing forbidden', findForbidden(cleanupEngine).length === 0, findForbidden(cleanupEngine).join(','));
const iSkip = ps1.indexOf("if ($env:DEMOIQ_RUN_KIND -ne 'cleanup') {");
const iStep4 = ps1.indexOf('# STEP 4: COPILOT CONTENT GENERATION');
const iDelete = ps1.indexOf("if ($env:DEMOIQ_RUN_KIND -eq 'cleanup') {\n  Update-Progress -Step 4");
const iRestore = ps1.indexOf('# CLEANUP: RESTORE TENANT SETTINGS');
const iFinish = ps1.indexOf('$_cuResult.tenantRestored = [bool]$_tsAllRestored');
check('content generation and seeding are skipped in a cleanup run', iSkip > 0 && iSkip < iStep4 && iStep4 < iDelete);
check('deletion runs after the tenant preparation and before the restore', iDelete > 0 && iDelete < iRestore);
check('the restore runs for a cleanup too, then the result is written', iRestore < iFinish && ps1.indexOf("Join-Path $OutputDir 'cleanup_result.json'") > iFinish);
check('a cleanup run skips the run-mode and history menu',
  ps1.indexOf("if ($env:DEMOIQ_RUN_KIND -eq 'cleanup') { $_fastMode = $true; $_modeConfirmed = $true;") >= 0);
check('the cleanup run reads the sources the dry-run preview reads',
  /Get-DemoIQCleanupSeedRunFolders -Workspace \$env:DEMOIQ_CLEANUP_WORKSPACE/.test(ps1) && /-LogsFolder \$_cuLogs -AdminToken \{ Get-DemoIQAdminToken \}/.test(ps1));
check('each demo user deletes with a public-client token, the administrator only with the run session',
  /Get-DemoIQCleanupUserToken -TenantId \$Global:TenantId -ClientId \$Global:AppId -Upn \$Upn/.test(ps1) &&
  /if \(\$Upn -eq \$AdminUPN\) \{ return \(Get-DemoIQAdminToken -Soft\) \}/.test(ps1));
check('a failed cleanup carries its reason to run_seeder', ps1.indexOf("$env:DEMOIQ_ABORT_REASON = 'cleanup-failed: ' + $_cuResult.error; exit 1") >= 0);
drift('cleanup-step4-gone', '# STEP 4: COPILOT CONTENT GENERATION', '# STEP 4: CONTENT GENERATION', /cleanup mode: the STEP 4 anchor/);
drift('cleanup-restore-changed', '$Global:TenantRestored = $true', '$Global:TenantWasRestored = $true', /cleanup mode: the tenant restore region/);

try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (e) {}
console.log(failures === 0 ? 'PLATFORM-PATCH TESTS PASSED' : ('PLATFORM-PATCH TESTS FAILED: ' + failures));
process.exit(failures === 0 ? 0 : 1);

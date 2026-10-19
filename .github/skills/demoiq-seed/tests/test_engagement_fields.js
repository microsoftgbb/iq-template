// test_engagement_fields.js - engagement-tracking fields (rossId / opportunityId /
// tpid) + company-name propagation test suite. Node built-ins only.
//
// Covers:
//   1. Round-trip: prefs with the three fields --emit both -> read the emitted plain,
//      owner-only config -> values present, trimmed, capped at 64 chars; no admin
//      password or certificate thumbprint ever reaches it.
//   2. From-config prompts dump (plain configs; an encrypted one is refused): every
//      content key's prompt carries the company
//      name on a CUSTOM-scenario run whose brief names no company (a real defect:
//      the typed company once reached no generated content), and the
//      engagement IDs appear in NO prompt (hard guardrail: business metadata,
//      never scenario content).
//   3. No telemetry (GitHub Copilot adaptation): generation writes no events
//      anywhere - not under the isolated HOME, not under a DEMOIQ_TELEMETRY_ROOT -
//      and a legacy config WITHOUT the keys still generates (backward compat).
//   4. Static contracts: the Config Builder / engine / template / generate.js all
//      carry the keys (config compatibility), the builder's autofill hardening
//      attributes exist (the admin-UPN cross-fill fix), and the builder and
//      serve_local are credential-free (no admin password, certificate or config
//      password; plain save to /save; drafted configs through GET /config).
//
// Every child process runs with an isolated HOME so nothing touches the user's real
// DemoIQ workspace.
'use strict';

const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const here = __dirname;
const root = path.resolve(here, '..');
const generateJs = path.join(root, 'scripts', 'generate.js');
const engineHtml = path.join(root, 'engine', 'DemoIQ.html');

let failures = 0;
function check(name, ok, detail) {
  if (ok) { console.log('  ok - ' + name); }
  else { failures++; console.log('  FAIL - ' + name + (detail ? ' :: ' + detail : '')); }
}

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'demoiq-engagement-'));
function tmpPath(name) { return path.join(tmp, name); }

const ROSS = 'ROSS-7745812';
const OPP = '7-FAKEOPP001';
const TPID_RAW = '9'.repeat(70);           // over-length on purpose
const TPID_CAPPED = '9'.repeat(64);        // cap is 64
const COMPANY = 'Northwind Traders';

// Obviously fake tenant + creds (never a real value).
const basePrefs = {
  adminUpn: 'admin@fakedemo.onmicrosoft.com',
  adminPwd: 'x',
  existingAppId: '00000000-0000-0000-0000-000000000001',
  existingCertThumbprint: '0'.repeat(40),
  demoLanguage: 'English',
  industry: 'Technology & Software',
  companyName: COMPANY,
  customScenarioText: 'Title: The Payments Platform Standoff\nDescription: The executive team debates going live with a new payments platform while a competitor circles.\nExcel: Launch_Readiness_Tracker.xlsx\nWord: GoLive_Risk_Assessment.docx\nPowerPoint: Launch_Decision_Briefing.pptx',
  contentTypes: ['emails', 'chats', 'channel', 'word', 'ppt', 'excel', 'meetings', 'audiotranscript'],
  users: [1, 2, 3, 4, 5].map(i => ({ upn: 'user' + i + '@fakedemo.onmicrosoft.com', pwd: 'x' }))
};

function runGenerate(args, extraEnv) {
  const n = (runGenerate.n = (runGenerate.n || 0) + 1);
  const home = tmpPath('home-' + n);
  const telRoot = tmpPath('telemetry-root-' + n);
  fs.mkdirSync(home, { recursive: true });
  const env = Object.assign({}, process.env, {
    HOME: home, USERPROFILE: home, DEMOIQ_TELEMETRY_ROOT: telRoot
  }, extraEnv || {});
  const r = spawnSync(process.execPath, [generateJs, '--html', engineHtml].concat(args), {
    encoding: 'utf8', env, timeout: 180000
  });
  return { r, home, telRoot };
}

// Any file a telemetry writer could have produced under the isolated roots.
function telemetryArtifacts(run) {
  const hits = [];
  const walk = (dir) => {
    if (!fs.existsSync(dir)) return;
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const f = path.join(dir, e.name);
      if (e.isDirectory()) walk(f);
      else if (/events\.jsonl|open-runs\.json|machine|telemetry/i.test(f)) hits.push(f);
    }
  };
  walk(run.home); walk(run.telRoot);
  return hits;
}

function writeConfig(obj, file) { fs.writeFileSync(file, JSON.stringify(obj, null, 2), 'utf8'); }

// ---------------------------------------------------------------- 1. round-trip
console.log('1. engagement fields round-trip through --emit both -> plain config');
{
  const prefs = Object.assign({}, basePrefs, {
    rossId: '  ' + ROSS + '  ',          // trim expected
    opportunityId: OPP,
    tpid: TPID_RAW                        // cap expected
  });
  const prefsFile = tmpPath('prefs-full.json');
  fs.writeFileSync(prefsFile, JSON.stringify(prefs), 'utf8');
  const outDir = tmpPath('out-both');
  const { r } = runGenerate(['--prefs', prefsFile, '--emit', 'both', '--out-dir', outDir, '--script-name', 't.ps1', '--config-name', 'cfg.json']);
  check('--emit both exits 0', r.status === 0, 'exit=' + r.status + ' stderr=' + String(r.stderr).slice(0, 300));
  const cfgFile = path.join(outDir, 'cfg.json');
  const cfg = JSON.parse(fs.readFileSync(cfgFile, 'utf8'));
  check('config is plain JSON (no encryption envelope)', cfg.v === undefined && cfg.adminUpn === basePrefs.adminUpn);
  if (process.platform !== 'win32') check('config file is owner-only (0600)', (fs.statSync(cfgFile).mode & 0o777) === 0o600, (fs.statSync(cfgFile).mode & 0o777).toString(8));
  check('no admin password reaches the config, whatever prefs carry', cfg.adminPwd === '', JSON.stringify(cfg.adminPwd));
  check('no certificate thumbprint reaches the config, whatever prefs carry', cfg.existingCertThumbprint === '', JSON.stringify(cfg.existingCertThumbprint));
  const ps1Both = fs.readFileSync(path.join(outDir, 't.ps1'), 'utf8');
  check('the seeder never carries the generation placeholders', ps1Both.indexOf('delegated-sign-in') < 0 && ps1Both.indexOf('0'.repeat(40)) < 0);
  const identity = JSON.parse(fs.readFileSync(path.join(outDir, 'run_identity.json'), 'utf8'));
  check('run_identity.json records no certificate', !('certThumbprint' in identity) && identity.appId === basePrefs.existingAppId);
  check('rossId persisted + trimmed', cfg.rossId === ROSS, JSON.stringify(cfg.rossId));
  check('opportunityId persisted', cfg.opportunityId === OPP, JSON.stringify(cfg.opportunityId));
  check('tpid persisted + capped at 64', cfg.tpid === TPID_CAPPED, 'len=' + String(cfg.tpid || '').length);
  check('companyName still persisted', cfg.companyName === COMPANY, JSON.stringify(cfg.companyName));
  check('adminUpn NOT cross-filled by any other field', cfg.adminUpn === basePrefs.adminUpn, JSON.stringify(cfg.adminUpn));

  // ------------------------------------------- 2. from-config prompts + guardrail
  // A builder-shaped config (flat u{i}upn keys + contentTypes array) - the realistic
  // --from-config input. Configs emitted by --emit both intentionally carry no
  // contentTypes (the chat-driven path bakes selection at generation time), which
  // would fall back to a minimal seeder here.
  console.log('2. from-config prompts: company name present, engagement IDs absent');
  const builderCfg = {
    adminUpn: basePrefs.adminUpn, adminPwd: 'x',
    existingAppId: basePrefs.existingAppId, existingCertThumbprint: basePrefs.existingCertThumbprint,
    demoLanguage: 'English', industry: basePrefs.industry,
    companyName: COMPANY, rossId: ROSS, opportunityId: OPP, tpid: TPID_CAPPED,
    customScenarioText: basePrefs.customScenarioText,
    contentTypes: basePrefs.contentTypes.slice()
  };
  [1, 2, 3, 4, 5].forEach(i => { builderCfg['u' + i + 'upn'] = 'user' + i + '@fakedemo.onmicrosoft.com'; builderCfg['u' + i + 'pwd'] = 'x'; });
  const builderCfgFile = tmpPath('builder-cfg.json');
  writeConfig(builderCfg, builderCfgFile);
  const emptyPrefs = tmpPath('prefs-empty.json');
  fs.writeFileSync(emptyPrefs, '{}', 'utf8');
  const encryptedCfgFile = tmpPath('encrypted-cfg.json');
  writeConfig({ v: 'cdx-enc-v1', salt: [1, 2, 3], iv: [4, 5, 6], data: [7, 8, 9] }, encryptedCfgFile);
  const enc = runGenerate(['--prefs', emptyPrefs, '--from-config', encryptedCfgFile, '--emit', 'prompts']);
  check('an encrypted config is refused with the convert hint (exit 2)', enc.r.status === 2 && /encrypted DemoIQ config[\s\S]*convert it once/.test(String(enc.r.stderr)), 'exit=' + enc.r.status + ' ' + String(enc.r.stderr).slice(0, 200));
  const p = runGenerate(['--prefs', emptyPrefs, '--from-config', builderCfgFile, '--emit', 'prompts']);
  check('--emit prompts exits 0', p.r.status === 0, 'exit=' + p.r.status + ' stderr=' + String(p.r.stderr).slice(0, 300));
  let dump = null;
  try { dump = JSON.parse(p.r.stdout); } catch (e) {}
  check('prompts dump parses', !!dump && !!dump.prompts, String(p.r.stdout).slice(0, 200));
  if (dump && dump.prompts) {
    const missingCo = dump.keys.filter(k => !new RegExp(COMPANY, 'i').test(dump.prompts[k]));
    check('EVERY prompt key carries the company name (custom scenario, no Company: line)', missingCo.length === 0, 'missing in: ' + missingCo.join(','));
    const all = Object.values(dump.prompts).join('\n');
    const leaked = [ROSS, OPP, TPID_CAPPED].filter(v => all.indexOf(v) >= 0);
    check('engagement IDs appear in NO prompt (hard guardrail)', leaked.length === 0, 'leaked: ' + leaked.join(','));
  }

  // --------------------------------------------- 3a. no telemetry, no ID leak
  console.log('3a. seeder generation: no ID leak, no telemetry written');
  const g = runGenerate(['--prefs', emptyPrefs, '--from-config', builderCfgFile, '--emit', 'ps1', '--out-dir', tmpPath('out-ps1'), '--script-name', 'seed.ps1']);
  check('--emit ps1 exits 0', g.r.status === 0, 'exit=' + g.r.status + ' stderr=' + String(g.r.stderr).slice(0, 300));
  const ps1Text = fs.readFileSync(path.join(tmpPath('out-ps1'), 'seed.ps1'), 'utf8');
  const leakedPs1 = [ROSS, OPP, TPID_CAPPED].filter(v => ps1Text.indexOf(v) >= 0);
  check('engagement IDs appear NOWHERE in the generated seeder', leakedPs1.length === 0, 'leaked: ' + leakedPs1.join(','));
  check('company name DOES reach the seeder (DemoCompany brand)', /\$Global:DemoCompany = 'Northwind Traders'/.test(ps1Text));
  check('generation wrote no telemetry files', telemetryArtifacts(g).length === 0, telemetryArtifacts(g).join(','));
  check('generated seeder carries no telemetry writer', !/DEMOIQ_TELEMETRY_LOGGER|log_event\.ps1/.test(ps1Text));
}

// ------------------------------------------------ 3b. legacy config (no new keys)
console.log('3b. legacy config without the keys: generates fine, still no telemetry');
{
  const legacy = {
    adminUpn: basePrefs.adminUpn, adminPwd: 'x',
    existingAppId: basePrefs.existingAppId, existingCertThumbprint: basePrefs.existingCertThumbprint,
    demoLanguage: 'English', industry: 'Technology & Software',
    companyName: '', customScenarioText: '', scenarioIndex: 1,
    contentTypes: ['emails', 'word']
  };
  [1, 2, 3, 4, 5].forEach(i => { legacy['u' + i + 'upn'] = 'user' + i + '@fakedemo.onmicrosoft.com'; legacy['u' + i + 'pwd'] = 'x'; });
  const legacyFile = tmpPath('legacy-cfg.json');
  writeConfig(legacy, legacyFile);
  const emptyPrefs = tmpPath('prefs-empty2.json');
  fs.writeFileSync(emptyPrefs, '{}', 'utf8');
  const g = runGenerate(['--prefs', emptyPrefs, '--from-config', legacyFile, '--emit', 'ps1', '--out-dir', tmpPath('out-legacy'), '--script-name', 'seed.ps1']);
  check('legacy config still generates (backward compat)', g.r.status === 0, 'exit=' + g.r.status + ' stderr=' + String(g.r.stderr).slice(0, 300));
  check('legacy config: no telemetry files written', telemetryArtifacts(g).length === 0);
}

// ---------------------------------------------------------- 4. static contracts
console.log('4. static contracts: builder / engine / template / generate.js');
{
  const builder = fs.readFileSync(path.join(root, 'DemoIQ-Config-Builder.html'), 'utf8');
  ['rossId', 'opportunityId', 'tpid'].forEach(k => {
    check('builder HIDDEN input exists: ' + k, new RegExp('<input type="hidden" id="' + k + '"').test(builder));
    check('builder serializes ' + k, builder.indexOf(k + ":str('" + k + "').trim().slice(0,64)") >= 0);
    check('builder prefill accepts ' + k + ' from the minted link', builder.indexOf("setIf('" + k + "',(q.get('" + k + "')||'').trim().slice(0,64))") >= 0);
  });
  check('builder KNOWN array carries the keys', /'companyName','rossId','opportunityId','tpid'/.test(builder));
  check('builder has NO visible engagement ask (chat step 0.5 is the only ask)', !/id="engWarn"/.test(builder) && !/Engagement tracking </.test(builder));
  check('engagement keys are NOT draft-content signals', !/textKeys=\[[^\]]*'rossId'/.test(builder));
  check('builder hydration clears absent keys (companyName pattern)', builder.indexOf("el('rossId').value = (p.rossId!=null) ? p.rossId : ''") >= 0);
  const serve = fs.readFileSync(path.join(root, 'scripts', 'serve_local.ps1'), 'utf8');
  check('serve_local never mints engagement prefill params (GitHub Copilot adaptation)', !/\$PrefillRossId|\$PrefillOpportunityId|\$PrefillTpid/.test(serve) && !/'(rossId|opportunityId|tpid)='/.test(serve));
  // Autofill hardening (admin-UPN cross-fill fix): no unmarked text+password pairs.
  check('adminUpn autocomplete=off', /id="adminUpn" autocomplete="off"/.test(builder));
  check('demo-user pwd rows autocomplete=new-password', /data-u="pwd" data-i="'\+i\+'" autocomplete="new-password"/.test(builder));
  check('demo-user upn rows autocomplete=off', /data-u="upn" data-i="'\+i\+'" autocomplete="off"/.test(builder));
  // Credential-free builder: no admin password, certificate or config password; plain
  // direct save only (no download), drafted configs through the token-gated GET /config.
  check('builder has no admin password input', !/id="adminPwd"/.test(builder));
  check('builder has no certificate thumbprint input', !/id="existingCertThumbprint"/.test(builder));
  check('builder has no config-password modal', !/pwModal|pwInput|promptPassword/.test(builder));
  check('builder never encrypts, decrypts or derives a key', !/crypto\.subtle|deriveKey/.test(builder));
  check('builder has no download fallback', !/\.download\s*=/.test(builder) && !/createObjectURL/.test(builder));
  check('builder App ID is read-only (from the deployment receipts)', /<input type="text" id="existingAppId" readonly/.test(builder) && !/app_reg_cert_setup/.test(builder));
  check('builder saves the plain config to /save with X-Save-Token', builder.indexOf('const body=JSON.stringify(buildConfigData());') >= 0 && /fetch\('\/save\?name='[\s\S]{0,200}?'X-Save-Token'/.test(builder));
  check('builder always writes adminPwd and existingCertThumbprint empty', builder.indexOf("adminPwd:''") >= 0 && builder.indexOf("existingCertThumbprint:''") >= 0);
  check('builder refuses an encrypted config with the convert hint', /p\.v==='cdx-enc-v1'/.test(builder) && /convert_config\.ps1/.test(builder));
  check('builder loads a drafted config through GET /config with X-Save-Token', /fetch\('\/config\?name='[\s\S]{0,200}?'X-Save-Token'/.test(builder) && /get\('config'\)/.test(builder));
  check('builder draft autosave drops every password field', /function draftSnapshot\(\)\{[\s\S]{0,200}?\/pwd\|password\|secret\|streamKey\|thumbprint\/i/.test(builder));
  check('builder has no Save & Encrypt wording', !/Save (&amp;|&) Encrypt|AES-256|PBKDF2/.test(builder));
  check('serve_local has no certificate prefill', !/PrefillCert|'cert='/.test(serve));
  check('serve_local prefills a drafted config (-PrefillConfig -> config=)', /\[string\]\$PrefillConfig/.test(serve) && /\('config='\s+\+ \[System\.Uri\]::EscapeDataString/.test(serve));
  check('serve_local serves the token-gated GET /config route', /AbsolutePath -eq '\/config'/.test(serve) && /Test-SaveToken \$req/.test(serve));
  check('serve_local /save accepts plain configs only and blanks the engine credentials', /Read-DemoIQConfigJson/.test(serve) && /foreach \(\$k in 'adminPwd', 'existingCertThumbprint'\)/.test(serve) && !/not an encrypted DemoIQ config/.test(serve));

  const engine = fs.readFileSync(engineHtml, 'utf8');
  ['rossId', 'opportunityId', 'tpid'].forEach(k => {
    check('engine hidden input exists: ' + k, new RegExp('<input type="hidden" id="' + k + '"').test(engine));
  });
  check('engine saveConfig persists the keys', /rossId: \(val\('rossId'\)\|\|''\)\.trim\(\)\.slice\(0,64\)/.test(engine));
  check('engine buildPromptContext never reads the engagement IDs', !/buildPromptContext[\s\S]{0,4000}v\('rossId'\)/.test(engine));

  const template = JSON.parse(fs.readFileSync(path.join(root, 'scripts', 'presets', 'DemoIQ_config.template.json'), 'utf8'));
  check('template documents the keys', ('rossId' in template) && ('opportunityId' in template) && ('tpid' in template) && /rossId\/opportunityId\/tpid/.test(template._notes));

  const gen = fs.readFileSync(generateJs, 'utf8');
  check('generate.js records no telemetry (no logEvent, no telemetry_lib)', !/logEvent\(|telemetry_lib/.test(gen));
  check('generate.js borrows the keys from-config', /'companyName', 'rossId', 'opportunityId', 'tpid', 'customScenarioText'/.test(gen));
}

try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (e) {}

console.log(failures === 0 ? 'ENGAGEMENT-FIELDS TESTS PASSED' : ('ENGAGEMENT-FIELDS TESTS FAILED: ' + failures));
process.exit(failures === 0 ? 0 : 1);

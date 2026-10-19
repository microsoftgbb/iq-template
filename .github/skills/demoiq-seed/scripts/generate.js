#!/usr/bin/env node
/*
 * DemoIQ skill generator (standalone Node, no browser).
 *
 * Reproduces exactly what DemoIQ.html does on "Download Script" + "Save Config":
 * it runs the HTML's OWN generatePs1() and the saveConfig() data-builder verbatim
 * (extracted from the file and executed in a vm sandbox with a preference-backed
 * DOM shim), then writes DemoIQ.ps1 and the plain, owner-only config JSON.
 * No Playwright, no browser. DemoIQ.html stays the single
 * source of truth for prompts, scenarios, personas, and languages.
 *
 * MODES
 *   Generate:
 *     node generate.js --html <DemoIQ.html> --prefs <prefs.json> --out-dir <dir>
 *          [--from-config <DemoIQ_config.json>] [--config-name DemoIQ_config.json]
 *          [--script-name DemoIQ.ps1] [--emit both|ps1|config]
 *   List (industries / scenarios / personas / languages, as JSON):
 *     node generate.js --html <DemoIQ.html> --list
 *
 *   --out-file <path>  (with --list, --emit prompts or --emit aop-context) writes the
 *     JSON to that file as UTF-8 and prints a one-line summary instead. Prefer it over
 *     shell redirection: Windows PowerShell 5.1 redirects as UTF-16.
 *
 * CONFIGS ARE PLAIN JSON: the GitHub Copilot adaptation keeps DemoIQ configs as plain,
 * owner-only files in the workspace, so --from-config needs no password and
 * --emit config writes plain JSON (owner-only). An encrypted (cdx-enc-v1) config is
 * refused: convert it once with the demoiq-seed skill.
 *
 * GITHUB COPILOT ADAPTATION: every generated seeder passes through
 * platform_patch.js (delegated sign-in, runs on Windows, macOS and Linux, no usage
 * telemetry), and this generator records no telemetry. The engine HTML is used
 * unmodified. The adaptation has no certificate and no stored admin password: the
 * administrator signs in for each run instead (scripts/admin_signin.ps1).
 *
 * prefs.json shape (all optional except adminUpn and existingAppId for a runnable script):
 *   {
 *     "adminUpn","existingAppId","speechResourceId",
 *     "demoLanguage":"English (US)",
 *     "industry":"Municipalities",
 *     "companyName":"",                  // optional; blank = the classic Contoso defaults
 *     "rossId":"", "opportunityId":"", "tpid":"",
 *                                        // upstream engagement metadata fields. The
 *                                        // GitHub Copilot adaptation never asks for or
 *                                        // transmits them; they only round-trip inside
 *                                        // the config for compatibility and are
 *                                        // NEVER used in prompts, generated content, file
 *                                        // names, or the AoP runbook.
 *     "scenarioIndex":1,                 // 1-4 (which pre-built scenario)
 *     "customScenarioText":"",
 *     "contentTypes":["emails","chats","channel","meetings","word","ppt","excel","aop"],
 *     "aopSurfaces":["outlook","teams","excel","ppt","word","agents","copilotapp","code","autopilot"],
 *     "aopAgents":["wxp","facilitator","interpreter","cowork","researcher","analyst"],
 *     "users":[{ "upn","pwd","persona","personaCustom" }, ... x5 ],
 *     "options":{ "emailThreads","emailReplies","emailThreaded","chatMessages",
 *                 "channelMessages","meetingCount","meetingRecurring","wordSections",
 *                 "pptSlides","excelRows","excelTables",
 *                 "wordDocs","pptDecks","excelWorkbooks" },   // file counts, default 1 each (max 5)
 *     "speechSpeedRate":1.2, "streamUrl":"", "streamKey":""
 *   }
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');
const { applyPlatformPatch, PlatformPatchError } = require('./platform_patch');

// POSIX pipes are asynchronous in Node: a large stdout write followed by
// process.exit() is cut off at the pipe buffer (~64 KB) on macOS/Linux. Make the
// pipe blocking so every JSON document arrives whole.
if (process.stdout._handle && typeof process.stdout._handle.setBlocking === 'function') {
  process.stdout._handle.setBlocking(true);
}

function parseArgs(argv) {
  const a = {};
  for (let i = 2; i < argv.length; i++) {
    const t = argv[i];
    if (t.startsWith('--')) {
      const key = t.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) { a[key] = true; }
      else { a[key] = next; i++; }
    }
  }
  return a;
}
const args = parseArgs(process.argv);
function need(name) {
  if (!args[name]) { console.error(`ERROR: missing required --${name}`); process.exit(2); }
  return args[name];
}

// Print (or, with --out-file, write) one JSON document, then exit.
function emitJson(obj, exitCode) {
  const text = JSON.stringify(obj, null, 2) + '\n';
  if (args['out-file']) {
    const file = path.resolve(String(args['out-file']));
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, text, 'utf8');
    const summary = { ok: obj.ok !== false, outFile: file, bytes: Buffer.byteLength(text, 'utf8') };
    if (Array.isArray(obj.keys)) summary.keys = obj.keys;
    if ('aop' in obj) summary.aop = obj.aop;
    if ('describeInChat' in obj) summary.describeInChat = obj.describeInChat;
    if ('language' in obj) summary.language = obj.language;
    process.stdout.write(JSON.stringify(summary) + '\n');
  } else {
    process.stdout.write(text);
  }
  process.exit(exitCode);
}

const htmlPath = path.resolve(need('html'));
let html;
try { html = fs.readFileSync(htmlPath, 'utf8'); }
catch (e) { console.error('ERROR: cannot read --html file: ' + e.message); process.exit(2); }
const listMode = !!args['list'];

// ---------- extract the authored JS ----------
// The real <script> body runs from the first opening tag to the LAST closing
// tag. Inner <script>/<\/script> are literal text inside template literals
// (closings are escaped as <\/script>), so this slice is exact.
const openIdx = html.indexOf('<script>');
if (openIdx < 0) { console.error('ERROR: no <script> found in HTML'); process.exit(3); }
const jsStart = openIdx + '<script>'.length;
const closeIdx = html.lastIndexOf('</script>');
if (closeIdx < 0 || closeIdx <= jsStart) { console.error('ERROR: no closing </script> found'); process.exit(3); }
const authoredJs = html.slice(jsStart, closeIdx);

// In-scope trailer: declared at the script's top level so it closes over all
// authored const/let/function declarations and can read/write _lastSegs.
const trailer = `
;(function(){
  try {
    globalThis.__demoiq_generate = function(){
      _lastSegs = getPromptSegments() || [];
      return generatePs1();
    };
    globalThis.__demoiq_configData = function(){
      // Verbatim reproduction of saveConfig()'s plaintext data object.
      const data = {
        adminUpn: val('adminUpn'), adminPwd: val('adminPwd'),
        existingAppId: val('existingAppId'), existingCertThumbprint: val('existingCertThumbprint'), speechResourceId: val('speechResourceId'),
        demoLanguage: val('demoLanguage'),
        speechSpeedRate: (typeof getSpeechSpeedRate === 'function') ? getSpeechSpeedRate() : 1.0,
        streamUrl: val('streamUrl'), streamKey: val('streamKey'),
        industry: val('industry'),
        companyName: (val('companyName')||'').trim(),
        rossId: (val('rossId')||'').trim().slice(0,64),
        opportunityId: (val('opportunityId')||'').trim().slice(0,64),
        tpid: (val('tpid')||'').trim().slice(0,64),
        customScenarioText: val('customScenarioText'),
        scenarioIndex: (function(){ for(let i=1;i<=4;i++){ const el=document.getElementById('industryScenario'+i); if(el&&el.checked) return i; } return null; })(),
        scenarioId: (function(){ for(let i=1;i<=4;i++){ const el=document.getElementById('industryScenario'+i); if(el&&el.checked) return el.getAttribute('data-scenario-id')||null; } return null; })(),
      };
      [1,2,3,4,5].forEach(function(i){
        data['u'+i+'upn'] = val('u'+i+'upn');
        data['u'+i+'pwd'] = val('u'+i+'pwd');
        data['u'+i+'persona'] = val('u'+i+'persona');
        data['u'+i+'personaCustom'] = val('u'+i+'personaCustom');
      });
      data.aopSurfaces = AOP_SURFACE_KEYS.filter(function(id){ const cb=$('aop_'+id); return cb && cb.checked; });
      data.aopAgents   = AOP_AGENT_KEYS.filter(function(id){ const cb=$('aop_agent_'+id); return cb && cb.checked; });
      return data;
    };
    globalThis.__demoiq_scenarioMap = function(){ return getIndustryScenarioMap(); };
    globalThis.__demoiq_personas = function(){
      return {
        universal: (typeof UNIVERSAL_PERSONAS !== 'undefined') ? UNIVERSAL_PERSONAS : [],
        byIndustry: (typeof INDUSTRY_PERSONAS !== 'undefined') ? INDUSTRY_PERSONAS : {}
      };
    };
    globalThis.__demoiq_aop = function(){
      return {
        surfaces: (typeof AOP_SURFACE_DEFS !== 'undefined') ? AOP_SURFACE_DEFS.map(function(s){return {id:s.id,heading:s.heading,implies:s.implies};}) : [],
        agents: (typeof AOP_AGENT_DEFS !== 'undefined') ? AOP_AGENT_DEFS.map(function(a){return {id:a.id,label:a.label};}) : []
      };
    };
    globalThis.__demoiq_officeMeta = function(){
      // Deterministic Office-file expectations for THIS config, read from the SAME
      // engine state generatePs1() bakes into the seeder: getScenarioFileNames()
      // for the primary (segment-0) file names, getContentOpts() for the per-type
      // file counts (wordDocs/pptDecks/excelWorkbooks), and the ct_* checkboxes
      // for selection. expected is 0 when the type is not selected.
      const names = (typeof getScenarioFileNames === 'function') ? getScenarioFileNames() : { word: '', ppt: '', excel: '' };
      const co = (typeof getContentOpts === 'function') ? getContentOpts() : {};
      const on = function(id){ const cb = $(id); return !!(cb && cb.checked); };
      const mk = function(sel, n, primary){
        return { selected: sel, expected: sel ? (n || 1) : 0, primaryName: sel ? (primary || null) : null };
      };
      return {
        word:  mk(on('ct_word'),  co.wordDocs,       names.word),
        ppt:   mk(on('ct_ppt'),   co.pptDecks,       names.ppt),
        excel: mk(on('ct_excel'), co.excelWorkbooks, names.excel),
        excelTables: (co.excelTables != null ? co.excelTables : null)
      };
    };
  } catch (e) {
    globalThis.__demoiq_trailer_error = String(e && e.stack || e);
  }
})();
`;

// ---------- DOM shim ----------
const FORM_STATE = Object.create(null);
function setVal(id, v) { (FORM_STATE[id] || (FORM_STATE[id] = {})).value = (v == null ? '' : String(v)); }
function setChecked(id, b) { (FORM_STATE[id] || (FORM_STATE[id] = {})).checked = !!b; }
function setAttr(id, name, v) { (FORM_STATE[id] || (FORM_STATE[id] = {}))['attr_' + name] = v; }

function makeStyle() { return new Proxy({}, { get() { return ''; }, set() { return true; } }); }
const NODE_CACHE = Object.create(null);
// Drift detection: the shim is deliberately permissive so generatePs1() runs headless,
// but permissiveness HIDES upstream DemoIQ.html changes (a new element id or DOM property
// silently reads as empty/truthy and produces a plausible-but-wrong seeder). Log the
// first touch of anything unshimmed to stderr so drift is visible at generation time.
const WARNED_PROPS = new Set();
function warnUnshimmed(kind, name) {
  const key = kind + ':' + name;
  if (WARNED_PROPS.has(key)) return;
  WARNED_PROPS.add(key);
  console.error('[shim] WARN: unshimmed ' + kind + ' "' + name + '" accessed - if DemoIQ.html recently changed, the shim may need updating.');
}

function makeNode(id) {
  if (NODE_CACHE[id]) return NODE_CACHE[id];
  const st = FORM_STATE[id] || (FORM_STATE[id] = {});
  const base = {
    id,
    get value() { return st.value != null ? String(st.value) : ''; },
    set value(x) { st.value = x; },
    get checked() { return !!st.checked; },
    set checked(x) { st.checked = !!x; },
    getAttribute(n) { const k = 'attr_' + n; return st[k] != null ? st[k] : null; },
    setAttribute(n, v) { st['attr_' + n] = v; },
    hasAttribute(n) { return ('attr_' + n) in st; },
    removeAttribute(n) { delete st['attr_' + n]; },
    style: makeStyle(),
    dataset: new Proxy({}, { get() { return undefined; }, set() { return true; } }),
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild() {}, removeChild() {}, insertBefore() {}, replaceChild() {},
    insertAdjacentHTML() {}, append() {}, prepend() {}, remove() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
    focus() {}, blur() {}, click() {}, scrollIntoView() {}, select() {},
    closest() { return null; }, matches() { return false; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0, right: 0, bottom: 0 }; },
    innerHTML: '', outerHTML: '', textContent: '', innerText: '', placeholder: '',
    children: [], childNodes: [], options: [], files: [],
    parentNode: null, parentElement: null, nextSibling: null, firstChild: null,
  };
  const proxy = new Proxy(base, {
    get(t, p) {
      if (p in t) return Reflect.get(t, p);
      if (typeof p === 'symbol') return undefined;
      warnUnshimmed('node property', String(p));
      return function () { return undefined; };
    },
    set(t, p, v) { t[p] = v; return true; },
    has() { return true; },
  });
  NODE_CACHE[id] = proxy;
  return proxy;
}
const documentShim = {
  getElementById(id) {
    // Never returns null (existence guards must take the "exists" branch), but flag ids
    // that do not appear in the HTML source - a new upstream id is the classic drift.
    // Ids pre-seeded into FORM_STATE by this script (scenario radios, speaker rows) are
    // intentional stand-ins for dynamically-created elements - not drift.
    if (!NODE_CACHE[id] && !FORM_STATE[id] &&
        html.indexOf('id="' + id + '"') < 0 && html.indexOf("id='" + id + "'") < 0) {
      warnUnshimmed('element id', id);
    }
    return makeNode(id);
  },
  querySelector() { return null; }, querySelectorAll() { return []; },
  getElementsByClassName() { return []; }, getElementsByTagName() { return []; },
  createElement() { return makeNode('__el_' + Math.random()); },
  createTextNode() { return makeNode('__txt'); },
  createDocumentFragment() { return makeNode('__frag'); },
  addEventListener() {}, removeEventListener() {},
  body: makeNode('__body'), head: makeNode('__head'), documentElement: makeNode('__html'),
  cookie: '', readyState: 'complete',
};

// Sandbox console: route log/info/warn/debug to STDERR. The agent parses this
// script's stdout as JSON - a future console.log added upstream in DemoIQ.html
// must never be able to pollute the machine-readable output.
const consoleShim = {
  log: (...a) => console.error(...a),
  info: (...a) => console.error(...a),
  warn: (...a) => console.error(...a),
  debug: (...a) => console.error(...a),
  error: (...a) => console.error(...a),
};

const sandbox = {
  console: consoleShim,
  document: documentShim,
  navigator: { language: 'en-US', userAgent: 'node', clipboard: { writeText() { return Promise.resolve(); } } },
  location: { href: '', hostname: 'localhost', search: '', hash: '' },
  crypto: crypto.webcrypto,
  btoa: (s) => Buffer.from(String(s), 'binary').toString('base64'),
  atob: (s) => Buffer.from(String(s), 'base64').toString('binary'),
  TextEncoder, TextDecoder, URL,
  Blob: class { constructor() {} },
  setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  requestAnimationFrame: () => 0, cancelAnimationFrame: () => {},
  alert: () => {}, confirm: () => true, prompt: () => null,
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }),
  getComputedStyle: () => makeStyle(),
  localStorage: { getItem() { return null; }, setItem() {}, removeItem() {}, clear() {} },
  sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {}, clear() {} },
  showToast: () => {},
};
sandbox.window = sandbox; sandbox.globalThis = sandbox; sandbox.self = sandbox;
vm.createContext(sandbox);

function evalAuthored() {
  try {
    vm.runInContext(authoredJs + trailer, sandbox, { filename: 'DemoIQ.html#script', timeout: 30000 });
  } catch (e) {
    console.error('ERROR evaluating authored JS:', e && e.stack || e); process.exit(4);
  }
  if (sandbox.__demoiq_trailer_error) {
    console.error('ERROR in trailer:', sandbox.__demoiq_trailer_error); process.exit(4);
  }
}

// Parse <select id="demoLanguage"> option values straight from the HTML.
function parseLanguages() {
  const m = html.match(/<select[^>]*id="demoLanguage"[\s\S]*?<\/select>/i);
  if (!m) return [];
  const opts = m[0].match(/<option[^>]*>[\s\S]*?<\/option>/gi) || [];
  return opts.map(o => {
    const valM = o.match(/value="([^"]*)"/i);
    const txtM = o.replace(/<[^>]+>/g, '').trim();
    return { value: valM ? valM[1] : txtM, label: txtM };
  }).filter(o => o.label);
}

// ===================== LIST MODE =====================
if (listMode) {
  evalAuthored();
  const map = sandbox.__demoiq_scenarioMap ? sandbox.__demoiq_scenarioMap() : {};
  const personas = sandbox.__demoiq_personas ? sandbox.__demoiq_personas() : { universal: [], byIndustry: {} };
  const aop = sandbox.__demoiq_aop ? sandbox.__demoiq_aop() : { surfaces: [], agents: [] };
  const industries = Object.keys(map).map(name => ({
    industry: name,
    scenarios: (map[name] || []).map((s, i) => ({ index: i + 1, id: s.id, title: s.title, desc: s.desc, excelFile: s.excelFile, wordFile: s.wordFile, pptFile: s.pptFile })),
    personas: (personas.byIndustry[name] || []).map(p => ({ name: p.name, desc: p.desc })),
  }));
  emitJson({
    ok: true,
    industries,
    universalPersonas: (personas.universal || []).map(p => ({ name: p.name, desc: p.desc })),
    aopSurfaces: aop.surfaces,
    aopAgents: aop.agents,
    languages: parseLanguages(),
  }, 0);
}

// ===================== GENERATE MODE =====================
const prefsPath = path.resolve(need('prefs'));
const outDir = path.resolve(args['out-dir'] || '.');
const configName = args['config-name'] || 'DemoIQ_config.json';
const scriptName = args['script-name'] || 'DemoIQ.ps1';
const emit = args['emit'] || 'both';
const EMIT_MODES = ['prompts', 'aop-context', 'ps1', 'config', 'both'];
if (!EMIT_MODES.includes(emit)) {
  // A typo here used to fall through every branch and print {ok:true, outputs:{}} with
  // exit 0 - a silent no-op the agent read as success. Fail loudly instead.
  console.error('ERROR: unknown --emit value "' + emit + '" (expected one of: ' + EMIT_MODES.join(', ') + ')');
  process.exit(2);
}
let prefs;
try { prefs = JSON.parse(fs.readFileSync(prefsPath, 'utf8')); }
catch (e) { console.error('ERROR: cannot read/parse --prefs file: ' + e.message); process.exit(2); }
// The prefs exactly as given (before any field is borrowed from --from-config), for the
// run folder's re-seed copy below.
const prefsAsGiven = JSON.parse(JSON.stringify(prefs));

// Office-file expectations from the engine (see __demoiq_officeMeta in the trailer).
// Best-effort by design: metadata consumers (validate_pasted.js, verify_seeded_files.js)
// all degrade gracefully when it is absent, so an upstream engine change here should
// warn, not block generation.
function officeMetaFromSandbox() {
  try {
    if (typeof sandbox.__demoiq_officeMeta === 'function') return sandbox.__demoiq_officeMeta();
  } catch (e) {
    console.error('[warn] could not derive Office file metadata: ' + (e && e.message || e));
  }
  return null;
}

// Call a trailer-defined sandbox function with call-time errors reported as the
// documented exit 4 (an upstream rename of generatePs1/getPromptSegments/etc. used
// to escape as a raw Node stack trace with a generic exit 1).
function callSandbox(fnName) {
  const fn = sandbox[fnName];
  if (typeof fn !== 'function') {
    console.error('ERROR: sandbox function ' + fnName + ' is not defined - DemoIQ.html layout changed?');
    process.exit(4);
  }
  try { return fn(); }
  catch (e) {
    console.error('ERROR calling ' + fnName + ':', (e && e.stack) || e);
    process.exit(4);
  }
}

// The engine's generatePs1() emits a seeder only when an admin password and a
// certificate thumbprint are filled in, yet writes neither into the script (the
// seeder reads credentials from the config at run time). The GitHub Copilot
// adaptation has neither: the administrator signs in per run and there is no
// certificate. Satisfy that gate with inert placeholders for this call only, so they
// never reach a written config or run_identity.json.
const SEEDER_GATE_PLACEHOLDERS = { adminPwd: 'delegated-sign-in', existingCertThumbprint: '0'.repeat(40) };
function generateSeederText() {
  const filled = [];
  for (const [id, placeholder] of Object.entries(SEEDER_GATE_PLACEHOLDERS)) {
    if (!String((FORM_STATE[id] && FORM_STATE[id].value) || '').trim()) { setVal(id, placeholder); filled.push(id); }
  }
  try { return callSandbox('__demoiq_generate'); }
  finally { filled.forEach(id => setVal(id, '')); }
}

// Optionally hydrate credential/user fields from an EXISTING plain config so the user
// does not have to retype them. Nothing sensitive is printed. Prefs.json design choices
// always take priority; credential/user fields are only borrowed when absent from prefs.
if (args['from-config']) {
  const fromPath = path.resolve(args['from-config']);
  let src;
  try { src = JSON.parse(fs.readFileSync(fromPath, 'utf8')); }
  catch (e) { console.error('ERROR: cannot read --from-config file:', e.message); process.exit(2); }
  if (!src || typeof src !== 'object' || Array.isArray(src)) {
    console.error('ERROR: --from-config is not a DemoIQ config object.'); process.exit(2);
  }
  if (src.v === 'cdx-enc-v1') {
    console.error('ERROR: --from-config is an encrypted DemoIQ config. The GitHub Copilot adaptation uses plain, owner-only configs: convert it once with the demoiq-seed skill, then re-run.');
    process.exit(2);
  }
  // Borrow ID fields if not explicitly set in prefs. The admin password and certificate
  // thumbprint are never borrowed: the GitHub Copilot adaptation stores neither.
  ['adminUpn', 'existingAppId', 'speechResourceId',
    'streamUrl', 'streamKey'].forEach(k => { if (prefs[k] == null || prefs[k] === '') prefs[k] = src[k]; });
  if (prefs.speechSpeedRate == null && src.speechSpeedRate != null) prefs.speechSpeedRate = src.speechSpeedRate;
  // Borrow the full demo configuration (scenario, language, content selections,
  // AoP, counts) when prefs didn't specify them, so a saved config can drive a
  // run on its own. Prefs always take priority.
  // Capture whether prefs supplied their OWN custom scenario BEFORE we borrow one
  // from the config: if they did (e.g. DemoIQ-CoworkGuide always sets customScenarioText),
  // that custom scenario IS the scenario - do NOT also inherit the config's built-in
  // scenarioIndex/scenarioId, or the config's scenario bleeds in as a second (primary)
  // scenario alongside the custom one.
  const prefsHadOwnScenarioText = (typeof prefs.customScenarioText === 'string' && prefs.customScenarioText.trim() !== '');
  ['demoLanguage', 'industry', 'companyName', 'rossId', 'opportunityId', 'tpid', 'customScenarioText'].forEach(k => { if (prefs[k] == null || prefs[k] === '') prefs[k] = src[k]; });
  if (!prefsHadOwnScenarioText) {
    if (prefs.scenarioIndex == null && src.scenarioIndex != null) prefs.scenarioIndex = src.scenarioIndex;
    if (prefs.scenarioId == null && src.scenarioId != null) prefs.scenarioId = src.scenarioId;
  }
  if (!Array.isArray(prefs.contentTypes) || prefs.contentTypes.length === 0) {
    // The skill's Config Builder saves contentTypes as an ARRAY of type names
    // (like prefs.json), while the engine page's own Save & Encrypt writes an OBJECT
    // map ({ emails:true, ... }). Accept either so a saved config can drive a run:
    // an array is borrowed as-is; an object is flattened to its truthy keys.
    if (Array.isArray(src.contentTypes)) prefs.contentTypes = src.contentTypes;
    else if (src.contentTypes && typeof src.contentTypes === 'object') {
      prefs.contentTypes = Object.keys(src.contentTypes).filter(k => src.contentTypes[k]);
    }
  }
  if ((!Array.isArray(prefs.aopSurfaces) || prefs.aopSurfaces.length === 0) && Array.isArray(src.aopSurfaces)) prefs.aopSurfaces = src.aopSurfaces;
  if ((!Array.isArray(prefs.aopAgents) || prefs.aopAgents.length === 0) && Array.isArray(src.aopAgents)) prefs.aopAgents = src.aopAgents;
  if (prefs.describeInChat == null && src.describeInChat != null) prefs.describeInChat = src.describeInChat;
  if (src.options && typeof src.options === 'object') {
    prefs.options = Object.assign({}, src.options, prefs.options || {}); // prefs keys win, config fills the rest
  }
  // Borrow demo users (upn/pwd/persona/personaCustom) when prefs didn't specify them.
  if (!Array.isArray(prefs.users) || prefs.users.length === 0) {
    prefs.users = [];
    for (let i = 1; i <= 5; i++) {
      prefs.users.push({
        upn: src['u' + i + 'upn'] || '', pwd: src['u' + i + 'pwd'] || '',
        persona: src['u' + i + 'persona'] || '', personaCustom: src['u' + i + 'personaCustom'] || '',
      });
    }
  }
}

// preference -> form-state
// The administrator signs in for each run (scripts/admin_signin.ps1) and the app has no
// certificate, so the upstream admin-password and thumbprint fields always stay empty:
// neither can reach a written config, whatever prefs or an older config contain.
setVal('adminUpn', prefs.adminUpn);
setVal('adminPwd', '');
setVal('existingAppId', prefs.existingAppId);
setVal('existingCertThumbprint', '');
setVal('speechResourceId', prefs.speechResourceId);
setVal('demoLanguage', prefs.demoLanguage || 'English');
setVal('industry', prefs.industry || 'Technology & Software');
setVal('companyName', prefs.companyName || '');
// Engagement metadata (config round-trip only - the engine never reads these into
// prompts, content, file names, or the AoP runbook, and nothing transmits them).
setVal('rossId', prefs.rossId || '');
setVal('opportunityId', prefs.opportunityId || '');
setVal('tpid', prefs.tpid || '');
setVal('customScenarioText', prefs.customScenarioText || '');
setVal('streamUrl', prefs.streamUrl || '');
setVal('streamKey', prefs.streamKey || '');
setVal('speechSpeedRange', prefs.speechSpeedRate != null ? prefs.speechSpeedRate : 1.2);

const PREFIXES = ['AdilE', 'BillieV', 'CoraT', 'CoreyG', 'DaichiM'];
const DEFAULT_SPEAKER_NAMES = ['Adil Eli', 'Billie Vester', 'Cora Thomas', 'Corey Gray', 'Daichi Maruyama'];
const DEFAULT_SPEAKER_VOICES = [
  'en-US-AndrewMultilingualNeural',
  'en-US-AvaMultilingualNeural',
  'en-US-EmmaMultilingualNeural',
  'en-US-BrianMultilingualNeural',
  'en-US-DavisNeural',
];
// Derive a friendly first name from a UPN local part, e.g. "DakotaS" -> "Dakota",
// "KaiC" -> "Kai". Strips a trailing run of uppercase letters (surname initials).
function firstNameFromUpn(upn) {
  const local = String(upn || '').split('@')[0];
  if (!local) return '';
  const stripped = local.replace(/[A-Z]+$/, '');
  return (stripped || local).replace(/[._-].*$/, '');
}

const adminUpn = String(prefs.adminUpn || '');
const at = adminUpn.indexOf('@');
const domain = at > -1 ? adminUpn.slice(at + 1) : 'tenant.onmicrosoft.com';
const usersArr = Array.isArray(prefs.users) ? prefs.users : [];
const prefVoices = Array.isArray(prefs.voices) ? prefs.voices : [];
const derivedUpns = [];
for (let i = 0; i < 5; i++) {
  const u = usersArr[i] || {};
  // Blank UPN -> derive <CDX prefix>@<admin domain>, matching preflight_credentials.ps1.
  // The Config Builder has no preset demo-user names and requires all five UPNs to be
  // typed before it saves, so this derivation only fires for legacy/engine-saved
  // configs - and it is never silent: the derived names are the old CDX demo-tenant
  // defaults, which do NOT exist on every tenant (Diamond, customer-shaped), so a
  // warning below names exactly what was invented.
  const hasExplicitUpn = !!(u.upn && String(u.upn).trim());
  const upn = hasExplicitUpn ? u.upn : (PREFIXES[i] + '@' + domain);
  if (!hasExplicitUpn) derivedUpns.push(upn);
  setVal('u' + (i + 1) + 'upn', upn);
  setVal('u' + (i + 1) + 'pwd', u.pwd || '');
  setVal('u' + (i + 1) + 'persona', u.persona || '');
  setVal('u' + (i + 1) + 'personaCustom', u.personaCustom || '');
  // Meeting-audio speaker rows (spkName{i}/spkVoice{i}, 0-based) feed the
  // HTML's getSpeakers() -> $SpeakerVoiceMap in the generated PS1. Populate them
  // so audio synthesis is not skipped. Name = demo user's first name (best match
  // for transcript speaker labels); voice = pref or default multilingual voice.
  const spkName = firstNameFromUpn(upn) || DEFAULT_SPEAKER_NAMES[i];
  const spkVoice = (prefVoices[i] && String(prefVoices[i]).trim()) ? prefVoices[i] : DEFAULT_SPEAKER_VOICES[i];
  setVal('spkName' + i, spkName);
  setVal('spkVoice' + i, spkVoice);
}
if (derivedUpns.length) {
  console.error('[prefs] WARN: ' + derivedUpns.length + ' demo-user UPN(s) were not set in the prefs/config and were derived from the admin domain: '
    + derivedUpns.join(', ')
    + '. These are the standard CDX demo-tenant accounts - if this tenant provisions different users, re-save the config with the real UPNs (the Flow 1c.5 credential preflight rejects any account that does not exist).');
}

const ctSet = new Set((prefs.contentTypes || []).map(String));
setChecked('ct_emails', ctSet.has('emails'));
setChecked('ct_chats', ctSet.has('chats'));
setChecked('ct_channel', ctSet.has('channel'));
setChecked('ct_meetings', ctSet.has('meetings'));
setChecked('ct_word', ctSet.has('word'));
setChecked('ct_ppt', ctSet.has('ppt'));
setChecked('ct_excel', ctSet.has('excel'));
setChecked('ct_aop', ctSet.has('aop'));
setChecked('ct_audiotranscript', ctSet.has('meetings') || ctSet.has('audiotranscript'));

const surfSet = new Set((prefs.aopSurfaces || []).map(String));
['outlook', 'teams', 'excel', 'ppt', 'word', 'agents', 'copilotapp', 'code', 'autopilot'].forEach(id => setChecked('aop_' + id, surfSet.has(id)));
const agSet = new Set((prefs.aopAgents || []).map(String));
['wxp', 'facilitator', 'interpreter', 'cowork', 'researcher', 'analyst'].forEach(id => setChecked('aop_agent_' + id, agSet.has(id)));

const opt = prefs.options || {};
const optMap = {
  emailThreads: 'opt_emailThreads', emailReplies: 'opt_emailReplies',
  chatMessages: 'opt_chatMessages', channelMessages: 'opt_channelMessages',
  meetingCount: 'opt_meetingCount', wordSections: 'opt_wordSections',
  pptSlides: 'opt_pptSlides', excelRows: 'opt_excelRows', excelTables: 'opt_excelTables',
  wordDocs: 'opt_wordDocs', pptDecks: 'opt_pptDecks', excelWorkbooks: 'opt_excelWorkbooks',
};
// Counts are whole numbers >= 1 (the three file counts additionally cap at 5).
// A config saved by an older builder can carry a negative/zero/fractional count
// (a spinner scrolled to -3 was once saved verbatim by an older builder); passing that through would generate degenerate prompts/content, so
// drop the invalid value (the engine default applies) and say so on stderr.
const fileCountKeys = new Set(['wordDocs', 'pptDecks', 'excelWorkbooks']);
for (const k of Object.keys(optMap)) {
  if (opt[k] == null) continue;
  let v = Math.round(Number(opt[k]));
  if (!Number.isFinite(v) || v < 1) {
    console.error('[prefs] WARN: options.' + k + ' = ' + JSON.stringify(opt[k]) + ' is not a valid count (whole number >= 1) - ignoring it; the engine default applies.');
    continue;
  }
  if (fileCountKeys.has(k) && v > 5) {
    console.error('[prefs] WARN: options.' + k + ' = ' + JSON.stringify(opt[k]) + ' exceeds the 5-file max - clamping to 5.');
    v = 5;
  }
  setVal(optMap[k], v);
}
if (opt.emailThreaded != null) setChecked('opt_emailThreaded', opt.emailThreaded);
if (opt.meetingRecurring != null) setChecked('opt_meetingRecurring', opt.meetingRecurring);

const scenarioIndex = Number(prefs.scenarioIndex || 0);
for (let i = 1; i <= 4; i++) setChecked('industryScenario' + i, i === scenarioIndex);

evalAuthored();

// fill the chosen scenario radio's data-scenario-id from the authored map
if (scenarioIndex >= 1 && scenarioIndex <= 4 && typeof sandbox.__demoiq_scenarioMap === 'function') {
  try {
    const map = sandbox.__demoiq_scenarioMap();
    const list = map[prefs.industry || 'Technology & Software'] || [];
    const sc = list[scenarioIndex - 1];
    if (sc && sc.id) setAttr('industryScenario' + scenarioIndex, 'data-scenario-id', sc.id);
  } catch (_) { /* non-fatal */ }
}

// Emit the per-key prompts the seeder would use, recovered verbatim from a
// throwaway generatePs1() run ($Prompt_<key>='<b64>'). This reuses the engine's
// own prompt authoring (each prompt already carries that key's schema/format) so
// an agent can pre-generate the content and feed it back via --pasted. {{LANGUAGE}}
// is resolved to the configured demo language. Requires credentials (via prefs or
// --from-config) because generatePs1() returns null without them.
if (emit === 'prompts') {
  const ps1 = generateSeederText();
  if (ps1 == null || ps1 === '') {
    console.error('ERROR: generatePs1() returned null/empty - adminUpn and existingAppId are required (pass --from-config or set them in prefs).');
    process.exit(5);
  }
  const lang = prefs.demoLanguage || 'English';
  const prompts = {};
  const re = /\$Prompt_([A-Za-z0-9_]+)\s*=\s*'([A-Za-z0-9+/=]*)'/g;
  let mm;
  while ((mm = re.exec(ps1)) !== null) {
    if (!mm[2]) continue;
    let text = Buffer.from(mm[2], 'base64').toString('utf8');
    text = text.split('{{LANGUAGE}}').join(lang);
    prompts[mm[1]] = text;
  }
  // describeInChat: the user ticked "Describe in your own words" in the Config
  // Builder (no preset picked). The agent should gather the scenario + seed content
  // in chat, synthesize a customScenarioText into prefs, then re-dump and author.
  const describeInChat = !!prefs.describeInChat;
  // #P2-5 - report whether the seeder will generate an AoP runbook, derived from the
  // SAME flag the seeder reads (the ct_aop content-type checkbox). The AoP runbook is
  // built at PS1 runtime (not from a $Prompt_<key>), so it never appears in keys[];
  // surface it explicitly so the skill's "is AoP on?" decision matches the seeder.
  const aop = ctSet.has('aop');
  // Machine-readable Office expectations (per-type expected file count + the exact
  // primary file name), so downstream validation (validate_pasted.js --prompts) can
  // check the authored multi-file content against THIS config instead of guessing
  // from prompt text. Defensive: a missing engine helper degrades to null (with a
  // stderr warning) rather than blocking the prompts dump.
  const officeFiles = officeMetaFromSandbox();
  emitJson({ ok: true, language: lang, describeInChat, aop, officeFiles, keys: Object.keys(prompts), prompts }, 0);
}

// Optionally inject pre-generated ("pasted") Copilot responses so the engine's
// pasted-response path embeds them and SKIPS the runtime Copilot API for those
// keys. The map is { promptKey: responseText } (e.g. { "comm_email": "...cdx_seed_json..." }).
// window === sandbox, and the authored JS already ran (resetting _pastedResponses
// to {}), so we set it here, right before generatePs1() reads it via getPastedResponseB64.
let pastedMap = null;
if (args['pasted']) {
  try { pastedMap = JSON.parse(fs.readFileSync(path.resolve(args['pasted']), 'utf8')); }
  catch (e) { console.error('ERROR: cannot read --pasted file:', e.message); process.exit(2); }
  if (pastedMap && typeof pastedMap === 'object') {
    sandbox._pastedResponses = Object.assign(sandbox._pastedResponses || {}, pastedMap);
    console.error('[pasted] injected keys: ' + Object.keys(pastedMap).join(', '));
  }
}
// AoP is built at PS1 runtime (not in promptB64map), so a pasted "aop" key cannot be
// applied via the engine's pasted-response path. Instead we post-process the emitted
// seeder: guard the runtime AoP Copilot call and feed the pre-generated response in.
const pastedAop = (pastedMap && typeof pastedMap === 'object' && pastedMap.aop && String(pastedMap.aop).trim())
  ? String(pastedMap.aop) : null;

// Post-process the emitted seeder to honour a pre-generated ("pasted") AoP runbook
// WITHOUT modifying DemoIQ.html: set a dedicated flag, skip the runtime AoP API call,
// and assign the pasted text to $Global:AopResponse so the engine renders it as usual.
// Both anchor lines are unique in the generated seeder (verified). Uses replacer
// functions so '$' sequences in the replacement are not treated as match references.
function applyPastedAop(ps1, aopText) {
  const b64 = Buffer.from(aopText, 'utf8').toString('base64');
  const callLine = 'Invoke-CopilotPromptsParallel -Requests $aopReqs -MaxConcurrency 3';
  const assignLine = '$Global:AopResponse = $aopFullResponse.Trim()';
  if (ps1.indexOf(callLine) < 0 || ps1.indexOf(assignLine) < 0) {
    // FATAL, not a warning: the agent explicitly authored an AoP runbook. Silently
    // dropping it (and letting a live Copilot call replace it at runtime) is content
    // loss the user approved in the preview. Fail so the drift gets fixed.
    console.error('ERROR: AoP guard anchors not found in seeder - pasted aop CANNOT be applied (engine layout changed?). Update applyPastedAop anchors in generate.js.');
    process.exit(9);
  }
  ps1 = ps1.replace(callLine, () =>
    "$Global:AopPasted = '" + b64 + "'\n" +
    'if (-not $Global:AopPasted) { ' + callLine + ' }');
  ps1 = ps1.replace(assignLine, () =>
    'if ($Global:AopPasted) { $Global:AopResponse = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($Global:AopPasted)) } ' +
    'else { ' + assignLine + ' }');
  console.error('[pasted] aop applied: guarded runtime AoP call, injected ' + b64.length + ' b64 chars.');
  return ps1;
}

// Emit the AoP authoring context (the engine's own format rules + Microsoft article
// catalog + whitelisted surface headings) recovered from a throwaway seeder, so an
// agent can author a parseable AoP runbook and inject it via --pasted. The AoP is
// built at PS1 runtime from seeded content; the $aopSeededBlock / $aopScenarioLines /
// $aopIndustry tokens in formatRules are runtime placeholders the agent fills from the
// content it already generated (and the chosen industry).
if (emit === 'aop-context') {
  const ps1 = generateSeederText();
  if (ps1 == null || ps1 === '') {
    console.error('ERROR: generatePs1() returned null/empty - adminUpn and existingAppId are required (pass --from-config or set them in prefs).');
    process.exit(5);
  }
  const lang = prefs.demoLanguage || 'English';
  const grab = (varName) => {
    const re = new RegExp('\\$' + varName + '\\s*=\\s*@"\\r?\\n([\\s\\S]*?)\\r?\\n"@');
    const m = ps1.match(re);
    return m ? m[1] : null;
  };
  let formatRules = grab('aopPromptText');
  const catRe = /\$(?:_catHdr|_cat\w+)\s*=\s*@"\r?\n([\s\S]*?)\r?\n"@/g;
  const catParts = [];
  let cm;
  while ((cm = catRe.exec(ps1)) !== null) catParts.push(cm[1]);
  const catalog = catParts.join('\n');
  const surfaceHeadings = Array.from(new Set((catalog.match(/^##\s+(.+)$/gm) || []).map(s => s.replace(/^##\s+/, '').trim())));
  if (formatRules) formatRules = formatRules.split('{{LANGUAGE}}').join(lang);
  // Exit 8: a distinct code for a missing AoP format catalog, so an agent never mistakes
  // it for a problem with the config.
  emitJson({ ok: !!formatRules, language: lang, surfaceHeadings, formatRules, catalog }, formatRules ? 0 : 8);
}

const outputs = {};
if (emit === 'both' || emit === 'ps1') {
  let ps1 = generateSeederText();
  if (ps1 == null || ps1 === '') {
    console.error('ERROR: generatePs1() returned null/empty. A required field is missing ' +
      '(adminUpn, existingAppId). Provide them in prefs.json or pass --from-config.');
    process.exit(5);
  }
  if (pastedAop) ps1 = applyPastedAop(ps1, pastedAop);

  // GitHub Copilot adaptation: make the seeder run on Windows, macOS and Linux and
  // drop its usage telemetry. A required anchor that no longer matches the engine
  // fails here (exit 10) instead of producing a seeder that breaks mid-run.
  let platformPatch;
  try {
    platformPatch = applyPlatformPatch(ps1);
    ps1 = platformPatch.ps1;
  } catch (e) {
    if (e instanceof PlatformPatchError) {
      console.error('ERROR: cross-platform seeder patch failed: ' + e.message);
      process.exit(10);
    }
    throw e;
  }
  console.error('[platform] patches applied: ' + platformPatch.applied.join(', ') +
    (platformPatch.skipped.length ? ' (optional, not applicable: ' + platformPatch.skipped.join(', ') + ')' : ''));

  // --- Sanity checks: a shim gap or partial generatePs1() result must fail HERE, not
  // 20 minutes into a seed. A minimal-content seeder measures ~310 KB and a full one
  // ~1 MB (both with the landmarks below); anything much smaller or missing them is
  // plausible-but-wrong output.
  const MIN_PS1_BYTES = 250 * 1024;
  const ps1Size = Buffer.byteLength(ps1, 'utf8');
  const REQUIRED_MARKERS = ['param(', 'Invoke-CopilotPromptsParallel', 'Process-CopilotAnswer'];
  const missingMarkers = REQUIRED_MARKERS.filter(m => ps1.indexOf(m) < 0);
  if (ps1Size < MIN_PS1_BYTES || missingMarkers.length) {
    console.error('ERROR: generated seeder failed sanity checks (' + ps1Size + ' bytes' +
      (missingMarkers.length ? ', missing markers: ' + missingMarkers.join(', ') : '') +
      '). The DOM shim likely no longer matches DemoIQ.html - do NOT run this output.');
    process.exit(4);
  }
  // --- Verify every pasted key was actually consumed. The engine only embeds pasted
  // responses for ENABLED content types; a pasted key whose type is off vanishes
  // silently while the stderr log still says "injected". Fail so the mismatch between
  // the authored map and the config's contentTypes gets fixed before seeding.
  if (pastedMap && typeof pastedMap === 'object') {
    const unconsumed = Object.keys(pastedMap)
      .filter(k => k !== 'aop')
      .filter(k => ps1.indexOf('$PastedResponse_' + k) < 0);
    if (unconsumed.length) {
      console.error('ERROR: pasted key(s) not consumed by the seeder: ' + unconsumed.join(', ') +
        '. Their content type(s) are not enabled in this config/prefs - the authored content would silently not seed. ' +
        'Enable the matching contentTypes (or drop the keys) and regenerate.');
      process.exit(9);
    }
  }

  fs.mkdirSync(outDir, { recursive: true });
  const ps1Path = path.join(outDir, scriptName);
  // BOM parity with the browser's Download Script (DemoIQ.html prepends EF BB BF so
  // Windows PowerShell 5.1 parses multi-byte characters correctly). Atomic write:
  // never leave a truncated seeder that a later step might execute.
  const ps1Tmp = ps1Path + '.tmp';
  fs.writeFileSync(ps1Tmp, String.fromCharCode(0xFEFF) + ps1, 'utf8');
  fs.renameSync(ps1Tmp, ps1Path);
  outputs.ps1 = ps1Path; outputs.ps1Bytes = Buffer.byteLength(ps1, 'utf8');
  if (pastedAop) outputs.pastedAopApplied = true;
  // Persist the run's REUSABLE inputs beside the seeder (both non-secret):
  //  - demoiq-pasted.json: the authored content map. It is baked into the PS1 above, but
  //    a machine-readable copy lets a later "re-seed this run" regenerate WITHOUT
  //    re-authoring 10-15 minutes of content (the map is demo content only - no
  //    credentials live in it).
  //  - run_identity.json: the app identity IDs (no secrets). launch_seeder merges them
  //    into run.json, so the run folder alone records which app and Speech resource it used.
  //  - demoiq-prefs.json: the --prefs this seeder was generated from (scenario text,
  //    content selection, counts), without any credential field, so a re-seed uses the
  //    same story after <scratch> has moved on to another run. Owner-only.
  try {
    const safePrefs = JSON.parse(JSON.stringify(prefsAsGiven));
    for (const k of ['adminPwd', 'existingCertThumbprint', 'streamKey']) delete safePrefs[k];
    if (Array.isArray(safePrefs.users)) safePrefs.users = safePrefs.users.map(u => { const c = Object.assign({}, u); delete c.pwd; return c; });
    for (let i = 1; i <= 5; i++) delete safePrefs['u' + i + 'pwd'];
    const prefsCopy = path.join(outDir, 'demoiq-prefs.json');
    if (path.resolve(prefsCopy) !== prefsPath) {
      const prefsTmp = prefsCopy + '.tmp';
      fs.writeFileSync(prefsTmp, JSON.stringify(safePrefs, null, 2) + '\n', { encoding: 'utf8', mode: 0o600 });
      fs.chmodSync(prefsTmp, 0o600);
      fs.renameSync(prefsTmp, prefsCopy);
      outputs.prefsCopy = prefsCopy;
    }
  } catch (e) { console.error('[warn] could not persist the prefs copy:', e.message); }
  if (args['pasted'] && pastedMap && typeof pastedMap === 'object') {
    try {
      const pastedCopy = path.join(outDir, 'demoiq-pasted.json');
      const pastedSrc = path.resolve(args['pasted']);
      if (path.resolve(pastedCopy) !== pastedSrc) fs.copyFileSync(pastedSrc, pastedCopy);
      outputs.pastedCopy = pastedCopy;
    } catch (e) { console.error('[warn] could not persist the pasted-map copy:', e.message); }
  }
  try {
    const idPath = path.join(outDir, 'run_identity.json');
    // expectedOfficeFiles (ADDITIVE schema): the per-type Office file counts +
    // primary names this seeder was generated for. Post-seed verification
    // (verify_seeded_files.js) reads this to require the full expected set - three
    // perfectly-formed files out of an expected six must NOT verify as success.
    const om = officeMetaFromSandbox();
    const idData = {
      appId: prefs.existingAppId || null,
      speechResourceId: prefs.speechResourceId || null,
      adminUpn: prefs.adminUpn || null,
      tenant: domain || null,
      generatedAt: new Date().toISOString()
    };
    if (om) {
      idData.expectedOfficeFiles = {
        word: om.word.expected, ppt: om.ppt.expected, excel: om.excel.expected,
        total: om.word.expected + om.ppt.expected + om.excel.expected,
        primaryNames: { word: om.word.primaryName, ppt: om.ppt.primaryName, excel: om.excel.primaryName }
      };
    }
    fs.writeFileSync(idPath, JSON.stringify(idData, null, 2), 'utf8');
    outputs.runIdentity = idPath;
  } catch (e) { console.error('[warn] could not write run_identity.json:', e.message); }
  outputs.platformPatch = { applied: platformPatch.applied, skipped: platformPatch.skipped };
}
if (emit === 'both' || emit === 'config') {
  // Plain JSON in an owner-only file: the GitHub Copilot adaptation keeps configs in the
  // workspace (never the repository) and needs no config password.
  const data = callSandbox('__demoiq_configData');
  fs.mkdirSync(outDir, { recursive: true });
  const cfgPath = path.join(outDir, configName);
  const cfgTmp = cfgPath + '.tmp';
  fs.writeFileSync(cfgTmp, JSON.stringify(data, null, 2) + '\n', { encoding: 'utf8', mode: 0o600 });
  fs.chmodSync(cfgTmp, 0o600);
  fs.renameSync(cfgTmp, cfgPath);
  outputs.config = cfgPath; outputs.configFields = Object.keys(data).length;
}
console.log(JSON.stringify({ ok: true, outDir, outputs }, null, 2));

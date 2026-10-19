#!/usr/bin/env node
/*
 * verify_seeded_files.js - post-seed safety check for the seeded Office files.
 *
 * Confirms each seeded Word / PowerPoint / Excel file (a) has its proper
 * .docx/.pptx/.xlsx EXTENSION and (b) is linked with a browser-open webUrl
 * (a SharePoint "Doc.aspx" rendering link, or a ?web=1 / &web=1 view link) so it
 * opens in Office on the web rather than downloading. Source of truth is the run's
 * Art-of-the-Possible HTML (aop_demo_runbook_*.html), which embeds the real in-tenant
 * file links the seeder produced; it cross-checks the run's cleanup.config manifest
 * (seeded file names) to catch a seeded Office file that is missing a link.
 *
 * It ALSO verifies the seeded set against the run's EXPECTED Office file counts
 * (run_identity.json -> expectedOfficeFiles, written by generate.js): a run
 * configured for 2 Word + 2 PPT + 2 Excel that seeded only 3 perfectly-formed
 * files must report allGood:false - "every file present is fine" is not the same
 * as "every expected file is present".
 *
 * Usage:
 *   node verify_seeded_files.js --dir "<run folder>\DemoIQ-Output"
 *   node verify_seeded_files.js <aop_html_path> [--cleanup "<cleanup.config path>"]
 *   ... [--identity "<run_identity.json path>"]
 *
 * run_identity.json is auto-discovered in --dir and its PARENT (the run folder,
 * where generate.js writes it); --identity overrides. Runs generated before
 * expectedOfficeFiles existed verify exactly as before (no count requirement).
 *
 * Prints a friendly markdown block, then a final JSON line:
 *   {"ok":true,"allGood":true,"source":"aop","files":[...],
 *    "expected":6,"actual":6,"expectedByType":{...},"actualByType":{...},
 *    "missingByType":{...},"missingExpected":[...],"warnings":[]}
 * "ok" = the check ran; "allGood" = every found Office file has an extension AND a
 * browser-open link AND (if a manifest was found) every manifest Office file is
 * linked AND (if expected counts are known) every expected file is present.
 */
'use strict';
const fs = require('fs');

// POSIX pipes are asynchronous in Node: output written right before process.exit()
// is cut off at the pipe buffer (~64 KB) on macOS/Linux. Make the pipe blocking.
if (process.stdout._handle && typeof process.stdout._handle.setBlocking === 'function') {
  process.stdout._handle.setBlocking(true);
}
const path = require('path');

const OFFICE_RE = /\.(docx|pptx|xlsx)$/i;
const LABEL_BY_EXT = { docx: 'Word', pptx: 'PowerPoint', xlsx: 'Excel' };
const TYPE_BY_EXT = { docx: 'word', pptx: 'ppt', xlsx: 'excel' };

function arg(name) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 ? process.argv[i + 1] : null;
}
function decode(s) {
  return String(s)
    .replace(/<[^>]+>/g, '')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ').trim();
}

// ---- resolve the AoP html + the DemoIQ-Output dir ----
const dir = arg('dir');
let aopFile = null;
let outDir = null;
const positional = process.argv.slice(2).find(a => !a.startsWith('--') &&
  process.argv[process.argv.indexOf(a) - 1] !== '--dir' &&
  process.argv[process.argv.indexOf(a) - 1] !== '--cleanup' &&
  process.argv[process.argv.indexOf(a) - 1] !== '--identity');

if (dir) {
  outDir = dir;
  try {
    const cands = fs.readdirSync(dir)
      .filter(f => /^aop_demo_.*\.html$/i.test(f))
      .map(f => ({ f, m: fs.statSync(path.join(dir, f)).mtimeMs }))
      .sort((a, b) => b.m - a.m);
    if (cands.length) aopFile = path.join(dir, cands[0].f);
  } catch (e) { /* fall through */ }
} else if (positional) {
  aopFile = path.resolve(positional);
  outDir = path.dirname(aopFile);
}

// ---- load the run's expected Office file counts (best-effort, additive schema) ----
// generate.js writes run_identity.json into the RUN folder; the seeder's outputs
// (this script's --dir) live in "<run folder>\DemoIQ-Output" - so check the given
// dir first, then its parent.
function parseExpected(c, data) {
  const e = data && data.expectedOfficeFiles;
  if (!e || typeof e !== 'object') return null;
  return {
    source: c,
    byType: { word: Number(e.word) || 0, ppt: Number(e.ppt) || 0, excel: Number(e.excel) || 0 },
    primaryNames: (e.primaryNames && typeof e.primaryNames === 'object') ? e.primaryNames : {},
  };
}
function loadExpectedOfficeFiles(identityArg, baseDir) {
  if (identityArg) {
    // An EXPLICIT --identity must never degrade silently: a mistyped path or corrupt
    // file would drop the expected-count gate and let an incomplete seed verify as
    // allGood - strictly worse than omitting the flag (auto-discovery might find the
    // real file). Fail loudly instead. A file that parses but has no
    // expectedOfficeFiles field is the legacy schema: deliberate
    // no-expectations, same as auto-discovering an old run.
    const c = path.resolve(identityArg);
    if (!fs.existsSync(c)) {
      console.log(JSON.stringify({ ok: false, allGood: false, errors: ['--identity file not found: ' + c] }));
      process.exit(2);
    }
    let data;
    try { data = JSON.parse(fs.readFileSync(c, 'utf8')); }
    catch (err) {
      console.log(JSON.stringify({ ok: false, allGood: false, errors: ['--identity file is not valid JSON: ' + c + ' (' + err.message + ')'] }));
      process.exit(2);
    }
    return parseExpected(c, data);
  }
  const candidates = [];
  if (baseDir) {
    candidates.push(path.join(baseDir, 'run_identity.json'));
    candidates.push(path.join(path.dirname(path.resolve(baseDir)), 'run_identity.json'));
  }
  for (const c of candidates) {
    try {
      if (!fs.existsSync(c)) continue;
      const info = parseExpected(c, JSON.parse(fs.readFileSync(c, 'utf8')));
      if (info) return info;
    } catch (err) { /* unreadable candidate - try the next */ }
  }
  return null;
}
const expectedInfo = loadExpectedOfficeFiles(arg('identity'), outDir);

// ---- gather the seeded Office file names from the cleanup.config manifest (best-effort) ----
function collectManifestOfficeNames(cleanupPath) {
  const names = new Set();
  try {
    if (!cleanupPath || !fs.existsSync(cleanupPath)) return names;
    const data = JSON.parse(fs.readFileSync(cleanupPath, 'utf8'));
    const walk = (node) => {
      if (!node || typeof node !== 'object') return;
      if (Array.isArray(node)) { node.forEach(walk); return; }
      if (typeof node.name === 'string' && OFFICE_RE.test(node.name)) names.add(node.name);
      for (const k of Object.keys(node)) walk(node[k]);
    };
    walk(data);
  } catch (e) { /* ignore malformed manifest */ }
  return names;
}
let cleanupPath = arg('cleanup');
if (!cleanupPath && outDir) {
  const c = path.join(outDir, 'cleanup.config');
  if (fs.existsSync(c)) cleanupPath = c;
}
const manifestNames = collectManifestOfficeNames(cleanupPath);

// ---- expected-vs-actual accounting (shared by both output paths) ----
// A collision-renamed upload ("Name_20260611_0909.docx", the seeder's additive
// re-run behavior) still counts toward its expected primary via the prefix match.
function computeExpectations(files, warnings) {
  const actualByType = { word: 0, ppt: 0, excel: 0 };
  const seen = new Set();
  for (const f of files) {
    const key = String(f.name || '').toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    const t = TYPE_BY_EXT[(String(f.name).match(OFFICE_RE) || [, ''])[1].toLowerCase()];
    if (t) actualByType[t]++;
  }
  const actual = actualByType.word + actualByType.ppt + actualByType.excel;
  if (!expectedInfo) {
    return { expected: null, actual, expectedByType: null, actualByType, missingByType: null, missingExpected: [], countsSatisfied: true };
  }
  const expectedByType = expectedInfo.byType;
  const expected = expectedByType.word + expectedByType.ppt + expectedByType.excel;
  const missingByType = {};
  let missingTotal = 0;
  for (const t of ['word', 'ppt', 'excel']) {
    const miss = Math.max(0, expectedByType[t] - actualByType[t]);
    missingByType[t] = miss;
    missingTotal += miss;
  }
  // Name what is missing when identifiable: the primary has a known name; the
  // companions are only known as "N more of this type" (their names are authored).
  const missingExpected = [];
  for (const t of ['word', 'ppt', 'excel']) {
    if (!missingByType[t]) continue;
    const typeExt = { word: 'docx', ppt: 'pptx', excel: 'xlsx' }[t];
    const label = LABEL_BY_EXT[typeExt];
    const primary = expectedInfo.primaryNames[t];
    const primaryBase = primary ? String(primary).replace(OFFICE_RE, '').toLowerCase() : null;
    // The primary counts as present only as an exact same-type name match or the
    // seeder's collision-rename shape '<primary>_yyyyMMdd_HHmm.<ext>'. A loose
    // startsWith would let a fallback-named companion ('<primary>_2.docx') or a
    // different-type file sharing the base mask a genuinely missing primary in
    // the missing-files diagnostics.
    const primaryEsc = primaryBase ? primaryBase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') : null;
    const primaryRe = primaryEsc ? new RegExp('^' + primaryEsc + '(_\\d{8}_\\d{4})?$') : null;
    const primaryPresent = primaryRe && files.some(f => {
      const fname = String(f.name || '');
      const fext = (fname.match(OFFICE_RE) || [, ''])[1].toLowerCase();
      return fext === typeExt && primaryRe.test(fname.replace(OFFICE_RE, '').toLowerCase());
    });
    let unnamed = missingByType[t];
    if (primary && !primaryPresent) { missingExpected.push(`${label} primary "${primary}"`); unnamed--; }
    if (unnamed > 0) missingExpected.push(`${unnamed} ${label} companion file(s)`);
  }
  if (missingTotal > 0) {
    warnings.push(`Expected ${expected} Office files (${expectedByType.word} Word / ${expectedByType.ppt} PowerPoint / ${expectedByType.excel} Excel) but found ${actual} - missing: ${missingExpected.join(', ')}.`);
  }
  return { expected, actual, expectedByType, actualByType, missingByType, missingExpected, countsSatisfied: missingTotal === 0 };
}

function expectationLines(exp) {
  if (!exp || exp.expected == null) return [];
  const cell = t => `${exp.actualByType[t]}/${exp.expectedByType[t]}`;
  return ['', `**Expected vs seeded:** Word ${cell('word')} · PowerPoint ${cell('ppt')} · Excel ${cell('excel')} · total ${exp.actual}/${exp.expected}`];
}

// ---- no AoP file: fall back to the manifest (names => extension check only) ----
if (!aopFile || !fs.existsSync(aopFile)) {
  const files = [...manifestNames].map(name => {
    const ext = (name.match(OFFICE_RE) || [, ''])[1].toLowerCase();
    return { label: LABEL_BY_EXT[ext] || '', name, ext, docAspx: false, webView: false, webUrl: null, status: 'no-link' };
  });
  const warnings = ['No aop_demo_*.html found - verified file EXTENSIONS from cleanup.config only; webUrl (browser-open link) could not be confirmed.'];
  const exp = computeExpectations(files, warnings);
  const out = ['### Post-seed file check', ''];
  if (files.length) {
    out.push('| File | Type | Extension | Browser-open link |', '| --- | --- | --- | --- |');
    files.forEach(f => out.push(`| \`${f.name}\` | ${f.label} | OK | ⚠️ unknown (no AoP) |`));
  } else {
    out.push('_No Office files found to verify (no AoP runbook and no cleanup.config manifest)._');
  }
  out.push(...expectationLines(exp));
  out.push('');
  warnings.forEach(w => out.push('> ⚠️ ' + w));
  console.log(out.join('\n'));
  console.log(JSON.stringify({
    ok: true, allGood: false, source: manifestNames.size ? 'cleanup.config' : 'none', files,
    expected: exp.expected, actual: exp.actual, expectedByType: exp.expectedByType,
    actualByType: exp.actualByType, missingByType: exp.missingByType, missingExpected: exp.missingExpected,
    warnings,
  }));
  process.exit(0);
}

// ---- parse the AoP html for the file rows: {label} <a href="URL"><code>NAME</code></a> ----
const html = fs.readFileSync(aopFile, 'utf8');
const files = [];
const linkedNames = new Set();
const liRe = /<li class="sm-item">([\s\S]*?)<\/li>/gi;
let li;
while ((li = liRe.exec(html)) !== null) {
  const block = li[1];
  const a = block.match(/<a\s+href="([^"]+)"[^>]*>\s*<code>([^<]+)<\/code>\s*<\/a>/i);
  if (!a) continue;
  const url = decode(a[1]);       // decodes &amp; -> & so web=1 detection is reliable
  const name = decode(a[2]);
  if (!OFFICE_RE.test(name)) continue;
  const ext = (name.match(OFFICE_RE) || [, ''])[1].toLowerCase();
  const before = decode(block.slice(0, a.index).replace(/<[^>]+>/g, ' '));
  const label = before || LABEL_BY_EXT[ext] || '';
  const docAspx = /Doc\.aspx/i.test(url);
  const webView = /[?&]web=1\b/i.test(url);
  const status = (docAspx || webView) ? 'ok' : 'no-web-link';
  files.push({ label, name, ext, docAspx, webView, webUrl: url, status });
  linkedNames.add(name);
}

// ---- cross-check: every manifest Office file should appear as a link in the AoP ----
const warnings = [];
for (const mn of manifestNames) {
  if (!linkedNames.has(mn)) {
    warnings.push(`Seeded file \`${mn}\` is in cleanup.config but has no linked entry in the AoP runbook - confirm it uploaded.`);
    const ext = (mn.match(OFFICE_RE) || [, ''])[1].toLowerCase();
    files.push({ label: LABEL_BY_EXT[ext] || '', name: mn, ext, docAspx: false, webView: false, webUrl: null, status: 'missing-link' });
  }
}

const exp = computeExpectations(files, warnings);
const allGood = files.length > 0 && files.every(f => f.status === 'ok') && exp.countsSatisfied;

// ---- render ----
const out = ['### Post-seed file check', ''];
if (files.length) {
  out.push('| File | Type | Extension | Browser-open link |', '| --- | --- | --- | --- |');
  for (const f of files) {
    const extCell = OFFICE_RE.test(f.name) ? '✅ ' + f.ext : '❌ missing';
    let linkCell;
    if (f.status === 'ok') linkCell = f.docAspx ? '✅ Doc.aspx' : '✅ web=1';
    else if (f.status === 'missing-link') linkCell = '⚠️ not linked';
    else linkCell = '⚠️ no web link';
    out.push(`| \`${f.name}\` | ${f.label} | ${extCell} | ${linkCell} |`);
  }
} else {
  out.push('_No Office files were linked in the AoP runbook (Word/PPT/Excel may not have been selected this run)._');
}
out.push(...expectationLines(exp));
out.push('');
out.push(allGood
  ? '✅ All expected Office files are seeded with correct extensions and open-in-browser links.'
  : '⚠️ Some Office files need attention (see the table / warnings above).');
warnings.forEach(w => out.push('> ⚠️ ' + w));

console.log(out.join('\n'));
console.log(JSON.stringify({
  ok: true, allGood, source: 'aop', file: aopFile, files,
  expected: exp.expected, actual: exp.actual, expectedByType: exp.expectedByType,
  actualByType: exp.actualByType, missingByType: exp.missingByType, missingExpected: exp.missingExpected,
  warnings,
}));

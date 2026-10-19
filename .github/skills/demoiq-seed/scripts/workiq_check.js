#!/usr/bin/env node
/*
 * workiq_check.js - the optional Work IQ check after a seed run.
 *
 * A seed run proves the content was written. It does not prove Work IQ can find it:
 * Microsoft 365 indexing takes time, and Work IQ needs its own tenant consent and
 * licence. This helper keeps the check deterministic. It never contacts a tenant; the
 * agent asks Work IQ itself (the template's workiq plugin, signed in as a demo user).
 *
 *   node workiq_check.js prompts --dir "<run folder>/DemoIQ-Output" [--count 3]
 *     The "Start Here" prompt of up to --count scenario cards from the run's AoP
 *     runbook (Microsoft 365 Copilot app scenarios first; Word, PowerPoint, Excel,
 *     Code and Autopilot scenarios need their app open and are used last), and the
 *     demo users the run seeded - ask as one of them.
 *     -> { ok, file, demoUsers[], prompts:[{ surface, scenario, prompt }] }
 *
 *   node workiq_check.js record --dir "<run folder>/DemoIQ-Output" --results <file.json>
 *     results: { askedAs, answers:[{ prompt, answered, citations:[{ title, url? }] }] }
 *     Writes <dir>/workiq_check.json (owner-only) and prints
 *     { ok, path, verdict, prompts, answered, withSeededCitations, askedAsIsDemoUser }.
 *     A citation counts as seeded when its title matches an item in the run's
 *     cleanup.config: an email subject, file name, meeting subject, chat topic or
 *     channel name.
 *     verdict: found-seeded-content | answered-without-seeded-citations | not-found
 *
 * Exit 0 on success; 2 on a usage or input problem (one JSON line with error).
 */
'use strict';
const fs = require('fs');
const path = require('path');

if (process.stdout._handle && typeof process.stdout._handle.setBlocking === 'function') {
  process.stdout._handle.setBlocking(true);
}

function out(obj) { process.stdout.write(JSON.stringify(obj) + '\n'); }
function fail(message) { out({ ok: false, error: message }); process.exit(2); }
function arg(name) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 ? process.argv[i + 1] : null;
}

function decodeHtml(s) {
  return String(s)
    .replace(/<[^>]+>/g, '')
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(parseInt(d, 10)))
    .replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ').trim();
}

function newestAop(dir) {
  let names = [];
  try { names = fs.readdirSync(dir); } catch (e) { return null; }
  const cands = names.filter(f => /^aop_demo_.*\.html$/i.test(f))
    .map(f => ({ f: path.join(dir, f), m: fs.statSync(path.join(dir, f)).mtimeMs }))
    .sort((a, b) => b.m - a.m);
  return cands.length ? cands[0].f : null;
}

function readManifestRun(dir) {
  const file = path.join(dir, 'cleanup.config');
  if (!fs.existsSync(file)) return null;
  try {
    const text = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
    const runs = [].concat(JSON.parse(text).runs || []);
    return runs.length ? runs[runs.length - 1] : null;
  } catch (e) { return null; }
}

// Surfaces that answer from the user's whole work context come first; in-app surfaces
// (Word, PowerPoint, Excel, Code, Autopilot) need their app and a file open.
function surfaceRank(surface) {
  if (/\[APP\]/.test(surface)) return 0;
  if (/\[(DEMO|TEAMS|EMAIL|AGENTS)\]/.test(surface)) return 1;
  return 2;
}

function extractScenarios(html) {
  const scenarios = [];
  const sectionRe = /<section class="surface-section"><h2 class="surface-header">((?:(?!<\/h2>)[\s\S])*?)<span class="surface-count">[\s\S]*?(?=<section class="surface-section">|$)/g;
  let s;
  while ((s = sectionRe.exec(html))) {
    const surface = decodeHtml(s[1]);
    const cardRe = /<details class="scenario-card">[\s\S]*?<span class="scenario-title">([\s\S]*?)<\/span>([\s\S]*?)<\/details>/g;
    let c;
    while ((c = cardRe.exec(s[0]))) {
      const p = /data-prompt-text="([^"]*)"/.exec(c[2]);
      if (!p) continue;
      const prompt = decodeHtml(p[1].replace(/&#10;/g, ' '));
      if (prompt) scenarios.push({ surface, scenario: decodeHtml(c[1]), prompt });
    }
  }
  return scenarios;
}

// Lower case, no RE:/FW: prefix, no Office extension, underscores as spaces - so a citation
// "Q3 Reliability Review.pptx" matches the uploaded "Q3_Reliability_Review.pptx".
function normTitle(v) {
  return String(v || '').toLowerCase().trim().replace(/^((re|fw|fwd):\s*)+/, '')
    .replace(/\.(docx|pptx|xlsx)$/, '').replace(/[_\s]+/g, ' ').trim();
}

function seededTitles(run) {
  const titles = new Set();
  const add = (v) => {
    const t = normTitle(v);
    // Every team has a General channel: too generic to identify seeded content.
    if (t.length >= 4 && t !== 'general') titles.add(t);
  };
  if (!run) return titles;
  for (const e of [].concat(run.emails || [])) {
    add(e.subject);
    for (const m of [].concat(e.messages || [])) add(m.subject);
  }
  for (const f of [].concat(run.files || [])) add(f.name);
  for (const m of [].concat(run.meetings || [], run.meetingContent || [])) add(m.subject);
  for (const c of [].concat(run.chats || [])) add(c.topic);
  for (const c of [].concat(run.channels || [])) add(c.channelName);
  for (const c of [].concat((run.team && run.team.channels) || [])) add(c.name);
  return titles;
}

// Exact title match, or containment when the shorter title is long enough to be specific
// (a citation "Pump B maintenance window - Outlook" still matches the seeded subject).
function isSeeded(title, titles) {
  const t = normTitle(title);
  if (t.length < 4) return false;
  for (const s of titles) {
    if (t === s) return true;
    const shorter = t.length < s.length ? t : s;
    if (shorter.length >= 12 && (t.includes(s) || s.includes(t))) return true;
  }
  return false;
}

const command = process.argv[2];
const dir = arg('dir');
if (!['prompts', 'record'].includes(command) || !dir) {
  fail('Usage: node workiq_check.js prompts|record --dir "<run folder>/DemoIQ-Output" [--count 3] [--results <file.json>]');
}
if (!fs.existsSync(dir)) fail('Run output folder not found: ' + dir);
const run = readManifestRun(dir);
const demoUsers = run ? [].concat(run.users || []).filter(u => /@/.test(String(u))).map(String) : [];

if (command === 'prompts') {
  const file = newestAop(dir);
  if (!file) fail('No aop_demo_*.html in ' + dir + ': this run produced no AoP runbook (select the aop content type), so there are no runbook prompts to ask.');
  const count = Math.max(1, Math.min(5, parseInt(arg('count') || '3', 10) || 3));
  const all = extractScenarios(fs.readFileSync(file, 'utf8'));
  const picked = all
    .map((s, i) => ({ s, i, r: surfaceRank(s.surface) }))
    .sort((a, b) => a.r - b.r || a.i - b.i)
    .slice(0, count)
    .map(x => x.s);
  if (!picked.length) fail('The AoP runbook ' + file + ' has no scenario prompts.');
  out({ ok: true, file, demoUsers, prompts: picked });
  process.exit(0);
}

const resultsFile = arg('results');
if (!resultsFile || !fs.existsSync(resultsFile)) fail('--results <file.json> is required and must exist.');
let results;
try { results = JSON.parse(fs.readFileSync(resultsFile, 'utf8').replace(/^\uFEFF/, '')); } catch (e) { fail('--results is not valid JSON: ' + e.message); }
const answers = [].concat((results && results.answers) || []);
if (!answers.length || answers.some(a => !a || typeof a.prompt !== 'string' || !a.prompt.trim())) {
  fail('--results must be { askedAs, answers:[{ prompt, answered, citations:[{ title, url? }] }] } with at least one prompt.');
}
const askedAs = String((results && results.askedAs) || '').trim();
const titles = seededTitles(run);
const recorded = answers.map(a => {
  const citations = [].concat(a.citations || []).map(c => {
    const title = typeof c === 'string' ? c : String((c && c.title) || '');
    const url = (c && typeof c === 'object' && c.url) ? String(c.url) : null;
    return { title, url, seeded: isSeeded(title, titles) };
  });
  return { prompt: a.prompt.trim(), answered: a.answered === true, citations };
});
const answered = recorded.filter(r => r.answered).length;
const withSeededCitations = recorded.filter(r => r.answered && r.citations.some(c => c.seeded)).length;
const verdict = withSeededCitations > 0 ? 'found-seeded-content'
  : answered > 0 ? 'answered-without-seeded-citations' : 'not-found';
const askedAsIsDemoUser = demoUsers.some(u => u.toLowerCase() === askedAs.toLowerCase());
const record = {
  schema: 1,
  checkedAt: new Date().toISOString(),
  askedAs,
  askedAsIsDemoUser,
  manifestFound: Boolean(run),
  prompts: recorded.length,
  answered,
  withSeededCitations,
  verdict,
  results: recorded
};
const target = path.join(dir, 'workiq_check.json');
const temp = target + '.tmp';
fs.writeFileSync(temp, JSON.stringify(record, null, 2), { mode: 0o600 });
fs.renameSync(temp, target);
if (process.platform !== 'win32') fs.chmodSync(target, 0o600);
out({ ok: true, path: target, verdict, prompts: recorded.length, answered, withSeededCitations, askedAsIsDemoUser, manifestFound: Boolean(run) });

#!/usr/bin/env node
/*
 * Extract the "Demo Tenant Content Summary" from a DemoIQ Art-of-the-Possible
 * HTML (aop_demo_runbook_*.html) and render it as clean markdown for chat.
 *
 * Usage:
 *   node summarize_aop.js <aop_html_path>
 *   node summarize_aop.js --dir <DemoIQ-Output dir>   # picks newest aop_*.html
 *
 * Prints markdown to stdout, plus a final JSON line:
 *   {"ok":true,"file":"...","generated":"..."}
 * On no-file: {"ok":false,"error":"..."}.
 */
'use strict';
const fs = require('fs');

// POSIX pipes are asynchronous in Node: output written right before process.exit()
// is cut off at the pipe buffer (~64 KB) on macOS/Linux. Make the pipe blocking.
if (process.stdout._handle && typeof process.stdout._handle.setBlocking === 'function') {
  process.stdout._handle.setBlocking(true);
}
const path = require('path');

function arg(name) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 ? process.argv[i + 1] : null;
}

// resolve target file
let file = null;
const positional = process.argv.slice(2).find(a => !a.startsWith('--') &&
  process.argv[process.argv.indexOf(a) - 1] !== '--dir');
const dir = arg('dir');
if (dir) {
  try {
    const cands = fs.readdirSync(dir)
      .filter(f => /^aop_demo_.*\.html$/i.test(f))
      .map(f => ({ f, m: fs.statSync(path.join(dir, f)).mtimeMs }))
      .sort((a, b) => b.m - a.m);
    if (cands.length) file = path.join(dir, cands[0].f);
  } catch (e) { /* fall through */ }
} else if (positional) {
  file = path.resolve(positional);
}

if (!file || !fs.existsSync(file)) {
  console.log(JSON.stringify({ ok: false, error: 'No aop_demo_*.html found' }));
  process.exit(0);
}

const html = fs.readFileSync(file, 'utf8');

// helpers
function decode(s) {
  return String(s)
    .replace(/<[^>]+>/g, '')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/&mdash;/g, '-').replace(/&middot;/g, '·').replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ').trim();
}

// pull the summary-card details block
// turn block boundaries into ' | ' so headline rows don't glue together
function decodeInline(s) {
  return decode(String(s)
    .replace(/<\/div>\s*<div[^>]*>/gi, '</div>|<div>')
    .replace(/<br\s*\/?>/gi, '|'))
    .replace(/\s*\|\s*/g, ' | ').replace(/^\s*\|\s*|\s*\|\s*$/g, '');
}

const cardM = html.match(/<details class="summary-card">[\s\S]*?<\/details>/i);
if (!cardM) {
  console.log(JSON.stringify({ ok: false, error: 'Summary card not found in ' + file }));
  process.exit(0);
}
const card = cardM[0];

// intro line (contains the generated timestamp)
const introM = card.match(/<p class="summary-intro">([\s\S]*?)<\/p>/i);
const intro = introM ? decode(introM[1]) : '';
const genM = intro.match(/on (.+?)\. Each row/);
const generated = genM ? genM[1] : '';

const out = [];
out.push('## Demo Tenant Content Summary');
out.push('');
if (intro) out.push('_' + intro + '_');
out.push('');

// each top-level row: <tr><td class="sm-label">LABEL</td><td class="sm-val">VAL</td></tr>
const rowRe = /<tr>\s*<td class="sm-label">([\s\S]*?)<\/td>\s*<td class="sm-val">([\s\S]*?)<\/td>\s*<\/tr>/gi;
let m;
while ((m = rowRe.exec(card)) !== null) {
  const label = decode(m[1]);
  const valHtml = m[2];

  // headline text before the first list (block boundaries -> ' | ')
  const headEnd = valHtml.search(/<ul class="sm-list">/i);
  const head = decodeInline(headEnd > -1 ? valHtml.slice(0, headEnd) : valHtml);

  out.push('### ' + label + (head ? ' - ' + head : ''));

  // list items
  const items = valHtml.match(/<li class="sm-item">[\s\S]*?<\/li>/gi) || [];
  for (const it of items) {
    const rows = it.match(/<div class="muted-row">([\s\S]*?)<\/div>/gi) || [];
    const strongM = it.match(/<strong>([\s\S]*?)<\/strong>/i);
    if (rows.length) {
      // titled item (email thread / chat / meeting) with detail rows
      const title = strongM ? decode(strongM[1]) : decode(it).slice(0, 80);
      out.push('- **' + title + '**');
      for (const r of rows) {
        const t = decode(r);
        if (t) out.push('  - ' + t);
      }
    } else {
      // single-line item (file or persona): keep the whole decoded line,
      // emphasising the leading <strong> (name) if present
      const full = decodeInline(it);
      const nm = strongM ? decode(strongM[1]) : '';
      out.push('- ' + (nm ? full.replace(nm, '**' + nm + '**') : full));
    }
  }
  out.push('');
}

console.log(out.join('\n'));
console.log(JSON.stringify({ ok: true, file, generated }));

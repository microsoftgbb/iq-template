#!/usr/bin/env node
/*
 * test_workiq_check.js - scripts/workiq_check.js (the optional Work IQ check).
 *
 * The AoP fixture uses the exact section, card and prompt markup the seeder writes
 * (engine/DemoIQ.html: surface-section, scenario-card, data-prompt-text), so a change
 * there that breaks prompt extraction fails here. No tenant, no network.
 */
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const script = path.join(__dirname, '..', 'scripts', 'workiq_check.js');
const engine = fs.readFileSync(path.join(__dirname, '..', 'engine', 'DemoIQ.html'), 'utf8');
let failures = 0;
function check(name, cond) {
  if (cond) console.log('  PASS ' + name);
  else { console.log('  FAIL ' + name); failures++; }
}
function run(args) {
  const r = spawnSync(process.execPath, [script, ...args], { encoding: 'utf8' });
  const line = (r.stdout || '').trim().split('\n').filter(l => l.startsWith('{')).pop();
  return { code: r.status, json: line ? JSON.parse(line) : null };
}

console.log('-- the engine still writes the markup the helper reads --');
check('surface sections', engine.includes('<section class="surface-section"><h2 class="surface-header">'));
check('surface count badge', engine.includes('<span class="surface-count">'));
check('scenario cards with a title', engine.includes('<details class="scenario-card"><summary><span class="chevron">-&gt;</span><span class="scenario-title">'));
check('prompt text attribute', engine.includes('<blockquote class="demo-prompt" data-prompt-text="'));

function card(title, prompts) {
  const steps = prompts.map((p, i) => '<div class="demo-prompt-wrap"><div class="prompt-step-label"><span class="step-num">Step ' + (i + 1) +
    '</span> Start Here</div><div class="demo-prompt-row"><blockquote class="demo-prompt" data-prompt-text="' + p.attr + '">' + p.attr +
    '</blockquote><button type="button" class="prompt-copy-btn" onclick="copyPromptText(this)">Copy</button></div></div>').join('');
  return '<details class="scenario-card"><summary><span class="chevron">-&gt;</span><span class="scenario-title">' + title +
    '</span></summary><div class="scenario-body"><div class="demo-field"><div class="demo-field-label">Prompts to type</div><div class="prompt-sequence">' +
    steps + '</div></div></div></details>';
}
function section(header, cards) {
  return '<section class="surface-section"><h2 class="surface-header">' + header + ' <span class="surface-count">' + cards.length +
    '</span></h2><div class="scenarios">' + cards.join('') + '</div></section>';
}
const aop = '<html><body>' +
  '<section class="surface-section"><h2 class="surface-header">[DEMO] Presenter Guide (raw)</h2><div class="raw-md">no prompts here</div></section>' +
  section('[EXCEL] Excel', [card('Pump trends', [{ attr: 'Chart the pump hours in this workbook' }])]) +
  section('[APP] Microsoft 365 Copilot App', [
    card('Catch up on Pump B', [{ attr: 'What&#39;s the latest on Pump B&#10;maintenance at North Works?' }, { attr: 'Who owns the follow-up?' }]),
    card('Prep for the review', [{ attr: 'Summarize the &quot;Q3 Reliability Review&quot; deck' }])
  ]) +
  section('[TEAMS] Teams', [card('Channel recap', [{ attr: 'Recap the South Works channel this week' }])]) +
  '</body></html>';

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'demoiq-workiq-'));
const dir = path.join(root, 'DemoIQ-Output');
fs.mkdirSync(dir);
fs.writeFileSync(path.join(dir, 'aop_demo_runbook_20260101_120000.html'), aop);
fs.writeFileSync(path.join(dir, 'cleanup.config'), '\uFEFF' + JSON.stringify({
  runs: [
    { users: ['old@contoso.onmicrosoft.com'], emails: [{ subject: 'Old run mail' }] },
    {
      users: ['AlexW@contoso.onmicrosoft.com', 'MeganB@contoso.onmicrosoft.com'],
      emails: [{ subject: 'Pump B maintenance window', messages: [{ subject: 'RE: Pump B maintenance window' }] }],
      files: [{ type: 'PowerPoint', name: 'Q3_Reliability_Review.pptx' }],
      meetings: [{ subject: 'North Works weekly sync' }],
      chats: [{ topic: 'Pump B escalation' }],
      channels: [{ channelName: 'South Works Operations' }],
      team: { channels: [{ name: 'General' }] }
    }
  ]
}));

try {
  console.log('-- prompts --');
  let r = run(['prompts', '--dir', dir]);
  check('prompts: ok with the AoP file and the newest run\'s demo users', r.code === 0 && r.json.ok &&
    /aop_demo_runbook_/.test(r.json.file) && r.json.demoUsers.join() === 'AlexW@contoso.onmicrosoft.com,MeganB@contoso.onmicrosoft.com');
  check('prompts: three, Copilot app scenarios first, in-app Excel last', r.json.prompts.length === 3 &&
    r.json.prompts[0].scenario === 'Catch up on Pump B' && r.json.prompts[1].scenario === 'Prep for the review' &&
    r.json.prompts[2].scenario === 'Channel recap');
  check('prompts: only the Start Here prompt of each card, entities decoded', r.json.prompts[0].prompt === "What's the latest on Pump B maintenance at North Works?" &&
    r.json.prompts[1].prompt === 'Summarize the "Q3 Reliability Review" deck');
  check('prompts: the raw presenter guide never leaks into a surface name', r.json.prompts.every(p => !/Presenter Guide/.test(p.surface)));
  r = run(['prompts', '--dir', dir, '--count', '9']);
  check('prompts: --count is capped and in-app scenarios are used last', r.json.prompts.length === 4 && r.json.prompts[3].scenario === 'Pump trends');
  const noAop = path.join(root, 'empty');
  fs.mkdirSync(noAop);
  r = run(['prompts', '--dir', noAop]);
  check('prompts: a run without an AoP runbook says so (exit 2)', r.code === 2 && /no AoP runbook/.test(r.json.error));
  r = run(['prompts']);
  check('usage error without --dir (exit 2)', r.code === 2 && /Usage/.test(r.json.error));

  console.log('-- record --');
  const results = path.join(root, 'results.json');
  fs.writeFileSync(results, JSON.stringify({
    askedAs: 'alexw@contoso.onmicrosoft.com',
    answers: [
      { prompt: 'What is the latest on Pump B?', answered: true, citations: [{ title: 'RE: Pump B maintenance window', url: 'https://outlook.office.com/x' }, { title: 'Unrelated note' }] },
      { prompt: 'Summarize the deck', answered: true, citations: ['Q3_Reliability_Review'] },
      { prompt: 'Recap the channel', answered: false, citations: [] }
    ]
  }));
  r = run(['record', '--dir', dir, '--results', results]);
  const written = JSON.parse(fs.readFileSync(path.join(dir, 'workiq_check.json'), 'utf8'));
  check('record: seeded citations matched by subject and by file name', r.code === 0 && r.json.verdict === 'found-seeded-content' &&
    r.json.answered === 2 && r.json.withSeededCitations === 2 && written.results[0].citations[1].seeded === false);
  check('record: asked as a seeded demo user (case-insensitive)', r.json.askedAsIsDemoUser === true && written.askedAsIsDemoUser === true);
  if (process.platform !== 'win32') {
    check('record: workiq_check.json is owner-only (0600)', (fs.statSync(path.join(dir, 'workiq_check.json')).mode & 0o777) === 0o600);
  }
  fs.writeFileSync(results, JSON.stringify({ askedAs: 'admin@contoso.onmicrosoft.com', answers: [{ prompt: 'Anything on Pump B?', answered: true, citations: [{ title: 'Board minutes' }, { title: 'General' }, { title: 'General meeting notes' }] }] }));
  r = run(['record', '--dir', dir, '--results', results]);
  check('record: answers without seeded citations are not counted as found', r.json.verdict === 'answered-without-seeded-citations' && r.json.askedAsIsDemoUser === false);
  check('record: the default General channel never counts as seeded content', r.json.withSeededCitations === 0);
  fs.writeFileSync(results, JSON.stringify({ askedAs: 'MeganB@contoso.onmicrosoft.com', answers: [{ prompt: 'Summarize the deck', answered: true, citations: [{ title: 'Q3 Reliability Review.pptx' }, { title: 'Pump B maintenance window - Outlook' }] }] }));
  r = run(['record', '--dir', dir, '--results', results]);
  const spaced = JSON.parse(fs.readFileSync(path.join(dir, 'workiq_check.json'), 'utf8'));
  check('record: titles match across underscores, extensions and app suffixes', r.json.verdict === 'found-seeded-content' && spaced.results[0].citations.every(c => c.seeded));
  fs.writeFileSync(results, JSON.stringify({ askedAs: 'AlexW@contoso.onmicrosoft.com', answers: [{ prompt: 'Anything on Pump B?', answered: false }] }));
  r = run(['record', '--dir', dir, '--results', results]);
  check('record: no answers is not-found', r.json.verdict === 'not-found' && r.json.withSeededCitations === 0);
  fs.writeFileSync(results, JSON.stringify({ askedAs: 'x', answers: [] }));
  r = run(['record', '--dir', dir, '--results', results]);
  check('record: an empty answer list is rejected (exit 2)', r.code === 2);
  r = run(['record', '--dir', dir, '--results', path.join(root, 'missing.json')]);
  check('record: a missing results file is rejected (exit 2)', r.code === 2);
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}

if (failures) { console.log('test_workiq_check: ' + failures + ' FAILURE(S)'); process.exit(1); }
console.log('test_workiq_check: all passed');

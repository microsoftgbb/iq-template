import assert from 'node:assert/strict'
import test from 'node:test'
import { MARKER, parseReply } from '../src/live/reply.ts'

const ANSWER = '**Pump B is in maintenance, at North Works.** [D1] The team planned a seal replacement [W1]; documents never prove current status [F1 §2].'
const SOURCES = [
  '- [D1] ScenarioOntology · Asset · demo-asset-002',
  '- [W1] Outlook · Maintenance team · 10 November 2026 · Pump B seal replacement · https://example.com/mail/1',
  '- [F1] Synthetic asset operations starter knowledge · §2',
]
const STEPS = ['1. Fabric IQ ontology: asked for Asset rows with the Status value "Maintenance".', '2. Work IQ mail: searched for "Pump B".']

function reply(sources: string, steps: string): string {
  return [ANSWER, '', sources, ...SOURCES, '', steps, ...STEPS].join('\n')
}

test('Sources and Steps are found plain, bold, as headings or with a colon', () => {
  for (const [sources, steps] of [
    ['Sources', 'Steps'],
    ['**Sources**', '**Steps**'],
    ['### Sources', '## Steps'],
    ['Sources:', 'Steps:'],
    ['**Sources:**', '**Steps**:'],
    ['#### **Sources**', '__Steps__'],
    ['sources', 'STEPS'],
    ['  Sources  ', '### Steps:'],
  ]) {
    const parsed = parseReply(reply(sources, steps))
    assert.equal(parsed.answer, ANSWER, sources)
    assert.deepEqual(parsed.sources.map((source) => source.id), ['D1', 'W1', 'F1'], sources)
    assert.deepEqual(parsed.steps, ['Fabric IQ ontology: asked for Asset rows with the Status value "Maintenance".', 'Work IQ mail: searched for "Pump B".'], steps)
  }
})

test('the sections may come in either order, after a rule, with CRLF line endings', () => {
  const parsed = parseReply('Answer [D1].\r\n\r\n---\r\n\r\n**Steps**\r\n1. Asked the ontology [D1]\r\n\r\n**Sources**\r\n- [D1] ScenarioOntology · Asset · demo-asset-002\r\n')
  assert.equal(parsed.answer, 'Answer [D1].')
  assert.deepEqual(parsed.steps, ['Asked the ontology [D1]'])
  assert.deepEqual(parsed.sources, [{ id: 'D1', kind: 'fabric', text: 'ScenarioOntology · Asset · demo-asset-002' }])
})

test('without the sections, the whole reply is the answer; an empty Sources section is no source', () => {
  const raw = 'Only an answer [W1].\n\nThe sources are listed below:\n- none'
  assert.deepEqual(parseReply(raw), { answer: raw, sources: [], steps: [], markers: [{ id: 'W1', kind: 'work' }], usedIqs: ['work'] })
  assert.deepEqual(parseReply(''), { answer: '', sources: [], steps: [], markers: [], usedIqs: [] })
  const noTools = parseReply("**I can't confirm which assets are in maintenance.**\n\nSources\n\nSteps\n1. Called no tool: this version has none.")
  assert.deepEqual([noTools.answer, noTools.sources, noTools.steps], ["**I can't confirm which assets are in maintenance.**", [], ['Called no tool: this version has none.']])
  const listedNone = parseReply('An answer.\n\nSources\n- None\n\nSteps\n- Called no tool.')
  assert.deepEqual([listedNone.answer, listedNone.sources, listedNone.steps], ['An answer.', [], ['Called no tool.']])
})

test('a "Steps:" line inside the answer stays there when prose follows it', () => {
  const parsed = parseReply(['Do this first.', 'Steps:', 'Check the seal before the restart.', 'Then log the work.', '', 'Sources', '- [F1] Synthetic asset operations starter knowledge'].join('\n'))
  assert.equal(parsed.answer, 'Do this first.\nSteps:\nCheck the seal before the restart.\nThen log the work.')
  assert.deepEqual(parsed.steps, [])
  assert.deepEqual(parsed.sources.map((source) => source.id), ['F1'])
})

test('markers D, F, Web and W carry their IQ and section, listed once each in order; other markers are ignored', () => {
  const parsed = parseReply('A [F1 §2] b [F1 §4] c [F1 §2] d [F1] e [Web1] f [W1] g [D1] h [D2] i [F1§5.1] [L1] [C1] [U1] [X1] [W] [W1x] [F1, §2] [w1] [Web]')
  assert.deepEqual(parsed.markers, [
    { id: 'F1', kind: 'foundry', section: '2' },
    { id: 'F1', kind: 'foundry', section: '4' },
    { id: 'F1', kind: 'foundry' },
    { id: 'Web1', kind: 'web' },
    { id: 'W1', kind: 'work' },
    { id: 'D1', kind: 'fabric' },
    { id: 'D2', kind: 'fabric' },
    { id: 'F1', kind: 'foundry', section: '5.1' },
  ])
  assert.match('[Web12 §4.1]', MARKER)
  for (const ignored of ['[L1]', '[C1]', '[U1 §3]', '[X1]']) assert.doesNotMatch(ignored, MARKER)
})

test('the IQs used come from the answer and its sources, always ordered fabric, foundry, work, web', () => {
  assert.deepEqual(parseReply('[Web1] [D1] [F1] [W2]').usedIqs, ['fabric', 'foundry', 'work', 'web'])
  assert.deepEqual(parseReply('Answer [L1].\n\nSources\n- [Web1] Page · example.com\n- [D1] ScenarioOntology · Site · demo-site-001\n- [L1] Lesson').usedIqs, ['fabric', 'web'])
  assert.deepEqual(parseReply('Answer [C1] [U1 §3.2].').usedIqs, [])
  assert.deepEqual(parseReply('Answer.\n\nSteps\n1. Searched mail [W1].').usedIqs, [], 'steps are not sources')
})

test('source lines keep plain text and an http(s) URL; lines without a known marker are dropped', () => {
  const parsed = parseReply([
    'Answer.',
    '',
    'Sources',
    '- [Web1] Centrifugal pump maintenance basics · example.com · https://example.com/pumps.',
    '- [W1] **Outlook** · [Pump B seal replacement](https://outlook.office.com/mail/id/AAQ%3D) · 10 November 2026',
    '- [W2] Outlook · <https://outlook.office.com/mail/id/BB>',
    '- [F1]: Synthetic asset operations starter knowledge',
    '  · starter.txt',
    '* **[D1]** ScenarioOntology · Asset · demo-asset-002',
    '- F2 – Maintenance procedure · §2',
    '- [D2] Site [demo-site-001](javascript:alert(1))',
    '- [L1] A lesson from another scenario',
    '- No marker on this line',
    '- [W1] **Outlook** · [Pump B seal replacement](https://outlook.office.com/mail/id/AAQ%3D) · 10 November 2026',
  ].join('\n'))
  assert.deepEqual(parsed.sources, [
    { id: 'Web1', kind: 'web', text: 'Centrifugal pump maintenance basics · example.com · https://example.com/pumps.', url: 'https://example.com/pumps' },
    { id: 'W1', kind: 'work', text: 'Outlook · Pump B seal replacement · 10 November 2026', url: 'https://outlook.office.com/mail/id/AAQ%3D' },
    { id: 'W2', kind: 'work', text: 'Outlook · https://outlook.office.com/mail/id/BB', url: 'https://outlook.office.com/mail/id/BB' },
    { id: 'F1', kind: 'foundry', text: 'Synthetic asset operations starter knowledge · starter.txt' },
    { id: 'D1', kind: 'fabric', text: 'ScenarioOntology · Asset · demo-asset-002' },
    { id: 'F2', kind: 'foundry', text: 'Maintenance procedure · §2' },
    { id: 'D2', kind: 'fabric', text: 'Site demo-site-001' },
  ])
})

test('steps are the Steps list items, wrapped lines joined; unlisted lines are one step each', () => {
  assert.deepEqual(parseReply('A.\n\nSteps\n1. One\n   continued\n2) Two\n- Three\n\n---').steps, ['One continued', 'Two', 'Three'])
  assert.deepEqual(parseReply('A.\n\nSteps:\nAsked the ontology.\nFound one asset.').steps, ['Asked the ontology.', 'Found one asset.'])
})

test('the answer keeps its Markdown, without trailing rules or blank lines', () => {
  const parsed = parseReply('**Pump B is in maintenance.** [D1]\n\n- North Works [D1]\n\n***\n\nSources\n- [D1] ScenarioOntology · Asset · demo-asset-002')
  assert.equal(parsed.answer, '**Pump B is in maintenance.** [D1]\n\n- North Works [D1]')
})

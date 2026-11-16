import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import test from 'node:test'
import { SCRIPT, TOOL_NAMES, VARIANT_IQS, askOf, readScript, stepOfQuestion } from '../src/demo/script.ts'
import raw from '../src/demo/script.json' with { type: 'json' }
import { SERVER_IQ } from '../src/live/protocol.ts'

interface RawScripted { reply: string[]; tools: { server: string; tool: string; arguments: string }[] }
interface RawChecks { keyFacts: unknown[]; mustNot: unknown[]; markers: string[]; tools: string[] }
interface RawStep {
  id: string
  iq: string[]
  question?: string
  variants?: string[]
  requires?: string
  checks?: Record<string, RawChecks>
  scripted?: Record<string, RawScripted>
}

const fresh = () => structuredClone(raw) as unknown as { steps: RawStep[] }
const stepIn = (script: { steps: RawStep[] }, id: string) => script.steps.find((step) => step.id === id) as RawStep
const agents = new URL('../../agents/', import.meta.url)

function rejects(edit: (script: { steps: RawStep[] }) => void, ...problems: RegExp[]) {
  const script = fresh()
  edit(script)
  assert.throws(() => readScript(script), (error: Error) => {
    for (const problem of problems) assert.match(error.message, problem)
    return true
  })
}

test('the shipped script is valid: unique step ids, its questions and a side-by-side comparison of the live facts', () => {
  assert.equal(SCRIPT.format, 'iq-template.demo.v1')
  const ids = SCRIPT.steps.map((step) => step.id)
  assert.equal(new Set(ids).size, ids.length)
  assert.deepEqual(ids, ['welcome', 'live-facts', 'documents', 'public-context', 'work-context', 'wrap-up'])
  assert.deepEqual(stepOfQuestion(SCRIPT, ' which ASSETS are in maintenance, and at which site? ')?.asks.map((ask) => ask.variant),
    ['without_iq', 'with_iq'], 'asked in order: without IQ, then with IQ')
  assert.equal(stepOfQuestion(SCRIPT, 'Which sites exist?'), undefined)
  assert.deepEqual(SCRIPT.steps.filter((step) => step.requires).map((step) => [step.id, step.requires]), [['work-context', 'work_iq']])
  assert.equal(askOf(stepOfQuestion(SCRIPT, 'Find recent mail about Pump B'), 'without_iq'), undefined)
  for (const step of SCRIPT.steps.filter((entry) => !entry.question)) assert.deepEqual(step.asks, [], `${step.id} is talk only`)
})

test('the script names the with-IQ agent\'s tools by their files and server labels, and the without-IQ agent has none', () => {
  assert.deepEqual(Object.keys(TOOL_NAMES).sort(), Object.keys(SERVER_IQ).sort())
  const files = readdirSync(new URL('scenario-with-iq/tools/', agents)).filter((file) => file.endsWith('.json'))
  assert.deepEqual(files.map((file) => file.slice(0, -5)).sort(), Object.values(TOOL_NAMES).sort())
  for (const file of files) {
    const tool = JSON.parse(readFileSync(new URL(`scenario-with-iq/tools/${file}`, agents), 'utf8')) as { server_label: string }
    assert.equal(TOOL_NAMES[tool.server_label], file.slice(0, -5), file)
  }
  assert.ok(!existsSync(new URL('scenario-without-iq/tools/', agents)))
  assert.deepEqual(VARIANT_IQS.without_iq, [])
})

test('step ids must be unique slugs, and questions unique', () => {
  rejects((script) => { stepIn(script, 'documents').id = 'live-facts' }, /step ids must be unique: live-facts/)
  rejects((script) => { stepIn(script, 'welcome').id = 'Welcome Step' }, /id must be a lowercase slug/)
  rejects((script) => {
    stepIn(script, 'documents').question = stepIn(script, 'live-facts').question?.toUpperCase()
  }, /questions must be unique/)
})

test('variants and IQs must be known, and a talk-only step asks nobody', () => {
  rejects((script) => { stepIn(script, 'live-facts').variants = ['without_iq', 'with_tools'] }, /variants must list the agents to ask/)
  rejects((script) => { stepIn(script, 'live-facts').variants = ['with_iq', 'with_iq'] }, /variants must list the agents to ask/)
  rejects((script) => { stepIn(script, 'welcome').iq = ['fabric', 'graph'] }, /iq must list each of fabric, foundry, web, work/)
  rejects((script) => { stepIn(script, 'wrap-up').variants = ['with_iq'] }, /a talk-only step has no question, so no variants/)
})

test('every checks and scripted entry belongs to a variant the step asks, and each asked variant has both', () => {
  rejects((script) => {
    const documents = stepIn(script, 'documents')
    const liveFacts = stepIn(script, 'live-facts')
    if (documents.checks && liveFacts.checks) documents.checks.without_iq = liveFacts.checks.without_iq
  }, /documents\): checks\.without_iq belongs to a variant the step doesn't ask/)
  rejects((script) => {
    const scripted = stepIn(script, 'live-facts').scripted
    if (scripted) delete scripted.without_iq
  }, /live-facts\): scripted has no entry for without_iq, which the step asks/)
})

test('scripted replies cite only the tools they call, and those only of the variant\'s', () => {
  rejects((script) => {
    stepIn(script, 'documents').scripted?.with_iq.reply.splice(0, 1, '**From the live ontology** [D1] [F1].')
  }, /documents\)\.scripted\.with_iq: cites D1, but calls no fabric tool/)
  rejects((script) => {
    const scripted = stepIn(script, 'live-facts').scripted
    if (scripted) scripted.without_iq.reply = ['**Pump B** [D1].', '', 'Sources', '- [D1] ScenarioOntology · Asset · demo-asset-002']
  }, /live-facts\)\.scripted\.without_iq: cites D1, but calls no fabric tool/)
})

test('the without-IQ variant has no tools and no IQ markers', () => {
  rejects((script) => {
    const checks = stepIn(script, 'live-facts').checks
    if (checks) {
      checks.without_iq.markers = ['D']
      checks.without_iq.tools = ['FabricIQOntology']
    }
  }, /checks\.without_iq: expects fabric, which the without_iq agent doesn't have/)
  rejects((script) => {
    const scripted = stepIn(script, 'live-facts').scripted
    if (scripted) scripted.without_iq.tools = [...scripted.with_iq.tools]
  }, /scripted\.without_iq: calls fabric, which the without_iq agent doesn't have/)
})

test('checks name known markers and tools within the step\'s IQs; Work IQ mail needs requires; one error lists every problem', () => {
  rejects((script) => {
    const checks = stepIn(script, 'documents').checks
    if (checks) checks.with_iq.markers = ['X']
  }, /markers: must list marker prefixes from D, F, Web, W/)
  rejects((script) => {
    const checks = stepIn(script, 'documents').checks
    if (checks) checks.with_iq.tools = ['FabricIQOntology']
  }, /expects fabric, which isn't in the step's iq list/)
  rejects((script) => { delete stepIn(script, 'work-context').requires }, /expects Work IQ mail, so it needs requires: work_iq/)
  rejects((script) => {
    stepIn(script, 'welcome').id = 'welcome step'
    stepIn(script, 'live-facts').variants = ['everyone']
  }, /lowercase slug[\s\S]*variants must list/)
})

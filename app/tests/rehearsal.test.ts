import assert from 'node:assert/strict'
import test from 'node:test'
import { SCRIPT, TOOL_NAMES, askOf, type Ask } from '../src/demo/script.ts'
import type { TurnOutcome } from '../src/live/protocol.ts'
import type { AgentRun } from '../server/foundry.ts'
import {
  RESULTS_FORMAT, UsageError, compare, exitCode, parseCommand, readResults, rehearse, score, select, summarize,
  type Reply, type Results,
} from '../server/rehearsal.ts'

const config = {
  projectEndpoint: 'https://ai-demo.services.ai.azure.com/api/projects/demo',
  agents: { with_iq: 'demo-with-iq', without_iq: 'demo-without-iq' },
  turnTimeoutMs: 225_000,
}
const TOKEN = 'header.payload.signature'
const stepOf = (id: string) => SCRIPT.steps.find((step) => step.id === id) ?? SCRIPT.steps[0]
const liveFacts = stepOf('live-facts')

function ask(id: string, variant: 'with_iq' | 'without_iq'): Ask {
  const found = askOf(stepOf(id), variant)
  assert.ok(found, `${id} asks ${variant}`)
  return found
}

function scripted(id: string, variant: 'with_iq' | 'without_iq', extra: Partial<Reply> = {}): Reply {
  const { scripted: reply } = ask(id, variant)
  return {
    step: id, variant, run: 1, status: 'answered', text: reply.reply, elapsedMs: 30_000,
    tools: reply.tools.map((tool) => TOOL_NAMES[tool.server]), failedTools: [], ...extra,
  }
}

const failing = (reply: Reply, id = reply.step) => score(ask(id, reply.variant).checks, reply).checks.filter((check) => check.ok === false)

test('every scripted reply passes its own step\'s checks', () => {
  for (const step of SCRIPT.steps) {
    for (const { variant, checks } of step.asks) {
      const scored = score(checks, scripted(step.id, variant))
      assert.equal(scored.verdict, 'pass', `${step.id} ${variant}: ${JSON.stringify(scored.checks)}`)
    }
  }
})

test('missing facts, forbidden phrases, foreign markers, missing tools, a broken format and slow turns fail', () => {
  const answer = (text: string) => `${text}\n\nSources\n- [D1] ScenarioOntology · Asset · demo-asset-002\n\nSteps\n1. Fabric IQ ontology: asked.`
  assert.deepEqual(failing(scripted('live-facts', 'with_iq', { text: answer('**Pump B is in maintenance.** [D1]') }))
    .map((check) => [check.name, check.detail]), [['key facts', 'At North Works']])
  assert.deepEqual(failing(scripted('live-facts', 'with_iq', { text: answer('**I can’t confirm** where Pump B is [D1], at North Works.') }))
    .map((check) => check.name), ['must not'], 'typographic apostrophes count')
  assert.deepEqual(failing(scripted('live-facts', 'without_iq', { text: '**Pump B** is in maintenance at North Works [D1]. I can\'t confirm more.\n\nSources\n- [D1] Ontology\n\nSteps\n1. None.' }))
    .map((check) => [check.name, check.detail]), [['must not', 'Names an asset'], ['markers', 'not the without_iq agent\'s: D']])
  assert.deepEqual(failing(scripted('live-facts', 'with_iq', { text: '**Pump B is in maintenance, at North Works.** [F1]\n\nSources\n- [F1] starter.txt\n\nSteps\n1. Fabric IQ ontology: asked.' }))
    .map((check) => [check.name, check.detail]), [['markers', 'missing D']])
  assert.deepEqual(failing(scripted('live-facts', 'with_iq', { text: '**Pump B is in maintenance, at North Works.** [D1]' }))
    .map((check) => [check.name, check.detail]), [['format', 'no Sources line for D1; no Steps list']])
  assert.deepEqual(failing(scripted('live-facts', 'with_iq', { tools: ['FoundryIQKnowledge'], failedTools: ['FabricIQOntology'] }))
    .map((check) => [check.name, check.detail]), [['tools', 'not run: FabricIQOntology; failed: FabricIQOntology']])
  assert.deepEqual(failing(scripted('live-facts', 'without_iq', { tools: ['FabricIQOntology'] })).map((check) => check.detail),
    ['unexpected: FabricIQOntology'], 'the without-IQ agent has no tools')
  assert.deepEqual(failing(scripted('live-facts', 'with_iq', { elapsedMs: 120_001 })).map((check) => [check.name, check.detail]),
    [['time', '120.0 s (bar 120.0 s)']])
  const named = score(ask('live-facts', 'with_iq').checks, scripted('live-facts', 'with_iq', { tools: [] }))
  assert.equal(named.verdict, 'pass', 'without tool events from the stream, the Steps list is the evidence')
  assert.match(named.checks.find((check) => check.name === 'tools')?.detail ?? '', /only the Steps list names them/)
  for (const id of ['live-facts', 'documents', 'public-context']) {
    const { checks, scripted: { tools } } = ask(id, 'with_iq')
    const failed = tools.map((tool) => TOOL_NAMES[tool.server])
    assert.deepEqual(failing(scripted(id, 'with_iq', { tools: [], failedTools: failed })).map((check) => [check.name, check.detail]),
      [['tools', `not run: ${checks.tools.join(', ')}; failed: ${failed.join(', ')}`]], `${id}: a failed call never passes on the Steps list`)
  }
  assert.deepEqual(score(ask('public-context', 'with_iq').checks, scripted('public-context', 'with_iq')).checks.filter((check) => check.ok === null)
    .map((check) => check.detail), ['The Web IQ query in the tool timeline holds no site, asset or person\'s name.'])
})

test('a turn that failed is an error, and one that needs consent stops for the user', () => {
  const checks = ask('live-facts', 'with_iq').checks
  const failed = score(checks, { ...scripted('live-facts', 'with_iq'), status: 'failed', text: undefined, message: 'timeout: The agent didn\'t finish.' })
  assert.deepEqual([failed.verdict, failed.checks.map((check) => check.detail)], ['error', ['timeout: The agent didn\'t finish.']])
  const consent = score(checks, { ...scripted('live-facts', 'with_iq'), status: 'needs-consent', text: undefined, server: 'fabric_iq_ontology', consentUrl: 'https://consent.example/x' })
  assert.deepEqual([consent.verdict, consent.checks[0].detail], ['consent', 'Fabric IQ needs your consent'])
})

test('the selection follows the script, skips Work IQ unless asked, and refuses what it can\'t ask', () => {
  const pairs = (options: Partial<Parameters<typeof select>[1]>) => select(SCRIPT, { steps: [], runs: 2, withWorkIq: false, ...options })
  const all = pairs({})
  assert.deepEqual(all.planned.map((planned) => `${planned.step.id} ${planned.variant}`),
    ['live-facts without_iq', 'live-facts with_iq', 'documents with_iq', 'public-context with_iq'])
  assert.deepEqual(all.skipped, [{ step: 'work-context', reason: 'needs Work IQ (add --with-work-iq)' }])
  assert.deepEqual(pairs({ withWorkIq: true }).planned.map((planned) => planned.step.id).at(-1), 'work-context')
  assert.deepEqual(pairs({ variant: 'without_iq' }).planned.map((planned) => `${planned.step.id} ${planned.variant}`), ['live-facts without_iq'])
  assert.deepEqual(pairs({ steps: ['documents', 'live-facts'], variant: 'with_iq' }).planned.map((planned) => planned.step.id), ['live-facts', 'documents'])
  assert.throws(() => pairs({ steps: ['work-context'] }), (error) => error instanceof UsageError && /needs Work IQ: add --with-work-iq/.test(error.message))
  assert.throws(() => pairs({ steps: ['welcome'] }), /Unknown or talk-only step: welcome/)
  assert.throws(() => pairs({ steps: ['nope'] }), UsageError)
  assert.throws(() => pairs({ steps: ['documents'], variant: 'without_iq' }), /Nothing to ask: no selected step asks without_iq/)
})

test('the command line: two runs by default, at most five, known agents, and --compare with two files', () => {
  assert.deepEqual(parseCommand([]), { kind: 'rehearse', options: { steps: [], runs: 2, withWorkIq: false } })
  assert.deepEqual(parseCommand(['--step', 'live-facts', '--step', 'documents', '--variant', 'with_iq', '--runs', '3', '--with-work-iq']),
    { kind: 'rehearse', options: { steps: ['live-facts', 'documents'], variant: 'with_iq', runs: 3, withWorkIq: true } })
  assert.deepEqual(parseCommand(['--compare', 'a.json', 'b.json']), { kind: 'compare', files: ['a.json', 'b.json'] })
  assert.deepEqual(parseCommand(['--help']), { kind: 'help' })
  for (const argv of [['--runs', '0'], ['--runs', '6'], ['--runs', 'two'], ['--variant', 'both'], ['--compare', 'a.json'],
    ['--compare', 'a.json', 'b.json', '--runs', '1'], ['extra'], ['--parallel']]) {
    assert.throws(() => parseCommand(argv), UsageError, argv.join(' '))
  }
})

function fakeFoundry(outcome: (run: AgentRun) => TurnOutcome) {
  const runs: AgentRun[] = []
  let active = 0
  let most = 0
  const ask = async (run: AgentRun): Promise<TurnOutcome> => {
    runs.push(run)
    active += 1
    most = Math.max(most, active)
    await new Promise((resolve) => setTimeout(resolve, 1))
    active -= 1
    return outcome(run)
  }
  return { ask, runs, most: () => most }
}

function answering(run: AgentRun): TurnOutcome {
  const step = SCRIPT.steps.find((entry) => entry.question === run.question) ?? liveFacts
  const variant = run.agent === config.agents.with_iq ? 'with_iq' : 'without_iq'
  const reply = askOf(step, variant)?.scripted
  return {
    status: 'answered', text: reply?.reply ?? '', responseId: 'resp_1',
    toolCalls: (reply?.tools ?? []).map((tool, index) => ({ id: `mcp_${index}`, server: tool.server, tool: tool.tool, state: 'completed' as const })),
  }
}

test('rehearsing asks one question at a time, as the Azure CLI user, through the server\'s Foundry client', async () => {
  const foundry = fakeFoundry(answering)
  let tokens = 0
  let now = 0
  const results = await rehearse(select(SCRIPT, { steps: [], runs: 2, withWorkIq: false }), 2, {
    config, ask: foundry.ask, token: async () => { tokens += 1; return TOKEN }, clock: () => (now += 15_000), now: () => new Date(0),
  })
  assert.equal(foundry.most(), 1, 'never in parallel')
  assert.deepEqual(results.replies.map((reply) => `${reply.step} ${reply.variant} ${reply.run}`), [
    'live-facts without_iq 1', 'live-facts with_iq 1', 'live-facts without_iq 2', 'live-facts with_iq 2',
    'documents with_iq 1', 'documents with_iq 2', 'public-context with_iq 1', 'public-context with_iq 2',
  ])
  assert.equal(tokens, 8)
  for (const run of foundry.runs) {
    assert.equal(run.token, TOKEN)
    assert.equal(run.endpoint, config.projectEndpoint)
    assert.equal(run.timeoutMs, config.turnTimeoutMs)
    assert.equal(run.previousResponseId, undefined, 'each run is a new conversation')
    assert.deepEqual(Object.keys(run.metadata), ['cid', 'variant', 'step', 'purpose'])
    assert.match(run.metadata.cid, /^[0-9a-f-]{36}$/)
    assert.equal(run.metadata.purpose, 'rehearsal')
  }
  assert.deepEqual(foundry.runs.slice(0, 2).map((run) => [run.agent, run.question]),
    [['demo-without-iq', liveFacts.question], ['demo-with-iq', liveFacts.question]])
  assert.deepEqual(results.replies.map((reply) => [reply.verdict, reply.elapsedMs]).at(-1), ['pass', 15_000])
  assert.deepEqual([results.format, results.at, results.runs, exitCode(results)], [RESULTS_FORMAT, '1970-01-01T00:00:00.000Z', 2, 0])
  assert.deepEqual(results.replies[1].tools, ['FabricIQOntology'])
  const summary = summarize(results)
  assert.match(summary, /^step\s+variant\s+run\s+result\s+seconds\s+missing$/m)
  assert.match(summary, /^live-facts\s+with_iq\s+1\s+PASS\s+15\.0$/m)
  assert.match(summary, /8 of 8 turn\(s\) passed; a turn passes within 120\.0 s\./)
  assert.match(summary, /Check by eye: public-context \(with_iq\): The Web IQ query/)
  assert.match(summary, /Skipped work-context: needs Work IQ \(add --with-work-iq\)\./)
})

test('a consent request stops the rehearsal with its link and exit code 3; a failed turn exits 1', async () => {
  const consent = fakeFoundry((run) => (run.agent === config.agents.with_iq
    ? { status: 'needs-consent', consentUrl: 'https://consent.example/fabric', server: 'fabric_iq_ontology', toolCalls: [] }
    : answering(run)))
  const stopped = await rehearse(select(SCRIPT, { steps: [], runs: 2, withWorkIq: false }), 2, { config, ask: consent.ask, token: async () => TOKEN })
  assert.deepEqual(stopped.replies.map((reply) => reply.verdict), ['pass', 'consent'], 'nothing after the consent request')
  assert.equal(exitCode(stopped), 3)
  assert.match(summarize(stopped), /Fabric IQ needs your consent: open https:\/\/consent\.example\/fabric, accept as yourself, then rehearse again\./)
  const timeout = fakeFoundry(() => ({ status: 'failed', message: 'The agent didn\'t finish within 225 seconds.', code: 'timeout', toolCalls: [] }))
  const failed = await rehearse(select(SCRIPT, { steps: ['documents'], runs: 1, withWorkIq: false }), 1, { config, ask: timeout.ask, token: async () => TOKEN })
  assert.equal(exitCode(failed), 1)
  assert.match(summarize(failed), /^documents\s+with_iq\s+1\s+ERROR\s+\d+\.\d\s+timeout: The agent didn't finish within 225 seconds\.$/m)
})

test('two results files compare per step and agent: passes out of runs and the median time', () => {
  const results = (verdicts: [string, string, number][]): Results => readResults({
    format: RESULTS_FORMAT, at: '2026-11-16T10:00:00.000Z', slowMs: 120_000, runs: 2, skipped: [],
    replies: verdicts.map(([step, verdict, elapsedMs], index) => ({ ...scripted(step, 'with_iq'), run: index + 1, verdict, elapsedMs, checks: [] })),
  }, 'test.json')
  const before = results([['live-facts', 'pass', 40_000], ['live-facts', 'fail', 60_000]])
  const after = results([['live-facts', 'pass', 30_000], ['live-facts', 'pass', 34_000], ['documents', 'pass', 9_000]])
  assert.deepEqual(compare(before, after, ['before.json', 'after.json']).split('\n'), [
    'step        variant     before.json         after.json',
    'live-facts  with_iq     1/2 pass, 50.0 s    2/2 pass, 32.0 s',
    'documents   with_iq     –                   1/1 pass, 9.0 s',
  ])
  assert.throws(() => readResults({ format: 'other', replies: [] }, 'other.json'), /other\.json is not an iq-template\.rehearsal-results\.v1 file/)
  assert.throws(() => readResults(null, 'null.json'), UsageError)
})

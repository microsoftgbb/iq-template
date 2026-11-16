import assert from 'node:assert/strict'
import test from 'node:test'
import type { ToolCall, TurnOutcome } from '../src/live/protocol.ts'
import { parseReply } from '../src/live/reply.ts'
import { CANCELLED, runTurn, type Send, type TurnLog, type TurnOptions } from '../src/live/turn.ts'
import type { Question } from '../src/live/types.ts'

const cid = '8f14e45f-ceea-4e6b-9d55-1f1e2f3a4b5c'
const QUESTION: Question = { variant: 'with_iq', text: 'Which assets are in maintenance, and at which site?' }
const ANSWER = '**Pump B is in maintenance, at North Works.** [D1]\n\nSources\n- [D1] ScenarioOntology · Asset · demo-asset-002\n\nSteps\n1. Fabric IQ ontology: asked for Asset rows with the Status value "Maintenance".'
const knowledge: ToolCall = { id: 'mcp_1', server: 'foundry_iq_knowledge', tool: 'knowledge_base_retrieve', state: 'completed', ms: 4100 }
const ontology: ToolCall = { id: 'mcp_2', server: 'fabric_iq_ontology', tool: 'ask_ontology', state: 'completed', ms: 21800 }

// A clock that moves 250 ms per reading: every result below took 250 ms.
function options(send: Send, extra: Partial<TurnOptions> = {}): TurnOptions {
  let now = 1000
  return { question: QUESTION, send, clock: () => (now += 250), newId: () => cid, ...extra }
}

const answering = (text: string, toolCalls: ToolCall[] = []): Send => async () => ({ status: 'answered', text, responseId: 'resp_1', toolCalls })

test('a live answer is parsed, timed and tied to its Foundry run, with the tool calls Foundry reported', async () => {
  const sent: [Question, string][] = []
  const result = await runTurn(options(async (question, id) => {
    sent.push([question, id])
    return { status: 'answered', text: ANSWER, responseId: 'resp_1', toolCalls: [knowledge, ontology] }
  }))
  assert.deepEqual(result, { status: 'answered', reply: parseReply(ANSWER), raw: ANSWER, elapsedMs: 250, responseId: 'resp_1', toolCalls: [knowledge, ontology] })
  assert.deepEqual(sent, [[QUESTION, cid]])
})

test('every turn gets a fresh correlation ID, and a follow-up carries its previous response', async () => {
  const ids: string[] = []
  const asked: Question[] = []
  for (const question of [QUESTION, { ...QUESTION, variant: 'without_iq' as const }, { ...QUESTION, text: 'Since when?', previousResponseId: 'resp_1' }]) {
    await runTurn({
      question,
      send: async (sent, correlationId) => {
        asked.push(sent)
        ids.push(correlationId)
        return { status: 'answered', text: 'An answer.', toolCalls: [] }
      },
    })
  }
  assert.equal(new Set(ids).size, 3)
  for (const id of ids) assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  assert.deepEqual(asked.map((question) => [question.variant, question.previousResponseId]), [['with_iq', undefined], ['without_iq', undefined], ['with_iq', 'resp_1']])
})

test('tool calls reach the caller while the turn runs; a stream that broke off keeps the ones seen', async () => {
  const seen: ToolCall[] = []
  const running: ToolCall = { ...knowledge, state: 'running', ms: undefined }
  await runTurn(options(async (_question, _cid, _signal, onTool) => {
    onTool(running)
    onTool(knowledge)
    return { status: 'answered', text: 'Done [F1].', toolCalls: [knowledge] }
  }, { onTool: (call) => seen.push(call) }))
  assert.deepEqual(seen, [running, knowledge])
  const broken = await runTurn(options(async (_question, _cid, _signal, onTool) => {
    onTool({ ...ontology, state: 'running', ms: undefined })
    return { status: 'failed', message: 'The connection to the app server closed before the answer arrived. Ask again.', toolCalls: [] }
  }))
  assert.deepEqual(broken.toolCalls.map((call) => [call.id, call.state, call.error]), [['mcp_2', 'failed', 'The call did not finish.']])
})

test('a consent request becomes a link to authorize the tool, then ask again; only https links pass', async () => {
  const outcome: TurnOutcome = { status: 'needs-consent', consentUrl: 'https://login.microsoftonline.com/consent?x=1', server: 'work_iq_mail', responseId: 'resp_3', toolCalls: [] }
  assert.deepEqual(await runTurn(options(async () => outcome)), {
    status: 'needs-consent', consentUrl: 'https://login.microsoftonline.com/consent?x=1', server: 'work_iq_mail', elapsedMs: 250, responseId: 'resp_3', toolCalls: [],
  })
  const unsafe = await runTurn(options(async () => ({ ...outcome, consentUrl: 'javascript:alert(1)' })))
  assert.equal(unsafe.status, 'failed')
})

test('failures, empty answers and exceptions become failed results', async () => {
  assert.deepEqual(await runTurn(options(async () => ({ status: 'failed', message: 'Foundry answered HTTP 503. Ask again.', code: 'HTTP 503', toolCalls: [] }))), {
    status: 'failed', message: 'Foundry answered HTTP 503. Ask again.', code: 'HTTP 503', elapsedMs: 250, toolCalls: [],
  })
  assert.deepEqual(await runTurn(options(answering('   '))), { status: 'failed', message: 'The agent finished without an answer. Ask again.', elapsedMs: 250, responseId: 'resp_1', toolCalls: [] })
  assert.deepEqual(await runTurn(options(async () => { throw new Error('Network down') })), { status: 'failed', message: 'Network down', elapsedMs: 250, toolCalls: [] })
  assert.deepEqual(await runTurn(options(async () => { throw new Error('') })), { status: 'failed', message: 'The question could not be sent.', elapsedMs: 250, toolCalls: [] })
})

test('an abort resolves at once as cancelled, stops the request and ignores the late reply', async () => {
  const controller = new AbortController()
  let release: (value: TurnOutcome) => void = () => {}
  let requestSignal: AbortSignal | undefined
  const seen: ToolCall[] = []
  const pending = runTurn(options((_question, _cid, signal, onTool) => {
    requestSignal = signal
    onTool({ ...ontology, state: 'running', ms: undefined })
    return new Promise<TurnOutcome>((resolve) => { release = resolve })
  }, { signal: controller.signal, onTool: (call) => seen.push(call) }))
  controller.abort()
  const cancelled = await pending
  assert.deepEqual([cancelled.status, cancelled.status === 'failed' && cancelled.code], ['failed', CANCELLED])
  assert.deepEqual(cancelled.toolCalls.map((call) => call.state), ['failed'], 'a call that was running when the turn stopped never finished')
  assert.equal(requestSignal?.aborted, true)
  release({ status: 'answered', text: 'Late answer.', toolCalls: [] })
  assert.equal(seen.length, 1)

  const aborted = new AbortController()
  aborted.abort()
  let calls = 0
  const before = await runTurn(options(async () => {
    calls += 1
    return { status: 'answered', text: 'x', toolCalls: [] }
  }, { signal: aborted.signal }))
  assert.deepEqual([calls, before.status === 'failed' && before.code], [0, CANCELLED])
})

test('the turn log is value-free: no question or answer text, tool arguments, run or user identifiers', async () => {
  const records: TurnLog[] = []
  const log = (record: TurnLog) => { records.push(record) }
  const withArguments: ToolCall = { ...ontology, arguments: '{"question":"Which Asset rows have the Status value \\"Maintenance\\"?"}' }
  await runTurn(options(answering(ANSWER, [knowledge, withArguments]), { log }))
  await runTurn(options(async () => ({ status: 'failed', message: 'Forbidden for operator@contoso.example', toolCalls: [] }), { log, question: { variant: 'without_iq', text: 'Since when?', previousResponseId: 'resp_1' } }))
  const controller = new AbortController()
  controller.abort()
  await runTurn(options(answering('x'), { log, signal: controller.signal }))
  const reply = parseReply(ANSWER)
  assert.deepEqual(records, [
    {
      event: 'app.turn', correlationId: cid, variant: 'with_iq', followUp: false, outcome: 'answered', elapsedMs: 250,
      usedIqs: ['fabric'], counts: { markers: reply.markers.length, sources: reply.sources.length, steps: reply.steps.length },
      tools: [{ iq: 'foundry', state: 'completed', ms: 4100 }, { iq: 'fabric', state: 'completed', ms: 21800 }],
    },
    { event: 'app.turn', correlationId: cid, variant: 'without_iq', followUp: true, outcome: 'failed', elapsedMs: 250 },
    { event: 'app.turn', correlationId: cid, variant: 'with_iq', followUp: false, outcome: 'cancelled', elapsedMs: 250 },
  ])
  const text = JSON.stringify(records)
  for (const value of ['resp_1', 'Pump B', 'North Works', 'Maintenance', 'contoso', 'Forbidden', 'mcp_1', 'ask_ontology', 'Since when']) {
    assert.ok(!text.includes(value), value)
  }
})

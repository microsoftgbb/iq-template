import assert from 'node:assert/strict'
import test from 'node:test'
import {
  agentTurn, failureMessage, proofMatches, readStream, redact, sseEvents, PROOF_PREFIX, type StreamProgress,
} from '../src/agent.ts'
import { parseConfig } from '../src/config.ts'

const challenge = 'connection proof 12345678-1234-4234-8234-123456789abc'

function response(output: unknown[], extra: Record<string, unknown> = {}) {
  return { id: 'caresp_1', object: 'response', status: 'completed', output, ...extra }
}

function message(text: string) {
  return { type: 'message', role: 'assistant', content: [{ type: 'output_text', text, annotations: [] }] }
}

test('a completed response yields its assistant text and the tools it called', () => {
  const turn = agentTurn(response([
    { type: 'function_call', name: 'scenario_ontology___ask_ontology', status: 'completed', arguments: '{}' },
    { type: 'function_call_output', call_id: 'call_1', output: 'Pump B' },
    message('Pump B is in Maintenance at North Works (Source: ontology).'),
  ]))
  assert.deepEqual(turn, {
    responseId: 'caresp_1',
    text: 'Pump B is in Maintenance at North Works (Source: ontology).',
    tools: [{ name: 'scenario_ontology___ask_ontology', status: 'completed' }],
  })
})

test('only the exact fresh challenge proves the agent answered this check', () => {
  assert.equal(proofMatches(agentTurn(response([message(PROOF_PREFIX + challenge)])), challenge), true)
  for (const text of [`${PROOF_PREFIX}connection proof older`, challenge, ` ${PROOF_PREFIX}${challenge}x`]) {
    assert.equal(proofMatches(agentTurn(response([message(text)])), challenge), false)
  }
})

test('failed, incomplete and empty responses stay explicit failures', () => {
  assert.throws(() => agentTurn(response([], { status: 'failed', error: { code: 'server_error', message: 'Toolbox unavailable' } })),
    /failed: Toolbox unavailable/)
  assert.throws(() => agentTurn(response([], { status: 'incomplete' })), /incomplete/)
  assert.throws(() => agentTurn(response([message('   ')])), /without an answer/)
  for (const value of [undefined, null, [], 'text', { status: 'completed', output: [] }, response([message('x')], { id: '' })]) {
    assert.throws(() => agentTurn(value))
  }
})

test('a consent request is surfaced instead of being treated as an answer', () => {
  const turn = agentTurn(response([{ type: 'oauth_consent_request', consent_link: 'https://consent.example/flow' }]))
  assert.equal(turn.consentLink, 'https://consent.example/flow')
  assert.equal(turn.text, '')
})

test('HTTP failures map to actionable messages that keep the service detail', () => {
  assert.match(failureMessage(403, { error: { message: 'denied' } }), /Foundry Agent Consumer role.*\(denied\)/)
  assert.match(failureMessage(401, undefined), /sign in again/)
  assert.match(failureMessage(502, {}), /failed to answer/)
  assert.match(failureMessage(418, {}), /HTTP 418/)
})

test('diagnostics never show credentials', () => {
  assert.deepEqual(redact({ error: { message: 'x', access_token: 'secret' }, nested: [{ Authorization: 'Bearer y' }] }),
    { error: { message: 'x', access_token: '[redacted]' }, nested: [{ Authorization: '[redacted]' }] })
})

test('runtime configuration is validated before sign-in', () => {
  const valid = {
    clientId: '11111111-1111-4111-8111-111111111111',
    tenantId: '22222222-2222-4222-8222-222222222222',
    scope: 'https://ai.azure.com/user_impersonation',
    agentName: 'demo-assistant',
  }
  assert.deepEqual(parseConfig(valid), valid)
  for (const change of [{ clientId: 'not-a-guid' }, { scope: 'user_impersonation' }, { agentName: '' }]) {
    assert.throws(() => parseConfig({ ...valid, ...change }))
  }
  assert.throws(() => parseConfig([]))
})

// The live event order: two parallel tool calls, keep-alives while they run, their outputs, then the text.
function streamOf(chunks: string[]) {
  const bytes = new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(bytes.encode(chunk))
      controller.close()
    },
  })
}

function sse(event: Record<string, unknown>) {
  return `event: ${String(event.type)}\ndata: ${JSON.stringify(event)}\n\n`
}

const ontologyCall = { type: 'function_call', name: 'scenario_ontology___ask_ontology', call_id: 'c1', status: 'in_progress' }
const documentCall = { type: 'function_call', name: 'knowledge_base___knowledge_base_retrieve', call_id: 'c2', status: 'in_progress' }
const answer = 'Pump B is at North Works (Source: ontology).'
const liveEvents = [
  sse({ type: 'response.created', response: { id: 'caresp_1', status: 'in_progress', output: [] } }),
  sse({ type: 'response.output_item.added', output_index: 0, item: ontologyCall }),
  sse({ type: 'response.output_item.added', output_index: 1, item: documentCall }),
  ': keep-alive\n\n',
  sse({ type: 'response.output_item.done', output_index: 2, item: { type: 'function_call_output', call_id: 'c1', output: 'Pump B' } }),
  sse({ type: 'response.output_item.done', output_index: 3, item: { type: 'function_call_output', call_id: 'c2', output: 'starter.txt' } }),
  sse({ type: 'response.output_text.delta', output_index: 4, delta: 'Pump B is at ' }),
  sse({ type: 'response.output_text.delta', output_index: 4, delta: 'North Works (Source: ontology).' }),
  sse({ type: 'response.completed', response: response([{ ...ontologyCall, status: 'completed' },
    { ...documentCall, status: 'completed' }, message(answer)]) }),
]

test('server-sent events are parsed across chunk boundaries and keep-alives are skipped', () => {
  const first = sseEvents('event: a\ndata: {"type":"a"}\n\n: keep-alive\n\ndata: {"type":')
  assert.deepEqual(first.events, [{ type: 'a' }])
  const second = sseEvents(`${first.rest}"b",\ndata: "x":1}\n\ndata: [DONE]\n\n`)
  assert.deepEqual(second, { events: [{ type: 'b', x: 1 }], rest: '' })
})

test('a streamed answer reports running tools and growing text, then returns the validated turn', async () => {
  // Network chunks: the first event is split mid-way, and the last text arrives with the final response.
  const head = liveEvents.slice(0, 4).join('')
  const chunks = [head.slice(0, 150), head.slice(150), liveEvents.slice(4, 6).join(''), liveEvents[6],
    liveEvents[7] + liveEvents[8]]
  const seen: StreamProgress[] = []
  const turn = await readStream(streamOf(chunks), (progress) => seen.push(progress))
  assert.deepEqual(turn, {
    responseId: 'caresp_1',
    text: answer,
    tools: [{ name: ontologyCall.name, status: 'completed' }, { name: documentCall.name, status: 'completed' }],
  })
  assert.ok(seen.some((progress) => progress.tools.some((tool) => tool.status === 'running') && !progress.text))
  assert.ok(seen.some((progress) => progress.tools.every((tool) => tool.status === 'done') && !progress.text))
  assert.ok(seen.some((progress) => progress.text === 'Pump B is at '))
  assert.ok(seen.every((progress, index) => index === 0 || progress.text.startsWith(seen[index - 1].text)))
})

test('stream failures, failed responses and early ends are explicit errors', async () => {
  await assert.rejects(readStream(streamOf([liveEvents[0], sse({ type: 'error', message: 'The agent did not answer in time.' })]),
    () => {}), /did not answer in time/)
  await assert.rejects(readStream(streamOf([sse({ type: 'response.failed',
    response: { id: 'caresp_1', status: 'failed', error: { message: 'tool unavailable' }, output: [] } })]), () => {}),
  /failed: tool unavailable/)
  await assert.rejects(readStream(streamOf(liveEvents.slice(0, 6)), () => {}), /stopped before the agent finished/)
})

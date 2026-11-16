import assert from 'node:assert/strict'
import test from 'node:test'
import type { ToolCall } from '../src/live/protocol.ts'
import { askAgent, type AgentRun } from '../server/foundry.ts'

const endpoint = 'https://ai-demo-abc123.services.ai.azure.com/api/projects/demo-project'
const cid = '8f14e45f-ceea-4e6b-9d55-1f1e2f3a4b5c'
const ontology = {
  id: 'mcp_1', type: 'mcp_call', server_label: 'fabric_iq_ontology', name: 'ask_ontology',
  arguments: JSON.stringify({ question: 'Which Asset rows have the Status value "Maintenance"? Return each asset name and the name of its site.' }),
}

function sse(...events: Record<string, unknown>[]): string {
  return events.map((event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join('')
}

function completed(text: string, id = 'resp_1') {
  return { type: 'response.completed', response: { id, status: 'completed', output: [
    { type: 'mcp_list_tools', server_label: 'fabric_iq_ontology', tools: [{ name: 'ask_ontology' }] },
    { ...ontology, output: '...' },
    { type: 'message', role: 'assistant', content: [{ type: 'output_text', text }] },
  ] } }
}

interface Captured { url?: string; init?: RequestInit }

// A fetch that streams the given body in small chunks, as Foundry does.
function fetcher(body: string, captured: Captured = {}, status = 200): typeof fetch {
  return (async (url: string | URL | Request, init?: RequestInit) => {
    captured.url = String(url)
    captured.init = init
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        for (let index = 0; index < body.length; index += 37) controller.enqueue(encoder.encode(body.slice(index, index + 37)))
        controller.close()
      },
    })
    return new Response(status === 200 ? stream : body, { status, headers: { 'Content-Type': status === 200 ? 'text/event-stream' : 'application/json' } })
  }) as typeof fetch
}

function run(fetchImpl: typeof fetch, extra: Partial<AgentRun> = {}): AgentRun {
  let now = 0
  return {
    endpoint, agent: 'demo-with-iq', token: 'user-token', question: 'Which assets are in maintenance, and at which site?',
    metadata: { cid, variant: 'with_iq' },
    signal: new AbortController().signal, timeoutMs: 60_000, onTool: () => {}, fetch: fetchImpl, clock: () => (now += 500), ...extra,
  }
}

test('the agent runs by name on the project Responses endpoint as the user, streamed, with value-free metadata', async () => {
  const captured: Captured = {}
  const tools: ToolCall[] = []
  const body = sse(
    { type: 'response.created', response: { id: 'resp_1', status: 'in_progress' } },
    { type: 'response.output_item.added', item: { ...ontology } },
    { type: 'response.output_item.done', item: { ...ontology, output: 'Pump B, North Works.' } },
    { type: 'response.output_text.delta', delta: 'Pump' },
    completed('**Pump B is in maintenance, at North Works.** [D1]'),
  )
  const outcome = await askAgent(run(fetcher(body, captured), { onTool: (call) => tools.push(call) }))
  assert.equal(captured.url, `${endpoint}/openai/v1/responses`)
  const headers = captured.init?.headers as Record<string, string>
  assert.equal(headers.Authorization, 'Bearer user-token')
  assert.equal(headers.Accept, 'text/event-stream')
  assert.equal(headers['x-ms-client-request-id'], cid)
  assert.deepEqual(JSON.parse(String(captured.init?.body)), {
    agent_reference: { type: 'agent_reference', name: 'demo-with-iq' }, input: 'Which assets are in maintenance, and at which site?',
    metadata: { cid, variant: 'with_iq' }, stream: true,
  })
  const done: ToolCall = { id: 'mcp_1', server: 'fabric_iq_ontology', tool: 'ask_ontology', state: 'completed', ms: 500, arguments: ontology.arguments }
  assert.deepEqual(tools, [{ id: 'mcp_1', server: 'fabric_iq_ontology', tool: 'ask_ontology', state: 'running' }, done])
  assert.deepEqual(outcome, { status: 'answered', text: '**Pump B is in maintenance, at North Works.** [D1]', responseId: 'resp_1', toolCalls: [done] })
})

test('a follow-up continues the previous response', async () => {
  const captured: Captured = {}
  const outcome = await askAgent(run(fetcher(sse(completed('It has been since Monday [D1].', 'resp_2')), captured), {
    question: 'Since when?', previousResponseId: 'resp_1',
  }))
  const body = JSON.parse(String(captured.init?.body)) as Record<string, unknown>
  assert.equal(body.previous_response_id, 'resp_1')
  assert.equal(body.input, 'Since when?')
  assert.equal(outcome.responseId, 'resp_2')
  const first: Captured = {}
  await askAgent(run(fetcher(sse(completed('x')), first)))
  assert.equal('previous_response_id' in (JSON.parse(String(first.init?.body)) as Record<string, unknown>), false)
})

test('a consent request becomes needs-consent with its link, even when the model answered anyway', async () => {
  const consent = { type: 'oauth_consent_request', id: 'oauthreq_1', server_label: 'fabric_iq_ontology', consent_link: 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize?client_id=x' }
  const outcome = await askAgent(run(fetcher(sse({ type: 'response.output_item.done', item: consent }, completed('Partial answer.')))))
  assert.deepEqual(outcome, {
    status: 'needs-consent', consentUrl: consent.consent_link, server: 'fabric_iq_ontology', responseId: 'resp_1', toolCalls: [],
  })
  // Work IQ asks each user once the same way, through its OAuth connection, before the agent has an answer.
  const work = await askAgent(run(fetcher(sse({ type: 'response.output_item.done', item: { ...consent, server_label: 'work_iq_mail' } }, completed('')))))
  assert.deepEqual(work, { status: 'needs-consent', consentUrl: consent.consent_link, server: 'work_iq_mail', responseId: 'resp_1', toolCalls: [] })
  // Fabric IQ can put the link in the tool's CONSENT_REQUIRED error instead.
  const failedCall = { id: 'mcp_9', type: 'mcp_call', server_label: 'fabric_iq_ontology', name: 'ask_ontology', error: { code: 'CONSENT_REQUIRED', message: 'Open https://login.microsoftonline.com/consent?id=1 to consent.' } }
  const fromError = await askAgent(run(fetcher(sse({ type: 'response.output_item.added', item: failedCall }, { type: 'response.output_item.done', item: failedCall }, completed('I could not reach Fabric IQ.')))))
  assert.equal(fromError.status, 'needs-consent')
  assert.equal(fromError.status === 'needs-consent' && fromError.consentUrl, 'https://login.microsoftonline.com/consent?id=1')
  assert.deepEqual(fromError.toolCalls.map((call) => [call.state, call.ms]), [['failed', 500]])
  // Only https links are offered.
  const unsafe = await askAgent(run(fetcher(sse({ type: 'response.output_item.done', item: { ...consent, consent_link: 'javascript:alert(1)' } }, completed('An answer.')))))
  assert.equal(unsafe.status, 'answered')
})

test('failed, incomplete and broken runs are failures with a reason, never empty answers', async () => {
  const failed = await askAgent(run(fetcher(sse({ type: 'response.failed', response: { id: 'resp_2', status: 'failed', error: { code: 'server_error', message: 'The model failed.' } } }))))
  assert.deepEqual(failed, { status: 'failed', message: 'The agent run failed: The model failed.', toolCalls: [], responseId: 'resp_2', code: 'server_error' })
  const cut = await askAgent(run(fetcher(sse({ type: 'response.incomplete', response: { id: 'resp_3', status: 'incomplete', incomplete_details: { reason: 'content_filter' }, output: [] } }))))
  assert.equal(cut.status === 'failed' && cut.code, 'content_filter')
  const empty = await askAgent(run(fetcher(sse(completed('')))))
  assert.match(empty.status === 'failed' ? empty.message : '', /without an answer/)
  const truncated = await askAgent(run(fetcher(sse({ type: 'response.created', response: { id: 'resp_4' } }, { type: 'response.output_item.added', item: ontology }))))
  assert.equal(truncated.status, 'failed')
  assert.deepEqual(truncated.toolCalls.map((call) => [call.state, call.error]), [['failed', 'The call did not finish.']])
  const errored = await askAgent(run(fetcher(sse({ type: 'error', code: 'rate_limit_exceeded', message: 'Too many requests.' }))))
  assert.deepEqual([errored.status, errored.status === 'failed' && errored.code], ['failed', 'rate_limit_exceeded'])
})

test('refused requests explain what to fix, without echoing more than the service message', async () => {
  const cases: [number, RegExp][] = [
    [401, /sign-in/], [403, /Foundry User role/], [404, /no agent demo-with-iq/], [429, /throttling/], [503, /HTTP 503/], [400, /HTTP 400.*bad input/],
  ]
  for (const [status, message] of cases) {
    const outcome = await askAgent(run(fetcher(JSON.stringify({ error: { code: 'x', message: 'bad input' } }), {}, status)))
    assert.match(outcome.status === 'failed' ? outcome.message : '', message, String(status))
    assert.ok(!JSON.stringify(outcome).includes('user-token'), String(status))
  }
})

test('a run past its budget or abandoned by the browser stops, and says which', async () => {
  const hanging = (async (_url: string | URL | Request, init?: RequestInit) => new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(sse({ type: 'response.output_item.added', item: ontology })))
      init?.signal?.addEventListener('abort', () => controller.error(new DOMException('aborted', 'AbortError')))
    },
  }), { status: 200 })) as typeof fetch
  const timedOut = await askAgent(run(hanging, { timeoutMs: 50 }))
  assert.equal(timedOut.status === 'failed' && timedOut.code, 'timeout')
  assert.equal(timedOut.toolCalls[0]?.state, 'failed')
  const browser = new AbortController()
  const pending = askAgent(run(hanging, { signal: browser.signal }))
  setTimeout(() => browser.abort(), 20)
  const cancelled = await pending
  assert.equal(cancelled.status === 'failed' && cancelled.code, 'cancelled')
  const unreachable = await askAgent(run((async () => { throw new TypeError('fetch failed') }) as typeof fetch))
  assert.match(unreachable.status === 'failed' ? unreachable.message : '', /could not be reached/)
})

test('a server that offers none of the allowed tools is named, next to the answer', async () => {
  const body = sse({ type: 'response.output_item.done', item: { type: 'mcp_list_tools', server_label: 'fabric_iq_ontology', tools: [] } }, completed('No rows.'))
  const outcome = await askAgent(run(fetcher(body)))
  assert.equal(outcome.status, 'answered')
  assert.deepEqual(outcome.toolCalls.at(-1), { id: 'list-fabric_iq_ontology', server: 'fabric_iq_ontology', tool: 'tools/list', state: 'failed', error: 'The server offered none of the allowed tools.' })
})

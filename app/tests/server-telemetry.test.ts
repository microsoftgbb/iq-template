import assert from 'node:assert/strict'
import { createServer, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import test from 'node:test'
import { SpanKind, SpanStatusCode, trace } from '@opentelemetry/api'
import { InMemorySpanExporter, NodeTracerProvider, SimpleSpanProcessor, type ReadableSpan } from '@opentelemetry/sdk-trace-node'
import type { TurnOutcome } from '../src/live/protocol.ts'
import { createApp } from '../server/app.ts'
import type { Verifier } from '../server/auth.ts'
import type { ServerConfig } from '../server/config.ts'
import { askAgent, type AgentRun } from '../server/foundry.ts'
import { SERVICE_NAME, routeOf, startTelemetry } from '../server/telemetry.ts'

// This file's own process holds the global provider: the server's spans land here instead of Application Insights.
const exporter = new InMemorySpanExporter()
new NodeTracerProvider({ spanProcessors: [new SimpleSpanProcessor(exporter)] }).register()

const cid = '8f14e45f-ceea-4e6b-9d55-1f1e2f3a4b5c'
const QUESTION = 'Which assets are in maintenance, and at which site?'
const config: ServerConfig = {
  port: 0,
  projectEndpoint: 'https://ai-demo-abc123.services.ai.azure.com/api/projects/demo-project',
  tenantId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  clientId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  agents: { with_iq: 'demo-with-iq', without_iq: 'demo-without-iq' },
  turnTimeoutMs: 60_000,
}
const verify: Verifier = Object.assign(
  async (header: string | undefined) => (header === 'Bearer good.token.value'
    ? { ok: true as const, token: 'good.token.value' } : { ok: false as const, message: 'Sign in to ask the agents.' }),
  { warmUp: async () => {} },
)

async function listen(ask: (run: AgentRun) => Promise<TurnOutcome>): Promise<{ server: Server; base: string }> {
  const app = createApp({ config, verify, ask, log: () => {}, fallback: (_req, res) => { res.writeHead(200).end('page') } })
  const server = createServer(app)
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  return { server, base: `http://127.0.0.1:${(server.address() as AddressInfo).port}` }
}

// A span ends just after its response does, so wait for it.
async function finished(name: string): Promise<ReadableSpan> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const span = exporter.getFinishedSpans().find((candidate) => candidate.name === name)
    if (span) return span
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  assert.fail(`no span named ${name}`)
}

function ask(outcome: TurnOutcome) {
  return async (run: AgentRun) => {
    run.onTool({ id: 'mcp_1', server: 'fabric_iq_ontology', tool: 'ask_ontology', state: 'running' })
    run.onTool({ id: 'mcp_1', server: 'fabric_iq_ontology', tool: 'ask_ontology', state: 'completed', ms: 18000, arguments: '{"question":"Maintenance"}' })
    return outcome
  }
}

function post(base: string, variant: string, extra: Record<string, string> = {}) {
  return fetch(`${base}/api/turns`, {
    method: 'POST',
    headers: { Authorization: 'Bearer good.token.value', 'Content-Type': 'application/json' },
    body: JSON.stringify({ cid, variant, question: QUESTION, ...extra }),
  })
}

test('each request but the health probe gets a server span named by its route', async (t) => {
  exporter.reset()
  const { server, base } = await listen(ask({ status: 'answered', text: 'x', toolCalls: [] }))
  t.after(() => server.close())
  assert.equal((await fetch(`${base}/healthz`)).status, 200)
  await (await fetch(`${base}/api/config`)).text()
  await (await fetch(`${base}/answers?debug=1`)).text()
  await (await fetch(`${base}/api/elsewhere`)).text()
  const span = await finished('GET /api/config')
  assert.equal(span.kind, SpanKind.SERVER)
  assert.deepEqual(span.attributes, { 'http.request.method': 'GET', 'http.route': '/api/config', 'http.response.status_code': 200 })
  assert.equal((await finished('GET /*')).attributes['http.response.status_code'], 200)
  assert.equal((await finished('GET /api/*')).attributes['http.response.status_code'], 404)
  assert.equal(exporter.getFinishedSpans().some((candidate) => candidate.name.includes('healthz')), false, 'probes are not traced')
  assert.deepEqual(['/api/turns', '/api/x', '/assets/index-1.js', '/'].map((path) => routeOf(path, ['/api/turns'])), ['/api/turns', '/api/*', '/assets/*', '/*'])
})

test('a question is an invoke_agent span inside its request, value-free, with each finished tool call as an event', async (t) => {
  exporter.reset()
  const { server, base } = await listen(ask({ status: 'answered', text: 'Pump B is in maintenance, at North Works [D1].', responseId: 'resp_9', toolCalls: [
    { id: 'mcp_1', server: 'fabric_iq_ontology', tool: 'ask_ontology', state: 'completed', ms: 18000 },
  ] }))
  t.after(() => server.close())
  await (await post(base, 'with_iq', { previousResponseId: 'resp_8' })).text()
  const request = await finished('POST /api/turns')
  const agent = await finished('invoke_agent demo-with-iq')
  assert.equal(agent.kind, SpanKind.CLIENT)
  assert.equal(agent.spanContext().traceId, request.spanContext().traceId)
  assert.equal(agent.parentSpanContext?.spanId, request.spanContext().spanId)
  assert.deepEqual(agent.attributes, {
    'gen_ai.operation.name': 'invoke_agent', 'gen_ai.agent.name': 'demo-with-iq', 'server.address': 'ai-demo-abc123.services.ai.azure.com',
    'app.variant': 'with_iq', 'app.cid': cid, 'app.follow_up': true, 'app.outcome': 'answered', 'app.tool_calls': 1,
    'app.tool_failures': 0, 'gen_ai.response.id': 'resp_9',
  })
  assert.deepEqual(agent.events.map((event) => [event.name, event.attributes]), [['app.tool_call', {
    'gen_ai.tool.name': 'ask_ontology', 'gen_ai.tool.call.id': 'mcp_1', 'app.tool.server': 'fabric_iq_ontology',
    'app.tool.iq': 'fabric', 'app.tool.state': 'completed', 'app.tool.duration_ms': 18000,
  }]])
  assert.equal(agent.status.code, SpanStatusCode.UNSET)
  const recorded = JSON.stringify(exporter.getFinishedSpans().map((span) => [span.name, span.attributes, span.events, span.status]))
  for (const value of ['good.token', 'Maintenance', 'North Works', QUESTION, 'resp_8']) {
    assert.ok(!recorded.includes(value), `spans must not record ${value}`)
  }
  assert.equal(SERVICE_NAME, 'scenario-assistant')
})

test('a failed question marks its span failed with the code only; a cancelled one does not', async (t) => {
  exporter.reset()
  const failure = await listen(async () => ({ status: 'failed', message: 'Foundry stopped the run: secret detail', code: 'timeout', toolCalls: [] }))
  t.after(() => failure.server.close())
  await (await post(failure.base, 'without_iq')).text()
  const failed = await finished('invoke_agent demo-without-iq')
  assert.deepEqual([failed.status, failed.attributes['app.outcome.code']], [{ code: SpanStatusCode.ERROR, message: 'timeout' }, 'timeout'])
  assert.ok(!JSON.stringify([failed.attributes, failed.events, failed.status]).includes('secret detail'))
  exporter.reset()
  const cancel = await listen(async () => ({ status: 'failed', message: 'Cancelled', code: 'cancelled', toolCalls: [] }))
  t.after(() => cancel.server.close())
  await (await post(cancel.base, 'without_iq')).text()
  assert.equal((await finished('invoke_agent demo-without-iq')).status.code, SpanStatusCode.UNSET)
})

test('the Foundry request carries the active span as W3C trace context', async () => {
  let headers: Record<string, string> = {}
  const fetcher = (async (_url: string | URL | Request, init?: RequestInit) => {
    headers = init?.headers as Record<string, string>
    const completed = { type: 'response.completed', response: { id: 'resp_1', status: 'completed', output: [
      { type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'ok' }] }] } }
    return new Response(`event: response.completed\ndata: ${JSON.stringify(completed)}\n\n`, { headers: { 'Content-Type': 'text/event-stream' } })
  }) as typeof fetch
  await trace.getTracer('test').startActiveSpan('invoke_agent test', async (span) => {
    await askAgent({
      endpoint: config.projectEndpoint, agent: 'demo-with-iq', token: 'user-token', question: 'x', metadata: { cid },
      signal: new AbortController().signal, timeoutMs: 10_000, onTool: () => {}, fetch: fetcher,
    })
    span.end()
    const { traceId, spanId } = span.spanContext()
    assert.equal(headers.traceparent, `00-${traceId}-${spanId}-01`)
  })
})

test('without Application Insights settings nothing starts and shutdown is immediate', async () => {
  await startTelemetry(undefined).shutdown()
})

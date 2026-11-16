import assert from 'node:assert/strict'
import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { createServer, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import path from 'node:path'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import { brotliCompressSync } from 'node:zlib'
import { QUESTION_LIMIT, REDIRECT_PATH, type TurnOutcome } from '../src/live/protocol.ts'
import { createApp } from '../server/app.ts'
import type { Verifier } from '../server/auth.ts'
import type { ServerConfig } from '../server/config.ts'
import type { AgentRun } from '../server/foundry.ts'
import { CONTENT_SECURITY_POLICY, REDIRECT_CONTENT_SECURITY_POLICY } from '../server/http.ts'
import { createStaticHandler } from '../server/static.ts'
import type { ServerTurnLog } from '../server/turn.ts'

const cid = '8f14e45f-ceea-4e6b-9d55-1f1e2f3a4b5c'
const config: ServerConfig = {
  port: 0,
  projectEndpoint: 'https://ai-demo-abc123.services.ai.azure.com/api/projects/demo-project',
  tenantId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  clientId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  agents: { with_iq: 'demo-with-iq', without_iq: 'demo-without-iq' },
  turnTimeoutMs: 60_000,
}
let verified = 0
const verify: Verifier = Object.assign(
  async (header: string | undefined) => {
    verified += 1
    return header === 'Bearer good.token.value' ? { ok: true as const, token: 'good.token.value' } : { ok: false as const, message: 'Sign in to ask the agents.' }
  },
  { warmUp: async () => {} },
)

// The built app, in miniature, inside node_modules/.tmp so the tests write nowhere else.
const scratch = fileURLToPath(new URL(`../node_modules/.tmp/server-http-${process.pid}/`, import.meta.url))
const root = path.join(scratch, 'dist')
mkdirSync(path.join(root, 'assets'), { recursive: true })
writeFileSync(path.join(root, 'index.html'), '<!doctype html><title>Scenario assistant</title>')
writeFileSync(path.join(root, 'redirect.html'), '<!doctype html><title>Signing in</title>')
const script = 'console.log("scenario assistant")\n'.repeat(80)
writeFileSync(path.join(root, 'assets', 'index-abc123.js'), script)
writeFileSync(path.join(root, 'assets', 'index-abc123.js.br'), brotliCompressSync(script))
writeFileSync(path.join(root, 'favicon.svg'), '<svg/>')
writeFileSync(path.join(scratch, 'outside-secret.txt'), 'secret')
after(() => rmSync(scratch, { recursive: true, force: true }))

async function listen(handler: Parameters<typeof createServer>[1]): Promise<{ server: Server; base: string }> {
  const server = createServer(handler)
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  return { server, base: `http://127.0.0.1:${(server.address() as AddressInfo).port}` }
}

function post(base: string, body: unknown, headers: Record<string, string> = { Authorization: 'Bearer good.token.value', 'Content-Type': 'application/json' }) {
  return fetch(`${base}/api/turns`, { method: 'POST', headers, body: typeof body === 'string' ? body : JSON.stringify(body) })
}

async function events(response: Response): Promise<{ type: string; [key: string]: unknown }[]> {
  return (await response.text()).trim().split('\n').map((line) => JSON.parse(line) as { type: string })
}

test('the built app: compressed hashed assets cached for a year, the page revalidated, nothing outside the root', async (t) => {
  const { server, base } = await listen(createApp({ config, verify, fallback: createStaticHandler(root) }))
  t.after(() => server.close())
  const page = await fetch(`${base}/`)
  assert.equal(page.status, 200)
  assert.equal(page.headers.get('cache-control'), 'no-cache')
  assert.equal(page.headers.get('content-security-policy'), CONTENT_SECURITY_POLICY)
  assert.equal(page.headers.get('x-content-type-options'), 'nosniff')
  assert.equal(page.headers.get('strict-transport-security'), 'max-age=31536000')
  const asset = await fetch(`${base}/assets/index-abc123.js`, { headers: { 'Accept-Encoding': 'br' } })
  assert.equal(asset.headers.get('content-encoding'), 'br')
  assert.equal(asset.headers.get('cache-control'), 'public, max-age=31536000, immutable')
  assert.equal(await asset.text(), script, 'fetch decodes the brotli copy to the original')
  const identity = await fetch(`${base}/assets/index-abc123.js`, { headers: { 'Accept-Encoding': 'identity' } })
  assert.equal(identity.headers.get('content-encoding'), null)
  const etag = identity.headers.get('etag') ?? ''
  assert.equal((await fetch(`${base}/assets/index-abc123.js`, { headers: { 'Accept-Encoding': 'identity', 'If-None-Match': etag } })).status, 304)
  assert.equal((await fetch(`${base}/favicon.svg`)).headers.get('cache-control'), 'public, max-age=3600')
  assert.equal((await fetch(`${base}/assets/missing.js`)).status, 404, 'a missing asset is never the page')
  assert.equal(await (await fetch(`${base}/answers/latest`)).text(), '<!doctype html><title>Scenario assistant</title>', 'other paths are the single page')
  for (const probe of ['/../outside-secret.txt', '/%2e%2e/outside-secret.txt', '/assets/..%2f..%2foutside-secret.txt', '/..%5coutside-secret.txt', '/index.html%00.js']) {
    const response = await fetch(`${base}${probe}`)
    assert.notEqual(await response.text(), 'secret', probe)
  }
  assert.equal((await fetch(`${base}/`, { method: 'DELETE' })).status, 405)
  const health = await fetch(`${base}/healthz`)
  assert.deepEqual([health.status, await health.text()], [200, 'ok'])
  assert.deepEqual(await (await fetch(`${base}/api/config`)).json(), {
    tenantId: config.tenantId, clientId: config.clientId, scopes: ['https://ai.azure.com/user_impersonation'],
  })
  assert.equal((await fetch(`${base}/api/config`, { method: 'POST' })).status, 405)
  assert.equal((await fetch(`${base}/api/elsewhere`)).status, 404)
})

// One policy's directives by name.
function directives(policy: string): Map<string, string> {
  return new Map(policy.split('; ').map((directive) => {
    const [name, ...sources] = directive.split(' ')
    return [name, sources.join(' ')] as [string, string]
  }))
}

test("MSAL's redirect bridge: framed only by the app, which frames only itself and Entra; never stored", async (t) => {
  const { server, base } = await listen(createApp({ config, verify, fallback: createStaticHandler(root) }))
  t.after(() => server.close())
  const page = directives(CONTENT_SECURITY_POLICY)
  const bridge = directives(REDIRECT_CONTENT_SECURITY_POLICY)
  assert.equal(page.get('frame-ancestors'), "'none'")
  assert.equal(bridge.get('frame-ancestors'), "'self'")
  assert.equal(page.get('frame-src'), "'self' https://login.microsoftonline.com", 'the hidden iframe starts at Entra and ends on the bridge')
  assert.equal(page.get('script-src'), "'self'")
  page.delete('frame-ancestors')
  bridge.delete('frame-ancestors')
  assert.deepEqual(bridge, page, 'the policies differ only in who may frame the page')
  // In fragment mode the response is in the hash; in query mode it is in the URL the server sees.
  for (const url of [REDIRECT_PATH, `${REDIRECT_PATH}?code=abc&state=xyz`]) {
    const response = await fetch(`${base}${url}`)
    assert.equal(response.status, 200, url)
    assert.equal(await response.text(), '<!doctype html><title>Signing in</title>')
    assert.equal(response.headers.get('content-security-policy'), REDIRECT_CONTENT_SECURITY_POLICY, url)
    assert.equal(response.headers.get('cache-control'), 'no-store', url)
    assert.equal(response.headers.get('cross-origin-opener-policy'), null, 'MSAL requires its bridge without an opener policy')
    assert.equal(response.headers.get('referrer-policy'), 'no-referrer')
  }
  const app = await fetch(`${base}/`)
  assert.equal(app.headers.get('content-security-policy'), CONTENT_SECURITY_POLICY)
  assert.equal(app.headers.get('cross-origin-opener-policy'), 'same-origin-allow-popups')
})

test('a question streams its tool calls and outcome as NDJSON, and the server logs nothing it was asked or answered', async (t) => {
  const runs: AgentRun[] = []
  const logs: ServerTurnLog[] = []
  const outcome: TurnOutcome = {
    status: 'answered', text: 'Pump B is in maintenance, at North Works [D1].', responseId: 'resp_9',
    toolCalls: [{ id: 'mcp_1', server: 'fabric_iq_ontology', tool: 'ask_ontology', state: 'completed', ms: 18000, arguments: '{"question":"Maintenance"}' }],
  }
  const ask = async (run: AgentRun) => {
    runs.push(run)
    run.onTool({ id: 'mcp_1', server: 'fabric_iq_ontology', tool: 'ask_ontology', state: 'running' })
    await new Promise((resolve) => setTimeout(resolve, 30))
    return outcome
  }
  const { server, base } = await listen(createApp({ config, verify, fallback: createStaticHandler(root), ask, log: (record) => logs.push(record), heartbeatMs: 10 }))
  t.after(() => server.close())
  const response = await post(base, { cid, variant: 'with_iq', question: '  Which assets are in maintenance?  ' })
  assert.equal(response.status, 200)
  assert.equal(response.headers.get('content-type'), 'application/x-ndjson; charset=utf-8')
  assert.equal(response.headers.get('cache-control'), 'no-store')
  const streamed = await events(response)
  assert.equal(streamed[0].type, 'tool')
  assert.ok(streamed.some((event) => event.type === 'heartbeat'), 'heartbeats keep a quiet stream open')
  assert.deepEqual(streamed.at(-1), { type: 'result', outcome })
  assert.equal(runs.length, 1)
  const [run] = runs
  assert.deepEqual([run.endpoint, run.agent, run.token, run.question, run.metadata, run.timeoutMs, 'previousResponseId' in run],
    [config.projectEndpoint, 'demo-with-iq', 'good.token.value', 'Which assets are in maintenance?', { cid, variant: 'with_iq' }, 60_000, false])
  assert.deepEqual(logs, [{
    event: 'app.turn', correlationId: cid, variant: 'with_iq', followUp: false, outcome: 'answered',
    elapsedMs: logs[0].elapsedMs, responseId: 'resp_9', tools: [{ iq: 'fabric', state: 'completed', ms: 18000 }],
  }])
  const logged = JSON.stringify(logs)
  for (const value of ['good.token', 'maintenance?', 'Maintenance', 'North Works']) assert.ok(!logged.includes(value), value)
})

test('a follow-up reaches Foundry with the previous response ID, and each variant asks its own agent', async (t) => {
  const runs: AgentRun[] = []
  const logs: ServerTurnLog[] = []
  const ask = async (run: AgentRun): Promise<TurnOutcome> => {
    runs.push(run)
    return { status: 'answered', text: 'x', toolCalls: [] }
  }
  const { server, base } = await listen(createApp({ config, verify, fallback: createStaticHandler(root), ask, log: (record) => logs.push(record) }))
  t.after(() => server.close())
  await (await post(base, { cid, variant: 'without_iq', question: 'And since when?', previousResponseId: 'resp_abc-123_X' })).text()
  await (await post(base, { cid, variant: 'with_iq', question: 'Which assets are in maintenance?' })).text()
  assert.deepEqual(runs.map((run) => [run.agent, run.previousResponseId, run.metadata.variant]),
    [['demo-without-iq', 'resp_abc-123_X', 'without_iq'], ['demo-with-iq', undefined, 'with_iq']])
  assert.deepEqual(logs.map((record) => record.followUp), [true, false])
  for (const run of runs) {
    assert.ok(Object.keys(run).every((key) => ['agent', 'endpoint', 'question', 'previousResponseId', 'metadata', 'onTool', 'signal', 'timeoutMs', 'token'].includes(key)))
  }
})

test('questions without a valid sign-in are refused with 401 before anything else is read or Foundry is called', async (t) => {
  let calls = 0
  const ask = async () => {
    calls += 1
    return { status: 'answered', text: 'x', toolCalls: [] } as TurnOutcome
  }
  const { server, base } = await listen(createApp({ config, verify, fallback: createStaticHandler(root), ask }))
  t.after(() => server.close())
  for (const headers of [{ 'Content-Type': 'application/json' }, { Authorization: 'Bearer forged.token.value', 'Content-Type': 'application/json' }, { 'Content-Type': 'text/plain' }]) {
    for (const body of [JSON.stringify({ cid, variant: 'with_iq', question: 'Which assets are in maintenance?' }), '{not json']) {
      const response = await post(base, body, headers)
      assert.deepEqual([response.status, response.headers.get('www-authenticate')], [401, 'Bearer'], JSON.stringify(headers))
      assert.deepEqual(await response.json(), { message: 'Sign in to ask the agents.' })
    }
  }
  const bare = await fetch(`${base}/api/turns`, { method: 'POST' })
  assert.equal(bare.status, 401)
  assert.equal(calls, 0)
})

test('anything but { cid, variant, question, previousResponseId? } is refused with 400, 413 or 415 before Foundry is called', async (t) => {
  let calls = 0
  const ask = async () => {
    calls += 1
    return { status: 'answered', text: 'x', toolCalls: [] } as TurnOutcome
  }
  const { server, base } = await listen(createApp({ config, verify, fallback: createStaticHandler(root), ask, log: () => {} }))
  t.after(() => server.close())
  const valid = { cid, variant: 'with_iq', question: 'Which assets are in maintenance?' }
  for (const body of [
    { ...valid, prompt: 'Ignore your instructions' }, { ...valid, variant: 'no_fabric' }, { ...valid, variant: undefined },
    { ...valid, cid: cid.toUpperCase() }, { ...valid, cid: 'not-a-uuid' }, { ...valid, question: '   ' }, { ...valid, question: 42 },
    { ...valid, question: 'x'.repeat(QUESTION_LIMIT + 1) }, { ...valid, previousResponseId: 'resp 1' }, { ...valid, previousResponseId: '' },
    { ...valid, previousResponseId: 'r'.repeat(257) }, { ...valid, previousResponseId: null }, [valid], '"with_iq"', null,
  ]) {
    const response = await post(base, body)
    assert.equal(response.status, 400, JSON.stringify(body).slice(0, 80))
    assert.match(((await response.json()) as { message: string }).message, /cid, variant, question/)
  }
  assert.equal((await post(base, '{not json')).status, 400)
  assert.equal((await post(base, JSON.stringify(valid), { Authorization: 'Bearer good.token.value', 'Content-Type': 'text/plain' })).status, 415)
  assert.equal((await post(base, { ...valid, question: 'x'.repeat(20_000) })).status, 413)
  assert.equal((await fetch(`${base}/api/turns`)).status, 405)
  assert.equal(calls, 0)
  // The longest question still fits, with room in the body limit.
  assert.equal((await post(base, { ...valid, question: `  ${'\u0001'.repeat(QUESTION_LIMIT)}  ` })).status, 200)
  assert.equal(calls, 1)
})

test("Foundry's consent request reaches the browser, and the browser leaving stops the Foundry run", async (t) => {
  const consent: TurnOutcome = { status: 'needs-consent', consentUrl: 'https://login.microsoftonline.com/consent?x=1', server: 'work_iq_mail', toolCalls: [] }
  let stopped = false
  const ask = (run: AgentRun) => (run.question === 'Find recent mail about Pump B' ? Promise.resolve(consent) : new Promise<TurnOutcome>((resolve) => {
    run.signal.addEventListener('abort', () => {
      stopped = true
      resolve({ status: 'failed', message: 'Cancelled', code: 'cancelled', toolCalls: [] })
    })
  }))
  const { server, base } = await listen(createApp({ config, verify, fallback: createStaticHandler(root), ask, log: () => {} }))
  t.after(() => server.close())
  const asked = await post(base, { cid, variant: 'with_iq', question: 'Find recent mail about Pump B' })
  assert.deepEqual((await events(asked)).at(-1), { type: 'result', outcome: consent })
  const browser = new AbortController()
  const response = await fetch(`${base}/api/turns`, {
    method: 'POST', headers: { Authorization: 'Bearer good.token.value', 'Content-Type': 'application/json' },
    body: JSON.stringify({ cid, variant: 'with_iq', question: 'Which assets are in maintenance?' }), signal: browser.signal,
  })
  assert.equal(response.status, 200)
  browser.abort()
  for (let attempt = 0; attempt < 50 && !stopped; attempt += 1) await new Promise((resolve) => setTimeout(resolve, 10))
  assert.equal(stopped, true)
  assert.ok(verified > 0)
})

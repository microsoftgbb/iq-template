import assert from 'node:assert/strict'
import { once } from 'node:events'
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { createServer, request } from 'node:http'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { createHandler, routeName, settings, upstreamBody, MAX_INPUT_CHARS } from '../server/handlers.mjs'

const env = {
  AGENT_RESPONSES_URL: 'https://ai-demo.services.ai.azure.com/api/projects/demo-project/agents/demo-assistant/endpoint/protocols/openai/responses?api-version=v1',
  ENTRA_CLIENT_ID: '11111111-1111-4111-8111-111111111111',
  ENTRA_TENANT_ID: '22222222-2222-4222-8222-222222222222',
  AGENT_NAME: 'demo-assistant',
}

async function start(fetchImpl, logs = []) {
  const base = await mkdtemp(path.join(tmpdir(), 'app-'))
  const root = path.join(base, 'dist')
  await mkdir(path.join(root, 'assets'), { recursive: true })
  await writeFile(path.join(root, 'index.html'), '<!doctype html><title>app</title>')
  await writeFile(path.join(root, 'assets', 'app-abc.js'), 'console.log(1)')
  await writeFile(path.join(base, 'outside.txt'), 'secret')
  const calls = []
  const server = createServer(createHandler({
    settings: settings(env), root, log: (line) => logs.push(JSON.parse(line)),
    fetch: async (url, init) => {
      calls.push({ url, init })
      return fetchImpl(url, init)
    },
  }))
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  return {
    base: `http://127.0.0.1:${server.address().port}`, calls,
    async close() {
      await new Promise((resolve) => server.close(resolve))
      await rm(base, { recursive: true, force: true })
    },
  }
}

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

test('settings require the agent endpoint and Entra registration', () => {
  assert.equal(settings(env).config.scope, 'https://ai.azure.com/user_impersonation')
  for (const change of [{ AGENT_RESPONSES_URL: 'http://insecure.example' }, { ENTRA_CLIENT_ID: 'x' }, { AGENT_NAME: '' }]) {
    assert.throws(() => settings({ ...env, ...change }))
  }
})

test('the request body is narrowed to the question and conversation continuation', () => {
  assert.deepEqual(upstreamBody({ input: ' hi ' }), { input: 'hi', stream: false })
  assert.deepEqual(upstreamBody({ input: 'hi', previous_response_id: 'caresp_1' }),
    { input: 'hi', stream: false, previous_response_id: 'caresp_1' })
  assert.deepEqual(upstreamBody({ input: 'hi', stream: true }), { input: 'hi', stream: true })
  assert.throws(() => upstreamBody({ input: 'hi', stream: 'yes' }), (error) => error.status === 400)
  for (const value of [null, [], {}, { input: '' }, { input: 'x'.repeat(MAX_INPUT_CHARS + 1) }, { input: 'hi', model: 'x' },
    { input: 'hi', previous_response_id: '../x' }, { input: 'hi', tools: [] }]) {
    assert.throws(() => upstreamBody(value), (error) => error.status === 400)
  }
})

test('the proxy forwards only the user token and question to the configured endpoint', async () => {
  const app = await start(async () => json(200, { id: 'caresp_1', status: 'completed', output: [] }))
  try {
    const response = await fetch(`${app.base}/api/responses`, {
      method: 'POST',
      headers: { Authorization: 'Bearer user.jwt.token', 'Content-Type': 'application/json', Cookie: 'x=y', 'X-Forwarded-Host': 'evil' },
      body: JSON.stringify({ input: 'Which assets are in Maintenance?' }),
    })
    assert.equal(response.status, 200)
    assert.equal((await response.json()).id, 'caresp_1')
    assert.equal(app.calls.length, 1)
    const [{ url, init }] = app.calls
    assert.equal(url, env.AGENT_RESPONSES_URL)
    assert.deepEqual(init.headers, { Authorization: 'Bearer user.jwt.token', 'Content-Type': 'application/json', Accept: 'application/json' })
    assert.deepEqual(JSON.parse(init.body), { input: 'Which assets are in Maintenance?', stream: false })
    assert.equal(init.redirect, 'error')
  } finally {
    await app.close()
  }
})

test('the proxy refuses anonymous or malformed requests without calling the agent', async () => {
  const app = await start(async () => json(200, {}))
  try {
    const anonymous = await fetch(`${app.base}/api/responses`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ input: 'hi' }),
    })
    assert.equal(anonymous.status, 401)
    const text = await fetch(`${app.base}/api/responses`, {
      method: 'POST', headers: { Authorization: 'Bearer a.b.c', 'Content-Type': 'text/plain' }, body: 'hi',
    })
    assert.equal(text.status, 415)
    const large = await fetch(`${app.base}/api/responses`, {
      method: 'POST', headers: { Authorization: 'Bearer a.b.c', 'Content-Type': 'application/json' },
      body: JSON.stringify({ input: 'x'.repeat(20_000) }),
    })
    assert.equal(large.status, 413)
    assert.equal((await fetch(`${app.base}/api/responses`)).status, 405)
    assert.equal(app.calls.length, 0)
  } finally {
    await app.close()
  }
})

test('agent failures keep their status and never become an empty success', async () => {
  for (const [status, body] of [[403, { error: { message: 'denied' } }], [500, 'not json']]) {
    const app = await start(async () => new Response(typeof body === 'string' ? body : JSON.stringify(body), { status }))
    try {
      const response = await fetch(`${app.base}/api/responses`, {
        method: 'POST', headers: { Authorization: 'Bearer a.b.c', 'Content-Type': 'application/json' },
        body: JSON.stringify({ input: 'hi' }),
      })
      assert.equal(response.status, status)
      assert.ok((await response.json()).error.message)
    } finally {
      await app.close()
    }
  }
  const unreachable = await start(async () => { throw new TypeError('fetch failed') })
  try {
    const response = await fetch(`${unreachable.base}/api/responses`, {
      method: 'POST', headers: { Authorization: 'Bearer a.b.c', 'Content-Type': 'application/json' },
      body: JSON.stringify({ input: 'hi' }),
    })
    assert.equal(response.status, 502)
  } finally {
    await unreachable.close()
  }
})

test('static files, runtime configuration and health are served with security headers', async () => {
  const app = await start(async () => json(200, {}))
  try {
    const health = await fetch(`${app.base}/healthz`)
    assert.equal(await health.text(), 'ok')
    const config = await (await fetch(`${app.base}/config.json`)).json()
    assert.deepEqual(config, {
      clientId: env.ENTRA_CLIENT_ID, tenantId: env.ENTRA_TENANT_ID,
      scope: 'https://ai.azure.com/user_impersonation', agentName: 'demo-assistant',
    })
    const page = await fetch(`${app.base}/some/route`)
    assert.match(await page.text(), /<title>app<\/title>/)
    assert.match(page.headers.get('content-security-policy'), /frame-ancestors 'none'/)
    const asset = await fetch(`${app.base}/assets/app-abc.js`)
    assert.match(asset.headers.get('cache-control'), /immutable/)
    assert.equal((await fetch(`${app.base}/missing.js`)).status, 404)
    assert.equal((await fetch(`${app.base}/%2e%2e/outside.txt`)).status, 404)
    assert.equal((await fetch(`${app.base}/%E0%A4%A`)).status, 404)
  } finally {
    await app.close()
  }
})

const authorization = ['Bearer', 'user-token'].join(' ')

test('a malformed request target is refused without stopping the server', async () => {
  const app = await start(async () => json(200, {}))
  try {
    const { hostname, port } = new URL(app.base)
    const status = await new Promise((resolve, reject) => {
      const probe = request({ hostname, port, path: '//', method: 'GET' }, (answer) => {
        answer.resume()
        resolve(answer.statusCode)
      })
      probe.on('error', reject)
      probe.end()
    })
    assert.equal(status, 400)
    assert.equal(await (await fetch(`${app.base}/healthz`)).text(), 'ok')
  } finally {
    await app.close()
  }
})

test('an answer that stalls or drops after its headers is a gateway error', async () => {
  for (const [error, status] of [[new DOMException('timed out', 'TimeoutError'), 504], [new TypeError('terminated'), 502]]) {
    const app = await start(async () => new Response(new ReadableStream({ start(controller) { controller.error(error) } })))
    try {
      const response = await fetch(`${app.base}/api/responses`, {
        method: 'POST', headers: { Authorization: authorization, 'Content-Type': 'application/json' },
        body: JSON.stringify({ input: 'hi' }),
      })
      assert.equal(response.status, status)
      assert.ok((await response.json()).error.message)
    } finally {
      await app.close()
    }
  }
})

test('request telemetry is named by a low-cardinality route', () => {
  assert.deepEqual(['/api/responses', '/config.json', '/healthz', '/assets/app-abc.js', '/', '/some/route', null].map(routeName),
    ['/api/responses', '/config.json', '/healthz', '/assets/*', '/*', '/*', 'invalid'])
})

const encoder = new TextEncoder()
const sseResponse = (start) => new Response(new ReadableStream({ start }), { headers: { 'Content-Type': 'text/event-stream' } })
const streamed = (app, signal) => fetch(`${app.base}/api/responses`, {
  method: 'POST', signal,
  headers: { Authorization: authorization, 'Content-Type': 'application/json' },
  body: JSON.stringify({ input: 'hi', stream: true }),
})

test('streamed answers are relayed unchanged as server-sent events', async () => {
  const events = 'event: response.created\ndata: {"type":"response.created"}\n\n: keep-alive\n\n'
    + 'event: response.completed\ndata: {"type":"response.completed"}\n\n'
  const app = await start(async () => sseResponse((controller) => {
    controller.enqueue(encoder.encode(events.slice(0, 40)))
    controller.enqueue(encoder.encode(events.slice(40)))
    controller.close()
  }))
  try {
    const response = await streamed(app)
    assert.equal(response.status, 200)
    assert.match(response.headers.get('content-type'), /^text\/event-stream/)
    assert.match(response.headers.get('content-security-policy'), /default-src 'self'/)
    assert.equal(await response.text(), events)
    const [{ init }] = app.calls
    assert.equal(init.headers.Accept, 'text/event-stream')
    assert.deepEqual(JSON.parse(init.body), { input: 'hi', stream: true })
  } finally {
    await app.close()
  }
  // An agent error before streaming starts keeps its status and JSON body.
  const refused = await start(async () => json(403, { error: { message: 'denied' } }))
  try {
    const response = await streamed(refused)
    assert.equal(response.status, 403)
    assert.equal((await response.json()).error.message, 'denied')
  } finally {
    await refused.close()
  }
})

test('a stream that fails after it started ends with an error event', async () => {
  // The first read delivers an event; the next one fails, as a timed-out connection does.
  let reads = 0
  const app = await start(async () => new Response(new ReadableStream({
    pull(controller) {
      reads += 1
      if (reads === 1) controller.enqueue(encoder.encode('data: {"type":"response.created"}\n\n'))
      else controller.error(new DOMException('timed out', 'TimeoutError'))
    },
  }), { headers: { 'Content-Type': 'text/event-stream' } }))
  try {
    const response = await streamed(app)
    assert.equal(response.status, 200)
    assert.equal(await response.text(), 'data: {"type":"response.created"}\n\n'
      + 'event: error\ndata: {"type":"error","message":"The agent did not answer in time."}\n\n')
  } finally {
    await app.close()
  }
})

test('the agent call stops when the browser leaves mid-answer', async () => {
  let upstreamSignal
  const app = await start(async (url, init) => {
    upstreamSignal = init.signal
    return sseResponse((controller) => controller.enqueue(encoder.encode('data: {"type":"response.created"}\n\n')))
  })
  try {
    const browser = new AbortController()
    const response = await streamed(app, browser.signal)
    const reader = response.body.getReader()
    await reader.read()
    browser.abort()
    for (let attempt = 0; attempt < 50 && !upstreamSignal.aborted; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 20))
    }
    assert.equal(upstreamSignal.aborted, true)
  } finally {
    await app.close()
  }
})

test('a browser that stops reading and leaves still ends the relay and the agent call', async () => {
  // The browser never reads, so the relay waits for it to drain; leaving must end that wait too.
  let pulls = 0
  let upstreamSignal
  const logs = []
  const app = await start(async (url, init) => {
    upstreamSignal = init.signal
    return new Response(new ReadableStream({
      pull(controller) {
        pulls += 1
        controller.enqueue(encoder.encode(`: ${'x'.repeat(65536)}\n\n`))
      },
    }), { headers: { 'Content-Type': 'text/event-stream' } })
  }, logs)
  try {
    const browser = request(`${app.base}/api/responses`, {
      method: 'POST',
      headers: { Authorization: authorization, 'Content-Type': 'application/json' },
    })
    browser.on('error', () => {})
    browser.end(JSON.stringify({ input: 'hi', stream: true }))
    const [response] = await once(browser, 'response')
    response.pause()
    let settled = -1
    for (let attempt = 0; attempt < 200 && pulls !== settled; attempt += 1) {
      settled = pulls
      await new Promise((resolve) => setTimeout(resolve, 25))
    }
    assert.equal(pulls, settled, 'the relay should wait for the browser to read')
    browser.destroy()
    for (let attempt = 0; attempt < 100 && !logs.length; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 20))
    }
    assert.equal(upstreamSignal.aborted, true)
    assert.deepEqual(logs.map(({ route, status }) => ({ route, status })), [{ route: '/api/responses', status: 200 }])
  } finally {
    await app.close()
  }
})

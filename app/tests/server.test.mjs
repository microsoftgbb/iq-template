import assert from 'node:assert/strict'
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { createHandler, settings, upstreamBody, MAX_INPUT_CHARS } from '../server/handlers.mjs'

const env = {
  AGENT_RESPONSES_URL: 'https://ai-demo.services.ai.azure.com/api/projects/demo-project/agents/demo-assistant/endpoint/protocols/openai/responses?api-version=v1',
  ENTRA_CLIENT_ID: '11111111-1111-4111-8111-111111111111',
  ENTRA_TENANT_ID: '22222222-2222-4222-8222-222222222222',
  AGENT_NAME: 'demo-assistant',
}

async function start(fetchImpl) {
  const base = await mkdtemp(path.join(tmpdir(), 'app-'))
  const root = path.join(base, 'dist')
  await mkdir(path.join(root, 'assets'), { recursive: true })
  await writeFile(path.join(root, 'index.html'), '<!doctype html><title>app</title>')
  await writeFile(path.join(root, 'assets', 'app-abc.js'), 'console.log(1)')
  await writeFile(path.join(base, 'outside.txt'), 'secret')
  const calls = []
  const server = createServer(createHandler({
    settings: settings(env), root, log: () => {},
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

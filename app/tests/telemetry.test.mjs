import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const telemetry = fileURLToPath(new URL('../server/telemetry.mjs', import.meta.url))
// Points at a closed local port, so nothing leaves the machine.
const connectionString = 'InstrumentationKey=00000000-0000-0000-0000-000000000000;'
  + 'IngestionEndpoint=http://127.0.0.1:9/;LiveEndpoint=http://127.0.0.1:9/'

async function listen() {
  const headers = []
  const server = createServer((request, response) => {
    headers.push(request.headers.traceparent ?? null)
    response.end('{}')
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  return { server, headers, url: `http://127.0.0.1:${server.address().port}/responses` }
}

// Run fetch calls in a child process that preloads the telemetry module, as the container does.
async function traceparents(env) {
  const agent = await listen()
  const other = await listen()
  try {
    const script = `await fetch(${JSON.stringify(agent.url)}); await fetch(${JSON.stringify(other.url)}); process.exit(0)`
    const child = spawn(process.execPath, ['--import', telemetry, '--input-type=module', '-e', script], {
      env: { PATH: process.env.PATH, AGENT_RESPONSES_URL: agent.url, APPLICATIONINSIGHTS_STATSBEAT_DISABLED: 'true', ...env },
      stdio: ['ignore', 'ignore', 'pipe'],
    })
    let stderr = ''
    child.stderr.on('data', (chunk) => { stderr += chunk })
    const code = await new Promise((resolve) => child.on('exit', resolve))
    assert.equal(code, 0, stderr)
    return { agent: agent.headers, other: other.headers }
  } finally {
    agent.server.close()
    other.server.close()
  }
}

test('with a connection string, agent calls carry W3C trace context and other calls do not', { timeout: 60_000 }, async () => {
  const seen = await traceparents({ APPLICATIONINSIGHTS_CONNECTION_STRING: connectionString })
  assert.equal(seen.agent.length, 1)
  assert.match(seen.agent[0] ?? '', /^00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]$/)
  assert.deepEqual(seen.other, [null])
})

test('without a connection string, telemetry loads nothing and adds no headers', { timeout: 60_000 }, async () => {
  const seen = await traceparents({})
  assert.deepEqual(seen, { agent: [null], other: [null] })
})

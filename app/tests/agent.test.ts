import assert from 'node:assert/strict'
import test from 'node:test'
import { agentTurn, failureMessage, proofMatches, redact, PROOF_PREFIX } from '../src/agent.ts'
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
  assert.match(failureMessage(403, { error: { message: 'denied' } }), /Foundry User role.*\(denied\)/)
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

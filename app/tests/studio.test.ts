import assert from 'node:assert/strict'
import test from 'node:test'
import { connectionManagerUrl, studioDiagnostic, studioFailure, studioProof, studioReply } from '../src/studio.ts'

const challenge = 'connection proof 12345678-1234-4234-8234-123456789abc'
const proofReply = `CPS_AGENT_PROOF|${challenge}`

test('a conversation receipt is accepted, never reported as an agent reply', () => {
  for (const key of ['ConversationId', 'conversationID', 'conversationId']) {
    assert.deepEqual(studioProof({ [key]: 'native-conversation' }, challenge), {
      status: 'accepted', conversationId: 'native-conversation', challenge,
    })
  }
  assert.throws(
    () => studioProof({ conversationID: 'one', ConversationId: 'another' }, challenge),
    /native conversation receipt/,
  )
})

test('only the exact fresh challenge proves the published agent answered', () => {
  assert.deepEqual(
    studioProof({ conversationId: 'native-conversation', responses: [proofReply], completed: false, isExpectingInput: false }, challenge),
    { status: 'responded', conversationId: 'native-conversation', challenge, reply: proofReply },
  )
  assert.deepEqual(
    studioProof({ conversationId: 'native-conversation', responses: [], completed: false, isExpectingInput: true }, challenge),
    { status: 'needs-input', conversationId: 'native-conversation', challenge },
  )
  for (const reply of ['CPS_AGENT_PROOF|older-challenge', '="CPS_AGENT_PROOF|" & System.Activity.Text']) {
    assert.throws(() => studioProof({ conversationId: 'native-conversation', lastResponse: reply }, challenge), /fresh challenge/)
  }
  for (const flags of [{ completed: 'true' }, { isExpectingInput: 1 }]) {
    assert.throws(
      () => studioProof({ conversationId: 'native-conversation', responses: [proofReply], ...flags }, challenge),
      /malformed (completed|isExpectingInput) flag/,
    )
  }
})

test('malformed responses remain explicit failures', () => {
  for (const value of [undefined, {}, { ConversationId: '' }, { ConversationId: 'native', responses: [null] }]) {
    assert.throws(() => studioProof(value, challenge))
  }
  assert.throws(() => studioReply({ conversationId: 'native', responses: [42] }), /malformed response/)
})

test('an answer requires non-empty agent text, not just a receipt', () => {
  assert.deepEqual(
    studioReply({ conversationId: 'native-conversation', responses: ['Pump B is in Maintenance.', '', 'Pump B is in Maintenance.'], completed: false }),
    { status: 'answered', conversationId: 'native-conversation', responses: ['Pump B is in Maintenance.'] },
  )
  for (const value of [
    { conversationId: 'native-conversation', responses: [] },
    { conversationId: 'native-conversation', responses: ['   '], isExpectingInput: false },
    { conversationID: 'native-conversation' },
  ]) {
    assert.throws(() => studioReply(value), /without an agent answer/)
  }
})

test('a dropped card is reported as waiting for input with the connection manager', () => {
  assert.deepEqual(
    studioReply({ conversationId: 'native-conversation', responses: [], completed: false, isExpectingInput: true }),
    { status: 'needs-input', conversationId: 'native-conversation' },
  )
  assert.equal(
    connectionManagerUrl('tenant', 'environment', 'iq_demo_assistant', 'a/b'),
    'https://copilotstudio.microsoft.com/c2/tenants/tenant/environments/environment/bots/iq_demo_assistant/channels/pva-autonomous/conversations/a%2Fb/user-connections',
  )
})

test('the connector refusal for GitHub Copilot harness agents is not an answer', () => {
  assert.throws(() => studioReply({
    conversationId: 'native-conversation',
    responses: ["This action doesn't support agents built with the GitHub Copilot harness. [Learn more](https://learn.microsoft.com/en-us/power-automate/call-copilot-studio-agent)"],
    completed: false,
    isExpectingInput: false,
  }), /does not support GitHub Copilot harness agents/)
})

test('connector timeouts explain the 120-second limit', () => {
  assert.match(studioFailure('{"error":{"code":"TimedOut","message":"Invocation of API timed out.","details":[]}}'), /120-second limit/)
  assert.equal(studioFailure('Forbidden'), 'Forbidden')
  assert.equal(studioFailure(undefined), 'The Copilot Studio connector call failed.')
})

test('diagnostic output redacts credential-like fields without hiding protocol receipts', () => {
  assert.deepEqual(studioDiagnostic({
    ConversationId: 'native-conversation',
    nested: [{ accessToken: 'never-display', authorization: 'never-display', message: 'native message' }],
  }), {
    ConversationId: 'native-conversation',
    nested: [{ accessToken: '[redacted]', authorization: '[redacted]', message: 'native message' }],
  })
})

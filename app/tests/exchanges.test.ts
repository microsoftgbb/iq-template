import assert from 'node:assert/strict'
import test from 'node:test'
import { followUpFrom, newTurn, reduce, type Exchange } from '../src/exchanges.ts'
import type { ToolCall, TurnResult } from '../src/live/types.ts'
import { parseReply } from '../src/live/reply.ts'

const running: ToolCall = { id: 'mcp_1', server: 'fabric_iq_ontology', tool: 'ask_ontology', state: 'running' }
const done: ToolCall = { ...running, state: 'completed', ms: 2100 }
const answered = (responseId?: string): TurnResult => ({
  status: 'answered', reply: parseReply('Pump B [D1].'), raw: 'Pump B [D1].', elapsedMs: 3000, ...(responseId ? { responseId } : {}), toolCalls: [done],
})

function asked(): Exchange[] {
  const exchange: Exchange = {
    id: 'e1', question: 'Which assets are in maintenance?',
    threads: [
      { id: 'with', variant: 'with_iq', turns: [newTurn('t1', 'Which assets are in maintenance?', 0)] },
      { id: 'without', variant: 'without_iq', turns: [newTurn('t2', 'Which assets are in maintenance?', 0)] },
    ],
  }
  return reduce([], { type: 'ask', exchange })
}

test('"Ask both" keeps one thread per agent; each tool call updates in place and the result settles the turn', () => {
  let state = asked()
  state = reduce(state, { type: 'tool', turnId: 't1', call: running })
  state = reduce(state, { type: 'tool', turnId: 't1', call: done })
  assert.deepEqual(state[0].threads[0].turns[0].toolCalls, [done])
  state = reduce(state, { type: 'finish', turnId: 't1', result: answered('resp_1') })
  const [withIq, withoutIq] = state[0].threads
  assert.deepEqual([withIq.turns[0].running, withIq.turns[0].result?.status, withoutIq.turns[0].running], [false, 'answered', true])
  assert.equal(reduce(state, { type: 'tool', turnId: 't1', call: running })[0].threads[0].turns[0].toolCalls[0].state, 'completed', 'a finished turn takes no late events')
})

test('a follow-up continues the thread’s last answer, and only once that answer has a Foundry response ID', () => {
  let state = asked()
  assert.equal(followUpFrom(state[0].threads[0]), undefined, 'not while the turn runs')
  state = reduce(state, { type: 'finish', turnId: 't1', result: answered() })
  assert.equal(followUpFrom(state[0].threads[0]), undefined, 'not without a response ID')
  state = reduce(state, { type: 'finish', turnId: 't1', result: answered('resp_1') })
  assert.equal(followUpFrom(state[0].threads[0]), 'resp_1')
  state = reduce(state, { type: 'follow-up', threadId: 'with', turn: newTurn('t3', 'Since when?', 5, 'resp_1') })
  assert.deepEqual(state[0].threads[0].turns.map((turn) => [turn.id, turn.previousResponseId]), [['t1', undefined], ['t3', 'resp_1']])
  assert.equal(state[0].threads[1].turns.length, 1, 'the other agent’s thread is untouched')
  assert.equal(followUpFrom(state[0].threads[0]), undefined)
})

test('asking again restarts the same turn with its question and previous response', () => {
  let state = reduce(asked(), { type: 'follow-up', threadId: 'with', turn: newTurn('t3', 'Since when?', 5, 'resp_1') })
  state = reduce(state, { type: 'finish', turnId: 't3', result: { status: 'needs-consent', consentUrl: 'https://login.microsoftonline.com/x', server: 'work_iq_mail', elapsedMs: 900, toolCalls: [] } })
  state = reduce(state, { type: 'restart', turnId: 't3', startedAt: 9 })
  assert.deepEqual(state[0].threads[0].turns[1], { id: 't3', question: 'Since when?', startedAt: 9, running: true, toolCalls: [], previousResponseId: 'resp_1' })
  assert.equal(reduce(state, { type: 'ask', exchange: { id: 'e2', question: 'q', threads: [] } })[0].id, 'e2', 'the newest question comes first')
})

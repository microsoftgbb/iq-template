import assert from 'node:assert/strict'
import test from 'node:test'
import {
  SCRIPTED_LABEL, elapsed, expectations, isTextField, presenterCommand, presenterFromUrl, readStored, reducePresenter,
  saveStored, scriptedFallback, startPresenter, type KeyPress,
} from '../src/demo/presenter.ts'
import { SCRIPT, askOf } from '../src/demo/script.ts'
import { newTurn, type Turn } from '../src/exchanges.ts'
import type { TurnResult } from '../src/live/types.ts'

const count = SCRIPT.steps.length

test('the step reducer moves within the script, restarting the step timer only when the step changes', () => {
  let state = startPresenter(count, 1_000)
  assert.deepEqual([state.index, state.sessionStartedAt, state.stepStartedAt, state.notes, state.blank], [0, 1_000, 1_000, true, false])
  assert.equal(reducePresenter(state, { type: 'previous', at: 2_000 }), state, 'nothing before the first step')
  state = reducePresenter(state, { type: 'next', at: 2_000 })
  assert.deepEqual([state.index, state.stepStartedAt, state.sessionStartedAt], [1, 2_000, 1_000])
  state = reducePresenter(state, { type: 'go', index: count + 3, at: 3_000 })
  assert.deepEqual([state.index, state.stepStartedAt], [count - 1, 3_000])
  assert.equal(reducePresenter(state, { type: 'next', at: 4_000 }), state, 'nothing after the last step')
  state = reducePresenter(state, { type: 'previous', at: 5_000 })
  assert.equal(state.index, count - 2)
  state = reducePresenter(reducePresenter(state, { type: 'toggle-notes' }), { type: 'toggle-blank' })
  assert.deepEqual([state.notes, state.blank], [false, true])
  state = reducePresenter(state, { type: 'restart', at: 9_000 })
  assert.deepEqual([state.index, state.sessionStartedAt, state.stepStartedAt], [0, 9_000, 9_000])
})

test('a clicker\'s keys move between steps, Enter asks, N and B work the notes, and nothing fires in a text field', () => {
  const press = (key: string, extra: Partial<KeyPress> = {}) => presenterCommand({ key, target: { tagName: 'BODY' }, ...extra })
  assert.deepEqual(['ArrowRight', 'PageDown', 'ArrowLeft', 'PageUp', 'Enter', 'n', 'N', 'b', 'B'].map((key) => press(key)),
    ['next', 'next', 'previous', 'previous', 'ask', 'toggle-notes', 'toggle-notes', 'toggle-blank', 'toggle-blank'])
  assert.equal(press('x'), undefined)
  assert.equal(presenterCommand({ key: 'PageDown' }), 'next', 'without a focused element')
  for (const tagName of ['INPUT', 'textarea', 'SELECT']) {
    for (const key of ['ArrowRight', 'PageUp', 'Enter', 'n', 'b']) assert.equal(press(key, { target: { tagName } }), undefined, `${key} in ${tagName}`)
  }
  assert.equal(press('ArrowLeft', { target: { tagName: 'DIV', isContentEditable: true } }), undefined)
  assert.deepEqual([{ tagName: 'input' }, { tagName: 'DIV', isContentEditable: true }, { tagName: 'SUMMARY' }, { tagName: 'A' }, null]
    .map(isTextField), [true, true, false, false, false], 'a new step takes the focus from anywhere but a text field')
  for (const tagName of ['BUTTON', 'A', 'SUMMARY']) {
    assert.equal(press('Enter', { target: { tagName } }), undefined, `Enter already activates a focused ${tagName}`)
    assert.equal(press('PageDown', { target: { tagName } }), 'next')
  }
  for (const modifier of ['altKey', 'ctrlKey', 'metaKey', 'isComposing'] as const) {
    assert.equal(press('ArrowRight', { [modifier]: true }), undefined, modifier)
  }
})

function ended(result: TurnResult): Turn {
  return { ...newTurn('t1', 'q', 0), running: false, result }
}

test('a step\'s scripted answer is offered, labelled, only for its asked agents\' first turns that didn\'t answer', () => {
  assert.equal(SCRIPTED_LABEL, 'Scripted answer, not live')
  const timedOut = ended({ status: 'failed', message: 'The agent didn\'t finish.', code: 'timeout', elapsedMs: 225_000, toolCalls: [] })
  const scripted = askOf(SCRIPT.steps.find((step) => step.id === 'live-facts'), 'with_iq')?.scripted
  assert.ok(scripted)
  assert.equal(scriptedFallback(SCRIPT, 'live-facts', 'with_iq', timedOut, true), scripted)
  for (const result of [
    { status: 'failed', message: 'Stopped before the agent answered.', code: 'cancelled', elapsedMs: 9_000, toolCalls: [] },
    { status: 'needs-consent', consentUrl: 'https://login.microsoftonline.com/consent', server: 'fabric_iq_ontology', elapsedMs: 900, toolCalls: [] },
  ] satisfies TurnResult[]) {
    assert.equal(scriptedFallback(SCRIPT, 'live-facts', 'with_iq', ended(result), true), scripted, result.status)
  }
  const answered = ended({ status: 'answered', reply: { answer: '', sources: [], steps: [], markers: [], usedIqs: [] }, raw: 'x', elapsedMs: 1, toolCalls: [] })
  assert.equal(scriptedFallback(SCRIPT, 'live-facts', 'with_iq', answered, true), undefined, 'a live answer stands')
  assert.equal(scriptedFallback(SCRIPT, 'live-facts', 'with_iq', newTurn('t2', 'q', 0), true), undefined, 'not while it runs')
  assert.equal(scriptedFallback(SCRIPT, 'live-facts', 'with_iq', timedOut, false), undefined, 'not for a follow-up')
  assert.equal(scriptedFallback(SCRIPT, undefined, 'with_iq', timedOut, true), undefined, 'not for a question asked outside a step')
  assert.equal(scriptedFallback(SCRIPT, 'documents', 'without_iq', timedOut, true), undefined, 'not for an agent the step doesn\'t ask')
})

test('presenter mode is remembered per tab, and the URL turns it on or off', () => {
  assert.deepEqual(['?presenter=1', '?presenter=0', '?presenter=yes', ''].map(presenterFromUrl), [true, false, undefined, undefined])
  const state = { ...startPresenter(count, 1_000), index: 2, stepStartedAt: 5_000, notes: false, blank: true }
  const restored = readStored(saveStored(true, state), count, 6_000)
  assert.deepEqual(restored, { on: true, state: { ...state, blank: false } }, 'blanking never survives a reload')
  for (const stored of [null, 'not json', '[]', JSON.stringify({ on: true, index: count, sessionStartedAt: 1, stepStartedAt: 1, notes: true }),
    JSON.stringify({ on: true, index: 0, sessionStartedAt: 9_000, stepStartedAt: 1, notes: true })]) {
    assert.deepEqual(readStored(stored, count, 6_000), { on: false, state: startPresenter(count, 6_000) }, String(stored))
  }
})

test('timers read m:ss, then h:mm:ss, and each step\'s notes list what each agent should show', () => {
  assert.deepEqual([0, 61_999, 600_000, 3_723_000, -5].map(elapsed), ['0:00', '1:01', '10:00', '1:02:03', '0:00'])
  const liveFacts = SCRIPT.steps.find((step) => step.id === 'live-facts')
  assert.ok(liveFacts)
  assert.deepEqual(expectations(liveFacts), [
    'Without IQ: Says it can\'t confirm. Not: Names an asset.',
    'With IQ: Pump B is in maintenance; At North Works. Not: Says it can\'t confirm the status. Cites D.',
  ])
  assert.match(expectations(SCRIPT.steps.find((step) => step.id === 'public-context') ?? liveFacts)[0], /holds no site, asset or person's name\.$/)
  assert.deepEqual(expectations(SCRIPT.steps[0]), [], 'a talk-only step expects nothing')
})

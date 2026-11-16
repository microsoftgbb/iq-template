import assert from 'node:assert/strict'
import { readFileSync, readdirSync, rmSync } from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { build } from 'vite'
import { SCRIPTED_LABEL } from '../src/demo/presenter.ts'
import { SCRIPT } from '../src/demo/script.ts'
import type { ToolCall } from '../src/live/protocol.ts'
import { parseReply } from '../src/live/reply.ts'
import { CANCELLED, type TurnLog } from '../src/live/turn.ts'
import {
  MOCK_CONSENT_SERVER, MOCK_CONSENT_URL, MOCK_FALLBACK, MOCK_SESSION, createMockClient, mockReply,
} from '../src/mock/client.ts'
import { SAMPLES } from '../src/samples.ts'

const app = fileURLToPath(new URL('../', import.meta.url))

test('mock mode answers each script question with its scripted reply, for each agent its step asks, through the same turn', async () => {
  const records: TurnLog[] = []
  const client = createMockClient({ delayMs: 0, log: (record) => { records.push(record) } })
  assert.equal(client.mode, 'mock')
  const asked = SCRIPT.steps.filter((step) => step.question)
  assert.deepEqual(SAMPLES.map((sample) => sample.question), asked.map((step) => step.question), 'the samples are the script\'s questions')
  assert.deepEqual(SAMPLES.filter((sample) => sample.note).map((sample) => sample.note), ['needs Work IQ'])
  let consents = 0
  for (const step of asked) {
    const text = step.question ?? ''
    for (const { variant, scripted } of step.asks) {
      const tools: ToolCall[] = []
      let result = await client.ask({ variant, text }, undefined, (call) => tools.push(call))
      if (result.status === 'needs-consent') {
        consents += 1
        assert.deepEqual([result.server, result.consentUrl], [MOCK_CONSENT_SERVER, MOCK_CONSENT_URL])
        assert.ok(scripted.tools.some((tool) => tool.server === MOCK_CONSENT_SERVER), `${step.id}: only Work IQ mail asks for consent`)
        result = await client.ask({ variant, text }, undefined, (call) => tools.push(call))
      }
      assert.equal(result.status, 'answered', `${step.id} ${variant}`)
      if (result.status !== 'answered') continue
      assert.equal(result.raw, scripted.reply)
      assert.deepEqual(result.reply, parseReply(scripted.reply))
      assert.match(result.responseId ?? '', /^resp_mock_[0-9a-f]{32}$/)
      assert.deepEqual(result.toolCalls.map((call) => [call.server, call.tool, call.state, call.arguments]),
        scripted.tools.map((tool) => [tool.server, tool.tool, 'completed', tool.arguments]))
      assert.deepEqual(tools.map((call) => call.state), scripted.tools.flatMap(() => ['running', 'completed']), 'the timeline sees each call start and finish')
      if (variant === 'with_iq') assert.ok(result.reply.markers.length > 0 && result.reply.sources.length > 0, step.id)
      else assert.deepEqual([result.reply.markers, result.reply.sources, result.toolCalls], [[], [], []], step.id)
    }
  }
  assert.equal(consents, 1, 'the one Work IQ consent example, asked once')
  assert.ok(records.every((record) => record.event === 'app.turn'))
})

test('other questions, and a script question asked of an agent its step doesn\'t ask, get the fallback; a mock turn can be stopped', async () => {
  const client = createMockClient({ delayMs: 0 })
  const other = await client.ask({ variant: 'with_iq', text: 'How many sites are there?' })
  assert.equal(other.status === 'answered' && other.raw, MOCK_FALLBACK.reply)
  const documents = SCRIPT.steps.find((step) => step.id === 'documents')?.question ?? ''
  assert.equal(mockReply(`  ${documents.toUpperCase()} `, 'with_iq'), SCRIPT.steps.find((step) => step.id === 'documents')?.asks[0].scripted)
  assert.equal(mockReply(documents, 'without_iq'), MOCK_FALLBACK)
  const controller = new AbortController()
  const pending = createMockClient({ delayMs: 5_000 }).ask({ variant: 'with_iq', text: SAMPLES[0].question }, controller.signal)
  setTimeout(() => controller.abort(), 10)
  const stopped = await pending
  assert.equal(stopped.status === 'failed' && stopped.code, CANCELLED)
  assert.ok(stopped.elapsedMs < 5_000)
})

test('mock mode needs no sign-in and labels itself', () => {
  assert.equal(MOCK_SESSION.mode, 'mock')
  assert.equal(MOCK_SESSION.identity?.name, 'Mock user')
  assert.match(MOCK_SESSION.notice ?? '', /^Mock mode/)
})

test('only literal mode checks load the mock module, and nothing loads the sign-in statically', () => {
  const source = readFileSync(new URL('../src/live/client.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /^import\b[^\n]*\.\/auth/m, 'no static import of the browser sign-in')
  assert.doesNotMatch(source, /^import\b[^\n]*mock\//m, 'no static import of the mock module')
  const loads = source.match(/import\('\.\.\/mock\/client\.ts'\)/g) ?? []
  const guards = source.match(/if \(import\.meta\.env\.MODE === 'mock'\) \{/g) ?? []
  assert.equal(loads.length, 2)
  assert.equal(guards.length, loads.length)
  for (const block of source.split(/^(?:export )?(?:async )?function /m).filter((part) => part.includes("import('../mock/client.ts')"))) {
    assert.ok(block.indexOf("if (import.meta.env.MODE === 'mock') {") < block.indexOf("import('../mock/client.ts')"), block.slice(0, 60))
  }
  for (const file of readdirSync(new URL('../src/', import.meta.url), { recursive: true, encoding: 'utf8' })) {
    if (!/\.tsx?$/.test(file) || file.startsWith(`mock${path.sep}`) || file === path.join('live', 'client.ts')) continue
    const text = readFileSync(new URL(`../src/${file.split(path.sep).join('/')}`, import.meta.url), 'utf8')
    assert.doesNotMatch(text, /mock\/client/, `${file} must not import the mock module`)
  }
})

test('a production build contains no mock text', async () => {
  const outDir = path.join(app, 'node_modules', '.tmp', `production-check-${process.pid}`)
  try {
    await build({ configFile: path.join(app, 'vite.config.ts'), mode: 'production', logLevel: 'silent', build: { outDir, emptyOutDir: true } })
    const files = readdirSync(outDir, { recursive: true, encoding: 'utf8' }).filter((file) => /\.(?:html|js|css)$/.test(file))
    const bundle = files.map((file) => readFileSync(path.join(outDir, file), 'utf8')).join('\n')
    assert.ok(files.includes('index.html') && files.includes('redirect.html'), 'the app and its redirect bridge')
    assert.ok(bundle.includes('Sample questions'), 'the probe reads the app itself')
    assert.ok(bundle.includes(SCRIPTED_LABEL), 'presenter mode and its labelled scripted answers ship')
    const mockSource = readFileSync(new URL('../src/mock/client.ts', import.meta.url), 'utf8')
    // The script's replies ship on purpose, as presenter mode's labelled scripted answers; the mock's own text doesn't.
    const probes = [MOCK_SESSION.notice ?? '', MOCK_CONSENT_URL, 'resp_mock_', /\*\*(.+?)\*\*/.exec(MOCK_FALLBACK.reply)?.[1] ?? '']
    for (const probe of probes) {
      assert.ok(probe && mockSource.includes(probe), `${probe} is mock text`)
      assert.ok(!bundle.includes(probe), `the production bundle must not contain ${probe}`)
    }
  } finally {
    rmSync(outDir, { recursive: true, force: true })
  }
})

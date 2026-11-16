import assert from 'node:assert/strict'
import test from 'node:test'
import type { ToolCall } from '../src/live/protocol.ts'
import { UNAUTHORIZED, readTurnStream } from '../src/live/stream.ts'

function streamed(...chunks: string[]): Response {
  const encoder = new TextEncoder()
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  }), { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } })
}

const call: ToolCall = { id: 'mcp_1', server: 'foundry_iq_knowledge', tool: 'knowledge_base_retrieve', state: 'running' }

test('tool events reach the caller and the result ends the stream, however the lines are split', async () => {
  const seen: ToolCall[] = []
  const done = { ...call, state: 'completed' as const, ms: 3000 }
  const result = { status: 'answered', text: 'From the live ontology [F1].', toolCalls: [done] }
  const lines = [{ type: 'tool', call }, { type: 'heartbeat' }, { type: 'tool', call: done }, { type: 'result', outcome: result }].map((event) => JSON.stringify(event)).join('\n') + '\n'
  const outcome = await readTurnStream(streamed(lines.slice(0, 17), lines.slice(17, 90), lines.slice(90)), (tool) => seen.push(tool))
  assert.deepEqual(outcome, result)
  assert.deepEqual(seen, [call, done])
})

test('a stream that ends without a result is a failure, not an empty answer', async () => {
  const outcome = await readTurnStream(streamed(`${JSON.stringify({ type: 'tool', call })}\n`), () => {})
  assert.equal(outcome.status, 'failed')
  assert.match(outcome.status === 'failed' ? outcome.message : '', /closed before the answer/)
})

test('an event the protocol rejects cancels the stream, so the server stops the agent run', async () => {
  let cancelled = false
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      // A malformed line, and the stream stays open as a slow agent run would.
      controller.enqueue(new TextEncoder().encode('{"type":"tool","call":42}\n'))
    },
    cancel() {
      cancelled = true
    },
  })
  const response = new Response(body, { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } })
  await assert.rejects(readTurnStream(response, () => {}))
  assert.equal(cancelled, true)
})

test("a refused sign-in is marked so the page can offer to sign in again; other errors use the server's message", async () => {
  const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  assert.deepEqual(await readTurnStream(json(401, { message: 'Your sign-in expired. Reload the page to sign in again.' }), () => {}),
    { status: 'failed', message: 'Your sign-in expired. Reload the page to sign in again.', code: UNAUTHORIZED, toolCalls: [] })
  const bare = await readTurnStream(new Response('', { status: 401 }), () => {})
  assert.deepEqual([bare.status === 'failed' && bare.code, bare.status === 'failed' && /sign-in/.test(bare.message)], [UNAUTHORIZED, true])
  assert.deepEqual(await readTurnStream(json(400, { message: 'Send { cid, variant, question }.' }), () => {}),
    { status: 'failed', message: 'Send { cid, variant, question }.', code: 'HTTP 400', toolCalls: [] })
  const plain = await readTurnStream(new Response('<html>bad gateway</html>', { status: 502 }), () => {})
  assert.deepEqual(plain, { status: 'failed', message: 'The app server answered HTTP 502.', code: 'HTTP 502', toolCalls: [] })
})

test('an unrecognized line is an error the caller reports', async () => {
  await assert.rejects(readTurnStream(streamed('{"type":"surprise"}\n'), () => {}), /unrecognized/)
})

import assert from 'node:assert/strict'
import { createServer, request } from 'node:http'
import type { AddressInfo } from 'node:net'
import test from 'node:test'
import { createShutdown } from '../server/shutdown.ts'

test('shutdown closes a long-running request after the drain period, then flushes telemetry and exits once', async () => {
  // A turn that would outlive the platform's grace period: headers sent, body never finished.
  const server = createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson' })
    res.write('{"type":"heartbeat"}\n')
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address() as AddressInfo
  const open = new Promise<void>((resolve) => {
    const req = request({ host: '127.0.0.1', port, path: '/api/turns', method: 'POST' }, (res) => {
      res.once('data', () => resolve())
      res.on('error', () => undefined)
    })
    req.on('error', () => undefined)
    req.end()
  })
  await open

  const events: string[] = []
  let exited: (code: number) => void = () => {}
  const exit = new Promise<number>((resolve) => { exited = resolve })
  const stop = createShutdown(server, {
    drainMs: 50,
    flush: async () => { events.push('flush') },
    exit: (code) => { events.push(`exit ${code}`); exited(code) },
  })
  const started = Date.now()
  stop()
  stop()
  assert.equal(await exit, 0)
  assert.ok(Date.now() - started < 2_000, 'the open request must not hold the shutdown')
  assert.deepEqual(events, ['flush', 'exit 0'])
})

test('a failing telemetry flush still exits', async () => {
  const server = createServer(() => {})
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  let exited: (code: number) => void = () => {}
  const exit = new Promise<number>((resolve) => { exited = resolve })
  createShutdown(server, { drainMs: 50, flush: () => Promise.reject(new Error('unreachable')), exit: (code) => exited(code) })()
  assert.equal(await exit, 0)
})

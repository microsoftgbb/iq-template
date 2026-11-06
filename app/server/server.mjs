import { createServer } from 'node:http'
import { fileURLToPath } from 'node:url'
import { createHandler, settings } from './handlers.mjs'
import { shutdownTelemetry } from './telemetry.mjs'

const config = settings(process.env)
const root = fileURLToPath(new URL('../dist/', import.meta.url))
const server = createServer(createHandler({ settings: config, root, fetch: globalThis.fetch, log: console.log }))
server.requestTimeout = config.timeoutMs + 10_000
server.listen(config.port, () => console.log(`Scenario assistant app listening on port ${config.port}.`))

for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, () => server.close(() => void shutdownTelemetry().finally(() => process.exit(0))))
}

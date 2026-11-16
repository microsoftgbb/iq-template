// Production entry: serves the built app from dist next to this server's bundle (dist-server), on PORT, and traces
// it to Application Insights when the container app is configured for it. Without a valid configuration it exits.
import { createServer } from 'node:http'
import { fileURLToPath } from 'node:url'
import { createApp } from './app.ts'
import { createTokenVerifier } from './auth.ts'
import { readServerConfig } from './config.ts'
import { createStaticHandler } from './static.ts'
import { startTelemetry } from './telemetry.ts'

const { config, problems } = readServerConfig(process.env)
if (!config) {
  console.error(JSON.stringify({ event: 'app.server.config', problems }))
  process.exit(1)
}
// Before the app exists, so its first request is traced.
const telemetry = startTelemetry(config.telemetry)
const verify = createTokenVerifier({ tenantId: config.tenantId, clientId: config.clientId })
const root = process.env.STATIC_ROOT ?? fileURLToPath(new URL('../dist/', import.meta.url))
const server = createServer(createApp({ config, verify, fallback: createStaticHandler(root) }))
// Longer than Azure Container Apps' 240-second request limit, so the platform, not Node, ends a slow turn.
server.requestTimeout = 250_000
server.headersTimeout = 30_000
server.keepAliveTimeout = 65_000
server.listen(config.port, () => {
  console.info(JSON.stringify({ event: 'app.server.listening', port: config.port, tracing: Boolean(config.telemetry) }))
  void verify.warmUp()
})
// Exports the queued spans before exiting, as Container Apps stops a replica.
const stop = () => server.close(() => {
  void telemetry.shutdown().catch(() => undefined).finally(() => process.exit(0))
})
process.on('SIGTERM', stop)
process.on('SIGINT', stop)

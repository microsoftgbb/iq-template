// Development entry (npm run dev): the same server with Vite's dev middleware instead of the built files, on
// http://localhost:3000, against the deployment that .env.local names (see app/README.md).
import { createServer } from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createVite } from 'vite'
import { createApp } from './app.ts'
import { createTokenVerifier } from './auth.ts'
import { readServerConfig } from './config.ts'

const { config, problems } = readServerConfig({ PORT: '3000', ...process.env })
if (!config) {
  console.error('Fill app/.env.local from terraform -chdir=infra output -json app_settings first (app/README.md):\n- ' + problems.join('\n- '))
  process.exit(1)
}
const vite = await createVite({
  configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
  server: { middlewareMode: true },
  appType: 'spa',
})
const verify = createTokenVerifier({ tenantId: config.tenantId, clientId: config.clientId })
const server = createServer(createApp({
  config,
  verify,
  fallback: (req, res) => vite.middlewares(req, res),
  contentSecurityPolicy: false,
}))
server.listen(config.port, () => {
  console.info(`Scenario assistant (live) on http://localhost:${config.port}/`)
  void verify.warmUp()
})

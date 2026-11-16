// The server's routes: health, public sign-in settings, the question stream and, for everything else, the app
// itself (the built files, or Vite's dev middleware in development). Each request but the health probe is traced.
import type { IncomingMessage, ServerResponse } from 'node:http'
import { CONFIG_PATH, FOUNDRY_SCOPE, REDIRECT_PATH, TURN_PATH, type PublicConfig } from '../src/live/protocol.ts'
import type { Verifier } from './auth.ts'
import type { ServerConfig } from './config.ts'
import { applySecurityHeaders, sendJson } from './http.ts'
import { routeOf, traceRequest } from './telemetry.ts'
import { createTurnHandler, type TurnDependencies } from './turn.ts'

export type Handler = (req: IncomingMessage, res: ServerResponse) => void | Promise<void>

export interface AppDependencies extends Omit<TurnDependencies, 'config' | 'verify'> {
  config: ServerConfig
  verify: Verifier
  // Serves the page and its assets.
  fallback: Handler
  // false only for Vite's development server.
  contentSecurityPolicy?: boolean
}

export function createApp(dependencies: AppDependencies): Handler {
  const { config, fallback } = dependencies
  const turn = createTurnHandler(dependencies)
  const publicConfig: PublicConfig = { tenantId: config.tenantId, clientId: config.clientId, scopes: [FOUNDRY_SCOPE] }
  const handle = async (req: IncomingMessage, res: ServerResponse, pathname: string): Promise<void> => {
    try {
      if (pathname === CONFIG_PATH) {
        if (req.method !== 'GET' && req.method !== 'HEAD') return sendJson(res, 405, { message: 'Use GET.' }, { Allow: 'GET, HEAD' })
        return sendJson(res, 200, publicConfig)
      }
      if (pathname === TURN_PATH) {
        if (req.method !== 'POST') return sendJson(res, 405, { message: 'Use POST.' }, { Allow: 'POST' })
        return await turn(req, res)
      }
      if (pathname.startsWith('/api/')) return sendJson(res, 404, { message: 'Not found.' })
      return await fallback(req, res)
    } catch {
      if (!res.headersSent) sendJson(res, 500, { message: 'The app server failed. Ask again.' })
      else res.destroy()
    }
  }
  return async (req, res) => {
    const pathname = (req.url ?? '/').split('?')[0]
    applySecurityHeaders(res, dependencies.contentSecurityPolicy !== false, pathname === REDIRECT_PATH)
    // Container Apps probes it every few seconds: tracing it would only add noise and cost.
    if (pathname === '/healthz') {
      res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' }).end('ok')
      return
    }
    const method = req.method ?? 'GET'
    await traceRequest(method, routeOf(pathname, [CONFIG_PATH, TURN_PATH]), () => res.statusCode, () => handle(req, res, pathname))
  }
}

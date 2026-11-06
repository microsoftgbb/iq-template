// The Container App's server: static SPA, runtime configuration and a transport-only proxy that
// forwards the signed-in user's own Foundry token to the one configured hosted-agent endpoint.
import { readFile } from 'node:fs/promises'
import path from 'node:path'

export const MAX_BODY_BYTES = 16 * 1024
export const MAX_INPUT_CHARS = 2000
const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const RESPONSE_ID = /^[A-Za-z0-9_-]{1,256}$/
const BEARER = /^Bearer ([A-Za-z0-9._~+/-]+=*)$/
const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.json': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.woff2': 'font/woff2',
}
const LOGIN = 'https://login.microsoftonline.com'
export const SECURITY_HEADERS = {
  'Content-Security-Policy': [
    "default-src 'self'", "script-src 'self'", "style-src 'self'", "img-src 'self' data:",
    `connect-src 'self' ${LOGIN}`, `frame-src ${LOGIN}`, "frame-ancestors 'none'", "base-uri 'self'",
    "object-src 'none'", `form-action 'self' ${LOGIN}`,
  ].join('; '),
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Strict-Transport-Security': 'max-age=31536000',
}

export class ClientError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

function required(env, name) {
  const value = (env[name] || '').trim()
  if (!value) throw new Error(`Set ${name} for the scenario assistant app.`)
  return value
}

export function settings(env) {
  const endpoint = new URL(required(env, 'AGENT_RESPONSES_URL'))
  if (endpoint.protocol !== 'https:' || endpoint.username || endpoint.password || endpoint.hash) {
    throw new Error('AGENT_RESPONSES_URL must be an https URL without credentials or fragment.')
  }
  const clientId = required(env, 'ENTRA_CLIENT_ID')
  const tenantId = required(env, 'ENTRA_TENANT_ID')
  if (!GUID.test(clientId) || !GUID.test(tenantId)) throw new Error('ENTRA_CLIENT_ID and ENTRA_TENANT_ID must be GUIDs.')
  const timeout = Number(env.AGENT_TIMEOUT_SECONDS || 220)
  if (!Number.isFinite(timeout) || timeout < 1) throw new Error('AGENT_TIMEOUT_SECONDS must be a positive number.')
  return {
    port: Number(env.PORT || 8080),
    responsesUrl: endpoint.toString(),
    timeoutMs: timeout * 1000,
    config: {
      clientId,
      tenantId,
      scope: (env.AGENT_SCOPE || 'https://ai.azure.com/user_impersonation').trim(),
      agentName: required(env, 'AGENT_NAME'),
    },
  }
}

export function upstreamBody(value) {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new ClientError(400, 'Send a JSON object with an input question.')
  }
  const unknown = Object.keys(value).filter((key) => key !== 'input' && key !== 'previous_response_id')
  if (unknown.length) throw new ClientError(400, `Unsupported fields: ${unknown.join(', ')}.`)
  const input = typeof value.input === 'string' ? value.input.trim() : ''
  if (!input || input.length > MAX_INPUT_CHARS) {
    throw new ClientError(400, `The question must be 1-${MAX_INPUT_CHARS} characters.`)
  }
  const body = { input, stream: false }
  if (value.previous_response_id !== undefined) {
    if (typeof value.previous_response_id !== 'string' || !RESPONSE_ID.test(value.previous_response_id)) {
      throw new ClientError(400, 'previous_response_id is malformed.')
    }
    body.previous_response_id = value.previous_response_id
  }
  return body
}

async function readJson(request) {
  if (!/^application\/json\b/i.test(request.headers['content-type'] || '')) {
    throw new ClientError(415, 'Send application/json.')
  }
  const chunks = []
  let size = 0
  for await (const chunk of request) {
    size += chunk.length
    if (size > MAX_BODY_BYTES) throw new ClientError(413, 'The request body is too large.')
    chunks.push(chunk)
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'))
  } catch {
    throw new ClientError(400, 'The request body is not valid JSON.')
  }
}

function send(response, status, body, headers = {}) {
  const payload = typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body)
  response.writeHead(status, {
    ...SECURITY_HEADERS,
    'Content-Type': typeof body === 'string' || Buffer.isBuffer(body) ? 'text/plain; charset=utf-8' : 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    ...headers,
  })
  response.end(payload)
}

async function proxy(request, response, options) {
  const match = BEARER.exec(request.headers.authorization || '')
  if (!match) throw new ClientError(401, 'Sign in first; the agent is called with your own token.')
  const body = upstreamBody(await readJson(request))
  let upstream
  let text
  try {
    upstream = await options.fetch(options.settings.responsesUrl, {
      method: 'POST',
      headers: { Authorization: `Bearer ${match[1]}`, 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
      redirect: 'error',
      signal: AbortSignal.timeout(options.settings.timeoutMs),
    })
    // The timeout also covers the body, so a stalled or dropped answer is a gateway error, not a server failure.
    text = await upstream.text()
  } catch (error) {
    const timedOut = error?.name === 'TimeoutError'
    const reason = timedOut ? 'The agent did not answer in time.'
      : upstream ? 'The agent connection failed while answering.' : 'The agent endpoint is unreachable.'
    throw new ClientError(timedOut ? 504 : 502, reason)
  }
  let parsed
  try {
    parsed = text ? JSON.parse(text) : {}
  } catch {
    parsed = { error: { message: `The agent endpoint returned HTTP ${upstream.status} without JSON.` } }
  }
  send(response, upstream.ok ? 200 : upstream.status, parsed)
}

async function serveStatic(request, response, root) {
  let pathname
  try {
    pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname)
  } catch {
    throw new ClientError(404, 'Not found.')
  }
  const relative = pathname === '/' || !path.extname(pathname) ? 'index.html' : pathname.replace(/^\/+/, '')
  const file = path.resolve(root, relative)
  if (!file.startsWith(path.resolve(root) + path.sep)) throw new ClientError(404, 'Not found.')
  let content
  try {
    content = await readFile(file)
  } catch {
    throw new ClientError(404, 'Not found.')
  }
  const immutable = relative.startsWith('assets/')
  send(response, 200, content, {
    'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream',
    'Cache-Control': immutable ? 'public, max-age=31536000, immutable' : 'no-cache',
  })
}

function routeOf(target) {
  try {
    return new URL(target, 'http://localhost').pathname
  } catch {
    return null
  }
}

export function createHandler(options) {
  return async (request, response) => {
    const started = Date.now()
    // A malformed target such as // must become a 400, never an unhandled rejection that stops the server.
    const route = routeOf(request.url)
    try {
      if (route === null) {
        throw new ClientError(400, 'The request target is not valid.')
      } else if (route === '/healthz' && request.method === 'GET') {
        send(response, 200, 'ok')
      } else if (route === '/config.json' && request.method === 'GET') {
        send(response, 200, options.settings.config)
      } else if (route === '/api/responses') {
        if (request.method !== 'POST') throw new ClientError(405, 'Use POST.')
        await proxy(request, response, options)
      } else if (request.method === 'GET' || request.method === 'HEAD') {
        await serveStatic(request, response, options.root)
      } else {
        throw new ClientError(405, 'Method not allowed.')
      }
    } catch (error) {
      const status = error instanceof ClientError ? error.status : 500
      const message = error instanceof ClientError ? error.message : 'The app server failed.'
      if (!(error instanceof ClientError)) console.error(`Unexpected ${error?.name || 'error'} on ${route}.`)
      if (!response.headersSent) send(response, status, { error: { message } })
      else response.end()
    }
    // Never log headers or bodies: they carry the user's token and question.
    options.log?.(JSON.stringify({ method: request.method, route, status: response.statusCode, ms: Date.now() - started }))
  }
}

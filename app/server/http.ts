// Small HTTP helpers shared by the server's routes: security headers on every response, JSON replies and bounded
// JSON request bodies.
import type { IncomingMessage, ServerResponse } from 'node:http'

// Microsoft Entra sign-in is the only other origin the page talks to: MSAL redirects there and refreshes tokens with
// fetch. Once a refresh token expires, a day after sign-in, MSAL renews in a hidden iframe that starts at Entra and
// ends on the app's redirect bridge (REDIRECT_PATH), so the page frames both, and only the bridge may be framed.
const ENTRA = 'https://login.microsoftonline.com'

function contentSecurityPolicy(frameAncestors: string): string {
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    `connect-src 'self' ${ENTRA}`,
    `frame-src 'self' ${ENTRA}`,
    `frame-ancestors ${frameAncestors}`,
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
  ].join('; ')
}

export const CONTENT_SECURITY_POLICY = contentSecurityPolicy("'none'")
export const REDIRECT_CONTENT_SECURITY_POLICY = contentSecurityPolicy("'self'")

const SHARED_HEADERS: Readonly<Record<string, string>> = {
  'Strict-Transport-Security': 'max-age=31536000',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
}

export const SECURITY_HEADERS: Readonly<Record<string, string>> = {
  'Content-Security-Policy': CONTENT_SECURITY_POLICY,
  ...SHARED_HEADERS,
  'Cross-Origin-Opener-Policy': 'same-origin-allow-popups',
}

// MSAL requires its redirect bridge without an opener policy, which would cut the bridge off from the app's page.
export const REDIRECT_SECURITY_HEADERS: Readonly<Record<string, string>> = {
  'Content-Security-Policy': REDIRECT_CONTENT_SECURITY_POLICY,
  ...SHARED_HEADERS,
}

// Vite's development client needs inline scripts and its own WebSocket, so development drops only the CSP.
export function applySecurityHeaders(res: ServerResponse, contentSecurityPolicy = true, redirectBridge = false): void {
  for (const [name, value] of Object.entries(redirectBridge ? REDIRECT_SECURITY_HEADERS : SECURITY_HEADERS)) {
    if (contentSecurityPolicy || name !== 'Content-Security-Policy') res.setHeader(name, value)
  }
}

export function sendJson(res: ServerResponse, status: number, body: unknown, headers: Record<string, string> = {}): void {
  const payload = JSON.stringify(body)
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(payload),
    'Cache-Control': 'no-store',
    ...headers,
  })
  res.end(payload)
}

export class RequestError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function readJson(req: IncomingMessage, limit: number): Promise<unknown> {
  if (!/^application\/json\b/i.test(req.headers['content-type'] ?? '')) {
    throw new RequestError(415, 'Send the question as JSON.')
  }
  const declared = Number(req.headers['content-length'] ?? '0')
  if (declared > limit) throw new RequestError(413, 'The request is too large.')
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of req) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk as string)
    size += buffer.length
    if (size > limit) throw new RequestError(413, 'The request is too large.')
    chunks.push(buffer)
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8')) as unknown
  } catch {
    throw new RequestError(400, 'The request is not valid JSON.')
  }
}

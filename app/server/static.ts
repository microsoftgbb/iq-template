// The built React app (dist): hashed assets cached for a year, index.html revalidated, the redirect bridge never
// stored, and the brotli or gzip copies the build writes next to each file served to browsers that accept them. Only
// files under the root.
import { createReadStream } from 'node:fs'
import { stat } from 'node:fs/promises'
import type { IncomingMessage, ServerResponse } from 'node:http'
import path from 'node:path'
import { pipeline } from 'node:stream/promises'

const TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.webp': 'image/webp',
  '.woff2': 'font/woff2',
  '.txt': 'text/plain; charset=utf-8',
}
const COMPRESSIBLE = new Set(['.html', '.js', '.mjs', '.css', '.json', '.map', '.svg', '.txt'])

interface Found {
  file: string
  size: number
  mtimeMs: number
}

async function file(candidate: string): Promise<Found | undefined> {
  try {
    const info = await stat(candidate)
    return info.isFile() ? { file: candidate, size: info.size, mtimeMs: info.mtimeMs } : undefined
  } catch {
    return undefined
  }
}

function accepts(req: IncomingMessage, encoding: string): boolean {
  const header = String(req.headers['accept-encoding'] ?? '')
  return header.split(',').some((part) => {
    const [name, ...parameters] = part.trim().split(';')
    const quality = parameters.find((parameter) => parameter.trim().startsWith('q='))
    return name.trim() === encoding && (!quality || Number(quality.trim().slice(2)) > 0)
  })
}

export function createStaticHandler(directory: string) {
  const root = path.resolve(directory)
  const index = path.join(root, 'index.html')
  return async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.writeHead(405, { Allow: 'GET, HEAD' }).end()
      return
    }
    let pathname: string
    try {
      pathname = decodeURIComponent(new URL(req.url ?? '/', 'http://localhost').pathname)
    } catch {
      res.writeHead(400).end()
      return
    }
    const requested = path.resolve(root, `.${path.posix.normalize(pathname)}`)
    if (pathname.includes('\0') || (requested !== root && !requested.startsWith(root + path.sep))) {
      res.writeHead(404).end()
      return
    }
    // Paths without an extension are the single page; a missing asset is a 404, never the page.
    let found = pathname.endsWith('/') ? await file(path.join(requested, 'index.html')) : await file(requested)
    if (!found && !path.extname(pathname)) found = await file(index)
    if (!found) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' }).end('Not found')
      return
    }
    const extension = path.extname(found.file)
    const headers: Record<string, string | number> = {
      'Content-Type': TYPES[extension] ?? 'application/octet-stream',
      // The only other page, MSAL's redirect bridge, carries a sign-in response in its URL.
      'Cache-Control': found.file === index ? 'no-cache' : extension === '.html' ? 'no-store'
        : pathname.startsWith('/assets/') ? 'public, max-age=31536000, immutable' : 'public, max-age=3600',
    }
    let served = found
    if (COMPRESSIBLE.has(extension)) {
      headers.Vary = 'Accept-Encoding'
      for (const [encoding, suffix] of [['br', '.br'], ['gzip', '.gz']] as const) {
        const compressed = accepts(req, encoding) ? await file(found.file + suffix) : undefined
        if (compressed) {
          served = compressed
          headers['Content-Encoding'] = encoding
          break
        }
      }
    }
    const etag = `W/"${served.size.toString(16)}-${Math.floor(served.mtimeMs).toString(16)}"`
    headers.ETag = etag
    if (req.headers['if-none-match'] === etag) {
      res.writeHead(304, headers).end()
      return
    }
    headers['Content-Length'] = served.size
    res.writeHead(200, headers)
    if (req.method === 'HEAD') {
      res.end()
      return
    }
    await pipeline(createReadStream(served.file), res).catch(() => res.destroy())
  }
}

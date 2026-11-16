// Stops the server as Azure Container Apps stops a replica: no new connections, a bounded wait for open requests (a
// turn can take 225 seconds, the platform allows 30), then the telemetry flush, then exit.
import type { Server } from 'node:http'

export interface ShutdownOptions {
  // How long open requests may finish before their connections are closed.
  drainMs: number
  // Exports queued telemetry; failures are ignored so the process still exits.
  flush: () => Promise<void>
  exit: (code: number) => void
}

export function createShutdown(server: Server, options: ShutdownOptions): () => void {
  let stopping = false
  return () => {
    if (stopping) return
    stopping = true
    const drain = setTimeout(() => server.closeAllConnections(), options.drainMs)
    server.close(() => {
      clearTimeout(drain)
      void options.flush().catch(() => undefined).finally(() => options.exit(0))
    })
    server.closeIdleConnections()
  }
}

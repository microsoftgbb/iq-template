// Runtime settings served by the Container App at /config.json, never compiled into the bundle.
export interface AppConfig {
  clientId: string
  tenantId: string
  scope: string
  agentName: string
}

const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function text(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`The app configuration has no ${label}.`)
  return value.trim()
}

export function parseConfig(value: unknown): AppConfig {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('The app configuration is not an object.')
  }
  const record = value as Record<string, unknown>
  const config = {
    clientId: text(record.clientId, 'client ID'),
    tenantId: text(record.tenantId, 'tenant ID'),
    scope: text(record.scope, 'agent scope'),
    agentName: text(record.agentName, 'agent name'),
  }
  if (!GUID.test(config.clientId) || !GUID.test(config.tenantId)) {
    throw new Error('The app configuration needs GUID client and tenant IDs.')
  }
  if (!/^https:\/\/[^\s/]+\/[\w.]+$/.test(config.scope)) {
    throw new Error('The app configuration needs a delegated https://<resource>/<scope> scope.')
  }
  return config
}

export async function loadConfig(): Promise<AppConfig> {
  const response = await fetch('/config.json', { cache: 'no-store' })
  if (!response.ok) throw new Error(`The app configuration is unavailable (HTTP ${response.status}).`)
  return parseConfig(await response.json())
}

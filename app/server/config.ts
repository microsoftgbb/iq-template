// The server's settings, from its environment (Terraform sets them on the container app; app_settings prints them
// for a local run). Strict, because the server forwards each user's Foundry token: it only ever calls the one
// Foundry project endpoint configured here, and only the two agents named here.
import type { Variant } from '../src/live/types.ts'

// Tracing to Application Insights, when the container app is configured for it (infra/monitoring.tf).
export interface TelemetryConfig {
  connectionString: string
  // The app's user-assigned managed identity: Application Insights accepts no ingestion key.
  clientId: string
  // The image tag, reported as the application version.
  version?: string
}

export interface ServerConfig {
  port: number
  projectEndpoint: string
  tenantId: string
  clientId: string
  agents: Record<Variant, string>
  // A turn's budget, inside Azure Container Apps' 240-second request limit.
  turnTimeoutMs: number
  telemetry?: TelemetryConfig
}

export type Environment = Record<string, string | undefined>

const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
// Foundry agent names: alphanumerics and inner hyphens, at most 63 characters.
const AGENT_NAME = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/
const PROJECT_ENDPOINT = /^https:\/\/[a-z0-9][a-z0-9-]{0,62}\.services\.ai\.azure\.com\/api\/projects\/[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/
const AGENT_VARIABLES: Record<Variant, string> = {
  with_iq: 'AGENT_WITH_IQ',
  without_iq: 'AGENT_WITHOUT_IQ',
}
export const DEFAULT_TURN_TIMEOUT_MS = 225_000
const VERSION = /^[A-Za-z0-9._-]{1,64}$/

// The connection string's settings by lowercase key, such as instrumentationkey and ingestionendpoint.
function connectionSettings(value: string): Map<string, string> {
  return new Map(value.split(';').filter(Boolean).map((part) => {
    const index = part.indexOf('=')
    return [part.slice(0, index).trim().toLowerCase(), part.slice(index + 1).trim()] as [string, string]
  }))
}

// The server sends its managed identity's token to the ingestion endpoint, so it must be Application Insights'.
function applicationInsightsEndpoint(value: string | undefined): boolean {
  try {
    const url = new URL(value ?? '')
    return url.protocol === 'https:' && url.hostname.endsWith('.applicationinsights.azure.com') && !url.username && !url.search
  } catch {
    return false
  }
}

function readTelemetry(env: Environment, problems: string[]): TelemetryConfig | undefined {
  const connectionString = (env.APPLICATIONINSIGHTS_CONNECTION_STRING ?? '').trim()
  if (!connectionString) return undefined
  const settings = connectionSettings(connectionString)
  if (!GUID.test(settings.get('instrumentationkey') ?? '') || !applicationInsightsEndpoint(settings.get('ingestionendpoint'))) {
    problems.push('APPLICATIONINSIGHTS_CONNECTION_STRING must be an Application Insights connection string with an https ingestion endpoint on applicationinsights.azure.com.')
  }
  const clientId = (env.AZURE_CLIENT_ID ?? '').toLowerCase()
  if (!GUID.test(clientId)) problems.push("AZURE_CLIENT_ID must be the client ID of the app's managed identity, which sends its traces.")
  const version = env.APP_VERSION?.trim()
  if (version && !VERSION.test(version)) problems.push('APP_VERSION must be at most 64 letters, digits, dots, hyphens or underscores.')
  return { connectionString, clientId, ...(version ? { version } : {}) }
}

export function readServerConfig(env: Environment): { config?: ServerConfig; problems: string[] } {
  const problems: string[] = []
  const port = Number(env.PORT ?? '8080')
  if (!Number.isInteger(port) || port < 1 || port > 65535) problems.push('PORT must be a TCP port number.')
  const projectEndpoint = (env.FOUNDRY_PROJECT_ENDPOINT ?? '').replace(/\/+$/, '')
  if (!PROJECT_ENDPOINT.test(projectEndpoint)) {
    problems.push('FOUNDRY_PROJECT_ENDPOINT must be the project endpoint https://<account>.services.ai.azure.com/api/projects/<project>.')
  }
  const tenantId = (env.ENTRA_TENANT_ID ?? '').toLowerCase()
  if (!GUID.test(tenantId)) problems.push('ENTRA_TENANT_ID must be the Entra tenant GUID.')
  const clientId = (env.ENTRA_CLIENT_ID ?? '').toLowerCase()
  if (!GUID.test(clientId)) problems.push("ENTRA_CLIENT_ID must be the app registration's client ID.")
  const agents = {} as Record<Variant, string>
  for (const [variant, name] of Object.entries(AGENT_VARIABLES) as [Variant, string][]) {
    const value = env[name] ?? ''
    if (!AGENT_NAME.test(value)) problems.push(`${name} must be the ${variant} Foundry agent's name.`)
    agents[variant] = value
  }
  if (agents.with_iq && agents.with_iq === agents.without_iq) problems.push('The two agent names must differ.')
  const turnTimeoutMs = Number(env.TURN_TIMEOUT_MS ?? DEFAULT_TURN_TIMEOUT_MS)
  if (!Number.isInteger(turnTimeoutMs) || turnTimeoutMs < 10_000 || turnTimeoutMs > 235_000) {
    problems.push('TURN_TIMEOUT_MS must be a whole number of milliseconds from 10000 to 235000.')
  }
  const telemetry = readTelemetry(env, problems)
  if (problems.length) return { problems }
  return {
    config: { port, projectEndpoint, tenantId, clientId, agents, turnTimeoutMs, ...(telemetry ? { telemetry } : {}) },
    problems,
  }
}

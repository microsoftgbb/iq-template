// Tracing to Application Insights (infra/monitoring.tf). The server records a span per request, except the health
// probe, and for each question an invoke_agent span around the Foundry call, with each finished tool call as an
// event. askAgent sends that span's W3C trace context to Foundry, whose server-side trace of the run continues it, so
// one trace holds both. Like the server's log line, spans are value-free: no question, answer, tool argument, token
// or user identifier; Foundry's own spans hold the content. Without a TelemetryConfig, as in tests and local runs,
// nothing is exported and the OpenTelemetry API stays a no-op.
import { ManagedIdentityCredential } from '@azure/identity'
import { AzureMonitorTraceExporter } from '@azure/monitor-opentelemetry-exporter'
import { DiagConsoleLogger, DiagLogLevel, SpanKind, SpanStatusCode, diag, trace, type Attributes } from '@opentelemetry/api'
import { resourceFromAttributes } from '@opentelemetry/resources'
import { BatchSpanProcessor, NodeTracerProvider } from '@opentelemetry/sdk-trace-node'
import { iqOf, type ToolCall, type TurnOutcome } from '../src/live/protocol.ts'
import type { TelemetryConfig } from './config.ts'

export const SERVICE_NAME = 'scenario-assistant'

export interface Telemetry {
  // Exports the spans still queued, for at most SHUTDOWN_MS; call it before the process exits.
  shutdown: () => Promise<void>
}

// Well inside Container Apps' 30-second termination grace period; the exporter alone would wait 30 seconds.
const SHUTDOWN_MS = 5_000

export function startTelemetry(config: TelemetryConfig | undefined, env: Record<string, string | undefined> = process.env): Telemetry {
  if (!config) return { shutdown: async () => {} }
  // Export failures, such as a missing role, reach the container's log.
  diag.setLogger(new DiagConsoleLogger(), DiagLogLevel.WARN)
  const exporter = new AzureMonitorTraceExporter({
    connectionString: config.connectionString,
    credential: new ManagedIdentityCredential({ clientId: config.clientId }),
  })
  const provider = new NodeTracerProvider({
    resource: resourceFromAttributes({
      'service.name': SERVICE_NAME,
      ...(config.version ? { 'service.version': config.version } : {}),
      // Container Apps names each replica; Application Insights shows it as the role instance.
      ...(env.CONTAINER_APP_REPLICA_NAME ? { 'service.instance.id': env.CONTAINER_APP_REPLICA_NAME } : {}),
    }),
    spanProcessors: [new BatchSpanProcessor(exporter)],
  })
  // The global provider, an async-context manager and the W3C trace-context propagator.
  provider.register()
  return {
    shutdown: () => Promise.race([
      provider.shutdown(),
      new Promise<void>((resolve) => setTimeout(resolve, SHUTDOWN_MS).unref()),
    ]),
  }
}

const tracer = () => trace.getTracer(SERVICE_NAME)

// A request's route, which names its span: the API paths, the hashed assets and every other page path.
export function routeOf(pathname: string, apiPaths: readonly string[]): string {
  if (apiPaths.includes(pathname)) return pathname
  if (pathname.startsWith('/api/')) return '/api/*'
  if (pathname.startsWith('/assets/')) return '/assets/*'
  return '/*'
}

// Runs `handle` inside the request's server span; 5xx responses mark it failed.
export function traceRequest(method: string, route: string, statusCode: () => number, handle: () => Promise<void>): Promise<void> {
  return tracer().startActiveSpan(`${method} ${route}`, {
    kind: SpanKind.SERVER,
    attributes: { 'http.request.method': method, 'http.route': route },
  }, async (span) => {
    try {
      await handle()
    } finally {
      const status = statusCode()
      span.setAttribute('http.response.status_code', status)
      if (status >= 500) span.setStatus({ code: SpanStatusCode.ERROR })
      span.end()
    }
  })
}

export interface AgentRunLabels {
  agent: string
  endpoint: string
  variant: string
  // The request's cid, a random UUID per question: it correlates the trace, the log line and Foundry's run.
  cid: string
  followUp: boolean
}

function toolAttributes(call: ToolCall): Attributes {
  return {
    'gen_ai.tool.name': call.tool,
    'gen_ai.tool.call.id': call.id,
    'app.tool.server': call.server,
    'app.tool.iq': iqOf(call.server) ?? 'other',
    'app.tool.state': call.state,
    ...(call.ms === undefined ? {} : { 'app.tool.duration_ms': call.ms }),
  }
}

// Runs one question inside an invoke_agent client span (OpenTelemetry's generative AI conventions): Foundry receives
// its trace context, each finished tool call becomes an event, and the outcome sets its attributes and status.
export function traceAgentRun(labels: AgentRunLabels, run: (onTool: (call: ToolCall) => void) => Promise<TurnOutcome>): Promise<TurnOutcome> {
  return tracer().startActiveSpan(`invoke_agent ${labels.agent}`, {
    kind: SpanKind.CLIENT,
    attributes: {
      'gen_ai.operation.name': 'invoke_agent',
      'gen_ai.agent.name': labels.agent,
      'server.address': new URL(labels.endpoint).hostname,
      'app.variant': labels.variant,
      'app.cid': labels.cid,
      'app.follow_up': labels.followUp,
    },
  }, async (span) => {
    try {
      const outcome = await run((call) => {
        if (call.state !== 'running') span.addEvent('app.tool_call', toolAttributes(call))
      })
      span.setAttributes({
        'app.outcome': outcome.status,
        'app.tool_calls': outcome.toolCalls.length,
        'app.tool_failures': outcome.toolCalls.filter((call) => call.state === 'failed').length,
        ...(outcome.responseId ? { 'gen_ai.response.id': outcome.responseId } : {}),
        ...(outcome.status === 'failed' && outcome.code ? { 'app.outcome.code': outcome.code } : {}),
      })
      if (outcome.status === 'failed' && outcome.code !== 'cancelled') {
        span.setStatus({ code: SpanStatusCode.ERROR, message: outcome.code ?? 'failed' })
      }
      return outcome
    } catch (error) {
      span.setStatus({ code: SpanStatusCode.ERROR, message: 'exception' })
      throw error
    } finally {
      span.end()
    }
  })
}

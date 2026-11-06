// Application Insights tracing for the app server, preloaded with `node --import ./server/telemetry.mjs` so the
// ESM instrumentation hooks register before the server imports node:http. Without a connection string (tests and
// local runs) nothing is loaded and nothing is exported.
const connectionString = process.env.APPLICATIONINSIGHTS_CONNECTION_STRING?.trim()
let shutdown = async () => {}

if (connectionString) {
  await import('@microsoft/opentelemetry/loader')
  const { useMicrosoftOpenTelemetry, shutdownMicrosoftOpenTelemetry } = await import('@microsoft/opentelemetry')
  const { registerInstrumentations } = await import('@opentelemetry/instrumentation')
  const { UndiciInstrumentation } = await import('@opentelemetry/instrumentation-undici')
  const agentOrigin = URL.canParse(process.env.AGENT_RESPONSES_URL ?? '')
    ? new URL(process.env.AGENT_RESPONSES_URL).origin
    : null

  useMicrosoftOpenTelemetry({
    azureMonitor: { azureMonitorExporterOptions: { connectionString } },
    instrumentationOptions: {
      // Each browser request becomes an App Insights request. Health probes would only add noise. HTTP spans
      // carry the method, path and status, never headers or bodies, so the user's token and question stay out.
      http: { enabled: true, ignoreIncomingRequestHook: (request) => request.url === '/healthz' },
      azureSdk: { enabled: false },
      mongoDb: { enabled: false },
      mySql: { enabled: false },
      postgreSql: { enabled: false },
      redis: { enabled: false },
      redis4: { enabled: false },
      openaiAgents: { enabled: false },
      langchain: { enabled: false },
    },
  })
  // The proxy calls the agent with fetch. Record those calls as dependencies and send W3C trace context, so the
  // hosted agent's spans join the same operation. Calls to any other origin, such as telemetry export, are skipped.
  registerInstrumentations({
    instrumentations: [new UndiciInstrumentation({ ignoreRequestHook: (request) => request.origin !== agentOrigin })],
  })
  shutdown = shutdownMicrosoftOpenTelemetry
}

// Flush buffered telemetry before the process exits; never block shutdown for long.
export async function shutdownTelemetry(timeoutMs = 5000) {
  let timer
  await Promise.race([
    shutdown().catch(() => {}),
    new Promise((resolve) => { timer = setTimeout(resolve, timeoutMs) }),
  ])
  clearTimeout(timer)
}

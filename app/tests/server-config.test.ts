import assert from 'node:assert/strict'
import test from 'node:test'
import { DEFAULT_TURN_TIMEOUT_MS, readServerConfig } from '../server/config.ts'

const env = {
  FOUNDRY_PROJECT_ENDPOINT: 'https://ai-demo-abc123.services.ai.azure.com/api/projects/demo-project/',
  ENTRA_TENANT_ID: 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA',
  ENTRA_CLIENT_ID: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  AGENT_WITH_IQ: 'demo-with-iq',
  AGENT_WITHOUT_IQ: 'demo-without-iq',
}

test('the settings Terraform sets start the server', () => {
  assert.deepEqual(readServerConfig(env), {
    config: {
      port: 8080,
      projectEndpoint: 'https://ai-demo-abc123.services.ai.azure.com/api/projects/demo-project',
      tenantId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      clientId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      agents: { with_iq: 'demo-with-iq', without_iq: 'demo-without-iq' },
      turnTimeoutMs: DEFAULT_TURN_TIMEOUT_MS,
    },
    problems: [],
  })
  assert.equal(DEFAULT_TURN_TIMEOUT_MS, 225_000)
  assert.ok(DEFAULT_TURN_TIMEOUT_MS < 240_000, "inside Azure Container Apps' request limit")
  assert.equal(readServerConfig({ ...env, PORT: '3000' }).config?.port, 3000)
})

test("the server only ever calls a Foundry project endpoint, so a token can't be relayed elsewhere", () => {
  for (const endpoint of [
    'https://evil.example.com/api/projects/x', 'http://ai-demo.services.ai.azure.com/api/projects/x',
    'https://ai-demo.services.ai.azure.com.evil.example/api/projects/x', 'https://ai-demo.services.ai.azure.com/api/projects/x?redirect=1',
    'https://user@ai-demo.services.ai.azure.com/api/projects/x', 'https://ai-demo.services.ai.azure.com/api/projects/x/../y',
  ]) {
    assert.match(readServerConfig({ ...env, FOUNDRY_PROJECT_ENDPOINT: endpoint }).problems.join(), /FOUNDRY_PROJECT_ENDPOINT/, endpoint)
  }
})

test('missing or malformed settings stop the server and name each one', () => {
  const { config, problems } = readServerConfig({ ...env, ENTRA_CLIENT_ID: 'app', AGENT_WITHOUT_IQ: '-bad-', PORT: '0', TURN_TIMEOUT_MS: '300000' })
  assert.equal(config, undefined)
  for (const name of ['ENTRA_CLIENT_ID', 'AGENT_WITHOUT_IQ', 'PORT', 'TURN_TIMEOUT_MS']) assert.match(problems.join('\n'), new RegExp(name))
  assert.deepEqual(readServerConfig({ ...env, AGENT_WITHOUT_IQ: env.AGENT_WITH_IQ }).problems, ['The two agent names must differ.'])
  assert.equal(readServerConfig({}).problems.length, 5, 'an empty environment names all five required settings')
})

test('tracing starts only with an Application Insights connection string and the app identity that sends it', () => {
  const connection = 'InstrumentationKey=11111111-1111-4111-8111-111111111111;IngestionEndpoint=https://westeurope-0.in.applicationinsights.azure.com/;'
    + 'LiveEndpoint=https://westeurope.livediagnostics.monitor.azure.com/;ApplicationId=22222222-2222-4222-8222-222222222222'
  const identity = 'CCCCCCCC-CCCC-4CCC-8CCC-CCCCCCCCCCCC'
  const traced = { ...env, APPLICATIONINSIGHTS_CONNECTION_STRING: connection, AZURE_CLIENT_ID: identity, APP_VERSION: '41b4cac7f148' }
  assert.deepEqual(readServerConfig(traced).config?.telemetry, {
    connectionString: connection, clientId: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', version: '41b4cac7f148',
  })
  assert.equal('telemetry' in (readServerConfig(env).config ?? {}), false)
  // The server sends its identity's token to the ingestion endpoint, so it must be Application Insights'.
  for (const endpoint of ['https://collector.example.com/', 'http://westeurope-0.in.applicationinsights.azure.com/',
    'https://westeurope-0.in.applicationinsights.azure.com.example.com/', 'https://user@westeurope-0.in.applicationinsights.azure.com/']) {
    const changed = connection.replace('https://westeurope-0.in.applicationinsights.azure.com/', endpoint)
    assert.match(readServerConfig({ ...traced, APPLICATIONINSIGHTS_CONNECTION_STRING: changed }).problems.join(), /APPLICATIONINSIGHTS_CONNECTION_STRING/, endpoint)
  }
  assert.match(readServerConfig({ ...traced, APPLICATIONINSIGHTS_CONNECTION_STRING: 'IngestionEndpoint=https://x.in.applicationinsights.azure.com/' }).problems.join(), /APPLICATIONINSIGHTS/)
  assert.match(readServerConfig({ ...traced, AZURE_CLIENT_ID: undefined }).problems.join(), /AZURE_CLIENT_ID/)
  assert.match(readServerConfig({ ...traced, APP_VERSION: 'a b' }).problems.join(), /APP_VERSION/)
})

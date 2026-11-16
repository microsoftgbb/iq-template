// The app's own wire format between the browser (live/client.ts) and its server (server/turn.ts): one POST per
// question, answered as newline-delimited JSON events. Pure types and checks, so both sides and the Node tests use it.
import type { IqKey, Variant } from './types.ts'

export const TURN_PATH = '/api/turns'
export const CONFIG_PATH = '/api/config'
// MSAL's redirect bridge (redirect.html, src/redirect.ts): Entra sends every sign-in response there, and the app
// registration (infra/app.tf) names it as the only redirect URI.
export const REDIRECT_PATH = '/redirect.html'

export const VARIANTS: readonly Variant[] = ['with_iq', 'without_iq']

// The Foundry agent's MCP server labels (agents/scenario-with-iq/tools/*.json) and the IQ each one is.
export const SERVER_IQ: Readonly<Record<string, IqKey>> = {
  fabric_iq_ontology: 'fabric',
  foundry_iq_knowledge: 'foundry',
  web_iq_public_context: 'web',
  work_iq_mail: 'work',
}

export const IQ_NAMES: Readonly<Record<IqKey, string>> = {
  fabric: 'Fabric IQ',
  foundry: 'Foundry IQ',
  web: 'Web IQ',
  work: 'Work IQ',
}

export const QUESTION_LIMIT = 2000
export const FOUNDRY_SCOPE = 'https://ai.azure.com/user_impersonation'
export const ARGUMENTS_LIMIT = 400

export interface TurnRequest {
  // A lowercase UUID the browser picks for each question, so its log line, its trace and Foundry's run correlate.
  cid: string
  variant: Variant
  // Trimmed, 1 to QUESTION_LIMIT characters.
  question: string
  // The Foundry response this question follows up, which continues that conversation.
  previousResponseId?: string
}

export type ToolState = 'running' | 'completed' | 'failed'

// One MCP tool call Foundry ran during the turn, as its stream reported it. Never logged with its arguments.
export interface ToolCall {
  id: string
  server: string
  tool: string
  state: ToolState
  ms?: number
  // The call's arguments, shortened, so the user can check that a Web IQ query holds nothing private.
  arguments?: string
  error?: string
}

export type TurnOutcome =
  | { status: 'answered'; text: string; responseId?: string; toolCalls: ToolCall[] }
  | { status: 'needs-consent'; consentUrl: string; server?: string; responseId?: string; toolCalls: ToolCall[] }
  | { status: 'failed'; message: string; code?: string; responseId?: string; toolCalls: ToolCall[] }

export type TurnEvent =
  | { type: 'tool'; call: ToolCall }
  | { type: 'heartbeat' }
  | { type: 'result'; outcome: TurnOutcome }

// What GET /api/config returns: only public sign-in settings.
export interface PublicConfig {
  tenantId: string
  clientId: string
  scopes: string[]
}

const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const CID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
// Foundry's response IDs, such as resp_6f1c…, are opaque: letters, digits, underscores and hyphens.
export const RESPONSE_ID = /^[A-Za-z0-9_-]{1,256}$/
const REQUEST_KEYS: ReadonlySet<string> = new Set(['cid', 'variant', 'question', 'previousResponseId'])

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown): value is string {
  return typeof value === 'string'
}

export function isVariant(value: unknown): value is Variant {
  return value === 'with_iq' || value === 'without_iq'
}

export function iqOf(server: string): IqKey | undefined {
  return Object.hasOwn(SERVER_IQ, server) ? SERVER_IQ[server] : undefined
}

export function isPublicConfig(value: unknown): value is PublicConfig {
  return record(value) && text(value.tenantId) && GUID.test(value.tenantId) && text(value.clientId) && GUID.test(value.clientId)
    && Array.isArray(value.scopes) && value.scopes.length > 0 && value.scopes.every(text)
}

function isToolCall(value: unknown): value is ToolCall {
  return record(value) && text(value.id) && text(value.server) && text(value.tool)
    && (value.state === 'running' || value.state === 'completed' || value.state === 'failed')
    && (value.ms === undefined || (typeof value.ms === 'number' && Number.isFinite(value.ms) && value.ms >= 0))
    && (value.arguments === undefined || text(value.arguments))
    && (value.error === undefined || text(value.error))
}

function isOutcome(value: unknown): value is TurnOutcome {
  if (!record(value) || !Array.isArray(value.toolCalls) || !value.toolCalls.every(isToolCall)) return false
  if (value.responseId !== undefined && !text(value.responseId)) return false
  switch (value.status) {
    case 'answered':
      return text(value.text)
    case 'needs-consent':
      return text(value.consentUrl) && (value.server === undefined || text(value.server))
    case 'failed':
      return text(value.message) && (value.code === undefined || text(value.code))
    default:
      return false
  }
}

// One line of the stream, or an error: the browser never trusts an unrecognized shape.
export function parseTurnEvent(line: string): TurnEvent {
  let value: unknown
  try {
    value = JSON.parse(line)
  } catch {
    throw new Error('The app server sent an unreadable event.')
  }
  if (record(value)) {
    if (value.type === 'heartbeat') return { type: 'heartbeat' }
    if (value.type === 'tool' && isToolCall(value.call)) return { type: 'tool', call: value.call }
    if (value.type === 'result' && isOutcome(value.outcome)) return { type: 'result', outcome: value.outcome }
  }
  throw new Error('The app server sent an unrecognized event.')
}

// The question as the server accepts it, trimmed, or undefined: only these four fields, each well formed.
export function parseTurnRequest(value: unknown): TurnRequest | undefined {
  if (!record(value) || !Object.keys(value).every((key) => REQUEST_KEYS.has(key))) return undefined
  const { cid, variant, question, previousResponseId } = value
  if (!text(cid) || !CID.test(cid) || !isVariant(variant) || !text(question)) return undefined
  const trimmed = question.trim()
  if (!trimmed || trimmed.length > QUESTION_LIMIT) return undefined
  if (previousResponseId === undefined) return { cid, variant, question: trimmed }
  if (!text(previousResponseId) || !RESPONSE_ID.test(previousResponseId)) return undefined
  return { cid, variant, question: trimmed, previousResponseId }
}

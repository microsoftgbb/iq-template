// Parses the native Copilot Studio connector's ExecuteCopilotAsyncV2 results.
export const PROOF_PREFIX = 'CPS_AGENT_PROOF|'
const HARNESS_REFUSAL = /doesn't support agents built with the GitHub Copilot harness/i

export interface StudioTurn {
  conversationId: string
  responses: string[]
  completed?: boolean
  isExpectingInput?: boolean
}

export interface StudioProof {
  status: 'accepted' | 'needs-input' | 'responded'
  conversationId: string
  challenge: string
  reply?: string
}

export type StudioReply =
  | { status: 'answered'; conversationId: string; responses: string[] }
  | { status: 'needs-input'; conversationId: string }

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function studioDiagnostic(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(studioDiagnostic)
  if (!record(value)) return value
  return Object.fromEntries(Object.entries(value).map(([key, entry]) => [
    key,
    /token|authorization|secret|password|credential|cookie|signature|claims/i.test(key)
      ? '[redacted]'
      : studioDiagnostic(entry),
  ]))
}

export function studioTurn(value: unknown): StudioTurn {
  if (!record(value)) {
    throw new Error('The Studio connector did not return an object response.')
  }
  const ids = [value.conversationID, value.ConversationId, value.conversationId]
    .filter((id): id is string => typeof id === 'string' && Boolean(id.trim()))
  if (!ids.length || new Set(ids).size !== 1) {
    throw new Error('The Studio connector did not return its native conversation receipt.')
  }
  const turn: StudioTurn = { conversationId: ids[0], responses: [] }
  // Replies report completed: false even after EndConversation, so the flags are
  // validated but never treated as proof of an answer.
  for (const key of ['completed', 'isExpectingInput'] as const) {
    const flag = value[key]
    if (flag !== undefined && typeof flag !== 'boolean') {
      throw new Error(`Studio returned a malformed ${key} flag.`)
    }
    if (flag !== undefined) turn[key] = flag
  }
  if (typeof value.lastResponse === 'string') turn.responses.push(value.lastResponse)
  const responses = value.responses
  if (responses !== undefined) {
    if (!Array.isArray(responses) || responses.some((response) => typeof response !== 'string')) {
      throw new Error('Studio returned malformed response activities.')
    }
    turn.responses.push(...responses)
  }
  return turn
}

export function studioProof(value: unknown, challenge: string): StudioProof {
  const { conversationId, responses, isExpectingInput } = studioTurn(value)
  const expected = PROOF_PREFIX + challenge
  const reply = responses.find((response) => response.trim() === expected)
  if (responses.length && !reply) {
    throw new Error('The published agent did not return the exact fresh challenge.')
  }
  if (reply) return { status: 'responded', conversationId, challenge, reply }
  // Studio asks for per-user connection consent before any topic runs; the connector drops that card.
  return { status: isExpectingInput ? 'needs-input' : 'accepted', conversationId, challenge }
}

export function studioReply(value: unknown): StudioReply {
  const turn = studioTurn(value)
  const responses = [...new Set(turn.responses.filter((response) => response.trim()))]
  // The connector reports this documented limitation as an ordinary text response.
  if (responses.some((response) => HARNESS_REFUSAL.test(response))) {
    throw new Error('The Studio connector does not support GitHub Copilot harness agents.')
  }
  if (responses.length) return { status: 'answered', conversationId: turn.conversationId, responses }
  // The connector drops cards, such as the per-user connection prompt, and returns no text.
  if (turn.isExpectingInput) return { status: 'needs-input', conversationId: turn.conversationId }
  throw new Error('Studio returned a conversation receipt without an agent answer.')
}

export function studioFailure(message: string | undefined): string {
  if (message && /TimedOut|timed out/i.test(message)) {
    return 'The agent did not answer within the connector\'s 120-second limit. Ask again; a cold ontology query can be slow.'
  }
  return message || 'The Copilot Studio connector call failed.'
}

export function connectionManagerUrl(tenantId: string, environmentId: string, agentSchema: string, conversationId: string): string {
  const path = [
    'c2', 'tenants', tenantId, 'environments', environmentId, 'bots', agentSchema,
    'channels', 'pva-autonomous', 'conversations', conversationId, 'user-connections',
  ].map(encodeURIComponent).join('/')
  return `https://copilotstudio.microsoft.com/${path}`
}

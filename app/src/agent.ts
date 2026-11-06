// Parses the hosted agent's Responses protocol results without trusting their shape.
export const PROOF_PREFIX = 'AGENT_PROOF|'

export interface ToolCall {
  name: string
  status?: string
}

export interface AgentTurn {
  responseId: string
  text: string
  tools: ToolCall[]
  consentLink?: string
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function errorText(value: unknown): string | undefined {
  if (!record(value)) return undefined
  const error = record(value.error) ? value.error : value
  return typeof error.message === 'string' && error.message.trim() ? error.message.trim() : undefined
}

export function agentTurn(value: unknown): AgentTurn {
  if (!record(value)) throw new Error('The agent did not return a response object.')
  if (value.status !== 'completed') {
    const detail = errorText(value)
    const status = typeof value.status === 'string' ? value.status : 'missing a status'
    throw new Error(`The agent response is ${status}${detail ? `: ${detail}` : '.'}`)
  }
  if (typeof value.id !== 'string' || !value.id) throw new Error('The agent response has no ID.')
  if (!Array.isArray(value.output)) throw new Error('The agent response has no output items.')
  const texts: string[] = []
  const tools: ToolCall[] = []
  let consentLink: string | undefined
  for (const item of value.output) {
    if (!record(item) || typeof item.type !== 'string') continue
    if (item.type === 'message' && item.role === 'assistant' && Array.isArray(item.content)) {
      for (const part of item.content) {
        if (record(part) && part.type === 'output_text' && typeof part.text === 'string') texts.push(part.text)
      }
    } else if (item.type === 'oauth_consent_request' && typeof item.consent_link === 'string') {
      consentLink = item.consent_link
    } else if (item.type.endsWith('_call') && typeof item.name === 'string') {
      tools.push({ name: item.name, ...(typeof item.status === 'string' ? { status: item.status } : {}) })
    }
  }
  const text = texts.join('\n\n').trim()
  if (!text && !consentLink) throw new Error('The agent completed without an answer.')
  return { responseId: value.id, text, tools, ...(consentLink ? { consentLink } : {}) }
}

export function proofMatches(turn: AgentTurn, challenge: string): boolean {
  return turn.text === PROOF_PREFIX + challenge
}

export function failureMessage(status: number, body: unknown): string {
  const detail = errorText(body)
  const reasons: Record<number, string> = {
    401: 'The agent did not accept your sign-in. Sign out and sign in again.',
    403: 'You are not allowed to use this agent. Ask for the Foundry User role on the project.',
    404: 'The agent is not deployed at the configured endpoint.',
    408: 'The agent did not answer in time. Ask again; a cold ontology query can be slow.',
    429: 'The agent is busy. Wait a moment and ask again.',
  }
  const reason = reasons[status] ?? (status >= 500
    ? 'The agent failed to answer.'
    : `The agent request failed with HTTP ${status}.`)
  return detail ? `${reason} (${detail})` : reason
}

export function redact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redact)
  if (!record(value)) return value
  return Object.fromEntries(Object.entries(value).map(([key, entry]) => [
    key,
    /token|authorization|secret|password|credential|cookie|signature|claims/i.test(key) ? '[redacted]' : redact(entry),
  ]))
}
